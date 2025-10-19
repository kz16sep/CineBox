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


@main_bp.route("/")
def home():
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
                    FROM cine.Movie m
                    JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                    JOIN cine.Genre g ON mg.genreId = g.genreId
                    WHERE g.name = :genre
                    ORDER BY m.createdAt DESC, m.movieId DESC
                """), {"genre": genre_filter}).mappings().all()
            else:
                # Lấy phim mới nhất tất cả thể loại
                rows = conn.execute(text(
                    "SELECT TOP 12 movieId, title, posterUrl, backdropUrl, overview, createdAt FROM cine.Movie ORDER BY createdAt DESC, movieId DESC"
                )).mappings().all()
            
            latest_movies = [
                {
                    "id": r["movieId"],
                    "title": r["title"],
                    "poster": r.get("posterUrl") if r.get("posterUrl") and r.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={r['title'][:20].replace(' ', '+')}",
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                    "createdAt": r.get("createdAt"),
                }
                for r in rows
            ]
            
            # Tạo carousel_movies từ 6 phim mới nhất (luôn lấy từ tất cả phim, không phụ thuộc genre)
            carousel_rows = conn.execute(text(
                "SELECT TOP 6 movieId, title, posterUrl, backdropUrl, overview, createdAt FROM cine.Movie ORDER BY createdAt DESC, movieId DESC"
            )).mappings().all()
            
            carousel_movies = [
                {
                    "id": r["movieId"],
                    "title": r["title"],
                    "poster": r.get("posterUrl") if r.get("posterUrl") and r.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={r['title'][:20].replace(' ', '+')}",
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                    "createdAt": r.get("createdAt"),
                }
                for r in carousel_rows
            ]
    except Exception:
        latest_movies = []
        carousel_movies = []
    
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
                        "poster": row.get("posterUrl") if row.get("posterUrl") and row.get("posterUrl") != "1" else "/static/img/dune2.jpg",
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
                print(f"Debug - Getting movies for genre '{genre_filter}', page {page}, offset {offset}, per_page {per_page}")
                all_rows = conn.execute(text("""
                    SELECT movieId, title, posterUrl, backdropUrl, overview, releaseYear
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
                    "poster": r.get("posterUrl") if r.get("posterUrl") and r.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={r['title'][:20].replace(' ', '+')}",
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                    "year": r.get("releaseYear")
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
    print(f"Debug - Final all_movies length: {len(all_movies) if all_movies else 0}")
    
    return render_template("home.html", 
                         latest_movies=latest_movies,  # Phim mới nhất (12 phim, không phân trang)
                         carousel_movies=carousel_movies,  # Carousel phim mới nhất (6 phim)
                         recommended=personal_recommendations,  # Phim đề xuất
                         all_movies=all_movies,  # Tất cả phim (có phân trang)
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
        "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
        "description": r.get("overview") or "",
    }
    
    # CONTENT-BASED: Phim liên quan sử dụng ContentBasedRecommender
    related = []
    related_pagination = None
    try:
        # Tạo recommender instance
        recommender = ContentBasedRecommender(current_app.db_engine)
        
        # Lấy tất cả phim liên quan từ model AI (không giới hạn)
        all_related_movies = recommender.get_related_movies(movie_id, limit=100)  # Lấy nhiều hơn để phân trang
        
        # Tính toán pagination
        total_related = len(all_related_movies)
        total_pages = (total_related + related_per_page - 1) // related_per_page
        offset = (related_page - 1) * related_per_page
        
        # Lấy phim cho trang hiện tại
        related_movies = all_related_movies[offset:offset + related_per_page]
        
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
        
        # Tạo pagination cho related movies
        related_pagination = {
            "page": related_page,
            "per_page": related_per_page,
            "total": total_related,
            "pages": total_pages,
            "has_prev": related_page > 1,
            "has_next": related_page < total_pages,
            "prev_num": related_page - 1 if related_page > 1 else None,
            "next_num": related_page + 1 if related_page < total_pages else None
        }
    except Exception as e:
        print(f"Error getting related movies: {e}")
        # Fallback: lấy phim ngẫu nhiên
        try:
            with current_app.db_engine.connect() as conn:
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
                    "posterUrl": row.get("posterUrl") if row.get("posterUrl") and row.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={row['title'][:20].replace(' ', '+')}",
                    "similarity": 0.0,
                    "overview": "",
                    "releaseYear": row.get("releaseYear")
                }
                for row in fallback_rows
            ]
        except Exception as fallback_error:
            print(f"Fallback error: {fallback_error}")
            related = []
            related_pagination = None
    
    return render_template("detail.html", movie=movie, related=related, related_pagination=related_pagination, movie_id=movie_id)


@main_bp.route("/watch/<int:movie_id>")
def watch(movie_id: int):
    # Kiểm tra xem có phải trailer không
    is_trailer = request.args.get('type') == 'trailer'
    
    with current_app.db_engine.connect() as conn:
        # Lấy thông tin phim chính
        r = conn.execute(text(
            "SELECT movieId, title, posterUrl, backdropUrl, releaseYear, overview, trailerUrl FROM cine.Movie WHERE movieId=:id"
        ), {"id": movie_id}).mappings().first()
        
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
                "title": movie["title"],
                "posterUrl": movie["posterUrl"],
                "similarity": movie.get("similarity", 0.0),
                "releaseYear": movie.get("releaseYear")
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
                    "title": row["title"],
                    "posterUrl": row.get("posterUrl") if row.get("posterUrl") and row.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={row['title'][:20].replace(' ', '+')}",
                    "releaseYear": row.get("releaseYear"),
                    "similarity": 0.0
                }
                for row in fallback_rows
            ]
        except Exception as fallback_error:
            print(f"Fallback error: {fallback_error}")
            # Fallback cuối cùng - tạo dữ liệu demo
            related_movies = [
                {
                    "movieId": 1,
                    "title": "Demo Movie 1",
                    "posterUrl": "https://dummyimage.com/300x450/2c3e50/ecf0f1&text=Demo+1",
                    "releaseYear": 2023,
                    "similarity": 0.0
                },
                {
                    "movieId": 2,
                    "title": "Demo Movie 2", 
                    "posterUrl": "https://dummyimage.com/300x450/2c3e50/ecf0f1&text=Demo+2",
                    "releaseYear": 2023,
                    "similarity": 0.0
                }
            ]
    
    return render_template("watch.html", 
                         movie=movie, 
                         related_movies=related_movies)


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
                    FROM [cine].[WatchList] wl
                    JOIN [cine].[Movie] m ON wl.movieId = m.movieId
                    WHERE wl.userId = :user_id AND m.title LIKE :search
                """), {"user_id": user_id, "search": f"%{watchlist_search}%"}).scalar()
                
                watchlist_offset = (watchlist_page - 1) * per_page
                watchlist = conn.execute(text("""
                    SELECT m.movieId, m.title, m.posterUrl, m.releaseYear, wl.addedAt
                    FROM [cine].[WatchList] wl
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
                    SELECT COUNT(*) FROM [cine].[WatchList] WHERE userId = :user_id
                """), {"user_id": user_id}).scalar()
                
                watchlist_offset = (watchlist_page - 1) * per_page
                watchlist = conn.execute(text("""
                SELECT m.movieId, m.title, m.posterUrl, m.releaseYear, wl.addedAt
                FROM [cine].[WatchList] wl
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
                    FROM [cine].[WatchList] wl
                    JOIN [cine].[Movie] m ON wl.movieId = m.movieId
                    WHERE wl.userId = :user_id AND m.title LIKE :search
                """), {"user_id": user_id, "search": f"%{search_query}%"}).scalar()
                
                watchlist_offset = (page - 1) * per_page
                watchlist = conn.execute(text("""
                    SELECT m.movieId, m.title, m.posterUrl, m.releaseYear, wl.addedAt
                    FROM [cine].[WatchList] wl
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
                    SELECT COUNT(*) FROM [cine].[WatchList] WHERE userId = :user_id
                """), {"user_id": user_id}).scalar()
                
                watchlist_offset = (page - 1) * per_page
                watchlist = conn.execute(text("""
                    SELECT m.movieId, m.title, m.posterUrl, m.releaseYear, wl.addedAt
                    FROM [cine].[WatchList] wl
                    JOIN [cine].[Movie] m ON wl.movieId = m.movieId
                    WHERE wl.userId = :user_id
                    ORDER BY wl.addedAt DESC
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """), {"user_id": user_id, "offset": watchlist_offset, "per_page": per_page}).mappings().all()
            
            # Tạo pagination
            watchlist_pages = (watchlist_total + per_page - 1) // per_page
            pagination = {
                "page": page,
                "per_page": per_page,
                "total": watchlist_total,
                "pages": watchlist_pages,
                "has_prev": page > 1,
                "has_next": page < watchlist_pages,
                "prev_num": page - 1 if page > 1 else None,
                "next_num": page + 1 if page < watchlist_pages else None
            }
            
            # Format data
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
            
            # Lấy kết quả tìm kiếm
            movies = conn.execute(text("""
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
                    "poster": r.get("posterUrl") if r.get("posterUrl") and r.get("posterUrl") != "1" else f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={r['title'][:20].replace(' ', '+')}",
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                    "year": r.get("releaseYear")
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
        return render_template('search.html', 
                             query=query, 
                             movies=[], 
                             pagination=None,
                             total_results=0,
                             error=str(e))


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
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


