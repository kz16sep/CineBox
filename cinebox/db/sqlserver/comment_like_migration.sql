-- ============================================
-- Comment Like Feature Migration
-- ============================================
-- Tạo bảng CommentLike và cập nhật bảng Comment
-- Chạy script này trên SQL Server database

USE CineBoxDB;
GO

-- ============================================
-- Tạo bảng CommentLike
-- ============================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'CommentLike' AND schema_id = SCHEMA_ID('cine'))
BEGIN
    CREATE TABLE [cine].[CommentLike] (
        [likeId] BIGINT IDENTITY(1,1) PRIMARY KEY,
        [commentId] BIGINT NOT NULL,
        [userId] BIGINT NOT NULL,
        [createdAt] DATETIME2 NOT NULL DEFAULT (GETUTCDATE()),
        
        -- Constraints
        CONSTRAINT FK_CommentLike_Comment FOREIGN KEY (commentId) REFERENCES [cine].[Comment](commentId) ON DELETE CASCADE,
        CONSTRAINT FK_CommentLike_User FOREIGN KEY (userId) REFERENCES [cine].[User](userId) ON DELETE CASCADE,
        CONSTRAINT UQ_CommentLike_User_Comment UNIQUE (commentId, userId)
    );
    
    PRINT 'Created table: [cine].[CommentLike]';
END
ELSE
BEGIN
    PRINT 'Table [cine].[CommentLike] already exists';
END
GO

-- ============================================
-- Thêm cột likeCount vào bảng Comment
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
-- Tạo indexes cho hiệu suất
-- ============================================
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_CommentLike_commentId' AND object_id = OBJECT_ID('[cine].[CommentLike]'))
BEGIN
    CREATE INDEX IX_CommentLike_commentId ON [cine].[CommentLike](commentId);
    PRINT 'Created index: IX_CommentLike_commentId';
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_CommentLike_userId' AND object_id = OBJECT_ID('[cine].[CommentLike]'))
BEGIN
    CREATE INDEX IX_CommentLike_userId ON [cine].[CommentLike](userId);
    PRINT 'Created index: IX_CommentLike_userId';
END
GO

-- ============================================
-- Tạo trigger để tự động cập nhật likeCount
-- ============================================
IF OBJECT_ID('[cine].[TR_CommentLike_UpdateCount]', 'TR') IS NOT NULL
    DROP TRIGGER [cine].[TR_CommentLike_UpdateCount];
GO

CREATE TRIGGER [cine].[TR_CommentLike_UpdateCount]
ON [cine].[CommentLike]
AFTER INSERT, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    
    -- Cập nhật likeCount cho comments bị ảnh hưởng
    UPDATE c
    SET likeCount = (
        SELECT COUNT(*)
        FROM [cine].[CommentLike] cl
        WHERE cl.commentId = c.commentId
    )
    FROM [cine].[Comment] c
    WHERE c.commentId IN (
        SELECT commentId FROM inserted
        UNION
        SELECT commentId FROM deleted
    );
END
GO

PRINT 'Created trigger: [cine].[TR_CommentLike_UpdateCount]';

-- ============================================
-- Cập nhật likeCount hiện tại (nếu có data)
-- ============================================
UPDATE c
SET likeCount = ISNULL(like_counts.count, 0)
FROM [cine].[Comment] c
LEFT JOIN (
    SELECT commentId, COUNT(*) as count
    FROM [cine].[CommentLike]
    GROUP BY commentId
) like_counts ON c.commentId = like_counts.commentId;

PRINT 'Updated existing likeCount values';

PRINT '============================================';
PRINT 'Comment Like feature migration completed!';
PRINT '============================================';
GO
