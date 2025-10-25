#!/usr/bin/env python3
"""
Enhanced Collaborative Filtering Recommender
Sử dụng tất cả dữ liệu tương tác với trọng số
"""

import os
import sys
import pandas as pd
import numpy as np
from sqlalchemy import text
import logging
from datetime import datetime
import pickle
from tqdm import tqdm

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collaborative_recommender import CollaborativeRecommender

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class EnhancedCFRecommender(CollaborativeRecommender):
    """
    Enhanced Collaborative Filtering Recommender
    Sử dụng tất cả dữ liệu tương tác với trọng số
    """
    
    def __init__(self, db_engine):
        super().__init__(db_engine)
        self.model_path = 'model_collaborative/enhanced_cf_model.pkl'
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
    
    def load_data_from_database(self, sample_size=None):
        """Load tất cả dữ liệu từ database với trọng số"""
        try:
            logger.info("Loading all interaction data from database...")
            
            with self.db_engine.connect() as conn:
                all_interactions = []
                
                # 1. Lịch sử xem phim (ViewHistory) - 35%
                view_history_query = text("""
                    SELECT vh.userId, vh.movieId, 
                           CASE 
                               WHEN vh.finishedAt IS NOT NULL THEN 0.35  -- Hoàn thành
                               WHEN vh.progressSec > 0 THEN 0.245  -- Đang xem
                               ELSE 0.105  -- Chỉ bắt đầu
                           END as weight
                    FROM cine.ViewHistory vh
                """)
                view_history_df = pd.read_sql(view_history_query, conn)
                if not view_history_df.empty:
                    view_history_df['interaction_type'] = 'view_history'
                    all_interactions.append(view_history_df[['userId', 'movieId', 'weight', 'interaction_type']])
                
                # 2. Đánh giá phim (Rating) - 25%
                ratings_query = text("""
                    SELECT r.userId, r.movieId, 
                           (0.25 * CAST(r.value AS FLOAT) / 5.0) as weight
                    FROM cine.Rating r
                    INNER JOIN cine.[User] u ON r.userId = u.userId
                    WHERE u.status = 'active' AND r.value IS NOT NULL
                """)
                ratings_df = pd.read_sql(ratings_query, conn)
                if not ratings_df.empty:
                    ratings_df['interaction_type'] = 'rating'
                    all_interactions.append(ratings_df[['userId', 'movieId', 'weight', 'interaction_type']])
                
                # 3. Danh sách yêu thích (Favorite) - 10%
                favorites_query = text("""
                    SELECT f.userId, f.movieId, 0.10 as weight
                    FROM cine.Favorite f
                """)
                favorites_df = pd.read_sql(favorites_query, conn)
                if not favorites_df.empty:
                    favorites_df['interaction_type'] = 'favorite'
                    all_interactions.append(favorites_df[['userId', 'movieId', 'weight', 'interaction_type']])
                
                # 4. Danh sách xem sau (Watchlist) - 8%
                watchlist_query = text("""
                    SELECT w.userId, w.movieId, 0.08 as weight
                    FROM cine.Watchlist w
                """)
                watchlist_df = pd.read_sql(watchlist_query, conn)
                if not watchlist_df.empty:
                    watchlist_df['interaction_type'] = 'watchlist'
                    all_interactions.append(watchlist_df[['userId', 'movieId', 'weight', 'interaction_type']])
                
                # 5. Bình luận (Comment) - 7%
                comments_query = text("""
                    SELECT c.userId, c.movieId, 0.07 as weight
                    FROM cine.Comment c
                """)
                comments_df = pd.read_sql(comments_query, conn)
                if not comments_df.empty:
                    comments_df['interaction_type'] = 'comment'
                    all_interactions.append(comments_df[['userId', 'movieId', 'weight', 'interaction_type']])
                
                # 6. Cold Start (Onboarding) - 5%
                cold_start_query = text("""
                    SELECT csr.userId, csr.movieId, 
                           CASE 
                               WHEN csr.score > 0.8 THEN 0.05
                               WHEN csr.score > 0.6 THEN 0.04
                               ELSE 0.03
                           END as weight
                    FROM cine.ColdStartRecommendations csr
                    WHERE csr.expiresAt > GETDATE()
                """)
                cold_start_df = pd.read_sql(cold_start_query, conn)
                if not cold_start_df.empty:
                    cold_start_df['interaction_type'] = 'cold_start'
                    all_interactions.append(cold_start_df[['userId', 'movieId', 'weight', 'interaction_type']])
                
                if not all_interactions:
                    logger.warning("No interaction data found")
                    return None
                
                # Combine all data
                combined_df = pd.concat(all_interactions, ignore_index=True)
                
                # Aggregate weights by user-item pair (take maximum weight)
                aggregated_df = combined_df.groupby(['userId', 'movieId']).agg({
                    'weight': 'max',
                    'interaction_type': lambda x: ','.join(x.unique())
                }).reset_index()
                
                # Apply sampling if specified
                if sample_size and len(aggregated_df) > sample_size:
                    aggregated_df = aggregated_df.sample(n=sample_size, random_state=42)
                
                logger.info(f"Loaded {len(aggregated_df)} user-item interactions")
                logger.info(f"Interaction types: {aggregated_df['interaction_type'].value_counts().to_dict()}")
                
                return aggregated_df
                
        except Exception as e:
            logger.error(f"Error loading data: {e}")
            return None
    
    def preprocess_data(self, interactions_df, min_interactions=5):
        """Preprocess data for training"""
        try:
            logger.info("Preprocessing data...")
            
            # Filter users and items with minimum interactions
            user_counts = interactions_df['userId'].value_counts()
            movie_counts = interactions_df['movieId'].value_counts()
            
            valid_users = user_counts[user_counts >= min_interactions].index
            valid_movies = movie_counts[movie_counts >= min_interactions].index
            
            # Filter data
            filtered_df = interactions_df[
                (interactions_df['userId'].isin(valid_users)) & 
                (interactions_df['movieId'].isin(valid_movies))
            ].copy()
            
            logger.info(f"Filtered to {len(filtered_df)} interactions from {len(valid_users)} users and {len(valid_movies)} movies")
            
            # Convert to implicit feedback (keep weights)
            filtered_df['interaction'] = filtered_df['weight']
            
            return filtered_df, valid_users, valid_movies
            
        except Exception as e:
            logger.error(f"Error preprocessing data: {e}")
            return None, None, None
    
    def train_model(self, sample_size=100000, n_factors=20, iterations=5, min_interactions=5):
        """Train enhanced CF model"""
        try:
            logger.info("Starting enhanced collaborative filtering training...")
            
            # Load data
            interactions_df = self.load_data_from_database(sample_size)
            if interactions_df is None:
                return False
            
            # Preprocess data
            filtered_df, valid_users, valid_movies = self.preprocess_data(interactions_df, min_interactions)
            if filtered_df is None:
                return False
            
            # Train ALS model
            success = self.train_als_model(filtered_df, n_factors, iterations)
            if not success:
                return False
            
            # Calculate similarities
            self.calculate_similarities()
            
            # Save model
            success = self.save_model()
            
            if success:
                logger.info("Enhanced CF model trained successfully!")
                return True
            else:
                logger.error("Failed to save enhanced CF model")
                return False
                
        except Exception as e:
            logger.error(f"Error training enhanced CF model: {e}")
            return False
    
    def train_als_model(self, interactions_df, n_factors=20, iterations=5, regularization=0.01):
        """Train ALS model with enhanced data"""
        try:
            logger.info("Training ALS model...")
            
            # Create user-item matrix with weights
            user_item_matrix = interactions_df.pivot_table(
                index='userId', 
                columns='movieId', 
                values='interaction', 
                fill_value=0
            ).astype(np.float32)
            
            # Convert to sparse matrix
            from scipy.sparse import csr_matrix
            matrix = csr_matrix(user_item_matrix.values)
            
            # Train ALS model
            from implicit.als import AlternatingLeastSquares
            
            model = AlternatingLeastSquares(
                factors=n_factors,
                iterations=iterations,
                regularization=regularization,
                random_state=42
            )
            
            logger.info("Fitting ALS model...")
            model.fit(matrix)
            
            # Get factors
            self.user_factors = model.user_factors
            self.item_factors = model.item_factors
            
            # Create mappings
            self.user_mapping = {user_id: idx for idx, user_id in enumerate(user_item_matrix.index)}
            self.item_mapping = {movie_id: idx for idx, movie_id in enumerate(user_item_matrix.columns)}
            
            self.reverse_user_mapping = {idx: user_id for user_id, idx in self.user_mapping.items()}
            self.reverse_item_mapping = {idx: movie_id for movie_id, idx in self.item_mapping.items()}
            
            logger.info(f"Model trained with {self.user_factors.shape[0]} users and {self.item_factors.shape[0]} items")
            
            return True
            
        except Exception as e:
            logger.error(f"Error training ALS model: {e}")
            return False
    
    def calculate_similarities(self):
        """Calculate similarity matrices"""
        try:
            logger.info("Calculating similarity matrices...")
            
            from sklearn.metrics.pairwise import cosine_similarity
            
            # User similarity
            self.user_similarity_matrix = cosine_similarity(self.user_factors)
            
            # Item similarity
            self.item_similarity_matrix = cosine_similarity(self.item_factors)
            
            logger.info("Similarity matrices calculated")
            return True
            
        except Exception as e:
            logger.error(f"Error calculating similarities: {e}")
            return False
    
    def save_model(self):
        """Save enhanced CF model"""
        try:
            logger.info(f"Saving enhanced CF model to {self.model_path}...")
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
            
            # Save model data
            import pickle
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
                "model_type": "Enhanced Collaborative Filtering",
                "interaction_weights": self.interaction_weights,
                "model_path": self.model_path
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
