"""
User account routes: account, profile, history
"""

from flask import render_template, request, redirect, url_for, session, current_app, jsonify, flash
from sqlalchemy import text
from werkzeug.utils import secure_filename
import os
import re
from . import main_bp
from .decorators import login_required
from .common import get_poster_or_dummy


@main_bp.route("/account")
@login_required
def account():
    """User account page"""
    user_id = session.get("user_id")
    
    if not user_id:
        current_app.logger.warning("No user_id in session")
        return redirect(url_for("main.login"))
    
    watchlist_page = request.args.get('watchlist_page', 1, type=int)
    favorites_page = request.args.get('favorites_page', 1, type=int)
    watchlist_search = request.args.get('watchlist_search', '', type=str).strip()
    favorites_search = request.args.get('favorites_search', '', type=str).strip()
    per_page = 8
    
    # Khởi tạo biến mặc định
    user_info = None
    watchlist = []
    favorites = []
    watchlist_total = 0
    favorites_total = 0
    watchlist_pagination = None
    favorites_pagination = None
    
    try:
        with current_app.db_engine.connect() as conn:
            user_info = conn.execute(text("""
                SELECT u.userId, u.email, u.avatarUrl, u.phone, u.status, u.createdAt, u.lastLoginAt, r.roleName, a.username
                FROM [cine].[User] u
                JOIN [cine].[Role] r ON u.roleId = r.roleId
                LEFT JOIN [cine].[Account] a ON a.userId = u.userId
                WHERE u.userId = :user_id
            """), {"user_id": user_id}).mappings().first()
            
            if not user_info:
                current_app.logger.error(f"User {user_id} not found in database")
                session.clear()
                return redirect(url_for("main.login"))
            
            if user_info.avatarUrl:
                session['avatar'] = user_info.avatarUrl
            
            # Watchlist với search và pagination
            if watchlist_search:
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
                    ORDER BY wl.addedAt DESC
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """), {
                    "user_id": user_id, 
                    "search": f"%{watchlist_search}%",
                    "offset": watchlist_offset, 
                    "per_page": per_page
                }).mappings().all()
            else:
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
            
            # Favorites với search và pagination
            if favorites_search:
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
                    ORDER BY f.addedAt DESC
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """), {
                    "user_id": user_id, 
                    "search": f"%{favorites_search}%",
                    "offset": favorites_offset, 
                    "per_page": per_page
                }).mappings().all()
            else:
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
            
            # Format watchlist và favorites với poster URL
            formatted_watchlist = []
            for movie in watchlist:
                formatted_watchlist.append({
                    "movieId": movie["movieId"],
                    "title": movie["title"],
                    "posterUrl": get_poster_or_dummy(movie.get("posterUrl"), movie["title"]),
                    "releaseYear": movie.get("releaseYear"),
                    "addedAt": movie.get("addedAt")
                })
            
            formatted_favorites = []
            for movie in favorites:
                formatted_favorites.append({
                    "movieId": movie["movieId"],
                    "title": movie["title"],
                    "posterUrl": get_poster_or_dummy(movie.get("posterUrl"), movie["title"]),
                    "releaseYear": movie.get("releaseYear"),
                    "addedAt": movie.get("addedAt")
                })
            
            # Pagination
            watchlist_pages = (watchlist_total + per_page - 1) // per_page if watchlist_total > 0 else 0
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
            
            favorites_pages = (favorites_total + per_page - 1) // per_page if favorites_total > 0 else 0
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
            
            watchlist = formatted_watchlist
            favorites = formatted_favorites
            
            # Debug logging
            current_app.logger.info(f"Account page - User {user_id}: watchlist_count={len(watchlist)}, favorites_count={len(favorites)}")
            current_app.logger.info(f"Watchlist total: {watchlist_total}, Favorites total: {favorites_total}")
            
    except Exception as e:
        current_app.logger.error(f"Error getting account info: {e}", exc_info=True)
        session.clear()
        return redirect(url_for("main.login"))
    
    if not user_info:
        current_app.logger.error(f"User info is None for user_id: {user_id}")
        session.clear()
        return redirect(url_for("main.login"))
    
    # Đảm bảo watchlist và favorites luôn là list (không phải None)
    if watchlist is None:
        watchlist = []
    if favorites is None:
        favorites = []
    if watchlist_pagination is None:
        watchlist_pagination = {"page": 1, "per_page": per_page, "total": 0, "pages": 0, "has_prev": False, "has_next": False}
    if favorites_pagination is None:
        favorites_pagination = {"page": 1, "per_page": per_page, "total": 0, "pages": 0, "has_prev": False, "has_next": False}
    
    current_app.logger.info(f"Rendering account page - watchlist: {len(watchlist)} items, favorites: {len(favorites)} items")
    
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
@login_required
def account_history():
    """User viewing history page"""
    user_id = session.get("user_id")
    
    if not user_id:
        current_app.logger.warning("No user_id in session")
        return redirect(url_for("main.login"))
    
    page = request.args.get('page', 1, type=int)
    search_query = request.args.get('q', '', type=str).strip()
    per_page = 10
    user_info = None
    
    try:
        with current_app.db_engine.connect() as conn:
            user_info = conn.execute(text("""
                SELECT u.userId, u.email, u.avatarUrl, u.phone, u.status, u.createdAt, u.lastLoginAt, r.roleName, a.username
                FROM [cine].[User] u
                JOIN [cine].[Role] r ON u.roleId = r.roleId
                LEFT JOIN [cine].[Account] a ON a.userId = u.userId
                WHERE u.userId = :user_id
            """), {"user_id": user_id}).mappings().first()
            
            if not user_info:
                current_app.logger.error(f"User {user_id} not found in database")
                session.clear()
                return redirect(url_for("main.login"))
            
            # History với search và pagination
            if search_query:
                # Đếm số phim duy nhất
                history_total = conn.execute(text("""
                    SELECT COUNT(DISTINCT vh.movieId) 
                    FROM [cine].[ViewHistory] vh
                    JOIN [cine].[Movie] m ON vh.movieId = m.movieId
                    WHERE vh.userId = :user_id AND m.title LIKE :search
                """), {"user_id": user_id, "search": f"%{search_query}%"}).scalar()
                
                history_offset = (page - 1) * per_page
                # Lấy phim duy nhất với lần xem gần nhất, kèm genres
                history = conn.execute(text("""
                    WITH LatestHistory AS (
                        SELECT 
                            vh.movieId,
                            MAX(vh.startedAt) AS latestStartedAt
                        FROM [cine].[ViewHistory] vh
                        JOIN [cine].[Movie] m ON vh.movieId = m.movieId
                        WHERE vh.userId = :user_id AND m.title LIKE :search
                        GROUP BY vh.movieId
                    )
                    SELECT 
                        vh.historyId, 
                        vh.movieId, 
                        m.title, 
                        m.posterUrl, 
                        m.releaseYear, 
                        vh.startedAt, 
                        vh.progressSec,
                        vh.finishedAt,
                        m.durationMin,
                        STUFF((
                            SELECT ', ' + g.name
                            FROM [cine].[MovieGenre] mg
                            JOIN [cine].[Genre] g ON mg.genreId = g.genreId
                            WHERE mg.movieId = m.movieId
                            FOR XML PATH(''), TYPE
                        ).value('.', 'NVARCHAR(MAX)'), 1, 2, '') AS genres
                    FROM [cine].[ViewHistory] vh
                    JOIN [cine].[Movie] m ON vh.movieId = m.movieId
                    JOIN LatestHistory lh ON vh.movieId = lh.movieId AND vh.startedAt = lh.latestStartedAt
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
                # Đếm số phim duy nhất (không phải số lần xem)
                history_total = conn.execute(text("""
                    SELECT COUNT(DISTINCT vh.movieId) 
                    FROM [cine].[ViewHistory] vh
                    WHERE vh.userId = :user_id
                """), {"user_id": user_id}).scalar()
                
                history_offset = (page - 1) * per_page
                # Lấy phim duy nhất với lần xem gần nhất, kèm genres
                history = conn.execute(text("""
                    WITH LatestHistory AS (
                        SELECT 
                            vh.movieId,
                            MAX(vh.startedAt) AS latestStartedAt
                        FROM [cine].[ViewHistory] vh
                        WHERE vh.userId = :user_id
                        GROUP BY vh.movieId
                    )
                    SELECT 
                        vh.historyId, 
                        vh.movieId, 
                        m.title, 
                        m.posterUrl, 
                        m.releaseYear, 
                        vh.startedAt, 
                        vh.progressSec,
                        vh.finishedAt,
                        m.durationMin,
                        STUFF((
                            SELECT ', ' + g.name
                            FROM [cine].[MovieGenre] mg
                            JOIN [cine].[Genre] g ON mg.genreId = g.genreId
                            WHERE mg.movieId = m.movieId
                            FOR XML PATH(''), TYPE
                        ).value('.', 'NVARCHAR(MAX)'), 1, 2, '') AS genres
                    FROM [cine].[ViewHistory] vh
                    JOIN [cine].[Movie] m ON vh.movieId = m.movieId
                    JOIN LatestHistory lh ON vh.movieId = lh.movieId AND vh.startedAt = lh.latestStartedAt
                    WHERE vh.userId = :user_id
                    ORDER BY vh.startedAt DESC
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """), {"user_id": user_id, "offset": history_offset, "per_page": per_page}).mappings().all()
            
            total_pages = (history_total + per_page - 1) // per_page if history_total > 0 else 0
            pagination = {
                "page": page,
                "per_page": per_page,
                "total": history_total,
                "pages": total_pages,
                "has_prev": page > 1,
                "has_next": page < total_pages,
                "prev_num": page - 1 if page > 1 else None,
                "next_num": page + 1 if page < total_pages else None
            }
            
            # Format history data với poster URL và genres
            formatted_history = []
            for item in history:
                # Tính progress percentage
                progress_percent = 0
                if item.get("durationMin") and item.get("durationMin") > 0 and item.get("progressSec"):
                    progress_percent = min(100, (item.get("progressSec") / 60.0 / item.get("durationMin")) * 100)
                
                # Kiểm tra hoàn thành: có finishedAt hoặc progress >= 90%
                is_completed = item.get("finishedAt") is not None or progress_percent >= 90
                
                formatted_history.append({
                    "historyId": item["historyId"],
                    "movieId": item["movieId"],
                    "title": item["title"],
                    "posterUrl": get_poster_or_dummy(item.get("posterUrl"), item["title"]),
                    "releaseYear": item.get("releaseYear"),
                    "startedAt": item.get("startedAt"),
                    "finishedAt": item.get("finishedAt"),
                    "progressSec": item.get("progressSec"),
                    "durationMin": item.get("durationMin"),
                    "progressPercent": round(progress_percent, 1),
                    "isCompleted": is_completed,
                    "genres": item.get("genres") or ""  # Lấy genres từ query
                })
            
            # Debug logging
            current_app.logger.info(f"History page - User {user_id}: history_count={len(formatted_history)}, total={history_total}")
            
            return render_template("history.html",
                                 user=user_info,
                                 view_history=formatted_history,  # Đổi tên từ history sang view_history
                                 history=formatted_history,  # Giữ cả hai để tương thích
                                 pagination=pagination,
                                 search_query=search_query)
            
    except Exception as e:
        current_app.logger.error(f"Error getting history: {e}", exc_info=True)
        session.clear()
        return redirect(url_for("main.login"))
    
    if not user_info:
        current_app.logger.error(f"User info is None for user_id: {user_id}")
        session.clear()
        return redirect(url_for("main.login"))


@main_bp.route("/update-profile", methods=["POST"])
@login_required
def update_profile():
    """Cập nhật thông tin profile"""
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("main.login"))
    
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    
    try:
        with current_app.db_engine.begin() as conn:
            # Update username in Account table
            if name:
                conn.execute(text("""
                    UPDATE [cine].[Account] 
                    SET username = :name
                    WHERE userId = :user_id
                """), {"name": name, "user_id": user_id})
                session["username"] = name
            
            # Update phone in User table
            if phone:
                conn.execute(text("""
                    UPDATE [cine].[User] 
                    SET phone = :phone
                    WHERE userId = :user_id
                """), {"phone": phone, "user_id": user_id})
        
        flash("Cập nhật thông tin thành công!", "success")
        return redirect(url_for("main.account"))
        
    except Exception as e:
        current_app.logger.error(f"Error updating profile: {e}")
        flash("Có lỗi xảy ra khi cập nhật thông tin", "error")
        return redirect(url_for("main.account"))


@main_bp.route('/update-password', methods=['POST'])
@login_required
def update_password():
    """Cập nhật mật khẩu"""
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("main.login"))
    
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
        current_app.logger.error(f"Error updating password: {e}")
        flash("Có lỗi xảy ra khi đổi mật khẩu", "error")
        return redirect(url_for("main.account"))


@main_bp.route('/api/update-email', methods=['POST'])
@login_required
def api_update_email():
    """API cập nhật email"""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "Chưa đăng nhập"}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "Dữ liệu không hợp lệ"}), 400
    
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
        
        session["email"] = new_email
        return jsonify({"success": True, "message": "Email đã được cập nhật thành công"})
        
    except Exception as e:
        current_app.logger.error(f"Error updating email: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"Có lỗi xảy ra khi cập nhật email: {str(e)}"}), 500


@main_bp.route('/api/update-username', methods=['POST'])
@login_required
def api_update_username():
    """API cập nhật username"""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "Chưa đăng nhập"}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "Dữ liệu không hợp lệ"}), 400
    
    new_username = data.get('username', '').strip()
    
    # Validation
    if not new_username:
        return jsonify({"success": False, "message": "Vui lòng nhập username"}), 400
    
    if len(new_username) < 3:
        return jsonify({"success": False, "message": "Username phải có ít nhất 3 ký tự"}), 400
    
    if len(new_username) > 100:
        return jsonify({"success": False, "message": "Username không được quá 100 ký tự"}), 400
    
    # Validate username pattern
    username_pattern = r'^[a-zA-Z0-9._\u00C0-\u017F\u1E00-\u1EFF\u0100-\u017F\u0180-\u024F -]+$'
    if not re.match(username_pattern, new_username):
        return jsonify({"success": False, "message": "Tên người dùng chỉ được chứa chữ cái (có dấu), số, dấu chấm, gạch dưới, gạch ngang và khoảng trắng"}), 400
    
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
        
        session["username"] = new_username
        return jsonify({"success": True, "message": "Username đã được cập nhật thành công"})
        
    except Exception as e:
        current_app.logger.error(f"Error updating username: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"Có lỗi xảy ra khi cập nhật username: {str(e)}"}), 500


@main_bp.route('/api/update-phone', methods=['POST'])
@login_required
def api_update_phone():
    """API cập nhật phone"""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "Chưa đăng nhập"}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "Dữ liệu không hợp lệ"}), 400
    
    new_phone = data.get('phone', '').strip() if data.get('phone') else ''
    
    # Validation - chỉ validate nếu có giá trị
    if new_phone:
        phone_pattern = r'^(\+84|84|0)[1-9][0-9]{8,9}$'
        if not re.match(phone_pattern, new_phone):
            return jsonify({"success": False, "message": "Số điện thoại không hợp lệ. Vui lòng nhập số điện thoại Việt Nam (10-11 số)"}), 400
        
        if len(new_phone) > 20:
            return jsonify({"success": False, "message": "Số điện thoại không được quá 20 ký tự"}), 400
    
    try:
        with current_app.db_engine.begin() as conn:
            # Update phone (có thể là None để xóa số điện thoại)
            conn.execute(text("""
                UPDATE [cine].[User] 
                SET phone = :phone
                WHERE userId = :user_id
            """), {"phone": new_phone if new_phone else None, "user_id": user_id})
        
        return jsonify({"success": True, "message": "Số điện thoại đã được cập nhật thành công"})
        
    except Exception as e:
        current_app.logger.error(f"Error updating phone: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"Có lỗi xảy ra khi cập nhật số điện thoại: {str(e)}"}), 500


@main_bp.route("/upload-avatar", methods=["POST"])
@login_required
def upload_avatar():
    """Upload avatar cho user"""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "Chưa đăng nhập"}), 401
    
    if 'avatar' not in request.files:
        return jsonify({"success": False, "message": "Không có file được chọn"}), 400
    
    file = request.files['avatar']
    if file.filename == '':
        return jsonify({"success": False, "message": "Không có file được chọn"}), 400
    
    # Validate file type
    allowed_extensions = {'png', 'jpg', 'jpeg', 'gif'}
    if '.' not in file.filename or file.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
        return jsonify({"success": False, "message": "File không hợp lệ. Chỉ chấp nhận PNG, JPG, JPEG, GIF"}), 400
    
    try:
        # Save file
        filename = secure_filename(f"{user_id}_{file.filename}")
        upload_folder = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'static', 'avatars')
        os.makedirs(upload_folder, exist_ok=True)
        file_path = os.path.join(upload_folder, filename)
        file.save(file_path)
        
        # Update database - sử dụng route /avatar/ để serve file
        avatar_url = f"/avatar/{filename}"
        with current_app.db_engine.begin() as conn:
            conn.execute(text("""
                UPDATE [cine].[User] 
                SET avatarUrl = :avatar_url
                WHERE userId = :user_id
            """), {"avatar_url": avatar_url, "user_id": user_id})
        
        session['avatar'] = avatar_url
        return jsonify({"success": True, "message": "Avatar đã được cập nhật", "avatar_url": avatar_url})
        
    except Exception as e:
        current_app.logger.error(f"Error uploading avatar: {e}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra khi upload avatar"}), 500


@main_bp.route("/avatar/<filename>")
def serve_avatar(filename):
    """Serve avatar file"""
    from flask import send_from_directory, abort
    # Đảm bảo filename an toàn (chỉ chứa tên file, không có path)
    filename = os.path.basename(filename)
    upload_folder = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'static', 'avatars')
    # Kiểm tra file có tồn tại không
    file_path = os.path.join(upload_folder, filename)
    if not os.path.exists(file_path):
        abort(404)
    return send_from_directory(upload_folder, filename)


@main_bp.route("/remove-history/<int:history_id>", methods=["POST"])
@login_required
def remove_history(history_id):
    """Xóa một item khỏi lịch sử xem"""
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("main.login"))
    
    try:
        with current_app.db_engine.begin() as conn:
            # Verify ownership
            owner = conn.execute(text("""
                SELECT userId FROM [cine].[ViewHistory] 
                WHERE historyId = :history_id
            """), {"history_id": history_id}).scalar()
            
            if owner != user_id:
                flash("Bạn không có quyền xóa item này", "error")
                return redirect(url_for("main.account_history"))
            
            # Delete history item
            conn.execute(text("""
                DELETE FROM [cine].[ViewHistory] 
                WHERE historyId = :history_id
            """), {"history_id": history_id})
        
        flash("Đã xóa khỏi lịch sử xem", "success")
        return redirect(url_for("main.account_history"))
        
    except Exception as e:
        current_app.logger.error(f"Error removing history: {e}")
        flash("Có lỗi xảy ra khi xóa", "error")
        return redirect(url_for("main.account_history"))

