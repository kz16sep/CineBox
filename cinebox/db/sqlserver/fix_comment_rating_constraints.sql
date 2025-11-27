-- ============================================
-- Script sửa lỗi Foreign Key Constraints cho CommentRating
-- ============================================
USE CineBoxDB;
GO

-- Kiểm tra dữ liệu không hợp lệ trước khi sửa
PRINT 'Checking for invalid data...';

-- Kiểm tra commentId không tồn tại
SELECT 'Invalid commentId count:' as CheckType, COUNT(*) as Count
FROM [cine].[CommentRating] cr
LEFT JOIN [cine].[Comment] c ON cr.commentId = c.commentId
WHERE c.commentId IS NULL;

-- Kiểm tra userId không tồn tại  
SELECT 'Invalid userId count:' as CheckType, COUNT(*) as Count
FROM [cine].[CommentRating] cr
LEFT JOIN [cine].[User] u ON cr.userId = u.userId
WHERE u.userId IS NULL;

-- Xóa dữ liệu không hợp lệ
PRINT 'Cleaning invalid data...';

DELETE cr 
FROM [cine].[CommentRating] cr
LEFT JOIN [cine].[Comment] c ON cr.commentId = c.commentId
WHERE c.commentId IS NULL;

DELETE cr
FROM [cine].[CommentRating] cr
LEFT JOIN [cine].[User] u ON cr.userId = u.userId
WHERE u.userId IS NULL;

-- Xóa constraints cũ nếu có
IF EXISTS (SELECT * FROM sys.foreign_keys WHERE name = 'FK_CommentRating_Comment')
BEGIN
    ALTER TABLE [cine].[CommentRating] DROP CONSTRAINT FK_CommentRating_Comment;
    PRINT 'Dropped FK_CommentRating_Comment';
END

IF EXISTS (SELECT * FROM sys.foreign_keys WHERE name = 'FK_CommentRating_User')
BEGIN
    ALTER TABLE [cine].[CommentRating] DROP CONSTRAINT FK_CommentRating_User;
    PRINT 'Dropped FK_CommentRating_User';
END

-- Thêm lại constraints với NO ACTION
ALTER TABLE [cine].[CommentRating] 
ADD CONSTRAINT FK_CommentRating_Comment 
FOREIGN KEY (commentId) REFERENCES [cine].[Comment](commentId) ON DELETE NO ACTION;

ALTER TABLE [cine].[CommentRating] 
ADD CONSTRAINT FK_CommentRating_User 
FOREIGN KEY (userId) REFERENCES [cine].[User](userId) ON DELETE NO ACTION;

PRINT 'Foreign key constraints added successfully!';
GO
