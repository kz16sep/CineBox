"""
Movie Query Helpers
Cung cấp các helper functions để query movie data, tránh code duplication
"""

from sqlalchemy import text
from typing import List, Dict, Optional
from flask import current_app
import logging

logger = logging.getLogger(__name__)


def get_movie_details_query(include_stats: bool = True) -> str:
    """
    Tạo SQL query để lấy movie details với ratings, genres, và stats.
    
    Args:
        include_stats: Nếu True, bao gồm watchlistCount, viewHistoryCount, etc.
    
    Returns:
        SQL query string
    """
    if include_stats:
        return """
            SELECT 
                m.movieId, m.title, m.releaseYear, m.country, m.posterUrl,
                m.viewCount, m.overview, m.backdropUrl,
                AVG(CAST(r.value AS FLOAT)) as avgRating,
                COUNT(r.movieId) as ratingCount,
                COUNT(DISTINCT w.userId) as watchlistCount,
                COUNT(DISTINCT vh.userId) as viewHistoryCount,
                COUNT(DISTINCT f.userId) as favoriteCount,
                COUNT(DISTINCT c.userId) as commentCount,
                STUFF((
                    SELECT TOP 5 ', ' + g2.name
                    FROM cine.MovieGenre mg2
                    JOIN cine.Genre g2 ON mg2.genreId = g2.genreId
                    WHERE mg2.movieId = m.movieId
                    ORDER BY g2.name
                    FOR XML PATH('')
                ), 1, 2, '') as genres
            FROM cine.Movie m
            LEFT JOIN cine.Rating r ON m.movieId = r.movieId
            LEFT JOIN cine.Watchlist w ON m.movieId = w.movieId
            LEFT JOIN cine.ViewHistory vh ON m.movieId = vh.movieId
            LEFT JOIN cine.Favorite f ON m.movieId = f.movieId
            LEFT JOIN cine.Comment c ON m.movieId = c.movieId
            WHERE m.movieId IN ({placeholders})
            GROUP BY m.movieId, m.title, m.releaseYear, m.country, m.posterUrl, m.viewCount, m.overview, m.backdropUrl
        """
    else:
        return """
            SELECT 
                m.movieId, m.title, m.releaseYear, m.country, m.posterUrl,
                m.viewCount, m.overview, m.backdropUrl,
                AVG(CAST(r.value AS FLOAT)) as avgRating,
                COUNT(r.movieId) as ratingCount,
                STUFF((
                    SELECT TOP 5 ', ' + g2.name
                    FROM cine.MovieGenre mg2
                    JOIN cine.Genre g2 ON mg2.genreId = g2.genreId
                    WHERE mg2.movieId = m.movieId
                    ORDER BY g2.name
                    FOR XML PATH('')
                ), 1, 2, '') as genres
            FROM cine.Movie m
            LEFT JOIN cine.Rating r ON m.movieId = r.movieId
            WHERE m.movieId IN ({placeholders})
            GROUP BY m.movieId, m.title, m.releaseYear, m.country, m.posterUrl, m.viewCount, m.overview, m.backdropUrl
        """


def get_movie_details(movie_ids: List[int], db_engine=None, include_stats: bool = True) -> Dict[int, Dict]:
    """
    Lấy thông tin chi tiết của movies từ database.
    
    Args:
        movie_ids: List of movie IDs
        db_engine: SQLAlchemy engine (nếu None, lấy từ current_app)
        include_stats: Nếu True, bao gồm watchlistCount, viewHistoryCount, etc.
    
    Returns:
        Dict[int, Dict]: Movie ID -> Movie details dict
    """
    if not movie_ids:
        return {}
    
    if db_engine is None:
        try:
            db_engine = current_app.db_engine
        except RuntimeError:
            raise RuntimeError("No application context. Use 'with app.app_context():'")
    
    try:
        # Create placeholders for IN clause
        placeholders = ','.join([f':id{i}' for i in range(len(movie_ids))])
        params = {f'id{i}': int(movie_id) for i, movie_id in enumerate(movie_ids)}
        
        query_template = get_movie_details_query(include_stats=include_stats)
        query = text(query_template.format(placeholders=placeholders))
        
        with db_engine.connect() as conn:
            result = conn.execute(query, params)
            movies = {}
            
            for row in result:
                movie_dict = {
                    'movieId': row.movieId,
                    'title': row.title,
                    'releaseYear': row.releaseYear,
                    'country': row.country,
                    'posterUrl': row.posterUrl,
                    'viewCount': row.viewCount,
                    'overview': row.overview,
                    'backdropUrl': row.backdropUrl,
                    'avgRating': round(float(row.avgRating), 2) if row.avgRating else 0.0,
                    'ratingCount': row.ratingCount,
                    'genres': row.genres or ''
                }
                
                if include_stats:
                    movie_dict.update({
                        'watchlistCount': row.watchlistCount,
                        'viewHistoryCount': row.viewHistoryCount,
                        'favoriteCount': row.favoriteCount,
                        'commentCount': row.commentCount,
                    })
                
                movies[row.movieId] = movie_dict
            
            return movies
            
    except Exception as e:
        logger.error(f"Error getting movie details: {e}")
        return {}


def get_movie_rating_stats(movie_ids: List[int], db_engine=None) -> Dict[int, Dict]:
    """
    Lấy rating stats (avgRating, ratingCount) cho một list movie IDs.
    
    Args:
        movie_ids: List of movie IDs
        db_engine: SQLAlchemy engine (nếu None, lấy từ current_app)
    
    Returns:
        Dict[int, Dict]: Movie ID -> {"avgRating": float, "ratingCount": int}
    """
    if not movie_ids:
        return {}
    
    if db_engine is None:
        try:
            db_engine = current_app.db_engine
        except RuntimeError:
            raise RuntimeError("No application context. Use 'with app.app_context():'")
    
    try:
        placeholders = ','.join([f':id{i}' for i in range(len(movie_ids))])
        params = {f'id{i}': int(movie_id) for i, movie_id in enumerate(movie_ids)}
        
        query = text(f"""
            SELECT m.movieId,
                   AVG(CAST(r.value AS FLOAT)) AS avgRating,
                   COUNT(r.movieId) AS ratingCount
            FROM cine.Movie m
            LEFT JOIN cine.Rating r ON m.movieId = r.movieId
            WHERE m.movieId IN ({placeholders})
            GROUP BY m.movieId
        """)
        
        with db_engine.connect() as conn:
            rows = conn.execute(query, params).mappings().all()
        
        # Tạo dict với key là int movieId
        result = {}
        for row in rows:
            movie_id = int(row["movieId"])
            result[movie_id] = {
                "avgRating": round(float(row["avgRating"] or 0), 2) if row["avgRating"] is not None else 0.0,
                "ratingCount": int(row["ratingCount"] or 0),
            }
        
        # Đảm bảo tất cả movie_ids đều có trong dict (với giá trị 0 nếu không có rating)
        for movie_id in movie_ids:
            movie_id_int = int(movie_id)
            if movie_id_int not in result:
                result[movie_id_int] = {
                    "avgRating": 0.0,
                    "ratingCount": 0,
                }
        
        return result
    except Exception as e:
        logger.error(f"Error fetching rating stats: {e}")
        return {}


def get_movie_genres(movie_id: int, db_engine=None) -> List[Dict[str, str]]:
    """
    Lấy danh sách genres của một movie.
    
    Args:
        movie_id: Movie ID
        db_engine: SQLAlchemy engine (nếu None, lấy từ current_app)
    
    Returns:
        List[Dict]: List of genres với {"name": str, "slug": str}
    """
    if db_engine is None:
        try:
            db_engine = current_app.db_engine
        except RuntimeError:
            raise RuntimeError("No application context. Use 'with app.app_context():'")
    
    try:
        query = text("""
            SELECT g.name
            FROM cine.MovieGenre mg
            JOIN cine.Genre g ON mg.genreId = g.genreId
            WHERE mg.movieId = :movie_id
            ORDER BY g.name
        """)
        
        with db_engine.connect() as conn:
            result = conn.execute(query, {"movie_id": movie_id})
            genres = [
                {"name": genre[0], "slug": genre[0].lower().replace(' ', '-')}
                for genre in result
            ]
        
        return genres
    except Exception as e:
        logger.error(f"Error getting movie genres: {e}")
        return []


def get_movies_genres(movie_ids: List[int], db_engine=None) -> Dict[int, str]:
    """
    Lấy genres của nhiều movies trong một query (batch query để tránh N+1).
    
    Args:
        movie_ids: List of movie IDs
        db_engine: SQLAlchemy engine (nếu None, lấy từ current_app)
    
    Returns:
        Dict[int, str]: Movie ID -> genres string (comma-separated)
    """
    if not movie_ids:
        return {}
    
    if db_engine is None:
        try:
            db_engine = current_app.db_engine
        except RuntimeError:
            raise RuntimeError("No application context. Use 'with app.app_context():'")
    
    try:
        placeholders = ','.join([f':id{i}' for i in range(len(movie_ids))])
        params = {f'id{i}': int(movie_id) for i, movie_id in enumerate(movie_ids)}
        
        query = text(f"""
            SELECT 
                mg.movieId,
                STRING_AGG(g.name, ', ') WITHIN GROUP (ORDER BY g.name) as genres
            FROM cine.MovieGenre mg
            JOIN cine.Genre g ON mg.genreId = g.genreId
            WHERE mg.movieId IN ({placeholders})
            GROUP BY mg.movieId
        """)
        
        with db_engine.connect() as conn:
            result = conn.execute(query, params).mappings().all()
        
        return {
            row["movieId"]: row["genres"] or ""
            for row in result
        }
    except Exception as e:
        logger.error(f"Error getting movies genres: {e}")
        return {}


def get_movie_interaction_stats(movie_id: int, db_engine=None) -> Dict[str, int]:
    """
    Lấy thống kê tương tác của movie (viewHistoryCount, watchlistCount, favoriteCount, commentCount).
    
    Args:
        movie_id: Movie ID
        db_engine: SQLAlchemy engine (nếu None, lấy từ current_app)
    
    Returns:
        Dict với các keys: viewHistoryCount, watchlistCount, favoriteCount, commentCount
    """
    stats = get_movies_interaction_stats([movie_id], db_engine)
    return stats.get(movie_id, {
        "viewHistoryCount": 0,
        "watchlistCount": 0,
        "favoriteCount": 0,
        "commentCount": 0,
    })


def get_movies_interaction_stats(movie_ids: List[int], db_engine=None) -> Dict[int, Dict[str, int]]:
    """
    Lấy thống kê tương tác của nhiều movies trong một query (batch query để tránh N+1).
    
    Args:
        movie_ids: List of movie IDs
        db_engine: SQLAlchemy engine (nếu None, lấy từ current_app)
    
    Returns:
        Dict[int, Dict]: Movie ID -> {"viewHistoryCount": int, "watchlistCount": int, ...}
    """
    if not movie_ids:
        return {}
    
    if db_engine is None:
        try:
            db_engine = current_app.db_engine
        except RuntimeError:
            raise RuntimeError("No application context. Use 'with app.app_context():'")
    
    try:
        # Create placeholders for IN clause
        placeholders = ','.join([f':id{i}' for i in range(len(movie_ids))])
        params = {f'id{i}': int(movie_id) for i, movie_id in enumerate(movie_ids)}
        
        query = text(f"""
            SELECT 
                m.movieId,
                COUNT(DISTINCT vh.userId) as viewHistoryCount,
                COUNT(DISTINCT w.userId) as watchlistCount,
                COUNT(DISTINCT f.userId) as favoriteCount,
                COUNT(DISTINCT c.userId) as commentCount
            FROM cine.Movie m
            LEFT JOIN cine.ViewHistory vh ON m.movieId = vh.movieId
            LEFT JOIN cine.Watchlist w ON m.movieId = w.movieId
            LEFT JOIN cine.Favorite f ON m.movieId = f.movieId
            LEFT JOIN cine.Comment c ON m.movieId = c.movieId
            WHERE m.movieId IN ({placeholders})
            GROUP BY m.movieId
        """)
        
        with db_engine.connect() as conn:
            result = conn.execute(query, params).mappings().all()
            
            stats = {}
            for row in result:
                stats[row.movieId] = {
                    "viewHistoryCount": row.viewHistoryCount or 0,
                    "watchlistCount": row.watchlistCount or 0,
                    "favoriteCount": row.favoriteCount or 0,
                    "commentCount": row.commentCount or 0,
                }
            
            # Đảm bảo tất cả movie_ids đều có trong dict (với giá trị 0)
            for movie_id in movie_ids:
                if movie_id not in stats:
                    stats[movie_id] = {
                        "viewHistoryCount": 0,
                        "watchlistCount": 0,
                        "favoriteCount": 0,
                        "commentCount": 0,
                    }
            
            return stats
    except Exception as e:
        logger.error(f"Error getting movies interaction stats: {e}")
        # Return empty stats for all movies
        return {
            movie_id: {
                "viewHistoryCount": 0,
                "watchlistCount": 0,
                "favoriteCount": 0,
                "commentCount": 0,
            }
            for movie_id in movie_ids
        }

