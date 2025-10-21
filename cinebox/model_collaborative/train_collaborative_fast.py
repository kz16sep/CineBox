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
        """Load data from database with optional sampling"""
        try:
            logger.info("Loading data from database...")
            
            with self.db_engine.connect() as conn:
                # Get ratings with optional sampling
                if sample_size:
                    query = f"""
                        SELECT TOP {sample_size} r.userId, r.movieId, r.value
                    FROM cine.Rating r
                    INNER JOIN cine.[User] u ON r.userId = u.userId
                    WHERE u.status = 'active'
                    ORDER BY NEWID()
                    """
                else:
                    query = """
                        SELECT r.userId, r.movieId, r.value
                        FROM cine.Rating r
                        INNER JOIN cine.[User] u ON r.userId = u.userId
                        WHERE u.status = 'active'
                    """
                
                ratings_df = pd.read_sql(query, conn)
                logger.info(f"Loaded {len(ratings_df)} ratings")
                
                return ratings_df
                
        except Exception as e:
            logger.error(f"Error loading data: {e}")
            return None
    
    def preprocess_data(self, ratings_df, min_interactions=5):
        """Preprocess data for fast training"""
        try:
            logger.info("Preprocessing data...")
            
            # Filter users and items with minimum interactions
            user_counts = ratings_df['userId'].value_counts()
            movie_counts = ratings_df['movieId'].value_counts()
            
            valid_users = user_counts[user_counts >= min_interactions].index
            valid_movies = movie_counts[movie_counts >= min_interactions].index
            
            # Filter data
            filtered_df = ratings_df[
                (ratings_df['userId'].isin(valid_users)) & 
                (ratings_df['movieId'].isin(valid_movies))
            ].copy()
            
            logger.info(f"Filtered to {len(filtered_df)} ratings from {len(valid_users)} users and {len(valid_movies)} movies")
            
            # Convert to implicit feedback (binary)
            filtered_df['interaction'] = 1
            
            return filtered_df, valid_users, valid_movies
            
        except Exception as e:
            logger.error(f"Error preprocessing data: {e}")
            return None, None, None
    
    def train_als_model(self, ratings_df, n_factors=20, iterations=5, regularization=0.01):
        """Train ALS model with reduced parameters"""
        try:
            logger.info("Training ALS model...")
            
            # Create user-item matrix
            user_item_matrix = ratings_df.pivot_table(
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
    print("Fast Training Configuration:")
    print("- Sample size: 500,000 ratings")
    print("- Latent factors: 20")
    print("- Iterations: 5")
    print("- Min interactions: 10")
    print()
    
    # Train model
    success = trainer.train_full_model_fast(
        sample_size=500000,
        n_factors=20,
        iterations=5,
        min_interactions=10
    )
        
    if success:
        print("\nFast training completed successfully!")
        print("Model saved to: model_collaborative/collaborative_model.pkl")
        print("You can now use the collaborative filtering recommendations in the web app.")
    else:
        print("\nFast training failed!")
    
    return success

if __name__ == "__main__":
    main()
