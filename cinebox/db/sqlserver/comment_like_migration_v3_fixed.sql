-- ============================================
-- Comment Like Feature Migration (Fixed Version)
-- ============================================
-- Sửa lỗi foreign key constraints và dọn dẹp dữ liệu không hợp lệ

USE CineBoxDB;
GO

-- ============================================
-- Bước 1: Thêm cột likeCount vào bảng Comment (đã hoàn thành)
-- ============================================
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('[cine].[Comment]') AND name = 'likeCount')
BEGIN
    ALTER TABLE [cine].[Comment] ADD [likeCount] INT NOT NULL DEFAULT (0);
    PRINT 'Added column: likeCount to [cine].[Comment]';
END
ELSE
BEGIN
    PRINT 'Column likeCount already exists in [cine].[Comment]';
END
GO

-- ============================================
-- Bước 2: Dọn dẹp dữ liệu không hợp lệ trong CommentRating
-- ============================================
PRINT 'Cleaning up invalid data in CommentRating...';

-- Xóa các record có commentId không tồn tại trong Comment
DELETE cr 
FROM [cine].[CommentRating] cr
LEFT JOIN [cine].[Comment] c ON cr.commentId = c.commentId
WHERE c.commentId IS NULL;

PRINT 'Deleted CommentRating records with invalid commentId';

-- Xóa các record có userId không tồn tại trong User  
DELETE cr
FROM [cine].[CommentRating] cr
LEFT JOIN [cine].[User] u ON cr.userId = u.userId
WHERE u.userId IS NULL;

PRINT 'Deleted CommentRating records with invalid userId';

-- ============================================
-- Bước 3: Xóa các constraints cũ nếu tồn tại
-- ============================================
IF EXISTS (SELECT * FROM sys.key_constraints WHERE name = 'PK_CommentRating')
BEGIN
    ALTER TABLE [cine].[CommentRating] DROP CONSTRAINT PK_CommentRating;
    PRINT 'Dropped existing PK_CommentRating';
END

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

-- ============================================
-- Bước 4: Thêm lại constraints
-- ============================================
-- Thêm primary key
ALTER TABLE [cine].[CommentRating] ADD CONSTRAINT PK_CommentRating PRIMARY KEY (userId, commentId);
PRINT 'Added primary key: PK_CommentRating';

-- Thêm foreign key constraints với NO ACTION để tránh cascade issues
ALTER TABLE [cine].[CommentRating] 
ADD CONSTRAINT FK_CommentRating_Comment 
FOREIGN KEY (commentId) REFERENCES [cine].[Comment](commentId) ON DELETE NO ACTION;
PRINT 'Added foreign key: FK_CommentRating_Comment';

ALTER TABLE [cine].[CommentRating] 
ADD CONSTRAINT FK_CommentRating_User 
FOREIGN KEY (userId) REFERENCES [cine].[User](userId) ON DELETE NO ACTION;
PRINT 'Added foreign key: FK_CommentRating_User';

-- ============================================
-- Bước 5: Tạo indexes cho hiệu suất
-- ============================================
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_CommentRating_commentId' AND object_id = OBJECT_ID('[cine].[CommentRating]'))
BEGIN
    CREATE INDEX IX_CommentRating_commentId ON [cine].[CommentRating](commentId);
    PRINT 'Created index: IX_CommentRating_commentId';
END

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_CommentRating_userId' AND object_id = OBJECT_ID('[cine].[CommentRating]'))
BEGIN
    CREATE INDEX IX_CommentRating_userId ON [cine].[CommentRating](userId);
    PRINT 'Created index: IX_CommentRating_userId';
END

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_CommentRating_isLike' AND object_id = OBJECT_ID('[cine].[CommentRating]'))
BEGIN
    CREATE INDEX IX_CommentRating_isLike ON [cine].[CommentRating](isLike);
    PRINT 'Created index: IX_CommentRating_isLike';
END

-- ============================================
-- Bước 6: Tạo trigger để tự động cập nhật likeCount
-- ============================================
IF OBJECT_ID('[cine].[TR_CommentRating_UpdateLikeCount]', 'TR') IS NOT NULL
BEGIN
    DROP TRIGGER [cine].[TR_CommentRating_UpdateLikeCount];
    PRINT 'Dropped existing trigger';
END

GO

CREATE TRIGGER [cine].[TR_CommentRating_UpdateLikeCount]
ON [cine].[CommentRating]
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    
    -- Cập nhật likeCount cho comments bị ảnh hưởng (chỉ đếm isLike = 1)
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
GO

PRINT 'Created trigger: [cine].[TR_CommentRating_UpdateLikeCount]';

-- ============================================
-- Bước 7: Cập nhật likeCount hiện tại cho tất cả comments
-- ============================================
UPDATE c
SET likeCount = ISNULL(like_counts.count, 0)
FROM [cine].[Comment] c
LEFT JOIN (
    SELECT commentId, COUNT(*) as count
    FROM [cine].[CommentRating]
    WHERE isLike = 1
    GROUP BY commentId
) like_counts ON c.commentId = like_counts.commentId;

PRINT 'Updated existing likeCount values for all comments';

-- ============================================
-- Bước 8: Kiểm tra kết quả
-- ============================================
PRINT '============================================';
PRINT 'Migration completed successfully!';

-- Hiển thị thống kê
SELECT 
    'Total Comments' as TableName,
    COUNT(*) as RecordCount
FROM [cine].[Comment]
UNION ALL
SELECT 
    'Total CommentRatings' as TableName,
    COUNT(*) as RecordCount  
FROM [cine].[CommentRating]
UNION ALL
SELECT 
    'Comments with Likes' as TableName,
    COUNT(*) as RecordCount
FROM [cine].[Comment] 
WHERE likeCount > 0;

PRINT '============================================';
GO
