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
            # Validate và sanitize limit để tránh SQL injection
            from app.sql_helpers import validate_limit, safe_top_clause
            validated_limit = validate_limit(limit, max_limit=100, default=5)
            top_clause = safe_top_clause(validated_limit, max_limit=100)
            
            with self.db_engine.connect() as conn:
                # Lấy phim liên quan từ MovieSimilarity
                # Sử dụng validated limit thay vì f-string trực tiếp
                query = text(f"""
                    SELECT {top_clause}
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
                
                # Kiểm tra nếu tất cả similarities đều = 1.0 (có vấn đề với dữ liệu)
                if related_movies and all(movie['similarity'] == 1.0 for movie in related_movies):
                    logger.warning(f"All similarities are 1.0 for movie {movie_id}, using fallback")
                    return self._get_fallback_recommendations(movie_id, limit)
                
                logger.info(f"Found {len(related_movies)} related movies for movie {movie_id}")
                return related_movies
                
        except Exception as e:
            logger.error(f"Error getting related movies for {movie_id}: {e}")
            return self._get_fallback_recommendations(movie_id, limit)
    
    def _get_fallback_recommendations(self, movie_id: int, limit: int) -> List[Dict]:
        """
        Fallback recommendations khi không có similarities hoặc similarities không đúng
        
        Args:
            movie_id: ID của phim cần tìm phim liên quan
            limit: Số lượng phim liên quan tối đa
            
        Returns:
            List[Dict]: Danh sách phim liên quan fallback
        """
        try:
            # Validate và sanitize limit để tránh SQL injection
            from app.sql_helpers import validate_limit, safe_top_clause
            validated_limit = validate_limit(limit, max_limit=100, default=5)
            # Tính toán limit * 2 một cách an toàn
            fallback_limit = min(validated_limit * 2, 200)  # Cap at 200
            top_clause = safe_top_clause(fallback_limit, max_limit=200)
            
            with self.db_engine.connect() as conn:
                # Lấy phim ngẫu nhiên với genres tương tự (nếu có)
                # Sử dụng validated limit thay vì f-string trực tiếp
                query = text(f"""
                    SELECT {top_clause}
                        m.movieId, m.title, m.releaseYear, m.country, m.posterUrl,
                        STRING_AGG(g.name, ', ') as genres
                    FROM cine.Movie m
                    LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                    LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                    WHERE m.movieId != :movie_id
                    GROUP BY m.movieId, m.title, m.releaseYear, m.country, m.posterUrl
                    ORDER BY NEWID()
                """)
                
                result = conn.execute(query, {'movie_id': movie_id})
                rows = result.fetchall()
                
                related_movies = []
                for i, row in enumerate(rows[:limit]):
                    # Clean genres
                    genres = row[5] or 'No genres'
                    if genres != 'No genres':
                        genre_list = list(set(genres.split(', ')))
                        genres = ', '.join(genre_list)
                    
                    # Tạo similarity giả lập dựa trên thứ tự (giảm dần)
                    fake_similarity = 0.9 - (i * 0.1)
                    if fake_similarity < 0.1:
                        fake_similarity = 0.1
                    
                    related_movies.append({
                        'movieId': row[0],
                        'title': row[1],
                        'releaseYear': row[2],
                        'country': row[3] or 'Unknown',
                        'posterUrl': row[4],
                        'similarity': fake_similarity,
                        'genres': genres
                    })
                
                logger.info(f"Using fallback recommendations for movie {movie_id}")
                return related_movies
                
        except Exception as e:
            logger.error(f"Error getting fallback recommendations for {movie_id}: {e}")
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
    
    def get_user_recommendations(self, user_id: int, limit: int = 10) -> List[Dict]:
        """
        Lấy danh sách phim được recommend cho user dựa trên content-based approach.
        Dựa trên các phim user đã tương tác (rated, viewed, favorited), tìm các phim tương tự.
        
        Args:
            user_id: ID của user
            limit: Số lượng phim được recommend tối đa
            
        Returns:
            List[Dict]: Danh sách phim được recommend với thông tin chi tiết
        """
        try:
            from app.sql_helpers import validate_limit, safe_top_clause
            validated_limit = validate_limit(limit, max_limit=100, default=10)
            top_clause = safe_top_clause(validated_limit * 2, max_limit=200)  # Lấy nhiều hơn để merge
            
            with self.db_engine.connect() as conn:
                # Lấy các phim user đã tương tác tích cực (để tìm phim tương tự)
                # Chỉ lấy phim user thích (rated >= 3.5, viewed >= 70%, favorited)
                user_movies_query = text("""
                    SELECT DISTINCT movieId FROM (
                        -- Rated >= 3.5
                        SELECT movieId FROM cine.Rating 
                        WHERE userId = :user_id AND value >= 3.5
                        UNION
                        -- Viewed (completed >= 70%)
                        SELECT vh.movieId FROM cine.ViewHistory vh
                        INNER JOIN cine.Movie m ON vh.movieId = m.movieId
                        WHERE vh.userId = :user_id 
                        AND (vh.finishedAt IS NOT NULL 
                             OR (m.durationMin > 0 AND CAST(vh.progressSec AS FLOAT) / 60.0 / m.durationMin >= 0.7))
                        UNION
                        -- Favorited
                        SELECT movieId FROM cine.Favorite 
                        WHERE userId = :user_id
                    ) AS user_movies
                """)
                
                # Đảm bảo user_id là int
                user_id = int(user_id) if user_id is not None else None
                if user_id is None:
                    logger.error("Invalid user_id: None")
                    return []
                
                user_movies_result = conn.execute(user_movies_query, {'user_id': user_id})
                user_movie_ids = [int(row[0]) for row in user_movies_result.fetchall() if row[0] is not None]
                
                if not user_movie_ids:
                    logger.info(f"User {user_id} has no positive interactions, using popular movies fallback")
                    return self._get_popular_movies_fallback(user_id, validated_limit)
                
                # Lấy tất cả phim user đã xem/rated để loại bỏ khỏi recommendations
                # Bao gồm: tất cả rated, tất cả viewed >= 70% hoặc finished
                # Loại bỏ phim đang xem dở (< 70% và chưa finished) để có thể recommend tiếp tục xem
                exclude_movies_query = text("""
                    SELECT DISTINCT movieId FROM (
                        -- Tất cả phim đã rated
                        SELECT movieId FROM cine.Rating 
                        WHERE userId = :user_id
                        UNION
                        -- Tất cả phim đã xem hoàn thành (>= 70% hoặc finished)
                        SELECT vh.movieId FROM cine.ViewHistory vh
                        INNER JOIN cine.Movie m ON vh.movieId = m.movieId
                        WHERE vh.userId = :user_id 
                        AND (
                            vh.finishedAt IS NOT NULL 
                            OR (m.durationMin > 0 AND CAST(vh.progressSec AS FLOAT) / 60.0 / m.durationMin >= 0.7)
                        )
                    ) AS exclude_movies
                """)
                
                exclude_result = conn.execute(exclude_movies_query, {'user_id': user_id})
                exclude_movie_ids = [int(row[0]) for row in exclude_result.fetchall() if row[0] is not None]
                
                # Lấy các phim tương tự từ MovieSimilarity
                # Aggregate similarity scores từ nhiều phim user đã tương tác
                placeholders = ','.join([f':movie_id{i}' for i in range(len(user_movie_ids))])
                params = {f'movie_id{i}': mid for i, mid in enumerate(user_movie_ids)}
                
                # Thêm exclude_movie_ids vào params để filter (nếu có)
                exclude_condition = ""
                if exclude_movie_ids:
                    exclude_placeholders = ','.join([f':exclude_movie_id{i}' for i in range(len(exclude_movie_ids))])
                    exclude_params = {f'exclude_movie_id{i}': mid for i, mid in enumerate(exclude_movie_ids)}
                    params.update(exclude_params)
                    exclude_condition = f"AND ms.movieId2 NOT IN ({exclude_placeholders})"
                
                similar_movies_query = text(f"""
                    SELECT {top_clause}
                        ms.movieId2 as movieId,
                        MAX(ms.similarity) as max_similarity,
                        AVG(ms.similarity) as avg_similarity,
                        COUNT(*) as source_count
                    FROM cine.MovieSimilarity ms
                    WHERE ms.movieId1 IN ({placeholders})
                    AND ms.movieId2 NOT IN ({placeholders})  -- Loại bỏ phim user đã tương tác tích cực
                    {exclude_condition}  -- Loại bỏ tất cả phim đã xem/rated (nếu có)
                    GROUP BY ms.movieId2
                    ORDER BY max_similarity DESC, avg_similarity DESC, source_count DESC
                """)
                
                similar_result = conn.execute(similar_movies_query, params)
                similar_rows = similar_result.fetchall()
                
                if not similar_rows:
                    logger.info(f"No similar movies found for user {user_id}")
                    return []
                
                # Lấy thông tin chi tiết của các phim
                similar_movie_ids = [row[0] for row in similar_rows]
                
                # Tạo placeholders mới cho similar_movie_ids (không dùng placeholders cũ từ user_movie_ids)
                if not similar_movie_ids:
                    logger.warning(f"No similar movie IDs to query details for user {user_id}")
                    return []
                
                movie_details_placeholders = ','.join([f':movie_detail_id{i}' for i in range(len(similar_movie_ids))])
                movie_details_query = text(f"""
                    SELECT 
                        m.movieId, m.title, m.posterUrl, m.releaseYear, m.country,
                        STRING_AGG(g.name, ', ') as genres
                    FROM cine.Movie m
                    LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                    LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                    WHERE m.movieId IN ({movie_details_placeholders})
                    GROUP BY m.movieId, m.title, m.posterUrl, m.releaseYear, m.country
                """)
                
                movie_details_params = {f'movie_detail_id{i}': mid for i, mid in enumerate(similar_movie_ids)}
                movie_details_result = conn.execute(movie_details_query, movie_details_params)
                movie_details = {row[0]: {
                    'movieId': row[0],
                    'title': row[1],
                    'posterUrl': row[2],
                    'releaseYear': row[3],
                    'country': row[4],
                    'genres': row[5] or ''
                } for row in movie_details_result.fetchall()}
                
                # Kết hợp similarity scores với movie details
                recommendations = []
                for row in similar_rows[:validated_limit]:
                    movie_id = int(row[0]) if row[0] is not None else None
                    if movie_id is None or movie_id not in movie_details:
                        continue
                    
                    movie = movie_details[movie_id]
                    # Clamp similarity để tránh quá gần 1.0 (< 0.98)
                    max_sim = float(row[1]) if row[1] is not None else 0.0
                    avg_sim = float(row[2]) if row[2] is not None else 0.0
                    # Clamp similarity values
                    max_sim = min(0.98, max(0.0, max_sim))
                    avg_sim = min(0.98, max(0.0, avg_sim))
                    
                    recommendations.append({
                        'movieId': movie_id,  # Đảm bảo là int
                        'title': movie['title'],
                        'posterUrl': movie['posterUrl'],
                        'releaseYear': movie['releaseYear'],
                        'country': movie['country'],
                        'genres': movie['genres'],
                        'similarity': max_sim,  # max_similarity (clamped < 0.98)
                        'avg_similarity': avg_sim,  # avg_similarity (clamped < 0.98)
                        'source_count': int(row[3]) if row[3] is not None else 0,  # source_count
                        'score': max_sim * 0.7 + avg_sim * 0.3  # Weighted score
                    })
                
                logger.info(f"Generated {len(recommendations)} content-based recommendations for user {user_id}")
                return recommendations
                
        except Exception as e:
            logger.error(f"Error getting content-based recommendations for user {user_id}: {e}")
            return []
    
    def _get_popular_movies_fallback(self, user_id: int, limit: int = 10) -> List[Dict]:
        """
        Fallback method: Lấy phim phổ biến khi Content-Based không có gợi ý
        """
        try:
            with self.db_engine.connect() as conn:
                # Lấy phim phổ biến mà user chưa xem
                popular_movies = conn.execute(text("""
                    WITH rating_stats AS (
                        SELECT movieId,
                               AVG(CAST(value AS FLOAT)) AS avgRating,
                               COUNT(*) AS ratingCount
                        FROM cine.Rating
                        GROUP BY movieId
                    )
                    SELECT TOP (:limit) 
                        m.movieId,
                        m.title,
                        m.posterUrl,
                        m.releaseYear,
                        m.country,
                        rs.avgRating,
                        rs.ratingCount,
                        STRING_AGG(g.name, ', ') AS genres
                    FROM cine.Movie m
                    JOIN rating_stats rs ON rs.movieId = m.movieId
                    LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                    LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                    WHERE rs.avgRating >= 4.0
                      AND rs.ratingCount >= 5
                      AND m.movieId NOT IN (
                          SELECT COALESCE(vh.movieId, 0) FROM cine.ViewHistory vh WHERE vh.userId = :user_id
                      )
                      AND m.movieId NOT IN (
                          SELECT COALESCE(r.movieId, 0) FROM cine.Rating r WHERE r.userId = :user_id
                      )
                    GROUP BY m.movieId, m.title, m.posterUrl, m.releaseYear, 
                             m.country, rs.avgRating, rs.ratingCount
                    ORDER BY rs.avgRating DESC, rs.ratingCount DESC
                """), {"limit": limit, "user_id": user_id}).mappings().all()
                
                recommendations = []
                for i, movie in enumerate(popular_movies):
                    recommendations.append({
                        "movieId": movie["movieId"],
                        "id": movie["movieId"],
                        "title": movie["title"],
                        "poster": movie["posterUrl"],
                        "posterUrl": movie["posterUrl"],
                        "releaseYear": movie["releaseYear"],
                        "country": movie["country"],
                        "avgRating": float(movie["avgRating"]) if movie["avgRating"] else 0.0,
                        "ratingCount": movie["ratingCount"] or 0,
                        "genres": movie["genres"] or "",
                        "similarity": 0.7 - (i * 0.03),  # Giảm dần similarity
                        "reason": f"Phim phổ biến (Top {i+1})",
                        "source": "cb_fallback",
                        "rank": i + 1
                    })
                
                logger.info(f"CB Fallback: Found {len(recommendations)} popular movies for user {user_id}")
                return recommendations
                
        except Exception as e:
            logger.error(f"Error in CB fallback for user {user_id}: {e}")
            return []

