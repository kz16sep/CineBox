#!/usr/bin/env python3
"""
Incremental Content-based Training
Chỉ train những phim mới được thêm vào database
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

class IncrementalContentTrainer:
    """
    Incremental Content-based Trainer
    Chỉ train những phim mới được thêm vào database
    """
    
    def __init__(self, db_engine):
        self.db_engine = db_engine
        self.movies_df = None
        self.tags_df = None
        self.ratings_df = None
        
    def get_new_movies(self) -> List[int]:
        """Lấy danh sách phim mới chưa có similarity"""
        with self.db_engine.connect() as conn:
            # Lấy phim chưa có similarity
            result = conn.execute(text("""
                SELECT m.movieId 
                FROM cine.Movie m
                LEFT JOIN cine.MovieSimilarity ms ON m.movieId = ms.movieId1
                WHERE ms.movieId1 IS NULL
                ORDER BY m.movieId
            """)).fetchall()
            
            new_movie_ids = [row[0] for row in result]
            logger.info(f"Found {len(new_movie_ids)} new movies without similarity data")
            return new_movie_ids
    
    def get_existing_movies(self) -> List[int]:
        """Lấy danh sách phim đã có similarity"""
        with self.db_engine.connect() as conn:
            result = conn.execute(text("""
                SELECT DISTINCT movieId1 FROM cine.MovieSimilarity
                UNION
                SELECT DISTINCT movieId2 FROM cine.MovieSimilarity
            """)).fetchall()
            
            existing_movie_ids = [row[0] for row in result]
            logger.info(f"Found {len(existing_movie_ids)} existing movies with similarity data")
            return existing_movie_ids
    
    def load_movie_data(self, movie_ids: List[int]) -> pd.DataFrame:
        """Load dữ liệu phim từ database"""
        with self.db_engine.connect() as conn:
            # Load movies
            movie_ids_str = ','.join(map(str, movie_ids))
            query = f"""
                SELECT m.movieId, m.title, m.releaseYear, m.overview, m.viewCount,
                       STRING_AGG(g.name, '|') as genres
                FROM cine.Movie m
                LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                WHERE m.movieId IN ({movie_ids_str})
                GROUP BY m.movieId, m.title, m.releaseYear, m.overview, m.viewCount
            """
            
            movies_df = pd.read_sql(text(query), conn)
            logger.info(f"Loaded {len(movies_df)} movies from database")
            return movies_df
    
    def create_features_for_movies(self, movies_df: pd.DataFrame) -> np.ndarray:
        """Tạo features cho danh sách phim"""
        logger.info("Creating features for movies...")
        
        # 1. Genres features (70% weight)
        genres_text = movies_df['genres'].fillna('').astype(str)
        genres_vectorizer = TfidfVectorizer(max_features=100, stop_words='english')
        genres_features = genres_vectorizer.fit_transform(genres_text)
        
        # 2. Title keywords features (5% weight)
        titles = movies_df['title'].fillna('').astype(str)
        title_vectorizer = TfidfVectorizer(max_features=50, stop_words='english')
        title_features = title_vectorizer.fit_transform(titles)
        
        # 3. Release year features (3% weight)
        years = movies_df['releaseYear'].fillna(1995).values.reshape(-1, 1)
        year_scaler = MinMaxScaler()
        year_features = year_scaler.fit_transform(years)
        
        # 4. Popularity features (2% weight)
        popularity = np.log1p(movies_df['viewCount'].fillna(0).values).reshape(-1, 1)
        popularity_scaler = MinMaxScaler()
        popularity_features = popularity_scaler.fit_transform(popularity)
        
        # Combine features with weights
        from scipy.sparse import hstack
        
        combined_features = hstack([
            genres_features * 0.7,
            title_features * 0.05,
            year_features * 0.03,
            popularity_features * 0.02
        ])
        
        logger.info(f"Created feature matrix: {combined_features.shape}")
        return combined_features.toarray()
    
    def calculate_similarity_for_new_movies(self, new_movie_ids: List[int], 
                                          existing_movie_ids: List[int], 
                                          top_n: int = 20) -> List[Dict]:
        """Tính similarity cho phim mới với phim đã có"""
        logger.info(f"Calculating similarity for {len(new_movie_ids)} new movies")
        
        # Load data for all movies (new + existing)
        all_movie_ids = new_movie_ids + existing_movie_ids
        movies_df = self.load_movie_data(all_movie_ids)
        
        # Create features
        features = self.create_features_for_movies(movies_df)
        
        # Calculate similarity matrix
        similarity_matrix = cosine_similarity(features)
        
        # Extract similarities for new movies only
        similarities_data = []
        new_movie_indices = [i for i, movie_id in enumerate(all_movie_ids) if movie_id in new_movie_ids]
        
        for i, movie_id in zip(new_movie_indices, new_movie_ids):
            # Get top similar movies
            similar_indices = np.argsort(similarity_matrix[i])[-top_n:][::-1]
            
            for similar_idx in similar_indices:
                similar_movie_id = all_movie_ids[similar_idx]
                if similar_movie_id != movie_id and similar_movie_id in existing_movie_ids:
                    similarities_data.append({
                        'movieId1': int(movie_id),
                        'movieId2': int(similar_movie_id),
                        'similarity': float(similarity_matrix[i][similar_idx])
                    })
        
        logger.info(f"Generated {len(similarities_data)} similarity pairs for new movies")
        return similarities_data
    
    def save_similarities(self, similarities_data: List[Dict]):
        """Lưu similarities vào database"""
        if not similarities_data:
            logger.info("No similarities to save")
            return
        
        logger.info(f"Saving {len(similarities_data)} similarity records...")
        
        with self.db_engine.connect() as conn:
            for similarity in similarities_data:
                try:
                    conn.execute(
                        text("""
                            INSERT INTO cine.MovieSimilarity (movieId1, movieId2, similarity, createdAt)
                            VALUES (?, ?, ?, SYSUTCDATETIME())
                        """),
                        [similarity['movieId1'], similarity['movieId2'], similarity['similarity']]
                    )
                except Exception as e:
                    logger.warning(f"Failed to insert similarity {similarity}: {e}")
            
            conn.commit()
            logger.info("Similarities saved successfully")
    
    def train_incremental(self, top_n: int = 20):
        """Train incremental cho phim mới"""
        try:
            logger.info("Starting incremental training...")
            
            # 1. Lấy phim mới
            new_movie_ids = self.get_new_movies()
            if not new_movie_ids:
                logger.info("No new movies to train")
                return
            
            # 2. Lấy phim đã có similarity
            existing_movie_ids = self.get_existing_movies()
            if not existing_movie_ids:
                logger.info("No existing movies found. Please run full training first.")
                return
            
            # 3. Tính similarity cho phim mới
            similarities_data = self.calculate_similarity_for_new_movies(
                new_movie_ids, existing_movie_ids, top_n
            )
            
            # 4. Lưu vào database
            self.save_similarities(similarities_data)
            
            logger.info("Incremental training completed successfully!")
            
        except Exception as e:
            logger.error(f"Incremental training failed: {e}")
            raise

def main():
    """Main function"""
    print("Incremental Content-based Training")
    print("=" * 50)
    
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
    
    # Initialize trainer
    trainer = IncrementalContentTrainer(db_engine)
    
    # Run incremental training
    trainer.train_incremental(top_n=20)
    
    print("\n" + "=" * 50)
    print("Incremental training completed!")
    print("New movies now have similarity data.")

if __name__ == "__main__":
    main()
