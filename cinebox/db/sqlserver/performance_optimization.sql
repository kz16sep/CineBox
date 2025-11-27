-- Performance Optimization for CineBox Database
-- Tối ưu hóa performance cho các query favorite và watchlist

-- 1. Tạo index cho bảng Favorite
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Favorite_UserId_MovieId' AND object_id = OBJECT_ID('[cine].[Favorite]'))
BEGIN
    CREATE NONCLUSTERED INDEX IX_Favorite_UserId_MovieId 
    ON [cine].[Favorite] (userId, movieId)
    INCLUDE (favoriteId, addedAt)
END

-- 2. Tạo index cho bảng Watchlist  
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Watchlist_UserId_MovieId' AND object_id = OBJECT_ID('[cine].[Watchlist]'))
BEGIN
    CREATE NONCLUSTERED INDEX IX_Watchlist_UserId_MovieId 
    ON [cine].[Watchlist] (userId, movieId)
    INCLUDE (watchlistId, addedAt, priority, isWatched)
END

-- 3. Tạo index cho bảng Comment (cho comment liking feature)
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Comment_MovieId_ParentId' AND object_id = OBJECT_ID('[cine].[Comment]'))
BEGIN
    CREATE NONCLUSTERED INDEX IX_Comment_MovieId_ParentId 
    ON [cine].[Comment] (movieId, parentCommentId)
    INCLUDE (commentId, userId, content, createdAt)
END

-- 4. Tạo index cho bảng CommentRating
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_CommentRating_CommentId_UserId' AND object_id = OBJECT_ID('[cine].[CommentRating]'))
BEGIN
    CREATE NONCLUSTERED INDEX IX_CommentRating_CommentId_UserId 
    ON [cine].[CommentRating] (commentId, userId)
    INCLUDE (ratingType, createdAt)
END

-- 5. Tạo index cho bảng Movie (tìm kiếm và sắp xếp)
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Movie_ReleaseYear_Title' AND object_id = OBJECT_ID('[cine].[Movie]'))
BEGIN
    CREATE NONCLUSTERED INDEX IX_Movie_ReleaseYear_Title 
    ON [cine].[Movie] (releaseYear, title)
    INCLUDE (movieId, posterUrl, averageRating, totalRatings)
END

-- 6. Tạo index cho bảng MovieGenre (lọc theo thể loại)
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_MovieGenre_GenreId_MovieId' AND object_id = OBJECT_ID('[cine].[MovieGenre]'))
BEGIN
    CREATE NONCLUSTERED INDEX IX_MovieGenre_GenreId_MovieId 
    ON [cine].[MovieGenre] (genreId, movieId)
END

PRINT 'Performance optimization indexes created successfully!'
