#!/usr/bin/env python3
"""
Upgrade poster URLs using TMDB IDs from links.csv
"""

import pandas as pd
import pyodbc
import requests
import time
from tqdm import tqdm
import json

def get_tmdb_poster(tmdb_id):
    """Get poster URL from TMDB using tmdbId"""
    try:
        # TMDB API key (you need to get this from https://www.themoviedb.org/settings/api)
        API_KEY = "YOUR_TMDB_API_KEY"  # Replace with your API key
        
        # Get movie details from TMDB
        url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
        params = {
            'api_key': API_KEY,
            'language': 'en-US'
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        poster_path = data.get('poster_path')
        
        if poster_path:
            return f"https://image.tmdb.org/t/p/w500{poster_path}"
        
        return None
        
    except Exception as e:
        print(f"Error getting TMDB poster for ID {tmdb_id}: {e}")
        return None

def upgrade_posters_tmdb():
    print("🎬 Upgrading poster URLs using TMDB IDs...")
    print("=" * 60)
    
    # Database connection
    conn_str = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=localhost;"
        "DATABASE=CineBoxDB;"
        "Trusted_Connection=yes;"
    )
    
    try:
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()
        
        # Load links.csv
        print("1️⃣ Loading links.csv...")
        links_df = pd.read_csv("../../ml-32m/links.csv")
        print(f"   Loaded {len(links_df)} movie links")
        
        # Create mapping dictionary
        tmdb_mapping = {}
        for _, row in links_df.iterrows():
            movie_id = int(row['movieId'])
            tmdb_id = row['tmdbId']
            if pd.notna(tmdb_id) and tmdb_id != 0:
                tmdb_mapping[movie_id] = int(tmdb_id)
        
        print(f"   Found {len(tmdb_mapping)} movies with TMDB IDs")
        
        # Get movies from database
        print("\n2️⃣ Getting movies from database...")
        cursor.execute("SELECT movieId, title FROM cine.Movie ORDER BY movieId")
        movies = cursor.fetchall()
        print(f"   Found {len(movies)} movies in database")
        
        # Process movies in batches
        batch_size = 50  # Smaller batch for API limits
        movies_with_tmdb = [(m[0], m[1]) for m in movies if m[0] in tmdb_mapping]
        total_batches = len(movies_with_tmdb) // batch_size + (1 if len(movies_with_tmdb) % batch_size > 0 else 0)
        
        print(f"\n3️⃣ Processing {len(movies_with_tmdb)} movies with TMDB IDs in {total_batches} batches...")
        
        success_count = 0
        error_count = 0
        
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(movies_with_tmdb))
            batch_movies = movies_with_tmdb[start_idx:end_idx]
            
            print(f"\n   Processing batch {batch_num + 1}/{total_batches} ({len(batch_movies)} movies)...")
            
            for movie in tqdm(batch_movies, desc=f"Batch {batch_num + 1}"):
                movie_id, title = movie
                tmdb_id = tmdb_mapping[movie_id]
                
                try:
                    # Get poster from TMDB
                    poster_url = get_tmdb_poster(tmdb_id)
                    
                    if poster_url:
                        # Update database
                        cursor.execute("""
                            UPDATE cine.Movie 
                            SET posterUrl = ? 
                            WHERE movieId = ?
                        """, (poster_url, movie_id))
                        success_count += 1
                    else:
                        error_count += 1
                    
                    # Rate limiting - TMDB allows 40 requests per 10 seconds
                    time.sleep(0.25)  # 4 requests per second
                    
                except Exception as e:
                    print(f"   Error processing movie {movie_id}: {e}")
                    error_count += 1
            
            # Commit batch
            conn.commit()
            print(f"   Batch {batch_num + 1} completed")
        
        # Final statistics
        print(f"\n4️⃣ Final statistics:")
        print(f"   ✅ Successfully upgraded: {success_count} posters")
        print(f"   ❌ Errors: {error_count}")
        print(f"   📊 Total processed: {success_count + error_count}")
        
        conn.close()
        print("\n✅ Poster upgrade completed!")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    print("⚠️  IMPORTANT: You need to get a TMDB API key from https://www.themoviedb.org/settings/api")
    print("   Replace 'YOUR_TMDB_API_KEY' in the script with your actual API key")
    print("   This will upgrade placeholder posters with real movie posters from TMDB")
    print()
    
    upgrade_posters_tmdb()
