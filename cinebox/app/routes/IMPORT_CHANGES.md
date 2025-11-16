# Thay đổi Import Paths

## ✅ Đã cập nhật

### 1. **run.py**
- **Trước**: `from app.routes import get_cf_state, clear_cf_dirty_and_set_last`
- **Sau**: `from app.routes.common import get_cf_state, clear_cf_dirty_and_set_last`

### 2. **routes.py (cũ)**
- **Trước**: `from app.routes import init_recommenders`
- **Sau**: `from app.routes.common import init_recommenders`

### 3. **routes/common.py**
- ✅ Đã thêm các CF helper functions: `set_cf_dirty`, `get_cf_state`, `clear_cf_dirty_and_set_last`
- ✅ Cải thiện sys.path handling để import recommenders đúng cách
- ✅ Export các functions này trong `__init__.py`

### 4. **routes/movies.py**
- ✅ Import từ `..movie_query_helpers` → `app.movie_query_helpers` (sau khi thêm sys.path)
- ✅ Import `recommenders` sau khi setup sys.path

### 5. **routes/__init__.py**
- ✅ Export thêm: `get_cf_state`, `clear_cf_dirty_and_set_last`, `set_cf_dirty`

## 📋 Cấu trúc Import trong routes package

### Relative imports (trong routes package):
```python
from . import main_bp                    # Import blueprint
from .decorators import login_required    # Import decorators
from .common import get_poster_or_dummy  # Import shared utilities
```

### Absolute imports (từ parent packages):
```python
# Sau khi setup sys.path
from recommenders.content_based import ContentBasedRecommender
from app.movie_query_helpers import get_movie_rating_stats
```

### Import từ routes package (từ bên ngoài):
```python
# Từ run.py hoặc các file khác
from app.routes.common import get_cf_state, clear_cf_dirty_and_set_last
from app.routes import main_bp, init_recommenders
```

## ⚠️ Lưu ý

1. **sys.path setup**: Các file trong routes package cần setup sys.path để import từ `recommenders` và `app` packages
2. **Relative imports**: Sử dụng `.` cho imports trong cùng package
3. **Absolute imports**: Sử dụng absolute imports sau khi setup sys.path
4. **Parent package imports**: Sử dụng `..` hoặc absolute path sau khi setup sys.path

## ✅ Kiểm tra

Tất cả imports đã được cập nhật và không có lỗi linter.

