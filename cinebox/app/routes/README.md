# Routes Package Structure

Routes đã được tách từ `routes.py` (7300+ dòng) thành nhiều modules nhỏ hơn để dễ maintain.

## Cấu trúc

```
routes/
├── __init__.py              # Export main_bp và init_recommenders
├── common.py                # Shared utilities, helpers, caches
├── decorators.py            # login_required, admin_required decorators
├── auth.py                  # Login, register, logout
├── movies.py                # Home, detail, watch, search, genre, all-movies
├── user.py                  # Account, profile, history
├── interactions.py          # Watchlist, favorites, ratings, comments
├── admin.py                 # Admin dashboard, movies/users management
├── api_recommendations.py   # Recommendation APIs
└── api_interactions.py      # Interaction APIs (view history, etc.)
```

## Routes đã được di chuyển

### ✅ Đã hoàn thành:
- **auth.py**: login, register, logout
- **movies.py**: home, detail, watch, search, genre_page, all_movies
- **user.py**: account, account_history (cơ bản)
- **interactions.py**: add/remove watchlist, add/remove favorite (cơ bản)
- **admin.py**: admin_dashboard, admin_movies, admin_users, admin_model (cơ bản)
- **api_recommendations.py**: get_recommendations, similar_movies, trending_movies (cơ bản)
- **api_interactions.py**: get_view_history, update_watch_progress, submit_rating (cơ bản)

### ⚠️ Cần di chuyển từ routes.py cũ:

#### **routes/user.py** cần thêm:
- `/update-profile` (POST)
- `/update-password` (POST)
- `/api/update-email` (POST)
- `/api/update-username` (POST)
- `/api/update-phone` (POST)
- `/upload-avatar` (POST)
- `/avatar/<filename>` (GET)
- `/remove-history/<int:history_id>` (POST)

#### **routes/interactions.py** cần thêm:
- `/check-watchlist/<int:movie_id>` (GET)
- `/toggle-watchlist/<int:movie_id>` (POST)
- `/api/search-watchlist` (GET)
- `/check-favorite/<int:movie_id>` (GET)
- `/toggle-favorite/<int:movie_id>` (POST)
- `/api/search-favorites` (GET)
- `/get-rating/<int:movie_id>` (GET)
- `/delete-rating/<int:movie_id>` (POST)
- `/submit-comment/<int:movie_id>` (POST)
- `/get-comments/<int:movie_id>` (GET)
- `/update-comment/<int:comment_id>` (POST)
- `/delete-comment/<int:comment_id>` (POST)

#### **routes/admin.py** cần thêm:
- `/admin/movies/create` (GET, POST)
- `/admin/movies/<int:movie_id>/edit` (GET, POST)
- `/admin/movies/<int:movie_id>/delete` (POST)
- `/admin/users/<int:user_id>/toggle-status` (POST)
- `/admin/users/<int:user_id>/delete` (POST)
- `/api/retrain_model` (POST)
- `/api/train_enhanced_cf` (POST)
- `/api/train_cf_model` (POST)
- `/api/model_status` (GET)
- `/api/switch_model/<model_type>` (POST)
- `/api/reload_cf_model` (POST)

#### **routes/api_recommendations.py** cần thêm:
- `/api/generate_recommendations` (POST)
- `/api/personalized_recommendations` (GET)
- `/api/cold_start_recommendations` (GET)
- `/api/hybrid_status` (GET)
- `/api/score_distribution` (GET)
- `/api/user_preference_analysis` (GET)
- `/api/user_data_status` (GET)
- `/api/cleanup_watched_recommendations` (POST)
- `/api/retrain_cf_model` (POST, GET)
- `/api/retrain_cf_model_internal` (POST)
- `/api/model_status_public` (GET)

#### **routes/api_interactions.py** cần thêm:
- `/history` (GET)
- `/api/delete_history_item/<int:history_id>` (DELETE)
- `/api/clear_all_history` (DELETE)
- `/api/user_rating_history` (GET)

#### **routes/movies.py** cần thêm:
- `/reset-view-count/<int:movie_id>` (GET) - có thể di chuyển sang admin.py
- `/api/search/suggestions` (GET)
- `/onboarding` (GET, POST)
- `/api/genres` (GET)
- `/api/actors` (GET)
- `/api/directors` (GET)
- `/api/save_user_preferences` (POST)
- `/api/test-comment-system` (GET)

## Cách di chuyển routes

1. **Mở routes.py cũ** và tìm route function cần di chuyển
2. **Copy toàn bộ function** (bao gồm decorator `@main_bp.route(...)`)
3. **Paste vào file tương ứng** trong routes package
4. **Kiểm tra imports**: Đảm bảo tất cả imports cần thiết đã có
5. **Test**: Chạy app và test route đó hoạt động đúng
6. **Xóa route cũ** khỏi routes.py sau khi đã test thành công

## Lưu ý

- Tất cả routes phải import `main_bp` từ `.` (relative import)
- Sử dụng decorators từ `.decorators` module
- Sử dụng helpers từ `.common` module
- Import helpers từ parent package: `from ..helpers.movie_query_helpers import ...`
- Sau khi di chuyển hết, có thể xóa hoặc backup `routes.py` cũ

## Testing

Sau khi di chuyển routes, test các chức năng:
- [ ] Login/Register/Logout
- [ ] Browse movies (home, detail, watch, search)
- [ ] User account management
- [ ] Watchlist/Favorites
- [ ] Ratings/Comments
- [ ] Admin functions
- [ ] Recommendations APIs
- [ ] View history

