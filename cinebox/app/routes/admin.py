"""
Admin routes: dashboard, movies management, users management, model management
"""

from flask import render_template, request, redirect, url_for, session, current_app, jsonify, flash
from sqlalchemy import text
from . import main_bp
from .decorators import admin_required, login_required


@main_bp.route("/admin")
@admin_required
def admin_dashboard():
    """Admin dashboard"""
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
        current_app.logger.error(f"Error getting admin dashboard stats: {e}", exc_info=True)
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
    page = request.args.get('page', 1, type=int)
    per_page = 50
    search_query = request.args.get('q', '').strip()
    
    try:
        with current_app.db_engine.connect() as conn:
            if search_query:
                # Tìm kiếm phim theo từ khóa
                total_count = conn.execute(text("""
                    SELECT COUNT(*) 
                    FROM cine.Movie 
                    WHERE title LIKE :query
                """), {"query": f"%{search_query}%"}).scalar()
                
                total_pages = (total_count + per_page - 1) // per_page
                offset = (page - 1) * per_page
                
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
                total_count = conn.execute(text("SELECT COUNT(*) FROM cine.Movie")).scalar()
                
                total_pages = (total_count + per_page - 1) // per_page
                offset = (page - 1) * per_page
                
                movies = conn.execute(text("""
                    SELECT movieId, title, releaseYear, posterUrl, viewCount, createdAt
                    FROM (
                        SELECT movieId, title, releaseYear, posterUrl, viewCount, createdAt,
                               ROW_NUMBER() OVER (ORDER BY createdAt DESC, movieId DESC) as rn
                        FROM cine.Movie
                    ) t
                    WHERE rn > :offset AND rn <= :offset + :per_page
                """), {"offset": offset, "per_page": per_page}).mappings().all()
            
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
        current_app.logger.error(f"Error loading admin movies: {e}", exc_info=True)
        flash(f"Lỗi khi tải danh sách phim: {str(e)}", "error")
        return render_template("admin_movies.html", 
                             movies=[], 
                             pagination=None,
                             search_query=search_query)


@main_bp.route("/admin/users")
@admin_required
def admin_users():
    """Quản lý người dùng với tìm kiếm và phân trang"""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    search_query = request.args.get('q', '').strip()
    
    try:
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
                    
                    total_pages = (total_count + per_page - 1) // per_page
                    offset = (page - 1) * per_page
                    
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
                    total_count = conn.execute(text("""
                        SELECT COUNT(*) 
                        FROM cine.[User] u
                        JOIN cine.Role r ON r.roleId = u.roleId
                        LEFT JOIN cine.Account a ON a.userId = u.userId
                        WHERE u.email LIKE :query OR a.username LIKE :query
                    """), {"query": f"%{search_query}%"}).scalar()
                    
                    total_pages = (total_count + per_page - 1) // per_page
                    offset = (page - 1) * per_page
                    
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
                # Lấy user mới nhất với phân trang
                total_count = conn.execute(text("SELECT COUNT(*) FROM cine.[User]")).scalar()
                
                total_pages = (total_count + per_page - 1) // per_page
                offset = (page - 1) * per_page
                
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
        current_app.logger.error(f"Error loading admin users: {e}", exc_info=True)
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
        current_app.logger.error(f"Error toggling user status: {e}", exc_info=True)
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
        current_app.logger.error(f"Error deleting user: {e}", exc_info=True)
        flash(f"❌ Lỗi khi xóa người dùng: {str(e)}", "error")
    
    return redirect(url_for("main.admin_users"))


@main_bp.route("/admin/movies/create", methods=["GET", "POST"])
@admin_required
def admin_movie_create():
    """Tạo phim mới với validation đầy đủ"""
    import re
    
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
        
        # 1. Title validation (required, max 300 chars)
        if not title:
            errors.append("Tiêu đề phim là bắt buộc")
        elif len(title) > 300:
            errors.append("Tiêu đề phim không được quá 300 ký tự")
        
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
        
        # 3. Country validation (max 80 chars)
        if country and len(country) > 80:
            errors.append("Tên quốc gia không được quá 80 ký tự")
        
        # 4. Director validation (max 200 chars)
        if director and len(director) > 200:
            errors.append("Tên đạo diễn không được quá 200 ký tự")
        
        # 5. Cast validation (max 500 chars)
        if cast and len(cast) > 500:
            errors.append("Tên diễn viên không được quá 500 ký tự")
        
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
                current_app.logger.error(f"Error loading genres: {e}")
                flash(f"Lỗi khi tải thể loại: {str(e)}", "error")
                return render_template("admin_movie_form.html", errors=errors, form_data=request.form)
        
        # Lưu vào database
        try:
            with current_app.db_engine.begin() as conn:
                # Tạo phim mới và lấy movieId
                result = conn.execute(text("""
                    INSERT INTO cine.Movie (title, releaseYear, country, overview, director, cast, 
                                          imdbRating, trailerUrl, posterUrl, backdropUrl, viewCount)
                    OUTPUT INSERTED.movieId
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
                movie_id = result.scalar()
                
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
            current_app.logger.error(f"Error creating movie: {e}", exc_info=True)
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
        current_app.logger.error(f"Error loading genres: {e}", exc_info=True)
        flash(f"Lỗi khi tải thể loại: {str(e)}", "error")
        return render_template("admin_movie_form.html", all_genres=[])


@main_bp.route("/admin/movies/<int:movie_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_movie_edit(movie_id):
    """Sửa phim với validation đầy đủ"""
    import re
    
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
        
        # Validation (giống như create)
        errors = []
        
        # 1. Title validation (required, max 300 chars)
        if not title:
            errors.append("Tiêu đề phim là bắt buộc")
        elif len(title) > 300:
            errors.append("Tiêu đề phim không được quá 300 ký tự")
        
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
        
        # 3. Country validation (max 80 chars)
        if country and len(country) > 80:
            errors.append("Tên quốc gia không được quá 80 ký tự")
        
        # 4. Director validation (max 200 chars)
        if director and len(director) > 200:
            errors.append("Tên đạo diễn không được quá 200 ký tự")
        
        # 5. Cast validation (max 500 chars)
        if cast and len(cast) > 500:
            errors.append("Tên diễn viên không được quá 500 ký tự")
        
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
                    # Lấy genres đã chọn từ form (ưu tiên genres từ form khi có lỗi)
                    selected_genre_ids = [int(gid) for gid in selected_genres if gid]
                    
                return render_template("admin_movie_form.html", 
                                     all_genres=all_genres,
                                     errors=errors,
                                     form_data=request.form,
                                     current_genre_ids=selected_genre_ids,
                                     is_edit=True,
                                     movie_id=movie_id)
            except Exception as e:
                current_app.logger.error(f"Error loading genres: {e}")
                flash(f"Lỗi khi tải thể loại: {str(e)}", "error")
                return render_template("admin_movie_form.html", 
                                     errors=errors, 
                                     form_data=request.form, 
                                     all_genres=[],
                                     current_genre_ids=[],
                                     is_edit=True, 
                                     movie_id=movie_id)
        
        # Lưu vào database
        try:
            with current_app.db_engine.begin() as conn:
                # Cập nhật thông tin phim
                conn.execute(text("""
                    UPDATE cine.Movie
                    SET title = :title, releaseYear = :year, country = :country, 
                        overview = :overview, director = :director, cast = :cast,
                        imdbRating = :rating, trailerUrl = :trailer, 
                        posterUrl = :poster, backdropUrl = :backdrop, viewCount = :views
                    WHERE movieId = :id
                """), {
                    "id": movie_id,
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
                
                # Xóa thể loại cũ
                conn.execute(text("DELETE FROM cine.MovieGenre WHERE movieId = :movieId"), {"movieId": movie_id})
                
                # Thêm thể loại mới
                for genre_id in selected_genres:
                    if genre_id:
                        conn.execute(text("""
                            INSERT INTO cine.MovieGenre (movieId, genreId) 
                            VALUES (:movieId, :genreId)
                        """), {"movieId": movie_id, "genreId": int(genre_id)})
                
                flash("✅ Cập nhật phim thành công!", "success")
                return redirect(url_for("main.admin_movies"))
    
        except Exception as e:
            current_app.logger.error(f"Error updating movie: {e}", exc_info=True)
            flash(f"❌ Lỗi khi cập nhật phim: {str(e)}", "error")
            try:
                with current_app.db_engine.connect() as conn:
                    all_genres = conn.execute(text("SELECT genreId, name FROM cine.Genre ORDER BY name")).mappings().all()
                    selected_genre_ids = [int(gid) for gid in selected_genres if gid]
                    return render_template("admin_movie_form.html", 
                                         all_genres=all_genres,
                                         form_data=request.form,
                                         current_genre_ids=selected_genre_ids,
                                         is_edit=True,
                                         movie_id=movie_id)
            except:
                return render_template("admin_movie_form.html", 
                                     form_data=request.form, 
                                     all_genres=[],
                                     current_genre_ids=[],
                                     is_edit=True, 
                                     movie_id=movie_id)
    
    # GET request - hiển thị form sửa
    try:
        with current_app.db_engine.connect() as conn:
            # Lấy đầy đủ thông tin phim
            movie = conn.execute(text("""
                SELECT movieId, title, releaseYear, country, overview, director, cast,
                       imdbRating, trailerUrl, posterUrl, backdropUrl, viewCount
                FROM cine.Movie
                WHERE movieId = :id
            """), {"id": movie_id}).mappings().first()
            
            if not movie:
                flash("Không tìm thấy phim.", "error")
                return redirect(url_for("main.admin_movies"))
            
            # Lấy genres hiện tại của phim
            current_genres = conn.execute(text("""
                SELECT genreId FROM cine.MovieGenre WHERE movieId = :movie_id
            """), {"movie_id": movie_id}).fetchall()
            current_genre_ids = [g[0] for g in current_genres]
            
            # Lấy tất cả genres
            all_genres = conn.execute(text("SELECT genreId, name FROM cine.Genre ORDER BY name")).mappings().all()
            
            # Tạo form_data từ movie để template hiển thị
            from werkzeug.datastructures import ImmutableMultiDict
            form_data = ImmutableMultiDict({
                'title': movie.get('title', ''),
                'release_year': str(movie.get('releaseYear', '')) if movie.get('releaseYear') else '',
                'country': movie.get('country', '') or '',
                'overview': movie.get('overview', '') or '',
                'director': movie.get('director', '') or '',
                'cast': movie.get('cast', '') or '',
                'imdb_rating': str(movie.get('imdbRating', '')) if movie.get('imdbRating') else '',
                'trailer_url': movie.get('trailerUrl', '') or '',
                'poster_url': movie.get('posterUrl', '') or '',
                'backdrop_url': movie.get('backdropUrl', '') or '',
                'view_count': str(movie.get('viewCount', 0)) if movie.get('viewCount') else '0',
                'genres': [str(gid) for gid in current_genre_ids]
            })
            
            return render_template("admin_movie_form.html", 
                                 movie=movie, 
                                 form_data=form_data,
                                 all_genres=all_genres,
                                 current_genre_ids=current_genre_ids,
                                 is_edit=True,
                                 movie_id=movie_id)
    except Exception as e:
        current_app.logger.error(f"Error loading movie: {e}", exc_info=True)
        flash(f"Lỗi khi tải thông tin phim: {str(e)}", "error")
        return redirect(url_for("main.admin_movies"))


@main_bp.route("/admin/movies/<int:movie_id>/delete", methods=["POST"])
@admin_required
def admin_movie_delete(movie_id):
    """Xóa phim"""
    try:
        with current_app.db_engine.begin() as conn:
            # Lấy thông tin phim trước khi xóa
            movie = conn.execute(text("""
                SELECT title FROM cine.Movie WHERE movieId = :id
            """), {"id": movie_id}).mappings().first()
            
            if not movie:
                flash("Không tìm thấy phim.", "error")
                return redirect(url_for("main.admin_movies"))
            
            # Xóa các thể loại liên quan
            conn.execute(text("DELETE FROM cine.MovieGenre WHERE movieId = :id"), {"id": movie_id})
            
            # Xóa phim
            conn.execute(text("DELETE FROM cine.Movie WHERE movieId = :id"), {"id": movie_id})
            
            flash(f"✅ Đã xóa phim '{movie.title}' thành công!", "success")
    except Exception as e:
        current_app.logger.error(f"Error deleting movie: {e}", exc_info=True)
        flash(f"❌ Lỗi khi xóa phim: {str(e)}", "error")
    
    return redirect(url_for("main.admin_movies"))


@main_bp.route("/admin/model")
@admin_required
def admin_model():
    """Admin model management page"""
    return render_template("admin_model.html")


@main_bp.route("/api/retrain_cf_model", methods=["POST", "GET"])
@admin_required
def retrain_cf_model():
    """Retrain Collaborative Filtering model - requires admin authentication"""
    return _retrain_cf_model_internal()


@main_bp.route("/api/retrain_cf_model_internal", methods=["POST"])
def retrain_cf_model_internal():
    """Internal endpoint for retraining CF model (called by background worker)"""
    import os
    from flask import request as flask_request
    
    # Verify internal secret
    secret = os.environ.get('INTERNAL_RETRAIN_SECRET', 'internal-retrain-secret-key-change-in-production')
    provided_secret = None
    
    # Check both JSON body and header
    if flask_request.is_json:
        data = flask_request.get_json()
        provided_secret = data.get('secret') if data else None
    
    header_secret = flask_request.headers.get('X-Internal-Secret')
    provided_secret = provided_secret or header_secret
    
    if provided_secret != secret:
        current_app.logger.warning("Unauthorized retrain_cf_model_internal request")
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    try:
        return _retrain_cf_model_internal()
    except Exception as e:
        current_app.logger.error(f"Error in retrain_cf_model_internal: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


def _retrain_cf_model_internal():
    """Internal function to retrain Collaborative Filtering model"""
    import subprocess
    import sys
    import os
    from datetime import datetime
    
    try:
        current_app.logger.info("Starting CF model retrain...")
        
        # Get paths - same logic as routes_old.py
        routes_dir = os.path.dirname(os.path.abspath(__file__))
        app_dir = os.path.dirname(routes_dir)
        cinebox_dir = os.path.dirname(app_dir)
        
        # Script path - same as routes_old.py
        script_path = os.path.join(cinebox_dir, 'model_collaborative', 'train_model.py')
        script_path = os.path.abspath(script_path)
        
        current_app.logger.info(f"Script path: {script_path}")
        current_app.logger.info(f"Script exists: {os.path.exists(script_path)}")
        
        if not os.path.exists(script_path):
            current_app.logger.error(f"Script không tồn tại: {script_path}")
            return jsonify({
                "success": False, 
                "message": f"Script không tồn tại: {script_path}"
            }), 500
        
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
                from .common import enhanced_cf_recommender
                if enhanced_cf_recommender:
                    current_app.logger.info("Reloading CF model...")
                    enhanced_cf_recommender.reload_model()
                    current_app.logger.info("CF model reloaded successfully")
                    return jsonify({
                        "success": True,
                        "message": "Model CF đã được retrain thành công",
                        "output": result.stdout[-1000:] if result.stdout else ""  # Chỉ trả về 1000 ký tự cuối
                    })
                else:
                    # Nếu chưa có, khởi tạo lại
                    current_app.logger.info("Initializing recommenders...")
                    from .common import init_recommenders
                    init_recommenders()
                    current_app.logger.info("Recommenders initialized")
                    return jsonify({
                        "success": True,
                        "message": "Model CF đã được retrain thành công",
                        "output": result.stdout[-1000:] if result.stdout else ""
                    })
            except Exception as reload_error:
                current_app.logger.error(f"Error reloading model: {reload_error}", exc_info=True)
                # Vẫn return success vì model đã được train, chỉ là reload failed
                return jsonify({
                    "success": True,
                    "message": "Model CF đã được retrain thành công, nhưng reload model thất bại. Vui lòng restart server.",
                    "output": result.stdout[-1000:] if result.stdout else "",
                    "warning": f"Reload error: {str(reload_error)}"
                })
        else:
            error_msg = result.stderr if result.stderr else "Unknown error"
            current_app.logger.error(f"Retrain failed with code {result.returncode}: {error_msg}")
            return jsonify({
                "success": False,
                "message": f"Lỗi khi retrain model (code: {result.returncode})",
                "output": result.stdout[-1000:] if result.stdout else "",
                "error": error_msg[-1000:] if error_msg else ""
            }), 500
            
    except subprocess.TimeoutExpired:
        current_app.logger.error("Retrain script timeout (exceeded 5 minutes)")
        return jsonify({
            "success": False,
            "message": "Retrain timeout - quá trình retrain mất quá nhiều thời gian"
        }), 500
    except Exception as e:
        current_app.logger.error(f"Error in _retrain_cf_model_internal: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Lỗi khi retrain model: {str(e)}"
        }), 500

