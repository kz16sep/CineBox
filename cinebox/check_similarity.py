"""
Script để kiểm tra xem phim mới có được tính similarity không
"""
import sys
import os
from sqlalchemy import create_engine, text
from datetime import datetime, timedelta

# Thêm đường dẫn
_cinebox_dir = os.path.dirname(os.path.abspath(__file__))
if _cinebox_dir not in sys.path:
    sys.path.insert(0, _cinebox_dir)

# Database connection string (cần cập nhật theo cấu hình của bạn)
# Lấy từ run.py hoặc config
try:
    from app import create_app
    app = create_app()
    with app.app_context():
        db_engine = app.db_engine
except Exception as e:
    print(f"Lỗi khi tạo app: {e}")
    print("Vui lòng cập nhật connection string trong script này")
    sys.exit(1)

def check_recent_movies():
    """Kiểm tra các phim mới được thêm trong 24h qua"""
    print("=" * 60)
    print("KIỂM TRA PHIM MỚI VÀ SIMILARITY")
    print("=" * 60)
    
    with db_engine.connect() as conn:
        # Lấy phim mới trong 24h qua
        recent_movies = conn.execute(text("""
            SELECT TOP 10
                m.movieId,
                m.title,
                m.createdAt,
                COUNT(ms.movieId2) as similarity_count
            FROM cine.Movie m
            LEFT JOIN cine.MovieSimilarity ms ON m.movieId = ms.movieId1
            WHERE m.createdAt >= DATEADD(HOUR, -24, GETDATE())
            GROUP BY m.movieId, m.title, m.createdAt
            ORDER BY m.createdAt DESC
        """)).mappings().all()
        
        if not recent_movies:
            print("\n❌ Không tìm thấy phim mới nào trong 24h qua")
            return
        
        print(f"\n📊 Tìm thấy {len(recent_movies)} phim mới trong 24h qua:\n")
        
        for movie in recent_movies:
            movie_id = movie['movieId']
            title = movie['title']
            created_at = movie['createdAt']
            sim_count = movie['similarity_count'] or 0
            
            print(f"🎬 Phim ID: {movie_id}")
            print(f"   Tiêu đề: {title}")
            print(f"   Ngày tạo: {created_at}")
            print(f"   Số similarity: {sim_count}")
            
            if sim_count > 0:
                print(f"   ✅ Đã có similarity ({sim_count} phim liên quan)")
                
                # Lấy top 5 phim liên quan
                related = conn.execute(text("""
                    SELECT TOP 5
                        m2.movieId,
                        m2.title,
                        ms.similarity
                    FROM cine.MovieSimilarity ms
                    JOIN cine.Movie m2 ON ms.movieId2 = m2.movieId
                    WHERE ms.movieId1 = :movie_id
                    ORDER BY ms.similarity DESC
                """), {"movie_id": movie_id}).mappings().all()
                
                if related:
                    print("   Top 5 phim liên quan:")
                    for i, rel in enumerate(related, 1):
                        print(f"      {i}. {rel['title']} (ID: {rel['movieId']}, similarity: {rel['similarity']:.4f})")
            else:
                print(f"   ⚠️  Chưa có similarity")
            
            print()

def check_all_movies_without_similarity():
    """Kiểm tra các phim chưa có similarity"""
    print("=" * 60)
    print("KIỂM TRA PHIM CHƯA CÓ SIMILARITY")
    print("=" * 60)
    
    with db_engine.connect() as conn:
        movies_without_sim = conn.execute(text("""
            SELECT TOP 20
                m.movieId,
                m.title,
                m.createdAt
            FROM cine.Movie m
            LEFT JOIN cine.MovieSimilarity ms ON m.movieId = ms.movieId1
            WHERE ms.movieId1 IS NULL
            ORDER BY m.createdAt DESC
        """)).mappings().all()
        
        if not movies_without_sim:
            print("\n✅ Tất cả phim đều đã có similarity")
            return
        
        print(f"\n⚠️  Tìm thấy {len(movies_without_sim)} phim chưa có similarity:\n")
        for movie in movies_without_sim:
            print(f"   - ID: {movie['movieId']}, Title: {movie['title']}, Created: {movie['createdAt']}")

def check_similarity_stats():
    """Thống kê tổng quan về similarity"""
    print("=" * 60)
    print("THỐNG KÊ SIMILARITY")
    print("=" * 60)
    
    with db_engine.connect() as conn:
        stats = conn.execute(text("""
            SELECT 
                COUNT(DISTINCT m.movieId) as total_movies,
                COUNT(DISTINCT ms.movieId1) as movies_with_similarity,
                COUNT(ms.movieId1) as total_similarity_pairs,
                AVG(ms.similarity) as avg_similarity,
                MIN(ms.similarity) as min_similarity,
                MAX(ms.similarity) as max_similarity
            FROM cine.Movie m
            LEFT JOIN cine.MovieSimilarity ms ON m.movieId = ms.movieId1
        """)).mappings().first()
        
        print(f"\n📊 Thống kê:")
        print(f"   Tổng số phim: {stats['total_movies']}")
        print(f"   Phim có similarity: {stats['movies_with_similarity']}")
        print(f"   Tổng số cặp similarity: {stats['total_similarity_pairs']}")
        if stats['avg_similarity']:
            print(f"   Similarity trung bình: {stats['avg_similarity']:.4f}")
            print(f"   Similarity min: {stats['min_similarity']:.4f}")
            print(f"   Similarity max: {stats['max_similarity']:.4f}")

if __name__ == "__main__":
    try:
        check_similarity_stats()
        print("\n")
        check_recent_movies()
        print("\n")
        check_all_movies_without_similarity()
    except Exception as e:
        print(f"\n❌ Lỗi: {e}")
        import traceback
        traceback.print_exc()

