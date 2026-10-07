"""The canonical TMDB movie genre map."""

TMDB_GENRE_IDS = {
    "Action": 28,
    "Adventure": 12,
    "Animation": 16,
    "Comedy": 35,
    "Crime": 80,
    "Documentary": 99,
    "Drama": 18,
    "Family": 10751,
    "Fantasy": 14,
    "History": 36,
    "Horror": 27,
    "Music": 10402,
    "Mystery": 9648,
    "Romance": 10749,
    "Science Fiction": 878,
    "TV Movie": 10770,
    "Thriller": 53,
    "War": 10752,
    "Western": 37,
}
GENRE_IDS = {
    **{name.lower(): genre_id for name, genre_id in TMDB_GENRE_IDS.items() if name != "TV Movie"},
    "musical": TMDB_GENRE_IDS["Music"],
    "sci-fi": TMDB_GENRE_IDS["Science Fiction"],
}
