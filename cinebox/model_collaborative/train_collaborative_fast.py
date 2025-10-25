#!/usr/bin/env python3
"""
Fast Collaborative Filtering Training Script
Optimized version for quick training with reduced parameters
"""

import os
import sys
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
import logging
from datetime import datetime
import pickle
from tqdm import tqdm

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collaborative_recommender import CollaborativeRecommender

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class FastCollaborativeTrainer:
    """
    Fast Collaborative Filtering Trainer
    Optimized for quick training with reduced parameters
    """
    
    def __init__(self, db_engine):
        self.db_engine = db_engine
        self.recommender = CollaborativeRecommender(db_engine)
    
    def load_data_from_database(self, sample_size=None):
        """Load tất cả dữ liệu từ database với trọng số"""
        try:
            logger.info("Loading all interaction data from database...")
            
            with self.db_engine.connect() as conn:
                all_interactions = []
                
                # 1. View Count - 35% (sử dụng viewCount thay vì ViewHistory)
                view_count_query = text("""
                    SELECT m.movieId, 
                           CASE 
                               WHEN m.viewCount >= 1000 THEN 0.35  -- Phim phổ biến
                               WHEN m.viewCount >= 100 THEN 0.245  -- Phim trung bình
                               WHEN m.viewCount >= 10 THEN 0.105  -- Phim ít xem
                               ELSE 0.05  -- Phim mới
                           END as weight
                    FROM cine.Movie m
                    WHERE m.viewCount > 0
                """)
                view_count_df = pd.read_sql(view_count_query, conn)
                if not view_count_df.empty:
                    # Tạo interactions cho tất cả users với viewCount
                    users_query = text("SELECT DISTINCT userId FROM cine.[User] WHERE status = 'active'")
                    users_df = pd.read_sql(users_query, conn)
                    
                    if not users_df.empty:
                        # Tạo cartesian product giữa users và movies có viewCount
                        view_count_interactions = []
                        for _, user in users_df.iterrows():
                            for _, movie in view_count_df.iterrows():
                                view_count_interactions.append({
                                    'userId': user['userId'],
                                    'movieId': movie['movieId'],
                                    'weight': movie['weight'],
                                    'interaction_type': 'view_count'
                                })
                        
                        if view_count_interactions:
                            view_count_df = pd.DataFrame(view_count_interactions)
                            all_interactions.append(view_count_df[['userId', 'movieId', 'weight', 'interaction_type']])
                
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
        """Preprocess data for fast training"""
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
    
    def train_als_model(self, interactions_df, n_factors=20, iterations=5, regularization=0.01):
        """Train ALS model with reduced parameters"""
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
            user_factors = model.user_factors
            item_factors = model.item_factors
            
            # Create mappings
            user_mapping = {user_id: idx for idx, user_id in enumerate(user_item_matrix.index)}
            item_mapping = {movie_id: idx for idx, movie_id in enumerate(user_item_matrix.columns)}
            
            reverse_user_mapping = {idx: user_id for user_id, idx in user_mapping.items()}
            reverse_item_mapping = {idx: movie_id for movie_id, idx in item_mapping.items()}
            
            logger.info(f"Model trained with {user_factors.shape[0]} users and {item_factors.shape[0]} items")
            
            return {
                'user_factors': user_factors,
                'item_factors': item_factors,
                'user_mapping': user_mapping,
                'item_mapping': item_mapping,
                'reverse_user_mapping': reverse_user_mapping,
                'reverse_item_mapping': reverse_item_mapping,
                'user_item_matrix': user_item_matrix,
                'model': model
            }
            
        except Exception as e:
            logger.error(f"Error training model: {e}")
            return None
    
    def calculate_similarities(self, user_factors, item_factors):
        """Calculate similarity matrices"""
        try:
            logger.info("Calculating similarity matrices...")
            
            from sklearn.metrics.pairwise import cosine_similarity
            
            # User similarity
            user_similarity = cosine_similarity(user_factors)
            
            # Item similarity
            item_similarity = cosine_similarity(item_factors)
            
            logger.info("Similarity matrices calculated")
            
            return user_similarity, item_similarity
            
        except Exception as e:
            logger.error(f"Error calculating similarities: {e}")
            return None, None
    
    def save_model(self, model_data, model_path='model_collaborative/collaborative_model.pkl'):
        """Save trained model"""
        try:
            logger.info(f"Saving model to {model_path}...")
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(model_path), exist_ok=True)
            
            # Save model data
            with open(model_path, 'wb') as f:
                pickle.dump(model_data, f)
            
            logger.info("Model saved successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error saving model: {e}")
            return False
    
    def train_full_model_fast(self, sample_size=100000, n_factors=20, iterations=5, min_interactions=5):
        """Train full model with fast configuration"""
        try:
            logger.info("Starting fast collaborative filtering training...")
            logger.info(f"Configuration: sample_size={sample_size}, n_factors={n_factors}, iterations={iterations}")
            
            # Load data
            ratings_df = self.load_data_from_database(sample_size)
            if ratings_df is None:
                return False
            
            # Preprocess data
            filtered_df, valid_users, valid_movies = self.preprocess_data(ratings_df, min_interactions)
            if filtered_df is None:
                return False
            
            # Train model
            model_data = self.train_als_model(filtered_df, n_factors, iterations)
            if model_data is None:
                return False
            
            # Calculate similarities
            user_similarity, item_similarity = self.calculate_similarities(
                model_data['user_factors'], 
                model_data['item_factors']
            )
            
            if user_similarity is not None and item_similarity is not None:
                model_data['user_similarity_matrix'] = user_similarity
                model_data['item_similarity_matrix'] = item_similarity
            
            # Save model
            if self.save_model(model_data):
                logger.info("Fast training completed successfully!")
                return True
            else:
                return False
                
        except Exception as e:
            logger.error(f"Error in fast training: {e}")
            return False

def main():
    """Main function"""
    print("Fast Collaborative Filtering Training")
    print("=" * 50)
    
    # Database connection
    odbc_str = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=localhost,1433;"
        "DATABASE=CineBoxDB;"
        "UID=sa;"
        "PWD=sapassword;"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
    )
    connection_url = URL.create("mssql+pyodbc", query={"odbc_connect": odbc_str})
    db_engine = create_engine(connection_url, fast_executemany=True)
    
    # Initialize trainer
    trainer = FastCollaborativeTrainer(db_engine)
    
    # Fast training configuration
    print("Enhanced Fast Training Configuration:")
    print("- Sample size: 500,000 interactions")
    print("- Latent factors: 20")
    print("- Iterations: 5")
    print("- Min interactions: 1")
    print()
    print("Data Sources & Weights:")
    print("- ViewCount: 35% (popular: 35%, medium: 24.5%, low: 10.5%, new: 5%)")
    print("- Rating: 25% (normalized by rating value)")
    print("- Favorite: 10%")
    print("- Watchlist: 8%")
    print("- Comment: 7%")
    print("- Cold Start: 5% (score-based)")
    print()
    
    # Train model
    success = trainer.train_full_model_fast(
        sample_size=500000,
        n_factors=20,
        iterations=5,
        min_interactions=1  # Reduced from 10 to 1 to work with limited data
    )
        
    if success:
        print("\nFast training completed successfully!")
        print("Model saved to: model_collaborative/collaborative_model.pkl")
        print("You can now use the collaborative filtering recommendations in the web app.")
        
        # Also create enhanced model
        print("\nCreating Enhanced CF model...")
        from enhanced_cf_recommender import EnhancedCFRecommender
        enhanced_cf = EnhancedCFRecommender(db_engine)
        enhanced_success = enhanced_cf.train_model(
            sample_size=200000,
            n_factors=20,
            iterations=5,
            min_interactions=1  # Reduced from 10 to 1 to work with limited data
        )
        
        if enhanced_success:
            print("Enhanced CF model created successfully!")
            print("Enhanced model saved to: model_collaborative/enhanced_cf_model.pkl")
        else:
            print("Enhanced CF model creation failed, but basic model is ready.")
    else:
        print("\nFast training failed!")
    
    return success

if __name__ == "__main__":
    main()
