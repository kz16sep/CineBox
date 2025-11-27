-- ============================================
-- Comment Like Feature Migration (Using existing CommentRating table)
-- ============================================
-- Sử dụng bảng CommentRating có sẵn và thêm cột likeCount vào Comment
-- Chạy script này trên SQL Server database

USE CineBoxDB;
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
-- Thêm constraints và indexes cho bảng CommentRating (nếu chưa có)
-- ============================================

-- Thêm primary key nếu chưa có
IF NOT EXISTS (SELECT * FROM sys.key_constraints WHERE name = 'PK_CommentRating')
BEGIN
    ALTER TABLE [cine].[CommentRating] ADD CONSTRAINT PK_CommentRating PRIMARY KEY (userId, commentId);
    PRINT 'Added primary key: PK_CommentRating';
END
GO

-- Thêm foreign key constraints nếu chưa có
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE name = 'FK_CommentRating_Comment')
BEGIN
    ALTER TABLE [cine].[CommentRating] 
    ADD CONSTRAINT FK_CommentRating_Comment 
    FOREIGN KEY (commentId) REFERENCES [cine].[Comment](commentId) ON DELETE CASCADE;
    PRINT 'Added foreign key: FK_CommentRating_Comment';
END
GO

IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE name = 'FK_CommentRating_User')
BEGIN
    ALTER TABLE [cine].[CommentRating] 
    ADD CONSTRAINT FK_CommentRating_User 
    FOREIGN KEY (userId) REFERENCES [cine].[User](userId) ON DELETE CASCADE;
    PRINT 'Added foreign key: FK_CommentRating_User';
END
GO

-- ============================================
-- Tạo indexes cho hiệu suất
-- ============================================
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_CommentRating_commentId' AND object_id = OBJECT_ID('[cine].[CommentRating]'))
BEGIN
    CREATE INDEX IX_CommentRating_commentId ON [cine].[CommentRating](commentId);
    PRINT 'Created index: IX_CommentRating_commentId';
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_CommentRating_userId' AND object_id = OBJECT_ID('[cine].[CommentRating]'))
BEGIN
    CREATE INDEX IX_CommentRating_userId ON [cine].[CommentRating](userId);
    PRINT 'Created index: IX_CommentRating_userId';
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_CommentRating_isLike' AND object_id = OBJECT_ID('[cine].[CommentRating]'))
BEGIN
    CREATE INDEX IX_CommentRating_isLike ON [cine].[CommentRating](isLike);
    PRINT 'Created index: IX_CommentRating_isLike';
END
GO

-- ============================================
-- Tạo trigger để tự động cập nhật likeCount
-- ============================================
IF OBJECT_ID('[cine].[TR_CommentRating_UpdateLikeCount]', 'TR') IS NOT NULL
    DROP TRIGGER [cine].[TR_CommentRating_UpdateLikeCount];
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
-- Cập nhật likeCount hiện tại (nếu có data)
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

PRINT 'Updated existing likeCount values';

PRINT '============================================';
PRINT 'Comment Like feature migration completed!';
PRINT 'Using existing CommentRating table with isLike field';
PRINT '============================================';
GO
