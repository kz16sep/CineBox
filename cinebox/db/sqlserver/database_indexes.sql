-- ============================================
-- Database Indexes for Performance Optimization
-- ============================================
-- Tạo các indexes để cải thiện hiệu suất queries
-- Chạy script này trên SQL Server database

USE CineBoxDB;
GO

-- ============================================
-- Rating Table Indexes
-- ============================================
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Rating_movieId' AND object_id = OBJECT_ID('cine.Rating'))
BEGIN
    CREATE INDEX IX_Rating_movieId ON cine.Rating(movieId);
    PRINT 'Created index: IX_Rating_movieId';
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Rating_userId' AND object_id = OBJECT_ID('cine.Rating'))
BEGIN
    CREATE INDEX IX_Rating_userId ON cine.Rating(userId);
    PRINT 'Created index: IX_Rating_userId';
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Rating_movieId_userId' AND object_id = OBJECT_ID('cine.Rating'))
BEGIN
    CREATE INDEX IX_Rating_movieId_userId ON cine.Rating(movieId, userId);
    PRINT 'Created index: IX_Rating_movieId_userId';
END
GO

-- ============================================
-- ViewHistory Table Indexes
-- ============================================
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_ViewHistory_userId' AND object_id = OBJECT_ID('cine.ViewHistory'))
BEGIN
    CREATE INDEX IX_ViewHistory_userId ON cine.ViewHistory(userId);
    PRINT 'Created index: IX_ViewHistory_userId';
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_ViewHistory_movieId' AND object_id = OBJECT_ID('cine.ViewHistory'))
BEGIN
    CREATE INDEX IX_ViewHistory_movieId ON cine.ViewHistory(movieId);
    PRINT 'Created index: IX_ViewHistory_movieId';
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_ViewHistory_userId_startedAt' AND object_id = OBJECT_ID('cine.ViewHistory'))
BEGIN
    CREATE INDEX IX_ViewHistory_userId_startedAt ON cine.ViewHistory(userId, startedAt DESC);
    PRINT 'Created index: IX_ViewHistory_userId_startedAt';
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_ViewHistory_userId_movieId' AND object_id = OBJECT_ID('cine.ViewHistory'))
BEGIN
    CREATE INDEX IX_ViewHistory_userId_movieId ON cine.ViewHistory(userId, movieId);
    PRINT 'Created index: IX_ViewHistory_userId_movieId';
END
GO

-- ============================================
-- MovieGenre Table Indexes
-- ============================================
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_MovieGenre_movieId' AND object_id = OBJECT_ID('cine.MovieGenre'))
BEGIN
    CREATE INDEX IX_MovieGenre_movieId ON cine.MovieGenre(movieId);
    PRINT 'Created index: IX_MovieGenre_movieId';
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_MovieGenre_genreId' AND object_id = OBJECT_ID('cine.MovieGenre'))
BEGIN
    CREATE INDEX IX_MovieGenre_genreId ON cine.MovieGenre(genreId);
    PRINT 'Created index: IX_MovieGenre_genreId';
END
GO

-- ============================================
-- Genre Table Indexes
-- ============================================
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Genre_name' AND object_id = OBJECT_ID('cine.Genre'))
BEGIN
    CREATE INDEX IX_Genre_name ON cine.Genre(name);
    PRINT 'Created index: IX_Genre_name';
END
GO

-- ============================================
-- Movie Table Indexes
-- ============================================
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Movie_createdAt' AND object_id = OBJECT_ID('cine.Movie'))
BEGIN
    CREATE INDEX IX_Movie_createdAt ON cine.Movie(createdAt DESC);
    PRINT 'Created index: IX_Movie_createdAt';
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Movie_releaseYear' AND object_id = OBJECT_ID('cine.Movie'))
BEGIN
    CREATE INDEX IX_Movie_releaseYear ON cine.Movie(releaseYear DESC);
    PRINT 'Created index: IX_Movie_releaseYear';
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Movie_viewCount' AND object_id = OBJECT_ID('cine.Movie'))
BEGIN
    CREATE INDEX IX_Movie_viewCount ON cine.Movie(viewCount DESC);
    PRINT 'Created index: IX_Movie_viewCount';
END
GO

-- ============================================
-- PersonalRecommendation Table Indexes
-- ============================================
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_PersonalRecommendation_userId_expiresAt' AND object_id = OBJECT_ID('cine.PersonalRecommendation'))
BEGIN
    CREATE INDEX IX_PersonalRecommendation_userId_expiresAt ON cine.PersonalRecommendation(userId, expiresAt);
    PRINT 'Created index: IX_PersonalRecommendation_userId_expiresAt';
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_PersonalRecommendation_userId_algo' AND object_id = OBJECT_ID('cine.PersonalRecommendation'))
BEGIN
    CREATE INDEX IX_PersonalRecommendation_userId_algo ON cine.PersonalRecommendation(userId, algo);
    PRINT 'Created index: IX_PersonalRecommendation_userId_algo';
END
GO

-- ============================================
-- Favorite, Watchlist, Comment Table Indexes
-- ============================================
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Favorite_userId_movieId' AND object_id = OBJECT_ID('cine.Favorite'))
BEGIN
    CREATE INDEX IX_Favorite_userId_movieId ON cine.Favorite(userId, movieId);
    PRINT 'Created index: IX_Favorite_userId_movieId';
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Watchlist_userId_movieId' AND object_id = OBJECT_ID('cine.Watchlist'))
BEGIN
    CREATE INDEX IX_Watchlist_userId_movieId ON cine.Watchlist(userId, movieId);
    PRINT 'Created index: IX_Watchlist_userId_movieId';
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Comment_movieId' AND object_id = OBJECT_ID('cine.Comment'))
BEGIN
    CREATE INDEX IX_Comment_movieId ON cine.Comment(movieId);
    PRINT 'Created index: IX_Comment_movieId';
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Comment_userId' AND object_id = OBJECT_ID('cine.Comment'))
BEGIN
    CREATE INDEX IX_Comment_userId ON cine.Comment(userId);
    PRINT 'Created index: IX_Comment_userId';
END
GO

PRINT '============================================';
PRINT 'All indexes created successfully!';
PRINT '============================================';
GO

