#!/usr/bin/env python3
"""
Collaborative Filtering Model Trainer
Train enhanced CF model with multiple interaction signals and time decay
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
from scipy.sparse import csr_matrix
from sklearn.metrics.pairwise import cosine_similarity
from implicit.als import AlternatingLeastSquares

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class CollaborativeFilteringTrainer:
    """
    Collaborative Filtering Trainer
    Train model với multiple interaction signals và time decay support
    """
    
    def __init__(self, db_engine):
        self.db_engine = db_engine
        self.interaction_weights = {
            'view_history': 1.0,   # Completed View ≥70% - Tín hiệu mạnh nhất
            'rating': 0.75,         # Hành vi rõ ràng, tin cậy
            'favorite': 0.35,       # Trung bình
            'comment': 0.20,        # Có thể tiêu cực/không chắc
            'watchlist': 0.18,      # Ý định, chưa chắc chán
            'cold_start': 0.05
        }
    
    def load_data_from_database(self, sample_size=None):
        """Load tất cả dữ liệu từ database với trọng số"""
        try:
            logger.info("Loading interaction data from database...")
            
            with self.db_engine.connect() as conn:
                all_interactions = []
                
                # 1. View History - 1.0 (chỉ cho completed view ≥70%)
                logger.info("Loading view history...")
                view_history_query = text("""
                    SELECT vh.userId, vh.movieId,
                           CASE 
                               -- Xem xong hoặc tiến trình ≥70%
                               WHEN vh.finishedAt IS NOT NULL THEN 1.0
                               WHEN m.durationMin > 0 AND 
                                    (CAST(vh.progressSec AS FLOAT) / 60.0 / m.durationMin) >= 0.7 THEN 1.0
                               -- Không đủ 70% -> không tính
                               ELSE 0
                           END as weight,
                           'view_history' as interaction_type
                    FROM cine.ViewHistory vh
                    INNER JOIN cine.[User] u ON vh.userId = u.userId
                    INNER JOIN cine.Movie m ON vh.movieId = m.movieId
                    WHERE u.status = 'active'
                """)
                view_history_df = pd.read_sql(view_history_query, conn)
                # Lọc bỏ các record có weight = 0 (không đủ 70%)
                if not view_history_df.empty:
                    view_history_df = view_history_df[view_history_df['weight'] > 0]
                    if not view_history_df.empty:
                        all_interactions.append(view_history_df)
                        logger.info(f"Loaded {len(view_history_df)} view history interactions (>=70% completed)")
                
                # 2. Ratings - 0.75 (Hành vi rõ ràng, tin cậy)
                logger.info("Loading ratings...")
                ratings_query = text("""
                    SELECT r.userId, r.movieId, 
                           (0.75 * CAST(r.value AS FLOAT) / 5.0) as weight,
                           'rating' as interaction_type
                    FROM cine.Rating r
                    INNER JOIN cine.[User] u ON r.userId = u.userId
                    WHERE u.status = 'active' AND r.value IS NOT NULL
                """)
                ratings_df = pd.read_sql(ratings_query, conn)
                if not ratings_df.empty:
                    all_interactions.append(ratings_df)
                    logger.info(f"Loaded {len(ratings_df)} rating interactions")
                
                # 3. Favorites - 0.35 (Trung bình)
                logger.info("Loading favorites...")
                favorites_query = text("""
                    SELECT userId, movieId, 0.35 as weight, 'favorite' as interaction_type
                    FROM cine.Favorite
                """)
                favorites_df = pd.read_sql(favorites_query, conn)
                if not favorites_df.empty:
                    all_interactions.append(favorites_df)
                    logger.info(f"Loaded {len(favorites_df)} favorite interactions")
                
                # 4. Watchlist - 0.18 (Ý định, chưa chắc chán)
                logger.info("Loading watchlist...")
                watchlist_query = text("""
                    SELECT userId, movieId, 0.18 as weight, 'watchlist' as interaction_type
                    FROM cine.Watchlist
                """)
                watchlist_df = pd.read_sql(watchlist_query, conn)
                if not watchlist_df.empty:
                    all_interactions.append(watchlist_df)
                    logger.info(f"Loaded {len(watchlist_df)} watchlist interactions")
                
                # 5. Comments - 0.20 (Có thể tiêu cực/không chắc)
                logger.info("Loading comments...")
                comments_query = text("""
                    SELECT userId, movieId, 0.20 as weight, 'comment' as interaction_type
                    FROM cine.Comment
                """)
                comments_df = pd.read_sql(comments_query, conn)
                if not comments_df.empty:
                    all_interactions.append(comments_df)
                    logger.info(f"Loaded {len(comments_df)} comment interactions")
                
                # 6. Cold Start - SKIPPED
                # With 3M+ existing ratings, cold start data is not needed
                # and causes memory issues with large user-item matrix
                logger.info("Skipping cold start data (not needed with 3M+ ratings)")
                
                # Combine all interactions
                if not all_interactions:
                    logger.error("No interactions found in database")
                    return None
                
                aggregated_df = pd.concat(all_interactions, ignore_index=True)
                
                # Group by user-movie pair and sum weights
                aggregated_df = aggregated_df.groupby(['userId', 'movieId']).agg({
                    'weight': 'sum',
                    'interaction_type': lambda x: ','.join(x)
                }).reset_index()
                
                # Đảm bảo userId và movieId là int để khớp với DB
                aggregated_df['userId'] = aggregated_df['userId'].astype(int)
                aggregated_df['movieId'] = aggregated_df['movieId'].astype(int)
                
                # Apply sampling if specified
                if sample_size and len(aggregated_df) > sample_size:
                    aggregated_df = aggregated_df.sample(n=sample_size, random_state=42)
                
                logger.info(f"Total interactions loaded: {len(aggregated_df)}")
                logger.info(f"Unique users: {aggregated_df['userId'].nunique()}")
                logger.info(f"Unique movies: {aggregated_df['movieId'].nunique()}")
                logger.info(f"User ID range: {aggregated_df['userId'].min()} - {aggregated_df['userId'].max()}")
                logger.info(f"Movie ID range: {aggregated_df['movieId'].min()} - {aggregated_df['movieId'].max()}")
                
                return aggregated_df
                
        except Exception as e:
            logger.error(f"Error loading data: {e}")
            return None
    
    def preprocess_data(self, interactions_df, min_interactions=1):
        """Preprocess data for training"""
        try:
            logger.info("Preprocessing data...")
            logger.info(f"Initial data: {len(interactions_df)} interactions, {interactions_df['userId'].nunique()} users, {interactions_df['movieId'].nunique()} movies")
            
            # Filter users and items with minimum interactions
            user_counts = interactions_df['userId'].value_counts()
            movie_counts = interactions_df['movieId'].value_counts()
            
            valid_users = user_counts[user_counts >= min_interactions].index
            valid_movies = movie_counts[movie_counts >= min_interactions].index
            
            logger.info(f"After filtering (min_interactions={min_interactions}): {len(valid_users)} users, {len(valid_movies)} movies")
            
            # Filter data
            filtered_df = interactions_df[
                (interactions_df['userId'].isin(valid_users)) & 
                (interactions_df['movieId'].isin(valid_movies))
            ].copy()
            
            logger.info(f"After filtering: {len(filtered_df)} interactions, "
                       f"{len(valid_users)} users, {len(valid_movies)} movies")
            
            return filtered_df, valid_users, valid_movies
            
        except Exception as e:
            logger.error(f"Error preprocessing data: {e}")
            return None, None, None
    
    def train_model(self, interactions_df, n_factors=50, iterations=15, regularization=0.01):
        """Train ALS model"""
        try:
            logger.info(f"Training ALS model with {n_factors} factors, {iterations} iterations...")
            
            # Create user-item matrix directly as sparse matrix to save memory
            logger.info("Creating sparse user-item matrix...")
            
            # Get unique users and movies with their mappings
            unique_users = interactions_df['userId'].unique()
            unique_movies = interactions_df['movieId'].unique()
            
            # Create mappings
            user_to_idx = {user_id: idx for idx, user_id in enumerate(sorted(unique_users))}
            movie_to_idx = {movie_id: idx for idx, movie_id in enumerate(sorted(unique_movies))}
            
            n_users = len(unique_users)
            n_movies = len(unique_movies)
            
            logger.info(f"Matrix size: {n_users} users x {n_movies} movies")
            logger.info(f"Estimated memory for dense matrix: {n_users * n_movies * 4 / (1024**3):.2f} GB (float32)")
            
            # Create sparse matrix directly from interactions (much more memory efficient)
            rows = interactions_df['userId'].map(user_to_idx).values
            cols = interactions_df['movieId'].map(movie_to_idx).values
            data = interactions_df['weight'].astype(np.float32).values
            
            # Create CSR matrix directly (no dense intermediate)
            matrix = csr_matrix((data, (rows, cols)), shape=(n_users, n_movies), dtype=np.float32)
            
            logger.info(f"Sparse matrix created: {matrix.nnz} non-zero elements ({matrix.nnz / (n_users * n_movies) * 100:.4f}% density)")
            logger.info(f"Memory usage: ~{matrix.data.nbytes / (1024**2):.2f} MB (sparse)")
            
            # Store mappings for later use
            self.user_to_idx = user_to_idx
            self.movie_to_idx = movie_to_idx
            self.idx_to_user = {idx: user_id for user_id, idx in user_to_idx.items()}
            self.idx_to_movie = {idx: movie_id for movie_id, idx in movie_to_idx.items()}
            
            # Train ALS model
            model = AlternatingLeastSquares(
                factors=n_factors,
                iterations=iterations,
                regularization=regularization,
                random_state=42,
                use_gpu=False
            )
            
            logger.info("Fitting ALS model (this may take a while)...")
            model.fit(matrix, show_progress=True)
            
            # Get factors
            user_factors = model.user_factors
            item_factors = model.item_factors
            
            # Use mappings we created earlier (already stored in self)
            user_mapping = {int(user_id): idx for user_id, idx in self.user_to_idx.items()}
            item_mapping = {int(movie_id): idx for movie_id, idx in self.movie_to_idx.items()}
            
            reverse_user_mapping = {idx: user_id for user_id, idx in user_mapping.items()}
            reverse_item_mapping = {idx: movie_id for movie_id, idx in item_mapping.items()}
            
            logger.info(f"Created mappings: {len(user_mapping)} users, {len(item_mapping)} items")
            logger.info(f"Sample user IDs: {list(user_mapping.keys())[:5]}")
            logger.info(f"Sample movie IDs: {list(item_mapping.keys())[:5]}")
            
            logger.info(f"Model trained: {user_factors.shape[0]} users, {item_factors.shape[0]} items")
            
            # Skip similarity matrices to reduce model size
            # Similarity matrices are too large (48K x 48K) and not essential
            # They can be computed on-the-fly when needed
            logger.info("Optimizing model for storage...")
            
            # Don't save full user_item_matrix (can be reconstructed from factors)
            # Save only the sparse matrix indices for faster lookups
            logger.info("Model optimization complete")
            
            return {
                'user_factors': user_factors,
                'item_factors': item_factors,
                'user_mapping': user_mapping,
                'item_mapping': item_mapping,
                'reverse_user_mapping': reverse_user_mapping,
                'reverse_item_mapping': reverse_item_mapping,
                'user_item_matrix': None,  # Skip to save disk space (can reconstruct)
                'user_similarity_matrix': None,  # Skip to save memory
                'item_similarity_matrix': None,  # Skip to save memory
                'interaction_weights': self.interaction_weights
            }
            
        except Exception as e:
            logger.error(f"Error training model: {e}")
            return None
    
    def save_model(self, model_data, model_path=None):
        """Save trained model"""
        if model_path is None:
            # Default to enhanced_cf_model.pkl
            model_path = os.path.join(os.path.dirname(__file__), 'enhanced_cf_model.pkl')
        
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
    
    def train_full_pipeline(self, sample_size=None, n_factors=50, iterations=15, 
                           min_interactions=1, model_path=None):
        """Complete training pipeline"""
        try:
            logger.info("="*60)
            logger.info("Starting Collaborative Filtering Training Pipeline")
            logger.info("="*60)
            logger.info(f"Configuration:")
            logger.info(f"  - Sample size: {sample_size if sample_size else 'All data'}")
            logger.info(f"  - Latent factors: {n_factors}")
            logger.info(f"  - Iterations: {iterations}")
            logger.info(f"  - Min interactions: {min_interactions}")
            logger.info(f"  - Interaction weights: {self.interaction_weights}")
            logger.info("="*60)
            
            # Load data
            interactions_df = self.load_data_from_database(sample_size)
            if interactions_df is None:
                return False
            
            # Preprocess data
            filtered_df, valid_users, valid_movies = self.preprocess_data(
                interactions_df, min_interactions
            )
            if filtered_df is None:
                return False
            
            # Train model
            model_data = self.train_model(filtered_df, n_factors, iterations)
            if model_data is None:
                return False
            
            # Save model
            if self.save_model(model_data, model_path):
                logger.info("="*60)
                logger.info("Training completed successfully!")
                logger.info(f"Model saved to: {model_path if model_path else 'enhanced_cf_model.pkl'}")
                logger.info("="*60)
                return True
            else:
                return False
                
        except Exception as e:
            logger.error(f"Error in training pipeline: {e}", exc_info=True)
            print(f"\n[ERROR] Training pipeline failed: {e}")
            import traceback
            traceback.print_exc()
            return False


def main():
    """Main training function"""
    import sys
    
    print("\n" + "="*60)
    print("COLLABORATIVE FILTERING MODEL TRAINER")
    print("="*60 + "\n")
    
    # Database connection - sử dụng config từ config.py
    try:
        # Add parent directories to path để import config
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # script_dir = cinebox/model_collaborative
        cinebox_dir = os.path.dirname(script_dir)  # cinebox directory
        project_dir = os.path.dirname(cinebox_dir)  # project root directory
        
        # Thêm cả project root và cinebox vào path
        for dir_path in [project_dir, cinebox_dir]:
            if dir_path not in sys.path:
                sys.path.insert(0, dir_path)
        
        # Import từ cinebox.config
        try:
            from cinebox.config import get_config
        except ImportError:
            # Fallback: thử import trực tiếp nếu đang chạy từ cinebox directory
            try:
                from config import get_config
            except ImportError:
                # Last resort: import trực tiếp từ file
                import importlib.util
                config_path = os.path.join(cinebox_dir, 'config.py')
                spec = importlib.util.spec_from_file_location("config", config_path)
                config_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(config_module)
                get_config = config_module.get_config
        
        config = get_config()
        
        # Build ODBC connection string từ config
        odbc_str = (
            f"DRIVER={{{config.SQLSERVER_DRIVER}}};"
            f"SERVER={config.SQLSERVER_SERVER};"
            f"DATABASE={config.SQLSERVER_DB};"
            f"UID={config.SQLSERVER_UID};"
            f"PWD={config.SQLSERVER_PWD};"
            f"Encrypt={config.SQL_ENCRYPT};"
            f"TrustServerCertificate={config.SQL_TRUST_CERT};"
        )
        connection_url = URL.create("mssql+pyodbc", query={"odbc_connect": odbc_str})
        db_engine = create_engine(connection_url, fast_executemany=True)
        logger.info(f"Connected to database: {config.SQLSERVER_DB} on {config.SQLSERVER_SERVER}")
    except Exception as e:
        logger.error(f"Failed to load config or connect to database: {e}", exc_info=True)
        print(f"\n[ERROR] Failed to connect to database: {e}")
        print("Please check your config.py or environment variables.")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # Initialize trainer
    trainer = CollaborativeFilteringTrainer(db_engine)
    
    # Training configuration
    print("Training Configuration:")
    print("-" * 60)
    print("Model Type: Enhanced Collaborative Filtering (ALS)")
    print("Sample Size: ALL INTERACTIONS (Full Training)")
    print("Latent Factors: 64 (Increased for better accuracy)")
    print("Iterations: 20 (Increased for convergence)")
    print("Regularization: 0.01")
    print("Min Interactions: 3 (filter sparse users/movies)")
    print("\nInteraction Weights (Updated):")
    print("  [OK] Completed View >=70%: 1.0  (Tin hieu manh nhat - thuc xem)")
    print("  [*] Rating: 0.75              (Hanh vi ro rang, tin cay)")
    print("  [*] Favorite: 0.35            (Trung binh)")
    print("  [*] Comment: 0.20             (Co the tieu cuc/khong chac)")
    print("  [*] Watchlist: 0.18           (Y dinh, chua chac chan)")
    print("  - Cold Start: SKIPPED (not needed)")
    print("-" * 60)
    print("Estimated training time: 10-15 minutes")
    print("Improvements:")
    print("  [+] More factors (64 vs 30) -> Better representation")
    print("  [+] More iterations (20 vs 10) -> Better convergence")
    print("  [+] All data (no sampling) -> Better coverage")
    print("-" * 60)
    print("\nStarting training...\n")
    
    # Train model - Optimized configuration for memory efficiency
    success = trainer.train_full_pipeline(
        sample_size=None,     # Use ALL data (no sampling)
        n_factors=50,         # Reduced from 64 to 50 for memory efficiency (still good quality)
        iterations=15,        # Reduced from 20 to 15 (good balance)
        min_interactions=5    # Increased from 3 to 5 to filter more sparse users/movies
    )
    
    if success:
        print("\n" + "="*60)
        print("[SUCCESS] Training completed successfully!")
        print("="*60)
        print("\nModel saved to: cinebox/model_collaborative/enhanced_cf_model.pkl")
        print("You can now use the model in your web application.")
        print("\nTo use in routes.py:")
        print("  from recommenders.enhanced_cf import EnhancedCFRecommender")
        print("  recommender = EnhancedCFRecommender(db_engine)")
        print("  recommendations = recommender.get_user_recommendations(user_id)")
        sys.exit(0)  # Exit với code 0 = success
    else:
        print("\n" + "="*60)
        print("[FAILED] Training failed!")
        print("="*60)
        print("Please check the logs above for error details.")
        sys.exit(1)  # Exit với code 1 = failure


if __name__ == "__main__":
    main()

