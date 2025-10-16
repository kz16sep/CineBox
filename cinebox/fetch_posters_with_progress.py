#!/usr/bin/env python3
"""
Fetch poster URLs from TMDB with progress display and rate limiting
40 requests per 10 seconds = 4 requests per second
"""

import pyodbc
import pandas as pd
import requests
import time
import os
from datetime import datetime

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def fetch_posters_with_progress():
    print("FETCHING POSTER URLs FROM TMDB WITH PROGRESS")
    print("=" * 60)
    
    # API key
    API_KEY = "410065906e9552ec1e24efe8c5393791"
    
    # Rate limiting: 20 requests per second (optimized)
    REQUESTS_PER_SECOND = 20
    DELAY_BETWEEN_REQUESTS = 1.0 / REQUESTS_PER_SECOND  # 0.05 seconds
    
    # Load links.csv
    print("1. Loading links.csv...")
    links_df = pd.read_csv("../../ml-32m/links.csv")
    print(f"   Loaded {len(links_df)} rows")
    
    # Create mapping from original movieId to tmdbId
    tmdb_mapping = {}
    for _, row in links_df.iterrows():
        original_movie_id = int(row['movieId'])
        tmdb_id = row['tmdbId']
        if pd.notna(tmdb_id) and tmdb_id != 0:
            tmdb_mapping[original_movie_id] = int(tmdb_id)
    
    print(f"   Found {len(tmdb_mapping)} movies with TMDB IDs")
    
    # Database connection
    conn_str = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=localhost;"
        "DATABASE=CineBoxDB;"
        "Trusted_Connection=yes;"
    )
    
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    print("   Database connected")
    
    # Load original movies.csv to get title mapping
    print("\n2. Loading movies.csv for title mapping...")
    movies_df = pd.read_csv("../../ml-32m/movies.csv")
    print(f"   Loaded {len(movies_df)} original movies")
    
    # Create mapping from title to original movieId
    title_to_original_id = {}
    for _, row in movies_df.iterrows():
        title = row['title']
        original_id = int(row['movieId'])
        title_to_original_id[title] = original_id
    
    # Get only movies without poster URLs from database
    print("\n3. Getting movies without poster URLs from database...")
    cursor.execute("SELECT movieId, title FROM cine.Movie WHERE posterUrl IS NULL ORDER BY movieId")
    db_movies = cursor.fetchall()
    print(f"   Found {len(db_movies)} movies without poster URLs")
    
    if len(db_movies) == 0:
        print("\n✅ All movies already have poster URLs! Nothing to process.")
        conn.close()
        return
    
    # Process movies in batches
    batch_size = 40
    total_batches = len(db_movies) // batch_size + (1 if len(db_movies) % batch_size > 0 else 0)
    
    print(f"\n4. Processing {len(db_movies)} movies without poster URLs in {total_batches} batches...")
    print(f"   Batch size: {batch_size} movies")
    print(f"   Rate limit: {REQUESTS_PER_SECOND} requests/second")
    print(f"   Estimated time: ~{len(db_movies) / (REQUESTS_PER_SECOND * 0.8) / 3600:.1f} hours")
    
    success_count = 0
    error_count = 0
    no_poster_count = 0
    start_time = time.time()
    
    for batch_num in range(total_batches):
        start_idx = batch_num * batch_size
        end_idx = min(start_idx + batch_size, len(db_movies))
        batch_movies = db_movies[start_idx:end_idx]
        
        print(f"  Processing movies: #{start_idx + 1} to #{end_idx} (IDs: {batch_movies[0][0]} to {batch_movies[-1][0]})")
        
        batch_success = 0
        batch_errors = 0
        batch_no_poster = 0
        
        for movie_idx, (db_movie_id, title) in enumerate(batch_movies):
            try:
                # Find original movieId from title
                original_movie_id = None
                if title in title_to_original_id:
                    original_movie_id = title_to_original_id[title]
                
                if original_movie_id and original_movie_id in tmdb_mapping:
                    tmdb_id = tmdb_mapping[original_movie_id]
                    
                    # Get poster from TMDB
                    url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
                    params = {'api_key': API_KEY}
                    
                    response = requests.get(url, params=params, timeout=10)
                    if response.status_code == 200:
                        data = response.json()
                        tmdb_title = data.get('title', 'Unknown')
                        poster_path = data.get('poster_path')
                        
                        # Check if titles match (case insensitive, remove year from database title)
                        db_title_clean = title.split(' (')[0].lower().strip()  # Remove year part
                        tmdb_title_clean = tmdb_title.lower().strip()
                        
                        if db_title_clean == tmdb_title_clean:
                            if poster_path:
                                poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}"
                                
                                # Update database with poster URL
                                cursor.execute("""
                                    UPDATE cine.Movie 
                                    SET posterUrl = ? 
                                    WHERE movieId = ?
                                """, (poster_url, db_movie_id))
                                
                                batch_success += 1
                                success_count += 1
                                print(f"   SUCCESS: #{start_idx + movie_idx + 1} | ID: {db_movie_id} | {title[:40]}... -> {poster_url}")
                            else:
                                # No poster available, fill '1'
                                cursor.execute("""
                                    UPDATE cine.Movie 
                                    SET posterUrl = '1' 
                                    WHERE movieId = ?
                                """, (db_movie_id,))
                                
                                batch_no_poster += 1
                                no_poster_count += 1
                                print(f"   NO POSTER: #{start_idx + movie_idx + 1} | ID: {db_movie_id} | {title[:40]}... -> Filled '1'")
                        else:
                            # Title mismatch, fill '1'
                            cursor.execute("""
                                UPDATE cine.Movie 
                                SET posterUrl = '1' 
                                WHERE movieId = ?
                            """, (db_movie_id,))
                            
                            batch_errors += 1
                            error_count += 1
                            print(f"   TITLE MISMATCH: #{start_idx + movie_idx + 1} | ID: {db_movie_id} | {title[:40]}... -> Filled '1'")
                    else:
                        # API error, fill '1'
                        cursor.execute("""
                            UPDATE cine.Movie 
                            SET posterUrl = '1' 
                            WHERE movieId = ?
                        """, (db_movie_id,))
                        
                        batch_errors += 1
                        error_count += 1
                        print(f"   API ERROR: #{start_idx + movie_idx + 1} | ID: {db_movie_id} | {title[:40]}... -> Filled '1'")
                else:
                    # No TMDB ID, fill '1'
                    cursor.execute("""
                        UPDATE cine.Movie 
                        SET posterUrl = '1' 
                        WHERE movieId = ?
                    """, (db_movie_id,))
                    
                    batch_errors += 1
                    error_count += 1
                    print(f"   NO TMDB ID: #{start_idx + movie_idx + 1} | ID: {db_movie_id} | {title[:40]}... -> Filled '1'")
                
                # Rate limiting
                time.sleep(DELAY_BETWEEN_REQUESTS)
                
            except Exception as e:
                # Error occurred, fill '1'
                cursor.execute("""
                    UPDATE cine.Movie 
                    SET posterUrl = '1' 
                    WHERE movieId = ?
                """, (db_movie_id,))
                
                batch_errors += 1
                error_count += 1
                print(f"   ERROR: #{start_idx + movie_idx + 1} | ID: {db_movie_id} | {title[:40]}... -> Filled '1' ({e})")
            
            # Update progress display
            current_movie = start_idx + movie_idx + 1
            elapsed_time = time.time() - start_time
            progress = (current_movie / len(db_movies)) * 100
            
            # Calculate ETA
            if current_movie > 0:
                avg_time_per_movie = elapsed_time / current_movie
                remaining_movies = len(db_movies) - current_movie
                eta_seconds = remaining_movies * avg_time_per_movie
                eta_hours = eta_seconds / 3600
            else:
                eta_hours = 0
            
            # Clear screen and show progress
            clear_screen()
            print("POSTER FETCHING PROGRESS")
            print("=" * 60)
            print(f"Batch: {batch_num + 1}/{total_batches} | Movie: {current_movie}/{len(db_movies)}")
            print(f"Progress: {progress:.1f}%")
            print()
            
            # Progress bar
            bar_length = 50
            filled_length = int(bar_length * progress / 100)
            bar = '█' * filled_length + '░' * (bar_length - filled_length)
            print(f"[{bar}] {progress:.1f}%")
            print()
            
            # Statistics
            print(f"Current batch results:")
            print(f"  Success: {batch_success} | No poster: {batch_no_poster} | Errors: {batch_errors}")
            print()
            print(f"Total results:")
            print(f"  Success: {success_count:,} | No poster: {no_poster_count:,} | Errors: {error_count:,}")
            print()
            print(f"Time: {elapsed_time/60:.1f} min elapsed | ETA: {eta_hours:.1f} hours")
            print(f"Rate: {REQUESTS_PER_SECOND} requests/second")
            print()
            print(f"Current movie: #{current_movie} | ID: {db_movie_id} | {title[:45]}...")
        
        # Commit batch
        conn.commit()
        
        # No delay between batches for faster processing
        print(f"\nBatch {batch_num + 1} completed. Moving to next batch...")
    
    # Final statistics
    clear_screen()
    print("POSTER FETCHING COMPLETED!")
    print("=" * 60)
    print(f"Final results:")
    print(f"  SUCCESS (with poster): {success_count:,}")
    print(f"  NO POSTER (filled '1'): {no_poster_count:,}")
    print(f"  ERRORS (filled '1'): {error_count:,}")
    print(f"  TOTAL PROCESSED: {success_count + no_poster_count + error_count:,}")
    print(f"  All changes saved to database")
    
    # Show final database status
    cursor.execute("SELECT COUNT(*) FROM cine.Movie WHERE posterUrl IS NOT NULL AND posterUrl != '1'")
    with_real_posters = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM cine.Movie WHERE posterUrl = '1'")
    with_placeholder = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM cine.Movie")
    total = cursor.fetchone()[0]
    
    print(f"\nDatabase status:")
    print(f"  Movies with real posters: {with_real_posters:,}")
    print(f"  Movies with placeholder '1': {with_placeholder:,}")
    print(f"  Total movies: {total:,}")
    print(f"  Coverage: {((with_real_posters + with_placeholder) / total * 100):.1f}%")
    
    conn.close()
    print(f"\n✅ Poster fetching completed!")

if __name__ == "__main__":
    print("This will fetch poster URLs from TMDB and update database!")
    print("Movies without poster URLs will be filled with '1'")
    print("Rate limit: 40 requests per 10 seconds")
    confirm = input("Continue? (y/n): ").strip().lower()
    if confirm == 'y':
        fetch_posters_with_progress()
    else:
        print("Cancelled by user")
