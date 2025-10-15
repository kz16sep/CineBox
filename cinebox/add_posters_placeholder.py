#!/usr/bin/env python3
"""
Add placeholder poster URLs for all movies
"""

import pyodbc
from tqdm import tqdm
import urllib.parse

def add_posters_placeholder():
    print("🎬 Adding placeholder poster URLs...")
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
        
        # Get all movies
        print("1️⃣ Getting all movies...")
        cursor.execute("SELECT movieId, title, releaseYear FROM cine.Movie ORDER BY movieId")
        movies = cursor.fetchall()
        print(f"   Found {len(movies)} movies")
        
        # Process movies in batches
        batch_size = 1000
        total_batches = len(movies) // batch_size + (1 if len(movies) % batch_size > 0 else 0)
        
        print(f"\n2️⃣ Processing {len(movies)} movies in {total_batches} batches...")
        
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(movies))
            batch_movies = movies[start_idx:end_idx]
            
            print(f"   Processing batch {batch_num + 1}/{total_batches} ({len(batch_movies)} movies)...")
            
            for movie in tqdm(batch_movies, desc=f"Batch {batch_num + 1}"):
                movie_id, title, year = movie
                
                try:
                    # Create placeholder URL
                    clean_title = title.replace("'", "").replace('"', "").replace("&", "and")
                    if year:
                        placeholder_url = f"https://via.placeholder.com/500x750/2a2a2a/ffffff?text={urllib.parse.quote(clean_title[:30])}+({year})"
                    else:
                        placeholder_url = f"https://via.placeholder.com/500x750/2a2a2a/ffffff?text={urllib.parse.quote(clean_title[:30])}"
                    
                    # Update database
                    cursor.execute("""
                        UPDATE cine.Movie 
                        SET posterUrl = ? 
                        WHERE movieId = ?
                    """, (placeholder_url, movie_id))
                    
                except Exception as e:
                    print(f"   Error processing movie {movie_id}: {e}")
            
            # Commit batch
            conn.commit()
            print(f"   Batch {batch_num + 1} completed")
        
        print(f"\n✅ Successfully added placeholder posters for {len(movies)} movies!")
        
        conn.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    add_posters_placeholder()
