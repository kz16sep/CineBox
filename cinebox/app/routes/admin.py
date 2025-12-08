"""
Admin routes: dashboard, movies management, users management, model management
"""

from flask import render_template, request, redirect, url_for, session, current_app, jsonify, flash
from sqlalchemy import text
from . import main_bp
from .decorators import admin_required, login_required
import threading
import queue
import re
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import MinMaxScaler
from typing import Optional

SIMILARITY_CHUNK_SIZE = 800
similarity_job_queue = queue.Queue()
similarity_worker_thread = None


def enqueue_similarity_job(movie_id: int, movie_title: Optional[str] = None):
    """
    Đưa job tính similarity vào hàng đợi nền và cập nhật progress ở trạng thái chờ
    """
    from .common import similarity_progress

    similarity_progress[movie_id] = {
        'status': 'queued',
        'progress': 0,
        'message': '⏳ Đang xếp hàng để tính similarity...',
        'movieTitle': movie_title
    }
    similarity_job_queue.put(movie_id)
    _ensure_similarity_worker()


def _ensure_similarity_worker(app=None):
    """
    Khởi tạo background worker nếu chưa chạy
    """
    global similarity_worker_thread
    if similarity_worker_thread and similarity_worker_thread.is_alive():
        return

    app = app or current_app._get_current_object()
    similarity_worker_thread = threading.Thread(
        target=_similarity_worker_loop,
        args=(app,),
        daemon=True
    )
    similarity_worker_thread.start()


def _similarity_worker_loop(app):
    """
    Worker chạy nền, liên tục lấy movieId từ queue và tính similarity
    """
    from .common import similarity_progress

    while True:
        movie_id = similarity_job_queue.get()
        try:
            with app.app_context():
                current_app.logger.info(f"[SimilarityWorker] Starting job for movie {movie_id}")
                _calculate_movie_similarity(movie_id)
                current_app.logger.info(f"[SimilarityWorker] Finished job for movie {movie_id}")
        except Exception as worker_error:
            current_app.logger.error(f"[SimilarityWorker] Error for movie {movie_id}: {worker_error}", exc_info=True)
            similarity_progress[movie_id] = {
                'status': 'error',
                'progress': 0,
                'message': f'❌ Lỗi worker: {worker_error}',
                'movieTitle': similarity_progress.get(movie_id, {}).get('movieTitle')
            }
        finally:
            similarity_job_queue.task_done()


@main_bp.record_once
def _register_similarity_worker(state):
    app = state.app
    with app.app_context():
        _ensure_similarity_worker(app)


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
        'message': 'Không tìm thấy thông tin tính similarity',
        'movieTitle': None
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
    status_filter = (request.args.get('status', 'all') or 'all').strip().lower()
    if status_filter not in {'all', 'active', 'inactive'}:
        status_filter = 'all'
    status_condition = " AND u.status = :status_filter" if status_filter != 'all' else ""
    
    try:
        with current_app.db_engine.connect() as conn:
            if search_query:
                # Kiểm tra xem search_query có phải là số (ID) không
                is_numeric = search_query.isdigit()
                
                if is_numeric:
                    # Tìm kiếm theo ID (exact match)
                    user_id = int(search_query)
                    count_params = {"user_id": user_id}
                    if status_filter != 'all':
                        count_params["status_filter"] = status_filter
                    total_count = conn.execute(text(f"""
                        SELECT COUNT(*) 
                        FROM cine.[User] u
                        JOIN cine.Role r ON r.roleId = u.roleId
                        WHERE u.userId = :user_id
                        {status_condition}
                    """), count_params).scalar()
                    
                    total_pages = (total_count + per_page - 1) // per_page
                    offset = (page - 1) * per_page
                    
                    query_params = {
                        "user_id": user_id,
                        "offset": offset,
                        "per_page": per_page
                    }
                    if status_filter != 'all':
                        query_params["status_filter"] = status_filter
                    users = conn.execute(text(f"""
                        SELECT u.userId, u.email, u.status, u.createdAt, u.lastLoginAt, r.roleName,
                               a.username
                        FROM (
                            SELECT u.userId, u.email, u.status, u.createdAt, u.lastLoginAt, u.roleId,
                                   ROW_NUMBER() OVER (ORDER BY u.createdAt DESC) as rn
                            FROM cine.[User] u
                            WHERE u.userId = :user_id
                            {status_condition}
                        ) t
                        JOIN cine.Role r ON r.roleId = t.roleId
                        LEFT JOIN cine.Account a ON a.userId = t.userId
                        WHERE t.rn > :offset AND t.rn <= :offset + :per_page
                    """), query_params).mappings().all()
                else:
                    # Tìm kiếm theo email hoặc username
                    count_params = {"query": f"%{search_query}%"}
                    if status_filter != 'all':
                        count_params["status_filter"] = status_filter
                    total_count = conn.execute(text(f"""
                        SELECT COUNT(*) 
                        FROM cine.[User] u
                        JOIN cine.Role r ON r.roleId = u.roleId
                        LEFT JOIN cine.Account a ON a.userId = u.userId
                        WHERE (u.email LIKE :query OR a.username LIKE :query)
                        {status_condition}
                    """), count_params).scalar()
                    
                    total_pages = (total_count + per_page - 1) // per_page
                    offset = (page - 1) * per_page
                    
                    query_params = {
                        "query": f"%{search_query}%",
                        "exact_query": f"{search_query}%",
                        "start_query": f"{search_query}%",
                        "offset": offset,
                        "per_page": per_page
                    }
                    if status_filter != 'all':
                        query_params["status_filter"] = status_filter
                    users = conn.execute(text(f"""
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
                            WHERE (u.email LIKE :query OR a.username LIKE :query)
                            {status_condition}
                        ) t
                        JOIN cine.Role r ON r.roleId = t.roleId
                        LEFT JOIN cine.Account a ON a.userId = t.userId
                        WHERE t.rn > :offset AND t.rn <= :offset + :per_page
                    """), query_params).mappings().all()
            else:
                # Lấy user mới nhất với phân trang
                count_params = {}
                if status_filter != 'all':
                    count_params["status_filter"] = status_filter
                total_count = conn.execute(text(f"""
                    SELECT COUNT(*) 
                    FROM cine.[User] u
                    WHERE 1=1
                    {status_condition}
                """), count_params).scalar()
                
                total_pages = (total_count + per_page - 1) // per_page
                offset = (page - 1) * per_page
                
                query_params = {"offset": offset, "per_page": per_page}
                if status_filter != 'all':
                    query_params["status_filter"] = status_filter
                users = conn.execute(text(f"""
                    SELECT u.userId, u.email, u.status, u.createdAt, u.lastLoginAt, r.roleName,
                           a.username
                    FROM cine.[User] u
                    JOIN cine.Role r ON r.roleId = u.roleId
                    LEFT JOIN cine.Account a ON a.userId = u.userId
                    WHERE 1=1
                    {status_condition}
                    ORDER BY u.createdAt DESC, u.userId DESC
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """), query_params).mappings().all()
            
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
                             search_query=search_query,
                             status_filter=status_filter)
    except Exception as e:
        current_app.logger.error(f"Error loading admin users: {e}", exc_info=True)
        flash(f"Lỗi khi tải danh sách người dùng: {str(e)}", "error")
        return render_template("admin_users.html", 
                             users=[], 
                             pagination=None,
                             search_query=search_query,
                             status_filter=status_filter)


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
        flash(f"Đã thay đổi trạng thái {user_info.email} thành {status_text}!", "success")
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
            
            flash(f"Đã xóa tài khoản {user_info.email} thành công!", "success")
    except Exception as e:
        current_app.logger.error(f"Error deleting user: {e}", exc_info=True)
        flash(f"❌ Lỗi khi xóa người dùng: {str(e)}", "error")
    
    return redirect(url_for("main.admin_users"))


def _calculate_movie_similarity(movie_id: int):
    """
    Tính similarity cho phim mới với các phim khác trong database
    Chạy trong background thread để không block request
    """
    from .common import similarity_progress
    
    def update_progress(progress_value: int, message: str, status: str = 'running', movie_title: Optional[str] = None):
        entry = similarity_progress.get(movie_id, {}).copy()
        entry['status'] = status
        entry['progress'] = max(0, min(100, int(progress_value)))
        entry['message'] = message
        if movie_title:
            entry['movieTitle'] = movie_title
        similarity_progress[movie_id] = entry

    def load_genres_for_movies(conn, ids):
        if not ids:
            return {}
        placeholders = ','.join([f':gid{i}' for i in range(len(ids))])
        params = {f'gid{i}': int(mid) for i, mid in enumerate(ids)}
        genre_rows = conn.execute(text(f"""
            SELECT mg.movieId, g.name
            FROM cine.MovieGenre mg
            JOIN cine.Genre g ON mg.genreId = g.genreId
            WHERE mg.movieId IN ({placeholders})
        """), params).fetchall()
        genres_map = {}
        for movie_id_value, genre_name in genre_rows:
            genres_map.setdefault(int(movie_id_value), []).append(genre_name)
        return genres_map

    def build_movies_data(rows, genres_map):
        movies_data = []
        for row in rows:
            movie_id_value = int(row.movieId)
            year = row.releaseYear or 2000
            title = row.title or ''
            title_with_year = f"{title} ({year})" if year and f"({year})" not in title else title
            genre_list = genres_map.get(movie_id_value, [])
            genres_text = " ".join(genre_list)
            movies_data.append({
                'movieId': movie_id_value,
                'title_for_vector': title_with_year,
                'genres_text': genres_text,
                'year': year,
                'avgRating': float(row.avgRating or 0.0),
                'ratingCount': int(row.ratingCount or 0)
            })
        return movies_data

    def compute_similarity_scores(new_meta, movies_data):
        if not movies_data:
            return np.array([])
        
        doc_genres = [new_meta['genres_text']] + [m['genres_text'] for m in movies_data]
        if any(doc.strip() for doc in doc_genres):
            genres_vectorizer = TfidfVectorizer(max_features=500, min_df=1)
            genres_matrix = genres_vectorizer.fit_transform(doc_genres)
            genres_sim = cosine_similarity(genres_matrix[0:1], genres_matrix[1:])[0]
        else:
            genres_sim = np.zeros(len(movies_data))

        title_docs = [new_meta['title_for_vector']] + [m['title_for_vector'] for m in movies_data]
        title_vectorizer = TfidfVectorizer(max_features=500, min_df=1)
        title_matrix = title_vectorizer.fit_transform(title_docs)
        title_sim = cosine_similarity(title_matrix[0:1], title_matrix[1:])[0]

        years = np.array([[new_meta['year']]] + [[m['year']] for m in movies_data], dtype=float)
        year_scaler = MinMaxScaler()
        years_scaled = year_scaler.fit_transform(years)
        year_sim = 1 - np.abs(years_scaled[0] - years_scaled[1:]).flatten()
        year_sim = np.clip(year_sim, 0, 1)

        popularity = np.array([[np.log1p(new_meta['ratingCount'])]] + [[np.log1p(m['ratingCount'])] for m in movies_data], dtype=float)
        pop_scaler = MinMaxScaler()
        pop_scaled = pop_scaler.fit_transform(popularity)
        pop_sim = 1 - np.abs(pop_scaled[0] - pop_scaled[1:]).flatten()
        pop_sim = np.clip(pop_sim, 0, 1)

        ratings = np.array([[new_meta['avgRating']]] + [[m['avgRating']] for m in movies_data], dtype=float)
        rating_scaler = MinMaxScaler()
        rating_scaled = rating_scaler.fit_transform(ratings)
        rating_sim = 1 - np.abs(rating_scaled[0] - rating_scaled[1:]).flatten()
        rating_sim = np.clip(rating_sim, 0, 1)

        final_scores = (
            genres_sim * 0.60 +
            title_sim * 0.20 +
            year_sim * 0.07 +
            pop_sim * 0.07 +
            rating_sim * 0.06
        )
        return final_scores

    def build_similarity_pairs(movies_data, scores):
        pairs = []
        for idx, score in enumerate(scores):
            if score <= 0.05:
                continue
            other_movie_id = movies_data[idx]['movieId']
            similarity_value = float(score)
            pairs.append({
                "movieId1": movie_id,
                "movieId2": other_movie_id,
                "similarity": similarity_value
            })
            pairs.append({
                "movieId1": other_movie_id,
                "movieId2": movie_id,
                "similarity": similarity_value
            })
        return pairs

    def save_similarity_pairs(conn, similarities_to_save):
        if not similarities_to_save:
            return
        batch_size = 100
        for i in range(0, len(similarities_to_save), batch_size):
            batch = similarities_to_save[i:i + batch_size]
            values_clauses = []
            params = {}
            for idx, sim in enumerate(batch):
                prefix = f"p{idx}"
                values_clauses.append(f"(:{prefix}_id1, :{prefix}_id2, :{prefix}_sim)")
                params[f"{prefix}_id1"] = int(sim["movieId1"])
                params[f"{prefix}_id2"] = int(sim["movieId2"])
                params[f"{prefix}_sim"] = float(sim["similarity"])
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
    
    try:
        update_progress(1, 'Đang khởi tạo tiến trình...')

        with current_app.db_engine.connect() as info_conn:
            new_movie = info_conn.execute(text("""
                SELECT m.movieId, m.title, m.releaseYear, m.overview,
                       AVG(CAST(r.value AS FLOAT)) AS avgRating,
                       COUNT(r.value) AS ratingCount
                FROM cine.Movie m
                LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                WHERE m.movieId = :movie_id
                GROUP BY m.movieId, m.title, m.releaseYear, m.overview
            """), {"movie_id": movie_id}).mappings().first()

            if not new_movie:
                update_progress(0, 'Không tìm thấy phim', status='error')
                return

            movie_title = new_movie["title"]
            title_with_year = movie_title
            movie_year = new_movie.get("releaseYear")
            if movie_year and f"({movie_year})" not in movie_title:
                title_with_year = f"{movie_title} ({movie_year})"

            new_genres = info_conn.execute(text("""
                SELECT g.name 
                FROM cine.Genre g
                JOIN cine.MovieGenre mg ON g.genreId = mg.genreId
                WHERE mg.movieId = :movie_id
            """), {"movie_id": movie_id}).fetchall()
            new_genres_list = [g[0] for g in new_genres]
            new_genres_text = " ".join(new_genres_list)

            update_progress(5, 'Đang thu thập dữ liệu', movie_title=movie_title)

            genre_filter = ""
            if new_genres_list:
                genre_filter = """
                    AND EXISTS (
                        SELECT 1 
                        FROM cine.MovieGenre mg1
                        JOIN cine.MovieGenre mg2 ON mg1.genreId = mg2.genreId
                        WHERE mg1.movieId = m.movieId 
                        AND mg2.movieId = :movie_id
                    )
                """

            count_query = text(f"""
                SELECT COUNT(*) 
                FROM cine.Movie m
                WHERE m.movieId != :movie_id
                {genre_filter}
            """)
            candidate_count = info_conn.execute(count_query, {"movie_id": movie_id}).scalar() or 0

            if candidate_count == 0:
                update_progress(100, 'Không có phim nào để so sánh', status='completed', movie_title=movie_title)
                current_app.logger.warning(f"No candidate movies for similarity calculation of {movie_id}")
                return

            update_progress(8, f'Đang chuẩn bị {candidate_count} phim để so sánh', movie_title=movie_title)

            candidate_query = text(f"""
                WITH candidate_movies AS (
                    SELECT 
                        m.movieId, m.title, m.releaseYear, m.overview,
                        AVG(CAST(r.value AS FLOAT)) AS avgRating,
                        COUNT(r.value) AS ratingCount,
                        (SELECT COUNT(*) 
                         FROM cine.MovieGenre mg1 
                         JOIN cine.MovieGenre mg2 ON mg1.genreId = mg2.genreId
                         WHERE mg1.movieId = m.movieId 
                         AND mg2.movieId = :movie_id
                        ) AS common_genres_count
                    FROM cine.Movie m
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    WHERE m.movieId != :movie_id
                    {genre_filter}
                    GROUP BY m.movieId, m.title, m.releaseYear, m.overview
                )
                SELECT *
                FROM candidate_movies
                ORDER BY 
                    CASE WHEN :has_genres = 1 THEN common_genres_count ELSE 1 END DESC,
                    avgRating DESC,
                    ratingCount DESC
            """)

            candidate_params = {"movie_id": movie_id, "has_genres": 1 if new_genres_list else 0}

        read_conn = current_app.db_engine.connect().execution_options(stream_results=True)
        candidate_result = read_conn.execute(candidate_query, candidate_params)

        processed = 0
        relationships_created = 0

        new_movie_meta = {
            "genres_text": new_genres_text,
            "title_for_vector": title_with_year,
            "year": movie_year or 2000,
            "avgRating": float(new_movie.get("avgRating") or 0.0),
            "ratingCount": int(new_movie.get("ratingCount") or 0),
        }

        try:
            with current_app.db_engine.begin() as write_conn:
                while True:
                    chunk_rows = candidate_result.fetchmany(SIMILARITY_CHUNK_SIZE)
                    if not chunk_rows:
                        break

                    chunk_ids = [int(row.movieId) for row in chunk_rows]
                    genres_map = load_genres_for_movies(write_conn, chunk_ids)
                    movies_data = build_movies_data(chunk_rows, genres_map)

                    if not movies_data:
                        processed += len(chunk_rows)
                        continue

                    scores = compute_similarity_scores(new_movie_meta, movies_data)
                    chunk_pairs = build_similarity_pairs(movies_data, scores)
                    if chunk_pairs:
                        save_similarity_pairs(write_conn, chunk_pairs)
                        relationships_created += len(chunk_pairs) // 2

                    processed += len(chunk_rows)
                    progress_value = 10 + int(75 * processed / max(1, candidate_count))
                    update_progress(progress_value, f'Đã xử lý {processed}/{candidate_count} phim', movie_title=movie_title)
        finally:
            candidate_result.close()
            read_conn.close()

        update_progress(90, 'Đang hoàn tất cập nhật dữ liệu...', movie_title=movie_title)
        update_progress(100,
                        f'🎉 Tính similarity hoàn tất cho {relationships_created} phim liên quan',
                        status='completed',
                        movie_title=movie_title)
        current_app.logger.info(f"Calculated similarity for movie {movie_id} with {relationships_created} related movies")

    except Exception as e:
        update_progress(0, f'❌ Lỗi: {str(e)}', status='error')
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
        language = request.form.get("language", "").strip()
        budget = request.form.get("budget", "").strip()
        revenue = request.form.get("revenue", "").strip()
        runtime = request.form.get("runtime", "").strip()
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
        
        # 7. Language validation (max 50 chars, chữ/số/dấu phân cách cơ bản)
        language_value = None
        language_pattern = r"^[A-Za-zÀ-ỹ0-9 ,.\-()]+$"
        if language:
            if len(language) > 50:
                errors.append("Ngôn ngữ không được quá 50 ký tự")
            elif not re.match(language_pattern, language):
                errors.append("Ngôn ngữ chỉ được chứa chữ, số và các ký tự , . - ( )")
            else:
                language_value = language

        # 8. Budget validation (số dương, tối đa 12 chữ số)
        digits_12_pattern = r"^\d{1,12}$"
        budget_value = None
        if budget:
            if not re.match(digits_12_pattern, budget):
                errors.append("Ngân sách phải là số dương, tối đa 12 chữ số (không chứa ký tự khác)")
            else:
                budget_value = int(budget)

        # 9. Revenue validation
        revenue_value = None
        if revenue:
            if not re.match(digits_12_pattern, revenue):
                errors.append("Doanh thu phải là số dương, tối đa 12 chữ số (không chứa ký tự khác)")
            else:
                revenue_value = int(revenue)

        # 10. Runtime validation (1-600 phút)
        runtime_value = None
        if runtime:
            if not re.match(r"^\d{1,3}$", runtime):
                errors.append("Thời lượng chỉ được nhập số (tối đa 3 chữ số)")
            else:
                runtime_value = int(runtime)
                if runtime_value < 1 or runtime_value > 600:
                    errors.append("Thời lượng phải từ 1 đến 600 phút")

        # 11. Genres validation
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
                                          trailerUrl, posterUrl, backdropUrl, movieUrl, language, budget, revenue, runtime, 
                                          viewCount, createdAt)
                    VALUES (:movieId, :title, :year, :country, :overview, :director, :cast, 
                            :trailer, :poster, :backdrop, :movieUrl, :language, :budget, :revenue, :runtime,
                            0, GETDATE())
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
                    "movieUrl": movie_url if movie_url else None,
                    "language": language_value,
                    "budget": budget_value,
                    "revenue": revenue_value,
                    "runtime": runtime_value
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
                
                # Đưa job tính similarity vào background worker
                enqueue_similarity_job(movie_id, title)
                current_app.logger.info(f"Queued similarity calculation for movie {movie_id}")
                
                # Thông báo thành công và redirect về trang quản lý phim với movie_id để hiển thị progress
                flash(f"Thêm phim thành công (ID: {movie_id})", "success")
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
        language = request.form.get("language", "").strip()
        budget = request.form.get("budget", "").strip()
        revenue = request.form.get("revenue", "").strip()
        runtime = request.form.get("runtime", "").strip()
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
        
        # 9. Language validation
        language_value = None
        language_pattern = r"^[A-Za-zÀ-ỹ0-9 ,.\-()]+$"
        if language:
            if len(language) > 50:
                errors.append("Ngôn ngữ không được quá 50 ký tự")
            elif not re.match(language_pattern, language):
                errors.append("Ngôn ngữ chỉ được chứa chữ, số và các ký tự , . - ( )")
            else:
                language_value = language

        # 10. Budget validation
        digits_12_pattern = r"^\d{1,12}$"
        budget_value = None
        if budget:
            if not re.match(digits_12_pattern, budget):
                errors.append("Ngân sách phải là số dương, tối đa 12 chữ số (không chứa ký tự khác)")
            else:
                budget_value = int(budget)

        # 11. Revenue validation
        revenue_value = None
        if revenue:
            if not re.match(digits_12_pattern, revenue):
                errors.append("Doanh thu phải là số dương, tối đa 12 chữ số (không chứa ký tự khác)")
            else:
                revenue_value = int(revenue)

        # 12. Runtime validation
        runtime_value = None
        if runtime:
            if not re.match(r"^\d{1,3}$", runtime):
                errors.append("Thời lượng chỉ được nhập số (tối đa 3 chữ số)")
            else:
                runtime_value = int(runtime)
                if runtime_value < 1 or runtime_value > 600:
                    errors.append("Thời lượng phải từ 1 đến 600 phút")

        # 13. Genres validation
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
                        posterUrl = :poster, backdropUrl = :backdrop, viewCount = :views,
                        language = :language, budget = :budget, revenue = :revenue, runtime = :runtime
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
                    "views": views,
                    "language": language_value,
                    "budget": budget_value,
                    "revenue": revenue_value,
                    "runtime": runtime_value
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
                
                flash("Cập nhật phim thành công!", "success")
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
                       imdbRating, trailerUrl, posterUrl, backdropUrl, viewCount,
                       language, budget, revenue, runtime
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
                'view_count': str(movie.get('viewCount', 0)) if movie.get('viewCount') is not None else '0',
                'language': movie.get('language', '') or '',
                'budget': str(movie.get('budget', '')) if movie.get('budget') is not None else '',
                'revenue': str(movie.get('revenue', '')) if movie.get('revenue') is not None else '',
                'runtime': str(movie.get('runtime', '')) if movie.get('runtime') is not None else '',
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
            
            flash(f"Đã xóa phim '{movie.title}' thành công!", "success")
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
        script_path = os.path.join(cinebox_dir, 'model_collaborative', 'train_collaborative.py')
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

