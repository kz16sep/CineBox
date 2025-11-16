"""
API routes for recommendations
"""

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


# Note: Các API routes còn lại (personalized_recommendations, cold_start_recommendations, 
# hybrid_status, score_distribution, etc.) sẽ được di chuyển từ routes.py sang đây

