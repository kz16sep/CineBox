#!/usr/bin/env python3
"""
Collaborative Filtering Recommender Service
Service class để phục vụ collaborative filtering recommendations cho web app
"""

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
import logging
from typing import List, Dict, Tuple, Optional
import pickle
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class CollaborativeRecommender:
    """
    Collaborative Filtering Recommender Service
    Phục vụ recommendations cho web application
    """
    
    def __init__(self, db_engine, model_path: str = None):
        self.db_engine = db_engine
        if model_path is None:
            # Tự động tìm đường dẫn model
            possible_paths = [
                'model_collaborative/collaborative_model.pkl',
                './model_collaborative/collaborative_model.pkl',
                os.path.join(os.path.dirname(__file__), 'model_collaborative', 'collaborative_model.pkl')
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    self.model_path = path
                    break
            else:
                self.model_path = 'model_collaborative/collaborative_model.pkl'
        else:
            self.model_path = model_path
        self.user_factors = None
        self.item_factors = None
        self.user_similarity_matrix = None
        self.item_similarity_matrix = None
        self.user_mapping = {}
        self.item_mapping = {}
        self.reverse_user_mapping = {}
        self.reverse_item_mapping = {}
        self.user_item_matrix = None
        self.model_loaded = False
        
        # Load model if exists
        self.load_model()
    
    def load_model(self) -> bool:
        """Load collaborative filtering model"""
        try:
            print(f"Looking for model at: {self.model_path}")
            print(f"Current working directory: {os.getcwd()}")
            print(f"Model exists: {os.path.exists(self.model_path)}")
            
            if not os.path.exists(self.model_path):
                logger.warning(f"Model file not found: {self.model_path}")
                return False
            
            with open(self.model_path, 'rb') as f:
                model_data = pickle.load(f)
            
            self.user_factors = model_data['user_factors']
            self.item_factors = model_data['item_factors']
            self.user_similarity_matrix = model_data['user_similarity_matrix']
            self.item_similarity_matrix = model_data['item_similarity_matrix']
            self.user_mapping = model_data['user_mapping']
            self.item_mapping = model_data['item_mapping']
            self.reverse_user_mapping = model_data['reverse_user_mapping']
            self.reverse_item_mapping = model_data['reverse_item_mapping']
            self.user_item_matrix = model_data['user_item_matrix']
            
            self.model_loaded = True
            logger.info("Collaborative filtering model loaded successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error loading collaborative model: {e}")
            return False
    
    def get_user_recommendations(self, user_id: int, limit: int = 10) -> List[Dict]:
        """
        Lấy danh sách phim được recommend cho user
        
        Args:
            user_id: ID của user
            limit: Số lượng recommendations tối đa
            
        Returns:
            List[Dict]: Danh sách phim được recommend với thông tin chi tiết
        """
        if not self.model_loaded:
            logger.error("Model not loaded")
            return []
        
        try:
            # Get recommendations from model
            recommendations = self._get_user_recommendations_internal(user_id, limit * 2)  # Get more for filtering
            
            if not recommendations:
                logger.warning(f"No recommendations found for user {user_id}")
                return []
            
            # Get movie details from database
            movie_ids = [rec[0] for rec in recommendations]
            movie_details = self._get_movie_details(movie_ids)
            
            # Combine recommendations with movie details
            result = []
            for movie_id, score in recommendations:
                if movie_id in movie_details:
                    movie_info = movie_details[movie_id]
                    movie_info['recommendation_score'] = round(score, 4)
                    result.append(movie_info)
                    
                    if len(result) >= limit:
                        break
            
            logger.info(f"Generated {len(result)} recommendations for user {user_id}")
            return result
            
        except Exception as e:
            logger.error(f"Error getting user recommendations: {e}")
            return []
    
    def _get_user_recommendations_internal(self, user_id: int, n_recommendations: int) -> List[Tuple[int, float]]:
        """Internal method to get user recommendations"""
        if user_id not in self.user_mapping:
            logger.warning(f"User {user_id} not found in model")
            return []
        
        user_idx = self.user_mapping[user_id]
        
        # Get user's rated items
        if isinstance(self.user_item_matrix, pd.DataFrame):
            # DataFrame case - get non-zero ratings
            user_ratings = self.user_item_matrix.iloc[user_idx]
            rated_items = set(user_ratings[user_ratings > 0].index)
        elif hasattr(self.user_item_matrix[user_idx], 'indices'):
            # Sparse matrix case
            rated_items = set(self.user_item_matrix[user_idx].indices)
        else:
            # Fallback: get rated items from database
            rated_items = set()
            try:
                with self.db_engine.connect() as conn:
                    result = conn.execute(text("""
                        SELECT movieId FROM cine.Rating WHERE userId = :user_id
                    """), {"user_id": user_id})
                    rated_items = set(row[0] for row in result.fetchall())
            except Exception as e:
                logger.warning(f"Could not get rated items from database: {e}")
        
        # Calculate scores for all items
        user_vector = self.user_factors[user_idx]
        scores = user_vector @ self.item_factors.T
        
        # Filter out already rated items and get top recommendations
        recommendations = []
        for item_idx, score in enumerate(scores):
            item_id = self.reverse_item_mapping[item_idx]
            if item_id not in rated_items:
                recommendations.append((item_id, float(score)))
        
        # Sort by score and return top N
        recommendations.sort(key=lambda x: x[1], reverse=True)
        return recommendations[:n_recommendations]
    
    def get_similar_users(self, user_id: int, limit: int = 10) -> List[Dict]:
        """
        Lấy danh sách users tương tự
        
        Args:
            user_id: ID của user
            limit: Số lượng similar users tối đa
            
        Returns:
            List[Dict]: Danh sách similar users với thông tin chi tiết
        """
        if not self.model_loaded:
            logger.error("Model not loaded")
            return []
        
        try:
            if user_id not in self.user_mapping:
                logger.warning(f"User {user_id} not found in model")
                return []
            
            user_idx = self.user_mapping[user_id]
            similarities = self.user_similarity_matrix[user_idx]
            
            # Get similar users (excluding self)
            similar_users = []
            for other_user_idx, similarity in enumerate(similarities):
                if other_user_idx != user_idx:
                    other_user_id = self.reverse_user_mapping[other_user_idx]
                    similar_users.append((other_user_id, float(similarity)))
            
            # Sort by similarity and get top N
            similar_users.sort(key=lambda x: x[1], reverse=True)
            similar_users = similar_users[:limit]
            
            # Get user details from database
            user_ids = [user[0] for user in similar_users]
            user_details = self._get_user_details(user_ids)
            
            # Combine similarities with user details
            result = []
            for user_id_sim, similarity in similar_users:
                if user_id_sim in user_details:
                    user_info = user_details[user_id_sim]
                    user_info['similarity_score'] = round(similarity, 4)
                    result.append(user_info)
            
            logger.info(f"Found {len(result)} similar users for user {user_id}")
            return result
            
        except Exception as e:
            logger.error(f"Error getting similar users: {e}")
            return []
    
    def get_similar_movies(self, movie_id: int, limit: int = 10) -> List[Dict]:
        """
        Lấy danh sách phim tương tự
        
        Args:
            movie_id: ID của phim
            limit: Số lượng similar movies tối đa
            
        Returns:
            List[Dict]: Danh sách similar movies với thông tin chi tiết
        """
        if not self.model_loaded:
            logger.error("Model not loaded")
            return []
        
        try:
            if movie_id not in self.item_mapping:
                logger.warning(f"Movie {movie_id} not found in model")
                return []
            
            item_idx = self.item_mapping[movie_id]
            similarities = self.item_similarity_matrix[item_idx]
            
            # Get similar items (excluding self)
            similar_items = []
            for other_item_idx, similarity in enumerate(similarities):
                if other_item_idx != item_idx:
                    other_item_id = self.reverse_item_mapping[other_item_idx]
                    similar_items.append((other_item_id, float(similarity)))
            
            # Sort by similarity and get top N
            similar_items.sort(key=lambda x: x[1], reverse=True)
            similar_items = similar_items[:limit]
            
            # Get movie details from database
            movie_ids = [item[0] for item in similar_items]
            movie_details = self._get_movie_details(movie_ids)
            
            # Combine similarities with movie details
            result = []
            for movie_id_sim, similarity in similar_items:
                if movie_id_sim in movie_details:
                    movie_info = movie_details[movie_id_sim]
                    movie_info['similarity_score'] = round(similarity, 4)
                    result.append(movie_info)
            
            logger.info(f"Found {len(result)} similar movies for movie {movie_id}")
            return result
            
        except Exception as e:
            logger.error(f"Error getting similar movies: {e}")
            return []
    
    def get_trending_movies(self, limit: int = 20) -> List[Dict]:
        """
        Lấy danh sách phim trending (dựa trên số lượng ratings)
        
        Args:
            limit: Số lượng phim trending tối đa
            
        Returns:
            List[Dict]: Danh sách phim trending
        """
        try:
            with self.db_engine.connect() as conn:
                query = text("""
                    SELECT TOP (:limit)
                        m.movieId, m.title, m.releaseYear, m.country, m.posterUrl,
                        m.viewCount, COUNT(r.movieId) as ratingCount,
                        AVG(CAST(r.value AS FLOAT)) as avgRating,
                        STRING_AGG(g.name, ', ') WITHIN GROUP (ORDER BY g.name) as genres
                    FROM cine.Movie m
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                    LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                    WHERE m.movieId IN (
                        SELECT movieId FROM cine.Movie 
                        WHERE movieId IN (SELECT DISTINCT movieId FROM cine.Rating)
                    )
                    GROUP BY m.movieId, m.title, m.releaseYear, m.country, m.posterUrl, m.viewCount
                    ORDER BY ratingCount DESC, avgRating DESC
                """)
                
                result = conn.execute(query, {"limit": limit})
                movies = []
                
                for row in result:
                    movie = {
                        'movieId': row.movieId,
                        'title': row.title,
                        'releaseYear': row.releaseYear,
                        'country': row.country,
                        'posterUrl': row.posterUrl,
                        'viewCount': row.viewCount,
                        'ratingCount': row.ratingCount,
                        'avgRating': round(float(row.avgRating), 2) if row.avgRating else 0.0,
                        'genres': row.genres or ''
                    }
                    movies.append(movie)
                
                logger.info(f"Retrieved {len(movies)} trending movies")
                return movies
                
        except Exception as e:
            logger.error(f"Error getting trending movies: {e}")
            return []
    
    def get_user_rating_history(self, user_id: int, limit: int = 20) -> List[Dict]:
        """
        Lấy lịch sử đánh giá của user
        
        Args:
            user_id: ID của user
            limit: Số lượng ratings tối đa
            
        Returns:
            List[Dict]: Lịch sử đánh giá của user
        """
        try:
            with self.db_engine.connect() as conn:
                query = text("""
                    SELECT TOP (:limit)
                        m.movieId, m.title, m.releaseYear, m.posterUrl,
                        r.value as rating, r.ratedAt,
                        STRING_AGG(g.name, ', ') WITHIN GROUP (ORDER BY g.name) as genres
                    FROM cine.Rating r
                    INNER JOIN cine.Movie m ON r.movieId = m.movieId
                    LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                    LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                    WHERE r.userId = :user_id
                    GROUP BY m.movieId, m.title, m.releaseYear, m.posterUrl, r.value, r.ratedAt
                    ORDER BY r.ratedAt DESC
                """)
                
                result = conn.execute(query, {"user_id": user_id, "limit": limit})
                ratings = []
                
                for row in result:
                    rating = {
                        'movieId': row.movieId,
                        'title': row.title,
                        'releaseYear': row.releaseYear,
                        'posterUrl': row.posterUrl,
                        'rating': row.rating,
                        'ratedAt': row.ratedAt.isoformat() if row.ratedAt else None,
                        'genres': row.genres or ''
                    }
                    ratings.append(rating)
                
                logger.info(f"Retrieved {len(ratings)} ratings for user {user_id}")
                return ratings
                
        except Exception as e:
            logger.error(f"Error getting user rating history: {e}")
            return []
    
    def _get_movie_details(self, movie_ids: List[int]) -> Dict[int, Dict]:
        """Lấy thông tin chi tiết của movies từ database"""
        if not movie_ids:
            return {}
        
        try:
            with self.db_engine.connect() as conn:
                # Create placeholders for IN clause
                placeholders = ','.join([f':id{i}' for i in range(len(movie_ids))])
                params = {f'id{i}': int(movie_id) for i, movie_id in enumerate(movie_ids)}
                
                query = text(f"""
                    SELECT 
                        m.movieId, m.title, m.releaseYear, m.country, m.posterUrl,
                        m.viewCount, m.overview,
                        AVG(CAST(r.value AS FLOAT)) as avgRating,
                        COUNT(r.movieId) as ratingCount,
                        STUFF((
                            SELECT TOP 5 ', ' + g2.name
                            FROM cine.MovieGenre mg2
                            JOIN cine.Genre g2 ON mg2.genreId = g2.genreId
                            WHERE mg2.movieId = m.movieId
                            ORDER BY g2.name
                            FOR XML PATH('')
                        ), 1, 2, '') as genres
                    FROM cine.Movie m
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    WHERE m.movieId IN ({placeholders})
                    GROUP BY m.movieId, m.title, m.releaseYear, m.country, m.posterUrl, m.viewCount, m.overview
                """)
                
                result = conn.execute(query, params)
                movies = {}
                
                for row in result:
                    movies[row.movieId] = {
                        'movieId': row.movieId,
                        'title': row.title,
                        'releaseYear': row.releaseYear,
                        'country': row.country,
                        'posterUrl': row.posterUrl,
                        'viewCount': row.viewCount,
                        'overview': row.overview,
                        'avgRating': round(float(row.avgRating), 2) if row.avgRating else 0.0,
                        'ratingCount': row.ratingCount,
                        'genres': row.genres or ''
                    }
                
                return movies
                
        except Exception as e:
            logger.error(f"Error getting movie details: {e}")
            return {}
    
    def _get_user_details(self, user_ids: List[int]) -> Dict[int, Dict]:
        """Lấy thông tin chi tiết của users từ database"""
        if not user_ids:
            return {}
        
        try:
            with self.db_engine.connect() as conn:
                # Create placeholders for IN clause
                placeholders = ','.join([f':id{i}' for i in range(len(user_ids))])
                params = {f'id{i}': int(user_id) for i, user_id in enumerate(user_ids)}
                
                query = text(f"""
                    SELECT 
                        u.userId, u.email, u.avatarUrl, u.createdAt, u.lastLoginAt,
                        COUNT(r.movieId) as ratingCount,
                        AVG(CAST(r.value AS FLOAT)) as avgRating
                    FROM cine.[User] u
                    LEFT JOIN cine.Rating r ON u.userId = r.userId
                    WHERE u.userId IN ({placeholders})
                    GROUP BY u.userId, u.email, u.avatarUrl, u.createdAt, u.lastLoginAt
                """)
                
                result = conn.execute(query, params)
                users = {}
                
                for row in result:
                    users[row.userId] = {
                        'userId': row.userId,
                        'email': row.email,
                        'avatarUrl': row.avatarUrl,
                        'createdAt': row.createdAt.isoformat() if row.createdAt else None,
                        'lastLoginAt': row.lastLoginAt.isoformat() if row.lastLoginAt else None,
                        'ratingCount': row.ratingCount,
                        'avgRating': round(float(row.avgRating), 2) if row.avgRating else 0.0
                    }
                
                return users
                
        except Exception as e:
            logger.error(f"Error getting user details: {e}")
            return {}
    
    def is_model_loaded(self) -> bool:
        """Kiểm tra xem model đã được load chưa"""
        return self.model_loaded
    
    def get_model_info(self) -> Dict:
        """Lấy thông tin về model"""
        if not self.model_loaded:
            return {"status": "not_loaded"}
        
        return {
            "status": "loaded",
            "n_users": len(self.user_mapping),
            "n_items": len(self.item_mapping),
            "model_path": self.model_path,
            "user_factors_shape": self.user_factors.shape if self.user_factors is not None else None,
            "item_factors_shape": self.item_factors.shape if self.item_factors is not None else None
        }
