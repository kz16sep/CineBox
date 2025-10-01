USE CineBoxDB;
GO

-- Genres
INSERT INTO cine.Genre(name) VALUES
 (N'Hành động'),(N'Viễn tưởng'),(N'Phiêu lưu'),(N'Kỳ ảo'),(N'Hài hước');

-- Movies
INSERT INTO cine.Movie(title, releaseYear, overview, posterUrl, backdropUrl)
VALUES
 (N'Hành Tinh Cát: Phần 2', 2024, N'Paul và số phận trên Arrakis...', N'/static/img/dune2.jpg', N'/static/img/dune2_backdrop.jpg'),
 (N'Doctor Strange', 2016, N'Bác sĩ Stephen Strange và phép thuật...', N'/static/img/doctorstrange.jpg', N'/static/img/doctorstrange_backdrop.jpg');

-- MovieGenre mappings
INSERT INTO cine.MovieGenre(movieId, genreId)
SELECT m.movieId, g.genreId FROM cine.Movie m CROSS APPLY (SELECT genreId FROM cine.Genre WHERE name IN (N'Hành động', N'Viễn tưởng')) g WHERE m.title=N'Hành Tinh Cát: Phần 2';
INSERT INTO cine.MovieGenre(movieId, genreId)
SELECT m.movieId, g.genreId FROM cine.Movie m CROSS APPLY (SELECT genreId FROM cine.Genre WHERE name IN (N'Phiêu lưu', N'Kỳ ảo')) g WHERE m.title=N'Doctor Strange';


