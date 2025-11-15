#!/usr/bin/env python3
"""
Enhanced Collaborative Filtering Recommender
Sử dụng tất cả dữ liệu tương tác với trọng số và time decay
"""

import os
import sys
import pandas as pd
import numpy as np
import math
from sqlalchemy import text
import logging
from datetime import datetime
import pickle
from typing import List, Dict, Tuple, Optional
import threading
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class EnhancedCFRecommender:
    """
    Enhanced Collaborative Filtering Recommender
    Sử dụng tất cả dữ liệu tương tác với trọng số và time decay
    """
    
    def __init__(self, db_engine, model_path: str = None, lazy_load: bool = True, background_load: bool = True):
        self.db_engine = db_engine
        
        if model_path is None:
            # Use absolute path for model - 2 levels up (recommenders -> cinebox)
            base_dir = os.path.dirname(os.path.dirname(__file__))
            # Try enhanced model first, fallback to collaborative model
            enhanced_path = os.path.join(base_dir, 'model_collaborative', 'enhanced_cf_model.pkl')
            collaborative_path = os.path.join(base_dir, 'model_collaborative', 'collaborative_model.pkl')
            
            if os.path.exists(enhanced_path):
                self.model_path = enhanced_path
            elif os.path.exists(collaborative_path):
                self.model_path = collaborative_path
            else:
                self.model_path = enhanced_path  # Default to enhanced path
        else:
            self.model_path = model_path
            
        # Model data
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
        
        # Loading state management
        self._loading = False
        self._load_lock = threading.Lock()
        self._load_error = None
        self._lazy_load = lazy_load
        self._background_load = background_load
        
        # Interaction weights for scoring (Updated weights)
        self.interaction_weights = {
            'view_history': 1.0,   # Completed View ≥70% - Tín hiệu mạnh nhất
            'rating': 0.75,         # Hành vi rõ ràng, tin cậy
            'favorite': 0.35,       # Trung bình
            'comment': 0.20,        # Có thể tiêu cực/không chắc
            'watchlist': 0.18,      # Ý định, chưa chắc chán
            'cold_start': 0.05
        }
        
        # Load model based on strategy
        if not lazy_load:
            # Immediate loading (blocking)
            self.load_model()
        elif background_load:
            # Background loading (non-blocking)
            self._start_background_load()
        # else: lazy_load=True and background_load=False -> load on first use
    
    def _start_background_load(self):
        """Start loading model in background thread"""
        def background_loader():
            try:
                logger.info("Starting background model loading...")
                self.load_model()
                logger.info("Background model loading completed")
            except Exception as e:
                logger.error(f"Background model loading failed: {e}")
        
        thread = threading.Thread(target=background_loader, daemon=True)
        thread.start()
        logger.info("Background model loading thread started")
    
    def _ensure_model_loaded(self) -> bool:
        """Ensure model is loaded (lazy loading)"""
        if self.model_loaded:
            return True
        
        # Check if loading in progress
        with self._load_lock:
            if self._loading:
                # Wait for loading to complete (with timeout)
                timeout = 30  # 30 seconds timeout
                start_time = time.time()
                while self._loading and (time.time() - start_time) < timeout:
                    time.sleep(0.1)
                return self.model_loaded
            
            # Start loading if not already loading
            if not self.model_loaded:
                self._loading = True
                try:
                    return self.load_model()
                finally:
                    self._loading = False
        
        return self.model_loaded
    
    def load_model(self) -> bool:
        """Load collaborative filtering model (thread-safe)"""
        # Thread-safe check
        with self._load_lock:
            if self.model_loaded:
                logger.debug("Model already loaded, skipping")
                return True
            
            if self._loading:
                logger.warning("Model loading already in progress")
                return False
            
            self._loading = True
            self._load_error = None
        
        try:
            start_time = time.time()
            logger.info(f"Loading CF model from: {self.model_path}")
            
            if not os.path.exists(self.model_path):
                logger.warning(f"Model file not found: {self.model_path}")
                self._load_error = "Model file not found"
                return False
            
            # Get file size for logging
            file_size = os.path.getsize(self.model_path)
            file_size_mb = file_size / (1024 * 1024)
            logger.info(f"Model file size: {file_size_mb:.2f} MB")
            
            # Load model with optimized pickle protocol
            logger.info("Reading model file...")
            with open(self.model_path, 'rb') as f:
                model_data = pickle.load(f)
            
            load_time = time.time() - start_time
            logger.info(f"Model file read in {load_time:.2f} seconds")
            
            # Extract model data
            logger.info("Extracting model data...")
            self.user_factors = model_data['user_factors']
            self.item_factors = model_data['item_factors']
            self.user_similarity_matrix = model_data.get('user_similarity_matrix', None)
            self.item_similarity_matrix = model_data.get('item_similarity_matrix', None)
            self.user_mapping = model_data['user_mapping']
            self.item_mapping = model_data['item_mapping']
            self.reverse_user_mapping = model_data['reverse_user_mapping']
            self.reverse_item_mapping = model_data['reverse_item_mapping']
            self.user_item_matrix = model_data.get('user_item_matrix', None)
            
            if 'interaction_weights' in model_data:
                self.interaction_weights = model_data['interaction_weights']
            
            total_time = time.time() - start_time
            logger.info(f"Collaborative filtering model loaded successfully in {total_time:.2f} seconds")
            logger.info(f"Model stats: {len(self.user_mapping)} users, {len(self.item_mapping)} items")
            
            self.model_loaded = True
            self._load_error = None
            return True
            
        except Exception as e:
            error_msg = f"Error loading collaborative model: {e}"
            logger.error(error_msg, exc_info=True)
            self._load_error = str(e)
            self.model_loaded = False
            return False
        finally:
            with self._load_lock:
                self._loading = False
    
    def reload_model(self) -> bool:
        """Reload model from disk (useful when model file is updated)"""
        logger.info("Reloading CF model...")
        with self._load_lock:
            # Reset state
            self.model_loaded = False
            self.user_factors = None
            self.item_factors = None
            self.user_similarity_matrix = None
            self.item_similarity_matrix = None
            self.user_mapping = {}
            self.item_mapping = {}
            self.reverse_user_mapping = {}
            self.reverse_item_mapping = {}
            self.user_item_matrix = None
            self._load_error = None
        
        # Load model
        return self.load_model()
    
    def calculate_time_decay(self, timestamp, half_life_days=30):
        """
        Calculate time decay weight based on timestamp
        
        Args:
            timestamp: datetime object of the interaction
            half_life_days: Number of days for weight to reduce by 50%
        
        Returns:
            float: Decay weight from 0.0 to 1.0
        """
        try:
            if timestamp is None:
                return 1.0
            
            # Calculate days ago
            current_time = datetime.now()
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            
            days_ago = (current_time - timestamp).days
            
            # If negative (future), return 1.0
            if days_ago < 0:
                return 1.0
            
            # Exponential decay: weight = e^(-ln(2) * days_ago / half_life)
            decay_factor = 0.693 / half_life_days
            weight = math.exp(-decay_factor * days_ago)
            
            # Minimum weight of 0.1
            return max(weight, 0.1)
            
        except Exception as e:
            logger.warning(f"Error calculating time decay: {e}")
            return 1.0
    
    def save_model(self):
        """Save CF model"""
        try:
            logger.info(f"Saving CF model to {self.model_path}...")
            
            # Ensure directory exists
            model_dir = os.path.dirname(self.model_path)
            if not os.path.exists(model_dir):
                os.makedirs(model_dir, exist_ok=True)
            
            # Save model data
            model_data = {
                'user_factors': self.user_factors,
                'item_factors': self.item_factors,
                'user_similarity_matrix': self.user_similarity_matrix,
                'item_similarity_matrix': self.item_similarity_matrix,
                'user_mapping': self.user_mapping,
                'item_mapping': self.item_mapping,
                'reverse_user_mapping': self.reverse_user_mapping,
                'reverse_item_mapping': self.reverse_item_mapping,
                'user_item_matrix': self.user_item_matrix,
                'interaction_weights': self.interaction_weights
            }
            
            with open(self.model_path, 'wb') as f:
                pickle.dump(model_data, f)
            
            # Mark as loaded
            self.model_loaded = True
            
            logger.info("CF model saved successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error saving CF model: {e}")
            return False
    
    def get_model_info(self):
        """Get model information"""
        # Try to load model if not loaded
        if not self.model_loaded:
            if self._loading:
                return {
                    "status": "Loading",
                    "message": "Model is currently being loaded in background"
                }
            elif self._load_error:
                return {
                    "status": "Error",
                    "message": f"Model loading failed: {self._load_error}"
                }
            else:
                return {
                    "status": "Not loaded",
                    "message": "Model not loaded (lazy loading enabled)"
                }
        
        try:
            info = {
                "status": "Loaded",
                "model_type": "Enhanced Collaborative Filtering with Time Decay",
                "interaction_weights": self.interaction_weights,
                "model_path": self.model_path,
                "time_decay_enabled": True,
                "time_decay_half_life": 30
            }
            
            if hasattr(self, 'user_factors') and hasattr(self, 'item_factors'):
                info.update({
                    "n_users": self.user_factors.shape[0],
                    "n_items": self.item_factors.shape[0],
                    "n_factors": self.user_factors.shape[1]
                })
            
            return info
            
        except Exception as e:
            return {"status": "Error", "message": str(e)}
    
    def get_user_recommendations(self, user_id: int, limit: int = 10) -> List[Dict]:
        """
        Override parent method to add time decay
        
        Lấy danh sách phim được recommend cho user với time decay
        """
        # Lazy load model if needed
        if not self.model_loaded:
            if not self._ensure_model_loaded():
                logger.error(f"Model not loaded and failed to load: {self._load_error}")
                return []
        
        # Kiểm tra model data có sẵn không
        if self.user_factors is None or self.item_factors is None:
            logger.error("Model factors not loaded (user_factors or item_factors is None)")
            return []
        
        try:
            # Get recommendations from model (same as parent)
            recommendations = self._get_user_recommendations_internal(user_id, limit * 2)
            
            if not recommendations:
                logger.warning(f"No recommendations found for user {user_id}")
                return []
            
            # Apply time decay to scores
            user_interaction_timestamps = self._get_user_interaction_timestamps(user_id)
            
            # Adjust scores based on recency
            adjusted_recommendations = []
            for movie_id, score in recommendations:
                # Get time weight
                time_weight = 1.0
                if user_interaction_timestamps and movie_id in user_interaction_timestamps:
                    timestamp = user_interaction_timestamps[movie_id]
                    if timestamp is not None:
                        try:
                            time_weight = self.calculate_time_decay(timestamp, half_life_days=30)
                        except Exception as e:
                            logger.warning(f"Error calculating time decay for movie {movie_id}: {e}")
                            time_weight = 1.0
                
                # Boost score for recent interactions
                # More recent = higher boost
                boosted_score = score * (1 + time_weight * 0.3)  # Boost 0-30%
                adjusted_recommendations.append((movie_id, boosted_score))
            
            # Sort by adjusted score
            adjusted_recommendations.sort(key=lambda x: x[1], reverse=True)
            
            # Get movie details
            movie_ids = [rec[0] for rec in adjusted_recommendations[:limit]]
            movie_details = self._get_movie_details(movie_ids)
            
            # Combine recommendations with movie details
            result = []
            for movie_id, score in adjusted_recommendations[:limit]:
                if movie_id in movie_details:
                    movie_info = movie_details[movie_id]
                    movie_info['recommendation_score'] = round(score, 4)
                    result.append(movie_info)
            
            logger.info(f"Generated {len(result)} time-decay-adjusted recommendations for user {user_id}")
            return result
            
        except Exception as e:
            logger.error(f"Error getting user recommendations with time decay: {e}")
            return []
    
    def _get_user_interaction_timestamps(self, user_id: int) -> Dict[int, datetime]:
        """
        Get timestamps of user's latest interaction with each movie
        
        Returns:
            Dict[int, datetime]: Movie ID -> Latest interaction timestamp
        """
        timestamps = {}
        
        try:
            with self.db_engine.connect() as conn:
                # View History
                result = conn.execute(text("""
                    SELECT movieId, MAX(startedAt) as latest_time
                    FROM cine.ViewHistory
                    WHERE userId = :user_id
                    GROUP BY movieId
                """), {"user_id": user_id})
                
                for row in result:
                    if row[0] is not None and row[1] is not None:
                        timestamps[row[0]] = row[1]
                
                # Rating
                result = conn.execute(text("""
                    SELECT movieId, MAX(ratedAt) as latest_time
                    FROM cine.Rating
                    WHERE userId = :user_id
                    GROUP BY movieId
                """), {"user_id": user_id})
                
                for row in result:
                    if row[0] is not None and row[1] is not None:
                        if row[0] not in timestamps or row[1] > timestamps.get(row[0], datetime.min):
                            timestamps[row[0]] = row[1]
                
                # Favorite, Watchlist - dùng addedAt
                for table in ['Favorite', 'Watchlist']:
                    result = conn.execute(text(f"""
                        SELECT movieId, MAX(addedAt) as latest_time
                        FROM cine.{table}
                        WHERE userId = :user_id
                        GROUP BY movieId
                    """), {"user_id": user_id})
                    
                    for row in result:
                        if row[0] not in timestamps or row[1] > timestamps.get(row[0], datetime.min):
                            timestamps[row[0]] = row[1]
                
                # Comment - dùng createdAt
                result = conn.execute(text("""
                    SELECT movieId, MAX(createdAt) as latest_time
                    FROM cine.Comment
                    WHERE userId = :user_id
                    GROUP BY movieId
                """), {"user_id": user_id})
                
                for row in result:
                    if row[0] is not None and row[1] is not None:
                        if row[0] not in timestamps or row[1] > timestamps.get(row[0], datetime.min):
                            timestamps[row[0]] = row[1]
            
            return timestamps
            
        except Exception as e:
            logger.warning(f"Error getting user interaction timestamps: {e}")
            return {}
    
    def _get_user_recommendations_internal(self, user_id: int, n_recommendations: int) -> List[Tuple[int, float]]:
        """Internal method to get user recommendations"""
        # Kiểm tra model data có sẵn không
        if self.user_factors is None or self.item_factors is None:
            logger.error("Model factors not loaded (user_factors or item_factors is None)")
            return []
        
        # Đảm bảo user_id là int để khớp với mapping
        user_id = int(user_id) if user_id is not None else None
        if user_id is None:
            logger.error("Invalid user_id: None")
            return []
        
        if user_id not in self.user_mapping:
            logger.warning(f"User {user_id} not found in model (available users: {len(self.user_mapping)})")
            return []
        
        user_idx = self.user_mapping[user_id]
        
        # Kiểm tra user_idx có hợp lệ không
        if user_idx >= len(self.user_factors):
            logger.error(f"User index {user_idx} out of range for user_factors (length: {len(self.user_factors)})")
            return []
        
        # Get user's rated items from matrix (nếu có)
        rated_items_from_matrix = set()
        if self.user_item_matrix is not None:
            if isinstance(self.user_item_matrix, pd.DataFrame):
                # DataFrame case - get non-zero ratings
                if user_idx < len(self.user_item_matrix):
                    user_ratings = self.user_item_matrix.iloc[user_idx]
                    rated_items_from_matrix = set(user_ratings[user_ratings > 0].index)
            elif hasattr(self.user_item_matrix, '__getitem__'):
                try:
                    # Sparse matrix case
                    if hasattr(self.user_item_matrix[user_idx], 'indices'):
                        rated_items_from_matrix = set(self.user_item_matrix[user_idx].indices)
                except (IndexError, KeyError):
                    pass
        
        # Luôn lấy tất cả phim đã rated và viewed từ database để đảm bảo đầy đủ
        # (Matrix có thể không có tất cả interactions)
        try:
            with self.db_engine.connect() as conn:
                # Lấy tất cả phim đã rated - đảm bảo convert về int
                result = conn.execute(text("""
                    SELECT movieId FROM cine.Rating WHERE userId = :user_id
                """), {"user_id": int(user_id)})
                rated_items_from_db = {int(row[0]) for row in result.fetchall() if row[0] is not None}
                
                # Lấy tất cả phim đã xem hoàn thành (>= 70% hoặc finished)
                # Loại bỏ phim đang xem dở (< 70% và chưa finished) để có thể recommend tiếp tục xem
                result = conn.execute(text("""
                    SELECT DISTINCT vh.movieId 
                    FROM cine.ViewHistory vh
                    INNER JOIN cine.Movie m ON vh.movieId = m.movieId
                    WHERE vh.userId = :user_id 
                    AND (
                        vh.finishedAt IS NOT NULL 
                        OR (m.durationMin > 0 AND CAST(vh.progressSec AS FLOAT) / 60.0 / m.durationMin >= 0.7)
                    )
                """), {"user_id": int(user_id)})
                viewed_items = {int(row[0]) for row in result.fetchall() if row[0] is not None}
                
                # Đảm bảo rated_items_from_matrix cũng là int set
                rated_items_from_matrix = {int(x) for x in rated_items_from_matrix if x is not None}
                
                # Kết hợp tất cả: từ matrix, từ DB (rated), và viewed items
                rated_items = rated_items_from_matrix.union(rated_items_from_db).union(viewed_items)
        except Exception as e:
            logger.warning(f"Could not get rated/viewed items from database: {e}")
            # Fallback: chỉ dùng items từ matrix nếu có
            rated_items = rated_items_from_matrix
        
        # Calculate scores for all items
        try:
            user_vector = self.user_factors[user_idx]
            scores = user_vector @ self.item_factors.T
        except Exception as e:
            logger.error(f"Error calculating scores: {e}", exc_info=True)
            return []
        
        # Filter out already rated items and get top recommendations
        recommendations = []
        try:
            for item_idx, score in enumerate(scores):
                if item_idx not in self.reverse_item_mapping:
                    continue
                item_id = self.reverse_item_mapping[item_idx]
                # Đảm bảo item_id là int để khớp với DB
                item_id = int(item_id) if item_id is not None else None
                if item_id is None:
                    continue
                # Đảm bảo rated_items cũng là int set
                if item_id not in rated_items:
                    recommendations.append((item_id, float(score)))
        except Exception as e:
            logger.error(f"Error processing recommendations: {e}", exc_info=True)
            return []
        
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
        # Lazy load model if needed
        if not self.model_loaded:
            if not self._ensure_model_loaded():
                logger.error(f"Model not loaded and failed to load: {self._load_error}")
                return []
        
        try:
            if user_id not in self.user_mapping:
                logger.warning(f"User {user_id} not found in model")
                return []
            
            user_idx = self.user_mapping[user_id]
            
            # Check if similarity matrix exists
            if self.user_similarity_matrix is not None:
                similarities = self.user_similarity_matrix[user_idx]
                
                # Get similar users (excluding self)
                similar_users = []
                for other_user_idx, similarity in enumerate(similarities):
                    if other_user_idx != user_idx:
                        other_user_id = self.reverse_user_mapping[other_user_idx]
                        similar_users.append((other_user_id, float(similarity)))
                
                # Sort by similarity and get top N
                similar_users.sort(key=lambda x: x[1], reverse=True)
            else:
                # Fallback: use model predictions directly
                logger.warning("User similarity matrix not available, using model predictions")
                similar_users = []
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
        # Lazy load model if needed
        if not self.model_loaded:
            if not self._ensure_model_loaded():
                logger.error(f"Model not loaded and failed to load: {self._load_error}")
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
                        COUNT(DISTINCT w.userId) as watchlistCount,
                        COUNT(DISTINCT vh.userId) as viewHistoryCount,
                        COUNT(DISTINCT f.userId) as favoriteCount,
                        COUNT(DISTINCT c.userId) as commentCount,
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
                    LEFT JOIN cine.Watchlist w ON m.movieId = w.movieId
                    LEFT JOIN cine.ViewHistory vh ON m.movieId = vh.movieId
                    LEFT JOIN cine.Favorite f ON m.movieId = f.movieId
                    LEFT JOIN cine.Comment c ON m.movieId = c.movieId
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
                        'watchlistCount': row.watchlistCount,
                        'viewHistoryCount': row.viewHistoryCount,
                        'favoriteCount': row.favoriteCount,
                        'commentCount': row.commentCount,
                        'genres': row.genres or ''
                    }
                    
                    # Debug logging
                    logger.info(f"Movie {row.movieId} ({row.title}): "
                              f"Ratings={row.ratingCount}, "
                              f"Watchlist={row.watchlistCount}, "
                              f"Views={row.viewHistoryCount}, "
                              f"Favorites={row.favoriteCount}, "
                              f"Comments={row.commentCount}")
                
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
    
    def get_loading_status(self) -> Dict:
        """Get detailed loading status"""
        return {
            "loaded": self.model_loaded,
            "loading": self._loading,
            "error": self._load_error,
            "model_path": self.model_path,
            "model_exists": os.path.exists(self.model_path) if self.model_path else False
        }

