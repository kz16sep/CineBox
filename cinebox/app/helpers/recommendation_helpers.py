"""
Recommendation Helpers
Cung cấp các helper functions cho recommendation logic, tránh code duplication
"""

from typing import List, Dict, Callable, Optional
import logging
import numpy as np

logger = logging.getLogger(__name__)


def sort_recommendations(
    recommendations: List[Dict],
    sort_keys: Optional[List[str]] = None,
    reverse: bool = True
) -> List[Dict]:
    """
    Sắp xếp recommendations theo nhiều keys.
    
    Args:
        recommendations: List of recommendation dicts
        sort_keys: List of keys để sort (mặc định: ['recommendation_score', 'avgRating', 'ratingCount'])
        reverse: Nếu True, sort descending (mặc định: True)
    
    Returns:
        Sorted list of recommendations
    """
    if not recommendations:
        return []
    
    if sort_keys is None:
        sort_keys = ['recommendation_score', 'avgRating', 'ratingCount']
    
    def sort_key_func(rec: Dict) -> tuple:
        """Tạo sort key tuple từ recommendation dict"""
        return tuple(
            rec.get(key, 0) if isinstance(rec.get(key, 0), (int, float)) else 0
            for key in sort_keys
        )
    
    try:
        sorted_recs = sorted(recommendations, key=sort_key_func, reverse=reverse)
        return sorted_recs
    except Exception as e:
        logger.error(f"Error sorting recommendations: {e}")
        return recommendations


def calculate_user_interaction_score(
    user_rating: float = 0,
    is_favorite: bool = False,
    is_watchlist: bool = False,
    has_viewed: bool = False,
    has_commented: bool = False,
    avg_rating: float = 0,
    total_ratings: int = 0,
    weights: Optional[Dict[str, float]] = None
) -> float:
    """
    Tính điểm recommendation dựa trên user interactions với movie.
    
    Args:
        user_rating: Rating của user (0-5)
        is_favorite: User đã favorite movie này
        is_watchlist: User đã thêm vào watchlist
        has_viewed: User đã xem movie này
        has_commented: User đã comment
        avg_rating: Rating trung bình của movie (0-5)
        total_ratings: Tổng số ratings của movie
        weights: Custom weights dict (nếu None, dùng default weights)
    
    Returns:
        float: Score từ 0.0 đến 2.0
    """
    # Default weights
    if weights is None:
        weights = {
            'user_rating': 0.4,      # 40% - Rating của user
            'favorite': 0.3,          # 30% - Favorite
            'watchlist': 0.25,        # 25% - Watchlist
            'view_history': 0.2,      # 20% - View history
            'comment': 0.15,          # 15% - Comment
            'avg_rating': 0.1,        # 10% - Average rating
            'popularity': 0.1,        # 10% - Popularity (max)
        }
    
    score = 0.0
    
    # 1. Rating của user (trọng số cao nhất)
    if user_rating > 0:
        score += (user_rating / 5.0) * weights['user_rating']
    
    # 2. Favorite (trọng số cao)
    if is_favorite:
        score += weights['favorite']
    
    # 3. Watchlist (trọng số cao)
    if is_watchlist:
        score += weights['watchlist']
    
    # 4. View History (trọng số trung bình)
    if has_viewed:
        score += weights['view_history']
    
    # 5. Comments (trọng số trung bình)
    if has_commented:
        score += weights['comment']
    
    # 6. Rating trung bình của phim (trọng số thấp)
    if avg_rating > 0:
        score += (avg_rating / 5.0) * weights['avg_rating']
    
    # 7. Độ phổ biến (số lượng rating)
    if total_ratings > 0:
        popularity_bonus = min(total_ratings / 100.0, weights['popularity'])
        score += popularity_bonus
    
    # Đảm bảo điểm trong khoảng 0-2.0
    score = max(0.0, min(score, 2.0))
    
    return round(score, 3)


def format_recommendation(
    movie: Dict,
    recommendation_score: float = 0.0,
    include_stats: bool = True
) -> Dict:
    """
    Format recommendation dict với đầy đủ thông tin.
    
    Args:
        movie: Movie dict từ database
        recommendation_score: Score từ recommendation algorithm
        include_stats: Nếu True, bao gồm stats (watchlistCount, etc.)
    
    Returns:
        Formatted recommendation dict
    """
    rec = {
        "id": movie.get("movieId"),
        "movieId": movie.get("movieId"),
        "title": movie.get("title"),
        "releaseYear": movie.get("releaseYear"),
        "country": movie.get("country", "Unknown"),
        "posterUrl": movie.get("posterUrl"),
        "backdropUrl": movie.get("backdropUrl"),
        "overview": movie.get("overview", ""),
        "viewCount": movie.get("viewCount", 0),
        "avgRating": round(float(movie.get("avgRating", 0)), 2),
        "ratingCount": movie.get("ratingCount", 0),
        "genres": movie.get("genres", ""),
        "score": recommendation_score,
        "recommendation_score": recommendation_score,
    }
    
    if include_stats:
        rec.update({
            "watchlistCount": movie.get("watchlistCount", 0),
            "viewHistoryCount": movie.get("viewHistoryCount", 0),
            "favoriteCount": movie.get("favoriteCount", 0),
            "commentCount": movie.get("commentCount", 0),
        })
    
    return rec


def merge_recommendations(
    *recommendation_lists: List[Dict],
    deduplicate: bool = True,
    sort: bool = True,
    limit: Optional[int] = None
) -> List[Dict]:
    """
    Merge nhiều recommendation lists thành một list.
    
    Args:
        *recommendation_lists: Multiple recommendation lists
        deduplicate: Nếu True, loại bỏ duplicates (dựa trên movieId)
        sort: Nếu True, sort theo recommendation_score
        limit: Giới hạn số lượng recommendations (nếu None, không giới hạn)
    
    Returns:
        Merged và sorted recommendation list
    """
    if not recommendation_lists:
        return []
    
    # Merge tất cả lists
    merged = []
    for rec_list in recommendation_lists:
        merged.extend(rec_list)
    
    # Deduplicate nếu cần
    if deduplicate:
        seen = set()
        unique = []
        for rec in merged:
            movie_id = rec.get("movieId") or rec.get("id")
            if movie_id and movie_id not in seen:
                seen.add(movie_id)
                unique.append(rec)
        merged = unique
    
    # Sort nếu cần
    if sort:
        merged = sort_recommendations(merged)
    
    # Limit nếu cần
    if limit is not None and limit > 0:
        merged = merged[:limit]
    
    return merged


def hybrid_recommendations(
    cf_recommendations: List[Dict],
    cb_recommendations: List[Dict],
    cf_weight: float = 0.6,
    cb_weight: float = 0.4,
    limit: int = 10,
    alpha: Optional[float] = None
) -> List[Dict]:
    """
    Kết hợp Collaborative Filtering và Content-Based recommendations thành hybrid recommendations.
    
    Args:
        cf_recommendations: Recommendations từ CF model (có 'score' hoặc 'similarity')
        cb_recommendations: Recommendations từ Content-Based (có 'score' hoặc 'similarity')
        cf_weight: Trọng số cho CF recommendations (0.0 - 1.0, mặc định: 0.6)
        cb_weight: Trọng số cho Content-Based recommendations (0.0 - 1.0, mặc định: 0.4)
        limit: Số lượng recommendations tối đa (mặc định: 10)
        alpha: Công tắc kiểm thử (0.0 - 1.0):
               - alpha=1.0 → chỉ dùng CF (cf_weight=1.0, cb_weight=0.0)
               - alpha=0.0 → chỉ dùng CB (cf_weight=0.0, cb_weight=1.0)
               - alpha=None → dùng cf_weight và cb_weight như bình thường
    
    Returns:
        List[Dict]: Hybrid recommendations đã được merge và sort
    """
    # Xử lý công tắc kiểm thử alpha
    if alpha is not None:
        # Clamp alpha về 0.0 - 1.0
        alpha = max(0.0, min(1.0, float(alpha)))
        # alpha=1 → chỉ CF, alpha=0 → chỉ CB
        cf_weight = alpha
        cb_weight = 1.0 - alpha
    
    # Xử lý trường hợp không có recommendations
    if not cf_recommendations and not cb_recommendations:
        return []
    
    # Nếu chỉ có một source, điều chỉnh weights (override alpha nếu cần)
    if not cf_recommendations:
        # Chỉ có CB, dùng CB 100%
        cb_weight = 1.0
        cf_weight = 0.0
    elif not cb_recommendations:
        # Chỉ có CF, dùng CF 100%
        cf_weight = 1.0
        cb_weight = 0.0
    
    # Normalize weights (chỉ khi cả hai đều có recommendations)
    if cf_recommendations and cb_recommendations:
        total_weight = cf_weight + cb_weight
        if total_weight > 0:
            cf_weight = cf_weight / total_weight
            cb_weight = cb_weight / total_weight
        else:
            cf_weight = 0.5
            cb_weight = 0.5
    
    # Lấy original scores (không normalize) để hiển thị
    def get_original_score(rec: Dict) -> float:
        """Lấy original score từ recommendation dict (không normalize)"""
        # Ưu tiên: recommendation_score > score > similarity
        recommendation_score = rec.get('recommendation_score')
        score = rec.get('score')
        similarity = rec.get('similarity')
        
        # Kiểm tra và convert về float với validation NaN/Inf
        def safe_float(value) -> Optional[float]:
            """Convert value to float và kiểm tra NaN/Inf"""
            if value is None:
                return None
            try:
                val = float(value)
                # Kiểm tra NaN và Inf (sử dụng math thay vì np để tránh scope issue)
                import math
                import logging
                _logger = logging.getLogger(__name__)  # Tạo logger trong scope local
                if math.isnan(val) or math.isinf(val):
                    _logger.warning(f"Invalid score detected: {val} (NaN or Inf)")
                    return None
                if val > 0:
                    return val
            except (ValueError, TypeError):
                return None
            return None
        
        # Thử các nguồn score
        val = safe_float(recommendation_score)
        if val is not None:
            return val
        
        val = safe_float(score)
        if val is not None:
            return val
        
        val = safe_float(similarity)
        if val is not None:
            return val
        
        return 0.0
    
    # Lấy raw scores từ cả hai sources
    # CF scores có thể là 0-5 (recommendation_score), CB scores là 0-1 (similarity)
    cf_raw_scores = [get_original_score(rec) for rec in cf_recommendations]
    cb_raw_scores = [get_original_score(rec) for rec in cb_recommendations]
    
    # Debug: Kiểm tra scores
    import logging
    logger = logging.getLogger(__name__)
    
    cf_scores_gt_zero = [s for s in cf_raw_scores if s > 0]
    cb_scores_gt_zero = [s for s in cb_raw_scores if s > 0]
    if len(cf_scores_gt_zero) == 0 and len(cb_scores_gt_zero) == 0:
        # Nếu cả CF và CB đều không có scores > 0, có thể có vấn đề
        logger.warning(f"Hybrid recommendations: CF có {len(cf_raw_scores)} scores (tất cả = 0), CB có {len(cb_raw_scores)} scores (tất cả = 0)")
    
    # Cải thiện normalization để phân phối điểm trải đều hơn
    # Sử dụng percentile-based normalization thay vì min-max để tránh tụ một cục
    
    import numpy as np
    
    # Xác định range cho CF scores sử dụng percentile để tránh outliers
    if cf_raw_scores and len(cf_scores_gt_zero) > 0:
        cf_scores_array = np.array(cf_scores_gt_zero)
        # Dùng percentile 5% và 95% để loại bỏ outliers
        cf_min = np.percentile(cf_scores_array, 5)
        cf_max = np.percentile(cf_scores_array, 95)
        # Nếu percentile quá gần nhau, dùng min-max thực tế
        if cf_max - cf_min < 0.01:
            cf_min = min(cf_scores_gt_zero)
            cf_max = max(cf_scores_gt_zero)
        # Nếu vẫn quá gần, dùng fixed range
        if cf_max - cf_min < 0.01:
            cf_max = 5.0
            cf_min = 0.0
        # Log để debug
        logger.debug(f"CF normalization range: min={cf_min:.4f}, max={cf_max:.4f}, "
                    f"actual_min={min(cf_scores_gt_zero):.4f}, actual_max={max(cf_scores_gt_zero):.4f}")
    else:
        # Nếu không có CF scores, set default range
        cf_max = 5.0
        cf_min = 0.0
    cf_range = cf_max - cf_min if cf_max > cf_min else 5.0
    
    # CB scores - cũng dùng percentile
    if cb_raw_scores and len(cb_scores_gt_zero) > 0:
        cb_scores_array = np.array(cb_scores_gt_zero)
        # Dùng percentile 5% và 95% để loại bỏ outliers
        cb_min = np.percentile(cb_scores_array, 5)
        cb_max = np.percentile(cb_scores_array, 95)
        # Nếu percentile quá gần nhau, dùng min-max thực tế
        if cb_max - cb_min < 0.01:
            cb_min = min(cb_scores_gt_zero)
            cb_max = max(cb_scores_gt_zero)
        # Nếu vẫn quá gần, dùng fixed range
        if cb_max - cb_min < 0.01:
            cb_max = 1.0
            cb_min = 0.0
        # Log để debug
        logger.debug(f"CB normalization range: min={cb_min:.4f}, max={cb_max:.4f}, "
                    f"actual_min={min(cb_scores_gt_zero):.4f}, actual_max={max(cb_scores_gt_zero):.4f}")
    else:
        # Nếu không có CB scores, set default range
        cb_max = 1.0
        cb_min = 0.0
    cb_range = cb_max - cb_min if cb_max > cb_min else 1.0
    
    # Helper function để normalize CF score về 0-1 với percentile-based normalization
    def normalize_cf_score(score: float) -> float:
        """Normalize CF score về range 0-1 với percentile-based normalization"""
        # Kiểm tra NaN/Inf
        if score <= 0 or np.isnan(score) or np.isinf(score):
            return 0.0
        # Normalize với percentile range (loại bỏ outliers)
        # Clamp score vào percentile range trước khi normalize
        clamped_score = max(cf_min, min(cf_max, score))
        normalized = (clamped_score - cf_min) / cf_range if cf_range > 0 else 0.0
        # Clamp về 0-1 và tránh giá trị quá gần 1 (< 0.98)
        normalized = min(0.98, max(0.0, normalized))
        return float(normalized)
    
    # Helper function để normalize CB score về 0-1 với percentile-based normalization
    def normalize_cb_score(score: float) -> float:
        """Normalize CB score về range 0-1 với percentile-based normalization"""
        # Kiểm tra NaN/Inf
        if score <= 0 or np.isnan(score) or np.isinf(score):
            return 0.0
        # Normalize với percentile range (loại bỏ outliers)
        # Clamp score vào percentile range trước khi normalize
        clamped_score = max(cb_min, min(cb_max, score))
        normalized = (clamped_score - cb_min) / cb_range if cb_range > 0 else 0.0
        # Clamp về 0-1 và tránh giá trị quá gần 1 (< 0.98) để tránh similarity = 100%
        normalized = min(0.98, max(0.0, normalized))
        return float(normalized)
    
    # Log phân phối để debug
    if cf_scores_gt_zero:
        logger.debug(f"CF score distribution: min={min(cf_scores_gt_zero):.4f}, "
                    f"p5={cf_min:.4f}, p95={cf_max:.4f}, max={max(cf_scores_gt_zero):.4f}")
    if cb_scores_gt_zero:
        logger.debug(f"CB score distribution: min={min(cb_scores_gt_zero):.4f}, "
                    f"p5={cb_min:.4f}, p95={cb_max:.4f}, max={max(cb_scores_gt_zero):.4f}")
    
    # Merge recommendations với hybrid scores
    movie_scores = {}  # movieId -> hybrid_score
    
    # Add CF recommendations
    for rec in cf_recommendations:
        movie_id = rec.get('movieId') or rec.get('id')
        if not movie_id:
            continue
        
        # Original score (không normalize) để hiển thị
        original_cf_score = get_original_score(rec)
        
        # Normalized score (cho tính toán hybrid) - normalize về 0-1
        normalized_cf_score = normalize_cf_score(original_cf_score)
        hybrid_score = normalized_cf_score * cf_weight
        
        if movie_id not in movie_scores:
            movie_scores[movie_id] = {
                'movie': rec,
                'hybrid_score': 0.0,
                'cf_score_original': 0.0,  # Original CF score (không normalize)
                'cb_score_original': 0.0,  # Original CB score (không normalize)
                'cf_score_normalized': 0.0,  # Normalized CF score (0-1)
                'cb_score_normalized': 0.0   # Normalized CB score (0-1)
            }
        
        movie_scores[movie_id]['hybrid_score'] += hybrid_score
        movie_scores[movie_id]['cf_score_original'] = original_cf_score
        movie_scores[movie_id]['cf_score_normalized'] = normalized_cf_score
    
    # Add CB recommendations
    for rec in cb_recommendations:
        movie_id = rec.get('movieId') or rec.get('id')
        if not movie_id:
            continue
        
        # Original score (không normalize) để hiển thị
        original_cb_score = get_original_score(rec)
        
        # Normalized score (cho tính toán hybrid) - normalize về 0-1
        normalized_cb_score = normalize_cb_score(original_cb_score)
        hybrid_score = normalized_cb_score * cb_weight
        
        if movie_id not in movie_scores:
            movie_scores[movie_id] = {
                'movie': rec,
                'hybrid_score': 0.0,
                'cf_score_original': 0.0,
                'cb_score_original': 0.0,
                'cf_score_normalized': 0.0,
                'cb_score_normalized': 0.0
            }
        
        movie_scores[movie_id]['hybrid_score'] += hybrid_score
        movie_scores[movie_id]['cb_score_original'] = original_cb_score
        movie_scores[movie_id]['cb_score_normalized'] = normalized_cb_score
    
    # Convert to list và sort theo hybrid_score
    hybrid_recs = []
    hybrid_scores_list = []
    for movie_id, data in movie_scores.items():
        # Kiểm tra NaN/Inf trong hybrid_score
        hybrid_score = data['hybrid_score']
        if np.isnan(hybrid_score) or np.isinf(hybrid_score):
            logger.warning(f"Invalid hybrid_score detected for movie {movie_id}: {hybrid_score}")
            hybrid_score = 0.0
        
        movie = data['movie'].copy()
        # Hybrid score (normalized, 0-1) - clamp để tránh quá gần 1
        hybrid_score = min(0.98, max(0.0, hybrid_score))
        movie['hybrid_score'] = round(hybrid_score, 4)
        hybrid_scores_list.append(hybrid_score)
        # Original scores (không normalize) để hiển thị
        movie['cf_score'] = round(data['cf_score_original'], 4)
        movie['cb_score'] = round(data['cb_score_original'], 4)
        # Normalized scores (0-1) để reference và logging
        movie['cf_score_normalized'] = round(data['cf_score_normalized'], 4)
        movie['cb_score_normalized'] = round(data['cb_score_normalized'], 4)
        # Main score = hybrid_score (để tương thích với code cũ)
        movie['score'] = hybrid_score
        # Logging chi tiết: lưu alpha và các scores để tái hiện
        movie['_alpha'] = cf_weight  # Lưu alpha (cf_weight) để debug
        hybrid_recs.append(movie)
    
    # Sort theo hybrid_score descending
    hybrid_recs.sort(key=lambda x: x.get('hybrid_score', 0), reverse=True)
    
    # Kiểm tra và cải thiện phân phối nếu cần (tránh tụ một cục)
    if hybrid_scores_list and len(hybrid_scores_list) > 1:
        hybrid_scores_array = np.array(hybrid_scores_list)
        score_min = float(np.min(hybrid_scores_array))
        score_max = float(np.max(hybrid_scores_array))
        score_mean = float(np.mean(hybrid_scores_array))
        score_std = float(np.std(hybrid_scores_array))
        score_range = score_max - score_min
        
        # Log phân phối ban đầu
        logger.info(f"Hybrid scores BEFORE re-scaling: min={score_min:.4f}, max={score_max:.4f}, "
                    f"mean={score_mean:.4f}, std={score_std:.4f}, range={score_range:.4f}")
        
        # Nếu phân phối quá tụ một cục (range < 0.1 hoặc std < 0.05), áp dụng re-scaling
        if score_range < 0.1 or score_std < 0.05:
            logger.warning(f"Hybrid scores too clustered: range={score_range:.4f}, std={score_std:.4f}, "
                          f"min={score_min:.4f}, max={score_max:.4f}, mean={score_mean:.4f}")
            
            # Re-scale để phân phối tốt hơn: map về 0.1-0.9 range (tránh 0 và 1)
            if score_range > 0.0001:  # Có sự khác biệt đáng kể
                for rec in hybrid_recs:
                    old_score = rec['hybrid_score']
                    # Map từ [min, max] về [0.1, 0.9]
                    new_score = 0.1 + (old_score - score_min) / score_range * 0.8
                    # Đảm bảo không vượt quá 0.9
                    new_score = min(0.9, max(0.1, new_score))
                    rec['hybrid_score'] = round(new_score, 4)
                    rec['score'] = new_score
            else:
                # Nếu tất cả scores giống nhau (hoặc gần như giống nhau), phân phối đều trong [0.3, 0.7]
                num_recs = len(hybrid_recs)
                for i, rec in enumerate(hybrid_recs):
                    if num_recs > 1:
                        new_score = 0.3 + (i / (num_recs - 1)) * 0.4
                    else:
                        new_score = 0.5  # Nếu chỉ có 1 item
                    rec['hybrid_score'] = round(new_score, 4)
                    rec['score'] = new_score
            
            # Re-sort sau khi re-scale
            hybrid_recs.sort(key=lambda x: x.get('hybrid_score', 0), reverse=True)
            
            # Log phân phối sau khi re-scale
            new_scores = [rec['hybrid_score'] for rec in hybrid_recs]
            logger.info(f"Re-scaled hybrid scores: new_range={max(new_scores)-min(new_scores):.4f}, "
                       f"new_mean={np.mean(new_scores):.4f}, new_std={np.std(new_scores):.4f}, "
                       f"new_min={min(new_scores):.4f}, new_max={max(new_scores):.4f}")
        else:
            logger.debug(f"Hybrid score distribution OK: range={score_range:.4f}, std={score_std:.4f}, "
                       f"mean={score_mean:.4f}, min={score_min:.4f}, max={score_max:.4f}")
    
    # Log một vài scores cuối cùng để debug
    if hybrid_recs:
        logger.info(f"Sample hybrid scores (top 5): {[round(rec['hybrid_score'], 4) for rec in hybrid_recs[:5]]}")
    
    # Limit
    if limit > 0:
        hybrid_recs = hybrid_recs[:limit]
    
    return hybrid_recs

