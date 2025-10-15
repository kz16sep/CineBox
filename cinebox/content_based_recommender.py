#!/usr/bin/env python3
"""
Content-Based Recommender Service
Service class để phục vụ content-based recommendations cho web app
"""

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
import logging
from typing import List, Dict, Tuple

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ContentBasedRecommender:
    """
    Content-Based Recommender Service
    Phục vụ recommendations cho web application
    """
    
    def __init__(self, db_engine):
        self.db_engine = db_engine
    
    def get_related_movies(self, movie_id: int, limit: int = 5) -> List[Dict]:
        """
        Lấy danh sách phim liên quan cho một phim cụ thể
        
        Args:
            movie_id: ID của phim cần tìm phim liên quan
            limit: Số lượng phim liên quan tối đa
            
        Returns:
            List[Dict]: Danh sách phim liên quan với thông tin chi tiết
        """
        try:
            with self.db_engine.connect() as conn:
                # Lấy phim liên quan từ MovieSimilarity
                query = text(f"""
                    SELECT TOP {limit}
                        m.movieId, m.title, m.releaseYear, m.country, m.posterUrl,
                        ms.similarity,
                        STRING_AGG(g.name, ', ') as genres
                    FROM cine.MovieSimilarity ms
                    JOIN cine.Movie m ON ms.movieId2 = m.movieId
                    LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                    LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                    WHERE ms.movieId1 = :movie_id
                    GROUP BY m.movieId, m.title, m.releaseYear, m.country, m.posterUrl, ms.similarity
                    ORDER BY ms.similarity DESC
                """)
                
                result = conn.execute(query, {'movie_id': movie_id})
                rows = result.fetchall()
                
                related_movies = []
                for row in rows:
                    # Clean genres (remove duplicates)
                    genres = row[6] or 'No genres'
                    if genres != 'No genres':
                        genre_list = list(set(genres.split(', ')))
                        genres = ', '.join(genre_list)
                    
                    related_movies.append({
                        'movieId': row[0],
                        'title': row[1],
                        'releaseYear': row[2],
                        'country': row[3] or 'Unknown',
                        'posterUrl': row[4],
                        'similarity': float(row[5]),
                        'genres': genres
                    })
                
                logger.info(f"Found {len(related_movies)} related movies for movie {movie_id}")
                return related_movies
                
        except Exception as e:
            logger.error(f"Error getting related movies for {movie_id}: {e}")
            return []
    
    def get_movie_info(self, movie_id: int) -> Dict:
        """
        Lấy thông tin chi tiết của một phim
        
        Args:
            movie_id: ID của phim
            
        Returns:
            Dict: Thông tin chi tiết của phim
        """
        try:
            with self.db_engine.connect() as conn:
                query = text("""
                    SELECT m.movieId, m.title, m.releaseYear, m.country, m.overview, 
                           m.posterUrl, m.backdropUrl, m.viewCount,
                           STRING_AGG(g.name, ', ') as genres
                    FROM cine.Movie m
                    LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                    LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                    WHERE m.movieId = :movie_id
                    GROUP BY m.movieId, m.title, m.releaseYear, m.country, 
                             m.overview, m.posterUrl, m.backdropUrl, m.viewCount
                """)
                
                result = conn.execute(query, {'movie_id': movie_id})
                row = result.fetchone()
                
                if not row:
                    return None
                
                # Clean genres
                genres = row[8] or 'No genres'
                if genres != 'No genres':
                    genre_list = list(set(genres.split(', ')))
                    genres = ', '.join(genre_list)
                
                return {
                    'movieId': row[0],
                    'title': row[1],
                    'releaseYear': row[2],
                    'country': row[3] or 'Unknown',
                    'overview': row[4] or 'No overview available',
                    'posterUrl': row[5],
                    'backdropUrl': row[6],
                    'viewCount': row[7] or 0,
                    'genres': genres
                }
                
        except Exception as e:
            logger.error(f"Error getting movie info for {movie_id}: {e}")
            return None
    
    def get_similarity_score(self, movie_id1: int, movie_id2: int) -> float:
        """
        Lấy điểm similarity giữa hai phim
        
        Args:
            movie_id1: ID phim thứ nhất
            movie_id2: ID phim thứ hai
            
        Returns:
            float: Điểm similarity (0.0 - 1.0)
        """
        try:
            with self.db_engine.connect() as conn:
                query = text("""
                    SELECT similarity FROM cine.MovieSimilarity 
                    WHERE movieId1 = :movie_id1 AND movieId2 = :movie_id2
                """)
                
                result = conn.execute(query, {
                    'movie_id1': movie_id1,
                    'movie_id2': movie_id2
                })
                row = result.fetchone()
                
                if row:
                    return float(row[0])
                else:
                    return 0.0
                    
        except Exception as e:
            logger.error(f"Error getting similarity between {movie_id1} and {movie_id2}: {e}")
            return 0.0
    
    def get_recommendation_stats(self) -> Dict:
        """
        Lấy thống kê về hệ thống recommendation
        
        Returns:
            Dict: Thống kê chi tiết
        """
        try:
            with self.db_engine.connect() as conn:
                # Tổng số phim
                total_movies = conn.execute(text("SELECT COUNT(*) FROM cine.Movie")).scalar()
                
                # Tổng số similarity pairs
                total_similarities = conn.execute(text("SELECT COUNT(*) FROM cine.MovieSimilarity")).scalar()
                
                # Thống kê similarity
                similarity_stats = conn.execute(text("""
                    SELECT 
                        AVG(similarity) as avg_similarity,
                        MIN(similarity) as min_similarity,
                        MAX(similarity) as max_similarity,
                        COUNT(CASE WHEN similarity >= 0.9 THEN 1 END) as high_sim,
                        COUNT(CASE WHEN similarity >= 0.7 AND similarity < 0.9 THEN 1 END) as medium_sim,
                        COUNT(CASE WHEN similarity < 0.7 THEN 1 END) as low_sim
                    FROM cine.MovieSimilarity
                """)).fetchone()
                
                return {
                    'total_movies': total_movies,
                    'total_similarities': total_similarities,
                    'avg_similarity': float(similarity_stats[0]) if similarity_stats[0] else 0.0,
                    'min_similarity': float(similarity_stats[1]) if similarity_stats[1] else 0.0,
                    'max_similarity': float(similarity_stats[2]) if similarity_stats[2] else 0.0,
                    'high_similarity_count': similarity_stats[3],
                    'medium_similarity_count': similarity_stats[4],
                    'low_similarity_count': similarity_stats[5]
                }
                
        except Exception as e:
            logger.error(f"Error getting recommendation stats: {e}")
            return {
                'total_movies': 0,
                'total_similarities': 0,
                'avg_similarity': 0.0,
                'min_similarity': 0.0,
                'max_similarity': 0.0,
                'high_similarity_count': 0,
                'medium_similarity_count': 0,
                'low_similarity_count': 0
            }
