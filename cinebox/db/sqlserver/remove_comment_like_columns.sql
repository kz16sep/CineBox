-- ============================================
-- Remove like/dislike columns from Comment table
-- ============================================
-- Xóa các cột likes, dislikes, likeCount khỏi bảng Comment
-- Sử dụng CommentRating để xử lý like/dislike

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
-- Bước 2: Xóa các cột likes, dislikes, likeCount
-- ============================================

-- Xóa cột likes nếu tồn tại
IF EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('[cine].[Comment]') AND name = 'likes')
BEGIN
    ALTER TABLE [cine].[Comment] DROP COLUMN [likes];
    PRINT 'Dropped column: likes from Comment table';
END
ELSE
BEGIN
    PRINT 'Column likes does not exist in Comment table';
END

-- Xóa cột dislikes nếu tồn tại
IF EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('[cine].[Comment]') AND name = 'dislikes')
BEGIN
    ALTER TABLE [cine].[Comment] DROP COLUMN [dislikes];
    PRINT 'Dropped column: dislikes from Comment table';
END
ELSE
BEGIN
    PRINT 'Column dislikes does not exist in Comment table';
END

-- Xóa cột likeCount nếu tồn tại
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
-- Bước 3: Kiểm tra cấu trúc bảng Comment sau khi xóa
-- ============================================
PRINT 'Current Comment table structure:';
SELECT 
    COLUMN_NAME,
    DATA_TYPE,
    IS_NULLABLE,
    COLUMN_DEFAULT
FROM INFORMATION_SCHEMA.COLUMNS 
WHERE TABLE_SCHEMA = 'cine' AND TABLE_NAME = 'Comment'
ORDER BY ORDINAL_POSITION;

-- ============================================
-- Bước 4: Kiểm tra cấu trúc bảng CommentRating
-- ============================================
PRINT 'Current CommentRating table structure:';
SELECT 
    COLUMN_NAME,
    DATA_TYPE,
    IS_NULLABLE,
    COLUMN_DEFAULT
FROM INFORMATION_SCHEMA.COLUMNS 
WHERE TABLE_SCHEMA = 'cine' AND TABLE_NAME = 'CommentRating'
ORDER BY ORDINAL_POSITION;

-- ============================================
-- Bước 5: Hiển thị thống kê
-- ============================================
PRINT '============================================';
PRINT 'Statistics after cleanup:';

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
PRINT 'Cleanup completed! Now use CommentRating table for like/dislike functionality.';
GO
