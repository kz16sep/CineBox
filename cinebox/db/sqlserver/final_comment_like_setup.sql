-- ============================================
-- Final Comment Like Setup - Using existing structure
-- ============================================
USE CineBoxDB;
GO

-- ============================================
-- Bước 1: Dọn dẹp dữ liệu không hợp lệ
-- ============================================
PRINT 'Cleaning up invalid CommentRating data...';

DELETE cr 
FROM [cine].[CommentRating] cr
LEFT JOIN [cine].[Comment] c ON cr.commentId = c.commentId
WHERE c.commentId IS NULL;

DELETE cr
FROM [cine].[CommentRating] cr
LEFT JOIN [cine].[User] u ON cr.userId = u.userId
WHERE u.userId IS NULL;

-- ============================================
-- Bước 2: Tạo constraints (bỏ qua lỗi nếu đã tồn tại)
-- ============================================
-- Xóa constraints cũ nếu có
BEGIN TRY
    ALTER TABLE [cine].[CommentRating] DROP CONSTRAINT FK_CommentRating_Comment;
END TRY
BEGIN CATCH
    PRINT 'FK_CommentRating_Comment not exists or already dropped';
END CATCH

BEGIN TRY
    ALTER TABLE [cine].[CommentRating] DROP CONSTRAINT FK_CommentRating_User;
END TRY
BEGIN CATCH
    PRINT 'FK_CommentRating_User not exists or already dropped';
END CATCH

BEGIN TRY
    ALTER TABLE [cine].[CommentRating] DROP CONSTRAINT PK_CommentRating;
END TRY
BEGIN CATCH
    PRINT 'PK_CommentRating not exists or already dropped';
END CATCH

-- Thêm constraints mới
BEGIN TRY
    ALTER TABLE [cine].[CommentRating] ADD CONSTRAINT PK_CommentRating PRIMARY KEY (userId, commentId);
    PRINT 'Added PK_CommentRating';
END TRY
BEGIN CATCH
    PRINT 'Could not add PK_CommentRating - may already exist';
END CATCH

BEGIN TRY
    ALTER TABLE [cine].[CommentRating] 
    ADD CONSTRAINT FK_CommentRating_Comment 
    FOREIGN KEY (commentId) REFERENCES [cine].[Comment](commentId) ON DELETE CASCADE;
    PRINT 'Added FK_CommentRating_Comment';
END TRY
BEGIN CATCH
    PRINT 'Could not add FK_CommentRating_Comment';
END CATCH

BEGIN TRY
    ALTER TABLE [cine].[CommentRating] 
    ADD CONSTRAINT FK_CommentRating_User 
    FOREIGN KEY (userId) REFERENCES [cine].[User](userId) ON DELETE CASCADE;
    PRINT 'Added FK_CommentRating_User';
END TRY
BEGIN CATCH
    PRINT 'Could not add FK_CommentRating_User';
END CATCH

-- ============================================
-- Bước 3: Tạo trigger để tự động cập nhật likes
-- ============================================
IF OBJECT_ID('[cine].[TR_CommentRating_UpdateLikes]', 'TR') IS NOT NULL
BEGIN
    DROP TRIGGER [cine].[TR_CommentRating_UpdateLikes];
    PRINT 'Dropped existing trigger';
END

GO

CREATE TRIGGER [cine].[TR_CommentRating_UpdateLikes]
ON [cine].[CommentRating]
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    
    -- Cập nhật cột likes trong Comment dựa trên CommentRating
    UPDATE c
    SET likes = (
        SELECT COUNT(*)
        FROM [cine].[CommentRating] cr
        WHERE cr.commentId = c.commentId AND cr.isLike = 1
    ),
    dislikes = (
        SELECT COUNT(*)
        FROM [cine].[CommentRating] cr
        WHERE cr.commentId = c.commentId AND cr.isLike = 0
    )
    FROM [cine].[Comment] c
    WHERE c.commentId IN (
        SELECT commentId FROM inserted
        UNION
        SELECT commentId FROM deleted
    );
END
GO

PRINT 'Created trigger: TR_CommentRating_UpdateLikes';

-- ============================================
-- Bước 4: Cập nhật likes/dislikes hiện tại
-- ============================================
PRINT 'Updating current likes and dislikes count...';

UPDATE c
SET likes = ISNULL(like_counts.count, 0),
    dislikes = ISNULL(dislike_counts.count, 0)
FROM [cine].[Comment] c
LEFT JOIN (
    SELECT commentId, COUNT(*) as count
    FROM [cine].[CommentRating]
    WHERE isLike = 1
    GROUP BY commentId
) like_counts ON c.commentId = like_counts.commentId
LEFT JOIN (
    SELECT commentId, COUNT(*) as count
    FROM [cine].[CommentRating]
    WHERE isLike = 0
    GROUP BY commentId
) dislike_counts ON c.commentId = dislike_counts.commentId;

-- ============================================
-- Bước 5: Tạo indexes cho hiệu suất
-- ============================================
BEGIN TRY
    CREATE INDEX IX_CommentRating_commentId ON [cine].[CommentRating](commentId);
    PRINT 'Created index: IX_CommentRating_commentId';
END TRY
BEGIN CATCH
    PRINT 'Index IX_CommentRating_commentId already exists';
END CATCH

BEGIN TRY
    CREATE INDEX IX_CommentRating_isLike ON [cine].[CommentRating](isLike);
    PRINT 'Created index: IX_CommentRating_isLike';
END TRY
BEGIN CATCH
    PRINT 'Index IX_CommentRating_isLike already exists';
END CATCH

-- ============================================
-- Kết quả
-- ============================================
PRINT '============================================';
PRINT 'Comment Like setup completed successfully!';
PRINT '============================================';

-- Hiển thị thống kê
SELECT 
    'Comments' as TableName,
    COUNT(*) as TotalRecords,
    SUM(likes) as TotalLikes,
    SUM(dislikes) as TotalDislikes
FROM [cine].[Comment]
UNION ALL
SELECT 
    'CommentRatings',
    COUNT(*),
    SUM(CASE WHEN isLike = 1 THEN 1 ELSE 0 END),
    SUM(CASE WHEN isLike = 0 THEN 1 ELSE 0 END)
FROM [cine].[CommentRating];

PRINT 'Ready to use Comment Like feature!';
GO
