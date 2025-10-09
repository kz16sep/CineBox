#!/usr/bin/env python3
"""
Auto Training Manager
Tự động phát hiện phim mới và quyết định có cần train không
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from content_based_recommender import ContentBasedRecommender
import logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AutoTrainingManager:
    """Quản lý tự động training"""
    
    def __init__(self, db_engine):
        self.db_engine = db_engine
        self.recommender = ContentBasedRecommender(db_engine)
    
    def check_training_needed(self) -> bool:
        """Kiểm tra có cần train không"""
        with self.db_engine.connect() as conn:
            # Đếm phim mới chưa có similarity
            result = conn.execute(text("""
                SELECT COUNT(*) 
                FROM cine.Movie m
                LEFT JOIN cine.MovieSimilarity ms ON m.movieId = ms.movieId1
                WHERE ms.movieId1 IS NULL
            """)).scalar()
            
            new_movies_count = result
            logger.info(f"Found {new_movies_count} new movies without similarity data")
            
            # Nếu có >5 phim mới thì cần train
            return new_movies_count > 5
    
    def get_training_stats(self) -> dict:
        """Lấy thống kê training"""
        with self.db_engine.connect() as conn:
            # Tổng phim
            total_movies = conn.execute(text("SELECT COUNT(*) FROM cine.Movie")).scalar()
            
            # Phim có similarity
            movies_with_similarity = conn.execute(text("""
                SELECT COUNT(DISTINCT movieId1) FROM cine.MovieSimilarity
            """)).scalar()
            
            # Similarity records
            similarity_count = conn.execute(text("SELECT COUNT(*) FROM cine.MovieSimilarity")).scalar()
            
            # Phim mới
            new_movies = conn.execute(text("""
                SELECT COUNT(*) 
                FROM cine.Movie m
                LEFT JOIN cine.MovieSimilarity ms ON m.movieId = ms.movieId1
                WHERE ms.movieId1 IS NULL
            """)).scalar()
            
            return {
                'total_movies': total_movies,
                'movies_with_similarity': movies_with_similarity,
                'similarity_count': similarity_count,
                'new_movies': new_movies,
                'coverage': (movies_with_similarity / total_movies * 100) if total_movies > 0 else 0
            }
    
    def recommend_training_strategy(self) -> str:
        """Đề xuất chiến lược training"""
        stats = self.get_training_stats()
        
        if stats['new_movies'] == 0:
            return "OK - Khong can train - Tat ca phim da co similarity data"
        elif stats['new_movies'] <= 5:
            return "WARNING - Co the train incremental - It phim moi"
        elif stats['new_movies'] <= 20:
            return "INFO - Nen train incremental - So luong phim moi vua phai"
        else:
            return "RUN - Nen train toan bo - Nhieu phim moi"
    
    def auto_train(self):
        """Tự động train dựa trên tình hình"""
        stats = self.get_training_stats()
        
        print("=== AUTO TRAINING MANAGER ===")
        print(f"Total movies: {stats['total_movies']}")
        print(f"Movies with similarity: {stats['movies_with_similarity']}")
        print(f"Similarity records: {stats['similarity_count']}")
        print(f"New movies: {stats['new_movies']}")
        print(f"Coverage: {stats['coverage']:.1f}%")
        print()
        
        strategy = self.recommend_training_strategy()
        print(f"Recommended strategy: {strategy}")
        print()
        
        if stats['new_movies'] > 0:
            if stats['new_movies'] <= 20:
                print("INFO - Running incremental training...")
                # Chạy incremental training
                os.system("python incremental_train.py")
            else:
                print("RUN - Running full training...")
                # Chạy full training
                os.system("python train_content_based.py")
        else:
            print("OK - No training needed!")

def main():
    """Main function"""
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
    
    # Initialize manager
    manager = AutoTrainingManager(db_engine)
    
    # Auto train
    manager.auto_train()

if __name__ == "__main__":
    main()
