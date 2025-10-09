"""
Content-based Recommendation Test Script
File riêng để test và sử dụng mô hình đã training
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

def test_recommendations():
    """Test các chức năng recommendation"""
    print("Testing Content-based Recommendations")
    print("=" * 50)
    
    # Create recommender
    recommender = ContentBasedRecommender(create_db_engine())
    
    if not recommender.check_similarity_data_exists():
        print("No similarity data found. Please run training first:")
        print("   python train_content_based.py")
        return
    
    print("Similarity data found!")
    
    # Test 1: Get related movies for Toy Story
    print("\nTest 1: Related movies for Toy Story (ID: 1)")
    related_movies = recommender.get_related_movies(1, 6)
    
    if related_movies:
        for i, movie in enumerate(related_movies, 1):
            print(f"{i}. {movie['title']} (similarity: {movie['similarity']:.3f})")
    else:
        print("No related movies found")
    
    # Test 2: Get movie info
    print("\nTest 2: Movie info for Toy Story")
    movie_info = recommender.get_movie_info(1)
    if movie_info:
        print(f"Title: {movie_info['title']}")
        print(f"Year: {movie_info.get('releaseYear', 'N/A')}")
        print(f"Genres: {', '.join(movie_info['genres'])}")
        print(f"Overview: {movie_info['overview'][:100]}...")
    
    # Test 3: Similarity score between two movies
    print("\nTest 3: Similarity between Toy Story and Toy Story 2")
    similarity = recommender.get_similarity_score(1, 3114)  # Toy Story 2
    print(f"Similarity score: {similarity:.3f}")
    
    # Test 4: Top similar movie pairs
    print("\nTest 4: Top 5 most similar movie pairs")
    top_pairs = recommender.get_top_similar_movies(5)
    for i, pair in enumerate(top_pairs, 1):
        print(f"{i}. {pair['movie1_title']} <-> {pair['movie2_title']} (similarity: {pair['similarity']:.3f})")
    
    # Test 5: Statistics
    print("\nTest 5: System Statistics")
    stats = recommender.get_statistics()
    if stats:
        print(f"Total similarities: {stats.get('total_similarities', 0):,}")
        print(f"Unique movies: {stats.get('unique_movies', 0):,}")
        print(f"Average similarity: {stats.get('avg_similarity', 0):.3f}")
        print(f"Max similarity: {stats.get('max_similarity', 0):.3f}")
        print(f"Min similarity: {stats.get('min_similarity', 0):.3f}")
    
    print("\n" + "=" * 50)
    print("All tests completed!")

def interactive_test():
    """Interactive testing mode"""
    print("Interactive Testing Mode")
    print("=" * 30)
    
    recommender = ContentBasedRecommender(create_db_engine())
    
    if not recommender.check_similarity_data_exists():
        print("No similarity data found. Please run training first:")
        print("   python train_content_based.py")
        return
    
    while True:
        print("\nChoose an option:")
        print("1. Get related movies")
        print("2. Get movie info")
        print("3. Get similarity score")
        print("4. Get top similar movie pairs")
        print("5. Get system statistics")
        print("0. Exit")
        
        choice = input("\nEnter your choice (0-5): ").strip()
        
        if choice == "0":
            print("Goodbye!")
            break
        elif choice == "1":
            movie_id = int(input("Enter movie ID: "))
            limit = int(input("Enter number of recommendations (default 6): ") or "6")
            related = recommender.get_related_movies(movie_id, limit)
            if related:
                for i, movie in enumerate(related, 1):
                    print(f"{i}. {movie['title']} (similarity: {movie['similarity']:.3f})")
            else:
                print("No related movies found.")
        elif choice == "2":
            movie_id = int(input("Enter movie ID: "))
            info = recommender.get_movie_info(movie_id)
            if info:
                print(f"Title: {info['title']}")
                print(f"Year: {info.get('releaseYear', 'N/A')}")
                print(f"Genres: {', '.join(info['genres'])}")
                print(f"Overview: {info['overview'][:100]}...")
            else:
                print("Movie not found or no info available.")
        elif choice == "3":
            movie_id1 = int(input("Enter first movie ID: "))
            movie_id2 = int(input("Enter second movie ID: "))
            similarity = recommender.get_similarity_score(movie_id1, movie_id2)
            print(f"Similarity between {movie_id1} and {movie_id2}: {similarity:.3f}")
        elif choice == "4":
            limit = int(input("Enter number of pairs (default 5): ") or "5")
            top_pairs = recommender.get_top_similar_movies(limit)
            if top_pairs:
                for i, pair in enumerate(top_pairs, 1):
                    print(f"{i}. {pair['movie1_title']} <-> {pair['movie2_title']} (similarity: {pair['similarity']:.3f})")
            else:
                print("No similar pairs found.")
        elif choice == "5":
            stats = recommender.get_statistics()
            if stats:
                print(f"Total similarities: {stats.get('total_similarities', 0):,}")
                print(f"Unique movies: {stats.get('unique_movies', 0):,}")
                print(f"Average similarity: {stats.get('avg_similarity', 0):.3f}")
                print(f"Max similarity: {stats.get('max_similarity', 0):.3f}")
                print(f"Min similarity: {stats.get('min_similarity', 0):.3f}")
            else:
                print("Could not retrieve statistics.")
        else:
            print("Invalid choice. Please try again.")

def main():
    test_recommendations()
    # interactive_test()

if __name__ == "__main__":
    main()
