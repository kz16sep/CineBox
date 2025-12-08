"""
Recommenders Module
Chứa các recommendation algorithms
"""

# CollaborativeRecommender đã được thay thế bởi EnhancedCFRecommender
# from .collaborative import CollaborativeRecommender
from .collaborative_recommender import EnhancedCFRecommender
from .content_based_recommender import ContentBasedRecommender

__all__ = [
    # 'CollaborativeRecommender',  # Đã được thay thế bởi EnhancedCFRecommender
    'EnhancedCFRecommender',
    'ContentBasedRecommender'
]

