-- Create database (run in master)
-- CREATE DATABASE CineBoxDB;
-- GO

USE CineBoxDB;
GO

-- Schema
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'cine')
  EXEC('CREATE SCHEMA cine AUTHORIZATION dbo');
GO

-- ROLE
IF OBJECT_ID('cine.Role','U') IS NOT NULL DROP TABLE cine.Role;
CREATE TABLE cine.Role (
  roleId       INT IDENTITY(1,1) PRIMARY KEY,
  roleName     NVARCHAR(50) NOT NULL UNIQUE,
  description  NVARCHAR(255) NULL
);
INSERT INTO cine.Role(roleName, description) VALUES (N'Admin', N'Quản trị'), (N'User', N'Người dùng');
GO

-- USER
IF OBJECT_ID('cine.[User]','U') IS NOT NULL DROP TABLE cine.[User];
CREATE TABLE cine.[User] (
  userId       BIGINT IDENTITY(1,1) PRIMARY KEY,
  email        NVARCHAR(255) NOT NULL UNIQUE,
  avatarUrl    NVARCHAR(500) NULL,
  status       NVARCHAR(20) NOT NULL DEFAULT N'active',
  phone        NVARCHAR(20) NULL,
  createdAt    DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
  lastLoginAt  DATETIME2(0) NULL,
  roleId       INT NOT NULL CONSTRAINT FK_User_Role REFERENCES cine.Role(roleId)
);
GO

-- ACCOUNT
IF OBJECT_ID('cine.Account','U') IS NOT NULL DROP TABLE cine.Account;
CREATE TABLE cine.Account (
  accountId     BIGINT IDENTITY(1,1) PRIMARY KEY,
  username      NVARCHAR(100) NOT NULL UNIQUE,
  passwordHash  VARBINARY(256) NOT NULL,
  userId        BIGINT NOT NULL UNIQUE CONSTRAINT FK_Account_User REFERENCES cine.[User](userId)
);
GO

-- GENRE
IF OBJECT_ID('cine.Genre','U') IS NOT NULL DROP TABLE cine.Genre;
CREATE TABLE cine.Genre (
  genreId  INT IDENTITY(1,1) PRIMARY KEY,
  name     NVARCHAR(80) NOT NULL UNIQUE
);
GO

-- MOVIE
IF OBJECT_ID('cine.Movie','U') IS NOT NULL DROP TABLE cine.Movie;
CREATE TABLE cine.Movie (
  movieId     BIGINT IDENTITY(1,1) PRIMARY KEY,
  title       NVARCHAR(300) NOT NULL,
  releaseYear SMALLINT NULL,
  overview    NVARCHAR(MAX) NULL,
  country     NVARCHAR(80) NULL,
  posterUrl   NVARCHAR(500) NULL,
  backdropUrl NVARCHAR(500) NULL,
  viewCount   BIGINT NOT NULL DEFAULT 0,
  createdAt   DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX IX_Movie_title ON cine.Movie(title);
CREATE INDEX IX_Movie_releaseYear ON cine.Movie(releaseYear);
GO

-- MOVIEGENRE
IF OBJECT_ID('cine.MovieGenre','U') IS NOT NULL DROP TABLE cine.MovieGenre;
CREATE TABLE cine.MovieGenre (
  movieId BIGINT NOT NULL CONSTRAINT FK_MovieGenre_Movie REFERENCES cine.Movie(movieId) ON DELETE CASCADE,
  genreId INT NOT NULL CONSTRAINT FK_MovieGenre_Genre REFERENCES cine.Genre(genreId) ON DELETE CASCADE,
  CONSTRAINT PK_MovieGenre PRIMARY KEY (movieId, genreId)
);
CREATE INDEX IX_MovieGenre_genreId ON cine.MovieGenre(genreId);
GO

-- RATING
IF OBJECT_ID('cine.Rating','U') IS NOT NULL DROP TABLE cine.Rating;
CREATE TABLE cine.Rating (
  userId   BIGINT NOT NULL CONSTRAINT FK_Rating_User REFERENCES cine.[User](userId) ON DELETE CASCADE,
  movieId  BIGINT NOT NULL CONSTRAINT FK_Rating_Movie REFERENCES cine.Movie(movieId) ON DELETE CASCADE,
  value    TINYINT NOT NULL CONSTRAINT CK_Rating_Value CHECK (value BETWEEN 1 AND 5),
  ratedAt  DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
  CONSTRAINT PK_Rating PRIMARY KEY (userId, movieId)
);
CREATE INDEX IX_Rating_movieId ON cine.Rating(movieId);
GO

-- WATCHLIST
IF OBJECT_ID('cine.Watchlist','U') IS NOT NULL DROP TABLE cine.Watchlist;
CREATE TABLE cine.Watchlist (
  userId   BIGINT NOT NULL CONSTRAINT FK_Watchlist_User REFERENCES cine.[User](userId) ON DELETE CASCADE,
  movieId  BIGINT NOT NULL CONSTRAINT FK_Watchlist_Movie REFERENCES cine.Movie(movieId) ON DELETE CASCADE,
  addedAt  DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
  CONSTRAINT PK_Watchlist PRIMARY KEY (userId, movieId)
);
GO

-- VIEW HISTORY
IF OBJECT_ID('cine.ViewHistory','U') IS NOT NULL DROP TABLE cine.ViewHistory;
CREATE TABLE cine.ViewHistory (
  historyId BIGINT IDENTITY(1,1) PRIMARY KEY,
  userId    BIGINT NOT NULL CONSTRAINT FK_ViewHistory_User REFERENCES cine.[User](userId) ON DELETE CASCADE,
  movieId   BIGINT NOT NULL CONSTRAINT FK_ViewHistory_Movie REFERENCES cine.Movie(movieId) ON DELETE CASCADE,
  startedAt DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
  finishedAt DATETIME2(0) NULL,
  progressSec INT NULL
);
CREATE INDEX IX_ViewHistory_user_started ON cine.ViewHistory(userId, startedAt DESC);
GO

-- LIKE
IF OBJECT_ID('cine.MovieLike','U') IS NOT NULL DROP TABLE cine.MovieLike;
CREATE TABLE cine.MovieLike (
  userId  BIGINT NOT NULL CONSTRAINT FK_Like_User REFERENCES cine.[User](userId) ON DELETE CASCADE,
  movieId BIGINT NOT NULL CONSTRAINT FK_Like_Movie REFERENCES cine.Movie(movieId) ON DELETE CASCADE,
  likedAt DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
  CONSTRAINT PK_MovieLike PRIMARY KEY (userId, movieId)
);
GO

-- COMMENT
IF OBJECT_ID('cine.Comment','U') IS NOT NULL DROP TABLE cine.Comment;
CREATE TABLE cine.Comment (
  commentId BIGINT IDENTITY(1,1) PRIMARY KEY,
  userId    BIGINT NOT NULL CONSTRAINT FK_Comment_User REFERENCES cine.[User](userId) ON DELETE CASCADE,
  movieId   BIGINT NOT NULL CONSTRAINT FK_Comment_Movie REFERENCES cine.Movie(movieId) ON DELETE CASCADE,
  content   NVARCHAR(1000) NOT NULL,
  createdAt DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX IX_Comment_movie ON cine.Comment(movieId, createdAt DESC);
GO

-- RECOMMENDATION
IF OBJECT_ID('cine.Recommendation','U') IS NOT NULL DROP TABLE cine.Recommendation;
CREATE TABLE cine.Recommendation (
  recId       BIGINT IDENTITY(1,1) PRIMARY KEY,
  userId      BIGINT NOT NULL CONSTRAINT FK_Recommendation_User REFERENCES cine.[User](userId) ON DELETE CASCADE,
  movieId     BIGINT NOT NULL CONSTRAINT FK_Recommendation_Movie REFERENCES cine.Movie(movieId) ON DELETE CASCADE,
  algo        NVARCHAR(20) NOT NULL,
  score       FLOAT NOT NULL,
  rank        INT NOT NULL,
  generatedAt DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
  expiresAt   DATETIME2(0) NULL
);
CREATE UNIQUE INDEX UX_Recommendation ON cine.Recommendation(userId, movieId, algo);
CREATE INDEX IX_Recommendation_rank ON cine.Recommendation(userId, algo, rank);
GO

-- MOVIELENS INTEGRATION
-- Add external identifiers to Movie for mapping with MovieLens links.csv
IF COL_LENGTH('cine.Movie', 'mlMovieId') IS NULL
  ALTER TABLE cine.Movie ADD mlMovieId INT NULL CONSTRAINT UQ_Movie_mlMovieId UNIQUE;
IF COL_LENGTH('cine.Movie', 'imdbId') IS NULL
  ALTER TABLE cine.Movie ADD imdbId INT NULL;
IF COL_LENGTH('cine.Movie', 'tmdbId') IS NULL
  ALTER TABLE cine.Movie ADD tmdbId INT NULL;
CREATE INDEX IX_Movie_mlMovieId ON cine.Movie(mlMovieId);
CREATE INDEX IX_Movie_imdb_tmdb ON cine.Movie(imdbId, tmdbId);
GO

-- MovieLens anonymous users
IF OBJECT_ID('cine.MLUser','U') IS NOT NULL DROP TABLE cine.MLUser;
CREATE TABLE cine.MLUser (
  mlUserId   INT NOT NULL PRIMARY KEY,
  createdAt  DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME()
);
GO

-- MovieLens ratings (supports 0.5 to 5.0 in 0.5 increments)
IF OBJECT_ID('cine.MLRating','U') IS NOT NULL DROP TABLE cine.MLRating;
CREATE TABLE cine.MLRating (
  mlUserId  INT NOT NULL CONSTRAINT FK_MLRating_User REFERENCES cine.MLUser(mlUserId) ON DELETE CASCADE,
  movieId   BIGINT NOT NULL CONSTRAINT FK_MLRating_Movie REFERENCES cine.Movie(movieId) ON DELETE CASCADE,
  value     DECIMAL(2,1) NOT NULL,
  ratedAt   DATETIME2(0) NOT NULL,
  CONSTRAINT CK_MLRating_Value CHECK (value IN (0.5,1.0,1.5,2.0,2.5,3.0,3.5,4.0,4.5,5.0)),
  CONSTRAINT PK_MLRating PRIMARY KEY (mlUserId, movieId)
);
CREATE INDEX IX_MLRating_movieId ON cine.MLRating(movieId);
CREATE INDEX IX_MLRating_userId ON cine.MLRating(mlUserId);
GO

-- MovieLens tags (free-text tagging per user/movie)
IF OBJECT_ID('cine.MLTagging','U') IS NOT NULL DROP TABLE cine.MLTagging;
CREATE TABLE cine.MLTagging (
  taggingId  BIGINT IDENTITY(1,1) PRIMARY KEY,
  mlUserId   INT NOT NULL CONSTRAINT FK_MLTagging_User REFERENCES cine.MLUser(mlUserId) ON DELETE CASCADE,
  movieId    BIGINT NOT NULL CONSTRAINT FK_MLTagging_Movie REFERENCES cine.Movie(movieId) ON DELETE CASCADE,
  tag        NVARCHAR(255) NOT NULL,
  taggedAt   DATETIME2(0) NOT NULL
);
CREATE INDEX IX_MLTagging_movie ON cine.MLTagging(movieId, taggedAt DESC);
CREATE INDEX IX_MLTagging_user ON cine.MLTagging(mlUserId, taggedAt DESC);
CREATE INDEX IX_MLTagging_tag ON cine.MLTagging(tag);
GO

-- Performance indexes for MovieLens scale
-- Fast lookups by movie, and by user with recent activity
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MLRating_movie_value_time' AND object_id = OBJECT_ID('cine.MLRating'))
  CREATE NONCLUSTERED INDEX IX_MLRating_movie_value_time ON cine.MLRating(movieId, ratedAt DESC) INCLUDE (value, mlUserId);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MLRating_user_time' AND object_id = OBJECT_ID('cine.MLRating'))
  CREATE NONCLUSTERED INDEX IX_MLRating_user_time ON cine.MLRating(mlUserId, ratedAt DESC) INCLUDE (value, movieId);
GO

-- Aggregation views for convenient queries
IF OBJECT_ID('cine.vw_ML_RatingSummary','V') IS NOT NULL DROP VIEW cine.vw_ML_RatingSummary;
GO
EXEC('CREATE VIEW cine.vw_ML_RatingSummary AS
SELECT m.movieId,
       COUNT_BIG(r.value) AS ratingCount,
       AVG(CAST(r.value AS FLOAT)) AS avgRating,
       MAX(r.ratedAt) AS lastRatedAt
FROM cine.Movie m
LEFT JOIN cine.MLRating r ON r.movieId = m.movieId
GROUP BY m.movieId');
GO

IF OBJECT_ID('cine.vw_ML_TopTags','V') IS NOT NULL DROP VIEW cine.vw_ML_TopTags;
GO
EXEC('CREATE VIEW cine.vw_ML_TopTags AS
SELECT t.movieId,
       t.tag,
       COUNT_BIG(*) AS uses
FROM cine.MLTagging t
GROUP BY t.movieId, t.tag');
GO


