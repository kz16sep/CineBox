"""
Admin routes: dashboard, movies management, users management, model management
"""

from flask import render_template, request, redirect, url_for, session, current_app, jsonify, flash
from sqlalchemy import text
from . import main_bp
from .decorators import admin_required, login_required
import threading
import re
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import MinMaxScaler


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


@main_bp.route("/api/similarity-progress/<int:movie_id>")
@admin_required
def api_similarity_progress(movie_id):
    """API endpoint để lấy progress của similarity calculation"""
    from .common import similarity_progress
    from flask import jsonify
    
    progress = similarity_progress.get(movie_id, {
        'status': 'not_found',
        'progress': 0,
        'message': 'Không tìm thấy thông tin tính similarity'
    })
    
    return jsonify(progress)


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
            
        # Lấy new_movie_id từ query parameter nếu có
        new_movie_id = request.args.get('new_movie_id', type=int)
        
        return render_template("admin_movies.html", 
                             movies=movies, 
                             pagination=pagination,
                             search_query=search_query,
                             new_movie_id=new_movie_id)
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


def _calculate_movie_similarity_with_context(app, movie_id: int):
    """
    Wrapper function để chạy similarity calculation với Flask app context
    """
    with app.app_context():
        _calculate_movie_similarity(movie_id)


def _calculate_movie_similarity(movie_id: int):
    """
    Tính similarity cho phim mới với các phim khác trong database
    Chạy trong background thread để không block request
    """
    from .common import similarity_progress
    
    try:
        # Khởi tạo progress
        similarity_progress[movie_id] = {
            'status': 'running',
            'progress': 0,
            'message': 'Đang bắt đầu tính similarity...'
        }
        current_app.logger.info(f"Starting similarity calculation for movie {movie_id}")
        
        with current_app.db_engine.connect() as conn:
            # Lấy thông tin phim mới
            new_movie = conn.execute(text("""
                SELECT m.movieId, m.title, m.releaseYear, m.overview,
                       AVG(CAST(r.value AS FLOAT)) AS avgRating,
                       COUNT(r.value) AS ratingCount
                FROM cine.Movie m
                LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                WHERE m.movieId = :movie_id
                GROUP BY m.movieId, m.title, m.releaseYear, m.overview
            """), {"movie_id": movie_id}).mappings().first()
            
            if not new_movie:
                current_app.logger.error(f"Movie {movie_id} not found")
                similarity_progress[movie_id] = {
                    'status': 'error',
                    'progress': 0,
                    'message': 'Không tìm thấy phim'
                }
                return
            
            # Cập nhật progress: 10%
            similarity_progress[movie_id] = {
                'status': 'running',
                'progress': 10,
                'message': 'Đang lấy thông tin phim...'
            }
            
            # Lấy genres của phim mới
            new_genres = conn.execute(text("""
                SELECT g.name 
                FROM cine.Genre g
                JOIN cine.MovieGenre mg ON g.genreId = mg.genreId
                WHERE mg.movieId = :movie_id
            """), {"movie_id": movie_id}).fetchall()
            new_genres_list = [g[0] for g in new_genres]
            new_genres_text = " ".join(new_genres_list)
            
            # Format title với year (nếu có)
            title = new_movie["title"]
            year = new_movie.get("releaseYear")
            if year and f"({year})" not in title:
                title = f"{title} ({year})"
            
            # Cập nhật progress: 20%
            similarity_progress[movie_id] = {
                'status': 'running',
                'progress': 20,
                'message': 'Đang tìm phim để so sánh...'
            }
            
            # Lấy danh sách phim khác để so sánh (ưu tiên phim có genres chung)
            # TOP 1000 phim để tối ưu tốc độ (giảm từ 3000 xuống 1000)
            # Nếu phim mới có genres, chỉ lấy phim có ít nhất 1 genre chung
            if new_genres_list:
                # Có genres: chỉ lấy phim có genres chung
                all_movies = conn.execute(text("""
                    SELECT TOP 1000
                        m.movieId, m.title, m.releaseYear, m.overview,
                        AVG(CAST(r.value AS FLOAT)) AS avgRating,
                        COUNT(r.value) AS ratingCount,
                        (SELECT COUNT(*) 
                         FROM cine.MovieGenre mg1 
                         JOIN cine.Genre g1 ON mg1.genreId = g1.genreId
                         WHERE mg1.movieId = m.movieId 
                         AND g1.name IN (SELECT g2.name 
                                          FROM cine.Genre g2
                                          JOIN cine.MovieGenre mg2 ON g2.genreId = mg2.genreId
                                          WHERE mg2.movieId = :movie_id)
                        ) AS common_genres_count
                    FROM cine.Movie m
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    WHERE m.movieId != :movie_id
                    AND EXISTS (
                        SELECT 1 
                        FROM cine.MovieGenre mg1
                        JOIN cine.Genre g1 ON mg1.genreId = g1.genreId
                        JOIN cine.MovieGenre mg2 ON g1.genreId = mg2.genreId
                        WHERE mg1.movieId = m.movieId 
                        AND mg2.movieId = :movie_id
                    )
                    GROUP BY m.movieId, m.title, m.releaseYear, m.overview
                    ORDER BY common_genres_count DESC, avgRating DESC, ratingCount DESC
                """), {"movie_id": movie_id}).mappings().all()
            else:
                # Không có genres: lấy phim phổ biến
                all_movies = conn.execute(text("""
                    SELECT TOP 1000
                        m.movieId, m.title, m.releaseYear, m.overview,
                        AVG(CAST(r.value AS FLOAT)) AS avgRating,
                        COUNT(r.value) AS ratingCount,
                        0 AS common_genres_count
                    FROM cine.Movie m
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    WHERE m.movieId != :movie_id
                    GROUP BY m.movieId, m.title, m.releaseYear, m.overview
                    ORDER BY avgRating DESC, ratingCount DESC
                """), {"movie_id": movie_id}).mappings().all()
            
            if not all_movies:
                current_app.logger.warning(f"No movies found for similarity calculation")
                return
            
            # Batch query genres cho tất cả phim cùng lúc (tối ưu tốc độ)
            # Chia nhỏ thành batch để tránh vượt quá giới hạn parameters của SQL Server (2100)
            movie_ids = [m["movieId"] for m in all_movies]
            if not movie_ids:
                current_app.logger.warning(f"No movies found for similarity calculation")
                return
            
            # Query genres cho tất cả phim, chia thành batch 1000 phim mỗi lần
            BATCH_SIZE = 1000
            genres_results = []
            for i in range(0, len(movie_ids), BATCH_SIZE):
                batch_ids = movie_ids[i:i + BATCH_SIZE]
                placeholders = ','.join([f':id{j}' for j in range(len(batch_ids))])
                params_genres = {f'id{j}': int(mid) for j, mid in enumerate(batch_ids)}
                batch_results = conn.execute(text(f"""
                    SELECT mg.movieId, g.name
                    FROM cine.MovieGenre mg
                    JOIN cine.Genre g ON mg.genreId = g.genreId
                    WHERE mg.movieId IN ({placeholders})
                """), params_genres).fetchall()
                genres_results.extend(batch_results)
            
            # Cập nhật progress: 40%
            similarity_progress[movie_id] = {
                'status': 'running',
                'progress': 40,
                'message': 'Đang chuẩn bị dữ liệu...'
            }
            
            # Group genres by movieId
            genres_by_movie = {}
            for movie_id_item, genre_name in genres_results:
                if movie_id_item not in genres_by_movie:
                    genres_by_movie[movie_id_item] = []
                genres_by_movie[movie_id_item].append(genre_name)
            
            # Chuẩn bị dữ liệu cho tính toán
            movies_data = []
            for movie in all_movies:
                movie_id = movie["movieId"]
                genres_list = genres_by_movie.get(movie_id, [])
                genres_text = " ".join(genres_list)
                
                # Format title với year
                movie_title = movie["title"]
                movie_year = movie.get("releaseYear")
                if movie_year and f"({movie_year})" not in movie_title:
                    movie_title = f"{movie_title} ({movie_year})"
                
                movies_data.append({
                    "movieId": movie_id,
                    "title": movie_title,
                    "genres": genres_text,
                    "year": movie_year or 2000,
                    "avgRating": float(movie.get("avgRating") or 0),
                    "ratingCount": int(movie.get("ratingCount") or 0)
                })
            
            # Cập nhật progress: 50%
            similarity_progress[movie_id] = {
                'status': 'running',
                'progress': 50,
                'message': 'Đang tính similarity...'
            }
            
            # Tính similarity
            # Giảm max_features để tăng tốc độ tính toán (1000 -> 500)
            # 1. Genres similarity (60%)
            all_genres = [new_genres_text] + [m["genres"] for m in movies_data]
            genres_vectorizer = TfidfVectorizer(max_features=500, min_df=1)  # Giảm từ 1000 xuống 500
            genres_matrix = genres_vectorizer.fit_transform(all_genres)
            genres_sim = cosine_similarity(genres_matrix[0:1], genres_matrix[1:])[0]
            
            # 2. Title similarity (20%)
            all_titles = [title] + [m["title"] for m in movies_data]
            title_vectorizer = TfidfVectorizer(max_features=500, min_df=1)  # Giảm từ 1000 xuống 500
            title_matrix = title_vectorizer.fit_transform(all_titles)
            title_sim = cosine_similarity(title_matrix[0:1], title_matrix[1:])[0]
            
            # 3. Year similarity (7%)
            years = np.array([[year or 2000]] + [[m["year"]] for m in movies_data])
            year_scaler = MinMaxScaler()
            years_scaled = year_scaler.fit_transform(years)
            year_sim = 1 - np.abs(years_scaled[0] - years_scaled[1:]).flatten()
            year_sim = np.clip(year_sim, 0, 1)
            
            # 4. Popularity similarity (7%) - dựa trên ratingCount
            popularity = np.array([[np.log1p(0)]] + [[np.log1p(m["ratingCount"])] for m in movies_data])
            pop_scaler = MinMaxScaler()
            pop_scaled = pop_scaler.fit_transform(popularity)
            pop_sim = 1 - np.abs(pop_scaled[0] - pop_scaled[1:]).flatten()
            pop_sim = np.clip(pop_sim, 0, 1)
            
            # 5. Rating similarity (6%) - dựa trên avgRating
            ratings = np.array([[new_movie.get("avgRating") or 0]] + [[m["avgRating"]] for m in movies_data])
            rating_scaler = MinMaxScaler()
            rating_scaled = rating_scaler.fit_transform(ratings)
            rating_sim = 1 - np.abs(rating_scaled[0] - rating_scaled[1:]).flatten()
            rating_sim = np.clip(rating_sim, 0, 1)
            
            # Cập nhật progress: 70%
            similarity_progress[movie_id] = {
                'status': 'running',
                'progress': 70,
                'message': 'Đang kết hợp similarity scores...'
            }
            
            # Combine với weights: Genres (60%), Title (20%), Year (7%), Popularity (7%), Rating (6%)
            # Cùng weights với improved_train.py để đảm bảo tính nhất quán
            final_similarities = (
                genres_sim * 0.60 +      # Genres: 60%
                title_sim * 0.20 +       # Title: 20%
                year_sim * 0.07 +        # Year: 7%
                pop_sim * 0.07 +         # Popularity: 7%
                rating_sim * 0.06        # Rating: 6%
            )
            
            # Lấy top 20 phim tương tự nhất
            top_n = 20
            top_indices = np.argsort(final_similarities)[::-1][:top_n]
            
            # Lưu vào database (chỉ lưu top 20 phim tương tự)
            similarities_to_save = []
            for i in top_indices:
                sim_score = final_similarities[i]
                if sim_score > 0.05:  # Threshold
                    other_movie_id = movies_data[i]["movieId"]
                    # Lưu cả 2 chiều (movieId1, movieId2) và (movieId2, movieId1)
                    similarities_to_save.append({
                        "movieId1": movie_id,
                        "movieId2": other_movie_id,
                        "similarity": float(sim_score)
                    })
                    similarities_to_save.append({
                        "movieId1": other_movie_id,
                        "movieId2": movie_id,
                        "similarity": float(sim_score)
                    })
            
            # Cập nhật progress: 80%
            similarity_progress[movie_id] = {
                'status': 'running',
                'progress': 80,
                'message': 'Đang lưu vào database...'
            }
            
            # Batch insert vào database (tối ưu bằng bulk insert với VALUES nhiều dòng)
            if similarities_to_save:
                # Tối ưu: dùng bulk MERGE với VALUES nhiều dòng thay vì từng dòng
                batch_size = 100  # Batch size cho bulk insert
                inserted_count = 0
                
                for i in range(0, len(similarities_to_save), batch_size):
                    batch = similarities_to_save[i:i+batch_size]
                    
                    # Tạo VALUES clause cho bulk insert
                    values_clauses = []
                    params = {}
                    for idx, sim in enumerate(batch):
                        param_prefix = f"m{idx}"
                        values_clauses.append(f"(:{param_prefix}_id1, :{param_prefix}_id2, :{param_prefix}_sim)")
                        params[f"{param_prefix}_id1"] = sim["movieId1"]
                        params[f"{param_prefix}_id2"] = sim["movieId2"]
                        params[f"{param_prefix}_sim"] = sim["similarity"]
                    
                    # Bulk MERGE với VALUES nhiều dòng
                    try:
                        values_str = ", ".join(values_clauses)
                        conn.execute(text(f"""
                            MERGE cine.MovieSimilarity AS target
                            USING (
                                SELECT movieId1, movieId2, similarity
                                FROM (VALUES {values_str}) AS v(movieId1, movieId2, similarity)
                            ) AS source
                            ON target.movieId1 = source.movieId1 AND target.movieId2 = source.movieId2
                            WHEN MATCHED THEN
                                UPDATE SET similarity = source.similarity
                            WHEN NOT MATCHED THEN
                                INSERT (movieId1, movieId2, similarity)
                                VALUES (source.movieId1, source.movieId2, source.similarity);
                        """), params)
                        inserted_count += len(batch)
                    except Exception as e:
                        # Fallback: insert từng dòng nếu bulk insert fail
                        current_app.logger.warning(f"Bulk insert failed, falling back to individual inserts: {e}")
                        for sim in batch:
                            try:
                                conn.execute(text("""
                                    MERGE cine.MovieSimilarity AS target
                                    USING (SELECT :movieId1 AS movieId1, :movieId2 AS movieId2, :similarity AS similarity) AS source
                                    ON target.movieId1 = source.movieId1 AND target.movieId2 = source.movieId2
                                    WHEN MATCHED THEN
                                        UPDATE SET similarity = source.similarity
                                    WHEN NOT MATCHED THEN
                                        INSERT (movieId1, movieId2, similarity)
                                        VALUES (source.movieId1, source.movieId2, source.similarity);
                                """), {
                                    "movieId1": sim["movieId1"],
                                    "movieId2": sim["movieId2"],
                                    "similarity": sim["similarity"]
                                })
                                inserted_count += 1
                            except Exception as e2:
                                current_app.logger.error(f"Error saving similarity {sim}: {e2}")
                    
                    # Commit sau mỗi batch
                    conn.commit()
                
                # Cập nhật progress: 100% - Hoàn thành
                similarity_progress[movie_id] = {
                    'status': 'completed',
                    'progress': 100,
                    'message': f'✅ Đã tính similarity cho {len(similarities_to_save)//2} phim liên quan'
                }
                current_app.logger.info(f"✅ Calculated and saved {len(similarities_to_save)} similarity pairs (top {top_n} movies) for movie {movie_id}")
            else:
                similarity_progress[movie_id] = {
                    'status': 'completed',
                    'progress': 100,
                    'message': 'Không tìm thấy phim tương tự (similarity < 0.05)'
                }
                current_app.logger.warning(f"No similarities above threshold for movie {movie_id}")
                
    except Exception as e:
        similarity_progress[movie_id] = {
            'status': 'error',
            'progress': 0,
            'message': f'❌ Lỗi: {str(e)}'
        }
        current_app.logger.error(f"Error calculating similarity for movie {movie_id}: {e}", exc_info=True)


@main_bp.route("/admin/movies/create", methods=["GET", "POST"])
@admin_required
def admin_movie_create():
    """Tạo phim mới với validation đầy đủ"""
    import re
    
    if request.method == "POST":
        # Lấy dữ liệu từ form (bỏ imdb_rating và view_count)
        title = request.form.get("title", "").strip()
        release_year = request.form.get("release_year", "").strip()
        country = request.form.get("country", "").strip()
        overview = request.form.get("overview", "").strip()
        director = request.form.get("director", "").strip()
        cast = request.form.get("cast", "").strip()
        trailer_url = request.form.get("trailer_url", "").strip()
        poster_url = request.form.get("poster_url", "").strip()
        backdrop_url = request.form.get("backdrop_url", "").strip()
        movie_url = request.form.get("movie_url", "").strip()
        selected_genres = request.form.getlist("genres")
        
        # Validation
        errors = []
        
        # 1. Title validation (required, max 300 chars, hỗ trợ tiếng Việt)
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
        
        # 3. Country validation (max 80 chars, hỗ trợ tiếng Việt)
        if country and len(country) > 80:
            errors.append("Tên quốc gia không được quá 80 ký tự")
        
        # 4. Director validation (max 200 chars, hỗ trợ tiếng Việt)
        if director and len(director) > 200:
            errors.append("Tên đạo diễn không được quá 200 ký tự")
        
        # 5. Cast validation (max 500 chars, hỗ trợ tiếng Việt)
        if cast and len(cast) > 500:
            errors.append("Tên diễn viên không được quá 500 ký tự")
        
        # 6. URL validation
        url_pattern = r'^https?://[^\s/$.?#].[^\s]*$'
        
        if trailer_url and not re.match(url_pattern, trailer_url):
            errors.append("Trailer URL phải là địa chỉ web hợp lệ (bắt đầu bằng http:// hoặc https://)")
        
        if poster_url and not re.match(url_pattern, poster_url):
            errors.append("Poster URL phải là địa chỉ web hợp lệ (bắt đầu bằng http:// hoặc https://)")
        
        if backdrop_url and not re.match(url_pattern, backdrop_url):
            errors.append("Backdrop URL phải là địa chỉ web hợp lệ (bắt đầu bằng http:// hoặc https://)")
        
        if movie_url and not re.match(url_pattern, movie_url):
            errors.append("Movie URL phải là địa chỉ web hợp lệ (bắt đầu bằng http:// hoặc https://)")
        
        # 7. Genres validation
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
                # Generate movieId tự động (vì không phải IDENTITY)
                max_movie_id = conn.execute(text("SELECT ISNULL(MAX(movieId), 0) FROM cine.Movie")).scalar()
                movie_id = max_movie_id + 1
                
                # Tạo phim mới với movieId đã generate
                # Bỏ imdbRating (NULL) và viewCount (mặc định = 0)
                conn.execute(text("""
                    INSERT INTO cine.Movie (movieId, title, releaseYear, country, overview, director, cast, 
                                          trailerUrl, posterUrl, backdropUrl, movieUrl, viewCount, createdAt)
                    VALUES (:movieId, :title, :year, :country, :overview, :director, :cast, 
                            :trailer, :poster, :backdrop, :movieUrl, 0, GETDATE())
                """), {
                    "movieId": movie_id,
                    "title": title,
                    "year": year,
                    "country": country if country else None,
                    "overview": overview if overview else None,
                    "director": director if director else None,
                    "cast": cast if cast else None,
                    "trailer": trailer_url if trailer_url else None,
                    "poster": poster_url if poster_url else None,
                    "backdrop": backdrop_url if backdrop_url else None,
                    "movieUrl": movie_url if movie_url else None
                })
                
                # Thêm thể loại cho phim
                for genre_id in selected_genres:
                    if genre_id:
                        conn.execute(text("""
                            INSERT INTO cine.MovieGenre (movieId, genreId) 
                            VALUES (:movieId, :genreId)
                        """), {"movieId": movie_id, "genreId": int(genre_id)})
                
                # Clear cache để phim mới hiển thị ngay (giữ lại 'ttl')
                from .common import latest_movies_cache, carousel_movies_cache
                latest_movies_cache['data'] = None
                latest_movies_cache['key'] = None
                latest_movies_cache['timestamp'] = None
                carousel_movies_cache['data'] = None
                carousel_movies_cache['timestamp'] = None
                
                # Tính similarity trong background thread (không block response)
                # Truyền app context vào thread để tránh lỗi "Working outside of application context"
                app = current_app._get_current_object()
                similarity_thread = threading.Thread(
                    target=_calculate_movie_similarity_with_context,
                    args=(app, movie_id),
                    daemon=True
                )
                similarity_thread.start()
                current_app.logger.info(f"Started background similarity calculation for movie {movie_id}")
                
                # Thông báo thành công và redirect về trang quản lý phim với movie_id để hiển thị progress
                flash(f"✅ Thêm phim thành công (ID: {movie_id})", "success")
                return redirect(url_for("main.admin_movies", new_movie_id=movie_id))
    
        except Exception as e:
            current_app.logger.error(f"Error creating movie: {e}", exc_info=True)
            # Thông báo lỗi và render lại form với dữ liệu đã nhập
            error_message = f"❌ Lỗi khi thêm phim vào database: {str(e)}"
            flash(error_message, "error")
            try:
                with current_app.db_engine.connect() as conn:
                    all_genres = conn.execute(text("SELECT genreId, name FROM cine.Genre ORDER BY name")).mappings().all()
                    return render_template("admin_movie_form.html", 
                                         all_genres=all_genres,
                                         errors=[error_message],
                                         form_data=request.form)
            except Exception as ex:
                current_app.logger.error(f"Error loading genres after movie creation error: {ex}")
                return render_template("admin_movie_form.html", 
                                     errors=[error_message],
                                     form_data=request.form,
                                     all_genres=[])
    
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

