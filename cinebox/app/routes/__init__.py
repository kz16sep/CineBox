"""
Routes Package
Tách routes.py thành nhiều modules để dễ maintain
"""

from flask import Blueprint
from .common import (
    init_recommenders,
    content_recommender,
    enhanced_cf_recommender,
    get_poster_or_dummy,
    set_cf_dirty,
    get_cf_state,
    clear_cf_dirty_and_set_last
)

# Tạo main blueprint
main_bp = Blueprint("main", __name__)

# Import tất cả routes để đăng ký với blueprint
from . import auth
from . import movies
from . import user
from . import interactions
from . import admin
from . import api_recommendations
from . import api_interactions

__all__ = [
    'main_bp', 
    'init_recommenders',
    'get_cf_state',
    'clear_cf_dirty_and_set_last',
    'set_cf_dirty'
]

