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

-- Extend Movie with common metadata if missing
IF COL_LENGTH('cine.Movie','director') IS NULL
  ALTER TABLE cine.Movie ADD director NVARCHAR(200) NULL;
IF COL_LENGTH('cine.Movie','cast') IS NULL
  ALTER TABLE cine.Movie ADD cast NVARCHAR(500) NULL;
IF COL_LENGTH('cine.Movie','durationMin') IS NULL
  ALTER TABLE cine.Movie ADD durationMin INT NULL;
IF COL_LENGTH('cine.Movie','imdbRating') IS NULL
  ALTER TABLE cine.Movie ADD imdbRating DECIMAL(3,1) NULL;
IF COL_LENGTH('cine.Movie','trailerUrl') IS NULL
  ALTER TABLE cine.Movie ADD trailerUrl NVARCHAR(500) NULL;
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

-- MOVIE SIMILARITY (Content-based Filtering)
IF OBJECT_ID('cine.MovieSimilarity','U') IS NOT NULL DROP TABLE cine.MovieSimilarity;
CREATE TABLE cine.MovieSimilarity (
  movieId1 BIGINT NOT NULL CONSTRAINT FK_MovieSimilarity_Movie1 REFERENCES cine.Movie(movieId) ON DELETE CASCADE,
  movieId2 BIGINT NOT NULL CONSTRAINT FK_MovieSimilarity_Movie2 REFERENCES cine.Movie(movieId) ON DELETE NO ACTION,
  similarity FLOAT NOT NULL CONSTRAINT CK_MovieSimilarity_Value CHECK (similarity BETWEEN 0 AND 1),
  createdAt DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
  CONSTRAINT PK_MovieSimilarity PRIMARY KEY (movieId1, movieId2)
);
CREATE INDEX IX_MovieSimilarity_movie1 ON cine.MovieSimilarity(movieId1, similarity DESC);
CREATE INDEX IX_MovieSimilarity_movie2 ON cine.MovieSimilarity(movieId2, similarity DESC);
GO

-- PERSONAL RECOMMENDATION (Collaborative Filtering)
IF OBJECT_ID('cine.PersonalRecommendation','U') IS NOT NULL DROP TABLE cine.PersonalRecommendation;
CREATE TABLE cine.PersonalRecommendation (
  recId BIGINT IDENTITY(1,1) PRIMARY KEY,
  userId BIGINT NOT NULL CONSTRAINT FK_PersonalRecommendation_User REFERENCES cine.[User](userId) ON DELETE CASCADE,
  movieId BIGINT NOT NULL CONSTRAINT FK_PersonalRecommendation_Movie REFERENCES cine.Movie(movieId) ON DELETE CASCADE,
  score FLOAT NOT NULL,
  rank INT NOT NULL,
  algo NVARCHAR(20) NOT NULL DEFAULT 'collaborative',
  generatedAt DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
  expiresAt DATETIME2(0) NOT NULL DEFAULT DATEADD(day, 7, SYSUTCDATETIME())
);
CREATE UNIQUE INDEX UX_PersonalRecommendation ON cine.PersonalRecommendation(userId, movieId);
CREATE INDEX IX_PersonalRecommendation_rank ON cine.PersonalRecommendation(userId, rank);
CREATE INDEX IX_PersonalRecommendation_expires ON cine.PersonalRecommendation(expiresAt);
GO

-- FAVORITE
IF OBJECT_ID('cine.Favorite','U') IS NOT NULL DROP TABLE cine.Favorite;
CREATE TABLE cine.Favorite (
  favoriteId  BIGINT IDENTITY(1,1) PRIMARY KEY,
  userId      BIGINT NOT NULL CONSTRAINT FK_Favorite_User REFERENCES cine.[User](userId) ON DELETE CASCADE,
  movieId     BIGINT NOT NULL CONSTRAINT FK_Favorite_Movie REFERENCES cine.Movie(movieId) ON DELETE CASCADE,
  addedAt     DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
  CONSTRAINT UK_Favorite_User_Movie UNIQUE (userId, movieId)
);
GO

-- WATCHLIST
IF OBJECT_ID('cine.WatchList','U') IS NOT NULL DROP TABLE cine.WatchList;
CREATE TABLE cine.WatchList (
  watchListId BIGINT IDENTITY(1,1) PRIMARY KEY,
  userId      BIGINT NOT NULL CONSTRAINT FK_WatchList_User REFERENCES cine.[User](userId) ON DELETE CASCADE,
  movieId     BIGINT NOT NULL CONSTRAINT FK_WatchList_Movie REFERENCES cine.Movie(movieId) ON DELETE CASCADE,
  addedAt     DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
  CONSTRAINT UK_WatchList_User_Movie UNIQUE (userId, movieId)
);
GO

-- MovieLens integration removed per project requirements




