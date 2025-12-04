"""
Script đơn giản để kiểm tra phim mới nhất có similarity không
"""
import sys
import os
from sqlalchemy import create_engine, text

# Thêm đường dẫn
_cinebox_dir = os.path.dirname(os.path.abspath(__file__))
if _cinebox_dir not in sys.path:
    sys.path.insert(0, _cinebox_dir)

try:
    from app import create_app
    app = create_app()
    with app.app_context():
        db_engine = app.db_engine
        
        print("=" * 60)
        print("KIỂM TRA PHIM MỚI NHẤT VÀ SIMILARITY")
        print("=" * 60)
        
        with db_engine.connect() as conn:
            # Lấy 5 phim mới nhất
            recent_movies = conn.execute(text("""
                SELECT TOP 5
                    m.movieId,
                    m.title,
                    m.createdAt,
                    (SELECT COUNT(*) FROM cine.MovieSimilarity ms WHERE ms.movieId1 = m.movieId) as similarity_count
                FROM cine.Movie m
                ORDER BY m.createdAt DESC
            """)).mappings().all()
            
            if not recent_movies:
                print("\n❌ Không tìm thấy phim nào")
            else:
                print(f"\n📊 {len(recent_movies)} phim mới nhất:\n")
                
                for movie in recent_movies:
                    movie_id = movie['movieId']
                    title = movie['title']
                    created_at = movie['createdAt']
                    sim_count = movie['similarity_count'] or 0
                    
                    status = "✅ CÓ" if sim_count > 0 else "❌ CHƯA CÓ"
                    print(f"🎬 ID: {movie_id}")
                    print(f"   Tiêu đề: {title}")
                    print(f"   Ngày tạo: {created_at}")
                    print(f"   Similarity: {status} ({sim_count} phim liên quan)")
                    
                    if sim_count > 0:
                        # Lấy top 3 phim liên quan
                        related = conn.execute(text("""
                            SELECT TOP 3
                                m2.title,
                                ms.similarity
                            FROM cine.MovieSimilarity ms
                            JOIN cine.Movie m2 ON ms.movieId2 = m2.movieId
                            WHERE ms.movieId1 = :movie_id
                            ORDER BY ms.similarity DESC
                        """), {"movie_id": movie_id}).mappings().all()
                        
                        if related:
                            print("   Top 3 phim liên quan:")
                            for i, rel in enumerate(related, 1):
                                print(f"      {i}. {rel['title']} (similarity: {rel['similarity']:.4f})")
                    print()
        
except Exception as e:
    print(f"❌ Lỗi: {e}")
    import traceback
    traceback.print_exc()

