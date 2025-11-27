-- ============================================
-- Add Nested Comment Support
-- ============================================
-- Thêm cột parentCommentId để hỗ trợ reply comment

USE CineBoxDB;
GO

-- ============================================
-- Bước 1: Thêm cột parentCommentId vào bảng Comment
-- ============================================
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('[cine].[Comment]') AND name = 'parentCommentId')
BEGIN
    ALTER TABLE [cine].[Comment] ADD [parentCommentId] BIGINT NULL;
    PRINT 'Added column: parentCommentId to Comment table';
END
ELSE
BEGIN
    PRINT 'Column parentCommentId already exists in Comment table';
END
GO

-- ============================================
-- Bước 2: Thêm foreign key constraint (self-referencing)
-- ============================================
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE name = 'FK_Comment_ParentComment')
BEGIN
    ALTER TABLE [cine].[Comment] 
    ADD CONSTRAINT FK_Comment_ParentComment 
    FOREIGN KEY (parentCommentId) REFERENCES [cine].[Comment](commentId);
    PRINT 'Added foreign key: FK_Comment_ParentComment';
END
ELSE
BEGIN
    PRINT 'Foreign key FK_Comment_ParentComment already exists';
END
GO

-- ============================================
-- Bước 3: Tạo index cho parentCommentId
-- ============================================
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Comment_parentCommentId' AND object_id = OBJECT_ID('[cine].[Comment]'))
BEGIN
    CREATE INDEX IX_Comment_parentCommentId ON [cine].[Comment](parentCommentId);
    PRINT 'Created index: IX_Comment_parentCommentId';
END
ELSE
BEGIN
    PRINT 'Index IX_Comment_parentCommentId already exists';
END
GO

-- ============================================
-- Bước 4: Kiểm tra cấu trúc bảng sau khi thêm
-- ============================================
PRINT '============================================';
PRINT 'Updated Comment table structure:';
SELECT 
    COLUMN_NAME,
    DATA_TYPE,
    IS_NULLABLE,
    COLUMN_DEFAULT
FROM INFORMATION_SCHEMA.COLUMNS 
WHERE TABLE_SCHEMA = 'cine' AND TABLE_NAME = 'Comment'
ORDER BY ORDINAL_POSITION;

-- ============================================
-- Bước 5: Test data (optional)
-- ============================================
PRINT '============================================';
PRINT 'Current comment statistics:';

SELECT 
    'Total Comments' as Type,
    COUNT(*) as Count
FROM [cine].[Comment]
UNION ALL
SELECT 
    'Root Comments (no parent)',
    COUNT(*)
FROM [cine].[Comment]
WHERE parentCommentId IS NULL
UNION ALL
SELECT 
    'Reply Comments (has parent)',
    COUNT(*)
FROM [cine].[Comment]
WHERE parentCommentId IS NOT NULL;

PRINT '============================================';
PRINT 'Nested comment support added successfully!';
PRINT 'Now you can use parentCommentId for reply functionality.';
GO
