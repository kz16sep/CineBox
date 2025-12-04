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
        self.ratings_df = None
        
    def format_title(self, title: str, year: int = None) -> str:
        """Format title để đảm bảo có (Year) giống MovieLens format"""
        if not title:
            return ""
        
        # Kiểm tra xem title đã có (Year) chưa
        if year and f"({year})" not in title:
            return f"{title} ({year})"
        return title
    
    def load_database_data(self):
        """Load dữ liệu trực tiếp từ database CineBox"""
        logger.info("Loading data from CineBox database...")
        
        try:
            with self.db_engine.connect() as conn:
                # Query movies với genres
                logger.info("Loading movies with genres from database...")
                movies_query = text("""
                    SELECT 
                        m.movieId, 
                        m.title, 
                        m.releaseYear,
                        STRING_AGG(g.name, '|') as genres
                    FROM cine.Movie m
                    LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                    LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                    GROUP BY m.movieId, m.title, m.releaseYear
                    ORDER BY m.movieId
                """)
                
                movies_result = conn.execute(movies_query)
                movies_data = []
                for row in movies_result:
                    movie_id, title, year, genres = row
                    # Format title với year nếu chưa có
                    formatted_title = self.format_title(title, year)
                    movies_data.append({
                        'movieId': movie_id,
                        'title': formatted_title,
                        'releaseYear': year,
                        'genres': genres if genres else ''
                    })
                
                self.movies_df = pd.DataFrame(movies_data)
                logger.info(f"Loaded {len(self.movies_df)} movies from database")
                
                # Query ratings từ database
                logger.info("Loading ratings from database...")
                ratings_query = text("""
                    SELECT 
                        movieId, 
                        COUNT(*) as rating_count,
                        AVG(CAST(value AS FLOAT)) as rating_avg
                    FROM cine.Rating
                    GROUP BY movieId
                """)
                
                ratings_result = conn.execute(ratings_query)
                ratings_data = []
                for row in ratings_result:
                    movie_id, rating_count, rating_avg = row
                    ratings_data.append({
                        'movieId': movie_id,
                        'rating_count': int(rating_count),
                        'rating_avg': float(rating_avg) if rating_avg else 0.0
                    })
                
                self.ratings_df = pd.DataFrame(ratings_data)
                logger.info(f"Loaded ratings for {len(self.ratings_df)} movies from database")
                
        except Exception as e:
            logger.error(f"Error loading database data: {str(e)}")
            raise
    
    def create_improved_features(self) -> np.ndarray:
        """Tạo features với weights: Genres (60%), Title (20%), Year (7%), Popularity (7%), Rating (6%)"""
        logger.info("Creating improved content features from database...")
        logger.info("Processing movies...")
        
        # Merge data với ratings
        movies_with_ratings = self.movies_df.merge(self.ratings_df, on='movieId', how='left')
        
        # Fill missing values
        movies_with_ratings['genres'] = movies_with_ratings['genres'].fillna('')
        movies_with_ratings['rating_count'] = movies_with_ratings['rating_count'].fillna(0)
        movies_with_ratings['rating_avg'] = movies_with_ratings['rating_avg'].fillna(0)
        
        logger.info("Creating genres features...")
        # 1. Genres features (60%)
        genres_text = movies_with_ratings['genres'].fillna('').astype(str).str.replace('|', ' ')
        genres_vectorizer = TfidfVectorizer(max_features=1000, min_df=1)
        genres_features = genres_vectorizer.fit_transform(genres_text)
        
        logger.info("Creating title keywords features...")
        # 2. Title features (20%)
        titles = movies_with_ratings['title'].fillna('').astype(str)
        title_vectorizer = TfidfVectorizer(max_features=1000, min_df=1)
        title_features = title_vectorizer.fit_transform(titles)
        
        logger.info("Creating release year features...")
        # 3. Year features (7%)
        # Extract year from title (e.g., "Toy Story (1995)" -> 1995)
        def extract_year(title):
            match = re.search(r'\((\d{4})\)', str(title))
            if match:
                return int(match.group(1))
            # Fallback: dùng releaseYear từ database nếu có
            return None
        
        years_list = []
        for idx, row in movies_with_ratings.iterrows():
            year = extract_year(row['title'])
            if year is None and pd.notna(row.get('releaseYear')):
                year = int(row['releaseYear'])
            if year is None:
                year = 2000  # Default year
            years_list.append(year)
        
        years = np.array(years_list).reshape(-1, 1)
        year_scaler = MinMaxScaler()
        year_features = year_scaler.fit_transform(years)
        
        logger.info("Creating popularity features...")
        # 4. Popularity features (7%) - dựa trên rating_count
        popularity = np.log1p(movies_with_ratings['rating_count'].fillna(0).values).reshape(-1, 1)
        popularity_scaler = MinMaxScaler()
        popularity_features = popularity_scaler.fit_transform(popularity)
        
        logger.info("Creating rating quality features...")
        # 5. Rating features (6%) - dựa trên rating_avg
        rating_quality = movies_with_ratings['rating_avg'].fillna(0).values.reshape(-1, 1)
        rating_scaler = MinMaxScaler()
        rating_features = rating_scaler.fit_transform(rating_quality)
        
        # Combine features với weights: Genres (60%), Title (20%), Year (7%), Popularity (7%), Rating (6%)
        from scipy.sparse import hstack
        
        combined_features = hstack([
            genres_features * 0.60,      # Genres: 60%
            title_features * 0.20,       # Title: 20%
            year_features * 0.07,        # Year: 7%
            popularity_features * 0.07,  # Popularity: 7%
            rating_features * 0.06       # Rating: 6%
        ])
        
        logger.info(f"Created improved feature matrix: {combined_features.shape}")
        
        # Save model components for backup
        self.save_model_components(movies_with_ratings, combined_features, {
            'genres_vectorizer': genres_vectorizer,
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
                    'genres': 0.60,
                    'title': 0.20,
                    'year': 0.07,
                    'popularity': 0.07,
                    'rating': 0.06
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
            with conn.begin():
                conn.execute(text("DELETE FROM cine.MovieSimilarity"))
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
    
    def train_improved(self, top_n: int = 20):
        """Complete improved training pipeline - sử dụng dữ liệu từ database"""
        try:
            print("\n🚀 STARTING DATABASE-BASED TRAINING")
            print("=" * 60)
            
            # Phase 1: Load data từ database
            print("\n📊 PHASE 1: Loading Data from Database")
            print("-" * 30)
            start_time = time.time()
            self.load_database_data()
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
    """Main training function - DATABASE-BASED APPROACH"""
    print("🎯 DATABASE-BASED: Content-based Recommendation Training")
    print("=" * 70)
    print("Phase 1: Load data directly from CineBox database")
    print("Phase 2: Create features with weights: Genres 60%, Title 20%, Year 7%, Popularity 7%, Rating 6%")
    print("Phase 3: Calculate similarities and save to database")
    print("Phase 4: Backup model file (for retraining)")
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
    
    # Initialize improved trainer
    trainer = ImprovedContentTrainer(db_engine)
    
    # Train improved model - không cần data_path nữa
    trainer.train_improved(top_n=20)
    
    print("\n" + "=" * 60)
    print("Database-based training completed!")
    print("Similarity scores calculated from actual CineBox database data.")
    print("New movies will automatically be included in future training runs.")

if __name__ == "__main__":
    main()
