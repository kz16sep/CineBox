#!/usr/bin/env python3
"""
Improved Content-based Training
Cải thiện mô hình để tránh similarity = 100% bằng cách:
1. Giảm weight của genres
2. Thêm features đa dạng hơn
3. Sử dụng threshold để loại bỏ similarity quá cao
"""

import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import MinMaxScaler
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
import logging
from typing import List, Dict, Tuple
import re

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ImprovedContentTrainer:
    """
    Improved Content-based Trainer
    Cải thiện để tránh similarity = 100%
    """
    
    def __init__(self, db_engine):
        self.db_engine = db_engine
        self.movies_df = None
        self.tags_df = None
        self.ratings_df = None
        
    def load_movielens_data(self, data_path: str):
        """Load MovieLens dataset với sampling"""
        logger.info("Loading MovieLens dataset...")
        
        try:
            # Load movies (sample for performance)
            logger.info("Loading movies (sampling for performance)...")
            self.movies_df = pd.read_csv(f"{data_path}/movies.csv", nrows=10000)
            logger.info(f"Loaded {len(self.movies_df)} movies")
            
            # Load tags (sample for performance)
            logger.info("Loading tags (sampling for performance)...")
            tags_chunk = pd.read_csv(f"{data_path}/tags.csv", nrows=100000)
            
            # Group tags by movieId and join them
            self.tags_df = tags_chunk.groupby('movieId')['tag'].apply(lambda x: ' '.join(x.astype(str))).reset_index()
            self.tags_df.columns = ['movieId', 'tag']
            logger.info(f"Loaded tags for {len(self.tags_df)} movies")
            
            # Load ratings for popularity calculation
            logger.info("Loading ratings for popularity calculation...")
            ratings_chunk = pd.read_csv(f"{data_path}/ratings.csv", nrows=200000)
            self.ratings_df = ratings_chunk.groupby('movieId').agg({
                'rating': ['count', 'mean']
            }).round(2)
            self.ratings_df.columns = ['rating_count', 'rating_avg']
            self.ratings_df = self.ratings_df.reset_index()
            logger.info(f"Loaded ratings for {len(self.ratings_df)} movies")
            
        except Exception as e:
            logger.error(f"Error loading MovieLens data: {str(e)}")
            raise
    
    def create_improved_features(self) -> np.ndarray:
        """Tạo features cải tiến với weights cân bằng hơn"""
        logger.info("Creating improved content features...")
        logger.info("Processing movies...")
        
        # Merge data
        movies_with_tags = self.movies_df.merge(self.tags_df, on='movieId', how='left')
        movies_with_ratings = movies_with_tags.merge(self.ratings_df, on='movieId', how='left')
        
        # Fill missing values
        movies_with_ratings['tag'] = movies_with_ratings['tag'].fillna('')
        movies_with_ratings['rating_count'] = movies_with_ratings['rating_count'].fillna(0)
        movies_with_ratings['rating_avg'] = movies_with_ratings['rating_avg'].fillna(0)
        
        logger.info("Creating genres features...")
        # 1. Genres features (GIẢM WEIGHT từ 70% xuống 40%)
        genres_text = movies_with_ratings['genres'].fillna('').astype(str)
        genres_vectorizer = TfidfVectorizer(max_features=100, stop_words='english')
        genres_features = genres_vectorizer.fit_transform(genres_text)
        
        logger.info("Creating tags features...")
        # 2. Tags features (TĂNG WEIGHT từ 20% lên 30%)
        tags_text = movies_with_ratings['tag'].fillna('').astype(str)
        tags_vectorizer = TfidfVectorizer(max_features=200, stop_words='english')
        tags_features = tags_vectorizer.fit_transform(tags_text)
        
        logger.info("Creating title keywords features...")
        # 3. Title keywords features (TĂNG WEIGHT từ 5% lên 15%)
        titles = movies_with_ratings['title'].fillna('').astype(str)
        title_vectorizer = TfidfVectorizer(max_features=100, stop_words='english')
        title_features = title_vectorizer.fit_transform(titles)
        
        logger.info("Creating release year features...")
        # 4. Release year features (GIỮ NGUYÊN 5%)
        # Extract year from title (e.g., "Toy Story (1995)" -> 1995)
        def extract_year(title):
            import re
            match = re.search(r'\((\d{4})\)', str(title))
            return int(match.group(1)) if match else 1995
        
        years = movies_with_ratings['title'].apply(extract_year).values.reshape(-1, 1)
        year_scaler = MinMaxScaler()
        year_features = year_scaler.fit_transform(years)
        
        logger.info("Creating popularity features...")
        # 5. Popularity features (GIỮ NGUYÊN 5%)
        popularity = np.log1p(movies_with_ratings['rating_count'].fillna(0).values).reshape(-1, 1)
        popularity_scaler = MinMaxScaler()
        popularity_features = popularity_scaler.fit_transform(popularity)
        
        logger.info("Creating rating quality features...")
        # 6. Rating quality features (MỚI - 5%)
        rating_quality = movies_with_ratings['rating_avg'].fillna(0).values.reshape(-1, 1)
        rating_scaler = MinMaxScaler()
        rating_features = rating_scaler.fit_transform(rating_quality)
        
        # Combine features with IMPROVED weights
        from scipy.sparse import hstack
        
        combined_features = hstack([
            genres_features * 0.40,      # Giảm từ 70% xuống 40%
            tags_features * 0.30,         # Tăng từ 20% lên 30%
            title_features * 0.15,        # Tăng từ 5% lên 15%
            year_features * 0.05,         # Giữ nguyên 5%
            popularity_features * 0.05,   # Giữ nguyên 5%
            rating_features * 0.05        # Mới: 5%
        ])
        
        logger.info(f"Created improved feature matrix: {combined_features.shape}")
        return combined_features.toarray()
    
    def calculate_improved_similarity(self, features: np.ndarray) -> np.ndarray:
        """Tính similarity với cải tiến"""
        logger.info("Calculating improved cosine similarity matrix...")
        
        # Tính cosine similarity
        similarity_matrix = cosine_similarity(features)
        
        # Áp dụng threshold để tránh similarity = 100%
        # Loại bỏ các similarity quá cao (>0.95) để tăng tính đa dạng
        similarity_matrix = np.where(similarity_matrix > 0.95, 0.95, similarity_matrix)
        
        logger.info(f"Created improved similarity matrix: {similarity_matrix.shape}")
        return similarity_matrix
    
    def save_improved_similarities(self, similarity_matrix: np.ndarray, top_n: int = 20):
        """Lưu similarities với cải tiến"""
        logger.info(f"Saving top {top_n} similar movies to database...")
        
        # Get existing movie IDs from database
        with self.db_engine.connect() as conn:
            result = conn.execute(text("SELECT movieId FROM cine.Movie"))
            existing_movie_ids = set(row[0] for row in result)
        logger.info(f"Found {len(existing_movie_ids)} movies in database")
        
        # Clear existing data
        with self.db_engine.connect() as conn:
            conn.execute(text("DELETE FROM cine.MovieSimilarity"))
            conn.commit()
        logger.info("Cleared existing MovieSimilarity data")
        
        # Get movie IDs from training data
        movie_ids = self.movies_df['movieId'].values
        
        # Filter to only include movies that exist in database
        valid_indices = []
        valid_movie_ids = []
        for i, movie_id in enumerate(movie_ids):
            if movie_id in existing_movie_ids:
                valid_indices.append(i)
                valid_movie_ids.append(movie_id)
        
        logger.info(f"Filtered to {len(valid_movie_ids)} movies that exist in database")
        
        # Extract top similar movies for each valid movie
        similarities_data = []
        seen_pairs = set()
        
        for i, movie_id in zip(valid_indices, valid_movie_ids):
            similar_indices = np.argsort(similarity_matrix[i])[-top_n:][::-1]
            
            for rank, similar_idx in enumerate(similar_indices, 1):
                if i != similar_idx and movie_ids[similar_idx] in existing_movie_ids:
                    pair = (int(movie_id), int(movie_ids[similar_idx]))
                    if pair not in seen_pairs:
                        similarities_data.append({
                            'movieId1': pair[0],
                            'movieId2': pair[1],
                            'similarity': float(similarity_matrix[i][similar_idx])
                        })
                        seen_pairs.add(pair)
        
        logger.info(f"Generated {len(similarities_data)} unique similarity pairs")
        
        # Save to database in batches
        if similarities_data:
            df_similarities = pd.DataFrame(similarities_data)
            
            # Save to database in batches
            batch_size = 1000
            total_batches = len(df_similarities) // batch_size + (1 if len(df_similarities) % batch_size > 0 else 0)
            
            logger.info(f"Saving {len(df_similarities)} similarity records in {total_batches} batches...")
            
            for i in range(0, len(df_similarities), batch_size):
                batch_df = df_similarities.iloc[i:i+batch_size]
                batch_df.to_sql(
                    'MovieSimilarity',
                    self.db_engine,
                    schema='cine',
                    if_exists='append',
                    index=False,
                    method='multi',
                    chunksize=100
                )
                logger.info(f"Saved batch {i//batch_size + 1}/{total_batches} ({len(batch_df)} records)")
            
            logger.info(f"Saved {len(similarities_data)} similarity records")
    
    def train_improved(self, data_path: str, top_n: int = 20):
        """Complete improved training pipeline"""
        try:
            logger.info("Starting Improved Content-based Training...")
            
            # Load data
            self.load_movielens_data(data_path)
            
            # Create improved features
            features = self.create_improved_features()
            
            # Calculate improved similarity
            similarity_matrix = self.calculate_improved_similarity(features)
            
            # Save improved similarities
            self.save_improved_similarities(similarity_matrix, top_n)
            
            logger.info("Improved training completed successfully!")
            
            # Print statistics
            self.print_improved_statistics(similarity_matrix)
            
        except Exception as e:
            logger.error(f"Improved training failed: {e}")
            raise
    
    def print_improved_statistics(self, similarity_matrix: np.ndarray):
        """Print improved statistics"""
        logger.info("=== Improved Training Statistics ===")
        logger.info(f"Total movies processed: {len(self.movies_df)}")
        logger.info(f"Similarity matrix shape: {similarity_matrix.shape}")
        
        # Calculate statistics
        avg_similarity = np.mean(similarity_matrix)
        max_similarity = np.max(similarity_matrix)
        min_similarity = np.min(similarity_matrix)
        
        logger.info(f"Average similarity: {avg_similarity:.4f}")
        logger.info(f"Max similarity: {max_similarity:.4f}")
        logger.info(f"Min similarity: {min_similarity:.4f}")
        
        # Count similarity ranges
        high_similarity = np.sum(similarity_matrix >= 0.9)
        medium_similarity = np.sum((similarity_matrix >= 0.7) & (similarity_matrix < 0.9))
        low_similarity = np.sum(similarity_matrix < 0.7)
        
        logger.info(f"High similarity (≥0.9): {high_similarity} pairs")
        logger.info(f"Medium similarity (0.7-0.9): {medium_similarity} pairs")
        logger.info(f"Low similarity (<0.7): {low_similarity} pairs")

def main():
    """Main training function"""
    print("Improved Content-based Recommendation Training")
    print("=" * 60)
    
    # Database connection
    odbc_str = (
        "DRIVER=ODBC Driver 17 for SQL Server;"
        "SERVER=localhost,1433;"
        "DATABASE=CineBoxDB;"     
        "UID=sa;"
        "PWD=sapassword;"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
    )
    connection_url = URL.create("mssql+pyodbc", query={"odbc_connect": odbc_str})
    db_engine = create_engine(connection_url, fast_executemany=True)
    
    # Path to MovieLens dataset
    DATA_PATH = r"d:\N5\KLTN\ml-32m"
    
    # Initialize improved trainer
    trainer = ImprovedContentTrainer(db_engine)
    
    # Train improved model
    trainer.train_improved(data_path=DATA_PATH, top_n=20)
    
    print("\n" + "=" * 60)
    print("Improved training completed!")
    print("Similarity scores should now be more diverse and realistic.")

if __name__ == "__main__":
    main()
