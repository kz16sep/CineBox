#!/usr/bin/env python3
"""
Extended Test Script for Content-based Recommendations
Test với nhiều phim khác nhau để đánh giá chất lượng recommendation
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from content_based_recommender import ContentBasedRecommender
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def create_db_engine():
    """Create database engine"""
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
    return create_engine(connection_url, fast_executemany=True)

def test_multiple_movies():
    """Test recommendations for multiple movies"""
    print("Extended Content-based Recommendation Testing")
    print("=" * 60)
    
    # Create recommender
    recommender = ContentBasedRecommender(create_db_engine())
    
    if not recommender.check_similarity_data_exists():
        print("No similarity data found. Please run training first:")
        print("   python train_content_based.py")
        return
    
    print("Similarity data found! Testing multiple movies...")
    
    # Test movies với các thể loại khác nhau
    test_movies = [
        {"id": 1, "name": "Toy Story (Animation)"},
        {"id": 2, "name": "Jumanji (Adventure)"},
        {"id": 6, "name": "Heat (Action/Crime)"},
        {"id": 10, "name": "GoldenEye (Action)"},
        {"id": 11, "name": "American President (Comedy/Drama)"},
        {"id": 16, "name": "Casino (Crime/Drama)"},
        {"id": 17, "name": "Sense and Sensibility (Romance)"},
        {"id": 19, "name": "Ace Ventura (Comedy)"},
        {"id": 27, "name": "Twelve Monkeys (Sci-Fi)"},
        {"id": 29, "name": "Babe (Family)"}
    ]
    
    for movie in test_movies:
        print(f"\n{'='*60}")
        print(f"Testing: {movie['name']} (ID: {movie['id']})")
        print(f"{'='*60}")
        
        # Get movie info
        movie_info = recommender.get_movie_info(movie['id'])
        if movie_info:
            print(f"Title: {movie_info['title']}")
            print(f"Year: {movie_info.get('releaseYear', 'N/A')}")
            print(f"Genres: {', '.join(movie_info['genres']) if movie_info['genres'] else 'N/A'}")
            print(f"Overview: {movie_info['overview'][:100]}..." if movie_info['overview'] else "No overview")
        
        # Get related movies
        related_movies = recommender.get_related_movies(movie['id'], 5)
        
        if related_movies:
            print(f"\nTop 5 Related Movies:")
            for i, related in enumerate(related_movies, 1):
                print(f"{i}. {related['title']} (similarity: {related['similarity']:.3f})")
        else:
            print("\nNo related movies found")
        
        # Test similarity with another movie
        if movie['id'] < 10:  # Only test first few movies
            test_id = movie['id'] + 1
            similarity = recommender.get_similarity_score(movie['id'], test_id)
            print(f"\nSimilarity with movie ID {test_id}: {similarity:.3f}")

def test_genre_analysis():
    """Analyze recommendations by genre"""
    print(f"\n{'='*60}")
    print("GENRE ANALYSIS")
    print(f"{'='*60}")
    
    recommender = ContentBasedRecommender(create_db_engine())
    
    # Get all movies with their genres
    with create_db_engine().connect() as conn:
        result = conn.execute(text("""
            SELECT m.movieId, m.title, STRING_AGG(g.name, ', ') as genres
            FROM cine.Movie m
            LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
            LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
            GROUP BY m.movieId, m.title
            ORDER BY m.movieId
        """)).fetchall()
        
        print("Movies in database:")
        for row in result[:10]:  # Show first 10
            print(f"ID {row[0]}: {row[1]} - Genres: {row[2] or 'None'}")

def test_similarity_distribution():
    """Test similarity score distribution"""
    print(f"\n{'='*60}")
    print("SIMILARITY DISTRIBUTION ANALYSIS")
    print(f"{'='*60}")
    
    recommender = ContentBasedRecommender(create_db_engine())
    
    with create_db_engine().connect() as conn:
        # Get similarity ranges
        result = conn.execute(text("""
            SELECT 
                COUNT(*) as total_pairs,
                AVG(similarity) as avg_similarity,
                MIN(similarity) as min_similarity,
                MAX(similarity) as max_similarity,
                COUNT(CASE WHEN similarity >= 0.9 THEN 1 END) as high_similarity,
                COUNT(CASE WHEN similarity >= 0.7 AND similarity < 0.9 THEN 1 END) as medium_similarity,
                COUNT(CASE WHEN similarity < 0.7 THEN 1 END) as low_similarity
            FROM cine.MovieSimilarity
        """)).fetchone()
        
        print(f"Total similarity pairs: {result[0]}")
        print(f"Average similarity: {result[1]:.3f}")
        print(f"Min similarity: {result[2]:.3f}")
        print(f"Max similarity: {result[3]:.3f}")
        print(f"High similarity (≥0.9): {result[4]} pairs")
        print(f"Medium similarity (0.7-0.9): {result[5]} pairs")
        print(f"Low similarity (<0.7): {result[6]} pairs")

def test_edge_cases():
    """Test edge cases"""
    print(f"\n{'='*60}")
    print("EDGE CASES TESTING")
    print(f"{'='*60}")
    
    recommender = ContentBasedRecommender(create_db_engine())
    
    # Test with non-existent movie
    print("1. Testing with non-existent movie ID (99999):")
    related = recommender.get_related_movies(99999, 5)
    print(f"   Result: {len(related)} movies found")
    
    # Test with movie that has no similarities
    print("\n2. Testing with movie that might have no similarities:")
    related = recommender.get_related_movies(100, 5)  # Assuming ID 100 exists
    print(f"   Result: {len(related)} movies found")
    
    # Test similarity between same movie
    print("\n3. Testing similarity between same movie:")
    similarity = recommender.get_similarity_score(1, 1)
    print(f"   Similarity score: {similarity:.3f}")

def main():
    """Main test function"""
    test_multiple_movies()
    test_genre_analysis()
    test_similarity_distribution()
    test_edge_cases()
    
    print(f"\n{'='*60}")
    print("ALL TESTS COMPLETED!")
    print("=" * 60)

if __name__ == "__main__":
    main()
