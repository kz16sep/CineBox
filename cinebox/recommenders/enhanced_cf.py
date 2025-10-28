#!/usr/bin/env python3
"""
Enhanced Collaborative Filtering Recommender
Sử dụng tất cả dữ liệu tương tác với trọng số
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
from typing import List, Dict

# Import from same directory
from .collaborative import CollaborativeRecommender

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class EnhancedCFRecommender(CollaborativeRecommender):
    """
    Enhanced Collaborative Filtering Recommender
    Sử dụng tất cả dữ liệu tương tác với trọng số
    """
    
    def __init__(self, db_engine):
        super().__init__(db_engine)
        # Use absolute path for model - 2 levels up (recommenders -> cinebox)
        base_dir = os.path.dirname(os.path.dirname(__file__))
        self.model_path = os.path.join(base_dir, 'model_collaborative', 'enhanced_cf_model.pkl')
        self.interaction_weights = {
            'view_history': 0.35,
            'rating': 0.25,
            'favorite': 0.10,
            'watchlist': 0.08,
            'comment': 0.07,
            'cold_start': 0.05
        }
        
        # Load enhanced model if exists
        self.load_enhanced_model()
    
    def load_enhanced_model(self):
        """Load enhanced CF model"""
        try:
            if not os.path.exists(self.model_path):
                logger.warning(f"Enhanced CF model not found: {self.model_path}")
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
            
            if 'interaction_weights' in model_data:
                self.interaction_weights = model_data['interaction_weights']
            
            self.model_loaded = True
            logger.info("Enhanced CF model loaded successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error loading enhanced CF model: {e}")
            return False
    
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
        """Save enhanced CF model"""
        try:
            logger.info(f"Saving enhanced CF model to {self.model_path}...")
            
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
                'interaction_weights': self.interaction_weights
            }
            
            with open(self.model_path, 'wb') as f:
                pickle.dump(model_data, f)
            
            # Mark as loaded
            self.model_loaded = True
            
            logger.info("Enhanced CF model saved successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error saving enhanced CF model: {e}")
            return False
    
    def get_model_info(self):
        """Get model information"""
        if not self.is_model_loaded():
            return {"status": "Not loaded", "message": "Model not loaded"}
        
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
        if not self.model_loaded:
            logger.error("Model not loaded")
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
                if movie_id in user_interaction_timestamps:
                    timestamp = user_interaction_timestamps[movie_id]
                    time_weight = self.calculate_time_decay(timestamp, half_life_days=30)
                
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
            # Fallback to parent method
            return super().get_user_recommendations(user_id, limit)
    
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
                    timestamps[row[0]] = row[1]
                
                # Rating
                result = conn.execute(text("""
                    SELECT movieId, MAX(ratedAt) as latest_time
                    FROM cine.Rating
                    WHERE userId = :user_id
                    GROUP BY movieId
                """), {"user_id": user_id})
                
                for row in result:
                    if row[0] not in timestamps or row[1] > timestamps.get(row[0], datetime.min):
                        timestamps[row[0]] = row[1]
                
                # Favorite, Watchlist, Comment
                for table in ['Favorite', 'Watchlist', 'Comment']:
                    result = conn.execute(text(f"""
                        SELECT movieId, MAX(createdAt) as latest_time
                        FROM cine.{table}
                        WHERE userId = :user_id
                        GROUP BY movieId
                    """), {"user_id": user_id})
                    
                    for row in result:
                        if row[0] not in timestamps or row[1] > timestamps.get(row[0], datetime.min):
                            timestamps[row[0]] = row[1]
            
            return timestamps
            
        except Exception as e:
            logger.warning(f"Error getting user interaction timestamps: {e}")
            return {}

