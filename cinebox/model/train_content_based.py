"""
Content-based Recommendation Training Script
File riêng để training mô hình gợi ý phim liên quan
"""

import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import MinMaxScaler
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
import logging
import re
from scipy.sparse import hstack

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ContentBasedTrainer:
    """
    Content-based Recommendation Trainer
    Chỉ dành cho training, không có serving methods
    """
    
    def __init__(self, db_engine=None):
        """
        Initialize trainer
        
        Args:
            db_engine: SQLAlchemy database engine (optional)
        """
        if db_engine is None:
            # Default connection
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
        
        self.db_engine = db_engine
        self.movies_df = None
        self.tags_df = None
        self.ratings_df = None
        
    def load_movielens_data(self, data_path: str):
        """Load MovieLens dataset"""
        logger.info("Loading MovieLens dataset...")
        
        try:
            # Load movies (sample for performance - only first 10000 movies)
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
    
    def extract_title_keywords(self, title: str) -> str:
        """Extract keywords from movie title"""
        # Remove year in parentheses
        title_clean = re.sub(r'\s*\(\d{4}\)', '', title)
        
        # Remove common words
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by'}
        
        # Split and filter
        words = re.findall(r'\b[a-zA-Z]+\b', title_clean.lower())
        keywords = [word for word in words if word not in stop_words and len(word) > 2]
        
        return ' '.join(keywords)
    
    def create_content_features(self) -> np.ndarray:
        """Create content features matrix"""
        logger.info("Creating content features...")
        
        # Merge all data
        df = self.movies_df.copy()
        
        # Merge with tags
        df = df.merge(self.tags_df, on='movieId', how='left')
        df['tag'] = df['tag'].fillna('')
        
        # Merge with ratings
        df = df.merge(self.ratings_df, on='movieId', how='left')
        df['rating_count'] = df['rating_count'].fillna(0)
        df['rating_avg'] = df['rating_avg'].fillna(0)
        
        # Extract title keywords
        df['title_keywords'] = df['title'].apply(self.extract_title_keywords)
        
        # Extract release year
        df['release_year'] = df['title'].str.extract(r'\((\d{4})\)').astype(float)
        df['release_year'] = df['release_year'].fillna(df['release_year'].median())
        
        logger.info(f"Processing {len(df)} movies...")
        
        # 1. Genres features (70% weight)
        logger.info("Creating genres features...")
        genres_vectorizer = TfidfVectorizer(
            token_pattern=r'[^|]+',  # Split by pipe
            max_features=50,
            binary=True
        )
        genres_matrix = genres_vectorizer.fit_transform(df['genres'].fillna(''))
        
        # 2. Tags features (20% weight)
        logger.info("Creating tags features...")
        tags_vectorizer = TfidfVectorizer(
            max_features=200,
            stop_words='english',
            min_df=5,
            max_df=0.8
        )
        tags_matrix = tags_vectorizer.fit_transform(df['tag'].fillna(''))
        
        # 3. Title keywords features (5% weight)
        logger.info("Creating title keywords features...")
        title_vectorizer = TfidfVectorizer(
            max_features=100,
            stop_words='english',
            min_df=2
        )
        title_matrix = title_vectorizer.fit_transform(df['title_keywords'].fillna(''))
        
        # 4. Release year features (3% weight)
        logger.info("Creating release year features...")
        year_scaler = MinMaxScaler()
        year_normalized = year_scaler.fit_transform(df[['release_year']])
        
        # 5. Popularity features (2% weight)
        logger.info("Creating popularity features...")
        df['log_rating_count'] = np.log1p(df['rating_count'])
        popularity_scaler = MinMaxScaler()
        popularity_normalized = popularity_scaler.fit_transform(df[['log_rating_count']])
        
        # Combine all features with weights
        logger.info("Combining features with weights...")
        
        combined_features = hstack([
            genres_matrix * 0.70,      # 70% weight
            tags_matrix * 0.20,        # 20% weight  
            title_matrix * 0.05,       # 5% weight
            year_normalized * 0.03,    # 3% weight
            popularity_normalized * 0.02  # 2% weight
        ])
        
        logger.info(f"Created feature matrix: {combined_features.shape}")
        return combined_features.toarray()
    
    def calculate_similarity_matrix(self, features: np.ndarray) -> np.ndarray:
        """Calculate cosine similarity matrix"""
        logger.info("Calculating cosine similarity matrix...")
        
        similarity_matrix = cosine_similarity(features)
        np.fill_diagonal(similarity_matrix, 0)  # Remove self-similarity
        
        logger.info(f"Created similarity matrix: {similarity_matrix.shape}")
        return similarity_matrix
    
    def save_to_database(self, similarity_matrix: np.ndarray, top_n: int = 20):
        """Save similarity matrix to database"""
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
        seen_pairs = set()  # Track seen pairs to avoid duplicates
        
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
    
    def train(self, data_path: str, top_n: int = 20):
        """Complete training pipeline"""
        try:
            logger.info("Starting Content-based Training...")
            
            # Load data
            self.load_movielens_data(data_path)
            
            # Create features
            features = self.create_content_features()
            
            # Calculate similarity
            similarity_matrix = self.calculate_similarity_matrix(features)
            
            # Save to database
            self.save_to_database(similarity_matrix, top_n)
            
            logger.info("✅ Training completed successfully!")
            
            # Print statistics
            self.print_training_statistics(similarity_matrix)
            
        except Exception as e:
            logger.error(f"❌ Training failed: {str(e)}")
            raise
    
    def print_training_statistics(self, similarity_matrix: np.ndarray):
        """Print training statistics"""
        logger.info("=== Training Statistics ===")
        logger.info(f"Total movies processed: {len(self.movies_df)}")
        logger.info(f"Similarity matrix shape: {similarity_matrix.shape}")
        logger.info(f"Average similarity: {np.mean(similarity_matrix):.4f}")
        logger.info(f"Max similarity: {np.max(similarity_matrix):.4f}")
        logger.info(f"Min similarity: {np.min(similarity_matrix):.4f}")
        
        # Show examples
        logger.info("\n=== Example Similarities ===")
        movie_ids = self.movies_df['movieId'].values
        titles = self.movies_df['title'].values
        
        if len(movie_ids) > 0:
            first_movie_idx = 0
            top_similar = np.argsort(similarity_matrix[first_movie_idx])[-6:-1][::-1]
            
            logger.info(f"\nTop 5 similar movies to '{titles[first_movie_idx]}':")
            for i, similar_idx in enumerate(top_similar, 1):
                logger.info(f"{i}. {titles[similar_idx]} (similarity: {similarity_matrix[first_movie_idx][similar_idx]:.4f})")


def main():
    """Main training function"""
    print("Content-based Recommendation Training")
    print("=" * 50)
    
    # Path to MovieLens dataset
    DATA_PATH = r"d:\N5\KLTN\ml-32m"
    
    # Create trainer
    trainer = ContentBasedTrainer()
    
    # Train model
    trainer.train(data_path=DATA_PATH, top_n=20)
    
    print("\n" + "=" * 50)
    print("Training completed! You can now use the test script.")
    print("Run: python test_content_based.py")


if __name__ == "__main__":
    main()
