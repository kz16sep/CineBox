#!/usr/bin/env python3
"""
Collaborative Filtering Trainer với Implicit Feedback
Sử dụng Alternating Least Squares (ALS) cho implicit feedback
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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class CollaborativeFilteringTrainer:
    """
    Collaborative Filtering Trainer với Implicit Feedback
    Sử dụng ALS (Alternating Least Squares) algorithm
    """
    
    def __init__(self, db_engine):
        self.db_engine = db_engine
        self.ratings_df = None
        self.movies_df = None
        self.users_df = None
        self.user_item_matrix = None
        self.user_similarity_matrix = None
        self.item_similarity_matrix = None
        self.user_factors = None
        self.item_factors = None
        self.user_mapping = {}
        self.item_mapping = {}
        self.reverse_user_mapping = {}
        self.reverse_item_mapping = {}
        
    def load_data_from_database(self):
        """Load dữ liệu từ database CineBox"""
        logger.info("Loading data from CineBox database...")
        
        try:
            with self.db_engine.connect() as conn:
                # Load ratings
                logger.info("Loading ratings...")
                ratings_query = text("""
                    SELECT r.userId, r.movieId, r.value, r.ratedAt
                    FROM cine.Rating r
                    INNER JOIN cine.[User] u ON r.userId = u.userId
                    INNER JOIN cine.Movie m ON r.movieId = m.movieId
                    WHERE u.status = 'active'
                """)
                self.ratings_df = pd.read_sql(ratings_query, conn)
                logger.info(f"Loaded {len(self.ratings_df)} ratings")
                
                # Load movies
                logger.info("Loading movies...")
                movies_query = text("""
                    SELECT movieId, title, releaseYear, viewCount
                    FROM cine.Movie
                """)
                self.movies_df = pd.read_sql(movies_query, conn)
                logger.info(f"Loaded {len(self.movies_df)} movies")
                
                # Load users
                logger.info("Loading users...")
                users_query = text("""
                    SELECT userId, email, createdAt, lastLoginAt
                    FROM cine.[User]
                    WHERE status = 'active'
                """)
                self.users_df = pd.read_sql(users_query, conn)
                logger.info(f"Loaded {len(self.users_df)} users")
                
        except Exception as e:
            logger.error(f"Error loading data from database: {e}")
            raise
    
    def load_movielens_data(self, data_path: str):
        """Load dữ liệu từ MovieLens dataset (backup method)"""
        logger.info("Loading MovieLens dataset...")
        
        try:
            # Load ratings
            self.ratings_df = pd.read_csv(f"{data_path}/ratings.csv")
            logger.info(f"Loaded {len(self.ratings_df)} ratings")
            
            # Load movies
            self.movies_df = pd.read_csv(f"{data_path}/movies.csv")
            logger.info(f"Loaded {len(self.movies_df)} movies")
            
            # Create users from ratings
            unique_users = self.ratings_df['userId'].unique()
            self.users_df = pd.DataFrame({
                'userId': unique_users,
                'email': [f'user{uid}@movielens.local' for uid in unique_users]
            })
            logger.info(f"Created {len(self.users_df)} users")
            
        except Exception as e:
            logger.error(f"Error loading MovieLens data: {e}")
            raise
    
    def preprocess_implicit_feedback(self, min_rating: float = 3.0, min_interactions: int = 5):
        """
        Chuyển đổi explicit ratings thành implicit feedback
        
        Args:
            min_rating: Rating tối thiểu để coi là positive feedback
            min_interactions: Số lượng tương tác tối thiểu của user/item
        """
        logger.info("Preprocessing implicit feedback...")
        
        # Filter ratings >= min_rating
        positive_ratings = self.ratings_df[self.ratings_df['value'] >= min_rating].copy()
        logger.info(f"Positive ratings (>= {min_rating}): {len(positive_ratings)}")
        
        # Filter users with minimum interactions
        user_counts = positive_ratings['userId'].value_counts()
        active_users = user_counts[user_counts >= min_interactions].index
        positive_ratings = positive_ratings[positive_ratings['userId'].isin(active_users)]
        logger.info(f"Active users (>= {min_interactions} interactions): {len(active_users)}")
        
        # Filter items with minimum interactions
        item_counts = positive_ratings['movieId'].value_counts()
        active_items = item_counts[item_counts >= min_interactions].index
        positive_ratings = positive_ratings[positive_ratings['movieId'].isin(active_items)]
        logger.info(f"Active items (>= {min_interactions} interactions): {len(active_items)}")
        
        # Create user and item mappings
        unique_users = sorted(positive_ratings['userId'].unique())
        unique_items = sorted(positive_ratings['movieId'].unique())
        
        self.user_mapping = {user_id: idx for idx, user_id in enumerate(unique_users)}
        self.item_mapping = {item_id: idx for idx, item_id in enumerate(unique_items)}
        self.reverse_user_mapping = {idx: user_id for user_id, idx in self.user_mapping.items()}
        self.reverse_item_mapping = {idx: item_id for item_id, idx in self.item_mapping.items()}
        
        logger.info(f"Final dataset: {len(unique_users)} users, {len(unique_items)} items")
        
        # Create implicit feedback matrix
        self._create_user_item_matrix(positive_ratings)
        
        return positive_ratings
    
    def _create_user_item_matrix(self, ratings_df: pd.DataFrame):
        """Tạo user-item matrix cho implicit feedback"""
        logger.info("Creating user-item matrix...")
        
        # Map user and item IDs to matrix indices
        user_indices = [self.user_mapping[uid] for uid in ratings_df['userId']]
        item_indices = [self.item_mapping[mid] for mid in ratings_df['movieId']]
        
        # Create implicit feedback values (binary: 1 if positive, 0 if not)
        values = np.ones(len(ratings_df))
        
        # Create sparse matrix
        n_users = len(self.user_mapping)
        n_items = len(self.item_mapping)
        
        self.user_item_matrix = csr_matrix(
            (values, (user_indices, item_indices)),
            shape=(n_users, n_items)
        )
        
        logger.info(f"User-item matrix shape: {self.user_item_matrix.shape}")
        logger.info(f"Matrix density: {self.user_item_matrix.nnz / (n_users * n_items):.4f}")
    
    def train_als_model(self, n_factors: int = 50, iterations: int = 10, regularization: float = 0.01):
        """
        Train ALS model cho collaborative filtering
        
        Args:
            n_factors: Số lượng latent factors
            iterations: Số lần lặp
            regularization: Regularization parameter
        """
        logger.info(f"Training ALS model with {n_factors} factors...")
        
        n_users, n_items = self.user_item_matrix.shape
        
        # Initialize factors randomly
        self.user_factors = np.random.normal(0, 0.1, (n_users, n_factors))
        self.item_factors = np.random.normal(0, 0.1, (n_items, n_factors))
        
        # ALS iterations
        for iteration in tqdm(range(iterations), desc="ALS iterations"):
            # Update user factors
            for u in range(n_users):
                # Get items rated by user u
                user_items = self.user_item_matrix[u].indices
                if len(user_items) > 0:
                    # Solve: (X^T * X + λI) * user_factors = X^T * ratings
                    X = self.item_factors[user_items]
                    XtX = X.T @ X
                    XtX += np.eye(n_factors) * regularization
                    
                    # For implicit feedback, we use binary values
                    ratings = self.user_item_matrix[u, user_items].toarray().flatten()
                    XtR = X.T @ ratings
                    
                    try:
                        self.user_factors[u] = np.linalg.solve(XtX, XtR)
                    except np.linalg.LinAlgError:
                        # Fallback to least squares if singular
                        self.user_factors[u] = np.linalg.lstsq(XtX, XtR, rcond=None)[0]
            
            # Update item factors
            for i in range(n_items):
                # Get users who rated item i
                item_users = self.user_item_matrix[:, i].indices
                if len(item_users) > 0:
                    # Solve: (Y^T * Y + λI) * item_factors = Y^T * ratings
                    Y = self.user_factors[item_users]
                    YtY = Y.T @ Y
                    YtY += np.eye(n_factors) * regularization
                    
                    ratings = self.user_item_matrix[item_users, i].toarray().flatten()
                    YtR = Y.T @ ratings
                    
                    try:
                        self.item_factors[i] = np.linalg.solve(YtY, YtR)
                    except np.linalg.LinAlgError:
                        # Fallback to least squares if singular
                        self.item_factors[i] = np.linalg.lstsq(YtY, YtR, rcond=None)[0]
            
            # Calculate RMSE for monitoring
            if iteration % 2 == 0:
                rmse = self._calculate_rmse()
                logger.info(f"Iteration {iteration}: RMSE = {rmse:.4f}")
    
    def _calculate_rmse(self) -> float:
        """Tính RMSE của model"""
        predictions = self.user_factors @ self.item_factors.T
        actual = self.user_item_matrix.toarray()
        
        # Only calculate RMSE for non-zero entries
        mask = actual > 0
        if np.sum(mask) > 0:
            rmse = np.sqrt(np.mean((predictions[mask] - actual[mask]) ** 2))
        else:
            rmse = 0.0
        
        return rmse
    
    def train_svd_model(self, n_components: int = 50):
        """
        Train SVD model (alternative to ALS)
        
        Args:
            n_components: Số lượng components
        """
        logger.info(f"Training SVD model with {n_components} components...")
        
        # Use SVD on user-item matrix
        svd = TruncatedSVD(n_components=n_components, random_state=42)
        self.user_factors = svd.fit_transform(self.user_item_matrix)
        self.item_factors = svd.components_.T
        
        logger.info(f"SVD explained variance ratio: {svd.explained_variance_ratio_.sum():.4f}")
    
    def calculate_similarity_matrices(self):
        """Tính toán similarity matrices"""
        logger.info("Calculating similarity matrices...")
        
        # User similarity matrix
        logger.info("Calculating user similarity...")
        self.user_similarity_matrix = cosine_similarity(self.user_factors)
        
        # Item similarity matrix
        logger.info("Calculating item similarity...")
        self.item_similarity_matrix = cosine_similarity(self.item_factors)
        
        logger.info("Similarity matrices calculated")
    
    def save_model(self, model_path: str):
        """Lưu model và mappings"""
        logger.info(f"Saving model to {model_path}...")
        
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        
        model_data = {
            'user_factors': self.user_factors,
            'item_factors': self.item_factors,
            'user_similarity_matrix': self.user_similarity_matrix,
            'item_similarity_matrix': self.item_similarity_matrix,
            'user_mapping': self.user_mapping,
            'item_mapping': self.item_mapping,
            'reverse_user_mapping': self.reverse_user_mapping,
            'reverse_item_mapping': self.reverse_item_mapping,
            'user_item_matrix': self.user_item_matrix
        }
        
        with open(model_path, 'wb') as f:
            pickle.dump(model_data, f)
        
        logger.info("Model saved successfully")
    
    def load_model(self, model_path: str):
        """Load model và mappings"""
        logger.info(f"Loading model from {model_path}...")
        
        with open(model_path, 'rb') as f:
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
        
        logger.info("Model loaded successfully")
    
    def get_user_recommendations(self, user_id: int, n_recommendations: int = 10) -> List[Tuple[int, float]]:
        """
        Lấy recommendations cho một user
        
        Args:
            user_id: ID của user
            n_recommendations: Số lượng recommendations
            
        Returns:
            List[Tuple[int, float]]: List of (movie_id, score) tuples
        """
        if user_id not in self.user_mapping:
            logger.warning(f"User {user_id} not found in model")
            return []
        
        user_idx = self.user_mapping[user_id]
        
        # Get user's rated items
        rated_items = set(self.user_item_matrix[user_idx].indices)
        
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
    
    def get_similar_users(self, user_id: int, n_similar: int = 10) -> List[Tuple[int, float]]:
        """
        Lấy similar users
        
        Args:
            user_id: ID của user
            n_similar: Số lượng similar users
            
        Returns:
            List[Tuple[int, float]]: List of (user_id, similarity) tuples
        """
        if user_id not in self.user_mapping:
            return []
        
        user_idx = self.user_mapping[user_id]
        similarities = self.user_similarity_matrix[user_idx]
        
        # Get similar users (excluding self)
        similar_users = []
        for other_user_idx, similarity in enumerate(similarities):
            if other_user_idx != user_idx:
                other_user_id = self.reverse_user_mapping[other_user_idx]
                similar_users.append((other_user_id, float(similarity)))
        
        # Sort by similarity and return top N
        similar_users.sort(key=lambda x: x[1], reverse=True)
        return similar_users[:n_similar]
    
    def get_similar_items(self, item_id: int, n_similar: int = 10) -> List[Tuple[int, float]]:
        """
        Lấy similar items
        
        Args:
            item_id: ID của item
            n_similar: Số lượng similar items
            
        Returns:
            List[Tuple[int, float]]: List of (item_id, similarity) tuples
        """
        if item_id not in self.item_mapping:
            return []
        
        item_idx = self.item_mapping[item_id]
        similarities = self.item_similarity_matrix[item_idx]
        
        # Get similar items (excluding self)
        similar_items = []
        for other_item_idx, similarity in enumerate(similarities):
            if other_item_idx != item_idx:
                other_item_id = self.reverse_item_mapping[other_item_idx]
                similar_items.append((other_item_id, float(similarity)))
        
        # Sort by similarity and return top N
        similar_items.sort(key=lambda x: x[1], reverse=True)
        return similar_items[:n_similar]
    
    def train_full_model(self, data_source: str = 'database', data_path: str = None, 
                        min_rating: float = 3.0, min_interactions: int = 5,
                        n_factors: int = 50, iterations: int = 10, 
                        use_als: bool = True, model_path: str = 'model_collaborative/collaborative_model.pkl'):
        """
        Train toàn bộ model collaborative filtering
        
        Args:
            data_source: 'database' hoặc 'movielens'
            data_path: Đường dẫn đến MovieLens data (nếu data_source = 'movielens')
            min_rating: Rating tối thiểu cho positive feedback
            min_interactions: Số tương tác tối thiểu
            n_factors: Số latent factors
            iterations: Số iterations cho ALS
            use_als: Sử dụng ALS (True) hoặc SVD (False)
            model_path: Đường dẫn lưu model
        """
        logger.info("Starting collaborative filtering training...")
        start_time = time.time()
        
        # Load data
        if data_source == 'database':
            self.load_data_from_database()
        elif data_source == 'movielens':
            if not data_path:
                raise ValueError("data_path is required when data_source='movielens'")
            self.load_movielens_data(data_path)
        else:
            raise ValueError("data_source must be 'database' or 'movielens'")
        
        # Preprocess implicit feedback
        self.preprocess_implicit_feedback(min_rating, min_interactions)
        
        # Train model
        if use_als:
            self.train_als_model(n_factors, iterations)
        else:
            self.train_svd_model(n_factors)
        
        # Calculate similarity matrices
        self.calculate_similarity_matrices()
        
        # Save model
        self.save_model(model_path)
        
        # Training statistics
        training_time = time.time() - start_time
        logger.info(f"Training completed in {training_time:.2f} seconds")
        logger.info(f"Model saved to: {model_path}")
        
        return True

def main():
    """Main training function"""
    print("🤝 Collaborative Filtering Training")
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
    trainer = CollaborativeFilteringTrainer(db_engine)
    
    # Train model
    success = trainer.train_full_model(
        data_source='database',
        min_rating=3.0,
        min_interactions=5,
        n_factors=50,
        iterations=10,
        use_als=True
    )
    
    if success:
        print("✅ Collaborative filtering training completed!")
        print("🎬 Model ready for recommendations")
    else:
        print("❌ Training failed!")

if __name__ == "__main__":
    main()
