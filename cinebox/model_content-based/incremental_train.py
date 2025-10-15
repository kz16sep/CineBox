#!/usr/bin/env python3
"""
Incremental Training - Chỉ train cho phim mới được thêm
Hiệu quả hơn retrain toàn bộ
"""

import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import MinMaxScaler
from sqlalchemy import create_engine, text
import logging
import time
from tqdm import tqdm
import joblib

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class IncrementalTrainer:
    """
    Incremental Training cho phim mới
    """
    
    def __init__(self, db_engine):
        self.db_engine = db_engine
        self.model_data = None
        self.load_existing_model()
        
    def load_existing_model(self):
        """Load mô hình đã train"""
        try:
            self.model_data = joblib.load('hybrid_model_backup.pkl')
            logger.info("✅ Loaded existing model components")
        except Exception as e:
            logger.error(f"❌ Could not load existing model: {e}")
            self.model_data = None
    
    def get_new_movies(self):
        """Lấy phim mới chưa có similarity"""
        with self.db_engine.connect() as conn:
            # Lấy phim chưa có trong MovieSimilarity
            new_movies = conn.execute(text("""
                SELECT m.movieId, m.title, m.releaseYear, m.overview, m.country,
                       m.director, m.cast, m.durationMin, m.imdbRating, m.viewCount
                FROM cine.Movie m
                LEFT JOIN cine.MovieSimilarity ms ON m.movieId = ms.movieId
                WHERE ms.movieId IS NULL
            """)).mappings().all()
            
            return pd.DataFrame(new_movies)
    
    def get_existing_movies_features(self):
        """Lấy features của phim đã có similarity"""
        with self.db_engine.connect() as conn:
            # Lấy tất cả phim đã có similarity
            existing_movies = conn.execute(text("""
                SELECT DISTINCT m.movieId, m.title, m.releaseYear, m.overview, m.country,
                       m.director, m.cast, m.durationMin, m.imdbRating, m.viewCount
                FROM cine.Movie m
                INNER JOIN cine.MovieSimilarity ms ON m.movieId = ms.movieId
            """)).mappings().all()
            
            return pd.DataFrame(existing_movies)
    
    def create_features_for_movies(self, movies_df):
        """Tạo features cho danh sách phim"""
        if self.model_data is None:
            logger.error("No existing model found!")
            return None
            
        # Prepare data
        movies_data = movies_df.copy()
        movies_data['genres'] = movies_data.get('genres', '')
        movies_data['tags'] = movies_data.get('tags', '')
        movies_data['title'] = movies_data['title'].fillna('')
        movies_data['overview'] = movies_data['overview'].fillna('')
        
        # Get vectorizers from existing model
        vectorizers = self.model_data['vectorizers']
        feature_weights = self.model_data['feature_weights']
        
        # Create features
        genres_features = vectorizers['genres'].transform(movies_data['genres'])
        tags_features = vectorizers['tags'].transform(movies_data['tags'])
        title_features = vectorizers['title'].transform(movies_data['title'])
        
        # Other features
        year_features = np.array(movies_data['releaseYear'].fillna(1995)).reshape(-1, 1)
        popularity_features = np.array(movies_data.get('viewCount', 0)).reshape(-1, 1)
        rating_features = np.array(movies_data.get('imdbRating', 5.0)).reshape(-1, 1)
        
        # Scale features
        scaler = MinMaxScaler()
        year_features = scaler.fit_transform(year_features)
        popularity_features = scaler.fit_transform(popularity_features)
        rating_features = scaler.fit_transform(rating_features)
        
        # Combine features
        features = np.hstack([
            genres_features * feature_weights['genres'],
            tags_features * feature_weights['tags'],
            title_features * feature_weights['title'],
            year_features * feature_weights['year'],
            popularity_features * feature_weights['popularity'],
            rating_features * feature_weights['rating']
        ])
        
        return features
    
    def train_incremental(self, top_n=20):
        """Train incremental cho phim mới"""
        logger.info("Starting incremental training...")
        
        # Get new movies
        new_movies = self.get_new_movies()
        if new_movies.empty:
            logger.info("No new movies to train!")
            return True
            
        logger.info(f"Found {len(new_movies)} new movies to train")
        
        # Get existing movies
        existing_movies = self.get_existing_movies_features()
        if existing_movies.empty:
            logger.warning("No existing movies found! Please run full training first.")
            return False
            
        logger.info(f"Found {len(existing_movies)} existing movies for comparison")
        
        # Create features
        new_features = self.create_features_for_movies(new_movies)
        existing_features = self.create_features_for_movies(existing_movies)
        
        if new_features is None or existing_features is None:
            return False
        
        # Calculate similarities between new movies and all existing movies
        similarities = cosine_similarity(new_features, existing_features)
        
        # Save similarities
        with self.db_engine.connect() as conn:
            saved_count = 0
            
            for i, new_movie_id in enumerate(tqdm(new_movies['movieId'], desc="Training new movies")):
                # Get top similar existing movies
                movie_similarities = similarities[i]
                similar_indices = np.argsort(movie_similarities)[::-1][:top_n]
                
                for j in similar_indices:
                    similar_movie_id = existing_movies.iloc[j]['movieId']
                    similarity_score = movie_similarities[j]
                    
                    if similarity_score > 0.1:  # Threshold
                        # Save both directions
                        conn.execute(text("""
                            INSERT INTO cine.MovieSimilarity (movieId, similarMovieId, similarity)
                            VALUES (:movie_id, :similar_id, :similarity)
                        """), {
                            "movie_id": int(new_movie_id),
                            "similar_id": int(similar_movie_id),
                            "similarity": float(similarity_score)
                        })
                        
                        # Also save reverse direction
                        conn.execute(text("""
                            INSERT INTO cine.MovieSimilarity (movieId, similarMovieId, similarity)
                            VALUES (:movie_id, :similar_id, :similarity)
                        """), {
                            "movie_id": int(similar_movie_id),
                            "similar_id": int(new_movie_id),
                            "similarity": float(similarity_score)
                        })
                        
                        saved_count += 2
            
            conn.commit()
            logger.info(f"✅ Saved {saved_count} similarity pairs for new movies")
        
        return True

def main():
    """Main function"""
    # Database connection
    conn_str = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER=localhost,1433;"
        f"DATABASE=CineBoxDB;"
        f"UID=sa;"
        f"PWD=sapassword;"
        f"Encrypt=yes;"
        f"TrustServerCertificate=yes;"
    )
    
    engine = create_engine(conn_str, pool_pre_ping=True)
    
    # Initialize incremental trainer
    trainer = IncrementalTrainer(engine)
    
    # Train incremental
    success = trainer.train_incremental()
    
    if success:
        print("✅ Incremental training completed!")
        print("🎬 New movies now have recommendations!")
    else:
        print("❌ Incremental training failed!")

if __name__ == "__main__":
    main()
