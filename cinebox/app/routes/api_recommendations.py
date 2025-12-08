"""
API routes for recommendations
"""

import random
from flask import jsonify, request, session, current_app
from sqlalchemy import text
from . import main_bp
from .decorators import login_required
from .common import content_recommender, enhanced_cf_recommender


@main_bp.route("/api/get_recommendations")
@login_required
def get_recommendations():
    """Get personalized recommendations for user"""
    user_id = session.get("user_id")
    limit = request.args.get('limit', 10, type=int)
    
    try:
        # Get recommendations from both CF and Content-Based
        cf_recs = []
        cb_recs = []
        
        if enhanced_cf_recommender:
            cf_recs = enhanced_cf_recommender.get_user_recommendations(user_id, limit=limit)
        
        if content_recommender:
            cb_recs = content_recommender.get_user_recommendations(user_id, limit=limit)
        
        # Combine recommendations (simplified - should use hybrid_recommendations helper)
        recommendations = cf_recs[:limit] if cf_recs else cb_recs[:limit]
        
        return jsonify({
            "success": True,
            "recommendations": recommendations,
            "count": len(recommendations)
        })
    except Exception as e:
        current_app.logger.error(f"Error getting recommendations: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@main_bp.route("/api/similar_movies/<int:movie_id>")
def get_similar_movies(movie_id):
    """Get similar movies for a movie"""
    limit = request.args.get('limit', 10, type=int)
    
    try:
        if content_recommender:
            similar = content_recommender.get_related_movies(movie_id, limit=limit)
            return jsonify({"success": True, "movies": similar})
        else:
            return jsonify({"success": False, "message": "Content recommender not available"}), 503
    except Exception as e:
        current_app.logger.error(f"Error getting similar movies: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@main_bp.route("/api/trending_movies")
def get_trending_movies():
    """Get trending movies"""
    limit = request.args.get('limit', 20, type=int)
    
    try:
        if enhanced_cf_recommender:
            trending = enhanced_cf_recommender.get_trending_movies(limit=limit)
            return jsonify({"success": True, "movies": trending})
        else:
            return jsonify({"success": False, "message": "CF recommender not available"}), 503
    except Exception as e:
        current_app.logger.error(f"Error getting trending movies: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@main_bp.route("/api/model_status_public")
def get_model_status_public():
    """Get public model status - không cần login"""
    try:
        status = {
            "cf_model": {
                "available": False,
                "loaded": False,
                "loading": False,
                "error": None,
                "model_exists": False
            },
            "cb_model": {
                "available": False,
                "loaded": True  # Content-based luôn available
            }
        }
        
        # Check CF Model
        if enhanced_cf_recommender:
            cf_status = enhanced_cf_recommender.get_loading_status()
            status["cf_model"] = {
                "available": True,
                "loaded": cf_status.get("model_loaded", False),
                "loading": cf_status.get("loading", False),
                "error": cf_status.get("error"),
                "model_exists": cf_status.get("model_exists", False)
            }
        
        # Check CB Model
        if content_recommender:
            status["cb_model"]["available"] = True
        
        return jsonify({
            "success": True,
            "status": status
        })
    except Exception as e:
        current_app.logger.error(f"Error getting model status: {e}")
        return jsonify({
            "success": False, 
            "message": str(e),
            "status": {
                "cf_model": {"available": False, "loaded": False, "loading": False, "error": str(e)},
                "cb_model": {"available": False, "loaded": False}
            }
        }), 500


@main_bp.route("/api/personalized_recommendations")
@login_required
def get_personalized_recommendations():
    """Get personalized recommendations for logged-in user"""
    user_id = session.get("user_id")
    limit = request.args.get('limit', 10, type=int)
    
    try:
        recommendations = []
        source_info = []
        
        current_app.logger.info(f"Getting recommendations for user {user_id}")
        
        # Kiểm tra CF model trước
        if enhanced_cf_recommender:
            cf_status = enhanced_cf_recommender.get_loading_status()
            current_app.logger.info(f"CF Model status: {cf_status}")
            
            if enhanced_cf_recommender.is_model_loaded():
                try:
                    cf_recs = enhanced_cf_recommender.get_user_recommendations(user_id, limit=limit*2)
                    if cf_recs:
                        recommendations.extend(cf_recs[:limit])
                        source_info.append("cf")
                        current_app.logger.info(f"Got {len(cf_recs)} CF recommendations")
                except Exception as cf_error:
                    current_app.logger.error(f"CF recommendation error: {cf_error}")
        
        # Nếu không có CF hoặc không đủ, dùng Content-Based
        if len(recommendations) < limit and content_recommender:
            try:
                cb_recs = content_recommender.get_user_recommendations(user_id, limit=limit)
                if cb_recs:
                    # Merge và deduplicate
                    existing_ids = {rec.get('movieId') or rec.get('id') for rec in recommendations}
                    for rec in cb_recs:
                        rec_id = rec.get('movieId') or rec.get('id')
                        if rec_id not in existing_ids and len(recommendations) < limit:
                            recommendations.append(rec)
                            existing_ids.add(rec_id)
                    source_info.append("cb")
                    current_app.logger.info(f"Got {len(cb_recs)} CB recommendations")
            except Exception as cb_error:
                current_app.logger.error(f"CB recommendation error: {cb_error}")
        
        # Nếu vẫn không có gợi ý, dùng Cold Start hoặc Popular movies
        if len(recommendations) == 0:
            try:
                with current_app.db_engine.connect() as conn:
                    # Kiểm tra số lượng interactions của user
                    interaction_counts = conn.execute(text("""
                        SELECT 
                            (SELECT COUNT(*) FROM cine.Rating WHERE userId = :user_id) as rating_count,
                            (SELECT COUNT(*) FROM cine.ViewHistory WHERE userId = :user_id) as view_count
                    """), {"user_id": user_id}).mappings().first()
                    
                    total_interactions = (interaction_counts.rating_count or 0) + (interaction_counts.view_count or 0)
                    current_app.logger.info(f"User {user_id} has {total_interactions} total interactions")
                    
                    # Nếu user mới (ít interactions), dùng Cold Start
                    if total_interactions < 5:
                        from .common import get_cold_start_recommendations
                        cold_start_recs = get_cold_start_recommendations(user_id, conn)
                        
                        for rec in cold_start_recs:
                            recommendations.append({
                                "movieId": rec["id"],
                                "id": rec["id"],
                                "title": rec["title"],
                                "poster": rec["poster"],
                                "posterUrl": rec["poster"],
                                "releaseYear": rec["releaseYear"],
                                "country": rec["country"],
                                "avgRating": rec["avgRating"],
                                "ratingCount": rec["ratingCount"],
                                "genres": rec.get("genres", ""),
                                "reason": rec.get("reason", "Dựa trên sở thích của bạn"),
                                "source": "cold_start",
                                "hybrid_score": rec.get("score", 0.9)
                            })
                        source_info.append("cold_start")
                        current_app.logger.info(f"Used cold start: {len(cold_start_recs)} recommendations")
                    
                    # Nếu vẫn không có hoặc user có ít interactions, lấy phim phổ biến
                    if len(recommendations) < limit:
                        remaining_limit = limit - len(recommendations)
                        popular_movies = conn.execute(text("""
                            WITH rating_stats AS (
                                SELECT movieId,
                                       AVG(CAST(value AS FLOAT)) AS avgRating,
                                       COUNT(*) AS ratingCount
                                FROM cine.Rating
                                GROUP BY movieId
                            )
                            SELECT TOP (:limit) 
                                m.movieId,
                                m.title,
                                m.posterUrl AS poster,
                                m.releaseYear,
                                m.country,
                                rs.avgRating,
                                rs.ratingCount,
                                STRING_AGG(g.name, ', ') AS genres
                            FROM cine.Movie m
                            JOIN rating_stats rs ON rs.movieId = m.movieId
                            LEFT JOIN cine.MovieGenre mg ON m.movieId = mg.movieId
                            LEFT JOIN cine.Genre g ON mg.genreId = g.genreId
                            WHERE rs.avgRating >= 4.0
                              AND rs.ratingCount >= 10
                              AND m.movieId NOT IN (
                                  SELECT vh.movieId FROM cine.ViewHistory vh WHERE vh.userId = :user_id
                              )
                            GROUP BY m.movieId, m.title, m.posterUrl, m.releaseYear, 
                                     m.country, rs.avgRating, rs.ratingCount
                            ORDER BY rs.avgRating DESC, rs.ratingCount DESC
                        """), {"limit": remaining_limit, "user_id": user_id}).mappings().all()
                        
                        existing_ids = {rec.get('movieId') or rec.get('id') for rec in recommendations}
                        for movie in popular_movies:
                            if movie["movieId"] not in existing_ids:
                                recommendations.append({
                                    "movieId": movie["movieId"],
                                    "id": movie["movieId"],
                                    "title": movie["title"],
                                    "poster": movie["poster"],
                                    "posterUrl": movie["poster"],
                                    "releaseYear": movie["releaseYear"],
                                    "country": movie["country"],
                                    "avgRating": movie["avgRating"] or 0,
                                    "ratingCount": movie["ratingCount"] or 0,
                                    "genres": movie["genres"],
                                    "reason": "Phim phổ biến với đánh giá cao",
                                    "source": "popular",
                                    "hybrid_score": 0.8
                                })
                        source_info.append("popular")
                        current_app.logger.info(f"Added {len(popular_movies)} popular movies")
                        
            except Exception as fallback_error:
                current_app.logger.error(f"Fallback recommendations error: {fallback_error}")
        
        # Random shuffle recommendations để không theo score tăng dần
        if recommendations:
            random.shuffle(recommendations)
            current_app.logger.info(f"Shuffled {len(recommendations)} recommendations for randomization")
        
        # Kiểm tra số lượng rating của user để thông báo
        user_rating_count = 0
        try:
            with current_app.db_engine.connect() as conn:
                user_rating_count = conn.execute(text("""
                    SELECT COUNT(*) FROM [cine].[Rating] WHERE userId = :user_id
                """), {"user_id": user_id}).scalar() or 0
        except Exception:
            pass
        
        return jsonify({
            "success": True,
            "recommendations": recommendations,
            "count": len(recommendations),
            "source": "+".join(source_info) if source_info else "none",
            "userRatingCount": user_rating_count,
            "message": f"Tìm thấy {len(recommendations)} gợi ý từ {'+'.join(source_info) if source_info else 'none'}"
        })
    except Exception as e:
        current_app.logger.error(f"Error getting personalized recommendations: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


@main_bp.route("/api/hybrid_status")
def get_hybrid_status():
    """Get hybrid recommendation system status"""
    try:
        cf_available = False
        cf_loaded = False
        cb_available = False
        
        if enhanced_cf_recommender:
            cf_available = True
            cf_loaded = enhanced_cf_recommender.is_model_loaded()
        
        if content_recommender:
            cb_available = True
        
        return jsonify({
            "success": True,
            "cf_available": cf_available,
            "cf_loaded": cf_loaded,
            "cb_available": cb_available,
            "hybrid_ready": cf_loaded or cb_available
        })
    except Exception as e:
        current_app.logger.error(f"Error getting hybrid status: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@main_bp.route("/api/cold_start_recommendations")
@login_required
def get_cold_start_recommendations_api():
    """Get cold start recommendations for new users"""
    user_id = session.get("user_id")
    limit = request.args.get('limit', 10, type=int)
    
    try:
        with current_app.db_engine.connect() as conn:
            from .common import get_cold_start_recommendations
            
            # Lấy cold start recommendations
            cold_start_recs = get_cold_start_recommendations(user_id, conn)
            
            # Format cho API response
            recommendations = []
            for rec in cold_start_recs[:limit]:
                recommendations.append({
                    "movieId": rec["id"],
                    "id": rec["id"],
                    "title": rec["title"],
                    "poster": rec["poster"],
                    "posterUrl": rec["poster"],
                    "releaseYear": rec["releaseYear"],
                    "country": rec["country"],
                    "avgRating": rec["avgRating"],
                    "ratingCount": rec["ratingCount"],
                    "genres": rec.get("genres", ""),
                    "reason": rec.get("reason", "Dựa trên sở thích của bạn"),
                    "source": "cold_start"
                })
            
            return jsonify({
                "success": True,
                "recommendations": recommendations,
                "count": len(recommendations),
                "source": "cold_start"
            })
            
    except Exception as e:
        current_app.logger.error(f"Error getting cold start recommendations: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


@main_bp.route("/api/user_interaction_status")
@login_required
def get_user_interaction_status():
    """Get user interaction status for cold start logic"""
    user_id = session.get("user_id")
    
    try:
        with current_app.db_engine.connect() as conn:
            interaction_counts = conn.execute(text("""
                SELECT 
                    (SELECT COUNT(*) FROM cine.Rating WHERE userId = :user_id) as rating_count,
                    (SELECT COUNT(*) FROM cine.ViewHistory WHERE userId = :user_id) as view_count,
                    (SELECT COUNT(*) FROM cine.Favorite WHERE userId = :user_id) as favorite_count,
                    (SELECT COUNT(*) FROM cine.Watchlist WHERE userId = :user_id) as watchlist_count
            """), {"user_id": user_id}).mappings().first()
            
            return jsonify({
                "success": True,
                "rating_count": interaction_counts.rating_count or 0,
                "view_count": interaction_counts.view_count or 0,
                "favorite_count": interaction_counts.favorite_count or 0,
                "watchlist_count": interaction_counts.watchlist_count or 0,
                "total_interactions": (interaction_counts.rating_count or 0) + (interaction_counts.view_count or 0)
            })
            
    except Exception as e:
        current_app.logger.error(f"Error getting user interaction status: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


# Note: Các API routes còn lại (score_distribution, etc.) 
# sẽ được di chuyển từ routes.py sang đây

