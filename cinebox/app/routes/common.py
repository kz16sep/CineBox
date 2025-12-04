"""
Common utilities, helpers, and shared code for routes
"""

import sys
import os
from flask import current_app

# Add parent directory to path for imports (cinebox directory)
_cinebox_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _cinebox_dir not in sys.path:
    sys.path.insert(0, _cinebox_dir)

from recommenders.content_based import ContentBasedRecommender
from recommenders.enhanced_cf import EnhancedCFRecommender

# Global recommender instances
content_recommender = None
enhanced_cf_recommender = None

# Cache configurations
trending_cache = {
    'data': None,
    'timestamp': None,
    'ttl': 3600  # 1 hour
}

TRENDING_TIME_WINDOW_DAYS = 7

latest_movies_cache = {
    'data': None,
    'timestamp': None,
    'ttl': 300  # 5 minutes
}

carousel_movies_cache = {
    'data': None,
    'timestamp': None,
    'ttl': 300  # 5 minutes
}

# Similarity calculation progress tracker
similarity_progress = {}  # {movie_id: {'status': 'running'|'completed'|'error', 'progress': 0-100, 'message': ''}}

RECOMMENDATION_LIMIT = 12


def init_recommenders():
    """Initialize recommender instances - sử dụng lazy loading và background loading để tránh lag"""
    global content_recommender, enhanced_cf_recommender
    try:
        content_recommender = ContentBasedRecommender(current_app.db_engine)
        # Sử dụng lazy loading và background loading để không block startup
        enhanced_cf_recommender = EnhancedCFRecommender(
            current_app.db_engine,
            lazy_load=True,
            background_load=True
        )
        
        # Log status
        loading_status = enhanced_cf_recommender.get_loading_status()
        if loading_status['model_exists']:
            current_app.logger.info("Enhanced CF model will be loaded in background (non-blocking)")
        else:
            current_app.logger.warning("Enhanced CF model file not found")
        
        current_app.logger.info("Recommenders initialized successfully (non-blocking)")
    except Exception as e:
        current_app.logger.error(f"Error initializing recommenders: {e}")


def get_poster_or_dummy(poster_url, title):
    """Trả về poster URL hoặc dummy image nếu không có"""
    if poster_url and poster_url != "1" and poster_url.strip():
        return poster_url
    else:
        # Tạo dummy image với title
        safe_title = title[:20].replace(' ', '+').replace('&', 'and')
        return f"https://dummyimage.com/300x450/2c3e50/ecf0f1&text={safe_title}"


# --- CF retrain dirty-flag helpers ---
def set_cf_dirty(db_engine=None):
    """Set CF model dirty flag"""
    try:
        if db_engine is None:
            db_engine = current_app.db_engine
        with db_engine.begin() as conn:
            from sqlalchemy import text
            conn.execute(text("""
                IF OBJECT_ID('cine.AppState','U') IS NULL
                BEGIN
                  CREATE TABLE cine.AppState ( [key] NVARCHAR(50) PRIMARY KEY, [value] NVARCHAR(255) NULL )
                END;
                MERGE cine.AppState AS t
                USING (SELECT 'cf_dirty' AS [key], 'true' AS [value]) AS s
                ON t.[key] = s.[key]
                WHEN MATCHED THEN UPDATE SET [value] = 'true'
                WHEN NOT MATCHED THEN INSERT ([key],[value]) VALUES (s.[key], s.[value]);
            """))
    except Exception as e:
        current_app.logger.error(f"Error setting cf_dirty: {e}")


def get_cf_state(db_engine=None):
    """Lấy trạng thái CF model từ AppState table"""
    try:
        if db_engine is None:
            db_engine = current_app.db_engine
        
        from sqlalchemy import text
        with db_engine.begin() as conn:
            conn.execute(text("""
                IF OBJECT_ID('cine.AppState','U') IS NULL
                BEGIN
                  CREATE TABLE cine.AppState ( [key] NVARCHAR(50) PRIMARY KEY, [value] NVARCHAR(255) NULL )
                END;
            """))
        
        with db_engine.connect() as conn:
            result = conn.execute(text("""
                SELECT [key], [value] 
                FROM cine.AppState 
                WHERE [key] IN ('cf_dirty', 'cf_last_retrain')
            """)).mappings().all()
            
            state = {}
            for row in result:
                state[row['key']] = row['value']
            
            return state
    except Exception as e:
        current_app.logger.error(f"Error getting cf_state: {e}")
        return {}


def clear_cf_dirty_and_set_last(timestamp, db_engine=None):
    """Xóa flag cf_dirty và cập nhật cf_last_retrain"""
    try:
        if db_engine is None:
            db_engine = current_app.db_engine
        
        from sqlalchemy import text
        with db_engine.begin() as conn:
            conn.execute(text("""
                IF OBJECT_ID('cine.AppState','U') IS NULL
                BEGIN
                  CREATE TABLE cine.AppState ( [key] NVARCHAR(50) PRIMARY KEY, [value] NVARCHAR(255) NULL )
                END;
            """))
            
            conn.execute(text("""
                MERGE cine.AppState AS t
                USING (SELECT 'cf_dirty' AS [key], 'false' AS [value]) AS s
                ON t.[key] = s.[key]
                WHEN MATCHED THEN UPDATE SET [value] = 'false'
                WHEN NOT MATCHED THEN INSERT ([key],[value]) VALUES (s.[key], s.[value]);
            """))
            
            conn.execute(text("""
                MERGE cine.AppState AS t
                USING (SELECT 'cf_last_retrain' AS [key], :timestamp AS [value]) AS s
                ON t.[key] = s.[key]
                WHEN MATCHED THEN UPDATE SET [value] = :timestamp
                WHEN NOT MATCHED THEN INSERT ([key],[value]) VALUES (s.[key], s.[value]);
            """), {"timestamp": timestamp})
    except Exception as e:
        current_app.logger.error(f"Error clearing cf_dirty and setting last retrain: {e}")


def get_watched_movie_ids(user_id, conn):
    """Lấy danh sách movieId của các phim đã xem bởi user"""
    try:
        from sqlalchemy import text
        result = conn.execute(text("""
            SELECT DISTINCT movieId 
            FROM cine.ViewHistory 
            WHERE userId = :user_id
        """), {"user_id": user_id})
        return {row[0] for row in result}
    except Exception as e:
        current_app.logger.warning(f"Error getting watched movies for user {user_id}: {e}")
        return set()


def get_cold_start_recommendations(user_id, conn):
    """
    Tạo cold start recommendations cho user mới - CHỈ LẤY PHIM THEO PREFERENCES
    Chỉ sử dụng: Phim theo preferences của user (genres đã chọn trong onboarding)
    """
    try:
        from sqlalchemy import text
        from app.movie_query_helpers import get_movies_genres, get_movie_rating_stats
        
        recommendations = []
        all_movie_ids = []
        
        # Lấy preferences của user
        preferences = conn.execute(text("""
            SELECT preferenceType, preferenceId 
            FROM cine.UserPreference 
            WHERE userId = :user_id AND preferenceType = 'genre'
        """), {"user_id": user_id}).mappings().all()
        
        if not preferences:
            current_app.logger.info(f"User {user_id} has no genre preferences, returning empty cold start recommendations")
            return []
        
        genre_ids = [p["preferenceId"] for p in preferences]
        if not genre_ids:
            return []
        
        # Tạo placeholders cho genre_ids
        placeholders = ','.join([f':genre_{i}' for i in range(len(genre_ids))])
        params_pref = {f'genre_{i}': genre_id for i, genre_id in enumerate(genre_ids)}
        params_pref['user_id'] = user_id
        
        # Lấy phim theo genres đã chọn
        preference_movies = conn.execute(text(f"""
            SELECT TOP 12
                m.movieId, m.title, m.posterUrl, m.releaseYear, m.country,
                AVG(CAST(r.value AS FLOAT)) AS avgRating,
                COUNT(r.movieId) AS ratingCount
            FROM cine.Movie m
            INNER JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
            LEFT JOIN cine.Rating r ON m.movieId = r.movieId
            WHERE mg.genreId IN ({placeholders})
                AND NOT EXISTS (
                    SELECT 1 FROM cine.ViewHistory vh 
                    WHERE vh.movieId = m.movieId AND vh.userId = :user_id
                )
            GROUP BY m.movieId, m.title, m.posterUrl, m.releaseYear, m.country
            HAVING COUNT(r.movieId) >= 5 OR COUNT(r.movieId) = 0
            ORDER BY AVG(CAST(r.value AS FLOAT)) DESC, COUNT(r.movieId) DESC
        """), params_pref).mappings().all()
        
        for movie in preference_movies:
            all_movie_ids.append(movie["movieId"])
            recommendations.append({
                "id": movie["movieId"],
                "title": movie["title"],
                "poster": get_poster_or_dummy(movie.get("posterUrl"), movie["title"]),
                "releaseYear": movie["releaseYear"],
                "country": movie["country"],
                "score": 0.95,
                "rank": len(recommendations) + 1,
                "avgRating": round(float(movie["avgRating"]), 2) if movie["avgRating"] else 0.0,
                "ratingCount": movie["ratingCount"] or 0,
                "genres": "",
                "source": "preference_genre"
            })
        
        # Batch query genres và ratings
        if all_movie_ids:
            db_engine = conn.engine if hasattr(conn, 'engine') else current_app.db_engine
            genres_dict = get_movies_genres(all_movie_ids, db_engine)
            ratings_dict = get_movie_rating_stats(all_movie_ids, db_engine)
            
            for rec in recommendations:
                movie_id = rec["id"]
                rec["genres"] = genres_dict.get(movie_id, "")
                if movie_id in ratings_dict:
                    stats = ratings_dict[movie_id]
                    rec["avgRating"] = stats.get("avgRating", 0.0)
                    rec["ratingCount"] = stats.get("ratingCount", 0)
        
        return recommendations[:RECOMMENDATION_LIMIT]
        
    except Exception as e:
        current_app.logger.error(f"Error generating cold start recommendations: {e}")
        return []


def create_rating_based_recommendations(user_id, movies, db_engine=None):
    """Tạo recommendations dựa trên rating thực tế của user"""
    try:
        if not movies:
            return []
        
        if db_engine is None:
            db_engine = current_app.db_engine
        
        from app.movie_query_helpers import get_movies_interaction_stats
        from app.recommendation_helpers import calculate_user_interaction_score, sort_recommendations
        
        movie_ids = [movie["id"] for movie in movies]
        all_interaction_stats = get_movies_interaction_stats(movie_ids, db_engine)
        
        # Batch query user interactions
        from sqlalchemy import text
        placeholders = ','.join([f':id{i}' for i in range(len(movie_ids))])
        params = {f'id{i}': int(movie_id) for i, movie_id in enumerate(movie_ids)}
        params['user_id'] = user_id
        
        with db_engine.connect() as conn:
            interactions = conn.execute(text(f"""
                SELECT 
                    m.movieId,
                    MAX(r.value) as user_rating,
                    MAX(CASE WHEN f.userId IS NOT NULL THEN 1 ELSE 0 END) as is_favorite,
                    MAX(CASE WHEN w.userId IS NOT NULL THEN 1 ELSE 0 END) as is_watchlist,
                    MAX(CASE WHEN vh.userId IS NOT NULL THEN 1 ELSE 0 END) as has_viewed,
                    MAX(CASE WHEN c.userId IS NOT NULL THEN 1 ELSE 0 END) as has_commented,
                    COUNT(DISTINCT r2.userId) as total_ratings,
                    AVG(CAST(r2.value AS FLOAT)) as avg_rating
                FROM cine.Movie m
                LEFT JOIN cine.Rating r ON m.movieId = r.movieId AND r.userId = :user_id
                LEFT JOIN cine.Favorite f ON m.movieId = f.movieId AND f.userId = :user_id
                LEFT JOIN cine.Watchlist w ON m.movieId = w.movieId AND w.userId = :user_id
                LEFT JOIN cine.ViewHistory vh ON m.movieId = vh.movieId AND vh.userId = :user_id
                LEFT JOIN cine.Comment c ON m.movieId = c.movieId AND c.userId = :user_id
                LEFT JOIN cine.Rating r2 ON m.movieId = r2.movieId
                WHERE m.movieId IN ({placeholders})
                GROUP BY m.movieId
            """), params).mappings().all()
            
            scores = {}
            for row in interactions:
                movie_id = int(row["movieId"])
                score = calculate_user_interaction_score(
                    user_rating=row["user_rating"] or 0,
                    is_favorite=bool(row["is_favorite"]),
                    is_watchlist=bool(row["is_watchlist"]),
                    has_viewed=bool(row["has_viewed"]),
                    has_commented=bool(row["has_commented"]),
                    total_ratings=row["total_ratings"] or 0,
                    avg_rating=float(row["avg_rating"]) if row["avg_rating"] else 0.0
                )
                scores[movie_id] = score
            
            for movie_id in movie_ids:
                movie_id_int = int(movie_id)
                if movie_id_int not in scores:
                    scores[movie_id_int] = 0.5
        
        recommendations = []
        for movie in movies:
            movie_id = movie["id"]
            score = scores.get(movie_id, 0.5)
            
            stats = all_interaction_stats.get(movie_id, {})
            
            recommendations.append({
                "id": movie["id"],
                "title": movie["title"],
                "poster": movie.get("poster", ""),
                "year": movie.get("year"),
                "country": movie.get("country"),
                "score": score,
                "genres": movie.get("genres", ""),
                "avgRating": movie.get("avgRating", 0),
                "ratingCount": movie.get("ratingCount", 0),
                "viewHistoryCount": stats.get("viewHistoryCount", 0),
                "watchlistCount": stats.get("watchlistCount", 0),
                "favoriteCount": stats.get("favoriteCount", 0),
                "commentCount": stats.get("commentCount", 0),
                "algo": "rating_based",
                "reason": "User rating based recommendation"
            })
        
        recommendations = sort_recommendations(
            recommendations,
            sort_keys=['score', 'avgRating', 'ratingCount']
        )
        return recommendations
        
    except Exception as e:
        current_app.logger.error(f"Error creating rating based recommendations: {e}")
        return []

