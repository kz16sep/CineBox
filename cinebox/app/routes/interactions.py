"""
User interaction routes: watchlist, favorites, ratings, comments
"""

from flask import jsonify, request, session, current_app
from sqlalchemy import text
from . import main_bp
from .decorators import login_required
from .common import get_poster_or_dummy


# Note: Các routes watchlist, favorites, ratings, comments sẽ được di chuyển từ routes.py
# Đây chỉ là structure cơ bản

@main_bp.route("/add-watchlist/<int:movie_id>", methods=["POST"])
@login_required
def add_watchlist(movie_id):
    """Add movie to watchlist"""
    user_id = session.get("user_id")
    try:
        with current_app.db_engine.begin() as conn:
            # Check if already exists
            existing = conn.execute(text("""
                SELECT 1 FROM cine.Watchlist 
                WHERE userId = :user_id AND movieId = :movie_id
            """), {"user_id": user_id, "movie_id": movie_id}).scalar()
            
            if existing:
                return jsonify({"success": False, "message": "Đã có trong danh sách xem sau"})
            
            # Get max watchlistId
            max_id = conn.execute(text("SELECT ISNULL(MAX(watchlistId), 0) FROM cine.Watchlist")).scalar() or 0
            new_id = max_id + 1
            
            conn.execute(text("""
                INSERT INTO cine.Watchlist (watchlistId, userId, movieId, addedAt)
                VALUES (:id, :user_id, :movie_id, GETDATE())
            """), {"id": new_id, "user_id": user_id, "movie_id": movie_id})
            
            return jsonify({"success": True, "message": "Đã thêm vào danh sách xem sau"})
    except Exception as e:
        current_app.logger.error(f"Error adding to watchlist: {e}")
        return jsonify({"success": False, "message": f"Lỗi: {str(e)}"})


@main_bp.route("/remove-watchlist/<int:movie_id>", methods=["POST"])
@login_required
def remove_watchlist(movie_id):
    """Remove movie from watchlist"""
    user_id = session.get("user_id")
    try:
        with current_app.db_engine.begin() as conn:
            conn.execute(text("""
                DELETE FROM cine.Watchlist 
                WHERE userId = :user_id AND movieId = :movie_id
            """), {"user_id": user_id, "movie_id": movie_id})
            
            return jsonify({"success": True, "message": "Đã xóa khỏi danh sách xem sau"})
    except Exception as e:
        current_app.logger.error(f"Error removing from watchlist: {e}")
        return jsonify({"success": False, "message": f"Lỗi: {str(e)}"})


@main_bp.route("/add-favorite/<int:movie_id>", methods=["POST"])
@login_required
def add_favorite(movie_id):
    """Add movie to favorites"""
    user_id = session.get("user_id")
    try:
        with current_app.db_engine.begin() as conn:
            existing = conn.execute(text("""
                SELECT 1 FROM cine.Favorite 
                WHERE userId = :user_id AND movieId = :movie_id
            """), {"user_id": user_id, "movie_id": movie_id}).scalar()
            
            if existing:
                return jsonify({"success": False, "message": "Đã có trong danh sách yêu thích"})
            
            max_id = conn.execute(text("SELECT ISNULL(MAX(favoriteId), 0) FROM cine.Favorite")).scalar() or 0
            new_id = max_id + 1
            
            conn.execute(text("""
                INSERT INTO cine.Favorite (favoriteId, userId, movieId, addedAt)
                VALUES (:id, :user_id, :movie_id, GETDATE())
            """), {"id": new_id, "user_id": user_id, "movie_id": movie_id})
            
            return jsonify({"success": True, "message": "Đã thêm vào danh sách yêu thích"})
    except Exception as e:
        current_app.logger.error(f"Error adding to favorites: {e}")
        return jsonify({"success": False, "message": f"Lỗi: {str(e)}"})


@main_bp.route("/remove-favorite/<int:movie_id>", methods=["POST"])
@login_required
def remove_favorite(movie_id):
    """Remove movie from favorites"""
    user_id = session.get("user_id")
    try:
        with current_app.db_engine.begin() as conn:
            conn.execute(text("""
                DELETE FROM cine.Favorite 
                WHERE userId = :user_id AND movieId = :movie_id
            """), {"user_id": user_id, "movie_id": movie_id})
            
            return jsonify({"success": True, "message": "Đã xóa khỏi danh sách yêu thích"})
    except Exception as e:
        current_app.logger.error(f"Error removing from favorites: {e}")
        return jsonify({"success": False, "message": f"Lỗi: {str(e)}"})


@main_bp.route("/check-watchlist/<int:movie_id>", methods=["GET"])
def check_watchlist(movie_id):
    """Kiểm tra trạng thái xem sau của phim"""
    if not session.get("user_id"):
        return jsonify({"success": False, "is_watchlist": False})
    
    user_id = session.get("user_id")
    
    try:
        with current_app.db_engine.connect() as conn:
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
@login_required
def toggle_watchlist(movie_id):
    """Chuyển đổi trạng thái xem sau của phim"""
    user_id = session.get("user_id")
    
    try:
        with current_app.db_engine.begin() as conn:
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
                from .common import set_cf_dirty
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
                # Lấy watchlistId tiếp theo
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
                from .common import set_cf_dirty
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
        current_app.logger.error(f"Error checking favorite status for user {user_id}, movie {movie_id}: {e}", exc_info=True)
        return jsonify({"success": False, "is_favorite": False})


@main_bp.route("/toggle-favorite/<int:movie_id>", methods=["POST"])
@login_required
def toggle_favorite(movie_id):
    """Chuyển đổi trạng thái yêu thích của phim"""
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
                from .common import set_cf_dirty
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
                # Lấy favoriteId tiếp theo
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
                from .common import set_cf_dirty
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


# Rating routes
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
                "avgRating": round(stats.avg_rating, 1) if stats.avg_rating else 0,
                "ratingCount": stats.total_ratings,
                "avg_rating": round(stats.avg_rating, 1) if stats.avg_rating else 0,
                "total_ratings": stats.total_ratings
            })
            
    except Exception as e:
        current_app.logger.error(f"Error getting rating: {e}")
        return jsonify({"success": False, "user_rating": 0, "avg_rating": 0, "total_ratings": 0})


@main_bp.route("/delete-rating/<int:movie_id>", methods=["POST"])
@login_required
def delete_rating(movie_id):
    """Xóa đánh giá phim"""
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
            
            # Mark CF model as dirty
            from .common import set_cf_dirty
            try:
                set_cf_dirty(current_app.db_engine)
            except Exception:
                pass
            
            return jsonify({
                "success": True,
                "message": "Đã xóa đánh giá",
                "avgRating": round(stats.avg_rating, 1) if stats.avg_rating else 0,
                "ratingCount": stats.total_ratings,
                "avg_rating": round(stats.avg_rating, 1) if stats.avg_rating else 0,
                "total_ratings": stats.total_ratings
            })
            
    except Exception as e:
        current_app.logger.error(f"Error deleting rating: {e}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra khi xóa đánh giá"})


# Comment routes
@main_bp.route("/submit-comment/<int:movie_id>", methods=["POST"])
@login_required
def submit_comment(movie_id):
    """Gửi comment cho phim"""
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
@login_required
def update_comment(comment_id):
    """Cập nhật comment"""
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
@login_required
def delete_comment(comment_id):
    """Xóa comment"""
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


@main_bp.route("/api/search-watchlist", methods=["GET"])
@login_required
def api_search_watchlist():
    """API tìm kiếm watchlist với AJAX"""
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
                "page": page,  # Alias for compatibility
                "total_pages": total_pages,
                "pages": total_pages,  # Alias for compatibility
                "total_items": watchlist_total,
                "per_page": per_page,
                "has_prev": page > 1,
                "has_next": page < total_pages,
                "prev_num": page - 1 if page > 1 else None,
                "next_num": page + 1 if page < total_pages else None
            }
            
            # Format dữ liệu
            movies = []
            for movie in watchlist:
                movies.append({
                    "id": movie["movieId"],
                    "title": movie["title"],
                    "poster": get_poster_or_dummy(movie.get("posterUrl"), movie["title"]),
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
        current_app.logger.error(f"Error searching watchlist for user {user_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Có lỗi xảy ra"})


@main_bp.route("/api/search-favorites", methods=["GET"])
@login_required
def api_search_favorites():
    """API tìm kiếm favorites với AJAX"""
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
                """), {
                    "user_id": user_id,
                    "offset": favorites_offset, 
                    "per_page": per_page
                }).mappings().all()
            
            # Tính toán pagination
            total_pages = (favorites_total + per_page - 1) // per_page
            pagination = {
                "current_page": page,
                "page": page,  # Alias for compatibility
                "total_pages": total_pages,
                "pages": total_pages,  # Alias for compatibility
                "total_items": favorites_total,
                "per_page": per_page,
                "has_prev": page > 1,
                "has_next": page < total_pages,
                "prev_num": page - 1 if page > 1 else None,
                "next_num": page + 1 if page < total_pages else None
            }
            
            # Format dữ liệu
            movies = []
            for movie in favorites:
                movies.append({
                    "id": movie["movieId"],
                    "title": movie["title"],
                    "poster": get_poster_or_dummy(movie.get("posterUrl"), movie["title"]),
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
        current_app.logger.error(f"Error searching favorites for user {user_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Có lỗi xảy ra"})

