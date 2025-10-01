from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()


class Movie(db.Model):
    __tablename__ = "Movie"
    __table_args__ = {"schema": "cine"}

    movieId = db.Column(db.BigInteger, primary_key=True)
    title = db.Column(db.String(300), nullable=False)
    releaseYear = db.Column(db.SmallInteger)
    overview = db.Column(db.Text)
    country = db.Column(db.String(80))
    posterUrl = db.Column(db.String(500))
    backdropUrl = db.Column(db.String(500))
    viewCount = db.Column(db.BigInteger, default=0)

    genres = db.relationship(
        "Genre",
        secondary=lambda: MovieGenre.__table__,
        backref="movies",
        lazy="joined",
    )


class Genre(db.Model):
    __tablename__ = "Genre"
    __table_args__ = {"schema": "cine"}

    genreId = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)


class MovieGenre(db.Model):
    __tablename__ = "MovieGenre"
    __table_args__ = {"schema": "cine"}

    movieId = db.Column(
        db.BigInteger, db.ForeignKey("cine.Movie.movieId", ondelete="CASCADE"), primary_key=True
    )
    genreId = db.Column(
        db.Integer, db.ForeignKey("cine.Genre.genreId", ondelete="CASCADE"), primary_key=True
    )


