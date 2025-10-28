#!/usr/bin/env python3
"""
Enhanced Collaborative Filtering Trainer
Kết hợp nhiều yếu tố: ratings, views, favorites, watchlist, comments
"""

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
import logging
from typing import List, Dict, Tuple, Optional
import time
from tqdm import tqdm
from scipy.sparse import csr_matrix
from sklearn.decomposition import TruncatedSVD
from sklearn.metrics.pairwise import cosine_similarity
import pickle
import os

logger = logging.getLogger(__name__)

class EnhancedCollaborativeFilteringTrainer:
    """Enhanced Collaborative Filtering Trainer với multiple signals"""
    
    def __init__(self, db_engine):
        self.db_engine = db_engine
        self.interaction_matrix = None
        self.movies_df = None
        self.users_df = None
        self.user_similarity_matrix = None
        self.item_similarity_matrix = None
        self.user_factors = None
        self.item_factors = None
        self.user_mapping = {}
        self.item_mapping = {}
        self.reverse_user_mapping = {}
        self.reverse_item_mapping = {}
        
    def load_data_from_database(self):
        """Load dữ liệu từ database CineBox với multiple signals"""
        try:
            with self.db_engine.connect() as conn:
                # Load comprehensive interaction data
                interactions_df = pd.read_sql("""
                    WITH UserMovieInteractions AS (
                        -- Ratings (weight: 1.0)
                        SELECT userId, movieId, CAST(value AS FLOAT) as score, 1.0 as weight, 'rating' as signal_type
                        FROM cine.Rating
                        WHERE value IS NOT NULL
                        
                        UNION ALL
                        
                        -- View Count (weight: 0.8) - sử dụng viewCount thay vì ViewHistory
                        SELECT u.userId, m.movieId, 
                               CASE 
                                   WHEN m.viewCount >= 1000 THEN 1.0
                                   WHEN m.viewCount >= 100 THEN 0.7
                                   WHEN m.viewCount >= 10 THEN 0.4
                                   ELSE 0.1
                               END as score, 
                               0.8 as weight, 
                               'view_count' as signal_type
                        FROM cine.[User] u
                        CROSS JOIN cine.Movie m
                        WHERE u.status = 'active' AND m.viewCount > 0
                        
                        UNION ALL
                        
                        -- Favorites (weight: 0.9)
                        SELECT userId, movieId, 1.0 as score, 0.9 as weight, 'favorite' as signal_type
                        FROM cine.Favorite
                        
                        UNION ALL
                        
                        -- Watchlist (weight: 0.7)
                        SELECT userId, movieId, 1.0 as score, 0.7 as weight, 'watchlist' as signal_type
                        FROM cine.Watchlist
                        
                        UNION ALL
                        
                        -- Comments (weight: 0.6)
                        SELECT userId, movieId, 1.0 as score, 0.6 as weight, 'comment' as signal_type
                        FROM cine.Comment
                    )
                    SELECT userId, movieId, 
                           SUM(score * weight) as weighted_score,
                           COUNT(*) as interaction_count,
                           STRING_AGG(signal_type, ',') as signals
                    FROM UserMovieInteractions
                    GROUP BY userId, movieId
                """, conn)
                
                # Load movies data
                self.movies_df = pd.read_sql("""
                    SELECT movieId, title, releaseYear, country, posterUrl, viewCount, overview
                    FROM cine.Movie
                """, conn)
                
                # Load users data
                self.users_df = pd.read_sql("""
                    SELECT userId, email, status
                    FROM cine.[User]
                    WHERE status = 'active'
                """, conn)
                
                self.interactions_df = interactions_df
                print(f"Loaded {len(interactions_df)} interactions, {len(self.movies_df)} movies, {len(self.users_df)} users")
                
        except Exception as e:
            logger.error(f"Error loading data: {e}")
            raise
    
    def create_interaction_matrix(self):
        """Tạo interaction matrix từ weighted scores"""
        try:
            # Tạo mapping cho users và items
            unique_users = sorted(self.interactions_df['userId'].unique())
            unique_movies = sorted(self.interactions_df['movieId'].unique())
            
            self.user_mapping = {user_id: idx for idx, user_id in enumerate(unique_users)}
            self.item_mapping = {movie_id: idx for idx, movie_id in enumerate(unique_movies)}
            self.reverse_user_mapping = {idx: user_id for user_id, idx in self.user_mapping.items()}
            self.reverse_item_mapping = {idx: movie_id for movie_id, idx in self.item_mapping.items()}
            
            # Tạo sparse matrix với weighted scores
            user_indices = [self.user_mapping[uid] for uid in self.interactions_df['userId']]
            item_indices = [self.item_mapping[mid] for mid in self.interactions_df['movieId']]
            scores = self.interactions_df['weighted_score'].values
            
            self.interaction_matrix = csr_matrix(
                (scores, (user_indices, item_indices)),
                shape=(len(unique_users), len(unique_movies))
            )
            
            print(f"Created interaction matrix: {self.interaction_matrix.shape}")
            
        except Exception as e:
            logger.error(f"Error creating interaction matrix: {e}")
            raise
    
    def train_model(self, n_factors=100, n_iterations=30, regularization=0.01):
        """Train enhanced collaborative filtering model"""
        try:
            print("Training enhanced collaborative filtering model...")
            
            # Sử dụng TruncatedSVD cho matrix factorization
            svd = TruncatedSVD(n_components=n_factors, random_state=42)
            
            # Fit model
            self.user_factors = svd.fit_transform(self.interaction_matrix)
            self.item_factors = svd.components_.T
            
            print(f"Model trained with {n_factors} factors")
            
            # Tính similarity matrices
            self.user_similarity_matrix = cosine_similarity(self.user_factors)
            self.item_similarity_matrix = cosine_similarity(self.item_factors)
            
            print("Similarity matrices computed")
            
        except Exception as e:
            logger.error(f"Error training model: {e}")
            raise
    
    def save_model(self, model_path=None):
        """Lưu model đã train"""
        if model_path is None:
            # Use absolute path
            model_path = os.path.join(os.path.dirname(__file__), 'enhanced_cf_model.pkl')
        
        try:
            os.makedirs(os.path.dirname(model_path), exist_ok=True)
            
            model_data = {
                'user_factors': self.user_factors,
                'item_factors': self.item_factors,
                'user_similarity_matrix': self.user_similarity_matrix,
                'item_similarity_matrix': self.item_similarity_matrix,
                'interaction_matrix': self.interaction_matrix,
                'user_mapping': self.user_mapping,
                'item_mapping': self.item_mapping,
                'reverse_user_mapping': self.reverse_user_mapping,
                'reverse_item_mapping': self.reverse_item_mapping,
                'movies_df': self.movies_df,
                'users_df': self.users_df,
                'interactions_df': self.interactions_df
            }
            
            with open(model_path, 'wb') as f:
                pickle.dump(model_data, f)
            
            print(f"Enhanced model saved to {model_path}")
            
        except Exception as e:
            logger.error(f"Error saving model: {e}")
            raise
    
    def load_model(self, model_path=None):
        """Load model đã train"""
        if model_path is None:
            # Use absolute path
            model_path = os.path.join(os.path.dirname(__file__), 'enhanced_cf_model.pkl')
        try:
            with open(model_path, 'rb') as f:
                model_data = pickle.load(f)
            
            self.user_factors = model_data['user_factors']
            self.item_factors = model_data['item_factors']
            self.user_similarity_matrix = model_data['user_similarity_matrix']
            self.item_similarity_matrix = model_data['item_similarity_matrix']
            self.interaction_matrix = model_data['interaction_matrix']
            self.user_mapping = model_data['user_mapping']
            self.item_mapping = model_data['item_mapping']
            self.reverse_user_mapping = model_data['reverse_user_mapping']
            self.reverse_item_mapping = model_data['reverse_item_mapping']
            self.movies_df = model_data['movies_df']
            self.users_df = model_data['users_df']
            self.interactions_df = model_data['interactions_df']
            
            print(f"Enhanced model loaded from {model_path}")
            
        except Exception as e:
            logger.error(f"Error loading model: {e}")
            raise
    
    def get_user_recommendations(self, user_id: int, n_recommendations: int = 10) -> List[Tuple[int, float]]:
        """Lấy recommendations cho một user"""
        if user_id not in self.user_mapping:
            logger.warning(f"User {user_id} not found in model")
            return []
        
        user_idx = self.user_mapping[user_id]
        
        # Get user's interacted items
        interacted_items = set(self.interaction_matrix[user_idx].indices)
        
        # Calculate scores for all items
        user_vector = self.user_factors[user_idx]
        scores = user_vector @ self.item_factors.T
        
        # Filter out already interacted items and get top recommendations
        recommendations = []
        for item_idx, score in enumerate(scores):
            item_id = self.reverse_item_mapping[item_idx]
            if item_id not in interacted_items:
                recommendations.append((item_id, float(score)))
        
        # Sort by score and return top N
        recommendations.sort(key=lambda x: x[1], reverse=True)
        return recommendations[:n_recommendations]
    
    def get_similar_users(self, user_id: int, n_similar: int = 10) -> List[Tuple[int, float]]:
        """Lấy users tương tự"""
        if user_id not in self.user_mapping:
            return []
        
        user_idx = self.user_mapping[user_id]
        similarities = self.user_similarity_matrix[user_idx]
        
        # Get top similar users (excluding self)
        similar_users = []
        for other_user_idx, similarity in enumerate(similarities):
            if other_user_idx != user_idx:
                other_user_id = self.reverse_user_mapping[other_user_idx]
                similar_users.append((other_user_id, float(similarity)))
        
        similar_users.sort(key=lambda x: x[1], reverse=True)
        return similar_users[:n_similar]
    
    def get_similar_movies(self, movie_id: int, n_similar: int = 10) -> List[Tuple[int, float]]:
        """Lấy movies tương tự"""
        if movie_id not in self.item_mapping:
            return []
        
        movie_idx = self.item_mapping[movie_id]
        similarities = self.item_similarity_matrix[movie_idx]
        
        # Get top similar movies (excluding self)
        similar_movies = []
        for other_movie_idx, similarity in enumerate(similarities):
            if other_movie_idx != movie_idx:
                other_movie_id = self.reverse_item_mapping[other_movie_idx]
                similar_movies.append((other_movie_id, float(similarity)))
        
        similar_movies.sort(key=lambda x: x[1], reverse=True)
        return similar_movies[:n_similar]

def main():
    """Main function để train enhanced model"""
    try:
        # Database connection
        connection_string = (
            "DRIVER={ODBC Driver 17 for SQL Server};"
            "SERVER=localhost;"
            "DATABASE=CineBox;"
            "Trusted_Connection=yes;"
        )
        
        db_url = URL.create("mssql+pyodbc", query={"odbc_connect": connection_string})
        engine = create_engine(db_url)
        
        # Train enhanced model
        trainer = EnhancedCollaborativeFilteringTrainer(engine)
        trainer.load_data_from_database()
        trainer.create_interaction_matrix()
        trainer.train_model()
        trainer.save_model()
        
        print("Enhanced model training completed successfully!")
        
    except Exception as e:
        logger.error(f"Error in main: {e}")
        raise

if __name__ == "__main__":
    main()
