from flask import Blueprint, render_template, request, redirect, url_for, current_app, session, jsonify
from sqlalchemy import text
import sys
import os
import time
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from content_based_recommender import ContentBasedRecommender


main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def home():
    # Lấy danh sách phim từ DB bằng engine (odbc_connect); nếu chưa đăng nhập, chuyển tới form đăng nhập
    if not session.get("user_id"):
        return redirect(url_for("main.login"))
    
    # Lấy page parameter cho tất cả phim và genre filter
    page = request.args.get('page', 1, type=int)
    per_page = 12  # Số phim mỗi trang
    genre_filter = request.args.get('genre', '', type=str)  # Lọc theo thể loại
    
    # 1. Phim mới nhất (12 phim, không phân trang) - thay thế trending
    try:
        with current_app.db_engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT TOP 12 movieId, title, posterUrl, backdropUrl, overview, createdAt FROM cine.Movie ORDER BY createdAt DESC, movieId DESC"
            )).mappings().all()
            
            latest_movies = [
                {
                    "id": r["movieId"],
                    "title": r["title"],
                    "poster": r.get("posterUrl") or "/static/img/dune2.jpg",
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                    "createdAt": r.get("createdAt"),
                }
                for r in rows
            ]
    except Exception:
        latest_movies = []
    
    # Personal recommendations (gợi ý cá nhân)
    user_id = session.get("user_id")
    personal_recommendations = []
    
    if user_id:
        try:
            # Lấy gợi ý cá nhân từ PersonalRecommendation
            with current_app.db_engine.connect() as conn:
                personal_rows = conn.execute(text("""
                    SELECT TOP 12 m.movieId, m.title, m.posterUrl, pr.score
                    FROM cine.PersonalRecommendation pr
                    JOIN cine.Movie m ON m.movieId = pr.movieId
                    WHERE pr.userId = :user_id AND pr.expiresAt > GETDATE()
                    ORDER BY pr.rank
                """), {"user_id": user_id}).mappings().all()
                
                personal_recommendations = [
                    {
                        "id": row["movieId"],
                        "title": row["title"],
                        "poster": row.get("posterUrl") or "/static/img/dune2.jpg",
                        "score": row["score"]
                    }
                    for row in personal_rows
                ]
        except Exception as e:
            print(f"Error getting personal recommendations: {e}")
            personal_recommendations = []
    
    # Fallback nếu không có gợi ý cá nhân
    if not personal_recommendations:
        personal_recommendations = latest_movies
    
    # 3. Tất cả phim (có phân trang) - thay thế latest_movies cũ
    all_movies = []
    total_movies = 0
    pagination = None
    
    try:
        with current_app.db_engine.connect() as conn:
            if genre_filter:
                # Lọc theo thể loại nếu được chọn
                # Đếm tổng số phim theo thể loại
                total_count = conn.execute(text("""
                    SELECT COUNT(DISTINCT m.movieId)
                    FROM cine.Movie m
                    JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                    JOIN cine.Genre g ON mg.genreId = g.genreId
                    WHERE g.name = :genre
                """), {"genre": genre_filter}).scalar()
                total_movies = total_count
                
                # Tính toán phân trang
                total_pages = (total_movies + per_page - 1) // per_page
                offset = (page - 1) * per_page
                
                # Lấy phim theo thể loại với phân trang
                all_rows = conn.execute(text("""
                    SELECT m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear
                    FROM (
                        SELECT DISTINCT m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear,
                               ROW_NUMBER() OVER (ORDER BY m.movieId DESC) as rn
                        FROM cine.Movie m
                        JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                        JOIN cine.Genre g ON mg.genreId = g.genreId
                        WHERE g.name = :genre
                    ) t
                    WHERE rn > :offset AND rn <= :offset + :per_page
                """), {"genre": genre_filter, "offset": offset, "per_page": per_page}).mappings().all()
            else:
                # Lấy tất cả phim
                # Đếm tổng số phim
                total_count = conn.execute(text("SELECT COUNT(*) FROM cine.Movie")).scalar()
                total_movies = total_count
                
                # Tính toán phân trang
                total_pages = (total_movies + per_page - 1) // per_page
                offset = (page - 1) * per_page
                
                # Lấy tất cả phim với phân trang
                all_rows = conn.execute(text("""
                    SELECT movieId, title, posterUrl, backdropUrl, overview, releaseYear
                    FROM (
                        SELECT movieId, title, posterUrl, backdropUrl, overview, releaseYear,
                               ROW_NUMBER() OVER (ORDER BY movieId DESC) as rn
                        FROM cine.Movie
                    ) t
                    WHERE rn > :offset AND rn <= :offset + :per_page
                """), {"offset": offset, "per_page": per_page}).mappings().all()
            
            all_movies = [
                {
                    "id": r["movieId"],
                    "title": r["title"],
                    "poster": r.get("posterUrl") or "/static/img/dune2.jpg",
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                    "year": r.get("releaseYear")
                }
                for r in all_rows
            ]
            
            # Tạo pagination info
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
    
    return render_template("home.html", 
                         latest_movies=latest_movies,  # Phim mới nhất (12 phim, không phân trang)
                         recommended=personal_recommendations,  # Phim đề xuất
                         all_movies=all_movies,  # Tất cả phim (có phân trang)
                         pagination=pagination,
                         genre_filter=genre_filter)


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        
        print(f"🔍 Login attempt: username='{username}', password='{password}'")
        
        with current_app.db_engine.connect() as conn:
            # Test query đơn giản trước
            test_query = text("""
                SELECT u.userId, u.email, r.roleName
                FROM cine.Account a
                JOIN cine.[User] u ON u.userId = a.userId
                JOIN cine.Role r ON r.roleId = u.roleId
                WHERE (
                    a.username = :u OR u.email = :u
                ) AND a.passwordHash = HASHBYTES('SHA2_256', CONVERT(VARBINARY(512), :p))
            """)
            
            print(f"🔍 Executing query with params: u='{username}', p='{password}'")
            
            try:
                result = conn.execute(test_query, {"u": username, "p": password})
                rows = result.fetchall()
                print(f"🔍 Query result: {len(rows)} rows")
                
                if rows:
                    row = rows[0]
                    print(f"🔍 Found user: ID={row[0]}, Email={row[1]}, Role={row[2]}")
                    
                    session["user_id"] = int(row[0])
                    session["role"] = row[2]
                    session["username"] = username
                    session["email"] = row[1]
                    print(f"🔍 Session set: user_id={session['user_id']}, role={session['role']}")
                    return redirect(url_for("main.home"))
                else:
                    print("🔍 No user found with these credentials")
                    error = "Sai tài khoản hoặc mật khẩu"
            except Exception as e:
                print(f"🔍 Database error: {e}")
                error = f"Lỗi database: {str(e)}"
    return render_template("login.html", error=error)


@main_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        if not name or not email or not password:
            return render_template("register.html", error="Vui lòng nhập đầy đủ thông tin.")
        try:
            with current_app.db_engine.begin() as conn:
                # create user with User role
                role_id = conn.execute(text("SELECT roleId FROM cine.Role WHERE roleName=N'User'")) .scalar()
                if role_id is None:
                    conn.execute(text("INSERT INTO cine.Role(roleName, description) VALUES (N'User', N'Người dùng')"))
                    role_id = conn.execute(text("SELECT roleId FROM cine.Role WHERE roleName=N'User'")) .scalar()
                conn.execute(text("INSERT INTO cine.[User](email, avatarUrl, roleId) VALUES (:email, NULL, :roleId)"), {"email": email, "roleId": role_id})
                user_id = conn.execute(text("SELECT userId FROM cine.[User] WHERE email=:email"), {"email": email}).scalar()
                conn.execute(text("INSERT INTO cine.Account(username, passwordHash, userId) VALUES (:u, HASHBYTES('SHA2_256', CONVERT(VARBINARY(512), :p)), :uid)"), {"u": email, "p": password, "uid": user_id})
            return render_template("register.html", success="Đăng ký thành công! Bạn có thể đăng nhập ngay.")
        except Exception as ex:
            return render_template("register.html", error=f"Không thể đăng ký: {str(ex)}")
    return render_template("register.html")


@main_bp.route("/movie/<int:movie_id>")
def detail(movie_id: int):
    with current_app.db_engine.connect() as conn:
        # Lấy thông tin phim chính
        r = conn.execute(text(
            "SELECT movieId, title, releaseYear, posterUrl, backdropUrl, overview FROM cine.Movie WHERE movieId=:id"
        ), {"id": movie_id}).mappings().first()
        
        if not r:
            return redirect(url_for("main.home"))
        
        # Lấy genres của phim
        genres_query = text("""
            SELECT g.name
            FROM cine.MovieGenre mg
            JOIN cine.Genre g ON mg.genreId = g.genreId
            WHERE mg.movieId = :movie_id
        """)
        genres_result = conn.execute(genres_query, {"movie_id": movie_id}).fetchall()
        genres = [genre[0] for genre in genres_result]
        
        movie = {
            "id": r["movieId"],
            "title": r["title"],
            "year": r.get("releaseYear"),
            "duration": "120 phút",  # Default duration
            "genres": genres,
            "rating": 5.0,  # Default rating
            "poster": r.get("posterUrl") or "/static/img/dune2.jpg",
            "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
            "description": r.get("overview") or "",
            "sources": [{"label": "720p", "url": "https://www.w3schools.com/html/movie.mp4"}],
        }
    
    # CONTENT-BASED: Phim liên quan sử dụng ContentBasedRecommender
    related = []
    try:
        # Tạo recommender instance
        recommender = ContentBasedRecommender(current_app.db_engine)
        
        # Lấy phim liên quan từ model AI
        related_movies = recommender.get_related_movies(movie_id, limit=6)
        
        # Format data cho template
        related = [
            {
                "movieId": movie["movieId"],
                "title": movie["title"],
                "posterUrl": movie["posterUrl"],
                "similarity": movie.get("similarity", 0.0),
                "overview": movie.get("overview", ""),
                "releaseYear": movie.get("releaseYear")
            }
            for movie in related_movies
        ]
        
    except Exception as e:
        print(f"Error getting related movies: {e}")
        # Fallback: lấy phim ngẫu nhiên
        try:
            fallback_rows = conn.execute(text("""
                SELECT TOP 6 movieId, title, posterUrl, releaseYear
                FROM cine.Movie 
                WHERE movieId != :id
                ORDER BY NEWID()
            """), {"id": movie_id}).mappings().all()
            
            related = [
                {
                    "movieId": row["movieId"],
                    "title": row["title"],
                    "posterUrl": row.get("posterUrl") or "/static/img/dune2.jpg",
                    "similarity": 0.0,
                    "overview": "",
                    "releaseYear": row.get("releaseYear")
                }
                for row in fallback_rows
            ]
        except Exception as fallback_error:
            print(f"Fallback error: {fallback_error}")
            related = []
    
    return render_template("detail.html", movie=movie, related=related)


@main_bp.route("/watch/<int:movie_id>")
def watch(movie_id: int):
    with current_app.db_engine.connect() as conn:
        # Lấy thông tin phim chính
        r = conn.execute(text(
            "SELECT movieId, title, posterUrl, backdropUrl, releaseYear, overview FROM cine.Movie WHERE movieId=:id"
        ), {"id": movie_id}).mappings().first()
        
        if not r:
            return redirect(url_for("main.home"))
        
        movie = {
            "id": r["movieId"],
            "title": r["title"],
            "poster": r.get("posterUrl") or "/static/img/dune2.jpg",
            "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
            "year": r.get("releaseYear"),
            "overview": r.get("overview") or "",
            "sources": [{"label": "720p", "url": "https://www.w3schools.com/html/movie.mp4"}],
        }
        
        # Lấy phim liên quan từ model đã train
        related_movies = []
        try:
            # Sử dụng ContentBasedRecommender để lấy phim liên quan
            recommender = ContentBasedRecommender(current_app.db_engine)
            related_movies_raw = recommender.get_related_movies(movie_id, limit=8)
            
            # Format data cho template
            related_movies = [
                {
                    "movieId": movie["movieId"],
                    "title": movie["title"],
                    "posterUrl": movie["posterUrl"],
                    "similarity": movie.get("similarity", 0.0),
                    "releaseYear": movie.get("releaseYear")
                }
                for movie in related_movies_raw
            ]
        except Exception as e:
            print(f"Error getting related movies: {e}")
            # Fallback: lấy phim ngẫu nhiên nếu không có recommendations
            try:
                fallback_rows = conn.execute(text("""
                    SELECT TOP 8 movieId, title, posterUrl, releaseYear
                    FROM cine.Movie 
                    WHERE movieId != :id
                    ORDER BY NEWID()
                """), {"id": movie_id}).mappings().all()
                
                related_movies = [
                    {
                        "movieId": row["movieId"],
                        "title": row["title"],
                        "posterUrl": row.get("posterUrl") or "/static/img/dune2.jpg",
                        "releaseYear": row.get("releaseYear"),
                        "similarity": 0.0
                    }
                    for row in fallback_rows
                ]
            except Exception as fallback_error:
                print(f"Fallback error: {fallback_error}")
                related_movies = []
        
        # Lấy phim mới ra mắt (giữ nguyên logic cũ)
        try:
            new_releases_rows = conn.execute(text("""
                SELECT TOP 8 movieId, title, posterUrl, releaseYear
                FROM cine.Movie 
                ORDER BY releaseYear DESC, movieId DESC
            """)).mappings().all()
            
            new_releases = [
                {
                    "movieId": row["movieId"],
                    "title": row["title"],
                    "posterUrl": row.get("posterUrl") or "/static/img/dune2.jpg",
                    "releaseYear": row.get("releaseYear")
                }
                for row in new_releases_rows
            ]
        except Exception:
            new_releases = []
    
    return render_template("watch.html", 
                         movie=movie, 
                         related_movies=related_movies,
                         new_releases=new_releases)


@main_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.home"))


@main_bp.route("/account")
def account():
    """Trang tài khoản của tôi"""
    if not session.get("user_id"):
        return redirect(url_for("main.login"))
    
    user_id = session.get("user_id")
    
    # Lấy thông tin user
    try:
        with current_app.db_engine.connect() as conn:
            user_info = conn.execute(text("""
                SELECT u.userId, u.email, u.avatarUrl, u.phone, u.status, u.createdAt, u.lastLoginAt, r.roleName
                FROM [cine].[User] u
                JOIN [cine].[Role] r ON u.roleId = r.roleId
                WHERE u.userId = :user_id
            """), {"user_id": user_id}).mappings().first()
            
            # Cập nhật session với avatar mới nhất
            if user_info and user_info.avatarUrl:
                session['avatar'] = user_info.avatarUrl
            
            if not user_info:
                return redirect(url_for("main.login"))
            
            # Lấy danh sách xem sau (watchlist)
            watchlist = conn.execute(text("""
                SELECT m.movieId, m.title, m.posterUrl, m.releaseYear, wl.addedAt
                FROM [cine].[WatchList] wl
                JOIN [cine].[Movie] m ON wl.movieId = m.movieId
                WHERE wl.userId = :user_id
                ORDER BY wl.addedAt DESC
            """), {"user_id": user_id}).mappings().all()
            
            # Lấy danh sách yêu thích (favorites)
            favorites = conn.execute(text("""
                SELECT m.movieId, m.title, m.posterUrl, m.releaseYear, f.addedAt
                FROM [cine].[Favorite] f
                JOIN [cine].[Movie] m ON f.movieId = m.movieId
                WHERE f.userId = :user_id
                ORDER BY f.addedAt DESC
            """), {"user_id": user_id}).mappings().all()
            
    except Exception as e:
        print(f"Error getting account info: {e}")
        user_info = None
        watchlist = []
        favorites = []
    
    return render_template("account.html", 
                         user=user_info,
                         watchlist=watchlist,
                         favorites=favorites)


@main_bp.route("/update-profile", methods=["POST"])
def update_profile():
    """Cập nhật thông tin profile"""
    if not session.get("user_id"):
        return redirect(url_for("main.login"))
    
    user_id = session.get("user_id")
    phone = request.form.get("phone", "").strip()
    
    try:
        with current_app.db_engine.begin() as conn:
            # Cập nhật thông tin cơ bản
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
        
        return redirect(url_for("main.account"))
        
    except Exception as e:
        print(f"Error updating profile: {e}")
        return redirect(url_for("main.account"))


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
                SELECT 1 FROM [cine].[WatchList] 
                WHERE userId = :user_id AND movieId = :movie_id
            """), {"user_id": user_id, "movie_id": movie_id}).scalar()
            
            if not existing:
                conn.execute(text("""
                    INSERT INTO [cine].[WatchList] (userId, movieId, addedAt)
                    VALUES (:user_id, :movie_id, GETDATE())
                """), {"user_id": user_id, "movie_id": movie_id})
                
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
                DELETE FROM [cine].[WatchList] 
                WHERE userId = :user_id AND movieId = :movie_id
            """), {"user_id": user_id, "movie_id": movie_id})
            
            return jsonify({"success": True, "message": "Đã xóa khỏi danh sách xem sau"})
            
    except Exception as e:
        print(f"Error removing from watchlist: {e}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra"})


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
                conn.execute(text("""
                    INSERT INTO [cine].[Favorite] (userId, movieId, addedAt)
                    VALUES (:user_id, :movie_id, GETDATE())
                """), {"user_id": user_id, "movie_id": movie_id})
                
                return jsonify({"success": True, "message": "Đã thêm vào danh sách yêu thích"})
            else:
                return jsonify({"success": False, "message": "Phim đã có trong danh sách yêu thích"})
                
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


@main_bp.route("/avatar/<path:filename>")
def serve_avatar(filename):
    """Serve avatar files from D:/N5/KLTN/WebXemPhim/avatar"""
    from flask import send_file
    import os
    
    avatar_path = os.path.join(r"D:\N5\KLTN\WebXemPhim\avatar", filename)
    
    if os.path.exists(avatar_path):
        return send_file(avatar_path)
    else:
        # Return default avatar if file not found
        return send_file(os.path.join(current_app.static_folder, "img", "avatar_default.png"))


def require_role(role_name: str):
    def decorator(fn):
        from functools import wraps

        @wraps(fn)
        def wrapper(*args, **kwargs):
            role = session.get("role")
            if role != role_name:
                return redirect(url_for("main.login"))
            return fn(*args, **kwargs)

        return wrapper

    return decorator


@main_bp.route("/admin")
@require_role("Admin")
def admin_dashboard():
    # simple admin page
    return render_template("admin_movies.html", movies=[])


# Admin: movie CRUD (list/create/edit/delete)
@main_bp.route("/admin/movies")
@require_role("Admin")
def admin_movies():
    with current_app.db_engine.connect() as conn:
        rows = conn.execute(text("SELECT movieId, title, releaseYear FROM cine.Movie ORDER BY movieId DESC"))
        movies = [dict(r._mapping) for r in rows]
    return render_template("admin_movies.html", movies=movies)


@main_bp.route("/admin/movies/create", methods=["GET", "POST"])
@require_role("Admin")
def admin_movie_create():
    if request.method == "POST":
        # basic validation
        title = request.form.get("title", "").strip()
        releaseYear = request.form.get("releaseYear", type=int)
        durationMin = request.form.get("durationMin", type=int)
        imdbRating = request.form.get("imdbRating", type=float)
        if not title:
            return render_template("admin_movie_form.html", movie=None, error="Tiêu đề là bắt buộc")
        if releaseYear is not None and (releaseYear < 1800 or releaseYear > 2100):
            return render_template("admin_movie_form.html", movie=None, error="Năm phát hành phải trong khoảng 1800-2100")
        if durationMin is not None and durationMin <= 0:
            return render_template("admin_movie_form.html", movie=None, error="Thời lượng phải > 0 phút")
        if imdbRating is not None and (imdbRating < 0 or imdbRating > 10):
            return render_template("admin_movie_form.html", movie=None, error="Điểm IMDb phải từ 0 đến 10")

        data = {
            "title": request.form.get("title"),
            "releaseYear": releaseYear,
            "country": request.form.get("country"),
            "overview": request.form.get("overview"),
            "director": request.form.get("director"),
            "cast": request.form.get("cast"),
            "durationMin": durationMin,
            "imdbRating": imdbRating,
            "trailerUrl": request.form.get("trailerUrl"),
            "posterUrl": request.form.get("posterUrl"),
            "backdropUrl": request.form.get("backdropUrl"),
            "viewCount": request.form.get("viewCount", type=int) or 0,
        }
        with current_app.db_engine.begin() as conn:
            # Tạo phim mới
            result = conn.execute(text(
                """
                INSERT INTO cine.Movie(title, releaseYear, country, overview, director, cast, durationMin, imdbRating, trailerUrl, posterUrl, backdropUrl, viewCount)
                VALUES (:title, :releaseYear, :country, :overview, :director, :cast, :durationMin, :imdbRating, :trailerUrl, :posterUrl, :backdropUrl, :viewCount)
                """
            ), data)
            
            # Lấy movieId vừa tạo
            movie_id = result.lastrowid
            
            # Thêm thể loại cho phim mới
            selected_genres = request.form.getlist("genres")
            for genre_id in selected_genres:
                if genre_id:  # Kiểm tra không rỗng
                    conn.execute(text(
                        "INSERT INTO cine.MovieGenre (movieId, genreId) VALUES (:movieId, :genreId)"
                    ), {"movieId": movie_id, "genreId": int(genre_id)})
        
        return redirect(url_for("main.admin_movies"))
    
    # GET request - hiển thị form tạo phim mới với danh sách thể loại
    with current_app.db_engine.connect() as conn:
        all_genres = conn.execute(text("SELECT genreId, name FROM cine.Genre ORDER BY name")).mappings().all()
    
    return render_template("admin_movie_form.html", movie=None, all_genres=all_genres)


@main_bp.route("/admin/movies/<int:movie_id>/edit", methods=["GET", "POST"])
@require_role("Admin")
def admin_movie_edit(movie_id: int):
    if request.method == "POST":
        # basic validation
        title = request.form.get("title", "").strip()
        releaseYear = request.form.get("releaseYear", type=int)
        durationMin = request.form.get("durationMin", type=int)
        imdbRating = request.form.get("imdbRating", type=float)
        if not title:
            with current_app.db_engine.connect() as conn:
                existing = conn.execute(text("SELECT * FROM cine.Movie WHERE movieId=:id"), {"id": movie_id}).mappings().first()
            return render_template("admin_movie_form.html", movie=existing, error="Tiêu đề là bắt buộc")
        if releaseYear is not None and (releaseYear < 1800 or releaseYear > 2100):
            with current_app.db_engine.connect() as conn:
                existing = conn.execute(text("SELECT * FROM cine.Movie WHERE movieId=:id"), {"id": movie_id}).mappings().first()
            return render_template("admin_movie_form.html", movie=existing, error="Năm phát hành phải trong khoảng 1800-2100")
        if durationMin is not None and durationMin <= 0:
            with current_app.db_engine.connect() as conn:
                existing = conn.execute(text("SELECT * FROM cine.Movie WHERE movieId=:id"), {"id": movie_id}).mappings().first()
            return render_template("admin_movie_form.html", movie=existing, error="Thời lượng phải > 0 phút")
        if imdbRating is not None and (imdbRating < 0 or imdbRating > 10):
            with current_app.db_engine.connect() as conn:
                existing = conn.execute(text("SELECT * FROM cine.Movie WHERE movieId=:id"), {"id": movie_id}).mappings().first()
            return render_template("admin_movie_form.html", movie=existing, error="Điểm IMDb phải từ 0 đến 10")

        data = {
            "movieId": movie_id,
            "title": title,
            "releaseYear": releaseYear,
            "country": request.form.get("country"),
            "overview": request.form.get("overview"),
            "director": request.form.get("director"),
            "cast": request.form.get("cast"),
            "durationMin": durationMin,
            "imdbRating": imdbRating,
            "trailerUrl": request.form.get("trailerUrl"),
            "posterUrl": request.form.get("posterUrl"),
            "backdropUrl": request.form.get("backdropUrl"),
            "viewCount": request.form.get("viewCount", type=int) or 0,
        }
        with current_app.db_engine.begin() as conn:
            # Cập nhật thông tin phim
            conn.execute(text(
                """
                UPDATE cine.Movie
                SET title=:title, releaseYear=:releaseYear, country=:country, overview=:overview,
                    director=:director, cast=:cast, durationMin=:durationMin, imdbRating=:imdbRating, trailerUrl=:trailerUrl,
                    posterUrl=:posterUrl, backdropUrl=:backdropUrl, viewCount=:viewCount
                WHERE movieId=:movieId
                """
            ), data)
            
            # Xử lý thể loại
            selected_genres = request.form.getlist("genres")  # Lấy danh sách thể loại được chọn
            
            # Xóa tất cả thể loại cũ
            conn.execute(text("DELETE FROM cine.MovieGenre WHERE movieId = :movieId"), {"movieId": movie_id})
            
            # Thêm thể loại mới
            for genre_id in selected_genres:
                if genre_id:  # Kiểm tra không rỗng
                    conn.execute(text(
                        "INSERT INTO cine.MovieGenre (movieId, genreId) VALUES (:movieId, :genreId)"
                    ), {"movieId": movie_id, "genreId": int(genre_id)})
        
        return redirect(url_for("main.admin_movies"))
    
    # GET request - hiển thị form edit với thông tin thể loại
    with current_app.db_engine.connect() as conn:
        # Lấy thông tin phim
        movie = conn.execute(text("SELECT * FROM cine.Movie WHERE movieId=:id"), {"id": movie_id}).mappings().first()
        
        # Lấy thể loại hiện tại của phim
        current_genres = conn.execute(text("""
            SELECT g.genreId, g.name 
            FROM cine.Genre g 
            JOIN cine.MovieGenre mg ON g.genreId = mg.genreId 
            WHERE mg.movieId = :movie_id
            ORDER BY g.name
        """), {"movie_id": movie_id}).mappings().all()
        
        # Lấy tất cả thể loại có sẵn
        all_genres = conn.execute(text("SELECT genreId, name FROM cine.Genre ORDER BY name")).mappings().all()
        
        # Tạo danh sách ID thể loại hiện tại để check checkbox
        current_genre_ids = [genre.genreId for genre in current_genres]
    
    return render_template("admin_movie_form.html", 
                         movie=movie, 
                         current_genres=current_genres,
                         all_genres=all_genres,
                         current_genre_ids=current_genre_ids)


@main_bp.route("/admin/movies/<int:movie_id>/delete", methods=["POST"]) 
@require_role("Admin")
def admin_movie_delete(movie_id: int):
    with current_app.db_engine.begin() as conn:
        conn.execute(text("DELETE FROM cine.Movie WHERE movieId=:id"), {"id": movie_id})
    return redirect(url_for("main.admin_movies"))


# Admin: users management (placeholder list)
@main_bp.route("/admin/users")
@require_role("Admin")
def admin_users():
    with current_app.db_engine.connect() as conn:
        rows = conn.execute(text(
            """
            SELECT u.userId, u.email, r.roleName, u.status, u.createdAt
            FROM cine.[User] u JOIN cine.Role r ON r.roleId = u.roleId
            ORDER BY u.userId DESC
            """
        ))
        users = [dict(r._mapping) for r in rows]
    return render_template("admin_users.html", users=users)
    # Remove DB health/config endpoints in UI-only mode


# API Endpoints for Content-based Recommendations
@main_bp.route("/api/related-movies/<int:movie_id>")
def api_related_movies(movie_id: int):
    """
    API endpoint để lấy phim liên quan
    
    Args:
        movie_id: ID của phim đang xem
        
    Returns:
        JSON response với danh sách phim liên quan
    """
    try:
        # Tạo recommender instance
        recommender = ContentBasedRecommender(current_app.db_engine)
        
        # Lấy phim liên quan
        related_movies = recommender.get_related_movies_hybrid(movie_id, 6)
        
        return jsonify({
            "success": True,
            "movie_id": movie_id,
            "related_movies": related_movies,
            "count": len(related_movies)
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "movie_id": movie_id,
            "related_movies": []
        }), 500

@main_bp.route("/api/movie-info/<int:movie_id>")
def api_movie_info(movie_id: int):
    """
    API endpoint để lấy thông tin chi tiết phim
    
    Args:
        movie_id: ID của phim
        
    Returns:
        JSON response với thông tin phim
    """
    try:
        # Tạo recommender instance
        recommender = ContentBasedRecommender(current_app.db_engine)
        
        # Lấy thông tin phim
        movie_info = recommender.get_movie_info(movie_id)
        
        if movie_info:
            return jsonify({
                "success": True,
                "movie": movie_info
            })
        else:
            return jsonify({
                "success": False,
                "error": "Movie not found",
                "movie_id": movie_id
            }), 404
            
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "movie_id": movie_id
        }), 500

@main_bp.route("/api/similarity/<int:movie_id1>/<int:movie_id2>")
def api_similarity(movie_id1: int, movie_id2: int):
    """
    API endpoint để lấy điểm similarity giữa 2 phim
    
    Args:
        movie_id1: ID phim thứ nhất
        movie_id2: ID phim thứ hai
        
    Returns:
        JSON response với điểm similarity
    """
    try:
        # Tạo recommender instance
        recommender = ContentBasedRecommender(current_app.db_engine)
        
        # Lấy điểm similarity
        similarity = recommender.get_similarity_score(movie_id1, movie_id2)
        
        return jsonify({
            "success": True,
            "movie_id1": movie_id1,
            "movie_id2": movie_id2,
            "similarity": similarity
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "movie_id1": movie_id1,
            "movie_id2": movie_id2
        }), 500

@main_bp.route("/api/recommendation-stats")
def api_recommendation_stats():
    """
    API endpoint để lấy thống kê về hệ thống gợi ý
    
    Returns:
        JSON response với thống kê
    """
    try:
        # Tạo recommender instance
        recommender = ContentBasedRecommender(current_app.db_engine)
        
        # Lấy thống kê
        stats = recommender.get_statistics()
        has_data = recommender.check_similarity_data_exists()
        
        return jsonify({
            "success": True,
            "has_similarity_data": has_data,
            "statistics": stats
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

# DB health endpoints removed for UI-only build


