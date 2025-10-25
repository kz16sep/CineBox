from flask import Blueprint, render_template, request, redirect, url_for, current_app, session, jsonify, flash
from sqlalchemy import text
from functools import wraps
import sys
import os
import time
import uuid
import re
from werkzeug.utils import secure_filename
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from content_based_recommender import ContentBasedRecommender
from collaborative_recommender import CollaborativeRecommender
from enhanced_cf_recommender import EnhancedCFRecommender
# Global recommender instances
content_recommender = None
collaborative_recommender = None
enhanced_cf_recommender = None

# --- CF retrain dirty-flag helpers ---
def set_cf_dirty(db_engine=None):
    try:
        if db_engine is None:
            db_engine = current_app.db_engine
        with db_engine.connect() as conn:
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
            conn.commit()
    except Exception as e:
        print(f"Error setting cf_dirty: {e}")


def fetch_rating_stats_for_movies(movie_ids, db_engine=None):
    """Fetch avgRating and ratingCount for a list of movie IDs uniformly.

    Returns a dict: movieId -> {"avgRating": float, "ratingCount": int}
    """
    if not movie_ids:
        return {}
    if db_engine is None:
        db_engine = current_app.db_engine
    try:
        params = {f"id{i}": int(mid) for i, mid in enumerate(movie_ids)}
        placeholders = ",".join([f":id{i}" for i in range(len(movie_ids))])
        with db_engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT m.movieId,
                       AVG(CAST(r.value AS FLOAT)) AS avgRating,
                       COUNT(r.movieId) AS ratingCount
                FROM cine.Movie m
                LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                WHERE m.movieId IN ({placeholders})
                GROUP BY m.movieId
            """), params).mappings().all()
        return {
            row["movieId"]: {
                "avgRating": round(float(row["avgRating"] or 0), 2),
                "ratingCount": int(row["ratingCount"] or 0),
            }
            for row in rows
        }
    except Exception as e:
        print(f"Error fetching rating stats: {e}")
        return {}

def calculate_user_based_score(user_id, movie_id, db_engine=None):
    """Tính điểm gợi ý dựa trên tất cả tương tác của user với phim"""
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
            
            user_rating = interaction_result[0] or 0
            is_favorite = interaction_result[1]
            is_watchlist = interaction_result[2]
            has_viewed = interaction_result[3]
            has_commented = interaction_result[4]
            total_ratings = interaction_result[5] or 0
            avg_rating = interaction_result[6] or 0
            
            # Tính điểm dựa trên tất cả các yếu tố
            score = 0.0
            
            # 1. Rating của user (trọng số cao nhất)
            if user_rating > 0:
                score += (user_rating / 5.0) * 0.4  # 0-0.4 điểm
            
            # 2. Favorite (trọng số cao)
            if is_favorite:
                score += 0.3  # +0.3 điểm
            
            # 3. Watchlist (trọng số cao)
            if is_watchlist:
                score += 0.25  # +0.25 điểm
            
            # 4. View History (trọng số trung bình)
            if has_viewed:
                score += 0.2  # +0.2 điểm
            
            # 5. Comments (trọng số trung bình)
            if has_commented:
                score += 0.15  # +0.15 điểm
            
            # 6. Rating trung bình của phim (trọng số thấp)
            if avg_rating > 0:
                score += (avg_rating / 5.0) * 0.1  # 0-0.1 điểm
            
            # 7. Độ phổ biến (số lượng rating)
            if total_ratings > 0:
                popularity_bonus = min(total_ratings / 100.0, 0.1)  # Tối đa 0.1 điểm
                score += popularity_bonus
            
            # Đảm bảo điểm trong khoảng 0-2.0
            score = max(0.0, min(score, 2.0))
            
            return round(score, 3)
            
    except Exception as e:
        print(f"Error calculating user based score: {e}")
        return 0.5  # Fallback score

def create_rating_based_recommendations(user_id, movies, db_engine=None):
    """Tạo recommendations dựa trên rating thực tế của user"""
    try:
        recommendations = []
        for movie in movies:
            score = calculate_user_based_score(user_id, movie["id"], db_engine)
            
            # Lấy thêm thông tin về view, watchlist, favorites, comments
            try:
                with db_engine.connect() as conn:
                    # Lấy thông tin về view history, watchlist, favorites, comments
                    stats_result = conn.execute(text("""
                        SELECT 
                            COUNT(DISTINCT vh.userId) as viewHistoryCount,
                            COUNT(DISTINCT w.userId) as watchlistCount,
                            COUNT(DISTINCT f.userId) as favoriteCount,
                            COUNT(DISTINCT c.userId) as commentCount
                        FROM cine.Movie m
                        LEFT JOIN cine.ViewHistory vh ON m.movieId = vh.movieId
                        LEFT JOIN cine.Watchlist w ON m.movieId = w.movieId
                        LEFT JOIN cine.Favorite f ON m.movieId = f.movieId
                        LEFT JOIN cine.Comment c ON m.movieId = c.movieId
                        WHERE m.movieId = :movie_id
                        GROUP BY m.movieId
                    """), {"movie_id": movie["id"]}).fetchone()
                    
                    if stats_result:
                        viewHistoryCount = stats_result[0] or 0
                        watchlistCount = stats_result[1] or 0
                        favoriteCount = stats_result[2] or 0
                        commentCount = stats_result[3] or 0
                    else:
                        viewHistoryCount = watchlistCount = favoriteCount = commentCount = 0
                        
            except Exception as e:
                print(f"Error getting stats for movie {movie['id']}: {e}")
                viewHistoryCount = watchlistCount = favoriteCount = commentCount = 0
            
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
        
        # Sắp xếp theo điểm giảm dần, sau đó theo rating giảm dần
        recommendations.sort(key=lambda x: (x["score"], x["avgRating"], x["ratingCount"]), reverse=True)
        return recommendations
        
    except Exception as e:
        print(f"Error creating rating based recommendations: {e}")
        return []

def init_recommenders():
    """Initialize recommender instances"""
    global content_recommender, collaborative_recommender, enhanced_cf_recommender
    try:
        content_recommender = ContentBasedRecommender(current_app.db_engine)
        collaborative_recommender = CollaborativeRecommender(current_app.db_engine)
        enhanced_cf_recommender = EnhancedCFRecommender(current_app.db_engine)
        
        # Load CF models
        print("Loading Collaborative Filtering models...")
        if collaborative_recommender.load_model():
            print("Collaborative CF model loaded successfully")
        else:
            print("Collaborative CF model not found or failed to load")
            
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
        
        # 1. Phim trending (được xem nhiều nhất)
        trending_movies = conn.execute(text("""
            SELECT TOP 4
                m.movieId, m.title, m.posterUrl, m.releaseYear, m.country,
                m.viewCount, AVG(CAST(r.value AS FLOAT)) AS avgRating,
                COUNT(r.movieId) AS ratingCount,
                STRING_AGG(g.name, ', ') WITHIN GROUP (ORDER BY g.name) as genres
            FROM cine.Movie m
            LEFT JOIN cine.Rating r ON m.movieId = r.movieId
            LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
            LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
            WHERE m.viewCount > 0
            GROUP BY m.movieId, m.title, m.posterUrl, m.releaseYear, m.country, m.viewCount
            ORDER BY m.viewCount DESC, avgRating DESC
        """)).mappings().all()
        
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
        
        # 2. Phim mới nhất
        latest_movies = conn.execute(text("""
            SELECT TOP 4
                m.movieId, m.title, m.posterUrl, m.releaseYear, m.country,
                AVG(CAST(r.value AS FLOAT)) AS avgRating,
                COUNT(r.movieId) AS ratingCount,
                STRING_AGG(g.name, ', ') WITHIN GROUP (ORDER BY g.name) as genres
            FROM cine.Movie m
            LEFT JOIN cine.Rating r ON m.movieId = r.movieId
            LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
            LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
            WHERE m.releaseYear >= YEAR(GETDATE()) - 2
            GROUP BY m.movieId, m.title, m.posterUrl, m.releaseYear, m.country
            ORDER BY m.releaseYear DESC, avgRating DESC
        """)).mappings().all()
        
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
        
        # 3. Phim có rating cao nhất
        high_rated_movies = conn.execute(text("""
            SELECT TOP 4
                m.movieId, m.title, m.posterUrl, m.releaseYear, m.country,
                AVG(CAST(r.value AS FLOAT)) AS avgRating,
                COUNT(r.movieId) AS ratingCount,
                STRING_AGG(g.name, ', ') WITHIN GROUP (ORDER BY g.name) as genres
            FROM cine.Movie m
            INNER JOIN cine.Rating r ON m.movieId = r.movieId
            LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
            LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
            GROUP BY m.movieId, m.title, m.posterUrl, m.releaseYear, m.country
            HAVING COUNT(r.movieId) >= 10  -- Ít nhất 10 ratings
            ORDER BY avgRating DESC, ratingCount DESC
        """)).mappings().all()
        
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
            # Xóa recommendations cũ
            conn.execute(text("""
                DELETE FROM cine.ColdStartRecommendations WHERE userId = :user_id
            """), {"user_id": user_id})
            
            # Lưu recommendations mới
            for rec in recommendations:
                conn.execute(text("""
                    INSERT INTO cine.ColdStartRecommendations 
                    (userId, movieId, score, rank, source, generatedAt, expiresAt, reason)
                    VALUES (:user_id, :movie_id, :score, :rank, :source, GETUTCDATE(), DATEADD(day, 7, GETUTCDATE()), :reason)
                """), {
                    "user_id": user_id,
                    "movie_id": rec["id"],
                    "score": rec["score"],
                    "rank": rec["rank"],
                    "source": rec["source"],
                    "reason": f"Cold start recommendation based on {rec['source']}"
                })
            
            conn.commit()
            print(f"Generated {len(recommendations)} cold start recommendations for user {user_id}")
        
        return recommendations[:12]  # Trả về tối đa 12 recommendations
        
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
                    STRING_AGG(g.name, ', ') WITHIN GROUP (ORDER BY g.name) as genres
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
                    STRING_AGG(g.name, ', ') WITHIN GROUP (ORDER BY g.name) as genres
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
        data = request.get_json()
        
        movie_id = data.get('movie_id')
        progress_sec = data.get('progress_sec', 0)
        is_finished = data.get('is_finished', False)
        
        
        if not movie_id:
            return jsonify({"success": False, "message": "Thiếu thông tin movie_id"})
        
        with current_app.db_engine.begin() as conn:
            # Kiểm tra xem có history record nào cho movie này không
            history_record = conn.execute(text("""
                SELECT TOP 1 historyId FROM cine.ViewHistory 
                WHERE userId = :user_id AND movieId = :movie_id
                ORDER BY startedAt DESC
            """), {"user_id": user_id, "movie_id": movie_id}).scalar()
            
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
                # Tạo historyId tự động bằng cách lấy max + 1
                max_id = conn.execute(text("""
                    SELECT ISNULL(MAX(historyId), 0) + 1 FROM cine.ViewHistory
                """)).scalar()
                
                if is_finished:
                    # Nếu hoàn thành, set cả startedAt và finishedAt
                    conn.execute(text("""
                        INSERT INTO cine.ViewHistory (historyId, userId, movieId, startedAt, progressSec, finishedAt, deviceType, ipAddress, userAgent)
                        VALUES (:history_id, :user_id, :movie_id, GETDATE(), :progress_sec, GETDATE(), :device_type, :ip_address, :user_agent)
                    """), {
                        "history_id": max_id,
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
                        "history_id": max_id,
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
        print(f"Error updating watch progress: {e}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra khi cập nhật tiến độ xem"})


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
                
                if not has_completed:
                    return redirect(url_for('main.onboarding'))
        except Exception as e:
            print(f"Error checking onboarding status: {e}")
            # Nếu có lỗi, giả sử user chưa hoàn thành onboarding
            return redirect(url_for('main.onboarding'))
    
    # Lấy danh sách phim từ DB bằng engine (odbc_connect); nếu chưa đăng nhập, chuyển tới form đăng nhập
    if not session.get("user_id"):
        return redirect(url_for("main.login"))
    
    # Lấy page parameter cho tất cả phim và genre filter
    page = request.args.get('page', 1, type=int)
    per_page = 12  # Số phim mỗi trang
    genre_filter = request.args.get('genre', '', type=str)  # Lọc theo thể loại
    search_query = request.args.get('q', '', type=str)  # Tìm kiếm
    
    # 1. Phim mới nhất (12 phim, không phân trang) - thay thế trending
    try:
        with current_app.db_engine.connect() as conn:
            if genre_filter:
                # Lấy phim mới nhất theo thể loại
                rows = conn.execute(text("""
                    SELECT TOP 12 m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.createdAt
                    SELECT TOP 12 
                        m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.createdAt,
                        AVG(CAST(r.value AS FLOAT)) AS avgRating,
                        COUNT(r.movieId) AS ratingCount,
                        STUFF((
                            SELECT ', ' + g2.name
                            FROM cine.MovieGenre mg2
                            JOIN cine.Genre g2 ON mg2.genreId = g2.genreId
                            WHERE mg2.movieId = m.movieId
                            GROUP BY g2.name
                            ORDER BY g2.name
                            FOR XML PATH('')
                        ),1,2,'') AS genres
                    FROM cine.Movie m
                    JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                    JOIN cine.Genre g ON mg.genreId = g.genreId
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    WHERE g.name = :genre
                    GROUP BY m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.createdAt
                    ORDER BY m.createdAt DESC, m.movieId DESC
                """), {"genre": genre_filter}).mappings().all()
            else:
                # Lấy phim mới nhất tất cả thể loại
                rows = conn.execute(text(
                    "SELECT TOP 12 movieId, title, posterUrl, backdropUrl, overview, createdAt FROM cine.Movie ORDER BY createdAt DESC, movieId DESC"
                    """
                    SELECT TOP 12 
                        m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.createdAt,
                        AVG(CAST(r.value AS FLOAT)) AS avgRating,
                        COUNT(r.movieId) AS ratingCount,
                        STUFF((
                            SELECT ', ' + g.name
                            FROM cine.MovieGenre mg
                            JOIN cine.Genre g ON mg.genreId = g.genreId
                            WHERE mg.movieId = m.movieId
                            GROUP BY g.name
                            ORDER BY g.name
                            FOR XML PATH('')
                        ),1,2,'') AS genres
                    FROM cine.Movie m
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    GROUP BY m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.createdAt
                    ORDER BY m.createdAt DESC, m.movieId DESC
                    """
                )).mappings().all()
            
            latest_movies = [
                {
                    "id": r["movieId"],
                    "title": r["title"],
                    "poster": r.get("posterUrl") if r.get("posterUrl") and r.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={r['title'][:20].replace(' ', '+')}",
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                    "createdAt": r.get("createdAt"),
                    "avgRating": 0,
                    "ratingCount": 0,
                    "genres": r.get("genres") or "",
                }
                for r in rows
            ]
            # Overwrite ratings using a single uniform aggregation
            try:
                stats = fetch_rating_stats_for_movies([m["id"] for m in latest_movies])
                for m in latest_movies:
                    s = stats.get(m["id"]) or {}
                    m["avgRating"] = s.get("avgRating", 0)
                    m["ratingCount"] = s.get("ratingCount", 0)
            except Exception:
                pass
            
            # Tạo carousel_movies từ 6 phim mới nhất (theo thể loại nếu có)
            if genre_filter:
                carousel_rows = conn.execute(text("""
                    SELECT TOP 6 m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.createdAt
                    FROM cine.Movie m
                    JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                    JOIN cine.Genre g ON mg.genreId = g.genreId
                    WHERE g.name = :genre
                    ORDER BY m.createdAt DESC, m.movieId DESC
                """), {"genre": genre_filter}).mappings().all()
            else:
                carousel_rows = conn.execute(text(
                    "SELECT TOP 6 movieId, title, posterUrl, backdropUrl, overview, createdAt FROM cine.Movie ORDER BY createdAt DESC, movieId DESC"
                )).mappings().all()
            # Tạo carousel_movies từ 6 phim mới nhất (luôn lấy từ tất cả phim, không phụ thuộc genre)
            carousel_rows = conn.execute(text(
                """
                SELECT TOP 6 
                    m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.createdAt,
                    AVG(CAST(r.value AS FLOAT)) AS avgRating,
                    COUNT(r.movieId) AS ratingCount
                FROM cine.Movie m
                LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                GROUP BY m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.createdAt
                ORDER BY m.createdAt DESC, m.movieId DESC
                """
            )).mappings().all()
            
            carousel_movies = [
                {
                    "id": r["movieId"],
                    "title": r["title"],
                    "poster": r.get("posterUrl") if r.get("posterUrl") and r.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={r['title'][:20].replace(' ', '+')}",
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                    "createdAt": r.get("createdAt"),
                    "avgRating": 0,
                    "ratingCount": 0,
                    "genres": r.get("genres") or "",
                }
                for r in carousel_rows
            ]
            try:
                stats = fetch_rating_stats_for_movies([m["id"] for m in carousel_movies])
                for m in carousel_movies:
                    s = stats.get(m["id"]) or {}
                    m["avgRating"] = s.get("avgRating", 0)
                    m["ratingCount"] = s.get("ratingCount", 0)
            except Exception:
                pass
    except Exception:
        latest_movies = []
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
                # Đếm số lượng ratings và view history của user
                rating_count = conn.execute(text("""
                    SELECT COUNT(*) FROM cine.Rating WHERE userId = :user_id
                """), {"user_id": user_id}).scalar()
                
                view_count = conn.execute(text("""
                    SELECT COUNT(*) FROM cine.ViewHistory WHERE userId = :user_id
                """), {"user_id": user_id}).scalar()
                
                total_interactions = rating_count + view_count
                
                # Nếu user có ít hơn 5 interactions, sử dụng cold start
                if total_interactions < 5:
                    print(f"User {user_id} has only {total_interactions} interactions, using cold start")
                    personal_recommendations = get_cold_start_recommendations(user_id, conn)
                else:
                    # Lấy gợi ý cá nhân từ PersonalRecommendation
                    personal_rows = conn.execute(text("""
                        SELECT TOP 12 m.movieId, m.title, m.posterUrl, pr.score
                        FROM cine.PersonalRecommendation pr
                        JOIN cine.Movie m ON m.movieId = pr.movieId
                        WHERE pr.userId = :user_id AND pr.expiresAt > GETDATE()
                        ORDER BY pr.rank
                    """), {"user_id": user_id}).mappings().all()
            
            # Initialize recommenders if not already done
            global content_recommender, collaborative_recommender
            if collaborative_recommender is None:
                init_recommenders()
            
            # Sử dụng Enhanced CF Recommender trước, fallback về Collaborative CF
            
            # Thử Enhanced CF model trước
            if enhanced_cf_recommender and enhanced_cf_recommender.is_model_loaded():
                personal_recommendations_raw = enhanced_cf_recommender.get_user_recommendations(user_id, limit=12)
            elif collaborative_recommender and collaborative_recommender.is_model_loaded():
                personal_recommendations_raw = collaborative_recommender.get_user_recommendations(user_id, limit=12)
            else:
                personal_recommendations_raw = []
            
            if personal_recommendations_raw:
                
                # Sắp xếp recommendations theo score, sau đó theo avgRating
                personal_recommendations_raw.sort(key=lambda x: (x.get("recommendation_score", 0), x.get("avgRating", 0), x.get("ratingCount", 0)), reverse=True)
                
                # Lưu recommendations vào bảng PersonalRecommendation
                with current_app.db_engine.connect() as conn:
                    # Xóa recommendations cũ của user này
                    conn.execute(text("""
                        DELETE FROM cine.PersonalRecommendation 
                        WHERE userId = :user_id
                    """), {"user_id": user_id})
                    
                    # Lưu recommendations mới từ CF model
                    for rank, rec in enumerate(personal_recommendations_raw, 1):
                        # Generate recId
                        rec_id_result = conn.execute(text("""
                            SELECT ISNULL(MAX(recId), 0) + 1 FROM cine.PersonalRecommendation
                        """)).fetchone()
                        rec_id = rec_id_result[0] if rec_id_result else 1
                        
                        conn.execute(text("""
                            INSERT INTO cine.PersonalRecommendation 
                            (recId, userId, movieId, score, rank, algo, generatedAt, expiresAt)
                            VALUES (:rec_id, :user_id, :movie_id, :score, :rank, 'collaborative', GETUTCDATE(), DATEADD(day, 7, GETUTCDATE()))
                        """), {
                            "rec_id": rec_id,
                            "user_id": user_id,
                            "movie_id": rec["movieId"],
                            "score": rec.get("recommendation_score", 0),
                            "rank": rank
                        })
                    
                    conn.commit()
                
                personal_recommendations = []
                for rec in personal_recommendations_raw:
                    rec_dict = {
                        "id": rec["movieId"],
                        "title": rec["title"],
                        "poster": rec.get("posterUrl") if rec.get("posterUrl") and rec.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={rec['title'][:20].replace(' ', '+')}",
                        "year": rec.get("releaseYear"),
                        "country": rec.get("country"),
                        "score": rec.get("recommendation_score", 0),
                        "genres": rec.get("genres", ""),
                        "avgRating": rec.get("avgRating", 0),
                        "ratingCount": rec.get("ratingCount", 0),
                        "watchlistCount": rec.get("watchlistCount", 0),
                        "viewHistoryCount": rec.get("viewHistoryCount", 0),
                        "favoriteCount": rec.get("favoriteCount", 0),
                        "commentCount": rec.get("commentCount", 0),
                        "algo": "enhanced_cf" if enhanced_cf_recommender and enhanced_cf_recommender.is_model_loaded() else "collaborative_cf",
                        "reason": rec.get("reason", "Model prediction")
                    }
                    
                    personal_recommendations.append(rec_dict)
            else:
                # Tạo recommendations dựa trên rating thực tế của user
                personal_recommendations = create_rating_based_recommendations(user_id, latest_movies[:12], current_app.db_engine)
                
        except Exception as e:
            # Fallback: Lấy gợi ý từ database nếu model chưa load
            print(f"Error getting personal recommendations: {e}")
            with current_app.db_engine.connect() as conn:
                personal_rows = conn.execute(text("""
                    SELECT TOP 12 
                        m.movieId, m.title, m.posterUrl, m.releaseYear, m.country,
                        pr.score, pr.rank, pr.generatedAt, pr.algo,
                        AVG(CAST(r.value AS FLOAT)) as avgRating,
                        COUNT(r.movieId) as ratingCount,
                        COUNT(DISTINCT w.userId) as watchlistCount,
                        COUNT(DISTINCT vh.userId) as viewHistoryCount,
                        COUNT(DISTINCT f.userId) as favoriteCount,
                        COUNT(DISTINCT c.userId) as commentCount,
                        STRING_AGG(TOP 5 g.name, ', ') as genres
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
                
                
                # Lấy trending movies sử dụng collaborative recommender
                if collaborative_recommender and collaborative_recommender.is_model_loaded():
                    trending_recommendations_raw = collaborative_recommender.get_trending_movies(limit=12)
                    
                    trending_movies = [
                        {
                            "id": rec["movieId"],
                            "title": rec["title"],
                            "poster": rec.get("posterUrl") if rec.get("posterUrl") and rec.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={rec['title'][:20].replace(' ', '+')}",
                            "releaseYear": rec.get("releaseYear"),
                            "country": rec.get("country"),
                            "ratingCount": rec.get("ratingCount", 0),
                            "avgRating": rec.get("avgRating", 0),
                            "genres": rec.get("genres", ""),
                            "viewCount": rec.get("viewCount", 0)
                        }
                        for rec in trending_recommendations_raw
                    ]
                else:
                    # Fallback: Lấy trending movies từ database
                    trending_rows = conn.execute(text("""
                        SELECT TOP 12
                            m.movieId, m.title, m.posterUrl, m.releaseYear, m.country,
                            COUNT(r.movieId) as ratingCount,
                            AVG(CAST(r.value AS FLOAT)) as avgRating,
                            STRING_AGG(TOP 5 g.name, ', ') as genres
                        FROM cine.Movie m
                        LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                        LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                        LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                        WHERE m.movieId IN (SELECT DISTINCT movieId FROM cine.Rating)
                        GROUP BY m.movieId, m.title, m.posterUrl, m.releaseYear, m.country
                        ORDER BY ratingCount DESC, avgRating DESC
                    """)).mappings().all()
                    
                    trending_movies = [
                        {
                            "id": row["movieId"],
                            "title": row["title"],
                            "poster": row.get("posterUrl") if row.get("posterUrl") and row.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={row['title'][:20].replace(' ', '+')}",
                            "releaseYear": row["releaseYear"],
                            "country": row["country"],
                            "ratingCount": row["ratingCount"],
                            "avgRating": round(float(row["avgRating"]), 2) if row["avgRating"] else 0.0,
                            "genres": row["genres"] or ""
                        }
                        for row in trending_rows
                    ]
                
        except Exception as e:
            print(f"Error getting personal recommendations: {e}")
            personal_recommendations = []
            trending_movies = []
    
    # Fallback nếu không có gợi ý cá nhân - sử dụng CF model
    if not personal_recommendations:
        
        # Thử enhanced CF model trước
        if enhanced_cf_recommender and enhanced_cf_recommender.is_model_loaded():
            personal_recommendations_raw = enhanced_cf_recommender.get_user_recommendations(user_id, limit=50)
        elif collaborative_recommender and collaborative_recommender.is_model_loaded():
            personal_recommendations_raw = collaborative_recommender.get_user_recommendations(user_id, limit=50)
        else:
            personal_recommendations_raw = []
        
        if personal_recommendations_raw:
            print(f"Debug - Got {len(personal_recommendations_raw)} CF recommendations for user {user_id}")
            
            # Debug first few recommendations
            for i, rec in enumerate(personal_recommendations_raw[:3]):
                print(f"Debug - CF Recommendation {i+1}:")
                print(f"  Title: {rec.get('title', 'N/A')}")
                print(f"  Score: {rec.get('recommendation_score', 0)}")
                print(f"  Ratings: {rec.get('ratingCount', 0)}")
                print(f"  Views: {rec.get('viewHistoryCount', 0)}")
                print(f"  Watchlist: {rec.get('watchlistCount', 0)}")
                print(f"  Favorites: {rec.get('favoriteCount', 0)}")
                print(f"  Comments: {rec.get('commentCount', 0)}")
            
            # Sắp xếp recommendations theo score, sau đó theo avgRating
            personal_recommendations_raw.sort(key=lambda x: (x.get("recommendation_score", 0), x.get("avgRating", 0), x.get("ratingCount", 0)), reverse=True)
            
            # Lưu recommendations vào bảng PersonalRecommendation
            with current_app.db_engine.connect() as conn:
                # Xóa recommendations cũ của user này
                conn.execute(text("""
                    DELETE FROM cine.PersonalRecommendation 
                    WHERE userId = :user_id
                """), {"user_id": user_id})
                
                # Lưu recommendations mới từ CF model
                for rank, rec in enumerate(personal_recommendations_raw[:12], 1):
                    # Generate recId
                    rec_id_result = conn.execute(text("""
                        SELECT ISNULL(MAX(recId), 0) + 1 FROM cine.PersonalRecommendation
                    """)).fetchone()
                    rec_id = rec_id_result[0] if rec_id_result else 1
                    
                    conn.execute(text("""
                        INSERT INTO cine.PersonalRecommendation 
                        (recId, userId, movieId, score, rank, algo, generatedAt, expiresAt)
                        VALUES (:rec_id, :user_id, :movie_id, :score, :rank, 'collaborative', GETUTCDATE(), DATEADD(day, 7, GETUTCDATE()))
                    """), {
                        "rec_id": rec_id,
                        "user_id": user_id,
                        "movie_id": rec["movieId"],
                        "score": rec.get("recommendation_score", 0),
                        "rank": rank
                    })
                    
                    conn.commit()
                    print(f"Debug - Saved {len(personal_recommendations_raw[:12])} recommendations to PersonalRecommendation table")
                
                personal_recommendations = []
                for rec in personal_recommendations_raw[:12]:
                    rec_dict = {
                        "id": rec["movieId"],
                        "title": rec["title"],
                        "poster": rec.get("posterUrl") if rec.get("posterUrl") and rec.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={rec['title'][:20].replace(' ', '+')}",
                        "year": rec.get("releaseYear"),
                        "country": rec.get("country"),
                        "score": rec.get("recommendation_score", 0),
                        "genres": rec.get("genres", ""),
                        "avgRating": rec.get("avgRating", 0),
                        "ratingCount": rec.get("ratingCount", 0),
                        "watchlistCount": rec.get("watchlistCount", 0),
                        "viewHistoryCount": rec.get("viewHistoryCount", 0),
                        "favoriteCount": rec.get("favoriteCount", 0),
                        "commentCount": rec.get("commentCount", 0),
                        "algo": "enhanced_cf" if enhanced_cf_recommender and enhanced_cf_recommender.is_model_loaded() else "collaborative_cf",
                        "reason": rec.get("reason", "Model prediction")
                    }
                    
                    # Debug logging for first few recommendations
                    if len(personal_recommendations) < 3:
                        print(f"Debug - Fallback recommendation {len(personal_recommendations)+1}:")
                        print(f"  Title: {rec_dict['title']}")
                        print(f"  Score: {rec_dict['score']}")
                        print(f"  Ratings: {rec_dict['ratingCount']}")
                        print(f"  Views: {rec_dict['viewHistoryCount']}")
                        print(f"  Watchlist: {rec_dict['watchlistCount']}")
                        print(f"  Favorites: {rec_dict['favoriteCount']}")
                        print(f"  Comments: {rec_dict['commentCount']}")
                    
                    personal_recommendations.append(rec_dict)
                print(f"Debug - Created {len(personal_recommendations)} recommendations from CF model fallback")
        else:
            print(f"Debug - No recommendations from CF model fallback, using latest movies")
            personal_recommendations = latest_movies[:12]
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
                # Lấy phim theo thể loại với phân trang kèm avgRating, ratingCount, genres
                print(f"Debug - Getting movies for genre '{genre_filter}', page {page}, offset {offset}, per_page {per_page}")
                all_rows = conn.execute(text("""
                    SELECT movieId, title, posterUrl, backdropUrl, overview, releaseYear
                    FROM (
                        SELECT DISTINCT m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear,
                               ROW_NUMBER() OVER (ORDER BY m.movieId DESC) as rn
                    WITH filtered AS (
                        SELECT DISTINCT m.movieId,
                               ROW_NUMBER() OVER (ORDER BY m.movieId DESC) AS rn
                        FROM cine.Movie m
                        JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                        JOIN cine.Genre g ON mg.genreId = g.genreId
                        WHERE g.name = :genre
                    ) t
                    WHERE rn > :offset AND rn <= :offset + :per_page
                    )
                    SELECT 
                        m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear,
                        AVG(CAST(r.value AS FLOAT)) AS avgRating,
                        COUNT(r.movieId) AS ratingCount,
                        STUFF((
                            SELECT ', ' + g2.name
                            FROM cine.MovieGenre mg2
                            JOIN cine.Genre g2 ON mg2.genreId = g2.genreId
                            WHERE mg2.movieId = m.movieId
                            GROUP BY g2.name
                            ORDER BY g2.name
                            FOR XML PATH('')
                        ),1,2,'') AS genres
                    FROM filtered f
                    JOIN cine.Movie m ON m.movieId = f.movieId
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    WHERE f.rn > :offset AND f.rn <= :offset + :per_page
                    GROUP BY m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear
                    ORDER BY m.movieId DESC
                """), {"genre": genre_filter, "offset": offset, "per_page": per_page}).mappings().all()
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
                
                # Lấy tất cả phim với phân trang
                # Lấy tất cả phim với phân trang kèm avgRating, ratingCount, genres
                all_rows = conn.execute(text("""
                    WITH paged AS (
                        SELECT m.movieId,
                               ROW_NUMBER() OVER (ORDER BY m.movieId DESC) AS rn
                        FROM cine.Movie m
                    )
                    SELECT 
                        m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear,
                        AVG(CAST(r.value AS FLOAT)) AS avgRating,
                        COUNT(r.movieId) AS ratingCount,
                        STUFF((
                            SELECT ', ' + g.name
                            FROM cine.MovieGenre mg
                            JOIN cine.Genre g ON mg.genreId = g.genreId
                            WHERE mg.movieId = m.movieId
                            GROUP BY g.name
                            ORDER BY g.name
                            FOR XML PATH('')
                        ),1,2,'') AS genres
                    FROM paged p
                    JOIN cine.Movie m ON m.movieId = p.movieId
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    WHERE p.rn > :offset AND p.rn <= :offset + :per_page
                    GROUP BY m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear
                    ORDER BY m.movieId DESC
                """), {"offset": offset, "per_page": per_page}).mappings().all()
            
            all_movies = [
                {
                    "id": r["movieId"],
                    "title": r["title"],
                    "poster": r.get("posterUrl") if r.get("posterUrl") and r.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={r['title'][:20].replace(' ', '+')}",
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                    "year": r.get("releaseYear"),
                    "avgRating": 0,
                    "ratingCount": 0,
                    "genres": r.get("genres") or ""
                }
                for r in all_rows
            ]
            try:
                stats = fetch_rating_stats_for_movies([m["id"] for m in all_movies])
                for m in all_movies:
                    s = stats.get(m["id"]) or {}
                    m["avgRating"] = s.get("avgRating", 0)
                    m["ratingCount"] = s.get("ratingCount", 0)
            except Exception:
                pass
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
        all_movies = latest_movies[:12]  # Sử dụng latest_movies làm fallback
        pagination = {
            "page": 1,
            "per_page": 12,
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
    # Lấy lịch sử xem cho trang chủ (chỉ 10 phim gần nhất)
    view_history = []
    if user_id:
        try:
            with current_app.db_engine.connect() as conn:
                history_rows = conn.execute(text("""
                    SELECT TOP 10 
                        vh.historyId, vh.movieId, vh.startedAt, vh.finishedAt, vh.progressSec,
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
                    FROM cine.ViewHistory vh
                    JOIN cine.Movie m ON vh.movieId = m.movieId
                    WHERE vh.userId = :user_id
                    ORDER BY vh.startedAt DESC
                """), {"user_id": user_id}).mappings().all()
                
                print(f"Debug - Found {len(history_rows)} history records for user {user_id}")
                for row in history_rows:
                    print(f"Debug - History item: {row['title']}, completed: {bool(row['isCompleted'])}, finishedAt: {row['finishedAt']}")
                
                view_history = []
                for row in history_rows:
                    view_history.append({
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
                
                print(f"Debug - Processed {len(view_history)} history items")
        except Exception as e:
            print(f"Error loading view history: {e}")
            view_history = []
    
    return render_template("home.html", 
                         latest_movies=latest_movies,  # Phim mới nhất (12 phim, không phân trang)
                         carousel_movies=carousel_movies,  # Carousel phim mới nhất (6 phim)
                         recommended=personal_recommendations,  # Phim đề xuất cá nhân (Collaborative Filtering)
                         trending_movies=trending_movies,  # Phim trending (được đánh giá nhiều nhất)
                         all_movies=all_movies,  # Tất cả phim (có phân trang)
                         view_history=view_history,  # Lịch sử xem (10 phim gần nhất)
                         pagination=pagination,
                         genre_filter=genre_filter,
                         search_query=search_query,
                         all_genres=all_genres)  # Tất cả thể loại


@main_bp.route("/login", methods=["GET", "POST"])
def login():
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
    return render_template("login.html", error=error)


@main_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        print(f"Debug: Form data - name: '{name}', email: '{email}', password: '{password[:3]}...'")
        if not name or not email or not password:
            print("Debug: Missing required fields")
            return render_template("register.html", error="Vui lòng nhập đầy đủ thông tin.")
        try:
            print(f"Debug: Starting registration for email: {email}")
            with current_app.db_engine.begin() as conn:
                # create user with User role
                role_id = conn.execute(text("SELECT roleId FROM cine.Role WHERE roleName=N'User'")).scalar()
                print(f"Debug: Found role_id: {role_id}")
                if role_id is None:
                    # Get next available roleId
                    max_role_id = conn.execute(text("SELECT ISNULL(MAX(roleId), 0) FROM cine.Role")).scalar()
                    role_id = max_role_id + 1
                    conn.execute(text("INSERT INTO cine.Role(roleId, roleName, description) VALUES (:roleId, N'User', N'Người dùng')"), {"roleId": role_id})
                    print(f"Debug: Created new role with id: {role_id}")
                # Insert user with manual ID
                print(f"Debug: Inserting user with email: {email}, roleId: {role_id}")
                max_user_id = conn.execute(text("SELECT ISNULL(MAX(userId), 0) FROM cine.[User]")).scalar()
                user_id = max_user_id + 1
                conn.execute(text("INSERT INTO cine.[User](userId, email, avatarUrl, roleId) VALUES (:userId, :email, NULL, :roleId)"), {"userId": user_id, "email": email, "roleId": role_id})
                print(f"Debug: Created user with id: {user_id}")
                # Insert account with manual ID
                print(f"Debug: Inserting account for user_id: {user_id}")
                max_account_id = conn.execute(text("SELECT ISNULL(MAX(accountId), 0) FROM cine.[Account]")).scalar()
                account_id = max_account_id + 1
                conn.execute(text("INSERT INTO cine.[Account](accountId, username, passwordHash, userId) VALUES (:accountId, :u, HASHBYTES('SHA2_256', CONVERT(VARBINARY(512), :p)), :uid)"), {"accountId": account_id, "u": email, "p": password, "uid": user_id})
                print(f"Debug: Registration completed successfully")
            return render_template("register.html", success="Đăng ký thành công! Bạn có thể đăng nhập ngay.")
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
    
    with current_app.db_engine.connect() as conn:
        # Lấy thông tin phim chính
        r = conn.execute(text(
            "SELECT movieId, title, posterUrl, backdropUrl, releaseYear, overview, trailerUrl, viewCount FROM cine.Movie WHERE movieId=:id"
        ), {"id": movie_id}).mappings().first()
        
        # Tăng view count và lưu lịch sử xem
        if not is_trailer:
            # Kiểm tra xem user đã xem phim này trong session chưa
            viewed_movies = session.get('viewed_movies', [])
            if movie_id not in viewed_movies:
                conn.execute(text(
                    "UPDATE cine.Movie SET viewCount = viewCount + 1 WHERE movieId = :id"
                ), {"id": movie_id})
                
                # Lưu lịch sử xem vào database nếu user đã đăng nhập
                user_id = session.get("user_id")
                if user_id:
                    try:
                        # Lưu vào ViewHistory
                        conn.execute(text("""
                            INSERT INTO cine.ViewHistory (userId, movieId, startedAt, deviceType, ipAddress, userAgent)
                            VALUES (:user_id, :movie_id, GETDATE(), :device_type, :ip_address, :user_agent)
                        """), {
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
                        
                    except Exception as e:
                        print(f"Error saving view history: {e}")
                
                conn.commit()
                # Đánh dấu đã xem phim này trong session
                viewed_movies.append(movie_id)
                session['viewed_movies'] = viewed_movies
        
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
    
    # Reset view count
    with current_app.db_engine.connect() as conn:
        conn.execute(text(
            "UPDATE cine.Movie SET viewCount = 0 WHERE movieId = :id"
        ), {"id": movie_id})
        conn.commit()
    
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
                # Tạo watchlistId tự động
                max_id = conn.execute(text("""
                    SELECT ISNULL(MAX(watchlistId), 0) + 1 FROM cine.Watchlist
                """)).scalar()
                
                conn.execute(text("""
                    INSERT INTO [cine].[Watchlist] (watchlistId, userId, movieId, addedAt, priority, isWatched)
                    VALUES (:watchlist_id, :user_id, :movie_id, GETDATE(), 1, 0)
                """), {
                    "watchlist_id": max_id,
                    "user_id": user_id, 
                    "movie_id": movie_id
                })
                
                return jsonify({"success": True, "message": "Đã thêm vào danh sách xem sau"})
            else:
                return jsonify({"success": False, "message": "Phim đã có trong danh sách xem sau"})
                
    except Exception as e:
        print(f"Error adding to watchlist: {e}")
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
        print(f"Error removing from watchlist: {e}")
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
        print(f"Error checking watchlist status: {e}")
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
                
                return jsonify({
                    "success": True, 
                    "is_watchlist": False,
                    "message": "Đã xóa khỏi danh sách xem sau"
                })
            else:
                # Thêm vào watchlist - tạo watchlistId tự động
                max_id = conn.execute(text("""
                    SELECT ISNULL(MAX(watchlistId), 0) + 1 FROM cine.Watchlist
                """)).scalar()
                
                conn.execute(text("""
                    INSERT INTO [cine].[Watchlist] (watchlistId, userId, movieId, addedAt, priority, isWatched)
                    VALUES (:watchlist_id, :user_id, :movie_id, GETDATE(), 1, 0)
                """), {
                    "watchlist_id": max_id,
                    "user_id": user_id, 
                    "movie_id": movie_id
                })
                
                return jsonify({
                    "success": True, 
                    "is_watchlist": True,
                    "message": "Đã thêm vào danh sách xem sau"
                })
            
            # Mark CF model as dirty
            try:
                set_cf_dirty(current_app.db_engine)
            except Exception:
                pass
                
    except Exception as e:
        print(f"Error toggling watchlist: {e}")
        import traceback
        traceback.print_exc()
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
                # Tạo favoriteId tự động
                max_id = conn.execute(text("""
                    SELECT ISNULL(MAX(favoriteId), 0) + 1 FROM cine.Favorite
                """)).scalar()
                
                conn.execute(text("""
                    INSERT INTO [cine].[Favorite] (favoriteId, userId, movieId, addedAt)
                    VALUES (:favorite_id, :user_id, :movie_id, GETDATE())
                """), {
                    "favorite_id": max_id,
                    "user_id": user_id, 
                    "movie_id": movie_id
                })
                
                return jsonify({"success": True, "message": "Đã thêm vào danh sách yêu thích"})
            else:
                return jsonify({"success": False, "message": "Phim đã có trong danh sách yêu thích"})
            
            # Mark CF model as dirty
            try:
                set_cf_dirty(current_app.db_engine)
            except Exception:
                pass
                
    except Exception as e:
        print(f"Error adding to favorites: {e}")
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
        print(f"Error removing from favorites: {e}")
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
        print(f"Error searching watchlist: {e}")
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
        print(f"Error checking favorite status: {e}")
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
                
                return jsonify({
                    "success": True, 
                    "is_favorite": False,
                    "message": "Đã xóa khỏi danh sách yêu thích"
                })
            else:
                # Thêm vào favorites - tạo favoriteId tự động
                max_id = conn.execute(text("""
                    SELECT ISNULL(MAX(favoriteId), 0) + 1 FROM cine.Favorite
                """)).scalar()
                
                conn.execute(text("""
                    INSERT INTO [cine].[Favorite] (favoriteId, userId, movieId, addedAt)
                    VALUES (:favorite_id, :user_id, :movie_id, GETDATE())
                """), {
                    "favorite_id": max_id,
                    "user_id": user_id, 
                    "movie_id": movie_id
                })
                
                return jsonify({
                    "success": True, 
                    "is_favorite": True,
                    "message": "Đã thêm vào danh sách yêu thích"
                })
            
            # Mark CF model as dirty
            try:
                set_cf_dirty(current_app.db_engine)
            except Exception:
                pass
                
    except Exception as e:
        print(f"Error toggling favorite: {e}")
        import traceback
        traceback.print_exc()
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
        print(f"Error searching favorites: {e}")
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
    per_page = 12
    
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
        
        with current_app.db_engine.connect() as conn:
            # Tìm kiếm phim theo title (case-insensitive)
            suggestions = conn.execute(text(f"""
                SELECT TOP {limit} movieId, title, releaseYear, posterUrl
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
    """Retrain collaborative filtering model"""
    try:
        # Simple retrain logic without external module
        current_app.logger.info("Starting simple model retrain...")
        
        # For now, just return success - implement actual retrain logic later
        return jsonify({
            "success": True,
            "message": "Model retrain completed (placeholder)"
        })
        
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
        global collaborative_recommender, enhanced_cf_recommender
        
        # Debug logging
        current_app.logger.info(f"collaborative_recommender: {collaborative_recommender}")
        current_app.logger.info(f"enhanced_cf_recommender: {enhanced_cf_recommender}")
        
        # Initialize if not already done
        if collaborative_recommender is None or enhanced_cf_recommender is None:
            current_app.logger.info("Recommenders not initialized, initializing now...")
            init_recommenders()
        
        # Try Enhanced CF model first
        if enhanced_cf_recommender and enhanced_cf_recommender.is_model_loaded():
            model_info = enhanced_cf_recommender.get_model_info()
            model_info['model_type'] = 'Enhanced CF'
            current_app.logger.info(f"Enhanced CF model info: {model_info}")
            return jsonify({
                "success": True,
                "modelInfo": model_info
            })
        
        # Try Collaborative CF model
        if collaborative_recommender and collaborative_recommender.is_model_loaded():
            model_info = collaborative_recommender.get_model_info()
            model_info['model_type'] = 'CF'
            current_app.logger.info(f"Collaborative CF model info: {model_info}")
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
    """Switch between CF and Enhanced CF models"""
    try:
        global collaborative_recommender, enhanced_cf_recommender
        
        if model_type == "cf":
            if collaborative_recommender and collaborative_recommender.is_model_loaded():
                # Set priority to use CF model
                return jsonify({
                    "success": True,
                    "message": "Đã chuyển sang CF model",
                    "model_type": "CF"
                })
            else:
                return jsonify({
                    "success": False,
                    "message": "CF model chưa được load"
                })
                
        elif model_type == "enhanced":
            if enhanced_cf_recommender and enhanced_cf_recommender.is_model_loaded():
                # Set priority to use Enhanced CF model
                return jsonify({
                    "success": True,
                    "message": "Đã chuyển sang Enhanced CF model",
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
                "message": "Model type không hợp lệ. Chọn 'cf' hoặc 'enhanced'"
            })
            
    except Exception as e:
        current_app.logger.error(f"Error switching model: {str(e)}")
        return jsonify({
            "success": False,
            "message": f"Lỗi khi chuyển model: {str(e)}"
        })

@main_bp.route("/api/model_status", methods=["GET"])
@admin_required
def model_status():
    """Get model status and user information"""
    try:
        global collaborative_recommender
        
        if collaborative_recommender and collaborative_recommender.is_model_loaded():
            model_info = collaborative_recommender.get_model_info()
            
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
                    in_model = user_id in collaborative_recommender.user_mapping
                    
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

@main_bp.route("/api/generate_recommendations", methods=["POST"])
@login_required
def generate_recommendations():
    """Tạo recommendations cho user hiện tại và lưu vào database"""
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"success": False, "message": "Chưa đăng nhập"})
        
        # Khởi tạo collaborative recommender
        cf_recommender = CollaborativeRecommender(current_app.config['odbc_connect'])
        
        if not cf_recommender.is_model_loaded():
            return jsonify({"success": False, "message": "Model collaborative filtering chưa được load"})
        
        # Lấy recommendations từ model
        recommendations = cf_recommender.get_user_recommendations(user_id, limit=50)
        
        if not recommendations:
            return jsonify({"success": False, "message": "Không tìm thấy recommendations cho user này"})
        
        # Lưu recommendations vào database
        with current_app.config['odbc_connect'].connect() as conn:
            # Xóa recommendations cũ của user này
            conn.execute(text("""
                DELETE FROM [cine].[PersonalRecommendation] 
                WHERE userId = :user_id
            """), {"user_id": user_id})
            
            # Lưu recommendations mới
            for rank, movie in enumerate(recommendations, 1):
                conn.execute(text("""
                    INSERT INTO [cine].[PersonalRecommendation] 
                    (userId, movieId, score, rank, algo, generatedAt, expiresAt)
                    VALUES (:user_id, :movie_id, :score, :rank, 'collaborative', GETUTCDATE(), DATEADD(day, 7, GETUTCDATE()))
                """), {
                    "user_id": user_id,
                    "movie_id": movie['movieId'],
                    "score": movie['recommendation_score'],
                    "rank": rank
                })
            
            conn.commit()
        
        return jsonify({
            "success": True, 
            "message": f"Đã tạo {len(recommendations)} recommendations",
            "recommendations": recommendations[:10]  # Trả về 10 recommendations đầu tiên
        })
        
    except Exception as e:
        print(f"Error generating recommendations: {e}")
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
                    STRING_AGG(TOP 5 g.name, ', ') as genres
                FROM [cine].[PersonalRecommendation] pr
                INNER JOIN [cine].[Movie] m ON pr.movieId = m.movieId
                LEFT JOIN [cine].[Rating] r ON m.movieId = r.movieId
                LEFT JOIN [cine].[MovieGenre] mg ON m.movieId = mg.movieId
                LEFT JOIN [cine].[Genre] g ON mg.genreId = g.genreId
                WHERE pr.userId = :user_id 
                    AND pr.expiresAt > GETDATE()
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
        global content_recommender, collaborative_recommender
        if collaborative_recommender is None:
            init_recommenders()
        
        # Sử dụng Enhanced CF trước, fallback về Collaborative CF
        if enhanced_cf_recommender and enhanced_cf_recommender.is_model_loaded():
            similar_movies = enhanced_cf_recommender.get_similar_movies(movie_id, limit)
        elif collaborative_recommender and collaborative_recommender.is_model_loaded():
            similar_movies = collaborative_recommender.get_similar_movies(movie_id, limit)
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
    """Lấy danh sách phim trending"""
    try:
        limit = request.args.get('limit', 20, type=int)
        
        # Initialize recommenders if not already done
        global content_recommender, collaborative_recommender
        if collaborative_recommender is None:
            init_recommenders()
        
        # Lấy trending movies - Enhanced CF trước
        if enhanced_cf_recommender and enhanced_cf_recommender.is_model_loaded():
            trending_movies = enhanced_cf_recommender.get_trending_movies(limit)
        elif collaborative_recommender and collaborative_recommender.is_model_loaded():
            trending_movies = collaborative_recommender.get_trending_movies(limit)
        else:
            # Fallback: lấy từ database
            with current_app.db_engine.connect() as conn:
                trending_rows = conn.execute(text("""
                    SELECT TOP :limit
                        m.movieId, m.title, m.releaseYear, m.country, m.posterUrl,
                        m.viewCount, COUNT(r.movieId) as ratingCount,
                        AVG(CAST(r.value AS FLOAT)) as avgRating,
                        STRING_AGG(TOP 5 g.name, ', ') as genres
                    FROM cine.Movie m
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                    LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                    WHERE m.movieId IN (
                        SELECT movieId FROM cine.Movie 
                        WHERE movieId IN (SELECT DISTINCT movieId FROM cine.Rating)
                    )
                    GROUP BY m.movieId, m.title, m.releaseYear, m.country, m.posterUrl, m.viewCount
                    ORDER BY ratingCount DESC, avgRating DESC
                """), {"limit": limit}).mappings().all()
                
                trending_movies = [
                    {
                        'movieId': row.movieId,
                        'title': row.title,
                        'releaseYear': row.releaseYear,
                        'country': row.country,
                        'posterUrl': row.posterUrl,
                        'viewCount': row.viewCount,
                        'ratingCount': row.ratingCount,
                        'avgRating': round(float(row.avgRating), 2) if row.avgRating else 0.0,
                        'genres': row.genres or ''
                    }
                    for row in trending_rows
                ]
        
        return jsonify({
            "success": True,
            "trending_movies": trending_movies
        })
        
    except Exception as e:
        print(f"Error getting trending movies: {e}")
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
        
        # Khởi tạo collaborative recommender
        cf_recommender = CollaborativeRecommender(current_app.config['odbc_connect'])
        
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
        global content_recommender, collaborative_recommender
        if collaborative_recommender is None:
            init_recommenders()
        
        if collaborative_recommender:
            model_info = collaborative_recommender.get_model_info()
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
        
        # Initialize recommenders if not already done
        global content_recommender, collaborative_recommender
        if collaborative_recommender is None:
            init_recommenders()
        
        recommendations = []
        
        if collaborative_recommender and collaborative_recommender.is_model_loaded():
            current_app.logger.info(f"Getting personalized recommendations for user {user_id}")
            
            # Lấy recommendations từ CF model
            cf_recommendations = collaborative_recommender.get_user_recommendations(user_id, limit=limit)
            
            if cf_recommendations:
                # Lưu vào bảng PersonalRecommendation nếu force_refresh
                if force_refresh:
                    with current_app.db_engine.connect() as conn:
                        # Xóa recommendations cũ
                        conn.execute(text("""
                            DELETE FROM cine.PersonalRecommendation 
                            WHERE userId = :user_id
                        """), {"user_id": user_id})
                        
                        # Lưu recommendations mới
                        for rank, rec in enumerate(cf_recommendations, 1):
                            # Generate recId
                            rec_id_result = conn.execute(text("""
                                SELECT ISNULL(MAX(recId), 0) + 1 FROM cine.PersonalRecommendation
                            """)).fetchone()
                            rec_id = rec_id_result[0] if rec_id_result else 1
                            
                            conn.execute(text("""
                                INSERT INTO cine.PersonalRecommendation 
                                (recId, userId, movieId, score, rank, algo, generatedAt, expiresAt)
                                VALUES (:rec_id, :user_id, :movie_id, :score, :rank, 'collaborative', GETUTCDATE(), DATEADD(day, 7, GETUTCDATE()))
                            """), {
                                "rec_id": rec_id,
                                "user_id": user_id,
                                "movie_id": rec["movieId"],
                                "score": rec.get("recommendation_score", 0),
                                "rank": rank
                            })
                        
                        conn.commit()
                        print(f"Saved {len(cf_recommendations)} personalized recommendations to database")
                
                # Format recommendations
                recommendations = [
                    {
                        "movieId": rec["movieId"],
                        "title": rec["title"],
                        "poster": rec.get("posterUrl") if rec.get("posterUrl") and rec.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={rec['title'][:20].replace(' ', '+')}",
                        "releaseYear": rec.get("releaseYear"),
                        "country": rec.get("country"),
                        "genres": rec.get("genres", ""),
                        "recommendationScore": round(rec.get("recommendation_score", 0), 4),
                        "avgRating": round(rec.get("avgRating", 0), 2),
                        "ratingCount": rec.get("ratingCount", 0),  # Tổng số rating của phim
                        "reason": "Dựa trên sở thích của bạn và người dùng tương tự"
                    }
                    for rec in cf_recommendations
                ]
                
            return jsonify({
                "success": True,
                "message": f"Đã tạo {len(recommendations)} gợi ý cá nhân hóa",
                "recommendations": recommendations,
                "algorithm": "Collaborative Filtering",
                "userInModel": True
            })
        
            # no cf_recommendations
            return jsonify({
                "success": False,
                "message": "Không tìm thấy gợi ý cá nhân hóa. Hãy đánh giá thêm phim để có gợi ý tốt hơn.",
                "recommendations": [],
                "algorithm": "Collaborative Filtering",
                "userInModel": False
            })
        
        # model not loaded
        return jsonify({
            "success": False,
            "message": "Hệ thống gợi ý chưa sẵn sàng. Vui lòng thử lại sau.",
            "recommendations": [],
            "algorithm": "None",
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
                        STRING_AGG(g.name, ', ') WITHIN GROUP (ORDER BY g.name) as genres
                    FROM cine.ColdStartRecommendations csr
                    INNER JOIN cine.Movie m ON csr.movieId = m.movieId
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                    LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                    WHERE csr.userId = :user_id AND csr.expiresAt > GETUTCDATE()
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
                    STRING_AGG(g.name, ', ') WITHIN GROUP (ORDER BY g.name) as genres
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
            
            # Lưu recommendations mới
            for rec in recommendations:
                conn.execute(text("""
                    INSERT INTO cine.ColdStartRecommendations 
                    (userId, movieId, score, rank, source, generatedAt, expiresAt, reason)
                    VALUES (:user_id, :movie_id, :score, :rank, :source, GETUTCDATE(), DATEADD(day, 7, GETUTCDATE()), :reason)
                """), {
                    "user_id": user_id,
                    "movie_id": rec["id"],
                    "score": rec["score"],
                    "rank": rec["rank"],
                    "source": rec["source"],
                    "reason": f"Recommendation based on your genre preferences"
                })
            
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
    """Retrain Collaborative Filtering model"""
    try:
        import subprocess
        import os
        import sys
        
        # Chạy script retrain model
        script_path = os.path.join(os.path.dirname(__file__), '..', 'model_collaborative', 'train_collaborative_fast.py')
        
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
        
        # Chạy với timeout để tránh treo
        result = subprocess.run(
            [python_exec, script_path], 
            capture_output=True, 
            text=True, 
            cwd=os.path.dirname(script_path),
            timeout=300  # 5 phút timeout
        )
        
        current_app.logger.info(f"Return code: {result.returncode}")
        current_app.logger.info(f"Stdout: {result.stdout}")
        current_app.logger.info(f"Stderr: {result.stderr}")
        
        if result.returncode == 0:
            # Reload model sau khi retrain
            global collaborative_recommender
            if collaborative_recommender:
                collaborative_recommender.load_model()
            
            return jsonify({
                "success": True,
                "message": "Model CF đã được retrain thành công",
                "output": result.stdout
            })
        else:
            return jsonify({
                "success": False,
                "message": f"Lỗi khi retrain model (code: {result.returncode})",
                "output": result.stdout,
                "error": result.stderr
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
        
        with current_app.db_engine.connect() as conn:
            # Lấy 12 phim ngẫu nhiên
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
            
            # Xóa recommendations cũ
            conn.execute(text("""
                DELETE FROM cine.PersonalRecommendation WHERE userId = :user_id
            """), {"user_id": user_id})
            
            # Tạo recommendations mẫu
            for rank, movie in enumerate(movies, 1):
                score = round(0.5 + (rank * 0.1), 2)  # Score từ 0.6 đến 1.7
                
                # Generate recId
                rec_id_result = conn.execute(text("""
                    SELECT ISNULL(MAX(recId), 0) + 1 FROM cine.PersonalRecommendation
                """)).fetchone()
                rec_id = rec_id_result[0] if rec_id_result else 1
                
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
            
            conn.commit()
            
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


