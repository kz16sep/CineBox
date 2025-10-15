Hướng dẫn chạy giao diện CineBox (UI-only)

1) Tạo môi trường và cài gói
   - py -m venv .venv
   - .venv\Scripts\activate
   - pip install -r requirements.txt

2) Chạy ứng dụng
   - set FLASK_ENV=development (Windows)
   - python run.py

3) Mở trình duyệt tới http://127.0.0.1:5000

Các trang có sẵn: / (Trang chủ), /login, /register, /movie/1, /watch/1.

Kết nối SQL Server (CineBoxDB)
- Tạo DB và schema:
  - Mở SSMS, chạy file `db/sqlserver/schema.sql` (tùy chọn: uncomment dòng CREATE DATABASE nếu chưa có DB).
  - Sau đó chạy `db/sqlserver/seed.sql` để thêm dữ liệu mẫu.
- Kết nối từ Flask (nếu bật backend ORM): dùng chuỗi
  `mssql+pyodbc://USER:PASSWORD@SERVER/CineBoxDB?driver=ODBC+Driver+17+for+SQL+Server`


Tích hợp Collaborative Filtering (CF)

Web đã đọc gợi ý cá nhân từ bảng `cine.PersonalRecommendation` ở trang chủ. Để tích hợp model CF (.pkl):

1) Đặt model tại `cinebox/model/model.pkl` (hoặc đặt biến môi trường `CF_MODEL_PATH` tới file .pkl của bạn).

2) Cấu hình biến môi trường kết nối DB (giống app):

```
DB_USER=...
DB_PASSWORD=...
DB_HOST=...
DB_NAME=...
DB_DRIVER=ODBC Driver 17 for SQL Server
```

Biến tuỳ chỉnh CF (tuỳ chọn):

```
CF_TOP_N=50
CF_TTL_HOURS=24
```

3) Sinh gợi ý (batch):

```
python -m cinebox.model.generate_cf_recs
```

Script sẽ tính top‑N cho mọi user và ghi vào `cine.PersonalRecommendation` với `expiresAt`. Trang chủ sẽ tự hiển thị.

4) Lên lịch trên Windows (Task Scheduler):
   - Program/script: `python`
   - Arguments: `-m cinebox.model.generate_cf_recs`
   - Start in: thư mục dự án
   - Trigger: mỗi 6–24 giờ

5) API làm mới cho user hiện tại (tuỳ chọn):
   - POST `/api/recommendations/cf/refresh` (yêu cầu đăng nhập)
   - Tính lại và lưu gợi ý cho user hiện tại dùng cùng model và DB

Lưu ý: Điều chỉnh lời gọi tới model trong `cinebox/model/generate_cf_recs.py` (hàm `score_user_candidates`) cho khớp API model của bạn (ví dụ `model.predict(user_id, movie_id)` hoặc `model.recommend_for_user(...)`).

