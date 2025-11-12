"""
Recommenders Module
Chứa các recommendation algorithms
"""

# CollaborativeRecommender đã được thay thế bởi EnhancedCFRecommender
# from .collaborative import CollaborativeRecommender
from .enhanced_cf import EnhancedCFRecommender
from .content_based import ContentBasedRecommender

__all__ = [
    # 'CollaborativeRecommender',  # Đã được thay thế bởi EnhancedCFRecommender
    'EnhancedCFRecommender',
    'ContentBasedRecommender'
]

