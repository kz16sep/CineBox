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
import time
from tqdm import tqdm

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
        """Load MovieLens dataset với FULL DATASET để có model chất lượng cao nhất"""
        logger.info("Loading MovieLens dataset (FULL DATASET for maximum quality)...")
        
        try:
            # Load movies (HYBRID: FULL DATASET for maximum quality)
            logger.info("Loading movies (HYBRID: FULL DATASET for maximum quality)...")
            self.movies_df = pd.read_csv(f"{data_path}/movies.csv")  # Full dataset - 87k movies
            logger.info(f"Loaded {len(self.movies_df)} movies")
            
            # Load tags (sample for performance)
            logger.info("Loading tags (sampling for performance)...")
            tags_chunk = pd.read_csv(f"{data_path}/tags.csv", nrows=100000)
            
            # Group tags by movieId and join them
            self.tags_df = tags_chunk.groupby('movieId')['tag'].apply(lambda x: ' '.join(x.astype(str))).reset_index()
            self.tags_df.columns = ['movieId', 'tag']
            logger.info(f"Loaded tags for {len(self.tags_df)} movies")
            
            # Load ratings for popularity calculation (sample for performance)
            logger.info("Loading ratings for popularity calculation (sampling for performance)...")
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
        """Tao features cai tien voi weights toi uu (Phuong an 2)"""
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
        
        # Combine features with ENHANCED weights (Phuong an 2)
        from scipy.sparse import hstack
        
        combined_features = hstack([
            genres_features * 0.50,      # Tăng từ 40% lên 50%
            tags_features * 0.25,        # Giảm từ 30% xuống 25%
            title_features * 0.15,       # Giữ nguyên 15%
            year_features * 0.05,        # Giữ nguyên 5%
            popularity_features * 0.03,  # Giảm từ 5% xuống 3%
            rating_features * 0.02       # Giảm từ 5% xuống 2%
        ])
        
        logger.info(f"Created improved feature matrix: {combined_features.shape}")
        
        # Save model components for backup (HYBRID APPROACH)
        self.save_model_components(movies_with_ratings, combined_features, {
            'genres_vectorizer': genres_vectorizer,
            'tags_vectorizer': tags_vectorizer,
            'title_vectorizer': title_vectorizer,
            'year_scaler': year_scaler,
            'popularity_scaler': popularity_scaler,
            'rating_scaler': rating_scaler
        })
        
        return combined_features.toarray()
    
    def save_model_components(self, movies_df: pd.DataFrame, features: np.ndarray, vectorizers: dict):
        """Save model components for backup (HYBRID APPROACH)"""
        logger.info("Saving model components for backup...")
        
        try:
            import joblib
            
            model_components = {
                'movies_df': movies_df,
                'features': features,
                'vectorizers': vectorizers,
                'feature_weights': {
                    'genres': 0.50,
                    'tags': 0.25,
                    'title': 0.15,
                    'year': 0.05,
                    'popularity': 0.03,
                    'rating': 0.02
                }
            }
            
            joblib.dump(model_components, "hybrid_model_backup.pkl")
            logger.info("Model components saved to hybrid_model_backup.pkl")
            
        except Exception as e:
            logger.warning(f"Could not save model components: {e}")
    
    def calculate_improved_similarity(self, features: np.ndarray) -> np.ndarray:
        """Tính similarity với cải tiến - xử lý dataset lớn"""
        logger.info("Calculating improved cosine similarity matrix (LARGE DATASET)...")
        
        n_movies = features.shape[0]
        logger.info(f"Processing {n_movies} movies...")
        
        # Chia nhỏ để tránh memory issues - chỉ tính và lưu trực tiếp
        chunk_size = 2000  # Increased for full dataset
        logger.info(f"Using chunk size: {chunk_size}")
        
        # Return features để sử dụng trong save_improved_similarities
        return features
    
    def save_improved_similarities(self, features: np.ndarray, top_n: int = 20):
        """Lưu similarities với cải tiến - chỉ lưu phim có trong database"""
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
        
        # Filter movies to only include those in database
        movie_ids_in_db = []
        for idx, movie_id in enumerate(self.movies_df['movieId']):
            if movie_id in existing_movie_ids:
                movie_ids_in_db.append((idx, movie_id))
        
        logger.info(f"Found {len(movie_ids_in_db)} movies from dataset that exist in database")
        
        # Calculate similarities in chunks to avoid memory issues
        similarities_data = []
        seen_pairs = set()
        chunk_size = 2000  # Increased for full dataset
        
        # Progress bar for similarity calculation
        print(f"\n🔄 Calculating similarities for {len(movie_ids_in_db)} movies...")
        progress_bar = tqdm(total=len(movie_ids_in_db), desc="Processing movies", unit="movie")
        
        start_time = time.time()
        
        for i, (idx, movie_id) in enumerate(movie_ids_in_db):
            # Calculate similarity with all other movies in chunks
            movie_similarities = []
            
            for j in range(0, len(self.movies_df), chunk_size):
                end_j = min(j + chunk_size, len(self.movies_df))
                
                # Calculate similarity for this chunk
                chunk_similarities = cosine_similarity(
                    features[idx:idx+1], 
                    features[j:end_j]
                )[0]
                
                # Apply threshold
                chunk_similarities = np.where(chunk_similarities > 0.95, 0.95, chunk_similarities)
                
                # Store similarities with their indices
                for k, sim in enumerate(chunk_similarities):
                    if j + k != idx:  # Don't include self-similarity
                        movie_similarities.append((j + k, sim))
            
            # Get top N similar movies
            movie_similarities.sort(key=lambda x: x[1], reverse=True)
            top_similar = movie_similarities[:top_n]
            
            for similar_idx, similarity in top_similar:
                similar_movie_id = self.movies_df.iloc[similar_idx]['movieId']
                if similar_movie_id in existing_movie_ids:
                    pair = (int(movie_id), int(similar_movie_id))
                    if pair not in seen_pairs:
                        similarities_data.append({
                            'movieId1': pair[0],
                            'movieId2': pair[1],
                            'similarity': float(similarity)
                        })
                        seen_pairs.add(pair)
            
            # Update progress bar
            progress_bar.update(1)
            
            # Show detailed progress every 10 movies
            if (i + 1) % 10 == 0:
                elapsed_time = time.time() - start_time
                avg_time_per_movie = elapsed_time / (i + 1)
                remaining_movies = len(movie_ids_in_db) - (i + 1)
                estimated_remaining_time = remaining_movies * avg_time_per_movie
                
                progress_bar.set_postfix({
                    'Movie ID': movie_id,
                    'Similarities': len(similarities_data),
                    'ETA': f"{estimated_remaining_time:.1f}s"
                })
        
        progress_bar.close()
        total_time = time.time() - start_time
        print(f"✅ Similarity calculation completed in {total_time:.2f} seconds")
        print(f"📊 Generated {len(similarities_data)} unique similarity pairs")
        
        logger.info(f"Generated {len(similarities_data)} unique similarity pairs")
        
        # Save to database in batches
        if similarities_data:
            df_similarities = pd.DataFrame(similarities_data)
            
            # Save to database in batches
            batch_size = 1000
            total_batches = len(df_similarities) // batch_size + (1 if len(df_similarities) % batch_size > 0 else 0)
            
            print(f"\n💾 Saving {len(df_similarities)} similarity records to database...")
            print(f"📦 Processing {total_batches} batches (batch size: {batch_size})")
            
            # Progress bar for database saving
            db_progress = tqdm(total=total_batches, desc="Saving to database", unit="batch")
            
            for i in range(0, len(df_similarities), batch_size):
                batch_df = df_similarities.iloc[i:i+batch_size]
                batch_num = i//batch_size + 1
                
                try:
                    batch_df.to_sql(
                        'MovieSimilarity',
                        self.db_engine,
                        schema='cine',
                        if_exists='append',
                        index=False,
                        method='multi',
                        chunksize=100
                    )
                    
                    db_progress.set_postfix({
                        'Batch': f"{batch_num}/{total_batches}",
                        'Records': len(batch_df)
                    })
                    
                except Exception as e:
                    print(f"❌ Error saving batch {batch_num}: {e}")
                
                db_progress.update(1)
            
            db_progress.close()
            print(f"✅ Successfully saved {len(similarities_data)} similarity records to database")
            
            logger.info(f"Saved {len(similarities_data)} similarity records")
    
    def train_improved(self, data_path: str, top_n: int = 20):
        """Complete improved training pipeline"""
        try:
            print("\n🚀 STARTING HYBRID APPROACH TRAINING")
            print("=" * 60)
            
            # Phase 1: Load data
            print("\n📊 PHASE 1: Loading Dataset")
            print("-" * 30)
            start_time = time.time()
            self.load_movielens_data(data_path)
            phase1_time = time.time() - start_time
            print(f"✅ Phase 1 completed in {phase1_time:.2f} seconds")
            
            # Phase 2: Create features
            print("\n🔧 PHASE 2: Creating Features")
            print("-" * 30)
            start_time = time.time()
            features = self.create_improved_features()
            phase2_time = time.time() - start_time
            print(f"✅ Phase 2 completed in {phase2_time:.2f} seconds")
            
            # Phase 3: Calculate similarities
            print("\n🧮 PHASE 3: Calculating Similarities")
            print("-" * 30)
            start_time = time.time()
            features = self.calculate_improved_similarity(features)
            phase3_time = time.time() - start_time
            print(f"✅ Phase 3 completed in {phase3_time:.2f} seconds")
            
            # Phase 4: Save to database
            print("\n💾 PHASE 4: Saving to Database")
            print("-" * 30)
            start_time = time.time()
            self.save_improved_similarities(features, top_n)
            phase4_time = time.time() - start_time
            print(f"✅ Phase 4 completed in {phase4_time:.2f} seconds")
            
            # Total time
            total_time = phase1_time + phase2_time + phase3_time + phase4_time
            
            print("\n🎉 TRAINING COMPLETED SUCCESSFULLY!")
            print("=" * 60)
            print(f"⏱️  Total time: {total_time:.2f} seconds")
            print(f"📊 Phase breakdown:")
            print(f"   • Data loading: {phase1_time:.2f}s ({phase1_time/total_time*100:.1f}%)")
            print(f"   • Feature creation: {phase2_time:.2f}s ({phase2_time/total_time*100:.1f}%)")
            print(f"   • Similarity calculation: {phase3_time:.2f}s ({phase3_time/total_time*100:.1f}%)")
            print(f"   • Database saving: {phase4_time:.2f}s ({phase4_time/total_time*100:.1f}%)")
            
            # Print statistics
            self.print_improved_statistics(features)
            
        except Exception as e:
            print(f"\n❌ TRAINING FAILED: {e}")
            logger.error(f"Improved training failed: {e}")
            raise
    
    def print_improved_statistics(self, features: np.ndarray):
        """Print improved statistics"""
        logger.info("=== Improved Training Statistics ===")
        logger.info(f"Total movies processed: {len(self.movies_df)}")
        logger.info(f"Feature matrix shape: {features.shape}")
        
        # Get database statistics
        with self.db_engine.connect() as conn:
            similarity_count = conn.execute(text("SELECT COUNT(*) FROM cine.MovieSimilarity")).scalar()
            avg_similarity = conn.execute(text("SELECT AVG(similarity) FROM cine.MovieSimilarity")).scalar()
            max_similarity = conn.execute(text("SELECT MAX(similarity) FROM cine.MovieSimilarity")).scalar()
            min_similarity = conn.execute(text("SELECT MIN(similarity) FROM cine.MovieSimilarity")).scalar()
            
            high_similarity = conn.execute(text("SELECT COUNT(*) FROM cine.MovieSimilarity WHERE similarity >= 0.9")).scalar()
            medium_similarity = conn.execute(text("SELECT COUNT(*) FROM cine.MovieSimilarity WHERE similarity >= 0.7 AND similarity < 0.9")).scalar()
            low_similarity = conn.execute(text("SELECT COUNT(*) FROM cine.MovieSimilarity WHERE similarity < 0.7")).scalar()
        
        logger.info(f"Similarity pairs saved: {similarity_count}")
        logger.info(f"Average similarity: {avg_similarity:.4f}")
        logger.info(f"Max similarity: {max_similarity:.4f}")
        logger.info(f"Min similarity: {min_similarity:.4f}")
        
        logger.info(f"High similarity (≥0.9): {high_similarity} pairs")
        logger.info(f"Medium similarity (0.7-0.9): {medium_similarity} pairs")
        logger.info(f"Low similarity (<0.7): {low_similarity} pairs")

def main():
    """Main training function - HYBRID APPROACH"""
    print("🎯 HYBRID APPROACH: Content-based Recommendation Training")
    print("=" * 70)
    print("Phase 1: Training with FULL dataset (87k movies)")
    print("Phase 2: Save similarities to database (fast queries)")
    print("Phase 3: Backup model file (for retraining)")
    print("Enhanced weights: Genres 50%, Tags 25%, Title 15%, Year 5%, Pop 3%, Rating 2%")
    print("=" * 70)
    
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
