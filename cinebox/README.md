# 🎬 CineBox - Hệ thống Gợi ý Phim Thông minh

CineBox là website xem phim trực tuyến tích hợp hệ thống gợi ý phim thông minh sử dụng **Hybrid Recommendation System** (Collaborative Filtering + Content-Based Filtering).

## 📋 Mục lục

- [Giới thiệu](#-giới-thiệu)
- [Công nghệ sử dụng](#-công-nghệ-sử-dụng)
- [Giao diện và Chức năng](#-giao-diện-và-chức-năng)
- [Cài đặt](#-cài-đặt)
- [Cấu trúc thư mục](#-cấu-trúc-thư-mục)

---

## 🎯 Giới thiệu

CineBox là đồ án tốt nghiệp xây dựng hệ thống gợi ý phim sử dụng:
- **Collaborative Filtering (ALS)**: Gợi ý dựa trên hành vi người dùng tương tự
- **Content-Based Filtering (TF-IDF)**: Gợi ý dựa trên nội dung phim tương tự
- **Hybrid Scoring**: Kết hợp 2 mô hình với trọng số có thể điều chỉnh

---

## 🛠 Công nghệ sử dụng

| Thành phần | Công nghệ |
|------------|-----------|
| Backend | Python Flask |
| Database | SQL Server |
| Frontend | HTML/CSS/JavaScript, Jinja2 |
| ML Libraries | scikit-learn, implicit (ALS), NumPy, Pandas |
| ORM | SQLAlchemy |

---

## 🖥 Giao diện và Chức năng

### 1. 🏠 Trang chủ (Home)
**File:** `app/templates/home.html`

![Home Page](docs/screenshots/home.png)

**Chức năng:**
- **Hero Carousel**: Slider phim nổi bật với backdrop, mô tả, nút "Xem ngay" và "Thêm vào danh sách"
- **Gợi ý cá nhân hóa**: Danh sách phim được gợi ý dựa trên sở thích người dùng (CF + CB)
- **Phim Trending**: Phim đang được xem nhiều trong 7 ngày qua
- **Phim mới nhất**: Phim mới cập nhật
- **Tiếp tục xem**: Phim đang xem dở (cho user đã đăng nhập)
- **Lọc theo thể loại**: Menu filter nhanh theo genre

---

### 2. 🔐 Đăng nhập / Đăng ký
**Files:** `app/templates/login.html`, `app/templates/register.html`

**Chức năng:**
- **Đăng nhập**: Username + Password (mã hóa SHA2-256)
- **Đăng ký**: Tạo tài khoản mới với validation
- **Remember me**: Lưu session đăng nhập
- **Phân quyền**: Admin và User

---

### 3. 🎬 Chi tiết phim (Movie Detail)
**File:** `app/templates/detail.html`

**Chức năng:**
- **Thông tin phim**: Poster, backdrop, tiêu đề, năm, quốc gia, thời lượng, mô tả
- **Metadata**: Đạo diễn, diễn viên, thể loại
- **Nút hành động**:
  - ▶ **Xem phim**: Chuyển đến trang xem
  - 🎬 **Xem trailer**: Xem trailer từ YouTube/TMDB
  - 🤍 **Yêu thích**: Thêm/xóa khỏi danh sách yêu thích
  - 📋 **Xem sau**: Thêm/xóa khỏi watchlist
- **Đánh giá phim**: Rating 1-5 sao với hiển thị điểm trung bình
- **Bình luận**: Viết, sửa, xóa comment + like comment
- **Phim tương tự**: Gợi ý phim liên quan (Content-Based)

---

### 4. 📺 Xem phim (Watch)
**File:** `app/templates/watch.html`

**Chức năng:**
- **Video Player**: Trình phát video nhúng
- **Progress Tracking**: Tự động lưu tiến độ xem (progressSec)
- **Completed Detection**: Đánh dấu "đã xem" khi xem ≥70% hoặc finished
- **Rating sau xem**: Popup đánh giá sau khi xem xong

---

### 5. 🔍 Tìm kiếm (Search)
**File:** `app/templates/search.html`

**Chức năng:**
- **Tìm kiếm toàn văn**: Tìm theo tên phim, diễn viên, đạo diễn
- **Auto-suggest**: Gợi ý khi gõ (real-time suggestions)
- **Highlight kết quả**: Đánh dấu từ khóa trong kết quả
- **Phân trang**: Pagination cho kết quả nhiều

---

### 6. 🏷 Thể loại (Genre)
**File:** `app/templates/genre_page.html`, `app/templates/genre_results.html`

**Chức năng:**
- **Danh sách thể loại**: Hiển thị tất cả genres
- **Lọc phim theo genre**: Xem tất cả phim thuộc một thể loại
- **Sắp xếp**: Theo rating, năm, tên

---

### 7. 👤 Tài khoản (Account)
**File:** `app/templates/account.html`

**Chức năng:**
- **Thông tin cá nhân**: Email, username, số điện thoại (có thể chỉnh sửa inline)
- **Avatar**: Upload và thay đổi ảnh đại diện
- **Đổi mật khẩu**: Form đổi password
- **Danh sách yêu thích**: Xem và quản lý phim đã favorite
- **Danh sách xem sau**: Xem và quản lý watchlist
- **Tìm kiếm trong danh sách**: Search trong favorite/watchlist
- **Flashback**: Thống kê xem phim trong năm

---

### 8. 📜 Lịch sử xem (History)
**File:** `app/templates/history.html`

**Chức năng:**
- **Danh sách phim đã xem**: Hiển thị theo thời gian
- **Tiến độ xem**: Progress bar cho mỗi phim
- **Tiếp tục xem**: Nút xem tiếp từ vị trí đã dừng
- **Xóa lịch sử**: Xóa từng item hoặc toàn bộ

---

### 9. 🎯 Onboarding (Chọn sở thích)
**File:** `app/templates/onboarding.html`

**Chức năng:**
- **Chọn thể loại yêu thích**: Multi-select genres
- **Chọn diễn viên yêu thích**: Search và chọn actors
- **Chọn đạo diễn yêu thích**: Search và chọn directors
- **Cold-start recommendations**: Dùng preferences để gợi ý cho user mới

---

### 10. 🛡 Admin Dashboard
**Files:** `app/templates/admin_*.html`

**Chức năng:**

#### 10.1 Dashboard (`admin_dashboard.html`)
- **Thống kê tổng quan**: Số users, movies, ratings, views
- **Biểu đồ**: Charts thống kê hoạt động

#### 10.2 Quản lý phim (`admin_movies.html`, `admin_movie_form.html`)
- **Danh sách phim**: Table với search, filter, pagination
- **Thêm phim mới**: Form nhập thông tin + import từ TMDB API
- **Sửa phim**: Chỉnh sửa thông tin phim
- **Xóa phim**: Soft delete hoặc hard delete
- **Tính similarity**: Trigger tính toán phim tương tự

#### 10.3 Quản lý người dùng (`admin_users.html`)
- **Danh sách users**: Table với thông tin chi tiết
- **Kích hoạt/Vô hiệu hóa**: Toggle user status
- **Xóa user**: Xóa tài khoản

#### 10.4 Quản lý mô hình (`admin_model.html`)
- **Trạng thái CF Model**: Loaded/Loading/Error
- **Trigger Retrain**: Nút retrain CF model thủ công
- **Thống kê model**: Số users, items, factors

---

## 🔌 API Endpoints

### Recommendations API
| Endpoint | Method | Mô tả |
|----------|--------|-------|
| `/api/personalized_recommendations` | GET | Gợi ý cá nhân hóa (CF + CB) |
| `/api/similar_movies/<movie_id>` | GET | Phim tương tự |
| `/api/trending_movies` | GET | Phim trending |
| `/api/cold_start_recommendations` | GET | Gợi ý cho user mới |
| `/api/hybrid_status` | GET | Trạng thái hệ thống hybrid |

### Interactions API
| Endpoint | Method | Mô tả |
|----------|--------|-------|
| `/submit-rating/<movie_id>` | POST | Đánh giá phim |
| `/toggle-favorite/<movie_id>` | POST | Toggle yêu thích |
| `/toggle-watchlist/<movie_id>` | POST | Toggle xem sau |
| `/submit-comment/<movie_id>` | POST | Gửi bình luận |
| `/api/update_watch_progress` | POST | Cập nhật tiến độ xem |

### Search API
| Endpoint | Method | Mô tả |
|----------|--------|-------|
| `/api/search/suggestions` | GET | Auto-suggest khi tìm kiếm |
| `/search` | GET | Trang kết quả tìm kiếm |

---

## ⚙ Cài đặt

### Yêu cầu
- Python 3.8+
- SQL Server 2019+
- ODBC Driver 17 for SQL Server

### Bước 1: Clone repository
```bash
git clone https://github.com/your-repo/cinebox.git
cd cinebox
```

### Bước 2: Tạo virtual environment
```bash
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac
```

### Bước 3: Cài đặt dependencies
```bash
pip install -r requirements.txt
```

### Bước 4: Cấu hình database
Tạo file `.env`:
```env
SECRET_KEY=your-secret-key
SQLSERVER_SERVER=localhost,1433
SQLSERVER_DB=CineBoxDB
SQLSERVER_UID=sa
SQLSERVER_PWD=your-password
```

### Bước 5: Chạy ứng dụng
```bash
python run.py
```

Truy cập: `http://localhost:5000`

---

## 📁 Cấu trúc thư mục

```
cinebox/
├── app/                          # Flask application
│   ├── __init__.py              # App factory
│   ├── routes/                  # Route handlers
│   │   ├── auth.py              # Login, Register, Logout
│   │   ├── movies.py            # Home, Detail, Watch, Search
│   │   ├── user.py              # Account, History
│   │   ├── admin.py             # Admin Dashboard
│   │   ├── interactions.py      # Rating, Favorite, Watchlist, Comment
│   │   ├── api_recommendations.py  # Recommendation APIs
│   │   └── api_interactions.py  # Interaction APIs
│   ├── helpers/                 # Helper functions
│   │   ├── recommendation_helpers.py  # Hybrid scoring logic
│   │   ├── movie_query_helpers.py     # DB queries
│   │   └── sql_helpers.py       # SQL utilities
│   ├── templates/               # HTML templates (Jinja2)
│   └── static/                  # CSS, JS, Images
├── recommenders/                # Recommendation engines
│   ├── collaborative_recommender.py  # CF with ALS
│   └── content_based_recommender.py  # CB with TF-IDF
├── model_collaborative/         # CF model training
│   ├── train_collaborative.py   # Training script
│   └── enhanced_cf_model.pkl    # Trained model
├── model_content-based/         # CB model training
│   └── train_content_based.py   # Training script
├── config.py                    # Configuration
├── run.py                       # Entry point
└── requirements.txt             # Dependencies
```

---

## 👥 Tác giả

- **Sinh viên**: [Tên của bạn]
- **MSSV**: [Mã số sinh viên]
- **Đồ án**: Khóa luận tốt nghiệp
- **Trường**: [Tên trường]

---

## 📝 License

MIT License - Xem file [LICENSE](LICENSE) để biết thêm chi tiết.

