#!/usr/bin/env python3
"""
Upgrade poster URLs using IMDB IDs from links.csv via OMDB API
"""

import pandas as pd
import pyodbc
import requests
import time
from tqdm import tqdm
import re

def get_omdb_poster(imdb_id):
    """Get poster URL from OMDB using imdbId"""
    try:
        # OMDB API (free, no key required for basic usage)
        url = "http://www.omdbapi.com/"
        params = {
            'i': f"tt{imdb_id:07d}"  # Format IMDB ID as tt0000000
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
        print(f"Error getting OMDB poster for IMDB ID {imdb_id}: {e}")
        return None

def upgrade_posters_omdb():
    print("🎬 Upgrading poster URLs using IMDB IDs via OMDB API...")
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
        imdb_mapping = {}
        for _, row in links_df.iterrows():
            movie_id = int(row['movieId'])
            imdb_id = row['imdbId']
            if pd.notna(imdb_id) and imdb_id != 0:
                imdb_mapping[movie_id] = int(imdb_id)
        
        print(f"   Found {len(imdb_mapping)} movies with IMDB IDs")
        
        # Get movies from database
        print("\n2️⃣ Getting movies from database...")
        cursor.execute("SELECT movieId, title FROM cine.Movie ORDER BY movieId")
        movies = cursor.fetchall()
        print(f"   Found {len(movies)} movies in database")
        
        # Process movies in batches
        batch_size = 100  # OMDB is more lenient
        movies_with_imdb = [(m[0], m[1]) for m in movies if m[0] in imdb_mapping]
        total_batches = len(movies_with_imdb) // batch_size + (1 if len(movies_with_imdb) % batch_size > 0 else 0)
        
        print(f"\n3️⃣ Processing {len(movies_with_imdb)} movies with IMDB IDs in {total_batches} batches...")
        
        success_count = 0
        error_count = 0
        
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(movies_with_imdb))
            batch_movies = movies_with_imdb[start_idx:end_idx]
            
            print(f"\n   Processing batch {batch_num + 1}/{total_batches} ({len(batch_movies)} movies)...")
            
            for movie in tqdm(batch_movies, desc=f"Batch {batch_num + 1}"):
                movie_id, title = movie
                imdb_id = imdb_mapping[movie_id]
                
                try:
                    # Get poster from OMDB
                    poster_url = get_omdb_poster(imdb_id)
                    
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
                    
                    # Rate limiting - OMDB allows 1000 requests per day
                    time.sleep(0.1)  # 10 requests per second
                    
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
    print("ℹ️  OMDB API is free and doesn't require API key")
    print("   Rate limit: 1000 requests/day (more than enough for 87k movies)")
    print("   This will upgrade placeholder posters with real movie posters from OMDB")
    print()
    
    upgrade_posters_omdb()
