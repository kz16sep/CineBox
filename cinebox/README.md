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


