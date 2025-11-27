# Hướng dẫn cài đặt tính năng Like Comment

## 1. Chạy Migration Database

Chạy script SQL sau trên SQL Server để thêm cột `likeCount` và tạo trigger tự động cập nhật:

```sql
-- Chạy file: db/sqlserver/comment_like_migration_v2.sql
```

Hoặc chạy từng bước:

### Bước 1: Thêm cột likeCount
```sql
USE CineBoxDB;
GO

ALTER TABLE [cine].[Comment] ADD [likeCount] INT NOT NULL DEFAULT (0);
```

### Bước 2: Thêm constraints cho CommentRating
```sql
ALTER TABLE [cine].[CommentRating] ADD CONSTRAINT PK_CommentRating PRIMARY KEY (userId, commentId);

ALTER TABLE [cine].[CommentRating] 
ADD CONSTRAINT FK_CommentRating_Comment 
FOREIGN KEY (commentId) REFERENCES [cine].[Comment](commentId) ON DELETE CASCADE;

ALTER TABLE [cine].[CommentRating] 
ADD CONSTRAINT FK_CommentRating_User 
FOREIGN KEY (userId) REFERENCES [cine].[User](userId) ON DELETE CASCADE;
```

### Bước 3: Tạo indexes
```sql
CREATE INDEX IX_CommentRating_commentId ON [cine].[CommentRating](commentId);
CREATE INDEX IX_CommentRating_userId ON [cine].[CommentRating](userId);
CREATE INDEX IX_CommentRating_isLike ON [cine].[CommentRating](isLike);
```

### Bước 4: Tạo trigger tự động cập nhật likeCount
```sql
CREATE TRIGGER [cine].[TR_CommentRating_UpdateLikeCount]
ON [cine].[CommentRating]
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    
    UPDATE c
    SET likeCount = (
        SELECT COUNT(*)
        FROM [cine].[CommentRating] cr
        WHERE cr.commentId = c.commentId AND cr.isLike = 1
    )
    FROM [cine].[Comment] c
    WHERE c.commentId IN (
        SELECT commentId FROM inserted
        UNION
        SELECT commentId FROM deleted
    );
END
```

## 2. Tính năng đã thêm

### API Endpoints mới:
- `POST /toggle-comment-like/<comment_id>` - Like/Unlike comment
- `GET /check-comment-like/<comment_id>` - Kiểm tra trạng thái like

### Frontend:
- Nút like với icon trái tim (🤍/❤️)
- Hiển thị số lượng like
- Cập nhật real-time không cần refresh
- Thông báo toast khi like/unlike

### Cách hoạt động:
1. Sử dụng bảng `CommentRating` có sẵn với field `isLike`
2. `isLike = 1`: Like comment
3. `isLike = 0`: Dislike comment (không sử dụng trong UI hiện tại)
4. Trigger tự động cập nhật `likeCount` trong bảng `Comment`

## 3. Test tính năng

1. Đăng nhập vào website
2. Vào trang xem phim có comment
3. Click nút "Thích" (🤍) 
4. Nút sẽ chuyển thành ❤️ và số like tăng
5. Click lại để bỏ like

## 4. Lưu ý

- Chỉ user đã đăng nhập mới có thể like comment
- Mỗi user chỉ có thể like 1 lần cho mỗi comment
- Trigger tự động đảm bảo `likeCount` luôn chính xác
- UI cập nhật ngay lập tức không cần refresh trang
