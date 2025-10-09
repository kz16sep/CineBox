from flask import Blueprint, render_template, request, redirect, url_for, current_app, session, jsonify
from sqlalchemy import text
from content_based_recommender import ContentBasedRecommender


main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def home():
    # Lấy danh sách phim từ DB bằng engine (odbc_connect); nếu chưa đăng nhập, chuyển tới form đăng nhập
    if not session.get("user_id"):
        return redirect(url_for("main.login"))
    
    # Trending movies (phim phổ biến)
    try:
        with current_app.db_engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT TOP 12 movieId, title, posterUrl, backdropUrl, overview FROM cine.Movie ORDER BY viewCount DESC, movieId DESC"
            )).mappings().all()
            trending = [
                {
                    "id": r["movieId"],
                    "title": r["title"],
                    "poster": r.get("posterUrl") or "/static/img/dune2.jpg",
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                }
                for r in rows
            ]
    except Exception:
        trending = []
    
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
        personal_recommendations = trending
    
    if not trending:
        # Fallback demo data to avoid empty list errors in templates
        trending = [
            {
                "id": 1,
                "title": "Hành Tinh Cát: Phần 2",
                "poster": "/static/img/dune2.jpg",
                "backdrop": "/static/img/dune2_backdrop.jpg",
                "description": "Paul và số phận trên Arrakis...",
            },
            {
                "id": 2,
                "title": "Doctor Strange",
                "poster": "/static/img/doctorstrange.jpg",
                "backdrop": "/static/img/doctorstrange_backdrop.jpg",
                "description": "Bác sĩ Stephen Strange và phép thuật...",
            },
        ]
    
    return render_template("home.html", 
                         trending=trending, 
                         recommended=personal_recommendations)


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with current_app.db_engine.connect() as conn:
            row = conn.execute(text(
                """
                SELECT TOP 1 u.userId, u.email, r.roleName
                FROM cine.Account a
                JOIN cine.[User] u ON u.userId = a.userId
                JOIN cine.Role r ON r.roleId = u.roleId
                WHERE (
                    a.username = :u OR u.email = :u
                ) AND a.passwordHash = HASHBYTES('SHA2_256', CONVERT(VARBINARY(512), :p))
                """
            ), {"u": username, "p": password}).mappings().first()
        if row:
            session["user_id"] = int(row["userId"])
            session["role"] = row["roleName"]
            session["username"] = username
            session["email"] = row["email"]
            return redirect(url_for("main.home"))
        error = "Sai tài khoản hoặc mật khẩu"
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
        r = conn.execute(text(
            "SELECT movieId, title, releaseYear, posterUrl, backdropUrl, overview FROM cine.Movie WHERE movieId=:id"
        ), {"id": movie_id}).mappings().first()
    if not r:
        return redirect(url_for("main.home"))
    
    movie = {
        "id": r["movieId"],
        "title": r["title"],
        "year": r.get("releaseYear"),
        "duration": "",
        "genres": [],
        "rating": 0,
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
        
        # Lấy phim liên quan với hybrid approach
        related_movies = recommender.get_related_movies_hybrid(movie_id, 6)
        
        # Format data cho template
        related = [
            {
                "id": movie["movieId"],
                "title": movie["title"],
                "poster": movie["posterUrl"],
                "similarity": movie["similarity"],
                "overview": movie.get("overview", "")
            }
            for movie in related_movies
        ]
        
    except Exception as e:
        print(f"Error getting related movies: {e}")
        related = []
    
    return render_template("detail.html", movie=movie, related=related)


@main_bp.route("/watch/<int:movie_id>")
def watch(movie_id: int):
    with current_app.db_engine.connect() as conn:
        r = conn.execute(text(
            "SELECT movieId, title, posterUrl, backdropUrl FROM cine.Movie WHERE movieId=:id"
        ), {"id": movie_id}).mappings().first()
    if not r:
        return redirect(url_for("main.home"))
    movie = {
        "id": r["movieId"],
        "title": r["title"],
        "poster": r.get("posterUrl") or "/static/img/dune2.jpg",
        "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
        "sources": [{"label": "720p", "url": "https://www.w3schools.com/html/movie.mp4"}],
    }
    return render_template("watch.html", movie=movie)


@main_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.home"))


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
            conn.execute(text(
                """
                INSERT INTO cine.Movie(title, releaseYear, country, overview, director, cast, durationMin, imdbRating, trailerUrl, posterUrl, backdropUrl, viewCount)
                VALUES (:title, :releaseYear, :country, :overview, :director, :cast, :durationMin, :imdbRating, :trailerUrl, :posterUrl, :backdropUrl, :viewCount)
                """
            ), data)
        return redirect(url_for("main.admin_movies"))
    return render_template("admin_movie_form.html", movie=None)


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
            conn.execute(text(
                """
                UPDATE cine.Movie
                SET title=:title, releaseYear=:releaseYear, country=:country, overview=:overview,
                    director=:director, cast=:cast, durationMin=:durationMin, imdbRating=:imdbRating, trailerUrl=:trailerUrl,
                    posterUrl=:posterUrl, backdropUrl=:backdropUrl, viewCount=:viewCount
                WHERE movieId=:movieId
                """
            ), data)
        return redirect(url_for("main.admin_movies"))
    with current_app.db_engine.connect() as conn:
        r = conn.execute(text("SELECT * FROM cine.Movie WHERE movieId=:id"), {"id": movie_id}).mappings().first()
    return render_template("admin_movie_form.html", movie=r)


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


