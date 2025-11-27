-- ============================================
-- Remove like/dislike columns from Comment table (FIXED VERSION)
-- ============================================
-- Xóa constraints trước, sau đó xóa columns

USE CineBoxDB;
GO

-- ============================================
-- Bước 1: Xóa trigger cũ (nếu có)
-- ============================================
IF OBJECT_ID('[cine].[TR_CommentRating_UpdateLikes]', 'TR') IS NOT NULL
BEGIN
    DROP TRIGGER [cine].[TR_CommentRating_UpdateLikes];
    PRINT 'Dropped trigger: TR_CommentRating_UpdateLikes';
END
GO

-- ============================================
-- Bước 2: Xóa default constraints trước khi xóa columns
-- ============================================

-- Tìm và xóa default constraint cho cột likes
DECLARE @ConstraintName NVARCHAR(200)
SELECT @ConstraintName = dc.name
FROM sys.default_constraints dc
JOIN sys.columns c ON dc.parent_column_id = c.column_id
JOIN sys.objects o ON dc.parent_object_id = o.object_id
WHERE o.name = 'Comment' AND c.name = 'likes' AND o.schema_id = SCHEMA_ID('cine')

IF @ConstraintName IS NOT NULL
BEGIN
    EXEC('ALTER TABLE [cine].[Comment] DROP CONSTRAINT [' + @ConstraintName + ']')
    PRINT 'Dropped default constraint for likes: ' + @ConstraintName
END

-- Tìm và xóa default constraint cho cột dislikes
SELECT @ConstraintName = dc.name
FROM sys.default_constraints dc
JOIN sys.columns c ON dc.parent_column_id = c.column_id
JOIN sys.objects o ON dc.parent_object_id = o.object_id
WHERE o.name = 'Comment' AND c.name = 'dislikes' AND o.schema_id = SCHEMA_ID('cine')

IF @ConstraintName IS NOT NULL
BEGIN
    EXEC('ALTER TABLE [cine].[Comment] DROP CONSTRAINT [' + @ConstraintName + ']')
    PRINT 'Dropped default constraint for dislikes: ' + @ConstraintName
END

-- Tìm và xóa default constraint cho cột likeCount (nếu có)
SELECT @ConstraintName = dc.name
FROM sys.default_constraints dc
JOIN sys.columns c ON dc.parent_column_id = c.column_id
JOIN sys.objects o ON dc.parent_object_id = o.object_id
WHERE o.name = 'Comment' AND c.name = 'likeCount' AND o.schema_id = SCHEMA_ID('cine')

IF @ConstraintName IS NOT NULL
BEGIN
    EXEC('ALTER TABLE [cine].[Comment] DROP CONSTRAINT [' + @ConstraintName + ']')
    PRINT 'Dropped default constraint for likeCount: ' + @ConstraintName
END

-- ============================================
-- Bước 3: Xóa các cột likes, dislikes, likeCount
-- ============================================

-- Xóa cột likes
IF EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('[cine].[Comment]') AND name = 'likes')
BEGIN
    ALTER TABLE [cine].[Comment] DROP COLUMN [likes];
    PRINT 'Dropped column: likes from Comment table';
END
ELSE
BEGIN
    PRINT 'Column likes does not exist in Comment table';
END

-- Xóa cột dislikes
IF EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('[cine].[Comment]') AND name = 'dislikes')
BEGIN
    ALTER TABLE [cine].[Comment] DROP COLUMN [dislikes];
    PRINT 'Dropped column: dislikes from Comment table';
END
ELSE
BEGIN
    PRINT 'Column dislikes does not exist in Comment table';
END

-- Xóa cột likeCount
IF EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('[cine].[Comment]') AND name = 'likeCount')
BEGIN
    ALTER TABLE [cine].[Comment] DROP COLUMN [likeCount];
    PRINT 'Dropped column: likeCount from Comment table';
END
ELSE
BEGIN
    PRINT 'Column likeCount does not exist in Comment table';
END

-- ============================================
-- Bước 4: Kiểm tra cấu trúc bảng Comment sau khi xóa
-- ============================================
PRINT '============================================';
PRINT 'Current Comment table structure after cleanup:';
SELECT 
    COLUMN_NAME,
    DATA_TYPE,
    IS_NULLABLE,
    COLUMN_DEFAULT
FROM INFORMATION_SCHEMA.COLUMNS 
WHERE TABLE_SCHEMA = 'cine' AND TABLE_NAME = 'Comment'
ORDER BY ORDINAL_POSITION;

-- ============================================
-- Bước 5: Hiển thị thống kê CommentRating
-- ============================================
PRINT '============================================';
PRINT 'CommentRating statistics:';

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
    'Likes in CommentRating',
    COUNT(*)
FROM [cine].[CommentRating]
WHERE isLike = 1
UNION ALL
SELECT 
    'Dislikes in CommentRating',
    COUNT(*)
FROM [cine].[CommentRating]
WHERE isLike = 0;

PRINT '============================================';
PRINT 'Cleanup completed successfully!';
PRINT 'Now use CommentRating table for like/dislike functionality.';
GO
