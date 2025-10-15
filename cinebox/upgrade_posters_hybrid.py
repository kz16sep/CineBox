#!/usr/bin/env python3
"""
Hybrid poster upgrade using movieId, imdbId, tmdbId
"""

import pandas as pd
import pyodbc
import requests
import time
from tqdm import tqdm
import random

def get_tmdb_poster(tmdb_id):
    """Get poster URL from TMDB using tmdbId"""
    try:
        # TMDB API key (optional for basic usage)
        API_KEY = "YOUR_TMDB_API_KEY"  # Replace with your API key or leave empty
        
        url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
        params = {'api_key': API_KEY} if API_KEY != "YOUR_TMDB_API_KEY" else {}
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        poster_path = data.get('poster_path')
        
        if poster_path:
            return f"https://image.tmdb.org/t/p/w500{poster_path}"
        
        return None
        
    except Exception as e:
        return None

def get_omdb_poster(imdb_id):
    """Get poster URL from OMDB using imdbId"""
    try:
        url = "http://www.omdbapi.com/"
        params = {
            'i': f"tt{imdb_id:07d}",
            'apikey': 'YOUR_OMDB_API_KEY'  # Optional
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        if data.get('Response') == 'True':
            poster_url = data.get('Poster')
            if poster_url and poster_url != 'N/A':
                return poster_url
        
        return None
        
    except Exception as e:
        return None

def get_placeholder_poster(title, year=None):
    """Generate placeholder poster URL"""
    import urllib.parse
    clean_title = title.replace("'", "").replace('"', "").replace("&", "and")
    if year:
        return f"https://via.placeholder.com/500x750/2a2a2a/ffffff?text={urllib.parse.quote(clean_title[:30])}+({year})"
    else:
        return f"https://via.placeholder.com/500x750/2a2a2a/ffffff?text={urllib.parse.quote(clean_title[:30])}"

def upgrade_posters_hybrid():
    print("🎬 Hybrid poster upgrade using movieId, imdbId, tmdbId...")
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
        
        # Create mapping dictionaries
        tmdb_mapping = {}
        imdb_mapping = {}
        
        for _, row in links_df.iterrows():
            movie_id = int(row['movieId'])
            imdb_id = row['imdbId']
            tmdb_id = row['tmdbId']
            
            if pd.notna(imdb_id) and imdb_id != 0:
                imdb_mapping[movie_id] = int(imdb_id)
            if pd.notna(tmdb_id) and tmdb_id != 0:
                tmdb_mapping[movie_id] = int(tmdb_id)
        
        print(f"   Found {len(imdb_mapping)} movies with IMDB IDs")
        print(f"   Found {len(tmdb_mapping)} movies with TMDB IDs")
        
        # Get movies from database
        print("\n2️⃣ Getting movies from database...")
        cursor.execute("SELECT movieId, title, releaseYear FROM cine.Movie ORDER BY movieId")
        movies = cursor.fetchall()
        print(f"   Found {len(movies)} movies in database")
        
        # Process movies in batches
        batch_size = 50
        total_batches = len(movies) // batch_size + (1 if len(movies) % batch_size > 0 else 0)
        
        print(f"\n3️⃣ Processing {len(movies)} movies in {total_batches} batches...")
        
        tmdb_success = 0
        omdb_success = 0
        placeholder_count = 0
        error_count = 0
        
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(movies))
            batch_movies = movies[start_idx:end_idx]
            
            print(f"\n   Processing batch {batch_num + 1}/{total_batches} ({len(batch_movies)} movies)...")
            
            for movie in tqdm(batch_movies, desc=f"Batch {batch_num + 1}"):
                movie_id, title, year = movie
                poster_url = None
                source = None
                
                try:
                    # Try TMDB first (usually better quality)
                    if movie_id in tmdb_mapping:
                        poster_url = get_tmdb_poster(tmdb_mapping[movie_id])
                        if poster_url:
                            source = "TMDB"
                            tmdb_success += 1
                    
                    # Try OMDB if TMDB failed
                    if not poster_url and movie_id in imdb_mapping:
                        poster_url = get_omdb_poster(imdb_mapping[movie_id])
                        if poster_url:
                            source = "OMDB"
                            omdb_success += 1
                    
                    # Use placeholder if both failed
                    if not poster_url:
                        poster_url = get_placeholder_poster(title, year)
                        source = "Placeholder"
                        placeholder_count += 1
                    
                    # Update database
                    cursor.execute("""
                        UPDATE cine.Movie 
                        SET posterUrl = ? 
                        WHERE movieId = ?
                    """, (poster_url, movie_id))
                    
                    # Rate limiting
                    time.sleep(0.1)  # 10 requests per second
                    
                except Exception as e:
                    print(f"   Error processing movie {movie_id}: {e}")
                    error_count += 1
            
            # Commit batch
            conn.commit()
            print(f"   Batch {batch_num + 1} completed")
        
        # Final statistics
        print(f"\n4️⃣ Final statistics:")
        print(f"   🎬 TMDB posters: {tmdb_success}")
        print(f"   🎭 OMDB posters: {omdb_success}")
        print(f"   🎨 Placeholder posters: {placeholder_count}")
        print(f"   ❌ Errors: {error_count}")
        print(f"   📊 Total processed: {tmdb_success + omdb_success + placeholder_count + error_count}")
        
        conn.close()
        print("\n✅ Hybrid poster upgrade completed!")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    print("🎯 HYBRID APPROACH:")
    print("   1. Try TMDB first (best quality)")
    print("   2. Fallback to OMDB if TMDB fails")
    print("   3. Use placeholder if both fail")
    print("   This ensures all movies have posters!")
    print()
    
    upgrade_posters_hybrid()
