#!/usr/bin/env python3
"""
Content-based Recommendation Service
Serving logic for content-based movie recommendations
"""

import logging
from typing import List, Dict, Optional
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ContentBasedRecommender:
    """
    Content-based Recommendation Service
    Gợi ý phim liên quan dựa trên đặc điểm của phim đang xem
    """
    
    def __init__(self, db_engine):
        """
        Initialize Content-based Recommender
        
        Args:
            db_engine: SQLAlchemy database engine
        """
        self.db_engine = db_engine
        self.is_trained = False
    
    def check_similarity_data_exists(self) -> bool:
        """Check if similarity data exists in database"""
        try:
            with self.db_engine.connect() as conn:
                result = conn.execute(text("SELECT COUNT(*) FROM cine.MovieSimilarity")).scalar()
                return result > 0
        except Exception as e:
            logger.error(f"Error checking similarity data: {e}")
            return False
    
    def get_movie_info(self, movie_id: int) -> Optional[Dict]:
        """Get movie information"""
        try:
            with self.db_engine.connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT m.movieId, m.title, m.releaseYear, m.overview, m.posterUrl, m.backdropUrl,
                               STRING_AGG(g.name, ', ') as genres
                        FROM cine.Movie m
                        LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                        LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                        WHERE m.movieId = ?
                        GROUP BY m.movieId, m.title, m.releaseYear, m.overview, m.posterUrl, m.backdropUrl
                    """),
                    [movie_id]
                ).fetchone()
                
                if result:
                    return {
                        'movieId': result[0],
                        'title': result[1],
                        'releaseYear': result[2],
                        'overview': result[3] or '',
                        'posterUrl': result[4],
                        'backdropUrl': result[5],
                        'genres': result[6].split(', ') if result[6] else []
                    }
                return None
        except Exception as e:
            logger.error(f"Error getting movie info: {e}")
            return None
    
    def get_related_movies(self, movie_id: int, limit: int = 10) -> List[Dict]:
        """Get related movies based on content similarity"""
        try:
            logger.info(f"Getting {limit} related movies for movie ID: {movie_id}")
            
            with self.db_engine.connect() as conn:
                # Use string formatting for TOP clause since SQL Server doesn't support parameterized TOP
                query = f"""
                    SELECT TOP {limit} m.movieId, m.title, m.posterUrl, m.overview, ms.similarity
                    FROM cine.MovieSimilarity ms
                    JOIN cine.Movie m ON m.movieId = ms.movieId2
                    WHERE ms.movieId1 = ?
                    ORDER BY ms.similarity DESC
                """
                
                result = conn.execute(text(query), [movie_id]).fetchall()
                
                related_movies = []
                for row in result:
                    related_movies.append({
                        'movieId': row[0],
                        'title': row[1],
                        'posterUrl': row[2],
                        'overview': row[3] or '',
                        'similarity': float(row[4])
                    })
                
                logger.info(f"Found {len(related_movies)} related movies")
                return related_movies
                
        except Exception as e:
            logger.error(f"Error getting related movies: {e}")
            return []
    
    def get_related_movies_by_genres(self, movie_id: int, limit: int = 10) -> List[Dict]:
        """Get related movies based on genre similarity"""
        try:
            with self.db_engine.connect() as conn:
                query = f"""
                    SELECT TOP {limit} m.movieId, m.title, m.posterUrl, m.overview
                    FROM cine.Movie m
                    WHERE m.movieId != ? 
                    AND m.movieId IN (
                        SELECT mg2.movieId 
                        FROM cine.MovieGenre mg1
                        JOIN cine.MovieGenre mg2 ON mg1.genreId = mg2.genreId
                        WHERE mg1.movieId = ? AND mg2.movieId != ?
                    )
                    ORDER BY m.viewCount DESC
                """
                
                result = conn.execute(text(query), movie_id, movie_id, movie_id).fetchall()
                
                related_movies = []
                for row in result:
                    related_movies.append({
                        'movieId': row[0],
                        'title': row[1],
                        'posterUrl': row[2],
                        'overview': row[3] or '',
                        'similarity': 0.8  # Default similarity for genre-based
                    })
                
                return related_movies
                
        except Exception as e:
            logger.error(f"Error getting genre-based related movies: {e}")
            return []
    
    def get_related_movies_hybrid(self, movie_id: int, limit: int = 10) -> List[Dict]:
        """Get related movies using hybrid approach (content + genre fallback)"""
        # Try content-based first
        related = self.get_related_movies(movie_id, limit)
        
        # If not enough results, supplement with genre-based
        if len(related) < limit:
            genre_based = self.get_related_movies_by_genres(movie_id, limit - len(related))
            # Avoid duplicates
            existing_ids = {m['movieId'] for m in related}
            for movie in genre_based:
                if movie['movieId'] not in existing_ids:
                    related.append(movie)
        
        return related[:limit]
    
    def get_similarity_score(self, movie_id1: int, movie_id2: int) -> float:
        """Get similarity score between two movies"""
        try:
            with self.db_engine.connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT similarity 
                        FROM cine.MovieSimilarity 
                        WHERE movieId1 = ? AND movieId2 = ?
                    """),
                    [movie_id1, movie_id2]
                ).scalar()
                
                return float(result) if result is not None else 0.0
                
        except Exception as e:
            logger.error(f"Error getting similarity score: {e}")
            return 0.0
    
    def get_top_similar_movies(self, limit: int = 5) -> List[Dict]:
        """Get top similar movie pairs"""
        try:
            with self.db_engine.connect() as conn:
                query = f"""
                    SELECT TOP {limit} 
                           m1.title as movie1_title,
                           m2.title as movie2_title,
                           ms.similarity
                    FROM cine.MovieSimilarity ms
                    JOIN cine.Movie m1 ON ms.movieId1 = m1.movieId
                    JOIN cine.Movie m2 ON ms.movieId2 = m2.movieId
                    ORDER BY ms.similarity DESC
                """
                
                result = conn.execute(text(query)).fetchall()
                
                top_pairs = []
                for row in result:
                    top_pairs.append({
                        'movie1_title': row[0],
                        'movie2_title': row[1],
                        'similarity': float(row[2])
                    })
                
                return top_pairs
                
        except Exception as e:
            logger.error(f"Error getting top similar movies: {e}")
            return []
    
    def get_statistics(self) -> Dict:
        """Get recommendation system statistics"""
        try:
            with self.db_engine.connect() as conn:
                # Total similarities
                total_similarities = conn.execute(text("SELECT COUNT(*) FROM cine.MovieSimilarity")).scalar()
                
                # Unique movies
                unique_movies = conn.execute(text("""
                    SELECT COUNT(DISTINCT movieId1) + COUNT(DISTINCT movieId2) - COUNT(DISTINCT CASE WHEN movieId1 = movieId2 THEN movieId1 END)
                    FROM cine.MovieSimilarity
                """)).scalar()
                
                # Average similarity
                avg_similarity = conn.execute(text("SELECT AVG(similarity) FROM cine.MovieSimilarity")).scalar()
                
                # Max similarity
                max_similarity = conn.execute(text("SELECT MAX(similarity) FROM cine.MovieSimilarity")).scalar()
                
                # Min similarity
                min_similarity = conn.execute(text("SELECT MIN(similarity) FROM cine.MovieSimilarity")).scalar()
                
                return {
                    'total_similarities': total_similarities,
                    'unique_movies': unique_movies,
                    'avg_similarity': float(avg_similarity) if avg_similarity else 0.0,
                    'max_similarity': float(max_similarity) if max_similarity else 0.0,
                    'min_similarity': float(min_similarity) if min_similarity else 0.0
                }
                
        except Exception as e:
            logger.error(f"Error getting statistics: {e}")
            return {}

def create_content_recommender(db_engine=None):
    """Factory function to create ContentBasedRecommender"""
    if db_engine is None:
        # Create default engine
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
    
    return ContentBasedRecommender(db_engine)

if __name__ == "__main__":
    # Test the recommender
    print("Content-based Recommendation Service")
    print("=" * 50)
    
    recommender = create_content_recommender()
    
    if recommender.check_similarity_data_exists():
        print("Similarity data found! Testing recommendations...")
        
        # Test with movie ID 1
        related = recommender.get_related_movies(1, 5)
        print(f"\nRelated movies for movie ID 1:")
        for i, movie in enumerate(related, 1):
            print(f"{i}. {movie['title']} (similarity: {movie['similarity']:.3f})")
        
        # Get statistics
        stats = recommender.get_statistics()
        print(f"\nStatistics:")
        print(f"Total similarities: {stats.get('total_similarities', 0)}")
        print(f"Unique movies: {stats.get('unique_movies', 0)}")
        print(f"Average similarity: {stats.get('avg_similarity', 0):.3f}")
    else:
        print("No similarity data found. Please run training first:")
        print("python train_content_based.py")
