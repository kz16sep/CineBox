from flask import Blueprint, render_template, request, redirect, url_for, current_app
from sqlalchemy import text


main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def home():
    # Lấy danh sách phim từ DB bằng engine (odbc_connect)
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
    return render_template("home.html", trending=trending, recommended=trending)


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        return redirect(url_for("main.home"))
    return render_template("login.html")


@main_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        return redirect(url_for("main.login"))
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
    # Remove DB health/config endpoints in UI-only mode


# DB health endpoints removed for UI-only build


