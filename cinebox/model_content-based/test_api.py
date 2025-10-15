#!/usr/bin/env python3
"""
Test Web Application API với Model Mới
"""

import requests
import json
import time

def test_web_api():
    """Test web application API"""
    print("🌐 TESTING WEB APPLICATION API")
    print("=" * 50)
    
    base_url = "http://127.0.0.1:5000"
    
    try:
        # Test 1: Home page
        print("\n1. Testing Home Page:")
        print("-" * 25)
        response = requests.get(f"{base_url}/", timeout=5)
        print(f"Status: {response.status_code}")
        print(f"Response time: {response.elapsed.total_seconds():.3f}s")
        
        # Test 2: Movie detail page
        print("\n2. Testing Movie Detail Page:")
        print("-" * 25)
        movie_id = 2  # Jumanji
        response = requests.get(f"{base_url}/movie/{movie_id}", timeout=5)
        print(f"Status: {response.status_code}")
        print(f"Response time: {response.elapsed.total_seconds():.3f}s")
        
        # Test 3: Recommendations API
        print("\n3. Testing Recommendations API:")
        print("-" * 25)
        
        test_movies = [2, 3, 4, 5, 6]  # Test với 5 phim
        
        for movie_id in test_movies:
            start_time = time.time()
            try:
                response = requests.get(f"{base_url}/api/recommendations/{movie_id}", timeout=5)
                end_time = time.time()
                
                if response.status_code == 200:
                    data = response.json()
                    recommendations = data.get('recommendations', [])
                    print(f"Movie ID {movie_id}: {len(recommendations)} recommendations ({end_time - start_time:.3f}s)")
                    
                    # Show top 3 recommendations
                    for i, rec in enumerate(recommendations[:3], 1):
                        title = rec.get('title', 'Unknown')
                        similarity = rec.get('similarity', 0)
                        print(f"  {i}. {title} (similarity: {similarity:.3f})")
                else:
                    print(f"Movie ID {movie_id}: Error {response.status_code}")
            except requests.exceptions.RequestException as e:
                print(f"Movie ID {movie_id}: Connection error - {e}")
            print()
        
        # Test 4: Performance Summary
        print("\n4. Performance Summary:")
        print("-" * 25)
        
        total_time = 0
        successful_requests = 0
        
        for movie_id in test_movies:
            try:
                start_time = time.time()
                response = requests.get(f"{base_url}/api/recommendations/{movie_id}", timeout=5)
                end_time = time.time()
                
                if response.status_code == 200:
                    total_time += (end_time - start_time)
                    successful_requests += 1
            except:
                pass
        
        if successful_requests > 0:
            avg_time = total_time / successful_requests
            print(f"Average response time: {avg_time:.3f}s")
            print(f"Successful requests: {successful_requests}/{len(test_movies)}")
            print(f"Performance: {'Excellent' if avg_time < 0.1 else 'Good' if avg_time < 0.5 else 'Needs improvement'}")
        
        print("\n✅ WEB APPLICATION API TEST COMPLETED!")
        
    except requests.exceptions.ConnectionError:
        print("❌ Error: Cannot connect to web application")
        print("Make sure the web app is running on http://127.0.0.1:5000")
        print("Run: python run.py")
        
    except Exception as e:
        print(f"❌ Error: {e}")

def test_direct_database():
    """Test database trực tiếp"""
    print("\n🔧 TESTING DATABASE DIRECTLY:")
    print("-" * 30)
    
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import URL
    
    # Database connection
    odbc_str = (
        "DRIVER=ODBC Driver 17 for SQL Server;"
        "SERVER=localhost,1433;"
        "DATABASE=CineBoxDB;"     
        "UID=sa;"
        "PWD=sapassword;"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
    )
    connection_url = URL.create("mssql+pyodbc", query={"odbc_connect": odbc_str})
    db_engine = create_engine(connection_url, fast_executemany=True)
    
    try:
        with db_engine.connect() as conn:
            # Test recommendation query
            movie_id = 2
            start_time = time.time()
            
            query = text("""
                SELECT TOP 10 
                    m2.movieId, 
                    m2.title, 
                    m2.releaseYear,
                    ms.similarity,
                    STRING_AGG(g.name, ', ') as genres
                FROM cine.MovieSimilarity ms
                JOIN cine.Movie m2 ON ms.movieId2 = m2.movieId
                LEFT JOIN cine.MovieGenre mg ON m2.movieId = mg.movieId
                LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                WHERE ms.movieId1 = :movie_id
                GROUP BY m2.movieId, m2.title, m2.releaseYear, ms.similarity
                ORDER BY ms.similarity DESC
            """)
            
            result = conn.execute(query, {"movie_id": movie_id})
            recommendations = result.fetchall()
            
            end_time = time.time()
            query_time = end_time - start_time
            
            print(f"Direct database query time: {query_time:.4f}s")
            print(f"Found {len(recommendations)} recommendations")
            
            # Show top 5
            print("\nTop 5 recommendations:")
            for i, rec in enumerate(recommendations[:5], 1):
                movie_id, title, year, similarity, genres = rec
                print(f"  {i}. {title} ({year}) - {similarity:.3f}")
                if genres:
                    print(f"     Genres: {genres}")
            
    except Exception as e:
        print(f"❌ Database error: {e}")

if __name__ == "__main__":
    test_web_api()
    test_direct_database()
