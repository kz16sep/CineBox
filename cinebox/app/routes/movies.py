"""
Movie browsing routes: home, detail, watch, search, genre, all movies
"""

import time
import random
import threading
import re
import requests
from flask import render_template, request, redirect, url_for, session, current_app, jsonify
from sqlalchemy import text
from . import main_bp
from .decorators import login_required
from .common import (
    get_poster_or_dummy,
    latest_movies_cache,
    carousel_movies_cache,
    trending_cache,
    TRENDING_TIME_WINDOW_DAYS,
    content_recommender,
    enhanced_cf_recommender,
    get_watched_movie_ids,
    get_cold_start_recommendations,
    create_rating_based_recommendations
)
import sys
import os

# Add cinebox directory to path for imports
_cinebox_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _cinebox_dir not in sys.path:
    sys.path.insert(0, _cinebox_dir)

from app.helpers.movie_query_helpers import get_movie_rating_stats, get_movies_genres
from app.helpers.recommendation_helpers import hybrid_recommendations
from app.helpers.sql_helpers import validate_limit, safe_top_clause

HOME_SECTION_LIMIT = 12
CAROUSEL_LIMIT = 6
HOME_CACHE_REFRESH_INTERVAL = 300

# TMDB API configuration
TMDB_API_KEY = "410065906e9552ec1e24efe8c5393791"
TMDB_BASE_URL = "https://api.themoviedb.org/3"

def extract_tmdb_id_from_url(url):
    """Extract TMDB movie ID from TMDB URL"""
    if not url:
        return None
    # Pattern: https://www.themoviedb.org/movie/{id}
    match = re.search(r'themoviedb\.org/movie/(\d+)', url)
    if match:
        return int(match.group(1))
    return None

def extract_video_key_from_url(url):
    """Extract video key from YouTube/Vimeo URL for embedding"""
    if not url:
        return None, None
    
    # YouTube patterns
    youtube_patterns = [
        r'youtube\.com/watch\?v=([a-zA-Z0-9_-]+)',
        r'youtu\.be/([a-zA-Z0-9_-]+)',
        r'youtube\.com/embed/([a-zA-Z0-9_-]+)'
    ]
    
    for pattern in youtube_patterns:
        match = re.search(pattern, url)
        if match:
            return 'youtube', match.group(1)
    
    # Vimeo patterns
    vimeo_patterns = [
        r'vimeo\.com/(\d+)',
        r'player\.vimeo\.com/video/(\d+)'
    ]
    
    for pattern in vimeo_patterns:
        match = re.search(pattern, url)
        if match:
            return 'vimeo', match.group(1)
    
    return None, None


def build_highlight_snippet(text, keyword, radius=80):
    """Return snippet of text around keyword with highlight span"""
    if not text or not keyword:
        return ""
    
    lowered = text.lower()
    key_lower = keyword.lower()
    index = lowered.find(key_lower)
    
    if index == -1:
        snippet = text[: radius * 2]
        return snippet.strip()
    
    start = max(0, index - radius)
    end = min(len(text), index + len(keyword) + radius)
    snippet = text[start:end].strip()
    
    # Add ellipsis if trimmed
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    
    # Highlight keyword
    regex = re.compile(re.escape(keyword), re.IGNORECASE)
    snippet = regex.sub(lambda m: f"<mark>{m.group(0)}</mark>", snippet)
    return snippet

def get_tmdb_videos(tmdb_id, is_trailer=False):
    """Get videos from TMDB API for a movie"""
    try:
        url = f"{TMDB_BASE_URL}/movie/{tmdb_id}/videos"
        params = {'api_key': TMDB_API_KEY, 'language': 'en-US'}
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            results = data.get('results', [])
            
            # Chỉ lấy video từ YouTube hoặc Vimeo
            youtube_videos = [v for v in results if v.get('site') == 'YouTube']
            vimeo_videos = [v for v in results if v.get('site') == 'Vimeo']
            all_videos = youtube_videos + vimeo_videos
            
            if not all_videos:
                return None
            
            if is_trailer:
                # Khi xem trailer: Ưu tiên Trailer > Teaser > Other
                trailers = [v for v in all_videos if v.get('type') == 'Trailer']
                teasers = [v for v in all_videos if v.get('type') == 'Teaser']
                others = [v for v in all_videos if v.get('type') not in ['Trailer', 'Teaser']]
                
                # Ưu tiên YouTube
                youtube_trailers = [v for v in trailers if v.get('site') == 'YouTube']
                youtube_teasers = [v for v in teasers if v.get('site') == 'YouTube']
                youtube_others = [v for v in others if v.get('site') == 'YouTube']
                
                if youtube_trailers:
                    return youtube_trailers[0]
                elif youtube_teasers:
                    return youtube_teasers[0]
                elif youtube_others:
                    return youtube_others[0]
                elif trailers:
                    return trailers[0]
                elif teasers:
                    return teasers[0]
                elif others:
                    return others[0]
            else:
                # Khi xem phim: Ưu tiên video chính (không phải Trailer/Teaser) > Trailer > Teaser
                main_videos = [v for v in all_videos if v.get('type') not in ['Trailer', 'Teaser']]
                trailers = [v for v in all_videos if v.get('type') == 'Trailer']
                teasers = [v for v in all_videos if v.get('type') == 'Teaser']
                
                # Ưu tiên YouTube
                youtube_main = [v for v in main_videos if v.get('site') == 'YouTube']
                youtube_trailers = [v for v in trailers if v.get('site') == 'YouTube']
                youtube_teasers = [v for v in teasers if v.get('site') == 'YouTube']
                
                if youtube_main:
                    return youtube_main[0]
                elif youtube_trailers:
                    return youtube_trailers[0]
                elif youtube_teasers:
                    return youtube_teasers[0]
                elif main_videos:
                    return main_videos[0]
                elif trailers:
                    return trailers[0]
                elif teasers:
                    return teasers[0]
            
            return None
    except Exception as e:
        current_app.logger.error(f"Error fetching TMDB videos: {e}")
        return None


@main_bp.route("/")
def home():
    """Home page with movies list"""
    # Kiểm tra đăng nhập
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("main.login"))
    
    # Tối ưu onboarding check - chỉ check 1 lần và lưu vào session
    if not session.get("onboarding_checked"):
        try:
            with current_app.db_engine.connect() as conn:
                has_completed = conn.execute(text("""
                    SELECT COALESCE(hasCompletedOnboarding, 0) FROM cine.[User] WHERE userId = :user_id
                """), {"user_id": user_id}).scalar()
                
                session["onboarding_checked"] = True
                session["onboarding_completed"] = bool(has_completed)
                
                if not has_completed:
                    return redirect(url_for('main.onboarding'))
        except Exception as e:
            current_app.logger.error(f"Error checking onboarding status: {e}")
            return redirect(url_for('main.onboarding'))
    elif not session.get("onboarding_completed"):
        # Đã check rồi nhưng chưa hoàn thành onboarding
        return redirect(url_for('main.onboarding'))
    
    # Lấy parameters
    page = request.args.get('page', 1, type=int)
    per_page = 10
    genre_filter = request.args.get('genre', '', type=str)
    search_query = request.args.get('q', '', type=str)
    sort_by = request.args.get('sort', 'newest', type=str)
    year_filter = request.args.get('year', '', type=str)
    
    # Latest movies với caching
    cache_key = f"latest_{genre_filter or 'all'}"
    current_time = time.time()
    latest_movies = []
    
    if (
        latest_movies_cache.get('data')
        and latest_movies_cache.get('key') == cache_key
        and latest_movies_cache.get('timestamp')
        and latest_movies_cache.get('ttl')
        and current_time - latest_movies_cache['timestamp'] < latest_movies_cache['ttl']
    ):
        latest_movies = latest_movies_cache['data']
    else:
        try:
            with current_app.db_engine.connect() as conn:
                if genre_filter:
                    rows = conn.execute(text(f"""
                        SELECT DISTINCT TOP {HOME_SECTION_LIMIT}
                            m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.createdAt, m.viewCount, m.releaseYear, m.country
                        FROM cine.Movie m
                        JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                        JOIN cine.Genre g ON mg.genreId = g.genreId
                        WHERE g.name = :genre
                        ORDER BY m.createdAt DESC, m.movieId DESC
                    """), {"genre": genre_filter}).mappings().all()
                else:
                    rows = conn.execute(text(f"""
                        SELECT TOP {HOME_SECTION_LIMIT} 
                            movieId, title, posterUrl, backdropUrl, overview, createdAt, viewCount, releaseYear, country
                        FROM cine.Movie
                        ORDER BY createdAt DESC, movieId DESC
                    """)).mappings().all()
            
            latest_movies = [
                {
                    "id": r["movieId"],
                    "title": r["title"],
                    "poster": get_poster_or_dummy(r.get("posterUrl"), r["title"]),
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                    "createdAt": r.get("createdAt"),
                    "avgRating": 0,
                    "ratingCount": 0,
                    "viewCount": r.get("viewCount", 0) or 0,
                    "genres": "",
                    "releaseYear": r.get("releaseYear"),
                    "country": r.get("country"),
                }
                for r in rows
            ]
            
            # Update cache
            latest_movies_cache['data'] = latest_movies
            latest_movies_cache['key'] = cache_key
            latest_movies_cache['timestamp'] = current_time
            
            # Keep carousel cache in sync for default home
            if not genre_filter:
                carousel_movies_cache['data'] = latest_movies[:CAROUSEL_LIMIT]
                carousel_movies_cache['timestamp'] = current_time
        except Exception as e:
            current_app.logger.error(f"Error loading latest movies: {e}", exc_info=True)
            latest_movies = []
    
    # Debug log
    current_app.logger.info(f"Latest movies count: {len(latest_movies)}")
    if latest_movies:
        current_app.logger.info(f"First movie: {latest_movies[0]['title']}")
    else:
        current_app.logger.warning("No latest movies found!")
    
    # Carousel movies reuse latest data when possible
    if not genre_filter and latest_movies:
        carousel_movies = latest_movies[:CAROUSEL_LIMIT]
    elif (
        carousel_movies_cache.get('data')
        and carousel_movies_cache.get('timestamp')
        and current_time - carousel_movies_cache['timestamp'] < carousel_movies_cache['ttl']
    ):
        carousel_movies = carousel_movies_cache['data']
    else:
        try:
            with current_app.db_engine.connect() as conn:
                rows = conn.execute(text(f"""
                    SELECT TOP {CAROUSEL_LIMIT} 
                        movieId, title, posterUrl, backdropUrl, overview
                    FROM cine.Movie
                    ORDER BY createdAt DESC, movieId DESC
                """)).mappings().all()
                
                carousel_movies = [
                    {
                        "id": r["movieId"],
                        "title": r["title"],
                        "poster": get_poster_or_dummy(r.get("posterUrl"), r["title"]),
                        "backdrop": r.get("backdropUrl") or "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='1920' height='1080'%3E%3Cdefs%3E%3ClinearGradient id='grad' x1='0%25' y1='0%25' x2='100%25' y2='100%25'%3E%3Cstop offset='0%25' style='stop-color:%231e3c72;stop-opacity:1' /%3E%3Cstop offset='100%25' style='stop-color:%232a5298;stop-opacity:1' /%3E%3C/linearGradient%3E%3C/defs%3E%3Crect width='100%25' height='100%25' fill='url(%23grad)' /%3E%3C/svg%3E",
                        "description": (r.get("overview") or "")[:160]
                    }
                    for r in rows
                ]
                
                carousel_movies_cache['data'] = carousel_movies
                carousel_movies_cache['timestamp'] = current_time
        except Exception as e:
            current_app.logger.error(f"Error loading carousel movies: {e}")
            carousel_movies = []
    
    # Debug log for carousel
    current_app.logger.info(f"Carousel movies count: {len(carousel_movies)}")
    if carousel_movies:
        current_app.logger.info(f"First carousel movie: {carousel_movies[0]['title']}")
    else:
        current_app.logger.warning("No carousel movies found!")
    
    # All movies (lazy load - chỉ load đầy đủ khi có filter hoặc search)
    # Nếu không có filter, vẫn load 12 phim mới nhất để hiển thị section "Tất cả phim"
    all_movies = []
    pagination = None
    year_list = []
    genre_list = []
    
    # Nếu không có filter, tái sử dụng dữ liệu phim mới nhất
    if not genre_filter and not search_query and not year_filter:
        all_movies = [
            {
                **movie,
                "year": movie.get("releaseYear"),
                "country": movie.get("country"),
            }
            for movie in latest_movies
        ]
    # Nếu có filter hoặc search, load đầy đủ với pagination
    elif genre_filter or search_query or year_filter:
        try:
            with current_app.db_engine.connect() as conn:
                # Build query với genre filter và search
                where_clauses = []
                params = {}
                
                if genre_filter:
                    where_clauses.append("EXISTS (SELECT 1 FROM cine.MovieGenre mg JOIN cine.Genre g ON mg.genreId = g.genreId WHERE mg.movieId = m.movieId AND g.name = :genre)")
                    params['genre'] = genre_filter
                
                if search_query:
                    where_clauses.append("m.title LIKE :search")
                    params['search'] = f"%{search_query}%"
                
                where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
                
                # Thêm year filter nếu có
                if year_filter:
                    where_clauses.append("m.releaseYear = :year")
                    params['year'] = int(year_filter)
                    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
                
                # Xây dựng ORDER BY clause (chỉ khi có genre filter hoặc search)
                if genre_filter or search_query:
                    if sort_by == 'ratings':
                        order_by_inner = 'avgRating DESC, ratingCount DESC, m.movieId DESC'
                        order_by_outer = 'avgRating DESC, ratingCount DESC'
                    else:
                        order_by_map = {
                            'newest': 'm.releaseYear DESC, m.movieId DESC',
                            'oldest': 'm.releaseYear ASC, m.movieId ASC',
                            'views': 'm.viewCount DESC, m.movieId DESC',
                            'title_asc': 'm.title ASC',
                            'title_desc': 'm.title DESC'
                        }
                        order_by_inner = order_by_map.get(sort_by, order_by_map['newest'])
                        order_by_outer = order_by_inner
                else:
                    order_by_inner = 'm.createdAt DESC, m.movieId DESC'
                    order_by_outer = order_by_inner
                
                # Count total
                count_query = f"SELECT COUNT(*) FROM cine.Movie m WHERE {where_sql}"
                total_count = conn.execute(text(count_query), params).scalar()
                
                # Get movies
                offset = (page - 1) * per_page
                
                if (genre_filter or search_query) and sort_by == 'ratings':
                    # Query với ratings
                    movies_query = f"""
                        SELECT m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.country, m.viewCount,
                               AVG(CAST(r.value AS FLOAT)) AS avgRating,
                               COUNT(r.value) AS ratingCount
                        FROM cine.Movie m
                        LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                        WHERE {where_sql}
                        GROUP BY m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.country, m.viewCount
                        ORDER BY {order_by_outer}
                        OFFSET :offset ROWS
                        FETCH NEXT :per_page ROWS ONLY
                    """
                else:
                    # Query không có ratings hoặc không có filter
                    movies_query = f"""
                        SELECT m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.country, m.viewCount
                        FROM cine.Movie m
                        WHERE {where_sql}
                        ORDER BY {order_by_inner}
                        OFFSET :offset ROWS
                        FETCH NEXT :per_page ROWS ONLY
                    """
                
                params['offset'] = offset
                params['per_page'] = per_page
                
                rows = conn.execute(text(movies_query), params).mappings().all()
                movie_ids = [r["movieId"] for r in rows]
                
                # Batch query ratings và genres
                if (genre_filter or search_query) and sort_by == 'ratings':
                    # Đã có ratings trong query
                    rating_stats = {}
                    for r in rows:
                        movie_id = r["movieId"]
                        rating_stats[movie_id] = {
                            "avgRating": round(float(r.get("avgRating") or 0), 2),
                            "ratingCount": int(r.get("ratingCount") or 0)
                        }
                else:
                    rating_stats = get_movie_rating_stats(movie_ids, current_app.db_engine)
                genres_dict = get_movies_genres(movie_ids, current_app.db_engine)
                
                all_movies = []
                for r in rows:
                    movie_id = r["movieId"]
                    stats = rating_stats.get(movie_id, {"avgRating": 0.0, "ratingCount": 0})
                    genres = genres_dict.get(movie_id, "")
                    
                    all_movies.append({
                        "id": movie_id,
                        "title": r["title"],
                        "poster": get_poster_or_dummy(r.get("posterUrl"), r["title"]),
                        "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                        "description": (r.get("overview") or "")[:160],
                        "year": r.get("releaseYear"),
                        "country": r.get("country"),
                        "avgRating": stats["avgRating"],
                        "ratingCount": stats["ratingCount"],
                        "viewCount": r.get("viewCount", 0) or 0,
                        "genres": genres.split(", ") if genres else []
                    })
                
                # Lấy danh sách năm và thể loại cho filter (chỉ khi có genre filter hoặc search)
                year_list = []
                genre_list = []
                if genre_filter or search_query:
                    try:
                        years = conn.execute(text("""
                            SELECT DISTINCT releaseYear 
                            FROM cine.Movie 
                            WHERE releaseYear IS NOT NULL 
                            ORDER BY releaseYear DESC
                        """)).fetchall()
                        year_list = [y[0] for y in years]
                        
                        genres_list = conn.execute(text("""
                            SELECT DISTINCT g.name 
                            FROM cine.Genre g
                            JOIN cine.MovieGenre mg ON g.genreId = mg.genreId
                            ORDER BY g.name
                        """)).fetchall()
                        genre_list = [g[0] for g in genres_list]
                    except Exception as e:
                        current_app.logger.error(f"Error loading filter lists: {e}")
                        year_list = []
                        genre_list = []
                
                total_pages = (total_count + per_page - 1) // per_page
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
        except Exception as e:
            current_app.logger.error(f"Error loading all movies: {e}", exc_info=True)
            all_movies = []
            pagination = None
            year_list = []
            genre_list = []
    
    personal_recommendations = []
    # Các section nặng sẽ được lazy load qua API
    trending_movies = []
    recent_watched = []
    
    # Combine tất cả movie_ids để batch query genres/ratings
    all_movie_ids_combined = set()
    if latest_movies:
        all_movie_ids_combined.update([m.get("id") for m in latest_movies if m.get("id")])
    if carousel_movies:
        all_movie_ids_combined.update([m.get("id") for m in carousel_movies if m.get("id")])
    if personal_recommendations:
        all_movie_ids_combined.update([m.get("id") for m in personal_recommendations if m.get("id")])
    if trending_movies:
        all_movie_ids_combined.update([m.get("id") for m in trending_movies if m.get("id")])
    if recent_watched:
        all_movie_ids_combined.update([m.get("id") for m in recent_watched if m.get("id")])
    if all_movies:
        all_movie_ids_combined.update([m.get("id") for m in all_movies if m.get("id")])
    
    # Batch query genres và ratings
    if all_movie_ids_combined:
        combined_movie_ids = list(all_movie_ids_combined)
        combined_genres_dict = get_movies_genres(combined_movie_ids, current_app.db_engine)
        combined_rating_stats = get_movie_rating_stats(combined_movie_ids, current_app.db_engine)
        
        # Update genres và ratings cho tất cả movies
        all_movies_to_update = []
        all_movies_to_update.extend([("latest", m) for m in latest_movies])
        all_movies_to_update.extend([("carousel", m) for m in carousel_movies])
        all_movies_to_update.extend([("personal", m) for m in personal_recommendations])
        all_movies_to_update.extend([("trending", m) for m in trending_movies])
        all_movies_to_update.extend([("recent", m) for m in recent_watched])
        all_movies_to_update.extend([("all", m) for m in all_movies])
        
        for section_type, movie in all_movies_to_update:
            movie_id = movie.get("id")
            if movie_id:
                if movie_id in combined_genres_dict:
                    movie["genres"] = combined_genres_dict[movie_id]
                elif not movie.get("genres"):
                    movie["genres"] = ""
                
                if section_type != "recent" and movie_id in combined_rating_stats:
                    stats = combined_rating_stats[movie_id]
                    movie["avgRating"] = stats.get("avgRating", movie.get("avgRating", 0))
                    movie["ratingCount"] = stats.get("ratingCount", movie.get("ratingCount", 0))
    
    # Get all genres for filter
    try:
        with current_app.db_engine.connect() as conn:
            all_genres = conn.execute(text("SELECT name FROM cine.Genre ORDER BY name")).fetchall()
            all_genres = [g[0] for g in all_genres]
    except Exception as e:
        current_app.logger.error(f"Error loading genres: {e}")
        all_genres = []
    
    return render_template(
        "home.html",
        latest_movies=latest_movies,
        carousel_movies=carousel_movies,
        all_movies=all_movies,
        pagination=pagination,
        genre_filter=genre_filter,
        search_query=search_query,
        sort_by=sort_by,
        year_filter=year_filter,
        year_list=year_list if 'year_list' in locals() else [],
        genre_list=genre_list if 'genre_list' in locals() else [],
        all_genres=all_genres
    )


def _refresh_latest_cache():
    """Precompute default latest/carousel cache."""
    try:
        with current_app.db_engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT TOP {HOME_SECTION_LIMIT}
                    movieId, title, posterUrl, backdropUrl, overview, createdAt, viewCount, releaseYear, country
                FROM cine.Movie
                ORDER BY createdAt DESC, movieId DESC
            """)).mappings().all()

        latest_movies = [
            {
                "id": r["movieId"],
                "title": r["title"],
                "poster": get_poster_or_dummy(r.get("posterUrl"), r["title"]),
                "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                "description": (r.get("overview") or "")[:160],
                "createdAt": r.get("createdAt"),
                "avgRating": 0,
                "ratingCount": 0,
                "viewCount": r.get("viewCount", 0) or 0,
                "genres": "",
                "releaseYear": r.get("releaseYear"),
                "country": r.get("country"),
            }
            for r in rows
        ]

        latest_movies_cache['data'] = latest_movies
        latest_movies_cache['key'] = 'latest_all'
        latest_movies_cache['timestamp'] = time.time()
        carousel_movies_cache['data'] = latest_movies[:CAROUSEL_LIMIT]
        carousel_movies_cache['timestamp'] = time.time()
        return latest_movies
    except Exception as e:
        current_app.logger.error(f"Error refreshing latest cache: {e}", exc_info=True)
        return latest_movies_cache.get('data') or []


def _load_trending_movies(limit=HOME_SECTION_LIMIT, force_refresh=False):
    """Load trending movies with caching."""
    limit = max(1, min(limit, HOME_SECTION_LIMIT))
    current_time = time.time()
    if (
        not force_refresh
        and trending_cache.get('data')
        and trending_cache.get('timestamp')
        and current_time - trending_cache['timestamp'] < trending_cache['ttl']
    ):
        return trending_cache['data'][:limit]

    try:
        with current_app.db_engine.connect() as conn:
            trending_rows = conn.execute(text(f"""
                WITH view_stats AS (
                    SELECT 
                        movieId,
                        COUNT(DISTINCT historyId) as view_count_recent,
                        COUNT(DISTINCT userId) as unique_viewers_recent
                    FROM cine.ViewHistory
                    WHERE startedAt >= DATEADD(day, -{TRENDING_TIME_WINDOW_DAYS}, GETDATE())
                    GROUP BY movieId
                ),
                rating_stats AS (
                    SELECT 
                        movieId,
                        COUNT(DISTINCT userId) as rating_count_recent,
                        AVG(CAST(value AS FLOAT)) as avg_rating_recent
                    FROM cine.Rating
                    WHERE ratedAt >= DATEADD(day, -{TRENDING_TIME_WINDOW_DAYS}, GETDATE())
                    GROUP BY movieId
                )
                SELECT TOP {HOME_SECTION_LIMIT}
                    m.movieId, 
                    m.title, 
                    m.posterUrl, 
                    m.releaseYear, 
                    m.country,
                    ISNULL(vs.view_count_recent, 0) as view_count_recent,
                    ISNULL(vs.unique_viewers_recent, 0) as unique_viewers_recent,
                    ISNULL(rs.rating_count_recent, 0) as rating_count_recent,
                    ISNULL(rs.avg_rating_recent, 0) as avg_rating_recent,
                    ISNULL(vs.view_count_recent, 0) as trending_score
                FROM cine.Movie m
                LEFT JOIN view_stats vs ON m.movieId = vs.movieId
                LEFT JOIN rating_stats rs ON m.movieId = rs.movieId
                WHERE vs.view_count_recent > 0
                ORDER BY 
                    trending_score DESC,
                    ISNULL(rs.avg_rating_recent, 0) DESC
            """)).mappings().all()

        trending_movies = [
            {
                "id": row.movieId,
                "title": row.title,
                "poster": get_poster_or_dummy(row.posterUrl, row.title),
                "releaseYear": row.releaseYear,
                "country": row.country,
                "ratingCount": row.rating_count_recent or 0,
                "avgRating": float(row.avg_rating_recent) if row.avg_rating_recent else 0.0,
                "viewCount": row.view_count_recent or 0,
                "uniqueViewers": row.unique_viewers_recent or 0,
                "trendingScore": row.trending_score or 0,
                "genres": "",
            }
            for row in trending_rows
        ]

        if len(trending_movies) < HOME_SECTION_LIMIT:
            with current_app.db_engine.connect() as conn:
                existing_ids = [m["id"] for m in trending_movies]
                placeholders = ','.join([f':id{i}' for i in range(len(existing_ids))]) if existing_ids else ''
                params = {f'id{i}': mid for i, mid in enumerate(existing_ids)}
                fallback_query = f"""
                    SELECT TOP {HOME_SECTION_LIMIT}
                        m.movieId, m.title, m.posterUrl, m.releaseYear, m.country
                    FROM cine.Movie m
                """
                if existing_ids:
                    fallback_query += f" WHERE m.movieId NOT IN ({placeholders})"
                fallback_query += " ORDER BY m.releaseYear DESC, m.movieId DESC"
                fallback_rows = conn.execute(text(fallback_query), params).mappings().all()
                for row in fallback_rows:
                    trending_movies.append({
                        "id": row.movieId,
                        "title": row.title,
                        "poster": get_poster_or_dummy(row.posterUrl, row.title),
                        "releaseYear": row.releaseYear,
                        "country": row.country,
                        "ratingCount": 0,
                        "avgRating": 0.0,
                        "viewCount": 0,
                        "genres": "",
                    })

        trending_cache['data'] = trending_movies[:HOME_SECTION_LIMIT]
        trending_cache['timestamp'] = time.time()
        return trending_cache['data'][:limit]
    except Exception as e:
        current_app.logger.error(f"Error loading trending movies: {e}", exc_info=True)
        cached = trending_cache.get('data') or []
        return cached[:limit]


def _load_recent_watched(user_id, limit=HOME_SECTION_LIMIT, use_cache=True):
    """Load recently watched movies for a user."""
    limit = max(1, min(limit, HOME_SECTION_LIMIT))
    cache_key_recent = f"recent_watched_{user_id}"
    current_time = time.time()

    if (
        use_cache
        and session.get(cache_key_recent)
        and session.get(f'{cache_key_recent}_time')
        and current_time - session.get(f'{cache_key_recent}_time', 0) < 300
    ):
        return session.get(cache_key_recent, [])[:limit]

    try:
        with current_app.db_engine.connect() as conn:
            # Lấy 12 phim đã xem mới nhất (loại bỏ trùng lặp, mỗi phim chỉ xuất hiện 1 lần)
            # Lấy theo lần xem mới nhất của mỗi phim
            history_rows = conn.execute(text(f"""
                WITH ranked_history AS (
                    SELECT 
                        vh.movieId,
                        vh.startedAt AS lastWatchedAt,
                        vh.finishedAt AS lastFinishedAt,
                        vh.progressSec AS lastProgressSec,
                        m.title,
                        m.posterUrl,
                        m.releaseYear,
                        m.durationMin,
                        COUNT(*) OVER (PARTITION BY vh.movieId) AS watchCount,
                        CASE WHEN vh.finishedAt IS NOT NULL THEN 1 ELSE 0 END AS isCompleted,
                        ROW_NUMBER() OVER (PARTITION BY vh.movieId ORDER BY vh.startedAt DESC) AS rn
                    FROM cine.ViewHistory vh
                    JOIN cine.Movie m ON vh.movieId = m.movieId
                    WHERE vh.userId = :user_id
                )
                SELECT TOP {limit}
                    movieId,
                    lastWatchedAt,
                    lastFinishedAt,
                    lastProgressSec,
                    title,
                    posterUrl,
                    releaseYear,
                    durationMin,
                    watchCount,
                    isCompleted
                FROM ranked_history
                WHERE rn = 1
                ORDER BY lastWatchedAt DESC
            """), {"user_id": user_id}).mappings().all()
    except Exception as e:
        current_app.logger.error(f"Error loading recent watched movies: {e}", exc_info=True)
        return []

    recent_watched = []
    for row in history_rows[:limit]:
        progress_percent = 0
        if row["durationMin"] and row["durationMin"] > 0 and row["lastProgressSec"]:
            progress_percent = min(100, (row["lastProgressSec"] / 60.0 / row["durationMin"]) * 100)
        recent_watched.append({
            "movieId": row["movieId"],
            "id": row["movieId"],
            "title": row["title"],
            "posterUrl": get_poster_or_dummy(row["posterUrl"], row["title"]),
            "poster": get_poster_or_dummy(row["posterUrl"], row["title"]),
            "releaseYear": row["releaseYear"],
            "genres": "",
            "lastWatchedAt": row["lastWatchedAt"],
            "lastFinishedAt": row["lastFinishedAt"],
            "watchCount": row.get("watchCount") or 1,
            "isCompleted": bool(row["isCompleted"]),
            "progressPercent": round(progress_percent, 1)
        })

    if use_cache:
        session[cache_key_recent] = recent_watched
        session[f'{cache_key_recent}_time'] = current_time

    return recent_watched[:limit]


@main_bp.route("/api/home/trending")
@login_required
def api_home_trending():
    limit = validate_limit(request.args.get('limit', HOME_SECTION_LIMIT, type=int))
    movies = _load_trending_movies(limit=limit)
    return jsonify({"success": True, "movies": movies})


@main_bp.route("/api/home/recent-watched")
@login_required
def api_home_recent_watched():
    limit = validate_limit(request.args.get('limit', HOME_SECTION_LIMIT, type=int))
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "Chưa đăng nhập"}), 401
    # Không dùng cache để luôn cập nhật mỗi lần vào trang chủ
    movies = _load_recent_watched(user_id, limit=limit, use_cache=False)
    return jsonify({"success": True, "movies": movies})


def precompute_homepage_caches():
    """Background job to refresh expensive caches."""
    latest_data = _refresh_latest_cache()
    if latest_data:
        current_app.logger.info("Latest movies cache warmed with %d items", len(latest_data))
    trending_data = _load_trending_movies(force_refresh=True)
    if trending_data:
        current_app.logger.info("Trending movies cache warmed with %d items", len(trending_data))


@main_bp.route("/movie/<int:movie_id>")
@login_required
def detail(movie_id: int):
    """Movie detail page"""
    related_page = request.args.get('related_page', 1, type=int)
    related_per_page = 6
    
    with current_app.db_engine.connect() as conn:
        r = conn.execute(text(
            "SELECT movieId, title, releaseYear, posterUrl, backdropUrl, overview, director, cast, viewCount FROM cine.Movie WHERE movieId=:id"
        ), {"id": movie_id}).mappings().first()
        
    if not r:
        return redirect(url_for("main.home"))
    
    # Get genres
    with current_app.db_engine.connect() as conn:
        genres_query = text("""
            SELECT g.name
            FROM cine.MovieGenre mg
            JOIN cine.Genre g ON mg.genreId = g.genreId
            WHERE mg.movieId = :movie_id
        """)
        genres_result = conn.execute(genres_query, {"movie_id": movie_id}).fetchall()
        genres = [{"name": genre[0], "slug": genre[0].lower().replace(' ', '-')} for genre in genres_result]
    
    # Parse director and cast (có thể là string hoặc list)
    director = r.get("director")
    if director and director != '1' and director.strip():
        director = director.strip()
    else:
        director = None
    
    cast = r.get("cast")
    if cast and cast != '1' and cast.strip():
        # Cast có thể là string phân cách bởi dấu phẩy
        cast_list = [c.strip() for c in cast.split(',') if c.strip()]
        cast = cast_list if cast_list else None
    else:
        cast = None
    
    movie = {
        "id": r["movieId"],
        "title": r["title"],
        "year": r.get("releaseYear"),
        "duration": "120 phút",
        "genres": genres,
        "rating": 5.0,
        "poster": get_poster_or_dummy(r.get("posterUrl"), r["title"]),
        "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
        "description": r.get("overview") or "",
        "director": director,
        "cast": cast,
        "viewCount": r.get("viewCount", 0) or 0,
    }
    
    # Get related movies
    related_movies = []
    try:
        # Lấy top 20 theo similarity rồi chọn ngẫu nhiên 8 phim để hiển thị
        target_limit = 20
        display_limit = 8

        if content_recommender:
            related_movies_raw = content_recommender.get_related_movies(movie_id, limit=target_limit)
        else:
            # Fallback: khởi tạo mới nếu chưa có (sửa đúng module)
            from recommenders.content_based_recommender import ContentBasedRecommender
            recommender = ContentBasedRecommender(current_app.db_engine)
            related_movies_raw = recommender.get_related_movies(movie_id, limit=target_limit)
        
        sampled_movies = random.sample(
            related_movies_raw,
            k=min(display_limit, len(related_movies_raw))
        ) if related_movies_raw else []
        
        related_movies = [
            {
                "movieId": movie["movieId"],
                "id": movie["movieId"],
                "title": movie["title"],
                "posterUrl": get_poster_or_dummy(movie.get("posterUrl"), movie["title"]),
                "releaseYear": movie.get("releaseYear"),
                "country": movie.get("country"),
                "similarity": movie.get("similarity", 0),
                "genres": movie.get("genres", "")
            }
            for movie in sampled_movies
        ]
    except Exception as e:
        current_app.logger.error(f"Error getting related movies: {e}")
        related_movies = []
    
    # Fallback: random movies
    if not related_movies:
        try:
            with current_app.db_engine.connect() as conn:
                fallback_rows = conn.execute(text("""
                    SELECT TOP 8 movieId, title, posterUrl, releaseYear
                    FROM cine.Movie 
                    WHERE movieId != :movie_id
                    ORDER BY NEWID()
                """), {"movie_id": movie_id}).mappings().all()
                
                related_movies = [
                    {
                        "movieId": row["movieId"],
                        "id": row["movieId"],
                        "title": row["title"],
                        "posterUrl": get_poster_or_dummy(row.get("posterUrl"), row["title"]),
                        "releaseYear": row.get("releaseYear"),
                        "country": "",
                        "similarity": 0.5,
                        "genres": ""
                    }
                    for row in fallback_rows
                ]
        except Exception as e:
            current_app.logger.error(f"Error getting fallback movies: {e}")
            related_movies = []
    
    return render_template("detail.html", movie=movie, related_movies=related_movies)


def _prepare_watch_data(movie_id: int, is_trailer: bool = False):
    """
    Helper function để chuẩn bị dữ liệu cho watch/trailer page
    Returns: (movie dict, related_movies list, tmdb_video_embed) hoặc None nếu không tìm thấy phim
    """
    
    # Lấy thông tin phim chính
    with current_app.db_engine.connect() as conn:
        r = conn.execute(text(
            "SELECT movieId, title, posterUrl, backdropUrl, releaseYear, overview, trailerUrl, viewCount, movieUrl FROM cine.Movie WHERE movieId=:id"
        ), {"id": movie_id}).mappings().first()
        
    if not r:
        return None
    
    # Xử lý video source
    tmdb_video = None
    movie_url = r.get("movieUrl")
    trailer_url = r.get("trailerUrl")
    
    # Nếu xem trailer: Ưu tiên trailerUrl, nếu không có thì lấy từ TMDB API
    if is_trailer:
        if trailer_url and trailer_url != '1' and trailer_url.strip():
            # Có trailerUrl trực tiếp, không cần lấy từ TMDB
            pass
        else:
            # Không có trailerUrl, thử lấy từ TMDB API
            if movie_url and movie_url != '1' and movie_url.strip():
                movie_url = movie_url.strip()
                tmdb_id = extract_tmdb_id_from_url(movie_url)
                if tmdb_id:
                    tmdb_video = get_tmdb_videos(tmdb_id, is_trailer=True)
    else:
        # Nếu xem phim: Lấy video chính từ TMDB API
        if movie_url and movie_url != '1' and movie_url.strip():
            movie_url = movie_url.strip()
            tmdb_id = extract_tmdb_id_from_url(movie_url)
            if tmdb_id:
                tmdb_video = get_tmdb_videos(tmdb_id, is_trailer=False)
    
    # Tăng view count và lưu lịch sử xem (áp dụng cho cả trailer và xem phim)
    with current_app.db_engine.begin() as conn:
        conn.execute(text(
            "UPDATE cine.Movie SET viewCount = viewCount + 1 WHERE movieId = :id"
        ), {"id": movie_id})
        
        user_id = session.get("user_id")
        if user_id:
            try:
                max_history_id_result = conn.execute(text("""
                    SELECT ISNULL(MAX(historyId), 0) FROM cine.ViewHistory
                """)).fetchone()
                max_history_id = max_history_id_result[0] if max_history_id_result else 0
                new_history_id = max_history_id + 1
                
                conn.execute(text("""
                    INSERT INTO cine.ViewHistory (historyId, userId, movieId, startedAt, deviceType, ipAddress, userAgent)
                    VALUES (:history_id, :user_id, :movie_id, GETDATE(), :device_type, :ip_address, :user_agent)
                """), {
                    "history_id": new_history_id,
                    "user_id": user_id,
                    "movie_id": movie_id,
                    "device_type": request.headers.get('User-Agent', 'Unknown')[:50],
                    "ip_address": request.remote_addr or "unknown",
                    "user_agent": request.headers.get('User-Agent', 'unknown')[:500]
                })
                
                # Cập nhật lastLoginAt
                conn.execute(text("""
                    UPDATE cine.[User] SET lastLoginAt = GETDATE() WHERE userId = :user_id
                """), {"user_id": user_id})
                
                # Xóa phim này khỏi recommendations đã lưu
                conn.execute(text("""
                    DELETE FROM cine.PersonalRecommendation 
                    WHERE userId = :user_id AND movieId = :movie_id
                """), {"user_id": user_id, "movie_id": movie_id})
                
                # Xóa khỏi ColdStartRecommendations
                conn.execute(text("""
                    DELETE FROM cine.ColdStartRecommendations 
                    WHERE userId = :user_id AND movieId = :movie_id
                """), {"user_id": user_id, "movie_id": movie_id})
            except Exception as e:
                current_app.logger.error(f"Error saving view history: {e}")
    
    # Lấy genres
    with current_app.db_engine.connect() as conn:
        genres_query = text("""
            SELECT g.name
            FROM cine.MovieGenre mg
            JOIN cine.Genre g ON mg.genreId = g.genreId
            WHERE mg.movieId = :movie_id
        """)
        genres_result = conn.execute(genres_query, {"movie_id": movie_id}).fetchall()
        genres = [{"name": genre[0], "slug": genre[0].lower().replace(' ', '-')} for genre in genres_result]
    
    # Xác định video source
    video_sources = []
    tmdb_video_embed = None
    
    if is_trailer:
        # Xem trailer: Ưu tiên trailerUrl, sau đó là video từ TMDB API
        if trailer_url and trailer_url != '1' and trailer_url.strip():
            # Có trailerUrl trực tiếp - extract video key để embed
            video_site, video_key = extract_video_key_from_url(trailer_url.strip())
            if video_site == 'youtube' and video_key:
                tmdb_video_embed = {
                    'type': 'youtube',
                    'key': video_key,
                    'name': 'Trailer',
                    'site': 'YouTube'
                }
            elif video_site == 'vimeo' and video_key:
                tmdb_video_embed = {
                    'type': 'vimeo',
                    'key': video_key,
                    'name': 'Trailer',
                    'site': 'Vimeo'
                }
            else:
                # Không phải YouTube/Vimeo, dùng URL trực tiếp
                video_sources = [{"label": "Trailer", "url": trailer_url.strip()}]
        elif tmdb_video:
            # Có video từ TMDB API (trailer)
            video_key = tmdb_video.get('key')
            video_site = tmdb_video.get('site')
            if video_site == 'YouTube' and video_key:
                tmdb_video_embed = {
                    'type': 'youtube',
                    'key': video_key,
                    'name': tmdb_video.get('name', 'Trailer'),
                    'site': 'YouTube'
                }
            elif video_site == 'Vimeo' and video_key:
                tmdb_video_embed = {
                    'type': 'vimeo',
                    'key': video_key,
                    'name': tmdb_video.get('name', 'Trailer'),
                    'site': 'Vimeo'
                }
        else:
            # Không có trailer
            video_sources = [{"label": "Trailer", "url": "https://www.w3schools.com/html/movie.mp4"}]
    elif tmdb_video:
        # Có video từ TMDB API
        video_key = tmdb_video.get('key')
        video_site = tmdb_video.get('site')
        if video_site == 'YouTube' and video_key:
            # Embed YouTube video
            tmdb_video_embed = {
                'type': 'youtube',
                'key': video_key,
                'name': tmdb_video.get('name', 'Video'),
                'site': 'YouTube'
            }
        elif video_site == 'Vimeo' and video_key:
            # Embed Vimeo video
            tmdb_video_embed = {
                'type': 'vimeo',
                'key': video_key,
                'name': tmdb_video.get('name', 'Video'),
                'site': 'Vimeo'
            }
        else:
            # Fallback: dùng video URL trực tiếp nếu có
            video_sources = [{"label": "Video", "url": f"https://www.youtube.com/watch?v={video_key}"}]
    else:
        # Fallback: video mặc định
        video_sources = [{"label": "720p", "url": "https://www.w3schools.com/html/movie.mp4"}]
    
    movie = {
        "id": r["movieId"],
        "title": r["title"],
        "poster": get_poster_or_dummy(r.get("posterUrl"), r["title"]),
        "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
        "year": r.get("releaseYear"),
        "overview": r.get("overview") or "",
        "sources": video_sources,
        "genres": genres,
        "viewCount": r.get("viewCount", 0) or 0,
        "tmdb_video": tmdb_video_embed,  # Thêm video từ TMDB
        "movie_url": movie_url if (movie_url and movie_url != '1') else None,  # URL gốc để hiển thị link
    }
    
    # Lấy phim liên quan
    related_movies = []
    try:
        target_limit = 20
        display_limit = 8

        if content_recommender:
            related_movies_raw = content_recommender.get_related_movies(movie_id, limit=target_limit)
        else:
            from recommenders.content_based_recommender import ContentBasedRecommender
            recommender = ContentBasedRecommender(current_app.db_engine)
            related_movies_raw = recommender.get_related_movies(movie_id, limit=target_limit)
        
        sampled_movies = random.sample(
            related_movies_raw,
            k=min(display_limit, len(related_movies_raw))
        ) if related_movies_raw else []
        
        related_movies = [
            {
                "movieId": movie["movieId"],
                "id": movie["movieId"],
                "title": movie["title"],
                "posterUrl": get_poster_or_dummy(movie.get("posterUrl"), movie["title"]),
                "releaseYear": movie.get("releaseYear"),
                "country": movie.get("country"),
                "similarity": movie.get("similarity", 0.0),
                "genres": movie.get("genres", "")
            }
            for movie in sampled_movies
        ]
    except Exception as e:
        current_app.logger.error(f"Error getting related movies: {e}")
        related_movies = []
    
    # Fallback: lấy phim ngẫu nhiên
    if not related_movies:
        try:
            with current_app.db_engine.connect() as conn:
                fallback_rows = conn.execute(text("""
                    SELECT TOP 8 movieId, title, posterUrl, releaseYear
                    FROM cine.Movie 
                    WHERE movieId != :movie_id
                    ORDER BY NEWID()
                """), {"movie_id": movie_id}).mappings().all()
                
                related_movies = [
                    {
                        "movieId": row["movieId"],
                        "id": row["movieId"],
                        "title": row["title"],
                        "posterUrl": get_poster_or_dummy(row.get("posterUrl"), row["title"]),
                        "releaseYear": row.get("releaseYear"),
                        "country": "",
                        "similarity": 0.0,
                        "genres": ""
                    }
                    for row in fallback_rows
                ]
        except Exception as e:
            current_app.logger.error(f"Error getting fallback movies: {e}")
            related_movies = []
    
    return (movie, related_movies, tmdb_video_embed)


@main_bp.route("/watch/<int:movie_id>")
@login_required
def watch(movie_id: int):
    """Watch movie page"""
    result = _prepare_watch_data(movie_id, is_trailer=False)
    if result is None:
        return redirect(url_for("main.home"))
    
    movie, related_movies, tmdb_video_embed = result
    return render_template("watch.html", 
                         movie=movie, 
                         related_movies=related_movies,
                         is_trailer=False,
                         tmdb_video=tmdb_video_embed)


@main_bp.route("/trailer/<int:movie_id>")
@login_required
def trailer(movie_id: int):
    """Watch trailer page"""
    result = _prepare_watch_data(movie_id, is_trailer=True)
    if result is None:
        return redirect(url_for("main.home"))
    
    movie, related_movies, tmdb_video_embed = result
    return render_template("watch.html", 
                         movie=movie, 
                         related_movies=related_movies,
                         is_trailer=True,
                         tmdb_video=tmdb_video_embed)


@main_bp.route("/api/search/suggestions")
def search_suggestions():
    """API endpoint for search autocomplete suggestions"""
    query = request.args.get('q', '').strip()
    limit = request.args.get('limit', 6, type=int)
    
    if not query or len(query) < 2:
        return jsonify({"success": True, "groups": []})
    
    try:
        like_query = f"%{query}%"
        grouped_results = []
        
        with current_app.db_engine.connect() as conn:
            title_rows = conn.execute(text("""
                SELECT TOP (:limit) movieId, title, posterUrl, releaseYear
                FROM cine.Movie
                WHERE title LIKE :query
                ORDER BY 
                    CASE 
                        WHEN title LIKE :exact_query THEN 1
                        WHEN title LIKE :start_query THEN 2
                        ELSE 3
                    END,
                    releaseYear DESC
            """), {
                "query": like_query,
                "exact_query": query,
                "start_query": f"{query}%",
                "limit": max(4, min(8, limit))
            }).mappings().all()
            
            person_rows = conn.execute(text("""
                SELECT TOP (:limit) movieId, title, posterUrl, releaseYear, director, cast
                FROM cine.Movie
                WHERE (director LIKE :query OR cast LIKE :query)
                ORDER BY releaseYear DESC, title
            """), {"query": like_query, "limit": max(4, limit)}).mappings().all()
            
            overview_rows = conn.execute(text("""
                SELECT TOP (:limit) movieId, title, posterUrl, releaseYear, overview
                FROM cine.Movie
                WHERE overview LIKE :query
                ORDER BY releaseYear DESC, title
            """), {"query": like_query, "limit": max(4, limit)}).mappings().all()
        
        def make_title_item(row):
            return {
                "id": row["movieId"],
                "title": row["title"],
                "poster": get_poster_or_dummy(row.get("posterUrl"), row["title"]),
                "year": row.get("releaseYear"),
                "matchType": "title",
                "subtitle": "Khớp tiêu đề"
            }
        
        def make_person_item(row):
            director = row.get("director") or ""
            cast_text = row.get("cast") or ""
            query_lower = query.lower()
            subtitle = "Khớp diễn viên/đạo diễn"
            if director and query_lower in director.lower():
                subtitle = f"Đạo diễn: {director}"
            elif cast_text:
                cast_matches = [c.strip() for c in cast_text.split(",") if query_lower in c.lower()]
                if cast_matches:
                    subtitle = f"Diễn viên: {', '.join(cast_matches[:2])}"
            return {
                "id": row["movieId"],
                "title": row["title"],
                "poster": get_poster_or_dummy(row.get("posterUrl"), row["title"]),
                "year": row.get("releaseYear"),
                "matchType": "person",
                "subtitle": subtitle
            }
        
        def make_overview_item(row):
            snippet = build_highlight_snippet(row.get("overview") or "", query)
            return {
                "id": row["movieId"],
                "title": row["title"],
                "poster": get_poster_or_dummy(row.get("posterUrl"), row["title"]),
                "year": row.get("releaseYear"),
                "matchType": "overview",
                "subtitle": snippet
            }
        
        raw_groups = [
            {"type": "title", "label": "Theo tiêu đề", "items": [make_title_item(r) for r in title_rows]},
            {"type": "person", "label": "Theo diễn viên / đạo diễn", "items": [make_person_item(r) for r in person_rows]},
            {"type": "overview", "label": "Theo nội dung", "items": [make_overview_item(r) for r in overview_rows]},
        ]
        
        non_empty = [g for g in raw_groups if g["items"]]
        remaining_slots = max(limit, 6)
        remaining_groups = len(non_empty)
        final_groups = []
        
        for group in raw_groups:
            if not group["items"]:
                continue
            slots_for_group = max(1, remaining_slots - max(0, remaining_groups - 1))
            take = min(len(group["items"]), slots_for_group)
            final_groups.append({
                "type": group["type"],
                "label": group["label"],
                "items": group["items"][:take]
            })
            remaining_slots -= take
            remaining_groups -= 1
            if remaining_slots <= 0:
                break
        
        return jsonify({"success": True, "groups": final_groups})
            
    except Exception as e:
        current_app.logger.error(f"Error in search suggestions: {e}")
        return jsonify({"success": False, "groups": []})


@main_bp.route("/api/movie-preview/<int:movie_id>")
@login_required
def movie_preview(movie_id):
    """Return preview embed info for quick hover preview"""
    try:
        with current_app.db_engine.connect() as conn:
            movie = conn.execute(text("""
                SELECT movieId, title, trailerUrl, posterUrl, backdropUrl
                FROM cine.Movie
                WHERE movieId = :movie_id
            """), {"movie_id": movie_id}).mappings().first()
            
            if not movie:
                return jsonify({"success": False, "message": "Không tìm thấy phim"}), 404
            
            provider, video_key = extract_video_key_from_url(movie.get("trailerUrl"))
            embed_url = None
            if provider == 'youtube' and video_key:
                embed_url = f"https://www.youtube.com/embed/{video_key}?autoplay=1&mute=1&controls=0&start=0&end=10&playsinline=1&rel=0&modestbranding=1"
            elif provider == 'vimeo' and video_key:
                embed_url = f"https://player.vimeo.com/video/{video_key}?autoplay=1&muted=1#t=0s"
            
            preview_payload = {
                "title": movie["title"],
                "embedUrl": embed_url,
                "provider": provider,
                "fallbackImages": [
                    get_poster_or_dummy(movie.get("backdropUrl") or movie.get("posterUrl"), movie["title"]),
                    get_poster_or_dummy(movie.get("posterUrl"), movie["title"])
                ]
            }
            return jsonify({"success": True, "preview": preview_payload})
    except Exception as e:
        current_app.logger.error(f"Error fetching preview for movie {movie_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Không thể tải preview"}), 500


@main_bp.route("/search")
@login_required
def search():
    """Search page"""
    query = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)
    match_source = request.args.get('source', 'all')
    allowed_sources = {'all', 'title', 'person', 'overview'}
    if match_source not in allowed_sources:
        match_source = 'all'
    per_page = 10
    
    if not query:
        return render_template('search.html', 
                             query=query, 
                             movies=[], 
                             pagination=None,
                             total_results=0,
                             match_source=match_source,
                             source_counts={})
    
    try:
        like_query = f"%{query}%"
        params = {
            "like_query": like_query,
            "exact_match": query,
            "start_match": f"{query}%"
        }
        
        def build_condition(source_key):
            if source_key == 'title':
                return "m.title LIKE :like_query"
            if source_key == 'person':
                return "(m.director LIKE :like_query OR m.cast LIKE :like_query)"
            if source_key == 'overview':
                return "m.overview LIKE :like_query"
            return "1=1"
        
        active_sources = []
        if match_source == 'all':
            active_sources = ['title', 'person', 'overview']
        else:
            active_sources = [match_source]
        
        where_clauses = [build_condition(src) for src in active_sources]
        where_sql = " OR ".join([f"({clause})" for clause in where_clauses if clause != "1=1"]) or "1=1"
        
        with current_app.db_engine.connect() as conn:
            total_count = conn.execute(text(f"""
                SELECT COUNT(*) 
                FROM cine.Movie m
                WHERE {where_sql}
            """), params).scalar()
            
            source_counts = {"all": total_count}
            source_counts["title"] = conn.execute(text("""
                SELECT COUNT(*) FROM cine.Movie WHERE title LIKE :like_query
            """), params).scalar()
            source_counts["person"] = conn.execute(text("""
                SELECT COUNT(*) FROM cine.Movie WHERE (director LIKE :like_query OR cast LIKE :like_query)
            """), params).scalar()
            source_counts["overview"] = conn.execute(text("""
                SELECT COUNT(*) FROM cine.Movie WHERE overview LIKE :like_query
            """), params).scalar()
            
            total_pages = (total_count + per_page - 1) // per_page
            offset = (page - 1) * per_page
            
            movies = conn.execute(text(f"""
                WITH FilteredMovies AS (
                    SELECT 
                        m.movieId,
                        m.title,
                        m.posterUrl,
                        m.backdropUrl,
                        m.overview,
                        m.releaseYear,
                        m.country,
                        m.director,
                        m.cast,
                        CASE 
                            WHEN m.title LIKE :exact_match THEN 'title'
                            WHEN m.title LIKE :start_match THEN 'title'
                            WHEN m.title LIKE :like_query THEN 'title'
                            WHEN (m.director LIKE :like_query OR m.cast LIKE :like_query) THEN 'person'
                            WHEN m.overview LIKE :like_query THEN 'overview'
                            ELSE 'title'
                        END AS matchType,
                        CASE 
                            WHEN m.title LIKE :exact_match THEN 1
                            WHEN m.title LIKE :start_match THEN 2
                            WHEN m.title LIKE :like_query THEN 3
                            WHEN (m.director LIKE :like_query OR m.cast LIKE :like_query) THEN 4
                            WHEN m.overview LIKE :like_query THEN 5
                            ELSE 6
                        END AS matchPriority,
                        ROW_NUMBER() OVER (
                            ORDER BY 
                                CASE 
                                    WHEN m.title LIKE :exact_match THEN 1
                                    WHEN m.title LIKE :start_match THEN 2
                                    WHEN m.title LIKE :like_query THEN 3
                                    WHEN (m.director LIKE :like_query OR m.cast LIKE :like_query) THEN 4
                                    WHEN m.overview LIKE :like_query THEN 5
                                    ELSE 6
                                END,
                                m.title
                        ) as rn
                    FROM cine.Movie m
                    WHERE {where_sql}
                )
                SELECT 
                    fm.movieId,
                    fm.title,
                    fm.posterUrl,
                    fm.backdropUrl,
                    fm.overview,
                    fm.releaseYear,
                    fm.country,
                    fm.director,
                    fm.cast,
                    fm.matchType,
                    fm.matchPriority,
                    AVG(CAST(r.value AS FLOAT)) AS avgRating,
                    COUNT(r.value) AS ratingCount,
                    STUFF((
                        SELECT ', ' + g.name
                        FROM cine.MovieGenre mg
                        JOIN cine.Genre g ON mg.genreId = g.genreId
                        WHERE mg.movieId = fm.movieId
                        FOR XML PATH('')
                    ), 1, 2, '') AS genres
                FROM FilteredMovies fm
                LEFT JOIN cine.Rating r ON fm.movieId = r.movieId
                WHERE fm.rn > :offset AND fm.rn <= :offset + :per_page
                GROUP BY fm.movieId, fm.title, fm.posterUrl, fm.backdropUrl, fm.overview, fm.releaseYear, fm.country, fm.director, fm.cast, fm.matchType, fm.matchPriority, fm.rn
                ORDER BY fm.matchPriority, fm.rn
            """), {
                **params,
                "offset": offset,
                "per_page": per_page
            }).mappings().all()
            
            movie_list = []
            for r in movies:
                match_type = r.get("matchType") or 'title'
                director = r.get("director") or ""
                cast_text = r.get("cast") or ""
                snippet = ""
                match_reason = "Khớp tiêu đề"
                
                if match_type == 'person':
                    query_lower = query.lower()
                    if director and query_lower in director.lower():
                        match_reason = f"Đạo diễn: {director}"
                    elif cast_text:
                        cast_matches = [c.strip() for c in cast_text.split(",") if query_lower in c.lower()]
                        if cast_matches:
                            match_reason = f"Diễn viên: {', '.join(cast_matches[:2])}"
                        else:
                            match_reason = "Khớp diễn viên/đạo diễn"
                    else:
                        match_reason = "Khớp diễn viên/đạo diễn"
                elif match_type == 'overview':
                    snippet = build_highlight_snippet(r.get("overview") or "", query)
                    match_reason = "Khớp nội dung"
                else:
                    match_reason = "Khớp tiêu đề"
                
                movie_list.append({
                    "id": r["movieId"],
                    "title": r["title"],
                    "poster": get_poster_or_dummy(r.get("posterUrl"), r["title"]),
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                    "year": r.get("releaseYear"),
                    "country": r.get("country"),
                    "avgRating": round(float(r["avgRating"]), 2) if r["avgRating"] else 0.0,
                    "ratingCount": int(r["ratingCount"]) if r["ratingCount"] else 0,
                    "genres": r.get("genres", "").split(", ") if r.get("genres") else [],
                    "matchType": match_type,
                    "director": director,
                    "cast": cast_text,
                    "snippet": snippet,
                    "matchReason": match_reason
                })
            
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
                                 total_results=total_count,
                                 match_source=match_source,
                                 source_counts=source_counts)
            
    except Exception as e:
        current_app.logger.error(f"Error in search: {e}")
        return render_template('search.html', 
                             query=query, 
                             movies=[], 
                             pagination=None,
                             total_results=0,
                             match_source=match_source,
                             source_counts={})


@main_bp.route("/the-loai/<string:genre_slug>")
@login_required
def genre_page(genre_slug):
    """Genre page với bộ lọc và sắp xếp giống all-movies"""
    main_genre_mapping = {
        'action': 'Action',
        'adventure': 'Adventure', 
        'comedy': 'Comedy',
        'horror': 'Horror'
    }
    
    genre_name = main_genre_mapping.get(genre_slug)
    
    if not genre_name:
        try:
            with current_app.db_engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT name FROM cine.Genre 
                    WHERE LOWER(REPLACE(name, ' ', '-')) = :slug
                """), {"slug": genre_slug.lower()}).fetchone()
                
                if result:
                    genre_name = result[0]
                else:
                    return redirect(url_for('main.home'))
        except Exception as e:
            current_app.logger.error(f"Error finding genre: {e}")
            return redirect(url_for('main.home'))
    
    # Lấy các tham số filter (giống all_movies)
    page = request.args.get('page', 1, type=int)
    per_page = 24
    sort_by = request.args.get('sort', 'newest', type=str)
    year_filter = request.args.get('year', '', type=str)
    # Genre filter được fix từ slug, nhưng vẫn có thể filter thêm genre khác
    additional_genre_filter = request.args.get('genre', '', type=str)
    
    try:
        with current_app.db_engine.connect() as conn:
            # Xây dựng WHERE clause - luôn filter theo genre_name từ slug
            where_clauses = []
            params = {}
            
            # Luôn filter theo genre chính từ slug
            where_clauses.append("EXISTS (SELECT 1 FROM cine.MovieGenre mg2 JOIN cine.Genre g2 ON mg2.genreId = g2.genreId WHERE mg2.movieId = m.movieId AND g2.name = :main_genre)")
            params["main_genre"] = genre_name
            
            if year_filter:
                where_clauses.append("m.releaseYear = :year")
                params["year"] = int(year_filter)
            
            # Có thể filter thêm genre khác (nếu muốn)
            if additional_genre_filter and additional_genre_filter != genre_name:
                where_clauses.append("EXISTS (SELECT 1 FROM cine.MovieGenre mg3 JOIN cine.Genre g3 ON mg3.genreId = g3.genreId WHERE mg3.movieId = m.movieId AND g3.name = :additional_genre)")
                params["additional_genre"] = additional_genre_filter
            
            where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
            
            # Xây dựng ORDER BY clause (giống all_movies)
            if sort_by == 'ratings':
                order_by_inner = 'avgRating DESC, ratingCount DESC, m.movieId DESC'
                order_by_outer = 'avgRating DESC, ratingCount DESC'
            else:
                order_by_map = {
                    'newest': 'm.releaseYear DESC, m.movieId DESC',
                    'oldest': 'm.releaseYear ASC, m.movieId ASC',
                    'views': 'm.viewCount DESC, m.movieId DESC',
                    'title_asc': 'm.title ASC',
                    'title_desc': 'm.title DESC'
                }
                order_by_inner = order_by_map.get(sort_by, order_by_map['newest'])
                order_by_outer = order_by_inner
            
            # Đếm tổng số phim
            count_query = f"""
                SELECT COUNT(DISTINCT m.movieId)
                FROM cine.Movie m
                {where_sql}
            """
            total_count = conn.execute(text(count_query), params).scalar()
            
            # Tính toán phân trang
            total_pages = (total_count + per_page - 1) // per_page
            offset = (page - 1) * per_page
            
            # Lấy danh sách phim
            if sort_by == 'ratings':
                query = f"""
                    SELECT m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.country, m.viewCount,
                           AVG(CAST(r.value AS FLOAT)) AS avgRating,
                           COUNT(r.value) AS ratingCount
                    FROM cine.Movie m
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    {where_sql}
                    GROUP BY m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.country, m.viewCount
                    ORDER BY {order_by_outer}
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """
            else:
                query = f"""
                    SELECT m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.country, m.viewCount,
                           AVG(CAST(r.value AS FLOAT)) AS avgRating,
                           COUNT(r.value) AS ratingCount
                    FROM cine.Movie m
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    {where_sql}
                    GROUP BY m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.country, m.viewCount
                    ORDER BY {order_by_inner}
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """
            
            params["offset"] = offset
            params["per_page"] = per_page
            rows = conn.execute(text(query), params).mappings().all()
            
            # Lấy genres cho các phim
            movie_ids = [r["movieId"] for r in rows]
            genres_dict = get_movies_genres(movie_ids, current_app.db_engine) if movie_ids else {}
            
            # Format movies
            movies = []
            for r in rows:
                movie_id = r["movieId"]
                genres = genres_dict.get(movie_id, "")
                
                movies.append({
                    "id": movie_id,
                    "title": r["title"],
                    "poster": get_poster_or_dummy(r.get("posterUrl"), r["title"]),
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                    "year": r.get("releaseYear"),
                    "country": r.get("country"),
                    "avgRating": round(float(r["avgRating"]), 2) if r["avgRating"] else 0.0,
                    "ratingCount": int(r["ratingCount"]) if r["ratingCount"] else 0,
                    "viewCount": r.get("viewCount") or 0,
                    "genres": genres.split(", ") if genres else []
                })
            
            # Lấy danh sách năm và thể loại cho filter
            years = conn.execute(text("""
                SELECT DISTINCT releaseYear 
                FROM cine.Movie 
                WHERE releaseYear IS NOT NULL 
                ORDER BY releaseYear DESC
            """)).fetchall()
            year_list = [y[0] for y in years]
            
            genres_list = conn.execute(text("""
                SELECT DISTINCT g.name 
                FROM cine.Genre g
                JOIN cine.MovieGenre mg ON g.genreId = mg.genreId
                ORDER BY g.name
            """)).fetchall()
            genre_list = [g[0] for g in genres_list]
            
            # Pagination
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
            
            return render_template("genre_page.html", 
                                 movies=movies, 
                                 pagination=pagination,
                                 sort_by=sort_by,
                                 year_filter=year_filter,
                                 genre_filter=genre_name,  # Genre chính từ slug
                                 additional_genre_filter=additional_genre_filter,  # Genre phụ (nếu có)
                                 genre_slug=genre_slug,
                                 genre_name=genre_name,
                                 year_list=year_list,
                                 genre_list=genre_list)
            
    except Exception as e:
        current_app.logger.error(f"Error loading genre page: {e}", exc_info=True)
        return render_template("genre_page.html", 
                             movies=[], 
                             pagination=None,
                             sort_by='newest',
                             year_filter='',
                             genre_filter=genre_name,
                             additional_genre_filter='',
                             genre_slug=genre_slug,
                             genre_name=genre_name,
                             year_list=[],
                             genre_list=[])


@main_bp.route("/onboarding")
@login_required
def onboarding():
    """Trang onboarding cho user mới"""
    return render_template("onboarding.html")


@main_bp.route("/api/genres")
def get_genres():
    """API endpoint để lấy danh sách thể loại phim"""
    try:
        with current_app.db_engine.connect() as conn:
            genres = conn.execute(text("""
                SELECT g.genreId, g.name, COUNT(mg.movieId) as movie_count
                FROM cine.Genre g
                LEFT JOIN cine.MovieGenre mg ON g.genreId = mg.genreId
                GROUP BY g.genreId, g.name
                HAVING COUNT(mg.movieId) > 0
                ORDER BY movie_count DESC, g.name
            """)).mappings().all()
            
            return jsonify({
                "success": True,
                "genres": [
                    {
                        "genreId": genre["genreId"],
                        "name": genre["name"],
                        "movie_count": genre["movie_count"]
                    }
                    for genre in genres
                ]
            })
    except Exception as e:
        current_app.logger.error(f"Error getting genres: {e}")
        return jsonify({"success": False, "message": "Có lỗi xảy ra khi lấy danh sách thể loại"})


@main_bp.route("/api/actors")
def get_actors():
    """API endpoint để lấy danh sách diễn viên phổ biến từ cột cast trong Movie"""
    try:
        with current_app.db_engine.connect() as conn:
            # Lấy tất cả cast từ các phim
            movies = conn.execute(text("""
                SELECT cast
                FROM cine.Movie
                WHERE cast IS NOT NULL AND cast != ''
            """)).fetchall()
            
            # Parse và đếm số lần xuất hiện của mỗi diễn viên
            actor_count = {}
            for row in movies:
                cast_str = row[0] if row[0] else ""
                if cast_str:
                    # Tách các diễn viên (có thể cách nhau bởi dấu phẩy)
                    actors = [a.strip() for a in cast_str.split(',') if a.strip()]
                    for actor in actors:
                        actor_count[actor] = actor_count.get(actor, 0) + 1
            
            # Sắp xếp và lấy top 20
            sorted_actors = sorted(actor_count.items(), key=lambda x: (-x[1], x[0]))[:20]
            
            # Tạo danh sách với ID giả (dùng hash hoặc index)
            actors_list = [
                {
                    "actorId": hash(actor_name) % (10**9),  # Tạo ID từ hash
                    "name": actor_name,
                    "movie_count": count
                }
                for actor_name, count in sorted_actors
            ]
            
            return jsonify({
                "success": True,
                "actors": actors_list
            })
    except Exception as e:
        current_app.logger.error(f"Error getting actors: {e}", exc_info=True)
        # Trả về danh sách rỗng thay vì lỗi để không làm gián đoạn frontend
        return jsonify({
            "success": True,
            "actors": []
        })


@main_bp.route("/api/directors")
def get_directors():
    """API endpoint để lấy danh sách đạo diễn phổ biến từ cột director trong Movie"""
    try:
        with current_app.db_engine.connect() as conn:
            # Lấy tất cả directors từ các phim
            movies = conn.execute(text("""
                SELECT director
                FROM cine.Movie
                WHERE director IS NOT NULL AND director != ''
            """)).fetchall()
            
            # Parse và đếm số lần xuất hiện của mỗi đạo diễn
            director_count = {}
            for row in movies:
                director_str = row[0] if row[0] else ""
                if director_str:
                    # Tách các đạo diễn (có thể cách nhau bởi dấu phẩy)
                    directors = [d.strip() for d in director_str.split(',') if d.strip()]
                    for director in directors:
                        director_count[director] = director_count.get(director, 0) + 1
            
            # Sắp xếp và lấy top 20
            sorted_directors = sorted(director_count.items(), key=lambda x: (-x[1], x[0]))[:20]
            
            # Tạo danh sách với ID giả (dùng hash hoặc index)
            directors_list = [
                {
                    "directorId": hash(director_name) % (10**9),  # Tạo ID từ hash
                    "name": director_name,
                    "movie_count": count
                }
                for director_name, count in sorted_directors
            ]
            
            return jsonify({
                "success": True,
                "directors": directors_list
            })
    except Exception as e:
        current_app.logger.error(f"Error getting directors: {e}", exc_info=True)
        # Trả về danh sách rỗng thay vì lỗi để không làm gián đoạn frontend
        return jsonify({
            "success": True,
            "directors": []
        })


@main_bp.route("/api/save_user_preferences", methods=["POST"])
@login_required
def save_user_preferences():
    """API endpoint để lưu sở thích của user"""
    try:
        user_id = session.get("user_id")
        data = request.get_json()
        
        genres = data.get('genres', [])
        actors = data.get('actors', [])
        directors = data.get('directors', [])
        
        if not genres:
            return jsonify({"success": False, "message": "Vui lòng chọn ít nhất 1 thể loại phim"})
        
        with current_app.db_engine.begin() as conn:
            # Tạo bảng UserPreference nếu chưa tồn tại
            try:
                conn.execute(text("""
                    IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'UserPreference' AND schema_id = SCHEMA_ID('cine'))
                    BEGIN
                        CREATE TABLE [cine].[UserPreference] (
                            [prefId] bigint IDENTITY(1,1) NOT NULL,
                            [userId] bigint NOT NULL,
                            [preferenceType] nvarchar(20) NOT NULL,
                            [preferenceId] bigint NOT NULL,
                            [createdAt] datetime2 NOT NULL DEFAULT (sysutcdatetime())
                        );
                    END
                """))
            except Exception as e:
                current_app.logger.error(f"Error creating UserPreference table: {e}")
            
            # Thêm cột hasCompletedOnboarding nếu chưa tồn tại
            try:
                conn.execute(text("""
                    IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('[cine].[User]') AND name = 'hasCompletedOnboarding')
                    BEGIN
                        ALTER TABLE [cine].[User] ADD [hasCompletedOnboarding] bit NOT NULL DEFAULT (0);
                    END
                """))
            except Exception as e:
                current_app.logger.error(f"Error adding hasCompletedOnboarding column: {e}")
            
            # Xóa preferences cũ
            conn.execute(text("""
                DELETE FROM cine.UserPreference WHERE userId = :user_id
            """), {"user_id": user_id})
            
            # Lưu genre preferences
            for genre_id in genres:
                conn.execute(text("""
                    INSERT INTO cine.UserPreference (userId, preferenceType, preferenceId, createdAt)
                    VALUES (:user_id, 'genre', :preference_id, GETDATE())
                """), {"user_id": user_id, "preference_id": genre_id})
            
            # Lưu actor preferences (nếu có)
            for actor_id in actors:
                conn.execute(text("""
                    INSERT INTO cine.UserPreference (userId, preferenceType, preferenceId, createdAt)
                    VALUES (:user_id, 'actor', :preference_id, GETDATE())
                """), {"user_id": user_id, "preference_id": actor_id})
            
            # Lưu director preferences (nếu có)
            for director_id in directors:
                conn.execute(text("""
                    INSERT INTO cine.UserPreference (userId, preferenceType, preferenceId, createdAt)
                    VALUES (:user_id, 'director', :preference_id, GETDATE())
                """), {"user_id": user_id, "preference_id": director_id})
            
            # Đánh dấu đã hoàn thành onboarding
            conn.execute(text("""
                UPDATE cine.[User] 
                SET hasCompletedOnboarding = 1 
                WHERE userId = :user_id
            """), {"user_id": user_id})
        
        session["onboarding_completed"] = True
        return jsonify({"success": True, "message": "Đã lưu sở thích thành công!"})
        
    except Exception as e:
        current_app.logger.error(f"Error saving user preferences: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"Có lỗi xảy ra: {str(e)}"})


@main_bp.route("/all-movies")
@login_required
def all_movies():
    """Trang tất cả phim với bộ lọc"""
    # Lấy các tham số filter
    page = request.args.get('page', 1, type=int)
    per_page = 24
    sort_by = request.args.get('sort', 'newest', type=str)
    year_filter = request.args.get('year', '', type=str)
    genre_filter = request.args.get('genre', '', type=str)
    
    try:
        with current_app.db_engine.connect() as conn:
            # Xây dựng WHERE clause
            where_clauses = []
            params = {}
            
            if year_filter:
                where_clauses.append("m.releaseYear = :year")
                params["year"] = int(year_filter)
            
            if genre_filter:
                where_clauses.append("EXISTS (SELECT 1 FROM cine.MovieGenre mg2 JOIN cine.Genre g2 ON mg2.genreId = g2.genreId WHERE mg2.movieId = m.movieId AND g2.name = :genre)")
                params["genre"] = genre_filter
            
            where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
            
            # Xây dựng ORDER BY clause
            if sort_by == 'ratings':
                order_by_inner = 'avgRating DESC, ratingCount DESC, m.movieId DESC'
                order_by_outer = 'avgRating DESC, ratingCount DESC'
            else:
                order_by_map = {
                    'newest': 'm.releaseYear DESC, m.movieId DESC',
                    'oldest': 'm.releaseYear ASC, m.movieId ASC',
                    'views': 'm.viewCount DESC, m.movieId DESC',
                    'title_asc': 'm.title ASC',
                    'title_desc': 'm.title DESC'
                }
                order_by_inner = order_by_map.get(sort_by, order_by_map['newest'])
                order_by_outer = order_by_inner
            
            # Đếm tổng số phim
            count_query = f"""
                SELECT COUNT(DISTINCT m.movieId)
                FROM cine.Movie m
                {where_sql}
            """
            total_count = conn.execute(text(count_query), params).scalar()
            
            # Tính toán phân trang
            total_pages = (total_count + per_page - 1) // per_page
            offset = (page - 1) * per_page
            
            # Lấy danh sách phim
            if sort_by == 'ratings':
                # Query với ratings
                query = f"""
                    SELECT m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.country, m.viewCount,
                           AVG(CAST(r.value AS FLOAT)) AS avgRating,
                           COUNT(r.value) AS ratingCount
                    FROM cine.Movie m
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    {where_sql}
                    GROUP BY m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.country, m.viewCount
                    ORDER BY {order_by_outer}
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """
            else:
                # Query không có ratings
                query = f"""
                    SELECT m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.country, m.viewCount,
                           AVG(CAST(r.value AS FLOAT)) AS avgRating,
                           COUNT(r.value) AS ratingCount
                    FROM cine.Movie m
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    {where_sql}
                    GROUP BY m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.country, m.viewCount
                    ORDER BY {order_by_inner}
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """
            
            params["offset"] = offset
            params["per_page"] = per_page
            rows = conn.execute(text(query), params).mappings().all()
            
            # Lấy genres cho các phim
            movie_ids = [r["movieId"] for r in rows]
            genres_dict = get_movies_genres(movie_ids, current_app.db_engine) if movie_ids else {}
            
            # Format movies
            movies = []
            for r in rows:
                movie_id = r["movieId"]
                genres = genres_dict.get(movie_id, "")
                
                movies.append({
                    "id": movie_id,
                    "title": r["title"],
                    "poster": get_poster_or_dummy(r.get("posterUrl"), r["title"]),
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                    "year": r.get("releaseYear"),
                    "country": r.get("country"),
                    "avgRating": round(float(r["avgRating"]), 2) if r["avgRating"] else 0.0,
                    "ratingCount": int(r["ratingCount"]) if r["ratingCount"] else 0,
                    "genres": genres.split(", ") if genres else []
                })
            
            # Lấy danh sách năm và thể loại cho filter
            years = conn.execute(text("""
                SELECT DISTINCT releaseYear 
                FROM cine.Movie 
                WHERE releaseYear IS NOT NULL 
                ORDER BY releaseYear DESC
            """)).fetchall()
            year_list = [y[0] for y in years]
            
            genres_list = conn.execute(text("""
                SELECT DISTINCT g.name 
                FROM cine.Genre g
                JOIN cine.MovieGenre mg ON g.genreId = mg.genreId
                ORDER BY g.name
            """)).fetchall()
            genre_list = [g[0] for g in genres_list]
            
            # Pagination
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
            
            return render_template("all_movies.html", 
                                 movies=movies, 
                                 pagination=pagination,
                                 sort_by=sort_by,
                                 year_filter=year_filter,
                                 genre_filter=genre_filter,
                                 year_list=year_list,
                                 genre_list=genre_list)
            
    except Exception as e:
        current_app.logger.error(f"Error loading all movies: {e}", exc_info=True)
        return render_template("all_movies.html", 
                             movies=[], 
                             pagination=None,
                             sort_by='newest',
                             year_filter='',
                             genre_filter='',
                             year_list=[],
                             genre_list=[])


@main_bp.route("/api/all-movies")
@login_required
def api_all_movies():
    """API endpoint để load movies với pagination (JSON response)"""
    page = request.args.get('page', 1, type=int)
    per_page = 24
    sort_by = request.args.get('sort', 'newest', type=str)
    year_filter = request.args.get('year', '', type=str)
    genre_filter = request.args.get('genre', '', type=str)
    
    try:
        with current_app.db_engine.connect() as conn:
            # Xây dựng WHERE clause
            where_clauses = []
            params = {}
            
            if year_filter:
                where_clauses.append("m.releaseYear = :year")
                params["year"] = int(year_filter)
            
            if genre_filter:
                where_clauses.append("EXISTS (SELECT 1 FROM cine.MovieGenre mg2 JOIN cine.Genre g2 ON mg2.genreId = g2.genreId WHERE mg2.movieId = m.movieId AND g2.name = :genre)")
                params["genre"] = genre_filter
            
            where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
            
            # Xây dựng ORDER BY clause
            if sort_by == 'ratings':
                order_by_inner = 'avgRating DESC, ratingCount DESC, m.movieId DESC'
                order_by_outer = 'avgRating DESC, ratingCount DESC'
            else:
                order_by_map = {
                    'newest': 'm.releaseYear DESC, m.movieId DESC',
                    'oldest': 'm.releaseYear ASC, m.movieId ASC',
                    'views': 'm.viewCount DESC, m.movieId DESC',
                    'title_asc': 'm.title ASC',
                    'title_desc': 'm.title DESC'
                }
                order_by_inner = order_by_map.get(sort_by, order_by_map['newest'])
                order_by_outer = order_by_inner
            
            # Đếm tổng số phim
            count_query = f"""
                SELECT COUNT(DISTINCT m.movieId)
                FROM cine.Movie m
                {where_sql}
            """
            total_count = conn.execute(text(count_query), params).scalar()
            
            # Tính toán phân trang
            total_pages = (total_count + per_page - 1) // per_page
            offset = (page - 1) * per_page
            
            # Lấy danh sách phim
            if sort_by == 'ratings':
                query = f"""
                    SELECT m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.country, m.viewCount,
                           AVG(CAST(r.value AS FLOAT)) AS avgRating,
                           COUNT(r.value) AS ratingCount
                    FROM cine.Movie m
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    {where_sql}
                    GROUP BY m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.country, m.viewCount
                    ORDER BY {order_by_outer}
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """
            else:
                query = f"""
                    SELECT m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.country, m.viewCount,
                           AVG(CAST(r.value AS FLOAT)) AS avgRating,
                           COUNT(r.value) AS ratingCount
                    FROM cine.Movie m
                    LEFT JOIN cine.Rating r ON m.movieId = r.movieId
                    {where_sql}
                    GROUP BY m.movieId, m.title, m.posterUrl, m.backdropUrl, m.overview, m.releaseYear, m.country, m.viewCount
                    ORDER BY {order_by_inner}
                    OFFSET :offset ROWS
                    FETCH NEXT :per_page ROWS ONLY
                """
            
            params["offset"] = offset
            params["per_page"] = per_page
            rows = conn.execute(text(query), params).mappings().all()
            
            # Lấy genres cho các phim
            movie_ids = [r["movieId"] for r in rows]
            genres_dict = get_movies_genres(movie_ids, current_app.db_engine) if movie_ids else {}
            
            # Format movies
            movies = []
            for r in rows:
                movie_id = r["movieId"]
                genres = genres_dict.get(movie_id, "")
                
                movies.append({
                    "id": movie_id,
                    "title": r["title"],
                    "poster": get_poster_or_dummy(r.get("posterUrl"), r["title"]),
                    "backdrop": r.get("backdropUrl") or "/static/img/dune2_backdrop.jpg",
                    "description": (r.get("overview") or "")[:160],
                    "year": r.get("releaseYear"),
                    "country": r.get("country"),
                    "avgRating": round(float(r["avgRating"]), 2) if r["avgRating"] else 0.0,
                    "ratingCount": int(r["ratingCount"]) if r["ratingCount"] else 0,
                    "viewCount": int(r["viewCount"]) if r["viewCount"] else 0,
                    "genres": genres.split(", ") if genres else []
                })
            
            # Pagination
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
            
            return jsonify({
                "success": True,
                "movies": movies,
                "pagination": pagination
            })
            
    except Exception as e:
        current_app.logger.error(f"Error in API all movies: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": str(e),
            "movies": [],
            "pagination": None
        }), 500
