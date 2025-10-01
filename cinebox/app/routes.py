from flask import Blueprint, render_template, request, redirect, url_for


main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def home():
    tr_view = [
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
    rec_view = tr_view
    return render_template("home.html", trending=tr_view, recommended=rec_view)


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
    movie = {
        "id": movie_id,
        "title": "Hành Tinh Cát: Phần 2",
        "year": 2024,
        "duration": "",
        "genres": ["Sci-Fi", "Adventure"],
        "rating": 0,
        "poster": "/static/img/dune2.jpg",
        "backdrop": "/static/img/dune2_backdrop.jpg",
        "description": "Paul và số phận trên Arrakis...",
        "sources": [{"label": "720p", "url": "https://www.w3schools.com/html/movie.mp4"}],
    }
    related = [
        {"id": 2, "title": "Doctor Strange", "poster": "/static/img/doctorstrange.jpg"}
    ]
    return render_template("detail.html", movie=movie, related=related)


@main_bp.route("/watch/<int:movie_id>")
def watch(movie_id: int):
    movie = {
        "id": movie_id,
        "title": "Hành Tinh Cát: Phần 2",
        "poster": "/static/img/dune2.jpg",
        "backdrop": "/static/img/dune2_backdrop.jpg",
        "sources": [{"label": "720p", "url": "https://www.w3schools.com/html/movie.mp4"}],
    }
    return render_template("watch.html", movie=movie)


# DB health endpoints removed for UI-only build


