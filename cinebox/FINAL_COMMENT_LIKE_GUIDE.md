# Hướng dẫn hoàn chỉnh: Comment Like với CommentRating

## 🎯 Mục tiêu
- Xóa các cột `likes`, `dislikes`, `likeCount` khỏi bảng `Comment`
- Xử lý like/dislike hoàn toàn từ bảng `CommentRating`
- Đơn giản hóa database schema và tránh duplicate data

## 📋 Các bước thực hiện

### Bước 1: Chạy script xóa cột
```sql
-- Chạy file: remove_comment_like_columns.sql
```
Script này sẽ:
- Xóa trigger cũ
- Xóa 3 cột: `likes`, `dislikes`, `likeCount`
- Hiển thị cấu trúc bảng sau khi xóa

### Bước 2: Restart Flask app
```bash
cd CineBox/cinebox
python run.py
```

### Bước 3: Test tính năng
- Đăng nhập website
- Vào trang xem phim có comment
- Click nút "Thích" (🤍)
- Kiểm tra nút chuyển thành ❤️ và số like tăng

## 🔧 Cách hoạt động mới

### Database Schema:
```sql
-- Bảng Comment (đã loại bỏ cột likes/dislikes)
CREATE TABLE [cine].[Comment] (
    [commentId] bigint NOT NULL,
    [userId] bigint NOT NULL,
    [movieId] bigint NOT NULL,
    [content] nvarchar(1000) NOT NULL,
    [createdAt] datetime2 NOT NULL DEFAULT (sysutcdatetime())
);

-- Bảng CommentRating (xử lý like/dislike)
CREATE TABLE [cine].[CommentRating] (
    [userId] bigint NOT NULL,
    [commentId] bigint NOT NULL,
    [isLike] bit NOT NULL,  -- 1 = like, 0 = dislike
    [createdAt] datetime2 NOT NULL DEFAULT (sysutcdatetime())
);
```

### API Logic:
1. **Like comment**: INSERT vào `CommentRating` với `isLike = 1`
2. **Unlike comment**: DELETE khỏi `CommentRating`
3. **Đếm likes**: `COUNT(*) FROM CommentRating WHERE isLike = 1`
4. **Kiểm tra user đã like**: JOIN với `CommentRating`

### Query tối ưu:
```sql
-- Lấy comments với like count và trạng thái like của user
SELECT 
    c.commentId,
    c.content,
    c.createdAt,
    u.email as user_email,
    u.avatarUrl,
    u.userId,
    ISNULL(like_counts.like_count, 0) as likeCount,
    CASE WHEN user_likes.userId IS NOT NULL THEN 1 ELSE 0 END as is_liked_by_current_user
FROM [cine].[Comment] c
JOIN [cine].[User] u ON c.userId = u.userId
LEFT JOIN (
    SELECT commentId, COUNT(*) as like_count
    FROM [cine].[CommentRating]
    WHERE isLike = 1
    GROUP BY commentId
) like_counts ON c.commentId = like_counts.commentId
LEFT JOIN [cine].[CommentRating] user_likes ON c.commentId = user_likes.commentId 
    AND user_likes.userId = @current_user_id AND user_likes.isLike = 1
WHERE c.movieId = @movie_id
ORDER BY c.createdAt ASC
```

## ✅ Lợi ích của cách tiếp cận này

1. **Database sạch hơn**: Không duplicate data
2. **Flexible**: Có thể thêm dislike, reaction khác dễ dàng
3. **Consistent**: Dữ liệu luôn chính xác, không cần sync
4. **Scalable**: Dễ mở rộng cho nhiều loại reaction

## 🚀 Tính năng hiện có

- ✅ Like/Unlike comment
- ✅ Hiển thị số lượng like real-time
- ✅ UI đẹp với animation
- ✅ Kiểm tra đăng nhập
- ✅ API endpoints hoàn chỉnh
- ✅ Error handling

## 📊 Monitoring

Sau khi triển khai, có thể monitor bằng các query:

```sql
-- Thống kê likes
SELECT 
    COUNT(*) as total_likes,
    COUNT(DISTINCT userId) as unique_users,
    COUNT(DISTINCT commentId) as liked_comments
FROM [cine].[CommentRating] 
WHERE isLike = 1;

-- Top comments được like nhiều nhất
SELECT 
    c.content,
    COUNT(*) as like_count
FROM [cine].[Comment] c
JOIN [cine].[CommentRating] cr ON c.commentId = cr.commentId
WHERE cr.isLike = 1
GROUP BY c.commentId, c.content
ORDER BY like_count DESC;
```
