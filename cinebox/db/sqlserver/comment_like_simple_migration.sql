-- ============================================
-- Comment Like Simple Migration
-- ============================================
-- Sử dụng cấu trúc có sẵn: Comment.likes và CommentRating.isLike

USE CineBoxDB;
GO

-- ============================================
-- Bước 1: Kiểm tra cấu trúc hiện tại
-- ============================================
PRINT 'Checking current structure...';

-- Kiểm tra cột likes trong Comment
IF EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('[cine].[Comment]') AND name = 'likes')
    PRINT 'Column [likes] exists in [cine].[Comment]';
ELSE
    PRINT 'ERROR: Column [likes] NOT found in [cine].[Comment]';

-- Kiểm tra cột likeCount trong Comment (nếu đã thêm)
IF EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('[cine].[Comment]') AND name = 'likeCount')
    PRINT 'Column [likeCount] exists in [cine].[Comment]';
ELSE
    PRINT 'Column [likeCount] NOT found in [cine].[Comment] - will use [likes] column';

-- ============================================
-- Bước 2: Dọn dẹp dữ liệu không hợp lệ trong CommentRating
-- ============================================
PRINT 'Cleaning up invalid data in CommentRating...';

-- Xóa các record có commentId không tồn tại
DELETE cr 
FROM [cine].[CommentRating] cr
LEFT JOIN [cine].[Comment] c ON cr.commentId = c.commentId
WHERE c.commentId IS NULL;

-- Xóa các record có userId không tồn tại  
DELETE cr
FROM [cine].[CommentRating] cr
LEFT JOIN [cine].[User] u ON cr.userId = u.userId
WHERE u.userId IS NULL;

PRINT 'Cleaned up invalid CommentRating records';

-- ============================================
-- Bước 3: Tạo constraints cho CommentRating (nếu chưa có)
-- ============================================

-- Xóa constraints cũ nếu có lỗi
IF EXISTS (SELECT * FROM sys.foreign_keys WHERE name = 'FK_CommentRating_Comment')
BEGIN
    ALTER TABLE [cine].[CommentRating] DROP CONSTRAINT FK_CommentRating_Comment;
    PRINT 'Dropped existing FK_CommentRating_Comment';
END

IF EXISTS (SELECT * FROM sys.foreign_keys WHERE name = 'FK_CommentRating_User')
BEGIN
    ALTER TABLE [cine].[CommentRating] DROP CONSTRAINT FK_CommentRating_User;
    PRINT 'Dropped existing FK_CommentRating_User';
END

-- Thêm primary key nếu chưa có
IF NOT EXISTS (SELECT * FROM sys.key_constraints WHERE name = 'PK_CommentRating')
BEGIN
    ALTER TABLE [cine].[CommentRating] ADD CONSTRAINT PK_CommentRating PRIMARY KEY (userId, commentId);
    PRINT 'Added primary key: PK_CommentRating';
END

-- Thêm foreign keys
ALTER TABLE [cine].[CommentRating] 
ADD CONSTRAINT FK_CommentRating_Comment 
FOREIGN KEY (commentId) REFERENCES [cine].[Comment](commentId) ON DELETE CASCADE;

ALTER TABLE [cine].[CommentRating] 
ADD CONSTRAINT FK_CommentRating_User 
FOREIGN KEY (userId) REFERENCES [cine].[User](userId) ON DELETE CASCADE;

PRINT 'Added foreign key constraints';

-- ============================================
-- Bước 4: Tạo indexes
-- ============================================
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_CommentRating_commentId' AND object_id = OBJECT_ID('[cine].[CommentRating]'))
BEGIN
    CREATE INDEX IX_CommentRating_commentId ON [cine].[CommentRating](commentId);
    PRINT 'Created index: IX_CommentRating_commentId';
END

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_CommentRating_isLike' AND object_id = OBJECT_ID('[cine].[CommentRating]'))
BEGIN
    CREATE INDEX IX_CommentRating_isLike ON [cine].[CommentRating](isLike);
    PRINT 'Created index: IX_CommentRating_isLike';
END

-- ============================================
-- Bước 5: Cập nhật cột likes dựa trên CommentRating
-- ============================================
PRINT 'Updating likes count based on CommentRating data...';

-- Cập nhật cột likes trong Comment dựa trên CommentRating
UPDATE c
SET likes = ISNULL(like_counts.count, 0)
FROM [cine].[Comment] c
LEFT JOIN (
    SELECT commentId, COUNT(*) as count
    FROM [cine].[CommentRating]
    WHERE isLike = 1
    GROUP BY commentId
) like_counts ON c.commentId = like_counts.commentId;

-- Nếu có cột likeCount, cập nhật nó cũng
IF EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('[cine].[Comment]') AND name = 'likeCount')
BEGIN
    UPDATE c
    SET likeCount = ISNULL(like_counts.count, 0)
    FROM [cine].[Comment] c
    LEFT JOIN (
        SELECT commentId, COUNT(*) as count
        FROM [cine].[CommentRating]
        WHERE isLike = 1
        GROUP BY commentId
    ) like_counts ON c.commentId = like_counts.commentId;
    
    PRINT 'Updated likeCount column';
END

PRINT 'Updated likes count for all comments';

-- ============================================
-- Bước 6: Hiển thị kết quả
-- ============================================
PRINT '============================================';
PRINT 'Migration completed successfully!';

SELECT 
    'Total Comments' as Info,
    COUNT(*) as Count
FROM [cine].[Comment]
UNION ALL
SELECT 
    'Total CommentRatings',
    COUNT(*)
FROM [cine].[CommentRating]
UNION ALL
SELECT 
    'Comments with Likes',
    COUNT(*)
FROM [cine].[Comment] 
WHERE likes > 0;

PRINT '============================================';
GO
