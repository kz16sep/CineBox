from flask import Blueprint, render_template, request, redirect, url_for, current_app, session, jsonify, flash
from sqlalchemy import text
from functools import wraps
import sys
import os
import time
import uuid
import re
import random
from werkzeug.utils import secure_filename
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from recommenders.content_based import ContentBasedRecommender
from recommenders.enhanced_cf import EnhancedCFRecommender
# Import helpers để tránh code duplication
from .movie_query_helpers import (
    get_movie_rating_stats, 
    get_movie_genres,
    get_movies_genres,  # Batch query genres
    get_movie_interaction_stats,
    get_movies_interaction_stats  # Batch query để tránh N+1
)
from .recommendation_helpers import (
    calculate_user_interaction_score,
    sort_recommendations,
    format_recommendation,
    hybrid_recommendations
)
from .sql_helpers import (
    validate_limit,
    safe_top_clause
)
# Global recommender instances
content_recommender = None
enhanced_cf_recommender = None

# Trending movies cache (1 hour - tăng lên để giảm queries)
trending_cache = {
    'data': None,
    'timestamp': None,
    'ttl': 3600  # 1 hour in seconds (trending movies không cần update quá thường xuyên)
}

# Latest movies cache (5 minutes)
latest_movies_cache = {
    'data': None,
    'timestamp': None,
    'ttl': 300  # 5 minutes in seconds
}

# Carousel movies cache (5 minutes)
carousel_movies_cache = {
    'data': None,
    'timestamp': None,
    'ttl': 300  # 5 minutes in seconds
}

# --- CF retrain dirty-flag helpers ---
def set_cf_dirty(db_engine=None):
    try:
        if db_engine is None:
            db_engine = current_app.db_engine
        # Sử dụng begin() để tự động quản lý transaction
        with db_engine.begin() as conn:
            conn.execute(text("""
                IF OBJECT_ID('cine.AppState','U') IS NULL
                BEGIN
                  CREATE TABLE cine.AppState ( [key] NVARCHAR(50) PRIMARY KEY, [value] NVARCHAR(255) NULL )
                END;
                MERGE cine.AppState AS t
                USING (SELECT 'cf_dirty' AS [key], 'true' AS [value]) AS s
                ON t.[key] = s.[key]
                WHEN MATCHED THEN UPDATE SET [value] = 'true'
                WHEN NOT MATCHED THEN INSERT ([key],[value]) VALUES (s.[key], s.[value]);
            """))
            # begin() tự động commit khi exit context thành công
    except Exception as e:
        print(f"Error setting cf_dirty: {e}")


def get_cf_state(db_engine=None):
    """Lấy trạng thái CF model từ AppState table"""
    try:
        if db_engine is None:
            db_engine = current_app.db_engine
        
        # Đảm bảo table tồn tại (sử dụng begin() cho DDL)
        with db_engine.begin() as conn:
            conn.execute(text("""
                IF OBJECT_ID('cine.AppState','U') IS NULL
                BEGIN
                  CREATE TABLE cine.AppState ( [key] NVARCHAR(50) PRIMARY KEY, [value] NVARCHAR(255) NULL )
                END;
            """))
        
        # Lấy các giá trị từ AppState (read-only, sử dụng connect())
        with db_engine.connect() as conn:
            result = conn.execute(text("""
                SELECT [key], [value] 
                FROM cine.AppState 
                WHERE [key] IN ('cf_dirty', 'cf_last_retrain')
            """)).mappings().all()
            
            # Chuyển đổi thành dict
            state = {}
            for row in result:
                state[row['key']] = row['value']
            
            return state
    except Exception as e:
        print(f"Error getting cf_state: {e}")
        return {}


def clear_cf_dirty_and_set_last(timestamp, db_engine=None):
    """Xóa flag cf_dirty và cập nhật cf_last_retrain"""
    try:
        if db_engine is None:
            db_engine = current_app.db_engine
        
        with db_engine.begin() as conn:
            # Đảm bảo table tồn tại
            conn.execute(text("""
                IF OBJECT_ID('cine.AppState','U') IS NULL
                BEGIN
                  CREATE TABLE cine.AppState ( [key] NVARCHAR(50) PRIMARY KEY, [value] NVARCHAR(255) NULL )
                END;
            """))
            
            # Cập nhật cf_dirty = 'false' và cf_last_retrain = timestamp
            conn.execute(text("""
                MERGE cine.AppState AS t
                USING (SELECT 'cf_dirty' AS [key], 'false' AS [value]) AS s
                ON t.[key] = s.[key]
                WHEN MATCHED THEN UPDATE SET [value] = 'false'
                WHEN NOT MATCHED THEN INSERT ([key],[value]) VALUES (s.[key], s.[value]);
            """))
            
            conn.execute(text("""
                MERGE cine.AppState AS t
                USING (SELECT 'cf_last_retrain' AS [key], :timestamp AS [value]) AS s
                ON t.[key] = s.[key]
                WHEN MATCHED THEN UPDATE SET [value] = :timestamp
                WHEN NOT MATCHED THEN INSERT ([key],[value]) VALUES (s.[key], s.[value]);
            """), {"timestamp": timestamp})
            
            # begin() tự động commit khi exit context thành công
    except Exception as e:
        print(f"Error clearing cf_dirty and setting last retrain: {e}")


def fetch_rating_stats_for_movies(movie_ids, db_engine=None):
    """Fetch avgRating and ratingCount for a list of movie IDs uniformly.

    Returns a dict: movieId -> {"avgRating": float, "ratingCount": int}
    
    Note: This function now uses the helper module to avoid code duplication.
    """
    return get_movie_rating_stats(movie_ids, db_engine)

def calculate_user_based_score(user_id, movie_id, db_engine=None):
    """Tính điểm gợi ý dựa trên tất cả tương tác của user với phim
    
    Note: This function now uses the helper module to avoid code duplication.
    """
    try:
        if db_engine is None:
            db_engine = current_app.db_engine
            
        with db_engine.connect() as conn:
            # Lấy tất cả thông tin tương tác của user với phim này
            interaction_result = conn.execute(text("""
                SELECT 
                    r.value as user_rating,
                    CASE WHEN f.userId IS NOT NULL THEN 1 ELSE 0 END as is_favorite,
                    CASE WHEN w.userId IS NOT NULL THEN 1 ELSE 0 END as is_watchlist,
                    CASE WHEN vh.userId IS NOT NULL THEN 1 ELSE 0 END as has_viewed,
                    CASE WHEN c.userId IS NOT NULL THEN 1 ELSE 0 END as has_commented,
                    COUNT(DISTINCT r2.userId) as total_ratings,
                    AVG(CAST(r2.value AS FLOAT)) as avg_rating
                FROM cine.Movie m
                LEFT JOIN cine.Rating r ON m.movieId = r.movieId AND r.userId = :user_id
                LEFT JOIN cine.Favorite f ON m.movieId = f.movieId AND f.userId = :user_id
                LEFT JOIN cine.Watchlist w ON m.movieId = w.movieId AND w.userId = :user_id
                LEFT JOIN cine.ViewHistory vh ON m.movieId = vh.movieId AND vh.userId = :user_id
                LEFT JOIN cine.Comment c ON m.movieId = c.movieId AND c.userId = :user_id
                LEFT JOIN cine.Rating r2 ON m.movieId = r2.movieId
                WHERE m.movieId = :movie_id
                GROUP BY m.movieId, r.value, f.userId, w.userId, vh.userId, c.userId
            """), {"user_id": user_id, "movie_id": movie_id}).fetchone()
            
            if not interaction_result:
                return 0.5  # Fallback score
            
            # Sử dụng helper function để tính điểm
            return calculate_user_interaction_score(
                user_rating=interaction_result[0] or 0,
                is_favorite=bool(interaction_result[1]),
                is_watchlist=bool(interaction_result[2]),
                has_viewed=bool(interaction_result[3]),
                has_commented=bool(interaction_result[4]),
                total_ratings=interaction_result[5] or 0,
                avg_rating=interaction_result[6] or 0
            )
            
    except Exception as e:
        print(f"Error calculating user based score: {e}")
        return 0.5  # Fallback score

def create_rating_based_recommendations(user_id, movies, db_engine=None):
    """Tạo recommendations dựa trên rating thực tế của user
    
    Note: Sử dụng batch queries để tránh N+1 query problem.
    """
    try:
        if not movies:
            return []
        
        if db_engine is None:
            db_engine = current_app.db_engine
        
        # Batch query: Lấy interaction stats cho tất cả movies cùng lúc
        movie_ids = [movie["id"] for movie in movies]
        all_interaction_stats = get_movies_interaction_stats(movie_ids, db_engine)
        
        recommendations = []
        for movie in movies:
            movie_id = movie["id"]
            score = calculate_user_based_score(user_id, movie_id, db_engine)
            
            # Lấy stats từ batch query result
            stats = all_interaction_stats.get(movie_id, {})
            viewHistoryCount = stats.get("viewHistoryCount", 0)
            watchlistCount = stats.get("watchlistCount", 0)
            favoriteCount = stats.get("favoriteCount", 0)
            commentCount = stats.get("commentCount", 0)
            
            recommendations.append({
                "id": movie["id"],
                "title": movie["title"],
                "poster": movie["poster"],
                "year": movie.get("year"),
                "country": movie.get("country"),
                "score": score,
                "genres": movie.get("genres", ""),
                "avgRating": movie.get("avgRating", 0),
                "ratingCount": movie.get("ratingCount", 0),
                "viewHistoryCount": viewHistoryCount,
                "watchlistCount": watchlistCount,
                "favoriteCount": favoriteCount,
                "commentCount": commentCount,
                "algo": "rating_based",
                "reason": "User rating based recommendation"
            })
        
        # Sắp xếp theo điểm giảm dần, sau đó theo rating giảm dần (sử dụng helper)
        recommendations = sort_recommendations(
            recommendations,
            sort_keys=['score', 'avgRating', 'ratingCount']
        )
        return recommendations
        
    except Exception as e:
        print(f"Error creating rating based recommendations: {e}")
        return []

def init_recommenders():
    """Initialize recommender instances - chỉ load Enhanced CF model"""
    global content_recommender, enhanced_cf_recommender
    try:
        content_recommender = ContentBasedRecommender(current_app.db_engine)
        enhanced_cf_recommender = EnhancedCFRecommender(current_app.db_engine)
        
        # Chỉ load Enhanced CF model
        print("Loading Enhanced CF model...")
        if enhanced_cf_recommender.is_model_loaded():
            print("Enhanced CF model loaded successfully")
        else:
            print("Enhanced CF model not found or failed to load")
        
        print("Recommenders initialized successfully")
    except Exception as e:
        print(f"Error initializing recommenders: {e}")


main_bp = Blueprint("main", __name__)

# Decorator kiểm tra quyền admin
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            flash("Vui lòng đăng nhập để truy cập trang này.", "error")
            return redirect(url_for("main.login"))
        
        if session.get("role") != "Admin":
            flash("Bạn không có quyền truy cập trang này.", "error")
            return redirect(url_for("main.home"))
        
        return f(*args, **kwargs)
    return decorated_function

# Decorator kiểm tra đăng nhập
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            flash("Vui lòng đăng nhập để truy cập trang này.", "error")
            return redirect(url_for("main.login"))
        return f(*args, **kwargs)
    return decorated_function


def get_poster_or_dummy(poster_url, title):
    """Trả về poster URL hoặc dummy image nếu không có"""
    if poster_url and poster_url != "1" and poster_url.strip():
        return poster_url
    else:
        # Tạo dummy image với title
        safe_title = title[:20].replace(' ', '+').replace('&', 'and')
        return f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={safe_title}"

def get_cold_start_recommendations(user_id, conn):
    """
    Tạo cold start recommendations cho user mới
    Sử dụng các chiến lược:
    1. Phim phổ biến nhất (trending)
    2. Phim mới nhất
    3. Phim có rating cao nhất
    4. Phim theo thể loại phổ biến
    """
    try:
        # Lấy thông tin user để personalization
        user_info = conn.execute(text("""
            SELECT firstName, lastName, favoriteActors, favoriteDirectors, age, gender
            FROM cine.[User] WHERE userId = :user_id
        """), {"user_id": user_id}).mappings().first()
        
        recommendations = []
        
        # 1. Phim trending (được xem nhiều nhất) - loại bỏ phim đã xem
        trending_movies = conn.execute(text("""
            SELECT TOP 4
                m.movieId, m.title, m.posterUrl, m.releaseYear, m.country,
                m.viewCount, AVG(CAST(r.value AS FLOAT)) AS avgRating,
                COUNT(r.movieId) AS ratingCount,
                STUFF((
                    SELECT TOP 10 ', ' + g2.name
                    FROM cine.MovieGenre mg2
                    JOIN cine.Genre g2 ON mg2.genreId = g2.genreId
                    WHERE mg2.movieId = m.movieId
                    ORDER BY g2.name
                    FOR XML PATH(''), TYPE
                ).value('.', 'NVARCHAR(MAX)'), 1, 2, '') as genres
            FROM cine.Movie m
            LEFT JOIN cine.Rating r ON m.movieId = r.movieId
            WHERE m.viewCount > 0
                AND NOT EXISTS (
                    SELECT 1 FROM cine.ViewHistory vh 
                    WHERE vh.movieId = m.movieId AND vh.userId = :user_id
                )
            GROUP BY m.movieId, m.title, m.posterUrl, m.releaseYear, m.country, m.viewCount
            ORDER BY m.viewCount DESC, avgRating DESC
        """), {"user_id": user_id}).mappings().all()
        
        for movie in trending_movies:
            recommendations.append({
                "id": movie["movieId"],
                "title": movie["title"],
                "poster": get_poster_or_dummy(movie.get("posterUrl"), movie["title"]),
                "releaseYear": movie["releaseYear"],
                "country": movie["country"],
                "score": 0.9,  # High score for trending
                "rank": len(recommendations) + 1,
                "avgRating": round(float(movie["avgRating"]), 2) if movie["avgRating"] else 0.0,
                "ratingCount": movie["ratingCount"],
                "genres": movie["genres"] or "",
                "source": "trending"
            })
        
        # 2. Phim mới nhất - loại bỏ phim đã xem
        latest_movies = conn.execute(text("""
            SELECT TOP 4
                m.movieId, m.title, m.posterUrl, m.releaseYear, m.country,
                AVG(CAST(r.value AS FLOAT)) AS avgRating,
                COUNT(r.movieId) AS ratingCount,
                STUFF((
                    SELECT TOP 10 ', ' + g2.name
                    FROM cine.MovieGenre mg2
                    JOIN cine.Genre g2 ON mg2.genreId = g2.genreId
                    WHERE mg2.movieId = m.movieId
                    ORDER BY g2.name
                    FOR XML PATH(''), TYPE
                ).value('.', 'NVARCHAR(MAX)'), 1, 2, '') as genres
            FROM cine.Movie m
            LEFT JOIN cine.Rating r ON m.movieId = r.movieId
            WHERE m.releaseYear >= YEAR(GETDATE()) - 2
                AND NOT EXISTS (
                    SELECT 1 FROM cine.ViewHistory vh 
                    WHERE vh.movieId = m.movieId AND vh.userId = :user_id
                )
            GROUP BY m.movieId, m.title, m.posterUrl, m.releaseYear, m.country
            ORDER BY m.releaseYear DESC, avgRating DESC
        """), {"user_id": user_id}).mappings().all()
        
        for movie in latest_movies:
            if movie["movieId"] not in [r["id"] for r in recommendations]:
                recommendations.append({
                    "id": movie["movieId"],
                    "title": movie["title"],
                    "poster": get_poster_or_dummy(movie.get("posterUrl"), movie["title"]),
                    "releaseYear": movie["releaseYear"],
                    "country": movie["country"],
                    "score": 0.8,  # High score for latest
                    "rank": len(recommendations) + 1,
                    "avgRating": round(float(movie["avgRating"]), 2) if movie["avgRating"] else 0.0,
                    "ratingCount": movie["ratingCount"],
                    "genres": movie["genres"] or "",
                    "source": "latest"
                })
        
        # 3. Phim có rating cao nhất - loại bỏ phim đã xem
        high_rated_movies = conn.execute(text("""
            SELECT TOP 4
                m.movieId, m.title, m.posterUrl, m.releaseYear, m.country,
                AVG(CAST(r.value AS FLOAT)) AS avgRating,
                COUNT(r.movieId) AS ratingCount,
                STUFF((
                    SELECT TOP 10 ', ' + g2.name
                    FROM cine.MovieGenre mg2
                    JOIN cine.Genre g2 ON mg2.genreId = g2.genreId
                    WHERE mg2.movieId = m.movieId
                    ORDER BY g2.name
                    FOR XML PATH(''), TYPE
                ).value('.', 'NVARCHAR(MAX)'), 1, 2, '') as genres
            FROM cine.Movie m
            INNER JOIN cine.Rating r ON m.movieId = r.movieId
            WHERE NOT EXISTS (
                SELECT 1 FROM cine.ViewHistory vh 
                WHERE vh.movieId = m.movieId AND vh.userId = :user_id
            )
            GROUP BY m.movieId, m.title, m.posterUrl, m.releaseYear, m.country
            HAVING COUNT(r.movieId) >= 10  -- Ít nhất 10 ratings
            ORDER BY avgRating DESC, ratingCount DESC
        """), {"user_id": user_id}).mappings().all()
        
        for movie in high_rated_movies:
            if movie["movieId"] not in [r["id"] for r in recommendations]:
                recommendations.append({
                    "id": movie["movieId"],
                    "title": movie["title"],
                    "poster": get_poster_or_dummy(movie.get("posterUrl"), movie["title"]),
                    "releaseYear": movie["releaseYear"],
                    "country": movie["country"],
                    "score": 0.85,  # High score for high rated
                    "rank": len(recommendations) + 1,
                    "avgRating": round(float(movie["avgRating"]), 2) if movie["avgRating"] else 0.0,
                    "ratingCount": movie["ratingCount"],
                    "genres": movie["genres"] or "",
                    "source": "high_rated"
                })
        
        # Lưu cold start recommendations vào database
        if recommendations:
            # Sử dụng begin() để tự động quản lý transaction
            from flask import current_app
            with current_app.db_engine.begin() as trans_conn:
                # Xóa recommendations cũ
                trans_conn.execute(text("""
                    DELETE FROM cine.ColdStartRecommendations WHERE userId = :user_id
                """), {"user_id": user_id})
                
                # Lấy max recId để tạo recId mới
                max_rec_id_result = trans_conn.execute(text("""
                    SELECT ISNULL(MAX(recId), 0) FROM cine.ColdStartRecommendations
                """)).scalar()
                rec_id = max_rec_id_result + 1 if max_rec_id_result else 1
                
                # Lưu recommendations mới
                for rec in recommendations:
                    trans_conn.execute(text("""
                        INSERT INTO cine.ColdStartRecommendations 
                        (recId, userId, movieId, score, rank, source, generatedAt, expiresAt, reason)
                        VALUES (:rec_id, :user_id, :movie_id, :score, :rank, :source, GETUTCDATE(), DATEADD(day, 7, GETUTCDATE()), :reason)
                    """), {
                        "rec_id": rec_id,
                        "user_id": user_id,
                        "movie_id": rec["id"],
                        "score": rec["score"],
                        "rank": rec["rank"],
                        "source": rec["source"],
                        "reason": f"Cold start recommendation based on {rec['source']}"
                    })
                    rec_id += 1
                # begin() tự động commit khi exit context thành công
            print(f"Generated {len(recommendations)} cold start recommendations for user {user_id}")
        
        return recommendations[:10]  # Trả về tối đa 10 recommendations
        
    except Exception as e:
        print(f"Error generating cold start recommendations: {e}")
        return []

@main_bp.route("/history")
@login_required
def view_history():
    """Trang lịch sử xem của user"""
    user_id = session.get("user_id")
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    try:
        with current_app.db_engine.connect() as conn:
            # Lấy tổng số lượng
            total_count = conn.execute(text("""
                SELECT COUNT(*) FROM cine.ViewHistory WHERE userId = :user_id
            """), {"user_id": user_id}).scalar()
            
            # Lấy lịch sử xem với phân trang
            offset = (page - 1) * per_page
            history_query = text("""
                SELECT TOP (:per_page) 
                    vh.historyId, vh.startedAt, vh.finishedAt, vh.progressSec,
                    m.movieId, m.title, m.posterUrl, m.releaseYear, m.overview,
                    CAST(STRING_AGG(g.name, ', ') WITHIN GROUP (ORDER BY g.name) AS NVARCHAR(MAX)) as genres
                FROM cine.ViewHistory vh
                INNER JOIN cine.Movie m ON vh.movieId = m.movieId
                LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                WHERE vh.userId = :user_id
                GROUP BY vh.historyId, vh.startedAt, vh.finishedAt, vh.progressSec,
                         m.movieId, m.title, m.posterUrl, m.releaseYear, m.overview
                ORDER BY vh.startedAt DESC
                OFFSET :offset ROWS
            """)
            
            history_rows = conn.execute(history_query, {
                "user_id": user_id,
                "per_page": per_page,
                "offset": offset
            }).mappings().all()
            
            # Format dữ liệu
            view_history = []
            for row in history_rows:
                history_item = {
                    "historyId": row["historyId"],
                    "movieId": row["movieId"],
                    "title": row["title"],
                    "posterUrl": get_poster_or_dummy(row.get("posterUrl"), row["title"]),
                    "releaseYear": row["releaseYear"],
                    "overview": row["overview"],
                    "genres": row["genres"] or "",
                    "startedAt": row["startedAt"],
                    "finishedAt": row["finishedAt"],
                    "progressSec": row["progressSec"],
                    "isCompleted": row["finishedAt"] is not None
                }
                view_history.append(history_item)
            
            # Tính toán pagination
            total_pages = (total_count + per_page - 1) // per_page
            pagination = {
                "current_page": page,
                "total_pages": total_pages,
                "total_count": total_count,
                "has_prev": page > 1,
                "has_next": page < total_pages,
                "prev_page": page - 1 if page > 1 else None,
                "next_page": page + 1 if page < total_pages else None
            }
            
            return render_template("history.html", 
                                 view_history=view_history,
                                 pagination=pagination)
            
    except Exception as e:
        print(f"Error getting view history: {e}")
        return render_template("history.html", 
                             view_history=[],
                             pagination={"current_page": 1, "total_pages": 0, "total_count": 0},
                             error="Có lỗi xảy ra khi tải lịch sử xem")

@main_bp.route("/api/view_history")
@login_required
def get_view_history():
    """API endpoint để lấy lịch sử xem"""
    try:
        user_id = session.get("user_id")
        limit = request.args.get('limit', 20, type=int)
        
        with current_app.db_engine.connect() as conn:
            history_query = text("""
                SELECT TOP (:limit)
                    vh.historyId, vh.startedAt, vh.finishedAt, vh.progressSec,
                    m.movieId, m.title, m.posterUrl, m.releaseYear,
                    CAST(STRING_AGG(g.name, ', ') WITHIN GROUP (ORDER BY g.name) AS NVARCHAR(MAX)) as genres
                FROM cine.ViewHistory vh
                INNER JOIN cine.Movie m ON vh.movieId = m.movieId
                LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                WHERE vh.userId = :user_id
                GROUP BY vh.historyId, vh.startedAt, vh.finishedAt, vh.progressSec,
                         m.movieId, m.title, m.posterUrl, m.releaseYear
                ORDER BY vh.startedAt DESC
            """)
            
            history_rows = conn.execute(history_query, {
                "user_id": user_id,
                "limit": limit
            }).mappings().all()
            
            # Format dữ liệu
            view_history = []
            for row in history_rows:
                history_item = {
                    "historyId": row["historyId"],
                    "movieId": row["movieId"],
                    "title": row["title"],
                    "posterUrl": get_poster_or_dummy(row.get("posterUrl"), row["title"]),
                    "releaseYear": row["releaseYear"],
                    "genres": row["genres"] or "",
                    "startedAt": row["startedAt"].isoformat() if row["startedAt"] else None,
                    "finishedAt": row["finishedAt"].isoformat() if row["finishedAt"] else None,
                    "progressSec": row["progressSec"],
                    "isCompleted": row["finishedAt"] is not None
                }
                view_history.append(history_item)
            
            return jsonify({
                "success": True,
                "view_history": view_history
            })
            
    except Exception as e:
        print(f"Error getting view history API: {e}")
        return jsonify({
            "success": False,
            "message": f"Có lỗi xảy ra: {str(e)}"
        })


@main_bp.route("/api/delete_history_item/<int:history_id>", methods=["DELETE"])
@login_required
def delete_history_item(history_id):
    """Xóa một mục lịch sử xem cụ thể"""
    try:
        user_id = session.get("user_id")
        
        with current_app.db_engine.begin() as conn:
            # Kiểm tra xem history item có thuộc về user này không
            history_exists = conn.execute(text("""
                SELECT 1 FROM cine.ViewHistory 
                WHERE historyId = :history_id AND userId = :user_id
            """), {"history_id": history_id, "user_id": user_id}).scalar()
            
            if not history_exists:
                return jsonify({"success": False, "message": "Không tìm thấy lịch sử xem này"})
            
            # Xóa history item
            conn.execute(text("""
                DELETE FROM cine.ViewHistory 
                WHERE historyId = :history_id AND userId = :user_id
            """), {"history_id": history_id, "user_id": user_id})
            
            return jsonify({"success": True, "message": "Đã xóa mục lịch sử xem thành công"})
            
    except Exception as e:
        print(f"Error deleting history item: {e}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra khi xóa lịch sử xem"})


@main_bp.route("/api/clear_all_history", methods=["DELETE"])
@login_required
def clear_all_history():
    """Xóa toàn bộ lịch sử xem của user"""
    try:
        user_id = session.get("user_id")
        
        with current_app.db_engine.begin() as conn:
            # Đếm số lượng history items sẽ bị xóa
            count = conn.execute(text("""
                SELECT COUNT(*) FROM cine.ViewHistory WHERE userId = :user_id
            """), {"user_id": user_id}).scalar()
            
            if count == 0:
                return jsonify({"success": False, "message": "Không có lịch sử xem nào để xóa"})
            
            # Xóa toàn bộ lịch sử xem
            conn.execute(text("""
                DELETE FROM cine.ViewHistory WHERE userId = :user_id
            """), {"user_id": user_id})
            
            return jsonify({
                "success": True, 
                "message": f"Đã xóa {count} mục lịch sử xem thành công"
            })
            
    except Exception as e:
        print(f"Error clearing all history: {e}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra khi xóa lịch sử xem"})


@main_bp.route("/api/update_watch_progress", methods=["POST"])
@login_required
def update_watch_progress():
    """Cập nhật tiến độ xem phim"""
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"success": False, "message": "Bạn cần đăng nhập để cập nhật tiến độ xem"})
        
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Thiếu dữ liệu yêu cầu"})
        
        movie_id = data.get('movie_id')
        progress_sec = data.get('progress_sec', 0)
        is_finished = data.get('is_finished', False)
        
        if not movie_id:
            return jsonify({"success": False, "message": "Thiếu thông tin movie_id"})
        
        # Đảm bảo movie_id và progress_sec là số
        try:
            movie_id = int(movie_id)
            progress_sec = int(progress_sec) if progress_sec else 0
        except (ValueError, TypeError):
            return jsonify({"success": False, "message": "Dữ liệu không hợp lệ"})
        
        with current_app.db_engine.begin() as conn:
            # Kiểm tra xem có history record nào cho movie này không
            result = conn.execute(text("""
                SELECT TOP 1 historyId FROM cine.ViewHistory 
                WHERE userId = :user_id AND movieId = :movie_id
                ORDER BY startedAt DESC
            """), {"user_id": user_id, "movie_id": movie_id})
            
            # Lấy giá trị scalar một cách an toàn
            # Sử dụng fetchone() và lấy phần tử đầu tiên để tương thích với nhiều phiên bản SQLAlchemy
            row = result.fetchone()
            if row:
                # Truy cập phần tử đầu tiên, hỗ trợ cả Row object và tuple
                try:
                    history_record = row[0]
                except (TypeError, IndexError):
                    # Nếu là Row object với named access
                    history_record = row.historyId if hasattr(row, 'historyId') else None
            else:
                history_record = None
            
            if history_record:
                # Cập nhật record hiện tại
                if is_finished:
                    conn.execute(text("""
                        UPDATE cine.ViewHistory 
                        SET progressSec = :progress_sec, finishedAt = GETDATE()
                        WHERE historyId = :history_id
                    """), {"history_id": history_record, "progress_sec": progress_sec})
                else:
                    conn.execute(text("""
                        UPDATE cine.ViewHistory 
                        SET progressSec = :progress_sec
                        WHERE historyId = :history_id
                    """), {"history_id": history_record, "progress_sec": progress_sec})
            else:
                # Tạo record mới
                # Lấy max historyId để tạo ID mới
                max_history_id_result = conn.execute(text("""
                    SELECT ISNULL(MAX(historyId), 0) FROM cine.ViewHistory
                """)).fetchone()
                max_history_id = max_history_id_result[0] if max_history_id_result else 0
                new_history_id = max_history_id + 1
                
                if is_finished:
                    # Nếu hoàn thành, set cả startedAt và finishedAt
                    conn.execute(text("""
                        INSERT INTO cine.ViewHistory (historyId, userId, movieId, startedAt, progressSec, finishedAt, deviceType, ipAddress, userAgent)
                        VALUES (:history_id, :user_id, :movie_id, GETDATE(), :progress_sec, GETDATE(), :device_type, :ip_address, :user_agent)
                    """), {
                        "history_id": new_history_id,
                        "user_id": user_id,
                        "movie_id": movie_id,
                        "progress_sec": progress_sec,
                        "device_type": request.headers.get('User-Agent', 'Unknown')[:50],
                        "ip_address": request.remote_addr,
                        "user_agent": request.headers.get('User-Agent', '')[:500]
                    })
                else:
                    # Nếu chưa hoàn thành, chỉ set startedAt
                    conn.execute(text("""
                        INSERT INTO cine.ViewHistory (historyId, userId, movieId, startedAt, progressSec, deviceType, ipAddress, userAgent)
                        VALUES (:history_id, :user_id, :movie_id, GETDATE(), :progress_sec, :device_type, :ip_address, :user_agent)
                    """), {
                        "history_id": new_history_id,
                        "user_id": user_id,
                        "movie_id": movie_id,
                        "progress_sec": progress_sec,
                        "device_type": request.headers.get('User-Agent', 'Unknown')[:50],
                        "ip_address": request.remote_addr,
                        "user_agent": request.headers.get('User-Agent', '')[:500]
                    })
            
            message = "Đã cập nhật tiến độ xem"
            if is_finished:
                message = "🎉 Đã đánh dấu phim hoàn thành!"
            
            return jsonify({"success": True, "message": message})
            
    except Exception as e:
        current_app.logger.error(f"Error updating watch progress: {e}", exc_info=True)
        import traceback
        error_details = traceback.format_exc()
        current_app.logger.error(f"Traceback: {error_details}")
        return jsonify({"success": False, "message": f"Có lỗi xảy ra khi cập nhật tiến độ xem: {str(e)}"})


@main_bp.route("/")
def home():
    # Kiểm tra nếu user đã đăng nhập nhưng chưa hoàn thành onboarding
    user_id = session.get("user_id")
    if user_id:
        try:
            with current_app.db_engine.connect() as conn:
                # Kiểm tra xem user đã hoàn thành onboarding chưa
                # Sử dụng COALESCE để xử lý trường hợp cột chưa tồn tại
                has_completed = conn.execute(text("""
                    SELECT COALESCE(hasCompletedOnboarding, 0) FROM cine.[User] WHERE userId = :user_id
                """), {"user_id": user_id}).scalar()
                
                # Cache trong session
                session["onboarding_checked"] = True
                session["onboarding_completed"] = bool(has_completed)
                
                if not has_completed:
                    return redirect(url_for('main.onboarding'))
        except Exception as e:
            print(f"Error checking onboarding status: {e}")
            # Nếu có lỗi, giả sử user chưa hoàn thành onboarding
            return redirect(url_for('main.onboarding'))
    
    # Lấy danh sách phim từ DB bằng engine (odbc_connect); nếu chưa đăng nhập, chuyển tới form đăng nhập
    if not session.get("user_id"):
        return redirect(url_for("main.login"))
    
    # ✅ TỐI ƯU: Sử dụng cached onboarding status nếu đã check
    if session.get("onboarding_checked") and not session.get("onboarding_completed"):
        return redirect(url_for('main.onboarding'))
    
    # Lấy page parameter cho tất cả phim và genre filter
    page = request.args.get('page', 1, type=int)
    per_page = 10  # Số phim mỗi trang
    genre_filter = request.args.get('genre', '', type=str)  # Lọc theo thể loại
    search_query = request.args.get('q', '', type=str)  # Tìm kiếm
    
    # 1. Phim mới nhất (10 phim, không phân trang) - với caching
    cache_key = f"latest_{genre_filter}"
    current_time = time.time()
    
    # Kiểm tra cache
    if (latest_movies_cache.get('data') and 
        latest_movies_cache.get('key') == cache_key and
        latest_movies_cache.get('timestamp') and 
        current_time - latest_movies_cache['timestamp'] < latest_movies_cache['ttl']):
        latest_movies = latest_movies_cache['data']
    else:
        try:
            with current_app.db_engine.connect() as conn:
                # Query đơn giản không có STUFF subquery
                if genre_filter:
                    rows = conn.execute(text("""
                        SELECT DISTINCT TOP 10
                            m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.createdAt, m.viewCount
                    FROM cine.Movie m
                    JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                    JOIN cine.Genre g ON mg.genreId = g.genreId
                    WHERE g.name = :genre
                    ORDER BY m.createdAt DESC, m.movieId DESC
                """), {"genre": genre_filter}).mappings().all()
                else:
                    rows = conn.execute(text("""
                    SELECT TOP 10 
                            movieId, title, posterUrl, backdropUrl, overview, createdAt, viewCount
                        FROM cine.Movie
                        ORDER BY createdAt DESC, movieId DESC
                    """)).mappings().all()
                
                movie_ids = [r["movieId"] for r in rows]
            
            # ✅ TỐI ƯU: Không query genres/ratings ở đây, sẽ combine query sau
            latest_movies = [
                {
                    "id": r["movieId"],
                    "title": r["title"],
                    "poster": r.get("posterUrl") if r.get("posterUrl") and r.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={r['title'][:20].replace(' ', '+')}",
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                    "createdAt": r.get("createdAt"),
                    "avgRating": 0,  # Sẽ được update sau khi combine query
                    "ratingCount": 0,  # Sẽ được update sau khi combine query
                    "viewCount": r.get("viewCount", 0) or 0,
                    "genres": "",  # Sẽ được update sau khi combine query
                }
                for r in rows
            ]
            
            # Update cache
            latest_movies_cache['data'] = latest_movies
            latest_movies_cache['key'] = cache_key
            latest_movies_cache['timestamp'] = current_time
        except Exception as e:
            current_app.logger.error(f"Error loading latest movies: {e}", exc_info=True)
            latest_movies = []
    
    # 2. Carousel movies (6 phim mới nhất) - với caching
    if (carousel_movies_cache.get('data') and 
        carousel_movies_cache.get('timestamp') and 
        current_time - carousel_movies_cache['timestamp'] < carousel_movies_cache['ttl']):
        carousel_movies = carousel_movies_cache['data']
    else:
        try:
            with current_app.db_engine.connect() as conn:
                # Query đơn giản không có STUFF subquery
                carousel_rows = conn.execute(text("""
                SELECT TOP 6 
                        movieId, title, posterUrl, backdropUrl, overview, createdAt
                    FROM cine.Movie
                    ORDER BY createdAt DESC, movieId DESC
                """)).mappings().all()
                
                carousel_movie_ids = [r["movieId"] for r in carousel_rows]
            
            # ✅ TỐI ƯU: Không query genres/ratings ở đây, sẽ combine query sau
            carousel_movies = [
                {
                    "id": r["movieId"],
                    "title": r["title"],
                    "poster": r.get("posterUrl") if r.get("posterUrl") and r.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={r['title'][:20].replace(' ', '+')}",
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                    "createdAt": r.get("createdAt"),
                    "avgRating": 0,  # Sẽ được update sau khi combine query
                    "ratingCount": 0,  # Sẽ được update sau khi combine query
                    "genres": "",  # Sẽ được update sau khi combine query
                }
                for r in carousel_rows
            ]
            
            # Update cache
            carousel_movies_cache['data'] = carousel_movies
            carousel_movies_cache['timestamp'] = current_time
        except Exception as e:
            current_app.logger.error(f"Error loading carousel movies: {e}", exc_info=True)
            carousel_movies = []
    
    # Personal recommendations (gợi ý cá nhân)
    # Personal recommendations (gợi ý cá nhân) - Collaborative Filtering + Cold Start
    user_id = session.get("user_id")
    personal_recommendations = []
    trending_movies = []
    
    if user_id:
        try:
            # Kiểm tra xem user có đủ dữ liệu để tạo recommendations không
            with current_app.db_engine.connect() as conn:
                # Combine 2 queries thành 1 để tối ưu
                interaction_counts = conn.execute(text("""
                    SELECT 
                        (SELECT COUNT(*) FROM cine.Rating WHERE userId = :user_id) as rating_count,
                        (SELECT COUNT(*) FROM cine.ViewHistory WHERE userId = :user_id) as view_count
                """), {"user_id": user_id}).mappings().first()
                
                total_interactions = (interaction_counts.rating_count or 0) + (interaction_counts.view_count or 0)
                
                # Progressive Cold Start: Tỷ lệ cold start giảm dần theo số interactions
                # 0-5 interactions: 100% cold start
                # 6-10 interactions: 70% CF/CB, 30% cold start
                # 11-20 interactions: 80% CF/CB, 20% cold start
                # 21-50 interactions: 90% CF/CB, 10% cold start
                # 51+ interactions: 95% CF/CB, 5% cold start
                
                if total_interactions < 5:
                    # 100% cold start
                    cold_start_weight = 1.0
                    cf_cb_weight = 0.0
                    current_app.logger.info(f"User {user_id} has {total_interactions} interactions, using 100% cold start")
                elif total_interactions < 11:
                    # 30% cold start, 70% CF/CB
                    cold_start_weight = 0.3
                    cf_cb_weight = 0.7
                    current_app.logger.info(f"User {user_id} has {total_interactions} interactions, using 30% cold start, 70% CF/CB")
                elif total_interactions < 21:
                    # 20% cold start, 80% CF/CB
                    cold_start_weight = 0.2
                    cf_cb_weight = 0.8
                    current_app.logger.info(f"User {user_id} has {total_interactions} interactions, using 20% cold start, 80% CF/CB")
                elif total_interactions < 51:
                    # 10% cold start, 90% CF/CB
                    cold_start_weight = 0.1
                    cf_cb_weight = 0.9
                    current_app.logger.info(f"User {user_id} has {total_interactions} interactions, using 10% cold start, 90% CF/CB")
                else:
                    # 5% cold start, 95% CF/CB
                    cold_start_weight = 0.05
                    cf_cb_weight = 0.95
                    current_app.logger.info(f"User {user_id} has {total_interactions} interactions, using 5% cold start, 95% CF/CB")
                
                # Lấy cold start recommendations nếu cần
                cold_start_recs = []
                if cold_start_weight > 0:
                    cold_start_recs = get_cold_start_recommendations(user_id, conn)
                    # Format cold start recs để có cùng structure với CF/CB
                    for rec in cold_start_recs:
                        rec['algo'] = 'cold_start'
                        rec['cf_score'] = 0.0
                        rec['cb_score'] = 0.0
                        rec['hybrid_score'] = rec.get('score', 0.0)
                
                # Lấy CF/CB recommendations nếu cần
                cf_cb_recs = []
                if cf_cb_weight > 0:
                    # ✅ TỐI ƯU: Chỉ lấy từ database, không generate mỗi request
                    # Ưu tiên hybrid recommendations, nếu không có thì lấy CF
                    # ✅ LỌC: Loại bỏ phim đã xem khỏi recommendations
                    personal_rows = conn.execute(text("""
                        SELECT TOP 10 
                            m.movieId, m.title, m.posterUrl, m.releaseYear, m.country,
                            pr.score, pr.rank, pr.generatedAt, pr.algo
                        FROM cine.PersonalRecommendation pr
                        JOIN cine.Movie m ON m.movieId = pr.movieId
                        WHERE pr.userId = :user_id 
                            AND pr.expiresAt > GETUTCDATE()
                            AND NOT EXISTS (
                                SELECT 1 FROM cine.ViewHistory vh 
                                WHERE vh.movieId = pr.movieId AND vh.userId = :user_id
                            )
                        ORDER BY 
                            CASE WHEN pr.algo = 'hybrid' THEN 0 ELSE 1 END,  -- Ưu tiên hybrid trước
                            pr.rank
                    """), {"user_id": user_id}).mappings().all()
            
                    # Kiểm tra xem có recommendations cũ (collaborative) không
                    has_old_collaborative = False
                    has_hybrid = False
                    if personal_rows:
                        for row in personal_rows:
                            if row["algo"] == "collaborative" or row["algo"] == "enhanced_cf":
                                has_old_collaborative = True
                            elif row["algo"] == "hybrid":
                                has_hybrid = True
                    
                    # ✅ TỐI ƯU: Bỏ auto-generate hybrid trong route home() - quá chậm
                    # User có thể generate hybrid qua API endpoint /api/generate_recommendations
                    # Nếu có recommendations cũ (collaborative) nhưng chưa có hybrid, chỉ log warning
                    if has_old_collaborative and not has_hybrid:
                        current_app.logger.info(f"User {user_id} has old collaborative recommendations but no hybrid. User can generate via API.")
                    
                    if personal_rows:
                        # Có recommendations từ database - sử dụng luôn
                        personal_movie_ids = [row["movieId"] for row in personal_rows]
                        
                        # ✅ TỐI ƯU: Không query genres/ratings ở đây, sẽ combine query sau
                        cf_cb_recs = []
                        for row in personal_rows:
                            # Nếu là hybrid, cần lấy thêm CF và CB scores từ hybrid_recs nếu có
                            # Tạm thời set default, sẽ được update nếu có hybrid_recs
                            row_score = float(row["score"]) if row["score"] is not None else 0.0
                            rec_dict = {
                                "id": row["movieId"],
                                "title": row["title"],
                                "poster": row.get("posterUrl") if row.get("posterUrl") and row.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={row['title'][:20].replace(' ', '+')}",
                                "year": row.get("releaseYear"),
                                "country": row.get("country"),
                                "score": round(row_score, 4),
                                "rank": row["rank"],
                                "genres": "",  # Sẽ được update sau khi combine query
                                # Đảm bảo movieId là int để match với dict key
                                "avgRating": 0.0,  # Sẽ được update sau khi combine query
                                "ratingCount": 0,  # Sẽ được update sau khi combine query
                                "algo": row["algo"] or "hybrid",
                                "reason": "Cached recommendation",
                                # Default scores (sẽ được update nếu có hybrid_recs)
                                "cf_score": 0.0,
                                "cb_score": 0.0,
                                "hybrid_score": round(row_score, 4)
                            }
                            cf_cb_recs.append(rec_dict)
                        
                        # ✅ TỐI ƯU: Bỏ regenerate CF/CB scores - quá chậm (limit=200)
                        # Chỉ hiển thị hybrid_score từ database, CF/CB scores = 0.0
                        # Nếu cần CF/CB scores, user có thể generate lại recommendations qua API
                        # Hybrid recommendations đã được tính toán và lưu trong DB, không cần regenerate
                    else:
                        # Không có recommendations trong database - fallback về rating-based
                        cf_cb_recs = create_rating_based_recommendations(user_id, latest_movies[:10], current_app.db_engine)
                
                # Merge cold start và CF/CB recommendations theo tỷ lệ
                # CHỈ blend cold start nếu CF/CB recommendations không đủ hoặc chất lượng thấp
                if cold_start_weight > 0 and cold_start_recs:
                    if cf_cb_weight > 0 and cf_cb_recs:
                        # Kiểm tra chất lượng CF/CB recommendations
                        # Nếu có ít nhất 5 CF/CB recommendations với hybrid_score > 0.3 hoặc có CF/CB scores > 0, không cần cold start
                        high_quality_cf_cb = [
                            rec for rec in cf_cb_recs 
                            if rec.get('hybrid_score', 0) > 0.3 
                            or rec.get('cf_score', 0) > 0 
                            or rec.get('cb_score', 0) > 0
                        ]
                        
                        if len(high_quality_cf_cb) >= 5:
                            # Có đủ CF/CB recommendations chất lượng cao, không cần cold start
                            personal_recommendations = cf_cb_recs[:10]
                            current_app.logger.info(f"Using {len(personal_recommendations)} CF/CB recommendations (high quality: {len(high_quality_cf_cb)} items, skipping cold start) for user {user_id}")
                        else:
                            # Blend cold start với CF/CB khi CF/CB không đủ chất lượng
                            # Tính số lượng recommendations từ mỗi source
                            num_cold_start = max(1, int(10 * cold_start_weight))
                            num_cf_cb = 10 - num_cold_start
                            
                            # Lấy top recommendations từ mỗi source
                            selected_cold_start = cold_start_recs[:num_cold_start]
                            selected_cf_cb = cf_cb_recs[:num_cf_cb] if len(cf_cb_recs) >= num_cf_cb else cf_cb_recs
                            
                            # Merge và shuffle để trộn đều
                            personal_recommendations = selected_cold_start + selected_cf_cb
                            # Shuffle để trộn đều cold start và CF/CB recommendations
                            random.shuffle(personal_recommendations)
                            personal_recommendations = personal_recommendations[:10]
                            
                            # Update scores để reflect blending
                            for rec in personal_recommendations:
                                if rec.get('algo') == 'cold_start':
                                    rec['hybrid_score'] = rec.get('score', 0.0) * cold_start_weight
                                else:
                                    # CF/CB recommendations giữ nguyên hybrid_score nhưng nhân với weight
                                    rec['hybrid_score'] = rec.get('hybrid_score', rec.get('score', 0.0)) * cf_cb_weight
                            
                            current_app.logger.info(f"Blended {len(selected_cold_start)} cold start ({cold_start_weight*100:.0f}%) + {len(selected_cf_cb)} CF/CB ({cf_cb_weight*100:.0f}%) recommendations for user {user_id}")
                    else:
                        # Chỉ có cold start
                        personal_recommendations = cold_start_recs[:10]
                elif cf_cb_weight > 0 and cf_cb_recs:
                    # Chỉ có CF/CB
                    personal_recommendations = cf_cb_recs[:10]
                else:
                    # Fallback: không có recommendations
                    personal_recommendations = []
                
        except Exception as e:
            # Fallback: Lấy gợi ý từ database nếu model chưa load
            print(f"Error getting personal recommendations: {e}")
            with current_app.db_engine.connect() as conn:
                personal_rows = conn.execute(text("""
                    SELECT TOP 10 
                        m.movieId, m.title, m.posterUrl, m.releaseYear, m.country,
                        pr.score, pr.rank, pr.generatedAt, pr.algo,
                        AVG(CAST(r.value AS FLOAT)) as avgRating,
                        COUNT(r.movieId) as ratingCount,
                        COUNT(DISTINCT w.userId) as watchlistCount,
                        COUNT(DISTINCT vh.userId) as viewHistoryCount,
                        COUNT(DISTINCT f.userId) as favoriteCount,
                        COUNT(DISTINCT c.userId) as commentCount,
                        CAST(STRING_AGG(g.name, ', ') WITHIN GROUP (ORDER BY g.name) AS NVARCHAR(MAX)) as genres
                    FROM cine.PersonalRecommendation pr
                    JOIN cine.Movie m ON m.movieId = pr.movieId
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    LEFT JOIN cine.Watchlist w ON m.movieId = w.movieId
                    LEFT JOIN cine.ViewHistory vh ON m.movieId = vh.movieId
                    LEFT JOIN cine.Favorite f ON m.movieId = f.movieId
                    LEFT JOIN cine.Comment c ON m.movieId = c.movieId
                    LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                    LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                    WHERE pr.userId = :user_id AND pr.expiresAt > GETUTCDATE() AND pr.algo = 'collaborative'
                    GROUP BY m.movieId, m.title, m.posterUrl, m.releaseYear, m.country,
                             pr.score, pr.rank, pr.generatedAt, pr.algo
                    ORDER BY pr.rank
                """), {"user_id": user_id}).mappings().all()
                
                
                personal_recommendations = [
                    {
                        "id": row["movieId"],
                        "title": row["title"],
                        "poster": row.get("posterUrl") if row.get("posterUrl") and row.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={row['title'][:20].replace(' ', '+')}",
                        "releaseYear": row["releaseYear"],
                        "country": row["country"],
                        "score": round(float(row["score"]), 4),
                        "rank": row["rank"],
                        "avgRating": round(float(row["avgRating"]), 2) if row["avgRating"] else 0.0,
                        "ratingCount": row["ratingCount"],
                        "watchlistCount": row["watchlistCount"],
                        "viewHistoryCount": row["viewHistoryCount"],
                        "favoriteCount": row["favoriteCount"],
                        "commentCount": row["commentCount"],
                        "genres": row["genres"] or "",
                        "algo": row["algo"] or "database",
                        "generatedAt": row["generatedAt"],
                        "reason": "Database recommendation"
                    }
                    for row in personal_rows
                ]
                
                # ✅ TỐI ƯU: Lấy trending movies từ cache hoặc query đơn giản hơn
                # Kiểm tra cache trending movies
                if (trending_cache.get('data') and 
                    trending_cache.get('timestamp') and 
                    current_time - trending_cache['timestamp'] < trending_cache['ttl']):
                    trending_movies = trending_cache['data']
                else:
                    try:
                        with current_app.db_engine.connect() as conn:
                            # ✅ TỐI ƯU: Query được tối ưu để giảm chi phí
                            # 1. Tính DATEADD một lần trong CTE thay vì trong JOIN condition
                            # 2. Sử dụng subquery để filter trước khi JOIN (giảm số rows scan)
                            # 3. Loại bỏ HAVING bằng cách filter trong WHERE của subquery
                            trending_rows = conn.execute(text("""
                                WITH date_threshold AS (
                                    SELECT DATEADD(day, -7, GETDATE()) AS threshold_date
                                ),
                                recent_views AS (
                                    SELECT movieId, COUNT(DISTINCT historyId) as view_count_7d
                                    FROM cine.ViewHistory vh, date_threshold dt
                                    WHERE vh.startedAt >= dt.threshold_date
                                    GROUP BY movieId
                                ),
                                recent_ratings AS (
                                    SELECT movieId, COUNT(DISTINCT userId) as rating_count_7d
                                    FROM cine.Rating r, date_threshold dt
                                    WHERE r.ratedAt >= dt.threshold_date
                                    GROUP BY movieId
                                )
                                SELECT TOP 10
                                    m.movieId, m.title, m.posterUrl, m.releaseYear, m.country, m.viewCount,
                                    ISNULL(rv.view_count_7d, 0) as view_count_7d,
                                    ISNULL(rr.rating_count_7d, 0) as rating_count_7d
                                FROM cine.Movie m
                                LEFT JOIN recent_views rv ON m.movieId = rv.movieId
                                LEFT JOIN recent_ratings rr ON m.movieId = rr.movieId
                                WHERE (rv.view_count_7d > 0 OR rr.rating_count_7d > 0)
                                ORDER BY 
                                    (m.viewCount * 0.5 + ISNULL(rv.view_count_7d, 0) * 0.3 + ISNULL(rr.rating_count_7d, 0) * 0.2) DESC
                            """)).mappings().all()
                            
                            # ✅ TỐI ƯU: Không query genres/ratings ở đây, sẽ combine query sau
                            trending_movie_ids = [row.movieId for row in trending_rows]
                            
                            trending_movies = []
                            for row in trending_rows:
                                trending_movies.append({
                                    "id": row.movieId,
                                    "title": row.title,
                                    "poster": get_poster_or_dummy(row.posterUrl, row.title),
                                    "releaseYear": row.releaseYear,
                                    "country": row.country,
                                    "ratingCount": 0,  # Sẽ được update sau khi combine query
                                    "avgRating": 0,  # Sẽ được update sau khi combine query
                                    "viewCount": row.viewCount or 0,
                                    "genres": ""  # Sẽ được update sau khi combine query
                                })
                            
                            # Update cache
                            trending_cache['data'] = trending_movies
                            trending_cache['timestamp'] = current_time
                            
                            # Nếu không đủ 10 phim, bổ sung bằng phim mới nhất
                            if len(trending_movies) < 10:
                                existing_movie_ids = [m["id"] for m in trending_movies]
                                placeholders = ','.join([f':id{i}' for i in range(len(existing_movie_ids))])
                                params = {f'id{i}': mid for i, mid in enumerate(existing_movie_ids)}
                                
                                # Validate và sanitize fallback_limit để tránh SQL injection
                                fallback_limit = max(1, 10 - len(trending_movies))  # Đảm bảo >= 1
                                validated_fallback_limit = validate_limit(fallback_limit, max_limit=100, default=10)
                                top_clause = safe_top_clause(validated_fallback_limit, max_limit=100)
                                
                                # ✅ TỐI ƯU: Query đơn giản không có STRING_AGG
                                fallback_query = f"""
                                    SELECT {top_clause}
                                    m.movieId, m.title, m.posterUrl, m.releaseYear, m.country
                                    FROM cine.Movie m
                                    WHERE m.releaseYear IS NOT NULL
                                """
                                
                                if existing_movie_ids:
                                    fallback_query += f" AND m.movieId NOT IN ({placeholders})"
                                
                                fallback_query += """
                                    ORDER BY m.releaseYear DESC, m.movieId DESC
                                """
                                
                                fallback_rows = conn.execute(text(fallback_query), params).mappings().all()
                                
                                # ✅ TỐI ƯU: Không query genres/ratings ở đây, sẽ combine query sau
                                fallback_movie_ids = [row.movieId for row in fallback_rows]
                                
                                # Get viewCount từ Movie table
                                fallback_view_counts = {}
                                if fallback_movie_ids:
                                    placeholders_view = ','.join([f':id{i}' for i in range(len(fallback_movie_ids))])
                                    params_view = {f'id{i}': mid for i, mid in enumerate(fallback_movie_ids)}
                                    view_count_rows = conn.execute(text(f"""
                                        SELECT movieId, viewCount
                                        FROM cine.Movie
                                        WHERE movieId IN ({placeholders_view})
                                    """), params_view).mappings().all()
                                    fallback_view_counts = {row["movieId"]: row["viewCount"] or 0 for row in view_count_rows}
                                
                                for row in fallback_rows:
                                    movie_id = row.movieId
                                    trending_movies.append({
                                        "id": movie_id,
                                        "title": row.title,
                                        "poster": get_poster_or_dummy(row.posterUrl, row.title),
                                        "releaseYear": row.releaseYear,
                                        "country": row.country,
                                        "ratingCount": 0,  # Sẽ được update sau khi combine query
                                        "avgRating": 0,  # Sẽ được update sau khi combine query
                                        "viewCount": fallback_view_counts.get(movie_id, 0),
                                        "genres": ""  # Sẽ được update sau khi combine query
                                    })
                            
                            trending_movies = trending_movies[:10]
                    except Exception as e:
                        current_app.logger.error(f"Error getting trending movies: {e}", exc_info=True)
                        trending_movies = []
                
        except Exception as e:
            print(f"Error getting personal recommendations: {e}")
            personal_recommendations = []
            trending_movies = []
    
    # ✅ TỐI ƯU: Fallback - chỉ dùng latest movies, không gọi CF model (quá chậm)
    if not personal_recommendations:
            personal_recommendations = latest_movies[:10]
    if not trending_movies:
        trending_movies = latest_movies
    
    # Debug logging cuối cùng trước khi gửi đến template
    print(f"Debug - Final personal_recommendations before template:")
    print(f"  Total recommendations: {len(personal_recommendations)}")
    for i, rec in enumerate(personal_recommendations[:3]):
        print(f"  {i+1}. {rec.get('title', 'N/A')} (Score: {rec.get('score', 0):.3f})")
        print(f"     - Ratings: {rec.get('ratingCount', 0)}")
        print(f"     - Views: {rec.get('viewHistoryCount', 0)}")
        print(f"     - Watchlist: {rec.get('watchlistCount', 0)}")
        print(f"     - Favorites: {rec.get('favoriteCount', 0)}")
        print(f"     - Comments: {rec.get('commentCount', 0)}")
    
    # 3. Tất cả phim (có phân trang) - thay thế latest_movies cũ
    all_movies = []
    total_movies = 0
    pagination = None
    
    try:
        with current_app.db_engine.connect() as conn:
            if search_query:
                # Tìm kiếm phim theo từ khóa
                # Đếm tổng số kết quả tìm kiếm
                total_count = conn.execute(text("""
                    SELECT COUNT(*) 
                    FROM cine.Movie 
                    WHERE title LIKE :query
                """), {"query": f"%{search_query}%"}).scalar()
                total_movies = total_count
                
                # Tính toán phân trang
                total_pages = (total_movies + per_page - 1) // per_page
                offset = (page - 1) * per_page
                
                # Lấy kết quả tìm kiếm
                all_rows = conn.execute(text("""
                    SELECT movieId, title, posterUrl, backdropUrl, overview, releaseYear
                    FROM (
                        SELECT movieId, title, posterUrl, backdropUrl, overview, releaseYear,
                               ROW_NUMBER() OVER (
                                   ORDER BY 
                                       CASE 
                                           WHEN title LIKE :exact_query THEN 1
                                           WHEN title LIKE :start_query THEN 2
                                           ELSE 3
                                       END,
                                       title
                               ) as rn
                        FROM cine.Movie 
                        WHERE title LIKE :query
                    ) t
                    WHERE rn > :offset AND rn <= :offset + :per_page
                """), {
                    "query": f"%{search_query}%",
                    "exact_query": f"{search_query}%",
                    "start_query": f"{search_query}%",
                    "offset": offset,
                    "per_page": per_page
                }).mappings().all()
            elif genre_filter:
                # Lọc theo thể loại nếu được chọn
                # Đếm tổng số phim theo thể loại
                print(f"Debug - Filtering by genre: {genre_filter}")
                total_count = conn.execute(text("""
                    SELECT COUNT(*)
                    FROM (
                        SELECT DISTINCT m.movieId
                        FROM cine.Movie m
                        JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                        JOIN cine.Genre g ON mg.genreId = g.genreId
                        WHERE g.name = :genre
                    ) t
                """), {"genre": genre_filter}).scalar()
                total_movies = total_count
                print(f"Debug - Total movies for genre '{genre_filter}': {total_movies}")
                
                # Tính toán phân trang
                total_pages = (total_movies + per_page - 1) // per_page
                offset = (page - 1) * per_page
                print(f"Debug - Pagination: total_movies={total_movies}, per_page={per_page}, total_pages={total_pages}, page={page}, offset={offset}")
                
                # Lấy phim theo thể loại với phân trang
                # ✅ TỐI ƯU: Query movies trước, batch query genres sau
                print(f"Debug - Getting movies for genre '{genre_filter}', page {page}, offset {offset}, per_page {per_page}")
                all_rows = conn.execute(text("""
                    WITH filtered AS (
                        SELECT DISTINCT m.movieId,
                               ROW_NUMBER() OVER (ORDER BY m.movieId DESC) AS rn
                        FROM cine.Movie m
                        JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                        JOIN cine.Genre g ON mg.genreId = g.genreId
                        WHERE g.name = :genre
                    )
                    SELECT 
                        m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.viewCount
                    FROM filtered f
                    JOIN cine.Movie m ON m.movieId = f.movieId
                    WHERE f.rn > :offset AND f.rn <= :offset + :per_page
                    ORDER BY m.movieId DESC
                """), {"genre": genre_filter, "offset": offset, "per_page": per_page}).mappings().all()
                
                # ✅ TỐI ƯU: Không query genres/ratings ở đây, sẽ combine query sau
                all_movie_ids = [r["movieId"] for r in all_rows]
                print(f"Debug - Found {len(all_rows)} movies for genre '{genre_filter}' on page {page}")
                
                # Tạo pagination info cho genre
                pagination = {
                    "page": page,
                    "per_page": per_page,
                    "total": total_movies,
                    "pages": total_pages,
                    "has_prev": page > 1,
                    "has_next": page < total_pages,
                    "prev_num": page - 1 if page > 1 else None,
                    "next_num": page + 1 if page < total_pages else None
                }
                print(f"Debug - Genre pagination created: {pagination}")
            else:
                # Lấy tất cả phim
                # Đếm tổng số phim
                total_count = conn.execute(text("SELECT COUNT(*) FROM cine.Movie")).scalar()
                total_movies = total_count
                
                # Tính toán phân trang
                total_pages = (total_movies + per_page - 1) // per_page
                offset = (page - 1) * per_page
                
                # ✅ TỐI ƯU: Query movies trước, batch query genres sau
                all_rows = conn.execute(text("""
                    WITH paged AS (
                        SELECT m.movieId,
                               ROW_NUMBER() OVER (ORDER BY m.movieId DESC) AS rn
                        FROM cine.Movie m
                    )
                    SELECT 
                        m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.viewCount
                    FROM paged p
                    JOIN cine.Movie m ON m.movieId = p.movieId
                    WHERE p.rn > :offset AND p.rn <= :offset + :per_page
                    ORDER BY m.movieId DESC
                """), {"offset": offset, "per_page": per_page}).mappings().all()
            
                # ✅ TỐI ƯU: Không query genres/ratings ở đây, sẽ combine query sau
                all_movie_ids = [r["movieId"] for r in all_rows]
            
            # ✅ TỐI ƯU: Tạo all_movies structure, genres/ratings sẽ được update sau
            all_movies = [
                {
                    "id": r["movieId"],
                    "title": r["title"],
                    "poster": r.get("posterUrl") if r.get("posterUrl") and r.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={r['title'][:20].replace(' ', '+')}",
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                    "year": r.get("releaseYear"),
                    "viewCount": r.get("viewCount", 0) or 0,
                    "avgRating": 0,  # Sẽ được update sau khi combine query
                    "ratingCount": 0,  # Sẽ được update sau khi combine query
                    "genres": ""  # Sẽ được update sau khi combine query
                }
                for r in all_rows
            ]
            print(f"Debug - all_movies length: {len(all_movies)}")
            if all_movies:
                print(f"Debug - First movie: {all_movies[0]['title']} (ID: {all_movies[0]['id']})")
                print(f"Debug - Last movie: {all_movies[-1]['title']} (ID: {all_movies[-1]['id']})")
            
            # Tạo pagination info cho all movies (chỉ khi không có genre filter hoặc search)
            if not genre_filter and not search_query:
                pagination = {
                    "page": page,
                    "per_page": per_page,
                    "total": total_movies,
                    "pages": total_pages,
                    "has_prev": page > 1,
                    "has_next": page < total_pages,
                    "prev_num": page - 1 if page > 1 else None,
                    "next_num": page + 1 if page < total_pages else None
                }
            
    except Exception as e:
        print(f"Error getting all movies: {e}")
        all_movies = []
        pagination = None
        genre_filter = None
        search_query = None
    
    # Debug: In ra thông tin all_movies
    print(f"Debug - all_movies length: {len(all_movies) if all_movies else 0}")
    print(f"Debug - latest_movies length: {len(latest_movies) if latest_movies else 0}")
    print(f"Debug - genre_filter: '{genre_filter}'")
    print(f"Debug - search_query: '{search_query}'")
    print(f"Debug - pagination: {pagination}")
    print(f"Debug - total_movies: {total_movies}")
    print(f"Debug - URL: {request.url}")
    
    # Lấy danh sách tất cả thể loại từ database
    all_genres = []
    try:
        with current_app.db_engine.connect() as conn:
            genre_rows = conn.execute(text("""
                SELECT name, COUNT(mg.movieId) as movie_count
                FROM cine.Genre g
                LEFT JOIN cine.MovieGenre mg ON g.genreId = mg.genreId
                GROUP BY g.genreId, g.name
                ORDER BY movie_count DESC, g.name
            """)).mappings().all()
            
            all_genres = [
                {
                    "name": row["name"],
                    "slug": row["name"].lower().replace(' ', '-'),
                    "movie_count": row["movie_count"]
                }
                for row in genre_rows
            ]
    except Exception as e:
        print(f"Error getting genres: {e}")
        all_genres = []
    
    # Fallback nếu all_movies rỗng (chỉ khi không có genre_filter và search_query)
    if not all_movies and not genre_filter and not search_query:
        print("Debug - all_movies is empty, using fallback")
        all_movies = latest_movies[:10]  # Sử dụng latest_movies làm fallback
        pagination = {
            "page": 1,
            "per_page": 10,
            "total": len(all_movies),
            "pages": 1,
            "has_prev": False,
            "has_next": False,
            "prev_num": None,
            "next_num": None
        }
    elif not all_movies and (genre_filter or search_query):
        print("Debug - No movies found for filter/search, keeping empty list")
        # Giữ all_movies rỗng và pagination None để hiển thị "Không tìm thấy phim"
    
    if not latest_movies:
        # Fallback demo data to avoid empty list errors in templates
        latest_movies = [
            {
                "id": 1,
                "title": "Hành Tinh Cát: Phần 2",
                "poster": "/static/img/dune2.jpg",
                "backdrop": "/static/img/dune2_backdrop.jpg",
                "description": "Paul và số phận trên Arrakis...",
                "createdAt": "2025-01-01"
            },
            {
                "id": 2,
                "title": "Doctor Strange",
                "poster": "/static/img/doctorstrange.jpg",
                "backdrop": "/static/img/doctorstrange_backdrop.jpg",
                "description": "Bác sĩ Stephen Strange và phép thuật...",
                "createdAt": "2025-01-01"
            },
        ]
    
    print(f"Debug - Final pagination: {pagination}")
    # Lấy lịch sử xem gần đây cho trang chủ (10 phim unique, với thông tin số lần xem)
    recent_watched = []
    if user_id:
        try:
            with current_app.db_engine.connect() as conn:
                # ✅ Query để lấy phim xem gần đây với thông tin:
                # - Số lần xem (watch_count)
                # - Trạng thái hoàn thành (isCompleted - dựa trên lần xem gần nhất)
                # - Lần xem gần nhất (lastWatchedAt)
                history_rows = conn.execute(text("""
                    SELECT TOP 10
                        m.movieId,
                        m.title,
                        m.posterUrl,
                        m.releaseYear,
                        m.durationMin,
                        MAX(vh.startedAt) AS lastWatchedAt,
                        MAX(vh.finishedAt) AS lastFinishedAt,
                        COUNT(vh.historyId) AS watch_count,
                        CASE WHEN MAX(vh.finishedAt) IS NOT NULL THEN 1 ELSE 0 END AS isCompleted,
                        MAX(vh.progressSec) AS lastProgressSec
                    FROM cine.ViewHistory vh
                    JOIN cine.Movie m ON vh.movieId = m.movieId
                    WHERE vh.userId = :user_id
                    GROUP BY m.movieId, m.title, m.posterUrl, m.releaseYear, m.durationMin
                    ORDER BY MAX(vh.startedAt) DESC
                """), {"user_id": user_id}).mappings().all()
                
                # Lưu movie_ids để combine query sau
                history_movie_ids = [row["movieId"] for row in history_rows]
                
                recent_watched = []
                for row in history_rows:
                    # Tính phần trăm hoàn thành dựa trên progress và duration
                    progress_percent = 0
                    if row["durationMin"] and row["durationMin"] > 0 and row["lastProgressSec"]:
                        progress_percent = min(100, (row["lastProgressSec"] / 60.0 / row["durationMin"]) * 100)
                    
                    recent_watched.append({
                        "movieId": row["movieId"],
                        "id": row["movieId"],
                        "title": row["title"],
                        "posterUrl": row["posterUrl"] if row["posterUrl"] and row["posterUrl"] != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={row['title'][:20].replace(' ', '+')}",
                        "poster": row["posterUrl"] if row["posterUrl"] and row["posterUrl"] != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={row['title'][:20].replace(' ', '+')}",
                        "releaseYear": row["releaseYear"],
                        "genres": "",  # Sẽ được update sau khi combine query
                        "lastWatchedAt": row["lastWatchedAt"],
                        "lastFinishedAt": row["lastFinishedAt"],
                        "watchCount": int(row["watch_count"]),
                        "isCompleted": bool(row["isCompleted"]),
                        "progressPercent": round(progress_percent, 1)
                    })
        except Exception as e:
            current_app.logger.error(f"Error loading recent watched: {e}")
            recent_watched = []
            history_movie_ids = []
    
    # ✅ TỐI ƯU: Combine tất cả movie_ids và query genres/ratings một lần
    all_movie_ids_combined = set()
    
    # Thu thập movie_ids từ tất cả sections
    if latest_movies:
        all_movie_ids_combined.update([m.get("id") for m in latest_movies if m.get("id")])
    if carousel_movies:
        all_movie_ids_combined.update([m.get("id") for m in carousel_movies if m.get("id")])
    if personal_recommendations:
        all_movie_ids_combined.update([m.get("id") for m in personal_recommendations if m.get("id")])
    if trending_movies:
        all_movie_ids_combined.update([m.get("id") for m in trending_movies if m.get("id")])
    if recent_watched:
        all_movie_ids_combined.update([m.get("id") for m in recent_watched if m.get("id")])
    if all_movies:
        all_movie_ids_combined.update([m.get("id") for m in all_movies if m.get("id")])
    
    # Query genres và ratings một lần cho tất cả movies
    if all_movie_ids_combined:
        combined_movie_ids = list(all_movie_ids_combined)
        combined_genres_dict = get_movies_genres(combined_movie_ids, current_app.db_engine)
        combined_rating_stats = get_movie_rating_stats(combined_movie_ids, current_app.db_engine)
        
        # Update genres và ratings cho tất cả sections
        # Latest movies
        for movie in latest_movies:
            movie_id = movie.get("id")
            if movie_id:
                movie["genres"] = combined_genres_dict.get(movie_id, movie.get("genres", ""))
                if movie_id in combined_rating_stats:
                    stats = combined_rating_stats[movie_id]
                    movie["avgRating"] = stats.get("avgRating", movie.get("avgRating", 0))
                    movie["ratingCount"] = stats.get("ratingCount", movie.get("ratingCount", 0))
        
        # Carousel movies
        for movie in carousel_movies:
            movie_id = movie.get("id")
            if movie_id:
                movie["genres"] = combined_genres_dict.get(movie_id, movie.get("genres", ""))
                if movie_id in combined_rating_stats:
                    stats = combined_rating_stats[movie_id]
                    movie["avgRating"] = stats.get("avgRating", movie.get("avgRating", 0))
                    movie["ratingCount"] = stats.get("ratingCount", movie.get("ratingCount", 0))
        
        # Personal recommendations
        for movie in personal_recommendations:
            movie_id = movie.get("id")
            if movie_id:
                movie["genres"] = combined_genres_dict.get(movie_id, movie.get("genres", ""))
                if movie_id in combined_rating_stats:
                    stats = combined_rating_stats[movie_id]
                    movie["avgRating"] = stats.get("avgRating", movie.get("avgRating", 0))
                    movie["ratingCount"] = stats.get("ratingCount", movie.get("ratingCount", 0))
        
        # Trending movies
        for movie in trending_movies:
            movie_id = movie.get("id")
            if movie_id:
                movie["genres"] = combined_genres_dict.get(movie_id, movie.get("genres", ""))
                if movie_id in combined_rating_stats:
                    stats = combined_rating_stats[movie_id]
                    movie["avgRating"] = stats.get("avgRating", movie.get("avgRating", 0))
                    movie["ratingCount"] = stats.get("ratingCount", movie.get("ratingCount", 0))
        
        # Recent watched
        for movie in recent_watched:
            movie_id = movie.get("id")
            if movie_id:
                movie["genres"] = combined_genres_dict.get(movie_id, "")
        
        # All movies
        for movie in all_movies:
            movie_id = movie.get("id")
            if movie_id:
                movie["genres"] = combined_genres_dict.get(movie_id, movie.get("genres", ""))
                if movie_id in combined_rating_stats:
                    stats = combined_rating_stats[movie_id]
                    movie["avgRating"] = stats.get("avgRating", movie.get("avgRating", 0))
                    movie["ratingCount"] = stats.get("ratingCount", movie.get("ratingCount", 0))
    
    return render_template("home.html", 
                         latest_movies=latest_movies,  # Phim mới nhất (10 phim, không phân trang)
                         carousel_movies=carousel_movies,  # Carousel phim mới nhất (6 phim)
                         recommended=personal_recommendations,  # Phim đề xuất cá nhân (Collaborative Filtering)
                         trending_movies=trending_movies,  # Phim trending (được đánh giá nhiều nhất)
                         all_movies=all_movies,  # Tất cả phim (có phân trang)
                         recent_watched=recent_watched,  # Phim xem gần đây (10 phim unique với số lần xem)
                         pagination=pagination,
                         genre_filter=genre_filter,
                         search_query=search_query,
                         all_genres=all_genres)  # Tất cả thể loại


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    # Get success message from URL parameter
    success = request.args.get('success', '')
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        
        print(f"Login attempt: username='{username}', password='{password}'")
        
        with current_app.db_engine.connect() as conn:
            # Query kiểm tra đăng nhập với trạng thái user
            test_query = text("""
                SELECT u.userId, u.email, u.status, r.roleName
                FROM cine.Account a
                JOIN cine.[User] u ON u.userId = a.userId
                JOIN cine.Role r ON r.roleId = u.roleId
                WHERE (
                    a.username = :u OR u.email = :u
                ) AND a.passwordHash = HASHBYTES('SHA2_256', CONVERT(VARBINARY(512), :p))
            """)
            
            print(f"Executing query with params: u='{username}', p='{password}'")
            
            try:
                result = conn.execute(test_query, {"u": username, "p": password})
                rows = result.fetchall()
                print(f"Query result: {len(rows)} rows")
                
                if rows:
                    row = rows[0]
                    print(f"Found user: ID={row[0]}, Email={row[1]}, Status={row[2]}, Role={row[3]}")
                    
                    # Kiểm tra trạng thái user
                    if row[2] != "active":
                        print(f"User account is {row[2]}, login blocked")
                        error = "Tài khoản của bạn đã bị chặn. Vui lòng liên hệ quản trị viên."
                    else:
                        session["user_id"] = int(row[0])
                        session["role"] = row[3]
                        session["username"] = username
                        session["email"] = row[1]
                        print(f"Session set: user_id={session['user_id']}, role={session['role']}")
                        return redirect(url_for("main.home"))
                else:
                    print("No user found with these credentials")
                    error = "Sai tài khoản hoặc mật khẩu"
            except Exception as e:
                print(f"Database error: {e}")
                error = f"Lỗi database: {str(e)}"
    return render_template("login.html", error=error, success=success)


@main_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        
        # Validation
        errors = []
        
        # Name validation (1-20 chars, letters, numbers and spaces only, no special characters)
        if not name:
            errors.append("Vui lòng nhập user name.")
        else:
            if len(name) < 1:
                errors.append("User name ít nhất phải có 1 ký tự và không chứa ký tự đặc biệt.")
            elif len(name) > 20:
                errors.append("User name không được quá 20 ký tự.")
            else:
                # Check for letters, numbers and spaces only (no special characters)
                name_pattern = r'^[a-zA-Z0-9\u00C0-\u017F\u1E00-\u1EFF\u0100-\u017F\u0180-\u024F\s]+$'
                if not re.match(name_pattern, name):
                    errors.append("User name ít nhất phải có 1 ký tự và không chứa ký tự đặc biệt.")
        
        # Email validation
        if not email:
            errors.append("Vui lòng nhập email.")
        else:
            # Check email format
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, email):
                errors.append("Email không hợp lệ.")
        
        # Password strength validation
        if not password:
            errors.append("Vui lòng nhập mật khẩu.")
        else:
            if len(password) < 8:
                errors.append("Mật khẩu phải có ít nhất 8 ký tự.")
            elif len(password) > 20:
                errors.append("Mật khẩu không được quá 20 ký tự.")
            # Check for at least one uppercase, one lowercase, and one digit
            if not re.search(r'[A-Z]', password):
                errors.append("Mật khẩu phải chứa ít nhất một chữ in hoa.")
            if not re.search(r'[a-z]', password):
                errors.append("Mật khẩu phải chứa ít nhất một chữ thường.")
            if not re.search(r'[0-9]', password):
                errors.append("Mật khẩu phải chứa ít nhất một số.")
        
        # If there are validation errors, return them
        if errors:
            return render_template("register.html", error="; ".join(errors))
        
        # Check email and username duplicates
        try:
            with current_app.db_engine.connect() as conn:
                # Check email duplicate
                existing_email = conn.execute(text("""
                    SELECT 1 FROM cine.[User] WHERE email = :email
                """), {"email": email}).scalar()
                
                if existing_email:
                    return render_template("register.html", error="Email này đã được sử dụng. Vui lòng đăng nhập hoặc dùng email khác.")
                
                # Check username duplicate
                existing_username = conn.execute(text("""
                    SELECT 1 FROM cine.[Account] WHERE username = :username
                """), {"username": name}).scalar()
                
                if existing_username:
                    return render_template("register.html", error="User name này đã được sử dụng. Vui lòng chọn user name khác.")
        except Exception as check_error:
            print(f"Debug: Error checking duplicates: {check_error}")
        
        try:
            print(f"Debug: Starting registration for email: {email}")
            with current_app.db_engine.begin() as conn:
                # Get role_id for User
                role_id = conn.execute(text("SELECT roleId FROM cine.Role WHERE roleName=N'User'")).scalar()
                print(f"Debug: Found role_id: {role_id}")
                
                if role_id is None:
                    # Get next available roleId
                    max_role_id = conn.execute(text("SELECT ISNULL(MAX(roleId), 0) FROM cine.Role")).scalar()
                    role_id = max_role_id + 1
                    conn.execute(text("INSERT INTO cine.Role(roleId, roleName, description) VALUES (:roleId, N'User', N'Người dùng')"), {"roleId": role_id})
                    print(f"Debug: Created new role with id: {role_id}")
                
                # Insert user - let IDENTITY column auto-generate userId
                print(f"Debug: Inserting user with email: {email}, roleId: {role_id}")
                result = conn.execute(text("""
                    INSERT INTO cine.[User](email, avatarUrl, roleId) 
                    OUTPUT INSERTED.userId
                    VALUES (:email, NULL, :roleId)
                """), {"email": email, "roleId": role_id})
                user_id = result.scalar()
                print(f"Debug: Created user with auto-generated id: {user_id}")
                
                # Insert account - let IDENTITY column auto-generate accountId
                print(f"Debug: Inserting account for user_id: {user_id}, username (name): {name}")
                
                # Save name to username column
                conn.execute(text("""
                    INSERT INTO cine.[Account](username, passwordHash, userId) 
                    VALUES (:u, HASHBYTES('SHA2_256', CONVERT(VARBINARY(512), :p)), :uid)
                """), {"u": name, "p": password, "uid": user_id})
                
                print(f"Debug: Registration completed successfully with username: {name}")
            
            # Redirect to login with success message
            return redirect(url_for("main.login", success="Đăng ký thành công! Bạn có thể đăng nhập ngay."))
        except Exception as ex:
            print(f"Debug: Registration error: {str(ex)}")
            return render_template("register.html", error=f"Không thể đăng ký: {str(ex)}")
    
    return render_template("register.html")


@main_bp.route("/movie/<int:movie_id>")
def detail(movie_id: int):
    if not session.get("user_id"):
        return redirect(url_for("main.login"))
    
    # Lấy tham số phân trang cho related movies
    related_page = request.args.get('related_page', 1, type=int)
    related_per_page = 6
    
    with current_app.db_engine.connect() as conn:
        # Lấy thông tin phim chính
        r = conn.execute(text(
            "SELECT movieId, title, releaseYear, posterUrl, backdropUrl, overview FROM cine.Movie WHERE movieId=:id"
        ), {"id": movie_id}).mappings().first()
        
    if not r:
        return redirect(url_for("main.home"))
                        
        # Lấy genres của phim
    with current_app.db_engine.connect() as conn:
        genres_query = text("""
            SELECT g.name
            FROM cine.MovieGenre mg
            JOIN cine.Genre g ON mg.genreId = g.genreId
            WHERE mg.movieId = :movie_id
        """)
        genres_result = conn.execute(genres_query, {"movie_id": movie_id}).fetchall()
        genres = [{"name": genre[0], "slug": genre[0].lower().replace(' ', '-')} for genre in genres_result]
        
    movie = {
        "id": r["movieId"],
        "title": r["title"],
        "year": r.get("releaseYear"),
            "duration": "120 phút",  # Default duration
            "genres": genres,
            "rating": 5.0,  # Default rating
            "poster": r.get("posterUrl") if r.get("posterUrl") and r.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={r['title'][:20].replace(' ', '+')}",
        "duration": "120 phút",  # Default duration
        "genres": genres,
        "rating": 5.0,  # Default rating
        "poster": r.get("posterUrl") if r.get("posterUrl") and r.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={r['title'][:20].replace(' ', '+')}",
        "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
        "description": r.get("overview") or "",
    }
    
    # CONTENT-BASED: Phim liên quan sử dụng ContentBasedRecommender
    related = []
    related_pagination = None
    # Lấy phim liên quan từ model đã train
    related_movies = []
    try:
        # Tạo recommender instance
        # Sử dụng ContentBasedRecommender để lấy phim liên quan
        recommender = ContentBasedRecommender(current_app.db_engine)
        
        # Lấy tất cả phim liên quan từ model AI (không giới hạn)
        all_related_movies = recommender.get_related_movies(movie_id, limit=100)  # Lấy nhiều hơn để phân trang
        
        # Tính toán pagination
        total_related = len(all_related_movies)
        total_pages = (total_related + related_per_page - 1) // related_per_page
        offset = (related_page - 1) * related_per_page
        
        # Lấy phim cho trang hiện tại
        related_movies = all_related_movies[offset:offset + related_per_page]
        related_movies_raw = recommender.get_related_movies(movie_id, limit=12)
        
        # Format data cho template
        related_movies = [
            {
                "movieId": movie["movieId"],
                "id": movie["movieId"],
                "title": movie["title"],
                "posterUrl": get_poster_or_dummy(movie.get("posterUrl"), movie["title"]),
                "releaseYear": movie.get("releaseYear"),
                "country": movie.get("country"),
                "similarity": movie.get("similarity", 0),
                "genres": movie.get("genres", "")
            }
            for movie in related_movies_raw
        ]
    except Exception as e:
        print(f"Error getting related movies: {e}")
        related_movies = []
    
    # Fallback: lấy phim ngẫu nhiên nếu không có recommendations
    if not related_movies:
        try:
            with current_app.db_engine.connect() as conn:
                fallback_rows = conn.execute(text("""
                    SELECT TOP 8 movieId, title, posterUrl, releaseYear
                    FROM cine.Movie 
                    WHERE movieId != :movie_id
                    ORDER BY NEWID()
                """), {"movie_id": movie_id}).mappings().all()
                
                related_movies = [
                    {
                        "movieId": row["movieId"],
                        "id": row["movieId"],
                        "title": row["title"],
                        "posterUrl": get_poster_or_dummy(row.get("posterUrl"), row["title"]),
                        "releaseYear": row.get("releaseYear"),
                        "country": "Unknown",
                        "similarity": 0.5,
                        "genres": ""
                    }
                    for row in fallback_rows
                ]
        except Exception as e:
            print(f"Error getting fallback movies: {e}")
            related_movies = []
    
    return render_template("detail.html", movie=movie, related_movies=related_movies)

@main_bp.route("/watch/<int:movie_id>")
def watch(movie_id: int):
    # Kiểm tra xem có phải trailer không
    is_trailer = request.args.get('type') == 'trailer'
    
    # Lấy thông tin phim chính (read-only, không cần transaction)
    with current_app.db_engine.connect() as conn:
        r = conn.execute(text(
            "SELECT movieId, title, posterUrl, backdropUrl, releaseYear, overview, trailerUrl, viewCount FROM cine.Movie WHERE movieId=:id"
        ), {"id": movie_id}).mappings().first()
        
    # Tăng view count và lưu lịch sử xem (cần transaction)
    if not is_trailer:
        # Sử dụng begin() để tự động quản lý transaction
        with current_app.db_engine.begin() as conn:
            # Luôn tăng view count mỗi lần load trang (kể cả refresh)
            conn.execute(text(
                "UPDATE cine.Movie SET viewCount = viewCount + 1 WHERE movieId = :id"
            ), {"id": movie_id})
            
            # Lưu lịch sử xem vào database nếu user đã đăng nhập
            user_id = session.get("user_id")
            if user_id:
                try:
                    # Lưu vào ViewHistory
                    # Lấy max historyId để tạo ID mới
                    max_history_id_result = conn.execute(text("""
                        SELECT ISNULL(MAX(historyId), 0) FROM cine.ViewHistory
                    """)).fetchone()
                    max_history_id = max_history_id_result[0] if max_history_id_result else 0
                    new_history_id = max_history_id + 1
                    
                    conn.execute(text("""
                        INSERT INTO cine.ViewHistory (historyId, userId, movieId, startedAt, deviceType, ipAddress, userAgent)
                        VALUES (:history_id, :user_id, :movie_id, GETDATE(), :device_type, :ip_address, :user_agent)
                    """), {
                        "history_id": new_history_id,
                        "user_id": user_id,
                        "movie_id": movie_id,
                        "device_type": request.headers.get('User-Agent', 'Unknown')[:50],
                        "ip_address": request.remote_addr,
                        "user_agent": request.headers.get('User-Agent', '')[:500]
                    })
                    
                    # Cập nhật lastLoginAt trong User table
                    conn.execute(text("""
                        UPDATE cine.[User] SET lastLoginAt = GETDATE() WHERE userId = :user_id
                    """), {"user_id": user_id})
                    
                    # Xóa phim này khỏi recommendations đã lưu (nếu có)
                    # Để đảm bảo phim đã xem không còn trong gợi ý
                    conn.execute(text("""
                        DELETE FROM cine.PersonalRecommendation 
                        WHERE userId = :user_id AND movieId = :movie_id
                    """), {"user_id": user_id, "movie_id": movie_id})
                    
                    # Cũng xóa khỏi ColdStartRecommendations nếu có
                    conn.execute(text("""
                        DELETE FROM cine.ColdStartRecommendations 
                        WHERE userId = :user_id AND movieId = :movie_id
                    """), {"user_id": user_id, "movie_id": movie_id})
                    
                except Exception as e:
                    print(f"Error saving view history: {e}")
            # begin() tự động commit khi exit context thành công
        
    if not r:
        return redirect(url_for("main.home"))
    
    # Lấy thể loại của phim
    with current_app.db_engine.connect() as conn:
        genres_query = text("""
            SELECT g.name
            FROM cine.MovieGenre mg
            JOIN cine.Genre g ON mg.genreId = g.genreId
            WHERE mg.movieId = :movie_id
        """)
        genres_result = conn.execute(genres_query, {"movie_id": movie_id}).fetchall()
        genres = [{"name": genre[0], "slug": genre[0].lower().replace(' ', '-')} for genre in genres_result]
    
    # Xác định video source dựa trên loại (trailer hoặc phim)
    if is_trailer and r.get("trailerUrl"):
        video_sources = [{"label": "Trailer", "url": r.get("trailerUrl")}]
    else:
        video_sources = [{"label": "720p", "url": "https://www.w3schools.com/html/movie.mp4"}]
    
    # Tạo danh sách tập phim (giả lập - có thể mở rộng từ database)
    episodes = []
    # Giả sử phim có 3 tập để demo
    if not is_trailer:  # Chỉ hiển thị tập phim khi xem phim, không phải trailer
        episodes = [
            {"number": 1, "title": "Tập 1", "duration": "45 phút", "url": "https://www.w3schools.com/html/movie.mp4"},
            {"number": 2, "title": "Tập 2", "duration": "42 phút", "url": "https://www.w3schools.com/html/movie.mp4"},
            {"number": 3, "title": "Tập 3", "duration": "48 phút", "url": "https://www.w3schools.com/html/movie.mp4"}
        ]
        
    movie = {
        "id": r["movieId"],
        "title": r["title"],
        "poster": r.get("posterUrl") if r.get("posterUrl") and r.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={r['title'][:20].replace(' ', '+')}",
        "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
        "year": r.get("releaseYear"),
        "overview": r.get("overview") or "",
        "sources": video_sources,
        "genres": genres,
        "episodes": episodes,
        "viewCount": r.get("viewCount", 0),
    }
    
    # Lấy phim liên quan từ model đã train
    related_movies = []
    try:
        # Sử dụng ContentBasedRecommender để lấy phim liên quan
        recommender = ContentBasedRecommender(current_app.db_engine)
        related_movies_raw = recommender.get_related_movies(movie_id, limit=12)
        
        # Format data cho template
        related_movies = [
            {
                "movieId": movie["movieId"],
                "id": movie["movieId"],
                "title": movie["title"],
                "posterUrl": get_poster_or_dummy(movie.get("posterUrl"), movie["title"]),
                "releaseYear": movie.get("releaseYear"),
                "country": movie.get("country"),
                "similarity": movie.get("similarity", 0.0),
                "genres": movie.get("genres", "")
            }
            for movie in related_movies_raw
        ]
    except Exception as e:
        print(f"Error getting related movies: {e}")
        related_movies = []
    
    # Fallback: lấy phim ngẫu nhiên nếu không có recommendations
    if not related_movies:
        try:
            with current_app.db_engine.connect() as conn:
                fallback_rows = conn.execute(text("""
                    SELECT TOP 8 movieId, title, posterUrl, releaseYear
                    FROM cine.Movie 
                    WHERE movieId != :id
                    ORDER BY NEWID()
                """), {"id": movie_id}).mappings().all()
            
            related_movies = [
                {
                    "movieId": row["movieId"],
                    "id": row["movieId"],
                    "title": row["title"],
                    "posterUrl": get_poster_or_dummy(row.get("posterUrl"), row["title"]),
                    "releaseYear": row.get("releaseYear"),
                    "country": "Unknown",
                    "similarity": 0.0,
                    "genres": ""
                }
                for row in fallback_rows
            ]
        except Exception as fallback_error:
            print(f"Fallback error: {fallback_error}")
            # Fallback cuối cùng - tạo dữ liệu demo
            related_movies = [
                {
                    "movieId": 1,
                    "id": 1,
                    "title": "Demo Movie 1",
                    "posterUrl": "https://dummyimage.com/300x450/2c3e50/ecf0f1&text=Demo+1",
                    "releaseYear": 2023,
                    "country": "Unknown",
                    "similarity": 0.0,
                    "genres": ""
                },
                {
                    "movieId": 2,
                    "id": 2,
                    "title": "Demo Movie 2", 
                    "posterUrl": "https://dummyimage.com/300x450/2c3e50/ecf0f1&text=Demo+2",
                    "releaseYear": 2023,
                    "country": "Unknown",
                    "similarity": 0.0,
                    "genres": ""
                }
            ]
    
    return render_template("watch.html", 
                         movie=movie, 
                         related_movies=related_movies)


@main_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.home"))


@main_bp.route("/reset-view-count/<int:movie_id>")
def reset_view_count(movie_id):
    """Reset view count for a specific movie (admin only)"""
    if not session.get("user_id"):
        return redirect(url_for("main.login"))
    
    # Check if user is admin
    with current_app.db_engine.connect() as conn:
        user_role = conn.execute(text("""
            SELECT r.roleName 
            FROM cine.[User] u 
            JOIN cine.Role r ON u.roleId = r.roleId 
            WHERE u.userId = :user_id
        """), {"user_id": session.get("user_id")}).scalar()
    
    if user_role != "Admin":
        flash("Bạn không có quyền thực hiện hành động này!", "error")
        return redirect(url_for("main.home"))
    
    # Reset view count - sử dụng begin() để tự động quản lý transaction
    with current_app.db_engine.begin() as conn:
        conn.execute(text(
            "UPDATE cine.Movie SET viewCount = 0 WHERE movieId = :id"
        ), {"id": movie_id})
        # begin() tự động commit khi exit context thành công
    
    flash(f"Đã reset view count cho phim ID {movie_id}", "success")
    return redirect(url_for("main.admin_movies"))


@main_bp.route("/account")
def account():
    """Trang tài khoản của tôi"""
    if not session.get("user_id"):
        return redirect(url_for("main.login"))
    
    user_id = session.get("user_id")
    
    # Lấy tham số phân trang và tìm kiếm
    watchlist_page = request.args.get('watchlist_page', 1, type=int)
    favorites_page = request.args.get('favorites_page', 1, type=int)
    watchlist_search = request.args.get('watchlist_search', '', type=str).strip()
    favorites_search = request.args.get('favorites_search', '', type=str).strip()
    per_page = 8
    
    # Lấy thông tin user
    try:
        with current_app.db_engine.connect() as conn:
            user_info = conn.execute(text("""
                SELECT u.userId, u.email, u.avatarUrl, u.phone, u.status, u.createdAt, u.lastLoginAt, r.roleName, a.username
                FROM [cine].[User] u
                JOIN [cine].[Role] r ON u.roleId = r.roleId
                LEFT JOIN [cine].[Account] a ON a.userId = u.userId
                WHERE u.userId = :user_id
            """), {"user_id": user_id}).mappings().first()
            
            # Cập nhật session với avatar mới nhất
            if user_info and user_info.avatarUrl:
                session['avatar'] = user_info.avatarUrl
            
            if not user_info:
                return redirect(url_for("main.login"))
            
            # Lấy danh sách xem sau (watchlist) với phân trang và tìm kiếm
            if watchlist_search:
                # Query với tìm kiếm
                watchlist_total = conn.execute(text("""
                    SELECT COUNT(*) 
                    FROM [cine].[Watchlist] wl
                    JOIN [cine].[Movie] m ON wl.movieId = m.movieId
                    WHERE wl.userId = :user_id AND m.title LIKE :search
                """), {"user_id": user_id, "search": f"%{watchlist_search}%"}).scalar()
                
                watchlist_offset = (watchlist_page - 1) * per_page
                watchlist = conn.execute(text("""
                    SELECT m.movieId, m.title, m.posterUrl, m.releaseYear, wl.addedAt
                    FROM [cine].[Watchlist] wl
                    JOIN [cine].[Movie] m ON wl.movieId = m.movieId
                    WHERE wl.userId = :user_id AND m.title LIKE :search
                    ORDER BY 
                        CASE 
                            WHEN m.title LIKE :exact_search THEN 1
                            WHEN m.title LIKE :start_search THEN 2
                            ELSE 3
                        END,
                        wl.addedAt DESC
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """), {
                    "user_id": user_id, 
                    "search": f"%{watchlist_search}%",
                    "exact_search": f"{watchlist_search}%",
                    "start_search": f"{watchlist_search}%",
                    "offset": watchlist_offset, 
                    "per_page": per_page
                }).mappings().all()
            else:
                # Query không tìm kiếm
                watchlist_total = conn.execute(text("""
                    SELECT COUNT(*) FROM [cine].[Watchlist] WHERE userId = :user_id
                """), {"user_id": user_id}).scalar()
                
                watchlist_offset = (watchlist_page - 1) * per_page
                watchlist = conn.execute(text("""
                SELECT m.movieId, m.title, m.posterUrl, m.releaseYear, wl.addedAt
                FROM [cine].[Watchlist] wl
                JOIN [cine].[Movie] m ON wl.movieId = m.movieId
                WHERE wl.userId = :user_id
                ORDER BY wl.addedAt DESC
                OFFSET :offset ROWS
                FETCH NEXT :per_page ROWS ONLY
            """), {"user_id": user_id, "offset": watchlist_offset, "per_page": per_page}).mappings().all()
            
            # Lấy danh sách yêu thích (favorites) với phân trang và tìm kiếm
            if favorites_search:
                # Query với tìm kiếm
                favorites_total = conn.execute(text("""
                    SELECT COUNT(*) 
                    FROM [cine].[Favorite] f
                    JOIN [cine].[Movie] m ON f.movieId = m.movieId
                    WHERE f.userId = :user_id AND m.title LIKE :search
                """), {"user_id": user_id, "search": f"%{favorites_search}%"}).scalar()
                
                favorites_offset = (favorites_page - 1) * per_page
                favorites = conn.execute(text("""
                    SELECT m.movieId, m.title, m.posterUrl, m.releaseYear, f.addedAt
                    FROM [cine].[Favorite] f
                    JOIN [cine].[Movie] m ON f.movieId = m.movieId
                    WHERE f.userId = :user_id AND m.title LIKE :search
                    ORDER BY 
                        CASE 
                            WHEN m.title LIKE :exact_search THEN 1
                            WHEN m.title LIKE :start_search THEN 2
                            ELSE 3
                        END,
                        f.addedAt DESC
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """), {
                    "user_id": user_id, 
                    "search": f"%{favorites_search}%",
                    "exact_search": f"{favorites_search}%",
                    "start_search": f"{favorites_search}%",
                    "offset": favorites_offset, 
                    "per_page": per_page
                }).mappings().all()
            else:
                # Query không tìm kiếm
                favorites_total = conn.execute(text("""
                    SELECT COUNT(*) FROM [cine].[Favorite] WHERE userId = :user_id
                """), {"user_id": user_id}).scalar()
                
                favorites_offset = (favorites_page - 1) * per_page
                favorites = conn.execute(text("""
                SELECT m.movieId, m.title, m.posterUrl, m.releaseYear, f.addedAt
                FROM [cine].[Favorite] f
                JOIN [cine].[Movie] m ON f.movieId = m.movieId
                WHERE f.userId = :user_id
                ORDER BY f.addedAt DESC
                OFFSET :offset ROWS
                FETCH NEXT :per_page ROWS ONLY
            """), {"user_id": user_id, "offset": favorites_offset, "per_page": per_page}).mappings().all()
            
            # Tạo pagination cho watchlist
            watchlist_pages = (watchlist_total + per_page - 1) // per_page
            watchlist_pagination = {
                "page": watchlist_page,
                "per_page": per_page,
                "total": watchlist_total,
                "pages": watchlist_pages,
                "has_prev": watchlist_page > 1,
                "has_next": watchlist_page < watchlist_pages,
                "prev_num": watchlist_page - 1 if watchlist_page > 1 else None,
                "next_num": watchlist_page + 1 if watchlist_page < watchlist_pages else None
            }
            
            # Tạo pagination cho favorites
            favorites_pages = (favorites_total + per_page - 1) // per_page
            favorites_pagination = {
                "page": favorites_page,
                "per_page": per_page,
                "total": favorites_total,
                "pages": favorites_pages,
                "has_prev": favorites_page > 1,
                "has_next": favorites_page < favorites_pages,
                "prev_num": favorites_page - 1 if favorites_page > 1 else None,
                "next_num": favorites_page + 1 if favorites_page < favorites_pages else None
            }
            
    except Exception as e:
        print(f"Error getting account info: {e}")
        user_info = None
        watchlist = []
        favorites = []
        watchlist_pagination = None
        favorites_pagination = None
        watchlist_page = 1
        favorites_page = 1
    
    return render_template("account.html", 
                         user=user_info,
                         watchlist=watchlist,
                         favorites=favorites,
                         watchlist_pagination=watchlist_pagination,
                         favorites_pagination=favorites_pagination,
                         watchlist_page=watchlist_page,
                         favorites_page=favorites_page,
                         watchlist_search=watchlist_search,
                         favorites_search=favorites_search)


@main_bp.route("/account/history")
def account_history():
    """Trang lịch sử xem của user"""
    if not session.get("user_id"):
        return redirect(url_for("main.login"))
    
    user_id = session.get("user_id")
    
    # Lấy tham số phân trang và tìm kiếm
    page = request.args.get('page', 1, type=int)
    search_query = request.args.get('q', '', type=str).strip()
    per_page = 10
    
    try:
        with current_app.db_engine.connect() as conn:
            # Lấy thông tin user
            user_info = conn.execute(text("""
                SELECT u.userId, u.email, u.avatarUrl, u.phone, u.status, u.createdAt, u.lastLoginAt, r.roleName, a.username
                FROM [cine].[User] u
                JOIN [cine].[Role] r ON u.roleId = r.roleId
                LEFT JOIN [cine].[Account] a ON a.userId = u.userId
                WHERE u.userId = :user_id
            """), {"user_id": user_id}).mappings().first()
            
            if not user_info:
                return redirect(url_for("main.login"))
            
            # Lấy lịch sử xem với phân trang và tìm kiếm
            if search_query:
                # Query với tìm kiếm
                history_total = conn.execute(text("""
                    SELECT COUNT(*) 
                    FROM [cine].[ViewHistory] vh
                    JOIN [cine].[Movie] m ON vh.movieId = m.movieId
                    WHERE vh.userId = :user_id AND m.title LIKE :search
                """), {"user_id": user_id, "search": f"%{search_query}%"}).scalar()
                
                history_offset = (page - 1) * per_page
                view_history = conn.execute(text("""
                    SELECT vh.historyId, vh.movieId, vh.startedAt, vh.finishedAt, vh.progressSec,
                           m.title, m.posterUrl, m.releaseYear,
                           STUFF((
                               SELECT ', ' + g2.name
                               FROM cine.MovieGenre mg2
                               JOIN cine.Genre g2 ON mg2.genreId = g2.genreId
                               WHERE mg2.movieId = m.movieId
                               GROUP BY g2.name
                               ORDER BY g2.name
                               FOR XML PATH('')
                           ),1,2,'') AS genres,
                           CASE WHEN vh.finishedAt IS NOT NULL THEN 1 ELSE 0 END AS isCompleted
                    FROM [cine].[ViewHistory] vh
                    JOIN [cine].[Movie] m ON vh.movieId = m.movieId
                    WHERE vh.userId = :user_id AND m.title LIKE :search
                    ORDER BY vh.startedAt DESC
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """), {
                    "user_id": user_id, 
                    "search": f"%{search_query}%",
                    "offset": history_offset, 
                    "per_page": per_page
                }).mappings().all()
            else:
                # Query không tìm kiếm
                history_total = conn.execute(text("""
                    SELECT COUNT(*) FROM [cine].[ViewHistory] WHERE userId = :user_id
                """), {"user_id": user_id}).scalar()
                
                history_offset = (page - 1) * per_page
                view_history = conn.execute(text("""
                    SELECT vh.historyId, vh.movieId, vh.startedAt, vh.finishedAt, vh.progressSec,
                           m.title, m.posterUrl, m.releaseYear,
                           STUFF((
                               SELECT ', ' + g2.name
                               FROM cine.MovieGenre mg2
                               JOIN cine.Genre g2 ON mg2.genreId = g2.genreId
                               WHERE mg2.movieId = m.movieId
                               GROUP BY g2.name
                               ORDER BY g2.name
                               FOR XML PATH('')
                           ),1,2,'') AS genres,
                           CASE WHEN vh.finishedAt IS NOT NULL THEN 1 ELSE 0 END AS isCompleted
                    FROM [cine].[ViewHistory] vh
                    JOIN [cine].[Movie] m ON vh.movieId = m.movieId
                    WHERE vh.userId = :user_id
                    ORDER BY vh.startedAt DESC
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """), {"user_id": user_id, "offset": history_offset, "per_page": per_page}).mappings().all()
            
            # Format dữ liệu
            history_list = []
            for row in view_history:
                history_list.append({
                    "historyId": row["historyId"],
                    "movieId": row["movieId"],
                    "title": row["title"],
                    "posterUrl": row["posterUrl"],
                    "releaseYear": row["releaseYear"],
                    "genres": row["genres"],
                    "startedAt": row["startedAt"],
                    "finishedAt": row["finishedAt"],
                    "progressSec": row["progressSec"],
                    "isCompleted": bool(row["isCompleted"])
                })
            
            # Tạo pagination
            history_pages = (history_total + per_page - 1) // per_page
            pagination = {
                "page": page,
                "per_page": per_page,
                "total": history_total,
                "pages": history_pages,
                "has_prev": page > 1,
                "has_next": page < history_pages,
                "prev_num": page - 1 if page > 1 else None,
                "next_num": page + 1 if page < history_pages else None
            }
            
    except Exception as e:
        print(f"Error getting view history: {e}")
        user_info = None
        history_list = []
        pagination = None
        page = 1
        search_query = ""
    
    return render_template("history.html", 
                         user=user_info,
                         view_history=history_list,
                         pagination=pagination,
                         page=page,
                         search_query=search_query)


@main_bp.route("/remove-history/<int:history_id>", methods=["POST"])
def remove_history(history_id):
    """Xóa phim khỏi lịch sử xem"""
    if not session.get("user_id"):
        return jsonify({"success": False, "message": "Chưa đăng nhập"})
    
    user_id = session.get("user_id")
    
    try:
        with current_app.db_engine.begin() as conn:
            # Kiểm tra quyền sở hữu
            owner = conn.execute(text("""
                SELECT userId FROM [cine].[ViewHistory] 
                WHERE historyId = :history_id
            """), {"history_id": history_id}).scalar()
            
            if not owner:
                return jsonify({"success": False, "message": "Không tìm thấy bản ghi"})
            
            if owner != user_id:
                return jsonify({"success": False, "message": "Bạn không có quyền xóa bản ghi này"})
            
            # Xóa lịch sử
            conn.execute(text("""
                DELETE FROM [cine].[ViewHistory] 
                WHERE historyId = :history_id
            """), {"history_id": history_id})
            
            return jsonify({"success": True, "message": "Đã xóa khỏi lịch sử xem"})
            
    except Exception as e:
        print(f"Error removing from history: {e}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra"})


@main_bp.route("/update-profile", methods=["POST"])
def update_profile():
    """Cập nhật thông tin profile"""
    if not session.get("user_id"):
        return redirect(url_for("main.login"))
    
    user_id = session.get("user_id")
    username = request.form.get("username", "").strip()
    phone = request.form.get("phone", "").strip()
    current_password = request.form.get("current_password", "").strip()
    new_password = request.form.get("new_password", "").strip()
    confirm_password = request.form.get("confirm_password", "").strip()
    
    # Validation
    errors = []
    
    # Username validation (NVARCHAR(100) - max 100 chars, alphanumeric + special chars)
    if username:
        username_pattern = r'^[a-zA-Z0-9._\u00C0-\u017F\u1E00-\u1EFF\u0100-\u017F\u0180-\u024F -]+$'
        if not re.match(username_pattern, username):
            errors.append("Tên người dùng chỉ được chứa chữ cái (có dấu), số, dấu chấm, gạch dưới, gạch ngang và khoảng trắng")
        elif len(username) < 3:
            errors.append("Tên người dùng phải có ít nhất 3 ký tự")
        elif len(username) > 100:
            errors.append("Tên người dùng không được quá 100 ký tự")
    
    # Phone validation (NVARCHAR(20) - max 20 chars, Vietnamese phone format)
    if phone:
        phone_pattern = r'^(\+84|84|0)[1-9][0-9]{8,9}$'
        if not re.match(phone_pattern, phone):
            errors.append("Số điện thoại không hợp lệ. Vui lòng nhập số điện thoại Việt Nam (10-11 số)")
        elif len(phone) > 20:
            errors.append("Số điện thoại không được quá 20 ký tự")
    
    # Password validation
    if new_password:
        if not current_password:
            errors.append("Vui lòng nhập mật khẩu hiện tại")
        elif len(new_password) < 6:
            errors.append("Mật khẩu mới phải có ít nhất 6 ký tự")
        elif len(new_password) > 100:  # Reasonable limit for password
            errors.append("Mật khẩu mới không được quá 100 ký tự")
        elif new_password != confirm_password:
            errors.append("Mật khẩu xác nhận không khớp")
    
    # If there are validation errors, redirect back with error
    if errors:
        flash("; ".join(errors), "error")
        return redirect(url_for("main.account"))
    
    try:
        with current_app.db_engine.begin() as conn:
            # Check if username already exists (if changing username)
            if username:
                existing_username = conn.execute(text("""
                    SELECT 1 FROM [cine].[Account] 
                    WHERE username = :username AND userId != :user_id
                """), {"username": username, "user_id": user_id}).scalar()
                
                if existing_username:
                    flash("Tên người dùng đã được sử dụng bởi người dùng khác", "error")
                    return redirect(url_for("main.account"))
            
            # Verify current password if changing password
            if new_password:
                # Check current password
                password_check = conn.execute(text("""
                    SELECT 1 FROM [cine].[Account] 
                    WHERE userId = :user_id 
                    AND passwordHash = HASHBYTES('SHA2_256', CONVERT(VARBINARY(512), :current_password))
                """), {"user_id": user_id, "current_password": current_password}).scalar()
                
                if not password_check:
                    flash("Mật khẩu hiện tại không đúng", "error")
                    return redirect(url_for("main.account"))
                
                # Update password
                conn.execute(text("""
                    UPDATE [cine].[Account] 
                    SET passwordHash = HASHBYTES('SHA2_256', CONVERT(VARBINARY(512), :new_password))
                    WHERE userId = :user_id
                """), {"new_password": new_password, "user_id": user_id})
            
            # Update username if provided
            if username:
                conn.execute(text("""
                    UPDATE [cine].[Account] 
                    SET username = :username
                    WHERE userId = :user_id
                """), {"username": username, "user_id": user_id})
                
                # Update session with new username
                session['username'] = username
            
            # Update phone
            conn.execute(text("""
                UPDATE [cine].[User] 
                SET phone = :phone
                WHERE userId = :user_id
            """), {"phone": phone if phone else None, "user_id": user_id})
            
            # Xử lý upload avatar nếu có
            if 'avatar' in request.files:
                avatar_file = request.files['avatar']
                if avatar_file and avatar_file.filename:
                    # Lưu file avatar vào thư mục D:\N5\KLTN\WebXemPhim\avatar
                    filename = f"avatar_{user_id}_{int(time.time())}.jpg"
                    avatar_dir = r"D:\N5\KLTN\WebXemPhim\avatar"
                    avatar_file_path = os.path.join(avatar_dir, filename)
                    
                    # Tạo thư mục nếu chưa có
                    os.makedirs(avatar_dir, exist_ok=True)
                    avatar_file.save(avatar_file_path)
                    
                    # Cập nhật đường dẫn avatar trong database (lưu tên file để serve qua route)
                    avatar_url = f"/avatar/{filename}"
                    conn.execute(text("""
                        UPDATE [cine].[User] 
                        SET avatarUrl = :avatar_url
                        WHERE userId = :user_id
                    """), {"avatar_url": avatar_url, "user_id": user_id})
                    
                    # Cập nhật session với avatar mới
                    session['avatar'] = avatar_url
        
        # Success message
        success_msg = "Cập nhật thông tin thành công!"
        if username:
            success_msg += " Tên người dùng đã được cập nhật."
        if new_password:
            success_msg += " Mật khẩu đã được thay đổi."
        if phone:
            success_msg += " Số điện thoại đã được cập nhật."
        
        flash(success_msg, "success")
        return redirect(url_for("main.account"))
        
    except Exception as e:
        print(f"Error updating profile: {e}")
        flash(f"Lỗi khi cập nhật: {str(e)}", "error")
        return redirect(url_for("main.account"))

@main_bp.route('/update-password', methods=['POST'])
def update_password():
    """Cập nhật mật khẩu"""
    if not session.get("user_id"):
        return redirect(url_for("main.login"))
    
    user_id = session.get("user_id")
    current_password = request.form.get("current_password", "").strip()
    new_password = request.form.get("new_password", "").strip()
    confirm_password = request.form.get("confirm_password", "").strip()
    
    # Validation
    errors = []
    
    if not current_password:
        errors.append("Vui lòng nhập mật khẩu hiện tại")
    
    if not new_password:
        errors.append("Vui lòng nhập mật khẩu mới")
    elif len(new_password) < 6:
        errors.append("Mật khẩu mới phải có ít nhất 6 ký tự")
    elif len(new_password) > 100:
        errors.append("Mật khẩu mới không được quá 100 ký tự")
    
    if new_password != confirm_password:
        errors.append("Mật khẩu xác nhận không khớp")
    
    if errors:
        flash("; ".join(errors), "error")
        return redirect(url_for("main.account"))
    
    try:
        with current_app.db_engine.begin() as conn:
            # Verify current password
            password_check = conn.execute(text("""
                SELECT 1 FROM [cine].[Account] 
                WHERE userId = :user_id 
                AND passwordHash = HASHBYTES('SHA2_256', CONVERT(VARBINARY(512), :current_password))
            """), {"user_id": user_id, "current_password": current_password}).scalar()
            
            if not password_check:
                flash("Mật khẩu hiện tại không đúng", "error")
                return redirect(url_for("main.account"))
            
            # Update password
            conn.execute(text("""
                UPDATE [cine].[Account] 
                SET passwordHash = HASHBYTES('SHA2_256', CONVERT(VARBINARY(512), :new_password))
                WHERE userId = :user_id
            """), {"new_password": new_password, "user_id": user_id})
        
        flash("Đổi mật khẩu thành công!", "success")
        return redirect(url_for("main.account"))
        
    except Exception as e:
        current_app.logger.error(f"Error updating password: {str(e)}")
        flash("Có lỗi xảy ra khi đổi mật khẩu", "error")
        return redirect(url_for("main.account"))

@main_bp.route('/api/update-email', methods=['POST'])
def api_update_email():
    """API cập nhật email trực tiếp vào database"""
    if not session.get("user_id"):
        return jsonify({"success": False, "message": "Chưa đăng nhập"}), 401
    
    user_id = session.get("user_id")
    data = request.get_json()
    new_email = data.get('email', '').strip()
    
    # Validation
    if not new_email:
        return jsonify({"success": False, "message": "Vui lòng nhập email"}), 400
    
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, new_email):
        return jsonify({"success": False, "message": "Email không hợp lệ"}), 400
    
    if len(new_email) > 255:
        return jsonify({"success": False, "message": "Email không được quá 255 ký tự"}), 400
    
    try:
        with current_app.db_engine.begin() as conn:
            # Check if email already exists
            existing_email = conn.execute(text("""
                SELECT 1 FROM [cine].[User] 
                WHERE email = :email AND userId != :user_id
            """), {"email": new_email, "user_id": user_id}).scalar()
            
            if existing_email:
                return jsonify({"success": False, "message": "Email đã được sử dụng bởi người dùng khác"}), 400
            
            # Update email
            conn.execute(text("""
                UPDATE [cine].[User] 
                SET email = :email
                WHERE userId = :user_id
            """), {"email": new_email, "user_id": user_id})
        
        return jsonify({"success": True, "message": "Email đã được cập nhật thành công"})
        
    except Exception as e:
        current_app.logger.error(f"Error updating email: {str(e)}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra khi cập nhật email"}), 500

@main_bp.route('/api/update-username', methods=['POST'])
def api_update_username():
    """API cập nhật username trực tiếp vào database"""
    if not session.get("user_id"):
        return jsonify({"success": False, "message": "Chưa đăng nhập"}), 401
    
    user_id = session.get("user_id")
    data = request.get_json()
    new_username = data.get('username', '').strip()
    
    # Validation
    if not new_username:
        return jsonify({"success": False, "message": "Vui lòng nhập tên người dùng"}), 400
    
    username_pattern = r'^[a-zA-Z0-9._\u00C0-\u017F\u1E00-\u1EFF\u0100-\u017F\u0180-\u024F -]+$'
    if not re.match(username_pattern, new_username):
        return jsonify({"success": False, "message": "Tên người dùng chỉ được chứa chữ cái (có dấu), số, dấu chấm, gạch dưới, gạch ngang và khoảng trắng"}), 400
    
    if len(new_username) < 3:
        return jsonify({"success": False, "message": "Tên người dùng phải có ít nhất 3 ký tự"}), 400
    
    if len(new_username) > 100:
        return jsonify({"success": False, "message": "Tên người dùng không được quá 100 ký tự"}), 400
    
    try:
        with current_app.db_engine.begin() as conn:
            # Check if username already exists
            existing_username = conn.execute(text("""
                SELECT 1 FROM [cine].[Account] 
                WHERE username = :username AND userId != :user_id
            """), {"username": new_username, "user_id": user_id}).scalar()
            
            if existing_username:
                return jsonify({"success": False, "message": "Tên người dùng đã được sử dụng bởi người dùng khác"}), 400
            
            # Update username
            conn.execute(text("""
                UPDATE [cine].[Account] 
                SET username = :username
                WHERE userId = :user_id
            """), {"username": new_username, "user_id": user_id})
            
            # Update session
            session['username'] = new_username
        
        return jsonify({"success": True, "message": "Tên người dùng đã được cập nhật thành công"})
        
    except Exception as e:
        current_app.logger.error(f"Error updating username: {str(e)}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra khi cập nhật tên người dùng"}), 500

@main_bp.route('/api/update-phone', methods=['POST'])
def api_update_phone():
    """API cập nhật số điện thoại trực tiếp vào database"""
    if not session.get("user_id"):
        return jsonify({"success": False, "message": "Chưa đăng nhập"}), 401
    
    user_id = session.get("user_id")
    data = request.get_json()
    new_phone = data.get('phone', '').strip()
    
    # Validation
    if new_phone:
        phone_pattern = r'^(\+84|84|0)[1-9][0-9]{8,9}$'
        if not re.match(phone_pattern, new_phone):
            return jsonify({"success": False, "message": "Số điện thoại không hợp lệ. Vui lòng nhập số điện thoại Việt Nam (10-11 số)"}), 400
        
        if len(new_phone) > 20:
            return jsonify({"success": False, "message": "Số điện thoại không được quá 20 ký tự"}), 400
    
    try:
        with current_app.db_engine.begin() as conn:
            # Update phone
            conn.execute(text("""
                UPDATE [cine].[User] 
                SET phone = :phone
                WHERE userId = :user_id
            """), {"phone": new_phone if new_phone else None, "user_id": user_id})
        
        return jsonify({"success": True, "message": "Số điện thoại đã được cập nhật thành công"})
        
    except Exception as e:
        current_app.logger.error(f"Error updating phone: {str(e)}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra khi cập nhật số điện thoại"}), 500


@main_bp.route("/upload-avatar", methods=["POST"])
def upload_avatar():
    """Upload avatar từ header"""
    if not session.get("user_id"):
        return jsonify({"success": False, "error": "Chưa đăng nhập"})
    
    user_id = session.get("user_id")
    
    try:
        if 'avatar' not in request.files:
            return jsonify({"success": False, "error": "Không có file được chọn"})
        
        avatar_file = request.files['avatar']
        if not avatar_file or not avatar_file.filename:
            return jsonify({"success": False, "error": "Không có file được chọn"})
        
        # Validate file type
        if not avatar_file.content_type.startswith('image/'):
            return jsonify({"success": False, "error": "File phải là ảnh"})
        
        # Validate file size (max 5MB)
        avatar_file.seek(0, 2)  # Seek to end
        file_size = avatar_file.tell()
        avatar_file.seek(0)  # Reset to beginning
        
        if file_size > 5 * 1024 * 1024:
            return jsonify({"success": False, "error": "File quá lớn (max 5MB)"})
        
        # Generate unique filename
        filename = f"avatar_{user_id}_{int(time.time())}_{uuid.uuid4().hex[:8]}.jpg"
        avatar_dir = r"D:\N5\KLTN\WebXemPhim\avatar"
        avatar_file_path = os.path.join(avatar_dir, filename)
        
        # Create directory if not exists
        os.makedirs(avatar_dir, exist_ok=True)
        
        # Save file
        avatar_file.save(avatar_file_path)
        
        # Update database
        avatar_url = f"/avatar/{filename}"
        with current_app.db_engine.begin() as conn:
            conn.execute(text("""
                UPDATE [cine].[User] 
                SET avatarUrl = :avatar_url
                WHERE userId = :user_id
            """), {"avatar_url": avatar_url, "user_id": user_id})
        
        # Update session
        session['avatar'] = avatar_url
        
        return jsonify({
            "success": True, 
            "avatar_url": avatar_url,
            "message": "Đổi avatar thành công!"
        })
        
    except Exception as e:
        print(f"Error uploading avatar: {e}")
        return jsonify({"success": False, "error": f"Lỗi server: {str(e)}"})


@main_bp.route("/avatar/<filename>")
def serve_avatar(filename):
    """Serve avatar files"""
    try:
        avatar_dir = r"D:\N5\KLTN\WebXemPhim\avatar"
        avatar_path = os.path.join(avatar_dir, filename)
        
        if os.path.exists(avatar_path):
            from flask import send_file
            return send_file(avatar_path)
        else:
            # Return default avatar if file not found
            default_avatar = os.path.join(current_app.static_folder, 'img', 'avatar_default.png')
            return send_file(default_avatar)
    except Exception as e:
        print(f"Error serving avatar: {e}")
        # Return default avatar on error
        default_avatar = os.path.join(current_app.static_folder, 'img', 'avatar_default.png')
        return send_file(default_avatar)


@main_bp.route("/add-watchlist/<int:movie_id>", methods=["POST"])
def add_watchlist(movie_id):
    """Thêm phim vào danh sách xem sau"""
    if not session.get("user_id"):
        return jsonify({"success": False, "message": "Chưa đăng nhập"})
    
    user_id = session.get("user_id")
    
    try:
        with current_app.db_engine.begin() as conn:
            # Kiểm tra xem phim đã có trong watchlist chưa
            existing = conn.execute(text("""
                SELECT 1 FROM [cine].[Watchlist] 
                WHERE userId = :user_id AND movieId = :movie_id
            """), {"user_id": user_id, "movie_id": movie_id}).scalar()
            
            if not existing:
                # Lấy watchlistId tiếp theo (vì watchlistId không phải IDENTITY)
                result = conn.execute(text("""
                    SELECT ISNULL(MAX(watchlistId), 0) FROM [cine].[Watchlist]
                """)).fetchone()
                next_watchlist_id = (result[0] if result else 0) + 1
                
                conn.execute(text("""
                    INSERT INTO [cine].[Watchlist] (watchlistId, userId, movieId, addedAt, priority, isWatched)
                    VALUES (:watchlist_id, :user_id, :movie_id, GETDATE(), 1, 0)
                """), {
                    "watchlist_id": next_watchlist_id,
                    "user_id": user_id, 
                    "movie_id": movie_id
                })
                
                # Mark CF model as dirty
                try:
                    set_cf_dirty(current_app.db_engine)
                except Exception:
                    pass
                
                return jsonify({"success": True, "message": "Đã thêm vào danh sách xem sau"})
            else:
                return jsonify({"success": False, "message": "Phim đã có trong danh sách xem sau"})
                
    except Exception as e:
        current_app.logger.error(f"Error adding to watchlist for user {user_id}, movie {movie_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Có lỗi xảy ra"})


@main_bp.route("/remove-watchlist/<int:movie_id>", methods=["POST"])
def remove_watchlist(movie_id):
    """Xóa phim khỏi danh sách xem sau"""
    if not session.get("user_id"):
        return jsonify({"success": False, "message": "Chưa đăng nhập"})
    
    user_id = session.get("user_id")
    
    try:
        with current_app.db_engine.begin() as conn:
            conn.execute(text("""
                DELETE FROM [cine].[Watchlist] 
                WHERE userId = :user_id AND movieId = :movie_id
            """), {"user_id": user_id, "movie_id": movie_id})
            
            return jsonify({"success": True, "message": "Đã xóa khỏi danh sách xem sau"})
            
    except Exception as e:
        current_app.logger.error(f"Error removing from watchlist for user {user_id}, movie {movie_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Có lỗi xảy ra"})


@main_bp.route("/check-watchlist/<int:movie_id>", methods=["GET"])
def check_watchlist(movie_id):
    """Kiểm tra trạng thái xem sau của phim"""
    if not session.get("user_id"):
        return jsonify({"success": False, "is_watchlist": False})
    
    user_id = session.get("user_id")
    
    try:
        with current_app.db_engine.connect() as conn:
            # Tạo bảng Watchlist nếu chưa tồn tại
            try:
                conn.execute(text("""
                    IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Watchlist' AND schema_id = SCHEMA_ID('cine'))
                    BEGIN
                        CREATE TABLE [cine].[Watchlist] (
                            [watchlistId] bigint IDENTITY(1,1) NOT NULL,
                            [userId] bigint NOT NULL,
                            [movieId] bigint NOT NULL,
                            [addedAt] datetime2 NOT NULL DEFAULT (sysutcdatetime()),
                            [priority] int NOT NULL DEFAULT ((1)),
                            [notes] nvarchar(500) NULL,
                            [isWatched] bit NOT NULL DEFAULT ((0)),
                            [watchedAt] datetime2 NULL
                        );
                    END
                """))
                print("Debug - Watchlist table created or already exists")
            except Exception as e:
                print(f"Debug - Error creating Watchlist table: {e}")
            
            result = conn.execute(text("""
                SELECT 1 FROM [cine].[Watchlist] 
                WHERE userId = :user_id AND movieId = :movie_id
            """), {"user_id": user_id, "movie_id": movie_id}).scalar()
            
            is_watchlist = result is not None
            return jsonify({"success": True, "is_watchlist": is_watchlist})
            
    except Exception as e:
        current_app.logger.error(f"Error checking watchlist status for user {user_id}, movie {movie_id}: {e}", exc_info=True)
        return jsonify({"success": False, "is_watchlist": False})


@main_bp.route("/toggle-watchlist/<int:movie_id>", methods=["POST"])
def toggle_watchlist(movie_id):
    """Chuyển đổi trạng thái xem sau của phim"""
    if not session.get("user_id"):
        return jsonify({"success": False, "message": "Chưa đăng nhập"})
    
    user_id = session.get("user_id")
    
    try:
        with current_app.db_engine.begin() as conn:
            # Tạo bảng Watchlist nếu chưa tồn tại
            try:
                conn.execute(text("""
                    IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Watchlist' AND schema_id = SCHEMA_ID('cine'))
                    BEGIN
                        CREATE TABLE [cine].[Watchlist] (
                            [watchlistId] bigint IDENTITY(1,1) NOT NULL,
                            [userId] bigint NOT NULL,
                            [movieId] bigint NOT NULL,
                            [addedAt] datetime2 NOT NULL DEFAULT (sysutcdatetime()),
                            [priority] int NOT NULL DEFAULT ((1)),
                            [notes] nvarchar(500) NULL,
                            [isWatched] bit NOT NULL DEFAULT ((0)),
                            [watchedAt] datetime2 NULL
                        );
                    END
                """))
                print("Debug - Watchlist table created or already exists")
            except Exception as e:
                print(f"Debug - Error creating Watchlist table: {e}")
            
            # Kiểm tra xem phim đã có trong watchlist chưa
            existing = conn.execute(text("""
                SELECT 1 FROM [cine].[Watchlist] 
                WHERE userId = :user_id AND movieId = :movie_id
            """), {"user_id": user_id, "movie_id": movie_id}).scalar()
            
            if existing:
                # Xóa khỏi watchlist
                conn.execute(text("""
                    DELETE FROM [cine].[Watchlist] 
                    WHERE userId = :user_id AND movieId = :movie_id
                """), {"user_id": user_id, "movie_id": movie_id})
                
                # Mark CF model as dirty
                try:
                    set_cf_dirty(current_app.db_engine)
                except Exception:
                    pass
                
                return jsonify({
                    "success": True, 
                    "is_watchlist": False,
                    "message": "Đã xóa khỏi danh sách xem sau"
                })
            else:
                # Lấy watchlistId tiếp theo (vì watchlistId không phải IDENTITY)
                result = conn.execute(text("""
                    SELECT ISNULL(MAX(watchlistId), 0) FROM [cine].[Watchlist]
                """)).fetchone()
                next_watchlist_id = (result[0] if result else 0) + 1
                
                conn.execute(text("""
                    INSERT INTO [cine].[Watchlist] (watchlistId, userId, movieId, addedAt, priority, isWatched)
                    VALUES (:watchlist_id, :user_id, :movie_id, GETDATE(), 1, 0)
                """), {
                    "watchlist_id": next_watchlist_id,
                    "user_id": user_id, 
                    "movie_id": movie_id
                })
                
                # Mark CF model as dirty
                try:
                    set_cf_dirty(current_app.db_engine)
                except Exception:
                    pass
                
                return jsonify({
                    "success": True, 
                    "is_watchlist": True,
                    "message": "Đã thêm vào danh sách xem sau"
                })
                
    except Exception as e:
        current_app.logger.error(f"Error toggling watchlist for user {user_id}, movie {movie_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"Có lỗi xảy ra: {str(e)}"})


@main_bp.route("/add-favorite/<int:movie_id>", methods=["POST"])
def add_favorite(movie_id):
    """Thêm phim vào danh sách yêu thích"""
    if not session.get("user_id"):
        return jsonify({"success": False, "message": "Chưa đăng nhập"})
    
    user_id = session.get("user_id")
    
    try:
        with current_app.db_engine.begin() as conn:
            # Kiểm tra xem phim đã có trong favorites chưa
            existing = conn.execute(text("""
                SELECT 1 FROM [cine].[Favorite] 
                WHERE userId = :user_id AND movieId = :movie_id
            """), {"user_id": user_id, "movie_id": movie_id}).scalar()
            
            if not existing:
                # Lấy favoriteId tiếp theo (vì favoriteId không phải IDENTITY)
                result = conn.execute(text("""
                    SELECT ISNULL(MAX(favoriteId), 0) FROM [cine].[Favorite]
                """)).fetchone()
                next_favorite_id = (result[0] if result else 0) + 1
                
                conn.execute(text("""
                    INSERT INTO [cine].[Favorite] (favoriteId, userId, movieId, addedAt)
                    VALUES (:favorite_id, :user_id, :movie_id, GETDATE())
                """), {
                    "favorite_id": next_favorite_id,
                    "user_id": user_id, 
                    "movie_id": movie_id
                })
                
                # Mark CF model as dirty
                try:
                    set_cf_dirty(current_app.db_engine)
                except Exception:
                    pass
                
                return jsonify({"success": True, "message": "Đã thêm vào danh sách yêu thích"})
            else:
                return jsonify({"success": False, "message": "Phim đã có trong danh sách yêu thích"})
                
    except Exception as e:
        current_app.logger.error(f"Error adding to favorites for user {user_id}, movie {movie_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Có lỗi xảy ra"})


@main_bp.route("/remove-favorite/<int:movie_id>", methods=["POST"])
def remove_favorite(movie_id):
    """Xóa phim khỏi danh sách yêu thích"""
    if not session.get("user_id"):
        return jsonify({"success": False, "message": "Chưa đăng nhập"})
    
    user_id = session.get("user_id")
    
    try:
        with current_app.db_engine.begin() as conn:
            conn.execute(text("""
                DELETE FROM [cine].[Favorite] 
                WHERE userId = :user_id AND movieId = :movie_id
            """), {"user_id": user_id, "movie_id": movie_id})
            
            return jsonify({"success": True, "message": "Đã xóa khỏi danh sách yêu thích"})
            
    except Exception as e:
        current_app.logger.error(f"Error removing from favorites for user {user_id}, movie {movie_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Có lỗi xảy ra"})


@main_bp.route("/api/search-watchlist", methods=["GET"])
def api_search_watchlist():
    """API tìm kiếm watchlist với AJAX"""
    if not session.get("user_id"):
        return jsonify({"success": False, "message": "Chưa đăng nhập"})
    
    user_id = session.get("user_id")
    search_query = request.args.get('q', '', type=str).strip()
    page = request.args.get('page', 1, type=int)
    per_page = 8
    
    try:
        with current_app.db_engine.connect() as conn:
            if search_query:
                # Query với tìm kiếm
                watchlist_total = conn.execute(text("""
                    SELECT COUNT(*) 
                    FROM [cine].[Watchlist] wl
                    JOIN [cine].[Movie] m ON wl.movieId = m.movieId
                    WHERE wl.userId = :user_id AND m.title LIKE :search
                """), {"user_id": user_id, "search": f"%{search_query}%"}).scalar()
                
                watchlist_offset = (page - 1) * per_page
                watchlist = conn.execute(text("""
                    SELECT m.movieId, m.title, m.posterUrl, m.releaseYear, wl.addedAt
                    FROM [cine].[Watchlist] wl
                    JOIN [cine].[Movie] m ON wl.movieId = m.movieId
                    WHERE wl.userId = :user_id AND m.title LIKE :search
                    ORDER BY 
                        CASE 
                            WHEN m.title LIKE :exact_search THEN 1
                            WHEN m.title LIKE :start_search THEN 2
                            ELSE 3
                        END,
                        wl.addedAt DESC
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """), {
                    "user_id": user_id, 
                    "search": f"%{search_query}%",
                    "exact_search": f"{search_query}%",
                    "start_search": f"{search_query}%",
                    "offset": watchlist_offset, 
                    "per_page": per_page
                }).mappings().all()
            else:
                # Query không tìm kiếm
                watchlist_total = conn.execute(text("""
                    SELECT COUNT(*) FROM [cine].[Watchlist] WHERE userId = :user_id
                """), {"user_id": user_id}).scalar()
                
                watchlist_offset = (page - 1) * per_page
                watchlist = conn.execute(text("""
                    SELECT m.movieId, m.title, m.posterUrl, m.releaseYear, wl.addedAt
                    FROM [cine].[Watchlist] wl
                    JOIN [cine].[Movie] m ON wl.movieId = m.movieId
                    WHERE wl.userId = :user_id
                    ORDER BY wl.addedAt DESC
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """), {
                    "user_id": user_id,
                    "offset": watchlist_offset, 
                    "per_page": per_page
                }).mappings().all()
            
            # Tính toán pagination
            total_pages = (watchlist_total + per_page - 1) // per_page
            pagination = {
                "current_page": page,
                "total_pages": total_pages,
                "total_items": watchlist_total,
                "per_page": per_page,
                "has_prev": page > 1,
                "has_next": page < total_pages
            }
            
            # Format dữ liệu
            movies = []
            for movie in watchlist:
                movies.append({
                    "id": movie["movieId"],
                    "title": movie["title"],
                    "poster": movie.get("posterUrl") if movie.get("posterUrl") and movie.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={movie['title'][:20].replace(' ', '+')}",
                    "year": movie.get("releaseYear"),
                    "addedAt": movie.get("addedAt").strftime('%d/%m/%Y') if movie.get("addedAt") else 'N/A'
                })
            
            return jsonify({
                "success": True,
                "movies": movies,
                "pagination": pagination,
                "search_query": search_query
            })
            
    except Exception as e:
        user_id = session.get("user_id")
        current_app.logger.error(f"Error searching watchlist for user {user_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Có lỗi xảy ra"})


@main_bp.route("/check-favorite/<int:movie_id>", methods=["GET"])
def check_favorite(movie_id):
    """Kiểm tra trạng thái yêu thích của phim"""
    if not session.get("user_id"):
        return jsonify({"success": False, "is_favorite": False})
    
    user_id = session.get("user_id")
    
    try:
        with current_app.db_engine.connect() as conn:
            result = conn.execute(text("""
                SELECT 1 FROM [cine].[Favorite] 
                WHERE userId = :user_id AND movieId = :movie_id
            """), {"user_id": user_id, "movie_id": movie_id}).scalar()
            
            is_favorite = result is not None
            return jsonify({"success": True, "is_favorite": is_favorite})
            
    except Exception as e:
        user_id = session.get("user_id")
        current_app.logger.error(f"Error checking favorite status for user {user_id}, movie {movie_id}: {e}", exc_info=True)
        return jsonify({"success": False, "is_favorite": False})


@main_bp.route("/toggle-favorite/<int:movie_id>", methods=["POST"])
def toggle_favorite(movie_id):
    """Chuyển đổi trạng thái yêu thích của phim"""
    if not session.get("user_id"):
        return jsonify({"success": False, "message": "Chưa đăng nhập"})
    
    user_id = session.get("user_id")
    
    try:
        with current_app.db_engine.begin() as conn:
            # Kiểm tra xem phim đã có trong favorites chưa
            existing = conn.execute(text("""
                SELECT 1 FROM [cine].[Favorite] 
                WHERE userId = :user_id AND movieId = :movie_id
            """), {"user_id": user_id, "movie_id": movie_id}).scalar()
            
            if existing:
                # Xóa khỏi favorites
                conn.execute(text("""
                    DELETE FROM [cine].[Favorite] 
                    WHERE userId = :user_id AND movieId = :movie_id
                """), {"user_id": user_id, "movie_id": movie_id})
                
                # Mark CF model as dirty
                try:
                    set_cf_dirty(current_app.db_engine)
                except Exception:
                    pass
                
                return jsonify({
                    "success": True, 
                    "is_favorite": False,
                    "message": "Đã xóa khỏi danh sách yêu thích"
                })
            else:
                # Lấy favoriteId tiếp theo (vì favoriteId không phải IDENTITY)
                result = conn.execute(text("""
                    SELECT ISNULL(MAX(favoriteId), 0) FROM [cine].[Favorite]
                """)).fetchone()
                next_favorite_id = (result[0] if result else 0) + 1
                
                conn.execute(text("""
                    INSERT INTO [cine].[Favorite] (favoriteId, userId, movieId, addedAt)
                    VALUES (:favorite_id, :user_id, :movie_id, GETDATE())
                """), {
                    "favorite_id": next_favorite_id,
                    "user_id": user_id, 
                    "movie_id": movie_id
                })
                
                # Mark CF model as dirty
                try:
                    set_cf_dirty(current_app.db_engine)
                except Exception:
                    pass
                
                return jsonify({
                    "success": True, 
                    "is_favorite": True,
                    "message": "Đã thêm vào danh sách yêu thích"
                })
                
    except Exception as e:
        current_app.logger.error(f"Error toggling favorite for user {user_id}, movie {movie_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"Có lỗi xảy ra: {str(e)}"})


@main_bp.route("/api/search-favorites", methods=["GET"])
def api_search_favorites():
    """API tìm kiếm favorites với AJAX"""
    if not session.get("user_id"):
        return jsonify({"success": False, "message": "Chưa đăng nhập"})
    
    user_id = session.get("user_id")
    search_query = request.args.get('q', '', type=str).strip()
    page = request.args.get('page', 1, type=int)
    per_page = 8
    
    try:
        with current_app.db_engine.connect() as conn:
            if search_query:
                # Query với tìm kiếm
                favorites_total = conn.execute(text("""
                    SELECT COUNT(*) 
                    FROM [cine].[Favorite] f
                    JOIN [cine].[Movie] m ON f.movieId = m.movieId
                    WHERE f.userId = :user_id AND m.title LIKE :search
                """), {"user_id": user_id, "search": f"%{search_query}%"}).scalar()
                
                favorites_offset = (page - 1) * per_page
                favorites = conn.execute(text("""
                    SELECT m.movieId, m.title, m.posterUrl, m.releaseYear, f.addedAt
                    FROM [cine].[Favorite] f
                    JOIN [cine].[Movie] m ON f.movieId = m.movieId
                    WHERE f.userId = :user_id AND m.title LIKE :search
                    ORDER BY 
                        CASE 
                            WHEN m.title LIKE :exact_search THEN 1
                            WHEN m.title LIKE :start_search THEN 2
                            ELSE 3
                        END,
                        f.addedAt DESC
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """), {
                    "user_id": user_id, 
                    "search": f"%{search_query}%",
                    "exact_search": f"{search_query}%",
                    "start_search": f"{search_query}%",
                    "offset": favorites_offset, 
                    "per_page": per_page
                }).mappings().all()
            else:
                # Query không tìm kiếm
                favorites_total = conn.execute(text("""
                    SELECT COUNT(*) FROM [cine].[Favorite] WHERE userId = :user_id
                """), {"user_id": user_id}).scalar()
                
                favorites_offset = (page - 1) * per_page
                favorites = conn.execute(text("""
                    SELECT m.movieId, m.title, m.posterUrl, m.releaseYear, f.addedAt
                    FROM [cine].[Favorite] f
                    JOIN [cine].[Movie] m ON f.movieId = m.movieId
                    WHERE f.userId = :user_id
                    ORDER BY f.addedAt DESC
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """), {"user_id": user_id, "offset": favorites_offset, "per_page": per_page}).mappings().all()
            
            # Tạo pagination
            favorites_pages = (favorites_total + per_page - 1) // per_page
            pagination = {
                "page": page,
                "per_page": per_page,
                "total": favorites_total,
                "pages": favorites_pages,
                "has_prev": page > 1,
                "has_next": page < favorites_pages,
                "prev_num": page - 1 if page > 1 else None,
                "next_num": page + 1 if page < favorites_pages else None
            }
            
            # Format data
            movies = []
            for movie in favorites:
                movies.append({
                    "id": movie["movieId"],
                    "title": movie["title"],
                    "poster": movie.get("posterUrl") if movie.get("posterUrl") and movie.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={movie['title'][:20].replace(' ', '+')}",
                    "year": movie.get("releaseYear"),
                    "addedAt": movie.get("addedAt").strftime('%d/%m/%Y') if movie.get("addedAt") else 'N/A'
                })
            
            return jsonify({
                "success": True,
                "movies": movies,
                "pagination": pagination,
                "search_query": search_query
            })
            
    except Exception as e:
        user_id = session.get("user_id")
        current_app.logger.error(f"Error searching favorites for user {user_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Có lỗi xảy ra"})






# ==================== ADMIN ROUTES ====================

@main_bp.route("/admin")
@admin_required
def admin_dashboard():
    """Trang chủ admin"""
    try:
        with current_app.db_engine.connect() as conn:
            # Lấy thống kê tổng quan
            total_movies = conn.execute(text("SELECT COUNT(*) FROM cine.Movie")).scalar()
            total_users = conn.execute(text("SELECT COUNT(*) FROM cine.[User]")).scalar()
            total_views = conn.execute(text("SELECT SUM(viewCount) FROM cine.Movie")).scalar() or 0
            active_users = conn.execute(text("SELECT COUNT(*) FROM cine.[User] WHERE status = 'active'")).scalar()
            
            # Lấy thống kê bổ sung
            recent_movies = conn.execute(text("""
                SELECT TOP 5 movieId, title, createdAt 
                FROM cine.Movie 
                ORDER BY createdAt DESC
            """)).mappings().all()
            
            recent_users = conn.execute(text("""
                SELECT TOP 5 userId, email, createdAt 
                FROM cine.[User] 
                ORDER BY createdAt DESC
            """)).mappings().all()
            
            # Thống kê theo thể loại
            genre_stats = conn.execute(text("""
                SELECT g.name, COUNT(mg.movieId) as movie_count
                FROM cine.Genre g
                LEFT JOIN cine.MovieGenre mg ON g.genreId = mg.genreId
                GROUP BY g.genreId, g.name
                ORDER BY movie_count DESC
            """)).mappings().all()
            
        return render_template("admin_dashboard.html", 
                             total_movies=total_movies,
                             total_users=total_users,
                             total_views=total_views,
                             active_users=active_users,
                             recent_movies=recent_movies,
                             recent_users=recent_users,
                             genre_stats=genre_stats)
    except Exception as e:
        print(f"Error getting admin dashboard stats: {e}")
        return render_template("admin_dashboard.html", 
                             total_movies=0,
                             total_users=0,
                             total_views=0,
                             active_users=0,
                             recent_movies=[],
                             recent_users=[],
                             genre_stats=[])

@main_bp.route("/admin/movies")
@admin_required
def admin_movies():
    """Quản lý phim với tìm kiếm và phân trang"""
    # Lấy tham số từ URL
    page = request.args.get('page', 1, type=int)
    per_page = 50  # Chỉ hiển thị 50 phim mỗi trang
    search_query = request.args.get('q', '').strip()
    
    try:
        with current_app.db_engine.connect() as conn:
            if search_query:
                # Tìm kiếm phim theo từ khóa
                # Đếm tổng số kết quả tìm kiếm
                total_count = conn.execute(text("""
                    SELECT COUNT(*) 
                    FROM cine.Movie 
                    WHERE title LIKE :query
                """), {"query": f"%{search_query}%"}).scalar()
                
                # Tính toán phân trang
                total_pages = (total_count + per_page - 1) // per_page
                offset = (page - 1) * per_page
                
                # Lấy kết quả tìm kiếm
                movies = conn.execute(text("""
                    SELECT movieId, title, releaseYear, posterUrl, viewCount, createdAt
                    FROM (
                        SELECT movieId, title, releaseYear, posterUrl, viewCount, createdAt,
                               ROW_NUMBER() OVER (
                                   ORDER BY 
                                       CASE 
                                           WHEN title LIKE :exact_query THEN 1
                                           WHEN title LIKE :start_query THEN 2
                                           ELSE 3
                                       END,
                                       createdAt DESC
                               ) as rn
                        FROM cine.Movie 
                        WHERE title LIKE :query
                    ) t
                    WHERE rn > :offset AND rn <= :offset + :per_page
                """), {
                    "query": f"%{search_query}%",
                    "exact_query": f"{search_query}%",
                    "start_query": f"{search_query}%",
                    "offset": offset,
                    "per_page": per_page
                }).mappings().all()
            else:
                # Lấy phim mới nhất với phân trang
                # Đếm tổng số phim
                total_count = conn.execute(text("SELECT COUNT(*) FROM cine.Movie")).scalar()
                
                # Tính toán phân trang
                total_pages = (total_count + per_page - 1) // per_page
                offset = (page - 1) * per_page
                
                # Lấy phim với phân trang
                movies = conn.execute(text("""
                    SELECT movieId, title, releaseYear, posterUrl, viewCount, createdAt
                    FROM (
                        SELECT movieId, title, releaseYear, posterUrl, viewCount, createdAt,
                               ROW_NUMBER() OVER (ORDER BY createdAt DESC, movieId DESC) as rn
                        FROM cine.Movie
                    ) t
                    WHERE rn > :offset AND rn <= :offset + :per_page
                """), {"offset": offset, "per_page": per_page}).mappings().all()
            
            # Tạo pagination info
            pagination = {
                "page": page,
                "per_page": per_page,
                "total": total_count,
                "pages": total_pages,
                "has_prev": page > 1,
                "has_next": page < total_pages,
                "prev_num": page - 1 if page > 1 else None,
                "next_num": page + 1 if page < total_pages else None
            }
            
        return render_template("admin_movies.html", 
                             movies=movies, 
                             pagination=pagination,
                             search_query=search_query)
    except Exception as e:
        flash(f"Lỗi khi tải danh sách phim: {str(e)}", "error")
        return render_template("admin_movies.html", 
                             movies=[], 
                             pagination=None,
                             search_query=search_query)

@main_bp.route("/admin/movies/create", methods=["GET", "POST"])
@admin_required
def admin_movie_create():
    """Tạo phim mới với validation đầy đủ"""
    from datetime import datetime
    
    if request.method == "POST":
        # Lấy dữ liệu từ form
        title = request.form.get("title", "").strip()
        release_year = request.form.get("release_year", "").strip()
        country = request.form.get("country", "").strip()
        overview = request.form.get("overview", "").strip()
        director = request.form.get("director", "").strip()
        cast = request.form.get("cast", "").strip()
        imdb_rating = request.form.get("imdb_rating", "").strip()
        trailer_url = request.form.get("trailer_url", "").strip()
        poster_url = request.form.get("poster_url", "").strip()
        backdrop_url = request.form.get("backdrop_url", "").strip()
        view_count = request.form.get("view_count", "0").strip()
        selected_genres = request.form.getlist("genres")
        
        # Validation
        errors = []
        
        # 1. Title validation (required, max 300 chars, no special chars except basic punctuation)
        if not title:
            errors.append("Tiêu đề phim là bắt buộc")
        elif len(title) > 300:
            errors.append("Tiêu đề phim không được quá 300 ký tự")
        elif not re.match(r'^[a-zA-Z0-9\s\-.,:!?()]+$', title):
            errors.append("Tiêu đề phim chỉ được chứa chữ cái, số và dấu câu cơ bản")
        
        # 2. Release Year validation (1900-2030)
        if release_year:
            try:
                year = int(release_year)
                if year < 1900 or year > 2030:
                    errors.append("Năm phát hành phải trong khoảng 1900-2030")
            except ValueError:
                errors.append("Năm phát hành phải là số hợp lệ")
        else:
            year = None
        
        # 3. Country validation (max 80 chars, letters and spaces only)
        if country and len(country) > 80:
            errors.append("Tên quốc gia không được quá 80 ký tự")
        elif country and not re.match(r'^[a-zA-Z\s]+$', country):
            errors.append("Tên quốc gia chỉ được chứa chữ cái và khoảng trắng")
        
        # 4. Director validation (max 200 chars)
        if director and len(director) > 200:
            errors.append("Tên đạo diễn không được quá 200 ký tự")
        elif director and not re.match(r'^[a-zA-Z\s.,]+$', director):
            errors.append("Tên đạo diễn chỉ được chứa chữ cái, khoảng trắng và dấu câu")
        
        # 5. Cast validation (max 500 chars)
        if cast and len(cast) > 500:
            errors.append("Tên diễn viên không được quá 500 ký tự")
        elif cast and not re.match(r'^[a-zA-Z\s.,]+$', cast):
            errors.append("Tên diễn viên chỉ được chứa chữ cái, khoảng trắng và dấu câu")
        
        # 6. IMDb Rating validation (0.0-10.0)
        if imdb_rating:
            try:
                rating = float(imdb_rating)
                if rating < 0.0 or rating > 10.0:
                    errors.append("Điểm IMDb phải trong khoảng 0.0-10.0")
            except ValueError:
                errors.append("Điểm IMDb phải là số thập phân hợp lệ")
        else:
            rating = None
        
        # 7. URL validation
        url_pattern = r'^https?://[^\s/$.?#].[^\s]*$'
        
        if trailer_url and not re.match(url_pattern, trailer_url):
            errors.append("Trailer URL phải là địa chỉ web hợp lệ (bắt đầu bằng http:// hoặc https://)")
        
        if poster_url and not re.match(url_pattern, poster_url):
            errors.append("Poster URL phải là địa chỉ web hợp lệ (bắt đầu bằng http:// hoặc https://)")
        
        if backdrop_url and not re.match(url_pattern, backdrop_url):
            errors.append("Backdrop URL phải là địa chỉ web hợp lệ (bắt đầu bằng http:// hoặc https://)")
        
        # 8. View Count validation
        try:
            views = int(view_count) if view_count else 0
            if views < 0:
                errors.append("Lượt xem phải là số dương")
        except ValueError:
            errors.append("Lượt xem phải là số hợp lệ")
        
        # 9. Genres validation
        if not selected_genres:
            errors.append("Vui lòng chọn ít nhất một thể loại")
        
        # Nếu có lỗi, hiển thị lại form với lỗi
        if errors:
            try:
                with current_app.db_engine.connect() as conn:
                    all_genres = conn.execute(text("SELECT genreId, name FROM cine.Genre ORDER BY name")).mappings().all()
                return render_template("admin_movie_form.html", 
                                     all_genres=all_genres,
                                     errors=errors,
                                     form_data=request.form)
            except Exception as e:
                flash(f"Lỗi khi tải thể loại: {str(e)}", "error")
                return render_template("admin_movie_form.html", errors=errors, form_data=request.form)
        
        # Lưu vào database
        try:
            with current_app.db_engine.begin() as conn:
                # Tạo phim mới
                result = conn.execute(text("""
                    INSERT INTO cine.Movie (title, releaseYear, country, overview, director, cast, 
                                          imdbRating, trailerUrl, posterUrl, backdropUrl, viewCount)
                    VALUES (:title, :year, :country, :overview, :director, :cast, 
                            :rating, :trailer, :poster, :backdrop, :views)
                """), {
                    "title": title,
                    "year": year,
                    "country": country if country else None,
                    "overview": overview if overview else None,
                    "director": director if director else None,
                    "cast": cast if cast else None,
                    "rating": rating,
                    "trailer": trailer_url if trailer_url else None,
                    "poster": poster_url if poster_url else None,
                    "backdrop": backdrop_url if backdrop_url else None,
                    "views": views
                })
                
                # Lấy movieId vừa tạo
                movie_id = result.lastrowid
                
                # Thêm thể loại cho phim
                for genre_id in selected_genres:
                    if genre_id:
                        conn.execute(text("""
                            INSERT INTO cine.MovieGenre (movieId, genreId) 
                            VALUES (:movieId, :genreId)
                        """), {"movieId": movie_id, "genreId": int(genre_id)})
                
                flash("✅ Thêm phim thành công!", "success")
                return redirect(url_for("main.admin_movies"))
    
        except Exception as e:
            flash(f"❌ Lỗi khi thêm phim: {str(e)}", "error")
            try:
                with current_app.db_engine.connect() as conn:
                    all_genres = conn.execute(text("SELECT genreId, name FROM cine.Genre ORDER BY name")).mappings().all()
                    return render_template("admin_movie_form.html", 
                                         all_genres=all_genres,
                                         form_data=request.form)
            except:
                return render_template("admin_movie_form.html", form_data=request.form)
    
    # GET request - hiển thị form tạo mới
    try:
        with current_app.db_engine.connect() as conn:
            all_genres = conn.execute(text("SELECT genreId, name FROM cine.Genre ORDER BY name")).mappings().all()
        return render_template("admin_movie_form.html", all_genres=all_genres)
    except Exception as e:
        flash(f"Lỗi khi tải thể loại: {str(e)}", "error")
        return render_template("admin_movie_form.html", all_genres=[])

@main_bp.route("/admin/movies/<int:movie_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_movie_edit(movie_id):
    """Sửa phim"""
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        release_year = request.form.get("release_year", "").strip()
        overview = request.form.get("overview", "").strip()
        poster_url = request.form.get("poster_url", "").strip()
        backdrop_url = request.form.get("backdrop_url", "").strip()
        
        if not title:
            flash("Vui lòng nhập tên phim.", "error")
            return redirect(url_for("main.admin_movie_edit", movie_id=movie_id))
        
        try:
            with current_app.db_engine.begin() as conn:
                conn.execute(text("""
                UPDATE cine.Movie
                    SET title = :title, releaseYear = :year, overview = :overview, 
                        posterUrl = :poster, backdropUrl = :backdrop
                    WHERE movieId = :id
                """), {
                    "id": movie_id,
                    "title": title,
                    "year": int(release_year) if release_year else None,
                    "overview": overview,
                    "poster": poster_url,
                    "backdrop": backdrop_url
                })
                
                flash("Cập nhật phim thành công!", "success")
                return redirect(url_for("main.admin_movies"))
        except Exception as e:
            flash(f"Lỗi khi cập nhật phim: {str(e)}", "error")
    
        # Lấy thông tin phim
    try:
        with current_app.db_engine.connect() as conn:
            movie = conn.execute(text("""
                SELECT movieId, title, releaseYear, overview, posterUrl, backdropUrl
                FROM cine.Movie WHERE movieId = :id
            """), {"id": movie_id}).mappings().first()
            
        if not movie:
            flash("Không tìm thấy phim.", "error")
            return redirect(url_for("main.admin_movies"))
            
        return render_template("admin_movie_form.html", movie=movie)
    except Exception as e:
        flash(f"Lỗi khi tải thông tin phim: {str(e)}", "error")
        return redirect(url_for("main.admin_movies"))

@main_bp.route("/admin/movies/<int:movie_id>/delete", methods=["POST"]) 
@admin_required
def admin_movie_delete(movie_id):
    """Xóa phim"""
    try:
        with current_app.db_engine.begin() as conn:
            conn.execute(text("DELETE FROM cine.Movie WHERE movieId = :id"), {"id": movie_id})

        flash("Xóa phim thành công!", "success")
    except Exception as e:
        flash(f"Lỗi khi xóa phim: {str(e)}", "error")

    return redirect(url_for("main.admin_movies"))

@main_bp.route("/admin/model")
@admin_required
def admin_model():
    """Admin page để quản lý model"""
    return render_template("admin_model.html")

@main_bp.route("/admin/users/test")
@admin_required
def admin_users_test():
    """Test route để kiểm tra database"""
    try:
        with current_app.db_engine.connect() as conn:
            # Test query đơn giản
            result = conn.execute(text("SELECT COUNT(*) FROM cine.[User]")).scalar()
            print(f"Total users in database: {result}")
            
            # Test query với JOIN
            users = conn.execute(text("""
                SELECT u.userId, u.email, u.status, r.roleName
                FROM cine.[User] u
                JOIN cine.Role r ON r.roleId = u.roleId
                ORDER BY u.userId
            """)).mappings().all()
            
            print(f"Found {len(users)} users with roles:")
            for user in users:
                print(f"  - ID: {user.userId}, Email: {user.email}, Status: {user.status}, Role: {user.roleName}")
            
            return f"Test successful! Found {len(users)} users. Check console for details."
    except Exception as e:
        print(f"Database error: {e}")
        return f"Database error: {str(e)}"

@main_bp.route("/debug/users")
def debug_users():
    """Debug route không cần admin để test database"""
    try:
        with current_app.db_engine.connect() as conn:
            users = conn.execute(text("""
                SELECT u.userId, u.email, u.status, u.createdAt, u.lastLoginAt, r.roleName,
                       a.username
                FROM cine.[User] u
                JOIN cine.Role r ON r.roleId = u.roleId
                LEFT JOIN cine.Account a ON a.userId = u.userId
                ORDER BY u.createdAt DESC
            """)).mappings().all()
            
            result = f"<h1>Debug Users</h1><p>Found {len(users)} users:</p><ul>"
            for user in users:
                result += f"<li>ID: {user.userId}, Email: {user.email}, Status: {user.status}, Role: {user.roleName}, Username: {user.username or 'None'}</li>"
            result += "</ul>"
            return result
    except Exception as e:
        return f"<h1>Error</h1><p>{str(e)}</p>"

@main_bp.route("/admin/users/simple")
@admin_required
def admin_users_simple():
    """Route đơn giản để test template"""
    try:
        with current_app.db_engine.connect() as conn:
            users = conn.execute(text("""
                SELECT u.userId, u.email, u.status, u.createdAt, u.lastLoginAt, r.roleName,
                       a.username
                FROM cine.[User] u
                JOIN cine.Role r ON r.roleId = u.roleId
                LEFT JOIN cine.Account a ON a.userId = u.userId
                ORDER BY u.createdAt DESC
            """)).mappings().all()
            
            print(f"Simple query found {len(users)} users")
            for user in users:
                print(f"User: {dict(user)}")
            
            return render_template("admin_users.html", 
                                 users=users, 
                                 pagination=None,
                                 search_query="")
    except Exception as e:
        print(f"Simple query error: {e}")
        return f"Error: {str(e)}"

@main_bp.route("/admin/users")
@admin_required
def admin_users():
    """Quản lý người dùng với tìm kiếm và phân trang"""
    # Lấy tham số từ URL
    page = request.args.get('page', 1, type=int)
    per_page = 20  # Hiển thị 20 user mỗi trang
    search_query = request.args.get('q', '').strip()
    
    try:
        print(f"Admin users - page: {page}, per_page: {per_page}, search_query: '{search_query}'")
        with current_app.db_engine.connect() as conn:
            if search_query:
                # Kiểm tra xem search_query có phải là số (ID) không
                is_numeric = search_query.isdigit()
                
                if is_numeric:
                    # Tìm kiếm theo ID (exact match)
                    user_id = int(search_query)
                    total_count = conn.execute(text("""
                        SELECT COUNT(*) 
                        FROM cine.[User] u
                        JOIN cine.Role r ON r.roleId = u.roleId
                        WHERE u.userId = :user_id
                    """), {"user_id": user_id}).scalar()
                    
                    # Tính toán phân trang
                    total_pages = (total_count + per_page - 1) // per_page
                    offset = (page - 1) * per_page
                    
                    # Lấy kết quả tìm kiếm theo ID
                    users = conn.execute(text("""
                        SELECT u.userId, u.email, u.status, u.createdAt, u.lastLoginAt, r.roleName,
                               a.username
                        FROM (
                            SELECT u.userId, u.email, u.status, u.createdAt, u.lastLoginAt, u.roleId,
                                   ROW_NUMBER() OVER (ORDER BY u.createdAt DESC) as rn
                            FROM cine.[User] u
                            WHERE u.userId = :user_id
                        ) t
                        JOIN cine.Role r ON r.roleId = t.roleId
                        LEFT JOIN cine.Account a ON a.userId = t.userId
                        WHERE t.rn > :offset AND t.rn <= :offset + :per_page
                    """), {
                        "user_id": user_id,
                        "offset": offset,
                        "per_page": per_page
                    }).mappings().all()
                else:
                    # Tìm kiếm theo email hoặc username
                    # Đếm tổng số kết quả tìm kiếm
                    total_count = conn.execute(text("""
                        SELECT COUNT(*) 
                        FROM cine.[User] u
                        JOIN cine.Role r ON r.roleId = u.roleId
                        LEFT JOIN cine.Account a ON a.userId = u.userId
                        WHERE u.email LIKE :query OR a.username LIKE :query
                    """), {"query": f"%{search_query}%"}).scalar()
                    
                    # Tính toán phân trang
                    total_pages = (total_count + per_page - 1) // per_page
                    offset = (page - 1) * per_page
                    
                    # Lấy kết quả tìm kiếm theo email hoặc username
                    users = conn.execute(text("""
                        SELECT u.userId, u.email, u.status, u.createdAt, u.lastLoginAt, r.roleName,
                               a.username
                        FROM (
                            SELECT u.userId, u.email, u.status, u.createdAt, u.lastLoginAt, u.roleId,
                                   ROW_NUMBER() OVER (
                                       ORDER BY 
                                           CASE 
                                               WHEN u.email LIKE :exact_query THEN 1
                                               WHEN a.username LIKE :exact_query THEN 2
                                               WHEN u.email LIKE :start_query THEN 3
                                               WHEN a.username LIKE :start_query THEN 4
                                               ELSE 5
                                           END,
                                           u.createdAt DESC
                                   ) as rn
                            FROM cine.[User] u
                            LEFT JOIN cine.Account a ON a.userId = u.userId
                            WHERE u.email LIKE :query OR a.username LIKE :query
                        ) t
                        JOIN cine.Role r ON r.roleId = t.roleId
                        LEFT JOIN cine.Account a ON a.userId = t.userId
                        WHERE t.rn > :offset AND t.rn <= :offset + :per_page
                    """), {
                        "query": f"%{search_query}%",
                        "exact_query": f"{search_query}%",
                        "start_query": f"{search_query}%",
                        "offset": offset,
                        "per_page": per_page
                    }).mappings().all()
            else:
                # Lấy user mới nhất với phân trang (đơn giản hóa)
                # Đếm tổng số user
                total_count = conn.execute(text("SELECT COUNT(*) FROM cine.[User]")).scalar()
                
                # Tính toán phân trang
                total_pages = (total_count + per_page - 1) // per_page
                offset = (page - 1) * per_page
                
                # Lấy user với phân trang (query đơn giản)
                users = conn.execute(text("""
                    SELECT u.userId, u.email, u.status, u.createdAt, u.lastLoginAt, r.roleName,
                           a.username
                    FROM cine.[User] u
                    JOIN cine.Role r ON r.roleId = u.roleId
                    LEFT JOIN cine.Account a ON a.userId = u.userId
                    ORDER BY u.createdAt DESC, u.userId DESC
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """), {"offset": offset, "per_page": per_page}).mappings().all()
            
            print(f"Found {len(users)} users, total_count: {total_count}")
            for user in users:
                print(f"User: {dict(user)}")
            
            # Tạo pagination info
            pagination = {
                "page": page,
                "per_page": per_page,
                "total": total_count,
                "pages": total_pages,
                "has_prev": page > 1,
                "has_next": page < total_pages,
                "prev_num": page - 1 if page > 1 else None,
                "next_num": page + 1 if page < total_pages else None
            }
            
        return render_template("admin_users.html", 
                             users=users, 
                             pagination=pagination,
                             search_query=search_query)
    except Exception as e:
        flash(f"Lỗi khi tải danh sách người dùng: {str(e)}", "error")
        return render_template("admin_users.html", 
                             users=[], 
                             pagination=None,
                             search_query=search_query)

@main_bp.route("/admin/users/<int:user_id>/toggle-status", methods=["POST"])
@admin_required
def admin_user_toggle_status(user_id):
    """Thay đổi trạng thái người dùng"""
    try:
        with current_app.db_engine.begin() as conn:
            # Lấy thông tin user
            user_info = conn.execute(text("""
                SELECT u.email, u.status, r.roleName
                FROM cine.[User] u
                JOIN cine.Role r ON r.roleId = u.roleId
                WHERE u.userId = :id
            """), {"id": user_id}).mappings().first()
            
            if not user_info:
                flash("Không tìm thấy người dùng.", "error")
                return redirect(url_for("main.admin_users"))
            
            # Không cho phép thay đổi trạng thái admin
            if user_info.roleName == "Admin":
                flash("Không thể thay đổi trạng thái tài khoản Admin!", "error")
                return redirect(url_for("main.admin_users"))
            
            current_status = user_info.status
            new_status = "inactive" if current_status == "active" else "active"
            
            conn.execute(text("""
                UPDATE cine.[User] SET status = :status WHERE userId = :id
            """), {"id": user_id, "status": new_status})
        
        status_text = "không hoạt động" if new_status == "inactive" else "hoạt động"
        flash(f"✅ Đã thay đổi trạng thái {user_info.email} thành {status_text}!", "success")
    except Exception as e:
        flash(f"❌ Lỗi khi thay đổi trạng thái: {str(e)}", "error")
    
    return redirect(url_for("main.admin_users"))

@main_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_user_delete(user_id):
    """Xóa người dùng"""
    try:
        with current_app.db_engine.begin() as conn:
            # Lấy thông tin user
            user_info = conn.execute(text("""
                SELECT u.email, r.roleName
                FROM cine.[User] u
                JOIN cine.Role r ON r.roleId = u.roleId
                WHERE u.userId = :id
            """), {"id": user_id}).mappings().first()
            
            if not user_info:
                flash("Không tìm thấy người dùng.", "error")
                return redirect(url_for("main.admin_users"))
            
            # Không cho phép xóa admin
            if user_info.roleName == "Admin":
                flash("❌ Không thể xóa tài khoản Admin!", "error")
                return redirect(url_for("main.admin_users"))
            
            # Xóa user (cascade sẽ xóa account và rating)
            conn.execute(text("DELETE FROM cine.[User] WHERE userId = :id"), {"id": user_id})
            
            flash(f"✅ Đã xóa tài khoản {user_info.email} thành công!", "success")
    except Exception as e:
        flash(f"❌ Lỗi khi xóa người dùng: {str(e)}", "error")
    
    return redirect(url_for("main.admin_users"))


@main_bp.route("/search")
def search():
    """Trang kết quả tìm kiếm"""
    if not session.get("user_id"):
        return redirect(url_for("main.login"))
    
    query = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 10
    
    if not query:
        return render_template('search.html', 
                             query=query, 
                             movies=[], 
                             pagination=None,
                             total_results=0)
    
    try:
        with current_app.db_engine.connect() as conn:
            # Đếm tổng số kết quả
            total_count = conn.execute(text("""
                SELECT COUNT(*) 
                FROM cine.Movie 
                WHERE title LIKE :query
            """), {"query": f"%{query}%"}).scalar()
            
            # Tính toán phân trang
            total_pages = (total_count + per_page - 1) // per_page
            offset = (page - 1) * per_page
            
            # Lấy kết quả tìm kiếm với rating và genres
            movies = conn.execute(text("""
                SELECT m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.country,
                       AVG(CAST(r.value AS FLOAT)) AS avgRating,
                       COUNT(r.value) AS ratingCount,
                       STUFF((
                           SELECT ', ' + g.name
                           FROM cine.MovieGenre mg
                           JOIN cine.Genre g ON mg.genreId = g.genreId
                           WHERE mg.movieId = m.movieId
                           FOR XML PATH('')
                       ), 1, 2, '') AS genres
                FROM (
                    SELECT movieId, title, posterUrl, backdropUrl, overview, releaseYear, country,
                           ROW_NUMBER() OVER (
                               ORDER BY 
                                   CASE 
                                       WHEN title LIKE :exact_query THEN 1
                                       WHEN title LIKE :start_query THEN 2
                                       ELSE 3
                                   END,
                                   title
                           ) as rn
                    FROM cine.Movie 
                    WHERE title LIKE :query
                ) t
                JOIN cine.Movie m ON t.movieId = m.movieId
                LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                WHERE t.rn > :offset AND t.rn <= :offset + :per_page
                GROUP BY m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.country, t.rn
                ORDER BY t.rn
            """), {
                "query": f"%{query}%",
                "exact_query": f"{query}%",
                "start_query": f"{query}%",
                "offset": offset,
                "per_page": per_page
            }).mappings().all()
            
            movie_list = [
                {
                    "id": r["movieId"],
                    "title": r["title"],
                    "poster": get_poster_or_dummy(r.get("posterUrl"), r["title"]),
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                    "year": r.get("releaseYear"),
                    "country": r.get("country"),
                    "avgRating": round(float(r["avgRating"]), 2) if r["avgRating"] else 0.0,
                    "ratingCount": int(r["ratingCount"]) if r["ratingCount"] else 0,
                    "genres": r.get("genres", "").split(", ") if r.get("genres") else []
                }
                for r in movies
            ]
            
            # Tạo pagination info
            pagination = {
                "page": page,
                "per_page": per_page,
                "total": total_count,
                "pages": total_pages,
                "has_prev": page > 1,
                "has_next": page < total_pages,
                "prev_num": page - 1 if page > 1 else None,
                "next_num": page + 1 if page < total_pages else None
            }
            
            return render_template('search.html', 
                                 query=query, 
                                 movies=movie_list, 
                                 pagination=pagination,
                                 total_results=total_count)
            
    except Exception as e:
        current_app.logger.error(f"Error in search: {e}")
        return render_template('search.html', 
                             query=query, 
                             movies=[], 
                             pagination=None,
                             total_results=0)

@main_bp.route("/the-loai/<string:genre_slug>")
def genre_page(genre_slug):
    """Redirect đến trang chủ với filter thể loại"""
    if not session.get("user_id"):
        return redirect(url_for("main.login"))
    
    # Mapping từ slug sang tên thể loại (4 thể loại chính)
    main_genre_mapping = {
        'action': 'Action',
        'adventure': 'Adventure', 
        'comedy': 'Comedy',
        'horror': 'Horror'
    }
    
    # Kiểm tra 4 thể loại chính trước
    genre_name = main_genre_mapping.get(genre_slug)
    
    # Nếu không tìm thấy trong 4 thể loại chính, tìm trong database
    if not genre_name:
        try:
            with current_app.db_engine.connect() as conn:
                # Tìm thể loại theo slug trong database
                result = conn.execute(text("""
                    SELECT name FROM cine.Genre 
                    WHERE LOWER(REPLACE(name, ' ', '-')) = :slug
                """), {"slug": genre_slug.lower()}).fetchone()
                
                if result:
                    genre_name = result[0]
                else:
                    return redirect(url_for('main.home'))
        except Exception as e:
            print(f"Error finding genre: {e}")
            return redirect(url_for('main.home'))
    
    # Redirect về trang chủ với genre filter
    return redirect(url_for('main.home', genre=genre_name))


@main_bp.route('/api/search/suggestions')
def search_suggestions():
    """API endpoint để lấy gợi ý tìm kiếm phim"""
    try:
        query = request.args.get('q', '').strip()
        limit = request.args.get('limit', 10, type=int)
        
        if not query or len(query) < 2:
            return jsonify({
                "success": True,
                "suggestions": []
            })
        
        # Validate và sanitize limit để tránh SQL injection
        validated_limit = validate_limit(limit, max_limit=50, default=10)
        top_clause = safe_top_clause(validated_limit, max_limit=50)
        
        with current_app.db_engine.connect() as conn:
            # Tìm kiếm phim theo title (case-insensitive)
            # Sử dụng validated limit thay vì f-string trực tiếp
            suggestions = conn.execute(text(f"""
                SELECT {top_clause} movieId, title, releaseYear, posterUrl
                FROM cine.Movie 
                WHERE title LIKE :query
                ORDER BY 
                    CASE 
                        WHEN title LIKE :exact_query THEN 1
                        WHEN title LIKE :start_query THEN 2
                        ELSE 3
                    END,
                    title
            """), {
                "query": f"%{query}%",
                "exact_query": f"{query}%",
                "start_query": f"{query}%"
            }).mappings().all()
            
            results = []
            for row in suggestions:
                results.append({
                    "id": row["movieId"],
                    "title": row["title"],
                    "year": row.get("releaseYear"),
                    "poster": row.get("posterUrl") if row.get("posterUrl") and row.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={row['title'][:20].replace(' ', '+')}"
                })
            
            return jsonify({
                "success": True,
                "suggestions": results
            })
            
    except Exception as e:
        print(f"Error getting search suggestions: {e}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra"})


@main_bp.route("/submit-rating/<int:movie_id>", methods=["POST"])
def submit_rating(movie_id):
    """Gửi đánh giá phim"""
    if not session.get("user_id"):
        return jsonify({"success": False, "message": "Chưa đăng nhập"})
    
    user_id = session.get("user_id")
    data = request.get_json()
    rating_value = data.get('rating')
    
    if not rating_value or not isinstance(rating_value, int) or rating_value < 1 or rating_value > 5:
        return jsonify({"success": False, "message": "Đánh giá phải từ 1 đến 5 sao"})
    
    try:
        with current_app.db_engine.begin() as conn:
            # Kiểm tra xem user đã đánh giá phim này chưa
            existing = conn.execute(text("""
                SELECT value FROM [cine].[Rating] 
                WHERE userId = :user_id AND movieId = :movie_id
            """), {"user_id": user_id, "movie_id": movie_id}).scalar()
            
            if existing:
                # Cập nhật đánh giá cũ
                conn.execute(text("""
                    UPDATE [cine].[Rating] 
                    SET value = :rating, ratedAt = GETDATE()
                    WHERE userId = :user_id AND movieId = :movie_id
                """), {"user_id": user_id, "movie_id": movie_id, "rating": rating_value})
                message = f"Đã cập nhật đánh giá thành {rating_value} sao"
            else:
                # Thêm đánh giá mới
                conn.execute(text("""
                    INSERT INTO [cine].[Rating] (userId, movieId, value, ratedAt)
                    VALUES (:user_id, :movie_id, :rating, GETDATE())
                """), {"user_id": user_id, "movie_id": movie_id, "rating": rating_value})
                message = f"Đã đánh giá {rating_value} sao"
            
            # Lấy thống kê rating mới
            stats = conn.execute(text("""
                SELECT 
                    AVG(CAST(value AS FLOAT)) as avg_rating,
                    COUNT(*) as total_ratings
                FROM [cine].[Rating] 
                WHERE movieId = :movie_id
            """), {"movie_id": movie_id}).mappings().first()
            
            return jsonify({
                "success": True,
                "message": message,
                "user_rating": rating_value,
                # CF-style
                "avgRating": round(stats.avg_rating, 1) if stats.avg_rating else 0,
                "ratingCount": stats.total_ratings,
                # Backward compatibility
                "avg_rating": round(stats.avg_rating, 1) if stats.avg_rating else 0,
                "total_ratings": stats.total_ratings
            })
            
            # Mark CF model as dirty for retrain
            try:
                set_cf_dirty(current_app.db_engine)
            except Exception:
                pass
                
    except Exception as e:
        print(f"Error submitting rating: {e}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra khi đánh giá"})


@main_bp.route("/get-rating/<int:movie_id>", methods=["GET"])
def get_rating(movie_id):
    """Lấy thông tin đánh giá của phim"""
    user_id = session.get("user_id")
    user_rating = 0
    
    try:
        with current_app.db_engine.connect() as conn:
            # Lấy đánh giá của user hiện tại (nếu đã đăng nhập)
            if user_id:
                user_rating = conn.execute(text("""
                    SELECT value FROM [cine].[Rating] 
                    WHERE userId = :user_id AND movieId = :movie_id
                """), {"user_id": user_id, "movie_id": movie_id}).scalar() or 0
            
            # Lấy thống kê tổng quan (luôn hiển thị dù chưa đăng nhập)
            stats = conn.execute(text("""
                SELECT 
                    AVG(CAST(value AS FLOAT)) as avg_rating,
                    COUNT(*) as total_ratings
                FROM [cine].[Rating] 
                WHERE movieId = :movie_id
            """), {"movie_id": movie_id}).mappings().first()
            
            return jsonify({
                "success": True,
                "user_rating": user_rating,
                # CF-style
                "avgRating": round(stats.avg_rating, 1) if stats.avg_rating else 0,
                "ratingCount": stats.total_ratings,
                # Backward compatibility
                "avg_rating": round(stats.avg_rating, 1) if stats.avg_rating else 0,
                "total_ratings": stats.total_ratings
            })
            
    except Exception as e:
        print(f"Error getting rating: {e}")
        return jsonify({"success": False, "user_rating": 0, "avg_rating": 0, "total_ratings": 0})


@main_bp.route("/delete-rating/<int:movie_id>", methods=["POST"])
def delete_rating(movie_id):
    """Xóa đánh giá phim"""
    if not session.get("user_id"):
        return jsonify({"success": False, "message": "Chưa đăng nhập"})
    
    user_id = session.get("user_id")
    
    try:
        with current_app.db_engine.begin() as conn:
            # Xóa đánh giá
            conn.execute(text("""
                DELETE FROM [cine].[Rating] 
                WHERE userId = :user_id AND movieId = :movie_id
            """), {"user_id": user_id, "movie_id": movie_id})
            
            # Lấy thống kê rating mới
            stats = conn.execute(text("""
                SELECT 
                    AVG(CAST(value AS FLOAT)) as avg_rating,
                    COUNT(*) as total_ratings
                FROM [cine].[Rating] 
                WHERE movieId = :movie_id
            """), {"movie_id": movie_id}).mappings().first()
            
            return jsonify({
                "success": True,
                "message": "Đã xóa đánh giá",
                "user_rating": 0,
                # CF-style
                "avgRating": round(stats.avg_rating, 1) if stats.avg_rating else 0,
                "ratingCount": stats.total_ratings,
                # Backward compatibility
                "avg_rating": round(stats.avg_rating, 1) if stats.avg_rating else 0,
                "total_ratings": stats.total_ratings
            })
            
    except Exception as e:
        print(f"Error deleting rating: {e}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra khi xóa đánh giá"})



@main_bp.route("/submit-comment/<int:movie_id>", methods=["POST"])
def submit_comment(movie_id):
    """Gửi comment cho phim"""
    if not session.get("user_id"):
        return jsonify({"success": False, "message": "Chưa đăng nhập"})
    
    user_id = session.get("user_id")
    data = request.get_json()
    content = data.get('content', '').strip()
    parent_comment_id = data.get('parent_comment_id')
    
    if not content:
        return jsonify({"success": False, "message": "Nội dung comment không được để trống"})
    
    if len(content) > 1000:
        return jsonify({"success": False, "message": "Comment quá dài (tối đa 1000 ký tự)"})
    
    try:
        with current_app.db_engine.begin() as conn:
            # Tạo commentId tự động
            max_id = conn.execute(text("""
                SELECT ISNULL(MAX(commentId), 0) + 1 FROM cine.Comment
            """)).scalar()
            
            # Thêm comment mới
            conn.execute(text("""
                INSERT INTO [cine].[Comment] (commentId, userId, movieId, content, createdAt)
                VALUES (:comment_id, :user_id, :movie_id, :content, GETDATE())
            """), {
                "comment_id": max_id,
                "user_id": user_id, 
                "movie_id": movie_id, 
                "content": content
            })
            
            comment_id = max_id
            
            if not comment_id:
                return jsonify({"success": False, "message": "Không thể tạo comment"})
            
            # Lấy thông tin comment vừa tạo
            comment_data = conn.execute(text("""
                SELECT 
                    c.commentId,
                    c.content,
                    c.createdAt,
                    u.email as user_email,
                    u.avatarUrl
                FROM [cine].[Comment] c
                JOIN [cine].[User] u ON c.userId = u.userId
                WHERE c.commentId = :comment_id
            """), {"comment_id": comment_id}).mappings().first()
            
            if not comment_data:
                return jsonify({"success": False, "message": "Không thể lấy thông tin comment vừa tạo"})
            
            return jsonify({
                "success": True,
                "message": "Đã thêm comment thành công",
                "comment": {
                    "id": comment_data.commentId,
                    "content": comment_data.content,
                    "createdAt": comment_data.createdAt.isoformat(),
                    "user_email": comment_data.user_email,
                    "avatarUrl": comment_data.avatarUrl
                }
            })
                
    except Exception as e:
        current_app.logger.error(f"Error submitting comment: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Có lỗi xảy ra khi thêm comment: {str(e)}"})


@main_bp.route("/get-comments/<int:movie_id>", methods=["GET"])
def get_comments(movie_id):
    """Lấy danh sách comment của phim"""
    try:
        with current_app.db_engine.connect() as conn:
            # Lấy tất cả comment của phim
            comments = conn.execute(text("""
                SELECT 
                    c.commentId,
                    c.content,
                    c.createdAt,
                    u.email as user_email,
                    u.avatarUrl,
                    u.userId
                FROM [cine].[Comment] c
                JOIN [cine].[User] u ON c.userId = u.userId
                WHERE c.movieId = :movie_id
                ORDER BY c.createdAt ASC
            """), {"movie_id": movie_id}).mappings().all()
            
            # Đơn giản hóa - chỉ trả về danh sách comment
            comments_list = []
            
            for comment in comments:
                comments_list.append({
                    "id": comment.commentId,
                    "content": comment.content,
                    "createdAt": comment.createdAt.isoformat(),
                    "user_email": comment.user_email,
                    "avatarUrl": comment.avatarUrl,
                    "userId": comment.userId,
                    "replies": []
                })
            
            return jsonify({
                "success": True,
                "comments": comments_list
            })
            
    except Exception as e:
        current_app.logger.error(f"Error getting comments: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Có lỗi xảy ra khi lấy comment: {str(e)}"})


@main_bp.route("/update-comment/<int:comment_id>", methods=["POST"])
def update_comment(comment_id):
    """Cập nhật comment"""
    if not session.get("user_id"):
        return jsonify({"success": False, "message": "Chưa đăng nhập"})
    
    user_id = session.get("user_id")
    data = request.get_json()
    content = data.get('content', '').strip()
    
    if not content:
        return jsonify({"success": False, "message": "Nội dung comment không được để trống"})
    
    if len(content) > 1000:
        return jsonify({"success": False, "message": "Comment quá dài (tối đa 1000 ký tự)"})
    
    try:
        with current_app.db_engine.begin() as conn:
            # Kiểm tra quyền sở hữu comment
            comment_owner = conn.execute(text("""
                SELECT userId FROM [cine].[Comment] 
                WHERE commentId = :comment_id
            """), {"comment_id": comment_id}).scalar()
            
            if not comment_owner:
                return jsonify({"success": False, "message": "Comment không tồn tại"})
            
            if comment_owner != user_id:
                return jsonify({"success": False, "message": "Bạn không có quyền chỉnh sửa comment này"})
            
            # Cập nhật comment
            conn.execute(text("""
                UPDATE [cine].[Comment] 
                SET content = :content
                WHERE commentId = :comment_id
            """), {"content": content, "comment_id": comment_id})
            
            return jsonify({
                "success": True,
                "message": "Đã cập nhật comment thành công"
            })
                
    except Exception as e:
        current_app.logger.error(f"Error updating comment: {e}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra khi cập nhật comment"})


@main_bp.route("/delete-comment/<int:comment_id>", methods=["POST"])
def delete_comment(comment_id):
    """Xóa comment (soft delete)"""
    if not session.get("user_id"):
        return jsonify({"success": False, "message": "Chưa đăng nhập"})
    
    user_id = session.get("user_id")
    
    try:
        with current_app.db_engine.begin() as conn:
            # Kiểm tra quyền sở hữu comment
            comment_owner = conn.execute(text("""
                SELECT userId FROM [cine].[Comment] 
                WHERE commentId = :comment_id
            """), {"comment_id": comment_id}).scalar()
            
            if not comment_owner:
                return jsonify({"success": False, "message": "Comment không tồn tại"})
            
            if comment_owner != user_id:
                return jsonify({"success": False, "message": "Bạn không có quyền xóa comment này"})
            
            # Delete comment
            conn.execute(text("""
                DELETE FROM [cine].[Comment] 
                WHERE commentId = :comment_id
            """), {"comment_id": comment_id})
            
            return jsonify({
                "success": True,
                "message": "Đã xóa comment thành công"
            })
                
    except Exception as e:
        current_app.logger.error(f"Error deleting comment: {e}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra khi xóa comment"})


# ==================== MODEL MANAGEMENT ENDPOINTS ====================

@main_bp.route("/api/retrain_model", methods=["POST"])
@admin_required
def retrain_model():
    """Retrain collaborative filtering model - redirect to retrain_cf_model"""
    try:
        # Gọi retrain_cf_model để thực hiện retrain thực sự
        current_app.logger.info("Retrain model requested, redirecting to retrain_cf_model...")
        return retrain_cf_model()
        
    except Exception as e:
        current_app.logger.error(f"Error in retrain_model: {e}")
        return jsonify({
            "success": False,
            "message": f"Retrain failed: {str(e)}"
        })

@main_bp.route("/api/train_enhanced_cf", methods=["POST"])
@admin_required
def train_enhanced_cf():
    """Train enhanced CF model với tất cả dữ liệu"""
    try:
        global enhanced_cf_recommender
        
        if enhanced_cf_recommender is None:
            init_recommenders()
        
        # Train enhanced CF model
        success = enhanced_cf_recommender.train_model()
        
        if success:
            return jsonify({
                "success": True,
                "message": "Enhanced CF model trained successfully",
                "model_info": enhanced_cf_recommender.get_model_info()
            })
        else:
            return jsonify({
                "success": False,
                "message": "Failed to train enhanced CF model"
            })
            
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Error training enhanced CF model: {str(e)}"
        })

@main_bp.route("/api/train_cf_model", methods=["POST"])
@admin_required
def train_cf_model():
    """Train CF model using the fast training script"""
    try:
        import subprocess
        import sys
        import os
        
        # Path to the training script
        script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'model_collaborative', 'train_collaborative_fast.py')
        
        if not os.path.exists(script_path):
            return jsonify({"success": False, "message": "Training script not found"})
        
        # Run the training script
        result = subprocess.run([sys.executable, script_path], 
                              capture_output=True, text=True, cwd=os.path.dirname(script_path))
        
        if result.returncode == 0:
            # Reload models after training
            init_recommenders()
            return jsonify({
                "success": True, 
                "message": "CF model trained successfully",
                "output": result.stdout
            })
        else:
            return jsonify({
                "success": False, 
                "message": "Training failed",
                "error": result.stderr
            })
            
    except Exception as e:
        return jsonify({"success": False, "message": f"Error training CF model: {str(e)}"})

@main_bp.route("/api/model_status_public", methods=["GET"])
def model_status_public():
    """Get model status for public access"""
    try:
        global enhanced_cf_recommender
        
        # Debug logging
        current_app.logger.info(f"enhanced_cf_recommender: {enhanced_cf_recommender}")
        
        # Initialize if not already done
        if enhanced_cf_recommender is None:
            current_app.logger.info("Recommenders not initialized, initializing now...")
            init_recommenders()
        
        # Chỉ sử dụng Enhanced CF model
        if enhanced_cf_recommender and enhanced_cf_recommender.is_model_loaded():
            model_info = enhanced_cf_recommender.get_model_info()
            model_info['model_type'] = 'Enhanced CF'
            current_app.logger.info(f"Enhanced CF model info: {model_info}")
            return jsonify({
                "success": True,
                "modelInfo": model_info
            })
        
        # No model loaded
        current_app.logger.warning("No model loaded")
        return jsonify({
            "success": False,
            "message": "No model loaded"
        })
            
    except Exception as e:
        current_app.logger.error(f"Error in model_status_public: {str(e)}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Error getting model status: {str(e)}"
        })


# Load model 
@main_bp.route("/api/switch_model/<model_type>", methods=["POST"])
@admin_required
@login_required
def switch_model(model_type):
    """Switch model - chỉ hỗ trợ Enhanced CF model"""
    try:
        global enhanced_cf_recommender
        
        if model_type == "enhanced" or model_type == "cf":
            if enhanced_cf_recommender and enhanced_cf_recommender.is_model_loaded():
                return jsonify({
                    "success": True,
                    "message": "Đang sử dụng Enhanced CF model",
                    "model_type": "Enhanced CF"
                })
            else:
                return jsonify({
                    "success": False,
                    "message": "Enhanced CF model chưa được load"
                })
        else:
            return jsonify({
                "success": False,
                "message": "Chỉ hỗ trợ Enhanced CF model"
            })
            
    except Exception as e:
        current_app.logger.error(f"Error switching model: {str(e)}")
        return jsonify({
            "success": False,
            "message": f"Lỗi khi chuyển model: {str(e)}"
        })

@main_bp.route("/api/reload_cf_model", methods=["POST"])
@admin_required
@login_required
def reload_cf_model():
    """Reload CF model from disk (useful when model file is updated)"""
    try:
        global enhanced_cf_recommender
        
        if enhanced_cf_recommender:
            success = enhanced_cf_recommender.reload_model()
            if success:
                return jsonify({
                    "success": True,
                    "message": "Model CF đã được reload thành công",
                    "model_info": enhanced_cf_recommender.get_model_info()
                })
            else:
                return jsonify({
                    "success": False,
                    "message": "Không thể reload model. Kiểm tra file model có tồn tại không."
                })
        else:
            # Nếu chưa có, khởi tạo lại
            init_recommenders()
            if enhanced_cf_recommender and enhanced_cf_recommender.is_model_loaded():
                return jsonify({
                    "success": True,
                    "message": "Model CF đã được load thành công",
                    "model_info": enhanced_cf_recommender.get_model_info()
                })
            else:
                return jsonify({
                    "success": False,
                    "message": "Không thể load model"
                })
    except Exception as e:
        current_app.logger.error(f"Error reloading model: {str(e)}")
        return jsonify({
            "success": False,
            "message": f"Lỗi khi reload model: {str(e)}"
        })

@main_bp.route("/api/hybrid_status", methods=["GET"])
@login_required
def hybrid_status():
    """Kiểm tra trạng thái hybrid recommendations cho user hiện tại - CHI TIẾT"""
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"success": False, "message": "Chưa đăng nhập"})
        
        with current_app.db_engine.connect() as conn:
            # Kiểm tra recommendations trong database với chi tiết scores
            recs_result = conn.execute(text("""
                SELECT 
                    COUNT(*) as total_count,
                    COUNT(CASE WHEN algo = 'hybrid' THEN 1 END) as hybrid_count,
                    COUNT(CASE WHEN algo = 'enhanced_cf' THEN 1 END) as cf_count,
                    COUNT(CASE WHEN algo = 'collaborative' THEN 1 END) as collaborative_count,
                    COUNT(CASE WHEN algo = 'cold_start' THEN 1 END) as cold_start_count,
                    MAX(generatedAt) as last_generated,
                    MAX(expiresAt) as last_expires,
                    AVG(CAST(score AS FLOAT)) as avg_score,
                    MIN(CAST(score AS FLOAT)) as min_score,
                    MAX(CAST(score AS FLOAT)) as max_score
                FROM cine.PersonalRecommendation
                WHERE userId = :user_id AND expiresAt > GETUTCDATE()
            """), {"user_id": user_id}).mappings().first()
            
            # Lấy top 5 hybrid recommendations để phân tích chi tiết
            hybrid_details = conn.execute(text("""
                SELECT TOP 5
                    m.movieId, m.title, pr.score, pr.rank, pr.algo
                FROM cine.PersonalRecommendation pr
                JOIN cine.Movie m ON m.movieId = pr.movieId
                WHERE pr.userId = :user_id AND pr.expiresAt > GETUTCDATE() AND pr.algo = 'hybrid'
                ORDER BY pr.rank
            """), {"user_id": user_id}).mappings().all()
            
            # Kiểm tra CF model
            cf_loaded = enhanced_cf_recommender and enhanced_cf_recommender.is_model_loaded()
            
            # Kiểm tra Content-Based recommender
            cb_available = content_recommender is not None
            
            # Test CF recommendations với chi tiết
            cf_test = []
            cf_test_details = []
            if cf_loaded:
                try:
                    cf_test = enhanced_cf_recommender.get_user_recommendations(user_id, limit=5)
                    cf_test_details = [
                        {
                            "movieId": rec.get('movieId') or rec.get('id'),
                            "title": rec.get('title', 'N/A'),
                            "score": rec.get('recommendation_score') or rec.get('score', 0.0)
                        }
                        for rec in cf_test[:5]
                    ]
                except Exception as e:
                    current_app.logger.warning(f"CF test failed: {e}")
            
            # Test CB recommendations với chi tiết
            cb_test = []
            cb_test_details = []
            if cb_available:
                try:
                    cb_test = content_recommender.get_user_recommendations(user_id, limit=5)
                    cb_test_details = [
                        {
                            "movieId": rec.get('movieId') or rec.get('id'),
                            "title": rec.get('title', 'N/A'),
                            "score": rec.get('score') or rec.get('similarity', 0.0)
                        }
                        for rec in cb_test[:5]
                    ]
                except Exception as e:
                    current_app.logger.warning(f"CB test failed: {e}")
            
            # Kiểm tra số interactions của user
            interaction_counts = conn.execute(text("""
                SELECT 
                    (SELECT COUNT(*) FROM cine.Rating WHERE userId = :user_id) as rating_count,
                    (SELECT COUNT(*) FROM cine.ViewHistory WHERE userId = :user_id) as view_count
            """), {"user_id": user_id}).mappings().first()
            total_interactions = (interaction_counts.rating_count or 0) + (interaction_counts.view_count or 0)
            
            # Determine status
            has_hybrid = recs_result and recs_result["hybrid_count"] > 0
            can_generate_hybrid = cf_loaded and cb_available and (len(cf_test) > 0 or len(cb_test) > 0)
            
            # Tính cold start weight dựa trên interactions
            if total_interactions < 5:
                cold_start_weight = 1.0
            elif total_interactions < 11:
                cold_start_weight = 0.3
            elif total_interactions < 21:
                cold_start_weight = 0.2
            elif total_interactions < 51:
                cold_start_weight = 0.1
            else:
                cold_start_weight = 0.05
            
            return jsonify({
                "success": True,
                "hybrid_active": has_hybrid,
                "can_generate_hybrid": can_generate_hybrid,
                "user_info": {
                    "user_id": user_id,
                    "total_interactions": total_interactions,
                    "rating_count": interaction_counts.rating_count or 0,
                    "view_count": interaction_counts.view_count or 0,
                    "cold_start_weight": round(cold_start_weight * 100, 1),
                    "cf_cb_weight": round((1 - cold_start_weight) * 100, 1)
                },
                "recommendations": {
                    "total": recs_result["total_count"] if recs_result else 0,
                    "hybrid": recs_result["hybrid_count"] if recs_result else 0,
                    "cf": recs_result["cf_count"] if recs_result else 0,
                    "collaborative": recs_result["collaborative_count"] if recs_result else 0,
                    "cold_start": recs_result["cold_start_count"] if recs_result else 0,
                    "last_generated": recs_result["last_generated"].isoformat() if recs_result and recs_result["last_generated"] else None,
                    "expires_at": recs_result["last_expires"].isoformat() if recs_result and recs_result["last_expires"] else None,
                    "score_stats": {
                        "avg": round(float(recs_result["avg_score"]), 4) if recs_result and recs_result["avg_score"] else 0.0,
                        "min": round(float(recs_result["min_score"]), 4) if recs_result and recs_result["min_score"] else 0.0,
                        "max": round(float(recs_result["max_score"]), 4) if recs_result and recs_result["max_score"] else 0.0
                    },
                    "hybrid_details": [
                        {
                            "movieId": row["movieId"],
                            "title": row["title"],
                            "score": round(float(row["score"]), 4),
                            "rank": row["rank"]
                        }
                        for row in hybrid_details
                    ]
                },
                "models": {
                    "cf_loaded": cf_loaded,
                    "cb_available": cb_available,
                    "cf_test_count": len(cf_test),
                    "cb_test_count": len(cb_test),
                    "cf_test_details": cf_test_details,
                    "cb_test_details": cb_test_details
                },
                "status": "hybrid" if has_hybrid else ("ready" if can_generate_hybrid else "not_ready"),
                "health_check": {
                    "cf_model_ok": cf_loaded and len(cf_test) > 0,
                    "cb_model_ok": cb_available and len(cb_test) > 0,
                    "hybrid_working": has_hybrid and recs_result and recs_result["hybrid_count"] > 0,
                    "overall_status": "healthy" if (cf_loaded and cb_available and has_hybrid) else ("partial" if (cf_loaded or cb_available) else "unhealthy")
                }
            })
            
    except Exception as e:
        current_app.logger.error(f"Error checking hybrid status: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Lỗi khi kiểm tra trạng thái: {str(e)}"
        })

@main_bp.route("/api/score_distribution", methods=["GET"])
@login_required
def score_distribution():
    """Lấy thống kê phân phối điểm hybrid_score để kiểm tra"""
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"success": False, "message": "Chưa đăng nhập"})
        
        with current_app.db_engine.connect() as conn:
            # Lấy tất cả hybrid scores của user
            scores_result = conn.execute(text("""
                SELECT score 
                FROM cine.PersonalRecommendation
                WHERE userId = :user_id 
                    AND algo = 'hybrid'
                    AND expiresAt > GETUTCDATE()
                ORDER BY score DESC
            """), {"user_id": user_id}).fetchall()
            
            if not scores_result:
                return jsonify({
                    "success": False,
                    "message": "Không có hybrid recommendations để phân tích"
                })
            
            scores = [float(row[0]) for row in scores_result if row[0] is not None]
            
            if not scores:
                return jsonify({
                    "success": False,
                    "message": "Không có scores hợp lệ"
                })
            
            import numpy as np
            scores_array = np.array(scores)
            
            # Tính các thống kê
            stats = {
                "count": len(scores),
                "min": float(np.min(scores_array)),
                "max": float(np.max(scores_array)),
                "mean": float(np.mean(scores_array)),
                "median": float(np.median(scores_array)),
                "std": float(np.std(scores_array)),
                "range": float(np.max(scores_array) - np.min(scores_array)),
                "p5": float(np.percentile(scores_array, 5)),
                "p25": float(np.percentile(scores_array, 25)),
                "p50": float(np.percentile(scores_array, 50)),
                "p75": float(np.percentile(scores_array, 75)),
                "p95": float(np.percentile(scores_array, 95))
            }
            
            # Tạo histogram (10 bins từ 0-1)
            hist, bin_edges = np.histogram(scores_array, bins=10, range=(0.0, 1.0))
            histogram = {
                "bins": [float(edge) for edge in bin_edges],
                "counts": [int(count) for count in hist],
                "bin_centers": [float((bin_edges[i] + bin_edges[i+1]) / 2) for i in range(len(bin_edges)-1)]
            }
            
            # Đánh giá phân phối
            is_clustered = stats["range"] < 0.1 or stats["std"] < 0.05
            is_skewed_left = stats["mean"] < 0.3  # Tụ về 0
            is_skewed_right = stats["mean"] > 0.7  # Tụ về 1
            is_uniform = stats["std"] > 0.15 and 0.3 < stats["mean"] < 0.7  # Phân phối đều
            
            evaluation = {
                "is_clustered": is_clustered,
                "is_skewed_left": is_skewed_left,
                "is_skewed_right": is_skewed_right,
                "is_uniform": is_uniform,
                "quality": "good" if is_uniform and not is_clustered else "needs_improvement"
            }
            
            return jsonify({
                "success": True,
                "stats": stats,
                "histogram": histogram,
                "evaluation": evaluation
            })
            
    except Exception as e:
        current_app.logger.error(f"Error getting score distribution: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Error: {str(e)}"
        })


@main_bp.route("/api/model_status", methods=["GET"])
@admin_required
def model_status():
    """Get model status and user information"""
    try:
        global enhanced_cf_recommender
        
        if enhanced_cf_recommender and enhanced_cf_recommender.is_model_loaded():
            model_info = enhanced_cf_recommender.get_model_info()
            
            # Get users from database
            with current_app.db_engine.connect() as conn:
                users_query = text("""
                    SELECT TOP 10 u.userId, u.email, COUNT(r.movieId) as rating_count
                    FROM cine.[User] u
                    LEFT JOIN cine.Rating r ON u.userId = r.userId
                    WHERE u.status = 'active'
                    GROUP BY u.userId, u.email
                    ORDER BY u.userId
                """)
                
                users = conn.execute(users_query).fetchall()
                
                user_info = []
                for user in users:
                    user_id = user[0]
                    email = user[1]
                    rating_count = user[2]
                    in_model = user_id in enhanced_cf_recommender.user_mapping
                    
                    user_info.append({
                        "userId": user_id,
                        "email": email,
                        "ratingCount": rating_count,
                        "inModel": in_model
                    })
            
            return jsonify({
                "success": True,
                "modelInfo": model_info,
                "users": user_info
            })
        else:
            return jsonify({
                "success": False,
                "message": "Model not loaded"
            })
            
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Error getting model status: {str(e)}"
        })


# ==================== COLLABORATIVE FILTERING ENDPOINTS ====================

def get_watched_movie_ids(user_id, conn):
    """Lấy danh sách movieId của các phim đã xem bởi user"""
    try:
        result = conn.execute(text("""
            SELECT DISTINCT movieId 
            FROM cine.ViewHistory 
            WHERE userId = :user_id
        """), {"user_id": user_id})
        return {row[0] for row in result}
    except Exception as e:
        current_app.logger.warning(f"Error getting watched movies for user {user_id}: {e}")
        return set()

@main_bp.route("/api/generate_recommendations", methods=["POST"])
@login_required
def generate_recommendations():
    """Tạo hybrid recommendations cho user hiện tại và lưu vào database"""
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"success": False, "message": "Chưa đăng nhập"})
        
        # Lấy công tắc kiểm thử alpha từ request (nếu có)
        data = request.get_json() or {}
        alpha = data.get('alpha')  # alpha=1 → chỉ CF, alpha=0 → chỉ CB
        if alpha is not None:
            try:
                alpha = float(alpha)
                alpha = max(0.0, min(1.0, alpha))  # Clamp về 0-1
                current_app.logger.info(f"Using test switch alpha={alpha} (alpha=1→CF only, alpha=0→CB only)")
            except (ValueError, TypeError):
                alpha = None
                current_app.logger.warning(f"Invalid alpha value, using default weights")
        
        # Lấy CF recommendations
        cf_recommendations = []
        if enhanced_cf_recommender and enhanced_cf_recommender.is_model_loaded():
            cf_recommendations = enhanced_cf_recommender.get_user_recommendations(user_id, limit=30)
        
        # Lấy Content-Based recommendations
        cb_recommendations = []
        if content_recommender:
            cb_recommendations = content_recommender.get_user_recommendations(user_id, limit=30)
        
        # Kết hợp thành hybrid recommendations
        if not cf_recommendations and not cb_recommendations:
            return jsonify({"success": False, "message": "Không tìm thấy recommendations cho user này"})
        
        # Lấy danh sách phim đã xem để loại bỏ khỏi gợi ý
        with current_app.db_engine.connect() as conn:
            watched_movie_ids = get_watched_movie_ids(user_id, conn)
        
        # Lọc phim đã xem khỏi CF và CB recommendations
        if watched_movie_ids:
            cf_recommendations = [
                rec for rec in cf_recommendations 
                if (rec.get('movieId') or rec.get('id')) not in watched_movie_ids
            ]
            cb_recommendations = [
                rec for rec in cb_recommendations 
                if (rec.get('movieId') or rec.get('id')) not in watched_movie_ids
            ]
        
        # Sử dụng hybrid recommendations với công tắc alpha (nếu có)
        if alpha is not None:
            recommendations = hybrid_recommendations(
                cf_recommendations=cf_recommendations,
                cb_recommendations=cb_recommendations,
                alpha=alpha,  # Công tắc kiểm thử
                limit=50
            )
        else:
            # Mặc định: CF weight: 0.6, CB weight: 0.4
            recommendations = hybrid_recommendations(
                cf_recommendations=cf_recommendations,
                cb_recommendations=cb_recommendations,
                cf_weight=0.6,
                cb_weight=0.4,
                limit=50
            )
        
        if not recommendations:
            return jsonify({"success": False, "message": "Không tìm thấy recommendations sau khi merge"})
        
        # Lưu recommendations vào database
        # Sử dụng begin() để tự động quản lý transaction
        with current_app.db_engine.begin() as conn:
            # Xóa TẤT CẢ recommendations cũ của user này (bao gồm cả collaborative và hybrid cũ)
            deleted_count = conn.execute(text("""
                DELETE FROM [cine].[PersonalRecommendation] 
                WHERE userId = :user_id
            """), {"user_id": user_id}).rowcount
            
            # Logging chi tiết theo checklist: score_cf_norm, score_cb, score_hybrid, alpha, top-K
            alpha_used = alpha if alpha is not None else 0.6  # Default alpha = cf_weight
            top_k = min(10, len(recommendations))
            top_k_movies = recommendations[:top_k]
            
            # Log top-K với đầy đủ thông tin
            log_details = []
            for i, movie in enumerate(top_k_movies, 1):
                movie_id = movie.get('movieId') or movie.get('id')
                log_details.append({
                    "rank": i,
                    "movieId": movie_id,
                    "title": movie.get('title', 'N/A'),
                    "score_hybrid": movie.get('hybrid_score', 0),
                    "score_cf_norm": movie.get('cf_score_normalized', 0),
                    "score_cb": movie.get('cb_score_normalized', 0),
                    "score_cf_original": movie.get('cf_score', 0),
                    "score_cb_original": movie.get('cb_score', 0)
                })
            
            current_app.logger.info(
                f"Hybrid recommendations generated for user {user_id}: "
                f"alpha={alpha_used:.2f}, total={len(recommendations)}, top-K={top_k}. "
                f"Top-K details: {log_details}"
            )
            
            current_app.logger.info(f"Deleted {deleted_count} old recommendations for user {user_id}, generating {len(recommendations)} new hybrid recommendations")
            
            # Lấy max recId để tạo recId mới
            max_rec_id_result = conn.execute(text("""
                SELECT ISNULL(MAX(recId), 0) FROM cine.PersonalRecommendation
            """)).scalar()
            rec_id = max_rec_id_result + 1 if max_rec_id_result else 1
            
            # Lưu recommendations mới
            for rank, movie in enumerate(recommendations, 1):
                movie_id = movie.get('movieId') or movie.get('id')
                if not movie_id:
                    continue
                
                score = movie.get('hybrid_score') or movie.get('score') or movie.get('similarity', 0)
                
                conn.execute(text("""
                    INSERT INTO [cine].[PersonalRecommendation] 
                    (recId, userId, movieId, score, rank, algo, generatedAt, expiresAt)
                    VALUES (:rec_id, :user_id, :movie_id, :score, :rank, 'hybrid', GETUTCDATE(), DATEADD(day, 7, GETUTCDATE()))
                """), {
                    "rec_id": rec_id,
                    "user_id": user_id,
                    "movie_id": movie_id,
                    "score": score,
                    "rank": rank
                })
                rec_id += 1
            # begin() tự động commit khi exit context thành công
        
        return jsonify({
            "success": True, 
            "message": f"Đã tạo {len(recommendations)} hybrid recommendations",
            "recommendations": recommendations[:10],  # Trả về 10 recommendations đầu tiên
            "cf_count": len(cf_recommendations),
            "cb_count": len(cb_recommendations)
        })
        
    except Exception as e:
        current_app.logger.error(f"Error generating hybrid recommendations: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"Có lỗi xảy ra: {str(e)}"})


@main_bp.route("/api/get_recommendations")
@login_required
def get_recommendations():
    """Lấy recommendations đã lưu cho user hiện tại"""
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"success": False, "message": "Chưa đăng nhập"})
        
        limit = request.args.get('limit', 20, type=int)
        
        with current_app.config['odbc_connect'].connect() as conn:
            query = text("""
                SELECT TOP (:limit)
                    pr.movieId, pr.score, pr.rank, pr.generatedAt,
                    m.title, m.releaseYear, m.posterUrl, m.country,
                    AVG(CAST(r.value AS FLOAT)) as avgRating,
                    COUNT(r.movieId) as ratingCount,
                    CAST(STRING_AGG(g.name, ', ') WITHIN GROUP (ORDER BY g.name) AS NVARCHAR(MAX)) as genres
                FROM [cine].[PersonalRecommendation] pr
                INNER JOIN [cine].[Movie] m ON pr.movieId = m.movieId
                LEFT JOIN [cine].[Rating] r ON m.movieId = r.movieId
                LEFT JOIN [cine].[MovieGenre] mg ON pr.movieId = mg.movieId
                LEFT JOIN [cine].[Genre] g ON mg.genreId = g.genreId
                WHERE pr.userId = :user_id 
                    AND pr.expiresAt > GETDATE()
                    AND pr.movieId NOT IN (SELECT DISTINCT movieId FROM cine.ViewHistory WHERE userId = :user_id)
                GROUP BY pr.movieId, pr.score, pr.rank, pr.generatedAt, 
                         m.title, m.releaseYear, m.posterUrl, m.country
                ORDER BY pr.rank
            """)
            
            result = conn.execute(query, {"user_id": user_id, "limit": limit})
            recommendations = []
            
            for row in result:
                recommendations.append({
                    'movieId': row.movieId,
                    'title': row.title,
                    'releaseYear': row.releaseYear,
                    'posterUrl': row.posterUrl,
                    'country': row.country,
                    'score': round(float(row.score), 4),
                    'rank': row.rank,
                    'avgRating': round(float(row.avgRating), 2) if row.avgRating else 0.0,
                    'ratingCount': row.ratingCount,
                    'genres': row.genres or '',
                    'generatedAt': row.generatedAt.isoformat() if row.generatedAt else None
                })
        
        return jsonify({
            "success": True,
            "recommendations": recommendations
        })
        
    except Exception as e:
        print(f"Error getting recommendations: {e}")
        return jsonify({"success": False, "message": f"Có lỗi xảy ra: {str(e)}"})


@main_bp.route("/api/similar_movies/<int:movie_id>")
def get_similar_movies(movie_id):
    """Lấy danh sách phim tương tự dựa trên collaborative filtering"""
    try:
        limit = request.args.get('limit', 10, type=int)
        
        # Initialize recommenders if not already done
        global content_recommender, enhanced_cf_recommender
        if enhanced_cf_recommender is None:
            init_recommenders()
        
        # Chỉ sử dụng Enhanced CF
        if enhanced_cf_recommender and enhanced_cf_recommender.is_model_loaded():
            similar_movies = enhanced_cf_recommender.get_similar_movies(movie_id, limit)
        else:
            # Fallback: sử dụng content-based recommender
            if content_recommender is None:
                init_recommenders()
            similar_movies = content_recommender.get_related_movies(movie_id, limit)
        
        return jsonify({
            "success": True,
            "similar_movies": similar_movies
        })
        
    except Exception as e:
        print(f"Error getting similar movies: {e}")
        return jsonify({"success": False, "message": f"Có lỗi xảy ra: {str(e)}"})


@main_bp.route("/api/trending_movies")
def get_trending_movies():
    """Lấy danh sách phim trending dựa trên views và ratings trong 7 ngày"""
    try:
        limit = request.args.get('limit', 10, type=int)
        
        # Kiểm tra cache
        global trending_cache
        if trending_cache['data'] and trending_cache['timestamp']:
            elapsed = time.time() - trending_cache['timestamp']
            if elapsed < trending_cache['ttl']:
                # Trả về cache nếu còn trong TTL và limit phù hợp
                cached_movies = trending_cache['data'][:limit]
                return jsonify({
                    "success": True,
                    "trending_movies": cached_movies,
                    "cached": True
                })
        
        with current_app.db_engine.connect() as conn:
            # Step 1: Lấy phim trending dựa trên views + ratings trong 7 ngày
            trending_query = text("""
                SELECT 
                    m.movieId, 
                    m.title, 
                    m.releaseYear, 
                    m.country, 
                    m.posterUrl,
                    
                    -- View metrics (7 days)
                    COUNT(DISTINCT vh.userId) as unique_viewers_7d,
                    COUNT(DISTINCT vh.historyId) as view_count_7d,
                    
                    -- Rating metrics (7 days)
                    AVG(CAST(r.value AS FLOAT)) as avg_rating_7d,
                    COUNT(DISTINCT r.userId) as rating_count_7d,
                    
                    -- Combined trending score (70% view, 30% rating)
                    (
                        -- View component (70%): view count + unique viewers * 2
                        (
                            COUNT(DISTINCT vh.historyId) +
                            COUNT(DISTINCT vh.userId) * 2
                        ) * 0.7 +
                        
                        -- Rating component (30%): avg_rating * rating_count * 2
                        COALESCE(
                            AVG(CAST(r.value AS FLOAT)) * NULLIF(COUNT(DISTINCT r.userId), 0) * 2,
                            0
                        ) * 0.3
                    ) as trending_score,
                    
                    STRING_AGG(DISTINCT g.name, ', ') WITHIN GROUP (ORDER BY g.name) as genres
                    
                FROM cine.Movie m
                
                LEFT JOIN cine.ViewHistory vh 
                    ON m.movieId = vh.movieId 
                    AND vh.startedAt >= DATEADD(day, -7, GETDATE())
                
                LEFT JOIN cine.Rating r 
                    ON m.movieId = r.movieId 
                    AND r.ratedAt >= DATEADD(day, -7, GETDATE())
                    AND r.ratedAt IS NOT NULL
                
                LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                
                GROUP BY m.movieId, m.title, m.releaseYear, m.country, m.posterUrl
                
                HAVING 
                    -- Chỉ lấy phim có hoạt động trong 7 ngày
                    COUNT(DISTINCT vh.historyId) > 0 OR 
                    COUNT(DISTINCT r.userId) > 0
                
                ORDER BY trending_score DESC
            """)
            
            trending_rows = conn.execute(trending_query).mappings().all()
            
            trending_movies = []
            for row in trending_rows:
                trending_movies.append({
                    'movieId': row.movieId,
                    'title': row.title,
                    'releaseYear': row.releaseYear,
                    'country': row.country,
                    'posterUrl': get_poster_or_dummy(row.posterUrl, row.title),
                    'avgRating': round(float(row.avg_rating_7d or 0), 2),
                    'ratingCount': row.rating_count_7d or 0,
                    'viewCount': row.view_count_7d or 0,
                    'uniqueViewers': row.unique_viewers_7d or 0,
                    'genres': row.genres or '',
                    'trending_score': float(row.trending_score or 0)
                })
            
            # Step 2: Nếu không đủ limit, bổ sung bằng phim mới nhất
            if len(trending_movies) < limit:
                # Lấy danh sách movieId đã có
                existing_movie_ids = [m['movieId'] for m in trending_movies]
                placeholders = ','.join([':id' + str(i) for i in range(len(existing_movie_ids))])
                params = {f'id{i}': mid for i, mid in enumerate(existing_movie_ids)}
                
                # Validate và sanitize fallback limit để tránh SQL injection
                fallback_limit = max(1, limit - len(trending_movies))  # Đảm bảo >= 1
                validated_fallback_limit = validate_limit(fallback_limit, max_limit=100, default=10)
                top_clause = safe_top_clause(validated_fallback_limit, max_limit=100)
                
                # Query lấy phim mới nhất (ngoại trừ những phim đã có)
                # Sử dụng validated limit thay vì f-string trực tiếp
                fallback_query = f"""
                    SELECT {top_clause}
                        m.movieId, 
                        m.title, 
                        m.releaseYear, 
                        m.country, 
                        m.posterUrl,
                        AVG(CAST(r.value AS FLOAT)) as avgRating,
                        COUNT(r.movieId) as ratingCount,
                        STRING_AGG(DISTINCT g.name, ', ') WITHIN GROUP (ORDER BY g.name) as genres
                    FROM cine.Movie m
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                    LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                    WHERE m.releaseYear IS NOT NULL
                    """
                
                if existing_movie_ids:
                    fallback_query += f" AND m.movieId NOT IN ({placeholders})"
                
                fallback_query += """
                    GROUP BY m.movieId, m.title, m.releaseYear, m.country, m.posterUrl
                    ORDER BY m.releaseYear DESC, m.movieId DESC
                """
                
                fallback_rows = conn.execute(text(fallback_query), params).mappings().all()
                
                for row in fallback_rows:
                    trending_movies.append({
                        'movieId': row.movieId,
                        'title': row.title,
                        'releaseYear': row.releaseYear,
                        'country': row.country,
                        'posterUrl': get_poster_or_dummy(row.posterUrl, row.title),
                        'avgRating': round(float(row.avgRating or 0), 2),
                        'ratingCount': row.ratingCount or 0,
                        'viewCount': 0,
                        'uniqueViewers': 0,
                        'genres': row.genres or '',
                        'trending_score': 0
                    })
            
            # Chỉ trả về top limit
            trending_movies = trending_movies[:limit]
            
            # Update cache
            trending_cache['data'] = trending_movies
            trending_cache['timestamp'] = time.time()
        
        return jsonify({
            "success": True,
            "trending_movies": trending_movies[:limit],
            "cached": False
        })
        
    except Exception as e:
        print(f"Error getting trending movies: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Có lỗi xảy ra: {str(e)}"})


@main_bp.route("/api/user_rating_history")
@login_required
def get_user_rating_history():
    """Lấy lịch sử đánh giá của user"""
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"success": False, "message": "Chưa đăng nhập"})
        
        limit = request.args.get('limit', 20, type=int)
        
        # Khởi tạo enhanced CF recommender
        cf_recommender = EnhancedCFRecommender(current_app.db_engine)
        
        # Lấy rating history
        rating_history = cf_recommender.get_user_rating_history(user_id, limit)
        
        return jsonify({
            "success": True,
            "rating_history": rating_history
        })
        
    except Exception as e:
        print(f"Error getting rating history: {e}")
        return jsonify({"success": False, "message": f"Có lỗi xảy ra: {str(e)}"})


@main_bp.route("/api/model_status")
def get_model_status():
    """Kiểm tra trạng thái của model collaborative filtering"""
    try:
        # Initialize recommenders if not already done
        global content_recommender, enhanced_cf_recommender
        if enhanced_cf_recommender is None:
            init_recommenders()
        
        if enhanced_cf_recommender:
            model_info = enhanced_cf_recommender.get_model_info()
        else:
            model_info = {"status": "not_loaded", "message": "Recommender not initialized"}
        
        return jsonify({
            "success": True,
            "model_info": model_info
        })
        
    except Exception as e:
        print(f"Error getting model status: {e}")
        return jsonify({"success": False, "message": f"Có lỗi xảy ra: {str(e)}"})

@main_bp.route("/api/user_preference_analysis")
@login_required
def user_preference_analysis():
    """Phân tích sở thích của user dựa trên lịch sử rating"""
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"success": False, "message": "Chưa đăng nhập"})
        
        with current_app.db_engine.connect() as conn:
            # Phân tích rating theo thể loại
            genre_ratings = conn.execute(text("""
                SELECT g.name as genre, 
                       AVG(CAST(r.value AS FLOAT)) as avg_rating,
                       COUNT(r.movieId) as rating_count
                FROM cine.Rating r
                JOIN cine.MovieGenre mg ON r.movieId = mg.movieId
                JOIN cine.Genre g ON mg.genreId = g.genreId
                WHERE r.userId = :user_id
                GROUP BY g.name
                ORDER BY avg_rating DESC, rating_count DESC
            """), {"user_id": user_id}).mappings().all()
            
            # Phân tích rating theo năm
            year_ratings = conn.execute(text("""
                SELECT m.releaseYear as year,
                       AVG(CAST(r.value AS FLOAT)) as avg_rating,
                       COUNT(r.movieId) as rating_count
                FROM cine.Rating r
                JOIN cine.Movie m ON r.movieId = m.movieId
                WHERE r.userId = :user_id AND m.releaseYear IS NOT NULL
                GROUP BY m.releaseYear
                ORDER BY avg_rating DESC, rating_count DESC
            """), {"user_id": user_id}).mappings().all()
            
            # Phân tích rating theo quốc gia
            country_ratings = conn.execute(text("""
                SELECT m.country,
                       AVG(CAST(r.value AS FLOAT)) as avg_rating,
                       COUNT(r.movieId) as rating_count
                FROM cine.Rating r
                JOIN cine.Movie m ON r.movieId = m.movieId
                WHERE r.userId = :user_id AND m.country IS NOT NULL
                GROUP BY m.country
                ORDER BY avg_rating DESC, rating_count DESC
            """), {"user_id": user_id}).mappings().all()
            
            # Thống kê tổng quan
            total_ratings = conn.execute(text("""
                SELECT COUNT(*) as count FROM cine.Rating WHERE userId = :user_id
            """), {"user_id": user_id}).scalar()
            
            avg_user_rating = conn.execute(text("""
                SELECT AVG(CAST(value AS FLOAT)) as avg FROM cine.Rating WHERE userId = :user_id
            """), {"user_id": user_id}).scalar() or 0
            
            # Phân tích độ đa dạng sở thích
            diversity_score = len(genre_ratings) / 10.0  # Normalize to 0-1
            diversity_score = min(diversity_score, 1.0)
            
            return jsonify({
                "success": True,
                "analysis": {
                    "user_id": user_id,
                    "total_ratings": total_ratings,
                    "average_rating": round(avg_user_rating, 2),
                    "diversity_score": round(diversity_score, 2),
                    "preferred_genres": [
                        {
                            "genre": row["genre"],
                            "avg_rating": round(row["avg_rating"], 2),
                            "rating_count": row["rating_count"]
                        }
                        for row in genre_ratings[:5]
                    ],
                    "preferred_years": [
                        {
                            "year": row["year"],
                            "avg_rating": round(row["avg_rating"], 2),
                            "rating_count": row["rating_count"]
                        }
                        for row in year_ratings[:5]
                    ],
                    "preferred_countries": [
                        {
                            "country": row["country"],
                            "avg_rating": round(row["avg_rating"], 2),
                            "rating_count": row["rating_count"]
                        }
                        for row in country_ratings[:5]
                    ]
                }
            })
            
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@main_bp.route("/api/user_data_status")
@login_required
def user_data_status():
    """Kiểm tra dữ liệu của user (rating, favorite, etc.)"""
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"success": False, "message": "Chưa đăng nhập"})
        
        with current_app.db_engine.connect() as conn:
            # Kiểm tra số lượng rating
            rating_count = conn.execute(text("""
                SELECT COUNT(*) as count FROM cine.Rating WHERE userId = :user_id
            """), {"user_id": user_id}).scalar()
            
            # Kiểm tra số lượng favorite
            favorite_count = conn.execute(text("""
                SELECT COUNT(*) as count FROM cine.Favorite WHERE userId = :user_id
            """), {"user_id": user_id}).scalar()
            
            # Kiểm tra số lượng watchlist
            watchlist_count = conn.execute(text("""
                SELECT COUNT(*) as count FROM cine.Watchlist WHERE userId = :user_id
            """), {"user_id": user_id}).scalar()
            
            # Kiểm tra PersonalRecommendation
            pr_count = conn.execute(text("""
                SELECT COUNT(*) as count FROM cine.PersonalRecommendation WHERE userId = :user_id
            """), {"user_id": user_id}).scalar()
            
            return jsonify({
                "success": True,
                "user_data": {
                    "user_id": user_id,
                    "rating_count": rating_count,
                    "favorite_count": favorite_count,
                    "watchlist_count": watchlist_count,
                    "personal_recommendation_count": pr_count
                }
            })
            
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@main_bp.route("/api/cleanup_watched_recommendations", methods=["POST"])
@login_required
def cleanup_watched_recommendations():
    """Dọn dẹp recommendations chứa phim đã xem"""
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"success": False, "message": "Chưa đăng nhập"})
        
        with current_app.db_engine.begin() as conn:
            # Xóa phim đã xem khỏi PersonalRecommendation
            deleted_pr = conn.execute(text("""
                DELETE FROM cine.PersonalRecommendation 
                WHERE userId = :user_id 
                    AND movieId IN (
                        SELECT DISTINCT movieId 
                        FROM cine.ViewHistory 
                        WHERE userId = :user_id
                    )
            """), {"user_id": user_id}).rowcount
            
            # Xóa phim đã xem khỏi ColdStartRecommendations
            deleted_csr = conn.execute(text("""
                DELETE FROM cine.ColdStartRecommendations 
                WHERE userId = :user_id 
                    AND movieId IN (
                        SELECT DISTINCT movieId 
                        FROM cine.ViewHistory 
                        WHERE userId = :user_id
                    )
            """), {"user_id": user_id}).rowcount
        
        return jsonify({
            "success": True,
            "message": f"Đã xóa {deleted_pr} recommendations cá nhân và {deleted_csr} cold start recommendations chứa phim đã xem"
        })
        
    except Exception as e:
        current_app.logger.error(f"Error cleaning up watched recommendations: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"Có lỗi xảy ra: {str(e)}"})

@main_bp.route("/api/personalized_recommendations")
@login_required
def get_personalized_recommendations():
    """Lấy gợi ý phim cá nhân hóa cho user hiện tại"""
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"success": False, "message": "Chưa đăng nhập"})
        
        limit = request.args.get('limit', 12, type=int)
        force_refresh = request.args.get('refresh', 'false').lower() == 'true'
        
        # ✅ TỐI ƯU: Kiểm tra recommendations đã lưu trong database trước
        if not force_refresh:
            with current_app.db_engine.connect() as conn:
                existing_recs = conn.execute(text("""
                    SELECT TOP (:limit)
                        pr.movieId, pr.score, pr.rank,
                        m.title, m.releaseYear, m.posterUrl, m.country,
                        AVG(CAST(r.value AS FLOAT)) as avgRating,
                        COUNT(r.movieId) as ratingCount,
                        STUFF((
                            SELECT ', ' + g2.name
                            FROM cine.MovieGenre mg2
                            INNER JOIN cine.Genre g2 ON mg2.genreId = g2.genreId
                            WHERE mg2.movieId = pr.movieId
                            ORDER BY g2.name
                            FOR XML PATH(''), TYPE
                        ).value('.', 'NVARCHAR(MAX)'), 1, 2, '') as genres
                    FROM cine.PersonalRecommendation pr
                    INNER JOIN cine.Movie m ON pr.movieId = m.movieId
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    WHERE pr.userId = :user_id 
                        AND pr.algo = 'hybrid'
                        AND pr.expiresAt > GETUTCDATE()
                        AND pr.movieId NOT IN (SELECT DISTINCT movieId FROM cine.ViewHistory WHERE userId = :user_id)
                    GROUP BY pr.movieId, pr.score, pr.rank, 
                             m.title, m.releaseYear, m.posterUrl, m.country
                    ORDER BY pr.rank
                """), {"user_id": user_id, "limit": limit}).mappings().all()
                
                if existing_recs:
                    # Trả về recommendations từ database (nhanh hơn nhiều)
                    recommendations = [
                        {
                            "movieId": row["movieId"],
                            "title": row["title"],
                            "poster": row["posterUrl"] if row["posterUrl"] and row["posterUrl"] != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={row['title'][:20].replace(' ', '+')}",
                            "releaseYear": row["releaseYear"],
                            "country": row["country"],
                            "genres": row["genres"] or "",
                            "recommendationScore": round(float(row["score"]), 4),
                            "avgRating": round(float(row["avgRating"] or 0), 2),
                            "ratingCount": int(row["ratingCount"] or 0),
                            "reason": "Dựa trên kết hợp Collaborative Filtering và Content-Based (Hybrid)"
                        }
                        for row in existing_recs
                    ]
                    return jsonify({
                        "success": True,
                        "recommendations": recommendations,
                        "cached": True
                    })
        
        # Chỉ tính toán lại nếu force_refresh hoặc không có recommendations trong database
        # Initialize recommenders if not already done
        global content_recommender, enhanced_cf_recommender
        if enhanced_cf_recommender is None:
            init_recommenders()
        
        recommendations = []
        
        # ✅ THAY ĐỔI: Luôn sử dụng Hybrid thay vì CF
        # Lấy công tắc kiểm thử alpha từ query parameter (nếu có)
        alpha = request.args.get('alpha', type=float)
        if alpha is not None:
            alpha = max(0.0, min(1.0, alpha))  # Clamp về 0-1
            current_app.logger.info(f"Using test switch alpha={alpha} for personalized recommendations")
        
        current_app.logger.info(f"Generating new hybrid recommendations for user {user_id}")
        
        # Lấy danh sách phim đã xem để loại bỏ khỏi gợi ý
        with current_app.db_engine.connect() as conn:
            watched_movie_ids = get_watched_movie_ids(user_id, conn)
        
        # Lấy CF recommendations
        cf_recommendations = []
        if enhanced_cf_recommender and enhanced_cf_recommender.is_model_loaded():
            try:
                cf_recommendations = enhanced_cf_recommender.get_user_recommendations(user_id, limit=limit * 2)
            except Exception as e:
                current_app.logger.warning(f"CF recommendations failed for user {user_id}: {e}")
            
        # Lấy CB recommendations
        cb_recommendations = []
        if content_recommender:
            try:
                cb_recommendations = content_recommender.get_user_recommendations(user_id, limit=limit * 2)
            except Exception as e:
                current_app.logger.warning(f"CB recommendations failed for user {user_id}: {e}")
        
        # Lọc phim đã xem khỏi CF và CB recommendations
        if watched_movie_ids:
            cf_recommendations = [
                rec for rec in cf_recommendations 
                if (rec.get('movieId') or rec.get('id')) not in watched_movie_ids
            ]
            cb_recommendations = [
                rec for rec in cb_recommendations 
                if (rec.get('movieId') or rec.get('id')) not in watched_movie_ids
            ]
        
        # Generate hybrid recommendations với công tắc alpha (nếu có)
        if cf_recommendations or cb_recommendations:
            if alpha is not None:
                hybrid_recs = hybrid_recommendations(
                    cf_recommendations=cf_recommendations,
                    cb_recommendations=cb_recommendations,
                    alpha=alpha,  # Công tắc kiểm thử
                    limit=limit
                )
            else:
                hybrid_recs = hybrid_recommendations(
                    cf_recommendations=cf_recommendations,
                    cb_recommendations=cb_recommendations,
                    cf_weight=0.6,
                    cb_weight=0.4,
                    limit=limit
                )
            
            if hybrid_recs:
                # ✅ TỐI ƯU: Luôn lưu vào database để dùng cho lần sau
                with current_app.db_engine.begin() as conn:
                    # Xóa recommendations cũ (chỉ hybrid)
                    conn.execute(text("""
                        DELETE FROM cine.PersonalRecommendation 
                        WHERE userId = :user_id AND algo = 'hybrid'
                    """), {"user_id": user_id})
                    
                    # Lưu hybrid recommendations mới
                    max_rec_id = conn.execute(text("""
                        SELECT ISNULL(MAX(recId), 0) FROM cine.PersonalRecommendation
                    """)).scalar() or 0
                    
                    for rank, rec in enumerate(hybrid_recs, 1):
                        rec_id = max_rec_id + rank
                        movie_id = rec.get('movieId') or rec.get('id')
                        if not movie_id:
                            continue
                        
                        score = rec.get('hybrid_score') or rec.get('score') or rec.get('similarity', 0)
                        conn.execute(text("""
                            INSERT INTO cine.PersonalRecommendation 
                            (recId, userId, movieId, score, rank, algo, generatedAt, expiresAt)
                            VALUES (:rec_id, :user_id, :movie_id, :score, :rank, 'hybrid', GETUTCDATE(), DATEADD(day, 7, GETUTCDATE()))
                        """), {
                            "rec_id": rec_id,
                            "user_id": user_id,
                            "movie_id": movie_id,
                            "score": score,
                            "rank": rank
                        })
                current_app.logger.info(f"Saved {len(hybrid_recs)} hybrid recommendations to database")
                
                # Format recommendations
                recommendations = [
                    {
                        "movieId": rec.get("movieId") or rec.get("id"),
                        "title": rec.get("title"),
                        "poster": rec.get("posterUrl") if rec.get("posterUrl") and rec.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={rec.get('title', '')[:20].replace(' ', '+')}",
                        "releaseYear": rec.get("releaseYear"),
                        "country": rec.get("country"),
                        "genres": rec.get("genres", ""),
                        "recommendationScore": round(rec.get('hybrid_score') or rec.get('score') or rec.get('similarity', 0), 4),
                        "avgRating": round(rec.get("avgRating", 0), 2),
                        "ratingCount": rec.get("ratingCount", 0),
                        "reason": "Dựa trên kết hợp Collaborative Filtering và Content-Based (Hybrid)"
                    }
                    for rec in hybrid_recs
                ]
                
            return jsonify({
                "success": True,
                    "message": f"Đã tạo {len(recommendations)} gợi ý cá nhân hóa (Hybrid)",
                "recommendations": recommendations,
                    "algorithm": "Hybrid (CF + CB)",
                    "userInModel": True,
                    "cf_count": len(cf_recommendations),
                    "cb_count": len(cb_recommendations)
                })
        
        # Không có recommendations
            return jsonify({
                "success": False,
                "message": "Không tìm thấy gợi ý cá nhân hóa. Hãy đánh giá thêm phim để có gợi ý tốt hơn.",
                "recommendations": [],
            "algorithm": "Hybrid (CF + CB)",
            "userInModel": False
        })
            
    except Exception as e:
        current_app.logger.error(f"Error getting personalized recommendations: {e}")
        return jsonify({"success": False, "message": str(e)})

@main_bp.route("/api/cold_start_recommendations")
@login_required
def get_cold_start_recommendations_api():
    """API endpoint để lấy cold start recommendations"""
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"success": False, "message": "Chưa đăng nhập"})
        
        limit = request.args.get('limit', 12, type=int)
        force_refresh = request.args.get('refresh', 'false').lower() == 'true'
        
        with current_app.db_engine.connect() as conn:
            # Kiểm tra xem user có đủ dữ liệu không
            rating_count = conn.execute(text("""
                SELECT COUNT(*) FROM cine.Rating WHERE userId = :user_id
            """), {"user_id": user_id}).scalar()
            
            view_count = conn.execute(text("""
                SELECT COUNT(*) FROM cine.ViewHistory WHERE userId = :user_id
            """), {"user_id": user_id}).scalar()
            
            total_interactions = rating_count + view_count
            
            if total_interactions >= 5:
                return jsonify({
                    "success": False,
                    "message": "User đã có đủ dữ liệu, không cần cold start",
                    "interactions": total_interactions
                })
            
            # Lấy cold start recommendations từ database
            if not force_refresh:
                existing_recs = conn.execute(text("""
                    SELECT TOP (:limit)
                        csr.movieId, csr.score, csr.rank, csr.source, csr.reason,
                        m.title, m.posterUrl, m.releaseYear, m.country,
                        AVG(CAST(r.value AS FLOAT)) AS avgRating,
                        COUNT(r.movieId) AS ratingCount,
                        CAST(STRING_AGG(g.name, ', ') WITHIN GROUP (ORDER BY g.name) AS NVARCHAR(MAX)) as genres
                    FROM cine.ColdStartRecommendations csr
                    INNER JOIN cine.Movie m ON csr.movieId = m.movieId
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                    LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                    WHERE csr.userId = :user_id 
                        AND csr.expiresAt > GETUTCDATE()
                        AND csr.movieId NOT IN (SELECT DISTINCT movieId FROM cine.ViewHistory WHERE userId = :user_id)
                    GROUP BY csr.movieId, csr.score, csr.rank, csr.source, csr.reason,
                             m.title, m.posterUrl, m.releaseYear, m.country
                    ORDER BY csr.rank
                """), {"user_id": user_id, "limit": limit}).mappings().all()
                
                if existing_recs:
                    recommendations = []
                    for row in existing_recs:
                        recommendations.append({
                            "movieId": row["movieId"],
                            "title": row["title"],
                            "posterUrl": get_poster_or_dummy(row.get("posterUrl"), row["title"]),
                            "releaseYear": row["releaseYear"],
                            "country": row["country"],
                            "score": row["score"],
                            "rank": row["rank"],
                            "avgRating": round(float(row["avgRating"]), 2) if row["avgRating"] else 0.0,
                            "ratingCount": row["ratingCount"],
                            "genres": row["genres"] or "",
                            "source": row["source"],
                            "reason": row["reason"]
                        })
                    
                    return jsonify({
                        "success": True,
                        "recommendations": recommendations,
                        "source": "cached"
                    })
            
            # Tạo cold start recommendations mới
            recommendations = get_cold_start_recommendations(user_id, conn)
            
            return jsonify({
                "success": True,
                "recommendations": recommendations,
                "source": "generated",
                "interactions": total_interactions
            })
            
    except Exception as e:
        print(f"Error getting cold start recommendations: {e}")
        return jsonify({
            "success": False,
            "message": f"Có lỗi xảy ra: {str(e)}"
        })

@main_bp.route("/onboarding")
@login_required
def onboarding():
    """Trang onboarding cho user mới"""
    return render_template("onboarding.html")

@main_bp.route("/api/genres")
def get_genres():
    """API endpoint để lấy danh sách thể loại phim"""
    try:
        with current_app.db_engine.connect() as conn:
            genres = conn.execute(text("""
                SELECT g.genreId, g.name, COUNT(mg.movieId) as movie_count
                FROM cine.Genre g
                LEFT JOIN cine.MovieGenre mg ON g.genreId = mg.genreId
                GROUP BY g.genreId, g.name
                HAVING COUNT(mg.movieId) > 0
                ORDER BY movie_count DESC, g.name
            """)).mappings().all()
            
            return jsonify({
                "success": True,
                "genres": [
                    {
                        "genreId": genre["genreId"],
                        "name": genre["name"],
                        "movie_count": genre["movie_count"]
                    }
                    for genre in genres
                ]
            })
    except Exception as e:
        print(f"Error getting genres: {e}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra khi lấy danh sách thể loại"})

@main_bp.route("/api/actors")
def get_actors():
    """API endpoint để lấy danh sách diễn viên phổ biến"""
    try:
        with current_app.db_engine.connect() as conn:
            actors = conn.execute(text("""
                SELECT TOP 20 a.actorId, a.name, COUNT(ma.movieId) as movie_count
                FROM cine.Actor a
                LEFT JOIN cine.MovieActor ma ON a.actorId = ma.actorId
                GROUP BY a.actorId, a.name
                HAVING COUNT(ma.movieId) > 0
                ORDER BY movie_count DESC, a.name
            """)).mappings().all()
            
            return jsonify({
                "success": True,
                "actors": [
                    {
                        "actorId": actor["actorId"],
                        "name": actor["name"],
                        "movie_count": actor["movie_count"]
                    }
                    for actor in actors
                ]
            })
    except Exception as e:
        print(f"Error getting actors: {e}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra khi lấy danh sách diễn viên"})

@main_bp.route("/api/directors")
def get_directors():
    """API endpoint để lấy danh sách đạo diễn phổ biến"""
    try:
        with current_app.db_engine.connect() as conn:
            directors = conn.execute(text("""
                SELECT TOP 20 d.directorId, d.name, COUNT(md.movieId) as movie_count
                FROM cine.Director d
                LEFT JOIN cine.MovieDirector md ON d.directorId = md.directorId
                GROUP BY d.directorId, d.name
                HAVING COUNT(md.movieId) > 0
                ORDER BY movie_count DESC, d.name
            """)).mappings().all()
            
            return jsonify({
                "success": True,
                "directors": [
                    {
                        "directorId": director["directorId"],
                        "name": director["name"],
                        "movie_count": director["movie_count"]
                    }
                    for director in directors
                ]
            })
    except Exception as e:
        print(f"Error getting directors: {e}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra khi lấy danh sách đạo diễn"})

@main_bp.route("/api/test-comment-system", methods=["GET"])
def test_comment_system():
    """API test để kiểm tra comment"""
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"success": False, "message": "Chưa đăng nhập"})
        
        with current_app.db_engine.connect() as conn:
            # Test Comment table structure
            try:
                comment_count = conn.execute(text("""
                    SELECT COUNT(*) FROM [cine].[Comment] WHERE userId = :user_id
                """), {"user_id": user_id}).scalar()
                comment_status = f"✅ Comment table OK ({comment_count} comments)"
            except Exception as e:
                comment_status = f"❌ Comment error: {str(e)}"
            
            # Test Comment structure
            try:
                max_id = conn.execute(text("""
                    SELECT ISNULL(MAX(commentId), 0) + 1 FROM cine.Comment
                """)).scalar()
                max_id_status = f"✅ Max commentId: {max_id}"
            except Exception as e:
                max_id_status = f"❌ Max ID error: {str(e)}"
            
            return jsonify({
                "success": True,
                "user_id": user_id,
                "comment_table": comment_status,
                "max_id": max_id_status
            })
            
    except Exception as e:
        return jsonify({
            "success": False, 
            "message": f"Test error: {str(e)}"
        })

@main_bp.route("/api/save_user_preferences", methods=["POST"])
@login_required
def save_user_preferences():
    """API endpoint để lưu sở thích của user"""
    try:
        user_id = session.get("user_id")
        data = request.get_json()
        
        print(f"Debug - Saving preferences for user {user_id}: {data}")
        
        genres = data.get('genres', [])
        actors = data.get('actors', [])
        directors = data.get('directors', [])
        
        if not genres:
            return jsonify({"success": False, "message": "Vui lòng chọn ít nhất 1 thể loại phim"})
        
        with current_app.db_engine.begin() as conn:
            # Tạo bảng UserPreference nếu chưa tồn tại
            try:
                conn.execute(text("""
                    IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'UserPreference' AND schema_id = SCHEMA_ID('cine'))
                    BEGIN
                        CREATE TABLE [cine].[UserPreference] (
                            [prefId] bigint IDENTITY(1,1) NOT NULL,
                            [userId] bigint NOT NULL,
                            [preferenceType] nvarchar(20) NOT NULL,
                            [preferenceId] bigint NOT NULL,
                            [createdAt] datetime2 NOT NULL DEFAULT (sysutcdatetime())
                        );
                    END
                """))
                print("Debug - UserPreference table created or already exists")
            except Exception as e:
                print(f"Debug - Error creating UserPreference table: {e}")
            
            # Thêm cột hasCompletedOnboarding nếu chưa tồn tại
            try:
                conn.execute(text("""
                    IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('[cine].[User]') AND name = 'hasCompletedOnboarding')
                    BEGIN
                        ALTER TABLE [cine].[User] ADD [hasCompletedOnboarding] bit NOT NULL DEFAULT (0);
                    END
                """))
                print("Debug - hasCompletedOnboarding column added or already exists")
            except Exception as e:
                print(f"Debug - Error adding hasCompletedOnboarding column: {e}")
            
            # Xóa preferences cũ
            try:
                conn.execute(text("""
                    DELETE FROM cine.UserPreference WHERE userId = :user_id
                """), {"user_id": user_id})
                print("Debug - Deleted old preferences")
            except Exception as e:
                print(f"Debug - Error deleting old preferences: {e}")
            
            # Lưu genre preferences
            for genre_id in genres:
                try:
                    conn.execute(text("""
                        INSERT INTO cine.UserPreference (userId, preferenceType, preferenceId, createdAt)
                        VALUES (:user_id, 'genre', :preference_id, GETDATE())
                    """), {"user_id": user_id, "preference_id": genre_id})
                    print(f"Debug - Saved genre preference: {genre_id}")
                except Exception as e:
                    print(f"Debug - Error saving genre preference {genre_id}: {e}")
            
            # Lưu actor preferences
            for actor_id in actors:
                try:
                    conn.execute(text("""
                        INSERT INTO cine.UserPreference (userId, preferenceType, preferenceId, createdAt)
                        VALUES (:user_id, 'actor', :preference_id, GETDATE())
                    """), {"user_id": user_id, "preference_id": actor_id})
                    print(f"Debug - Saved actor preference: {actor_id}")
                except Exception as e:
                    print(f"Debug - Error saving actor preference {actor_id}: {e}")
            
            # Lưu director preferences
            for director_id in directors:
                try:
                    conn.execute(text("""
                        INSERT INTO cine.UserPreference (userId, preferenceType, preferenceId, createdAt)
                        VALUES (:user_id, 'director', :preference_id, GETDATE())
                    """), {"user_id": user_id, "preference_id": director_id})
                    print(f"Debug - Saved director preference: {director_id}")
                except Exception as e:
                    print(f"Debug - Error saving director preference {director_id}: {e}")
            
            # Đánh dấu user đã hoàn thành onboarding
            try:
                conn.execute(text("""
                    UPDATE cine.[User] 
                    SET hasCompletedOnboarding = 1, lastLoginAt = GETDATE()
                    WHERE userId = :user_id
                """), {"user_id": user_id})
                print("Debug - Updated user onboarding status")
            except Exception as e:
                print(f"Debug - Error updating user status: {e}")
            
            # Tạo cold start recommendations dựa trên preferences
            try:
                generate_preference_based_recommendations(user_id, conn)
                print("Debug - Generated preference-based recommendations")
            except Exception as e:
                print(f"Debug - Error generating recommendations: {e}")
            
        return jsonify({
            "success": True,
            "message": "Đã lưu sở thích thành công",
            "preferences": {
                "genres": len(genres),
                "actors": len(actors),
                "directors": len(directors)
            }
        })
        
    except Exception as e:
        print(f"Error saving user preferences: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Có lỗi xảy ra khi lưu sở thích: {str(e)}"})

def generate_preference_based_recommendations(user_id, conn):
    """Tạo recommendations dựa trên preferences của user"""
    try:
        # Lấy preferences của user
        preferences = conn.execute(text("""
            SELECT preferenceType, preferenceId FROM cine.UserPreference 
            WHERE userId = :user_id
        """), {"user_id": user_id}).mappings().all()
        
        if not preferences:
            return []
        
        recommendations = []
        
        # Lấy phim theo genre preferences
        genre_ids = [p["preferenceId"] for p in preferences if p["preferenceType"] == "genre"]
        if genre_ids:
            # Tạo placeholders cho genre_ids
            placeholders = ','.join([f':genre_{i}' for i in range(len(genre_ids))])
            params = {f'genre_{i}': genre_id for i, genre_id in enumerate(genre_ids)}
            
            sql_query = f"""
                SELECT TOP 6
                    m.movieId, m.title, m.posterUrl, m.releaseYear, m.country,
                    AVG(CAST(r.value AS FLOAT)) AS avgRating,
                    COUNT(r.movieId) AS ratingCount,
                    CAST(STRING_AGG(g.name, ', ') WITHIN GROUP (ORDER BY g.name) AS NVARCHAR(MAX)) as genres
                FROM cine.Movie m
                INNER JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                WHERE mg.genreId IN ({placeholders})
                GROUP BY m.movieId, m.title, m.posterUrl, m.releaseYear, m.country
                ORDER BY avgRating DESC, COUNT(r.movieId) DESC
            """
            genre_movies = conn.execute(text(sql_query), params).mappings().all()
            
            for movie in genre_movies:
                recommendations.append({
                    "id": movie["movieId"],
                    "title": movie["title"],
                    "poster": get_poster_or_dummy(movie.get("posterUrl"), movie["title"]),
                    "releaseYear": movie["releaseYear"],
                    "country": movie["country"],
                    "score": 0.9,  # High score for preference-based
                    "rank": len(recommendations) + 1,
                    "avgRating": round(float(movie["avgRating"]), 2) if movie["avgRating"] else 0.0,
                    "ratingCount": movie["ratingCount"],
                    "genres": movie["genres"] or "",
                    "source": "preference_genre"
                })
        
        # Lưu recommendations vào database
        if recommendations:
            # Xóa recommendations cũ
            conn.execute(text("""
                DELETE FROM cine.ColdStartRecommendations WHERE userId = :user_id
            """), {"user_id": user_id})
            
            # Lấy max recId để tạo recId mới
            max_rec_id_result = conn.execute(text("""
                SELECT ISNULL(MAX(recId), 0) FROM cine.ColdStartRecommendations
            """)).scalar()
            rec_id = max_rec_id_result + 1 if max_rec_id_result else 1
            
            # Lưu recommendations mới
            for rec in recommendations:
                conn.execute(text("""
                    INSERT INTO cine.ColdStartRecommendations 
                    (recId, userId, movieId, score, rank, source, generatedAt, expiresAt, reason)
                    VALUES (:rec_id, :user_id, :movie_id, :score, :rank, :source, GETUTCDATE(), DATEADD(day, 7, GETUTCDATE()), :reason)
                """), {
                    "rec_id": rec_id,
                    "user_id": user_id,
                    "movie_id": rec["id"],
                    "score": rec["score"],
                    "rank": rec["rank"],
                    "source": rec["source"],
                    "reason": f"Recommendation based on your genre preferences"
                })
                rec_id += 1
            
            print(f"Generated {len(recommendations)} preference-based recommendations for user {user_id}")
        
        return recommendations
        
    except Exception as e:
        print(f"Error generating preference-based recommendations: {e}")
        return []

@main_bp.route("/api/user_interaction_status")
@login_required
def get_user_interaction_status():
    """API endpoint để kiểm tra trạng thái tương tác của user"""
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"success": False, "message": "Chưa đăng nhập"})
        
        with current_app.db_engine.connect() as conn:
            # Đếm các loại tương tác
            rating_count = conn.execute(text("""
                SELECT COUNT(*) FROM cine.Rating WHERE userId = :user_id
            """), {"user_id": user_id}).scalar()
            
            view_count = conn.execute(text("""
                SELECT COUNT(*) FROM cine.ViewHistory WHERE userId = :user_id
            """), {"user_id": user_id}).scalar()
            
            favorite_count = conn.execute(text("""
                SELECT COUNT(*) FROM cine.Favorite WHERE userId = :user_id
            """), {"user_id": user_id}).scalar()
            
            watchlist_count = conn.execute(text("""
                SELECT COUNT(*) FROM cine.Watchlist WHERE userId = :user_id
            """), {"user_id": user_id}).scalar()
            
            total_interactions = rating_count + view_count + favorite_count + watchlist_count
            
            # Kiểm tra xem có cold start recommendations không
            cold_start_count = conn.execute(text("""
                SELECT COUNT(*) FROM cine.ColdStartRecommendations 
                WHERE userId = :user_id AND expiresAt > GETUTCDATE()
            """), {"user_id": user_id}).scalar()
            
            # Kiểm tra xem có personal recommendations không
            personal_count = conn.execute(text("""
                SELECT COUNT(*) FROM cine.PersonalRecommendation 
                WHERE userId = :user_id AND expiresAt > GETUTCDATE()
            """), {"user_id": user_id}).scalar()
            
            return jsonify({
                "success": True,
                "interactions": {
                    "ratings": rating_count,
                    "views": view_count,
                    "favorites": favorite_count,
                    "watchlist": watchlist_count,
                    "total": total_interactions
                },
                "recommendations": {
                    "cold_start": cold_start_count,
                    "personal": personal_count
                },
                "needs_cold_start": total_interactions < 5,
                "can_use_collaborative": total_interactions >= 5
            })
            
    except Exception as e:
        print(f"Error getting user interaction status: {e}")
        return jsonify({
            "success": False,
            "message": f"Có lỗi xảy ra: {str(e)}"
        })

@main_bp.route("/api/retrain_cf_model", methods=["POST", "GET"])
@admin_required
@login_required
def retrain_cf_model():
    """Retrain Collaborative Filtering model - requires admin authentication"""
    return _retrain_cf_model_internal()

@main_bp.route("/api/retrain_cf_model_internal", methods=["POST"])
def retrain_cf_model_internal():
    """Internal endpoint for background worker - no auth required but checks secret"""
    try:
        # Kiểm tra secret key từ request để tránh unauthorized access
        secret = request.headers.get('X-Internal-Secret') or request.json.get('secret') if request.is_json else None
        expected_secret = os.environ.get('INTERNAL_RETRAIN_SECRET', 'internal-retrain-secret-key-change-in-production')
        
        if secret != expected_secret:
            current_app.logger.warning("Unauthorized retrain attempt - invalid secret")
            return jsonify({
                "success": False,
                "message": "Unauthorized"
            }), 401
        
        return _retrain_cf_model_internal()
    except Exception as e:
        current_app.logger.error(f"Error in retrain_cf_model_internal: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Internal error: {str(e)}"
        }), 500

def _retrain_cf_model_internal():
    """Retrain Collaborative Filtering model"""
    try:
        import subprocess
        import os
        import sys
        
        # Chạy script retrain model
        script_path = os.path.join(os.path.dirname(__file__), '..', 'model_collaborative', 'train_model.py')
        script_path = os.path.abspath(script_path)  # Convert to absolute path
        
        # Debug: Log đường dẫn và kiểm tra file tồn tại
        current_app.logger.info(f"Script path: {script_path}")
        current_app.logger.info(f"Script exists: {os.path.exists(script_path)}")
        
        if not os.path.exists(script_path):
            return jsonify({
                "success": False, 
                "message": f"Script không tồn tại: {script_path}"
            })
        
        # Use current Python executable for reliability
        python_exec = sys.executable or 'python'
        current_app.logger.info(f"Using Python: {python_exec}")
        
        # Set working directory to project root để import config đúng
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(script_path)))
        current_app.logger.info(f"Project root: {project_root}")
        current_app.logger.info(f"Working directory will be: {project_root}")
        
        # Chạy với timeout để tránh treo
        current_app.logger.info(f"Starting retrain process with timeout 300 seconds...")
        result = subprocess.run(
            [python_exec, script_path], 
            capture_output=True, 
            text=True,
            encoding='utf-8',
            errors='replace',  # Replace encoding errors instead of failing
            cwd=project_root,  # Set working directory to project root
            timeout=300  # 5 phút timeout
        )
        
        current_app.logger.info(f"Retrain process completed. Return code: {result.returncode}")
        if result.stdout:
            current_app.logger.info(f"Stdout (last 500 chars): {result.stdout[-500:]}")
        if result.stderr:
            current_app.logger.warning(f"Stderr (last 500 chars): {result.stderr[-500:]}")
        
        if result.returncode == 0:
            # Reload model sau khi retrain
            try:
                global enhanced_cf_recommender
                if enhanced_cf_recommender:
                    current_app.logger.info("Reloading CF model...")
                    enhanced_cf_recommender.reload_model()
                    current_app.logger.info("CF model reloaded successfully")
                else:
                    # Nếu chưa có, khởi tạo lại
                    current_app.logger.info("Initializing recommenders...")
                    from app.routes import init_recommenders
                    init_recommenders()
                    current_app.logger.info("Recommenders initialized")
            except Exception as reload_error:
                current_app.logger.error(f"Error reloading model: {reload_error}", exc_info=True)
                # Vẫn return success vì model đã được train, chỉ là reload failed
                return jsonify({
                    "success": True,
                    "message": "Model CF đã được retrain thành công, nhưng reload model thất bại. Vui lòng restart server.",
                    "output": result.stdout,
                    "warning": f"Reload error: {str(reload_error)}"
                })
            
            return jsonify({
                "success": True,
                "message": "Model CF đã được retrain thành công",
                "output": result.stdout[-1000:] if result.stdout else ""  # Chỉ trả về 1000 ký tự cuối
            })
        else:
            error_msg = result.stderr if result.stderr else "Unknown error"
            current_app.logger.error(f"Retrain failed with code {result.returncode}: {error_msg}")
            return jsonify({
                "success": False,
                "message": f"Lỗi khi retrain model (code: {result.returncode})",
                "output": result.stdout[-1000:] if result.stdout else "",
                "error": error_msg[-1000:] if error_msg else ""
            })
            
    except subprocess.TimeoutExpired:
        return jsonify({
            "success": False, 
            "message": "Retrain timeout (quá 5 phút)"
        })
    except Exception as e:
        current_app.logger.error(f"Retrain error: {str(e)}")
        return jsonify({
            "success": False, 
            "message": f"Lỗi hệ thống: {str(e)}"
        })

@main_bp.route("/api/create_sample_recommendations")
@login_required
def create_sample_recommendations():
    """Tạo recommendations mẫu cho user hiện tại"""
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"success": False, "message": "Chưa đăng nhập"})
        
        # Lấy 12 phim ngẫu nhiên (read-only)
        with current_app.db_engine.connect() as conn:
            movies = conn.execute(text("""
                SELECT TOP 12 
                    m.movieId, m.title, m.posterUrl, m.releaseYear, m.country,
                    AVG(CAST(r.value AS FLOAT)) AS avgRating,
                    COUNT(r.movieId) AS ratingCount
                FROM cine.Movie m
                LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                GROUP BY m.movieId, m.title, m.posterUrl, m.releaseYear, m.country
                ORDER BY NEWID()
            """)).mappings().all()
            
        # Lưu recommendations mẫu (write operations - cần transaction)
        # Sử dụng begin() để tự động quản lý transaction
        with current_app.db_engine.begin() as conn:
            # Xóa recommendations cũ
            conn.execute(text("""
                DELETE FROM cine.PersonalRecommendation WHERE userId = :user_id
            """), {"user_id": user_id})
            
            # Tạo recommendations mẫu
            # Tính recId bắt đầu từ MAX + 1
            max_rec_id = conn.execute(text("""
                SELECT ISNULL(MAX(recId), 0) FROM cine.PersonalRecommendation
            """)).scalar() or 0
            
            for rank, movie in enumerate(movies, 1):
                score = round(0.5 + (rank * 0.1), 2)  # Score từ 0.6 đến 1.7
                rec_id = max_rec_id + rank
                
                conn.execute(text("""
                    INSERT INTO cine.PersonalRecommendation 
                    (recId, userId, movieId, score, rank, algo, generatedAt, expiresAt)
                    VALUES (:rec_id, :user_id, :movie_id, :score, :rank, 'sample', GETUTCDATE(), DATEADD(day, 7, GETUTCDATE()))
                """), {
                    "rec_id": rec_id,
                    "user_id": user_id,
                    "movie_id": movie["movieId"],
                    "score": score,
                    "rank": rank
                })
            # begin() tự động commit khi exit context thành công
            
            return jsonify({
                "success": True,
                "message": f"Đã tạo {len(movies)} recommendations mẫu",
                "recommendations": [
                    {
                        "movieId": movie["movieId"],
                        "title": movie["title"],
                        "score": round(0.5 + (i * 0.1), 2),
                        "rank": i + 1
                    }
                    for i, movie in enumerate(movies)
                ]
            })
            
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


