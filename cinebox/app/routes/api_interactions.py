"""
API routes for user interactions: view history, ratings, comments
"""

from flask import jsonify, request, session, current_app
from sqlalchemy import text
from . import main_bp
from .decorators import login_required


@main_bp.route("/api/view_history")
@login_required
def get_view_history():
    """Get user view history"""
    user_id = session.get("user_id")
    limit = request.args.get('limit', 20, type=int)
    
    try:
        with current_app.db_engine.connect() as conn:
            history = conn.execute(text("""
                SELECT TOP (:limit)
                    vh.historyId, vh.movieId, m.title, m.posterUrl, vh.startedAt, vh.progressSec
                FROM cine.ViewHistory vh
                JOIN cine.Movie m ON vh.movieId = m.movieId
                WHERE vh.userId = :user_id
                ORDER BY vh.startedAt DESC
            """), {"user_id": user_id, "limit": limit}).mappings().all()
            
            return jsonify({
                "success": True,
                "history": [dict(h) for h in history]
            })
    except Exception as e:
        current_app.logger.error(f"Error getting view history: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@main_bp.route("/api/update_watch_progress", methods=["POST"])
@login_required
def update_watch_progress():
    """Update watch progress for a movie"""
    user_id = session.get("user_id")
    data = request.get_json()
    
    movie_id = data.get('movie_id')
    progress_sec = data.get('progress_sec', 0)
    is_finished = data.get('finished', False)
    
    try:
        with current_app.db_engine.begin() as conn:
            # Update or insert view history
            conn.execute(text("""
                UPDATE cine.ViewHistory
                SET progressSec = :progress, finishedAt = CASE WHEN :finished = 1 THEN GETDATE() ELSE NULL END
                WHERE userId = :user_id AND movieId = :movie_id
            """), {
                "progress": progress_sec,
                "finished": 1 if is_finished else 0,
                "user_id": user_id,
                "movie_id": movie_id
            })
            
            return jsonify({"success": True, "message": "Đã cập nhật tiến độ xem"})
    except Exception as e:
        current_app.logger.error(f"Error updating watch progress: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@main_bp.route("/submit-rating/<int:movie_id>", methods=["POST"])
@login_required
def submit_rating(movie_id):
    """Gửi đánh giá phim"""
    user_id = session.get("user_id")
    data = request.get_json()
    rating_value = data.get('rating') if data else request.form.get('rating', type=int)
    
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
            
            # Mark CF model as dirty for retrain
            from .common import set_cf_dirty
            try:
                set_cf_dirty(current_app.db_engine)
            except Exception:
                pass
            
            return jsonify({
                "success": True,
                "message": message,
                "user_rating": rating_value,
                "avgRating": round(stats.avg_rating, 1) if stats.avg_rating else 0,
                "ratingCount": stats.total_ratings,
                "avg_rating": round(stats.avg_rating, 1) if stats.avg_rating else 0,
                "total_ratings": stats.total_ratings
            })
                
    except Exception as e:
        current_app.logger.error(f"Error submitting rating: {e}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra khi đánh giá"})


# Note: Các API routes còn lại (get-rating, delete-rating, submit-comment, get-comments, etc.)
# sẽ được di chuyển từ routes.py sang đây

