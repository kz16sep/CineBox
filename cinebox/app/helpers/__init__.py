"""
Helper utilities for database access, movie queries, recommendations, and SQL safety.
"""

from .db import get_db_connection, get_db_transaction, execute_query, execute_many_queries, get_pool_status
from .movie_query_helpers import (
    get_movie_details_query,
    get_movie_details,
    get_movie_rating_stats,
    get_movie_genres,
    get_movies_genres,
    get_movie_interaction_stats,
    get_movies_interaction_stats,
)
from .recommendation_helpers import (
    sort_recommendations,
    calculate_user_interaction_score,
    format_recommendation,
    merge_recommendations,
    hybrid_recommendations,
)
from .sql_helpers import validate_limit, validate_table_name, safe_top_clause, safe_table_name

__all__ = [
    "get_db_connection",
    "get_db_transaction",
    "execute_query",
    "execute_many_queries",
    "get_pool_status",
    "get_movie_details_query",
    "get_movie_details",
    "get_movie_rating_stats",
    "get_movie_genres",
    "get_movies_genres",
    "get_movie_interaction_stats",
    "get_movies_interaction_stats",
    "sort_recommendations",
    "calculate_user_interaction_score",
    "format_recommendation",
    "merge_recommendations",
    "hybrid_recommendations",
    "validate_limit",
    "validate_table_name",
    "safe_top_clause",
    "safe_table_name",
]

