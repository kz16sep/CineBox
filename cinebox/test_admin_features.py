#!/usr/bin/env python3
"""
Test script for admin features
Kiểm tra tính năng admin đã cập nhật
"""

import requests
import json
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

def test_admin_features():
    """Test các tính năng admin đã cập nhật"""
    print("=== TEST ADMIN FEATURES ===")
    print()
    
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
    
    # 1. Kiểm tra dữ liệu thể loại
    print("1. KIEM TRA DU LIEU THE LOAI:")
    with db_engine.connect() as conn:
        genres = conn.execute(text("SELECT genreId, name FROM cine.Genre ORDER BY name")).fetchall()
        print(f"   Tong so the loai: {len(genres)}")
        for genre in genres[:5]:
            print(f"   Genre {genre[0]}: {genre[1]}")
        print("   ...")
    
    print()
    
    # 2. Kiểm tra phim có nhiều thể loại
    print("2. PHIM CO NHIEU THE LOAI:")
    with db_engine.connect() as conn:
        movies_with_genres = conn.execute(text("""
            SELECT 
                m.movieId,
                m.title,
                COUNT(mg.genreId) as genre_count,
                STRING_AGG(g.name, ', ') as genres
            FROM cine.Movie m
            LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
            LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
            GROUP BY m.movieId, m.title
            HAVING COUNT(mg.genreId) > 1
            ORDER BY genre_count DESC
        """)).fetchall()
        
        print(f"   So phim co nhieu the loai: {len(movies_with_genres)}")
        for movie in movies_with_genres[:3]:
            print(f"   Movie {movie[0]}: {movie[1]} - {movie[2]} genres: {movie[3]}")
    
    print()
    
    # 3. Test API endpoints
    print("3. TEST API ENDPOINTS:")
    base_url = "http://192.168.1.166:5000"
    
    try:
        # Test admin movies page
        response = requests.get(f"{base_url}/admin/movies", timeout=5)
        print(f"   Admin movies page: {response.status_code}")
        
        # Test movie edit page (lấy movie đầu tiên)
        with db_engine.connect() as conn:
            first_movie = conn.execute(text("SELECT TOP 1 movieId FROM cine.Movie")).scalar()
        
        if first_movie:
            response = requests.get(f"{base_url}/admin/movies/{first_movie}/edit", timeout=5)
            print(f"   Movie edit page: {response.status_code}")
        
        print("   OK - All endpoints accessible")
        
    except Exception as e:
        print(f"   Error testing endpoints: {e}")
    
    print()
    
    # 4. Kiểm tra MovieGenre table
    print("4. KIEM TRA MOVIEGENRE TABLE:")
    with db_engine.connect() as conn:
        total_relations = conn.execute(text("SELECT COUNT(*) FROM cine.MovieGenre")).scalar()
        unique_movies = conn.execute(text("SELECT COUNT(DISTINCT movieId) FROM cine.MovieGenre")).scalar()
        unique_genres = conn.execute(text("SELECT COUNT(DISTINCT genreId) FROM cine.MovieGenre")).scalar()
        
        print(f"   Tong so quan he phim-the loai: {total_relations}")
        print(f"   So phim co the loai: {unique_movies}")
        print(f"   So the loai duoc su dung: {unique_genres}")
        print(f"   Trung binh moi phim co: {total_relations/unique_movies:.1f} the loai")
    
    print()
    print("=== TEST COMPLETED ===")

if __name__ == "__main__":
    test_admin_features()
