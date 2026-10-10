import streamlit as st
import pandas as pd
import sqlite3
import os
import re
from collections import Counter
from datetime import datetime

# ============================================================
# MOVIEVERSE - UPGRADED PYTHON-ONLY VERSION
# Python + Streamlit + Pandas + SQLite
# ============================================================

st.set_page_config(
    page_title="MovieVerse",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "movieverse.db")
CSV_NAME = os.path.join(BASE_DIR, "movies.csv")

# TMDB image server is only used to display the poster paths
IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
BACKDROP_BASE = "https://image.tmdb.org/t/p/w1280"


# ============================================================
# DATABASE
# ============================================================

def get_connection():
    return sqlite3.connect(DB_NAME)


def create_database():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            movie_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            movie_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ratings (
            movie_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 10)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            movie_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 10),
            review TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


create_database()


# ============================================================
# LOAD MOVIES
# ============================================================

@st.cache_data
def load_movies():
    """Load movie data robustly from common TMDB CSV formats."""
    if not os.path.isfile(CSV_NAME):
        st.error(f"CSV file not found: {CSV_NAME}")
        return pd.DataFrame()

    try:
        data = pd.read_csv(CSV_NAME, encoding="utf-8-sig", low_memory=False)
    except Exception as e:
        st.error(f"Could not read movies.csv: {e}")
        return pd.DataFrame()

    # Normalize headers so small naming differences do not break the app.
    data.columns = [str(c).strip().lower().replace(" ", "_") for c in data.columns]
    aliases = {
        "movie_id": "id",
        "tmdb_id": "id",
        "name": "title",
        "original_title": "title",
        "movie_title": "title",
        "description": "overview",
        "plot": "overview",
        "vote_average": "rating",
        "average_rating": "rating",
        "vote_count": "vote_count",
        "original_language": "language",
        "lang": "language",
        "poster": "poster_path",
        "poster_url": "poster_path",
        "backdrop": "backdrop_path",
        "backdrop_url": "backdrop_path",
        "year": "release_year",
        "release_years": "release_year",
    }
    for old, new_name in aliases.items():
        if old in data.columns and new_name not in data.columns:
            data.rename(columns={old: new_name}, inplace=True)

    # Derive release_year from the date when the CSV stores release_date only.
    if "release_year" not in data.columns and "release_date" in data.columns:
        data["release_year"] = data["release_date"].astype(str).str.slice(0, 4)

    # Provide optional columns used by the interface if absent.
    for column in ["id", "title", "overview", "rating", "popularity",
                   "release_date", "release_year", "language",
                   "poster_path", "backdrop_path"]:
        if column not in data.columns:
            data[column] = None

    data["id"] = pd.to_numeric(data["id"], errors="coerce")
    data["rating"] = pd.to_numeric(data["rating"], errors="coerce")
    data["popularity"] = pd.to_numeric(data["popularity"], errors="coerce")
    data["release_year"] = pd.to_numeric(data["release_year"], errors="coerce")
    data["title"] = data["title"].fillna("Unknown Title").astype(str).str.strip()
    data["overview"] = data["overview"].fillna("").astype(str)
    data["language"] = data["language"].fillna("").astype(str)
    data["poster_path"] = data["poster_path"].fillna("").astype(str).str.strip()
    data["backdrop_path"] = data["backdrop_path"].fillna("").astype(str).str.strip()

    # Remove rows without a usable title and duplicate IDs.
    data = data[data["title"].ne("")]
    data = data[data["title"].str.lower().ne("nan")]
    if data["id"].notna().any():
        data = data.drop_duplicates(subset=["id"], keep="first")

    return data.reset_index(drop=True)


df = load_movies()

if df.empty:
    st.error(
        "movies.csv was not found or could not be loaded. "
        "Put movies.csv in the same folder as app.py."
    )
    st.stop()


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def safe_value(movie, column, default="N/A"):
    if column not in movie.index:
        return default

    value = movie[column]

    if pd.isna(value) or str(value).strip() == "":
        return default

    return value


def movie_key(movie_id):
    return str(int(movie_id))


def get_movie(movie_id):
    matches = df[df["id"] == movie_id]

    if matches.empty:
        return None

    return matches.iloc[0]


def poster_url(movie):
    path = safe_value(movie, "poster_path", "")

    if path == "N/A" or not path:
        return None

    return IMAGE_BASE + str(path)


def backdrop_url(movie):
    path = safe_value(movie, "backdrop_path", "")

    if path == "N/A" or not path:
        return None

    return BACKDROP_BASE + str(path)


def get_favorites():
    conn = get_connection()
    result = pd.read_sql_query(
        "SELECT movie_id, title FROM favorites ORDER BY title",
        conn
    )
    conn.close()
    return result


def get_watchlist():
    conn = get_connection()
    result = pd.read_sql_query(
        "SELECT movie_id, title FROM watchlist ORDER BY title",
        conn
    )
    conn.close()
    return result


def get_ratings():
    conn = get_connection()
    result = pd.read_sql_query(
        "SELECT movie_id, title, rating FROM ratings ORDER BY title",
        conn
    )
    conn.close()
    return result


def get_reviews(movie_id=None):
    conn = get_connection()

    if movie_id is None:
        result = pd.read_sql_query(
            """
            SELECT id, movie_id, title, rating, review, created_at
            FROM reviews
            ORDER BY id DESC
            """,
            conn
        )
    else:
        result = pd.read_sql_query(
            """
            SELECT id, movie_id, title, rating, review, created_at
            FROM reviews
            WHERE movie_id = ?
            ORDER BY id DESC
            """,
            conn,
            params=(int(movie_id),)
        )

    conn.close()
    return result


def is_favorite(movie_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM favorites WHERE movie_id = ?",
        (int(movie_id),)
    ).fetchone()
    conn.close()
    return row is not None


def is_watchlisted(movie_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM watchlist WHERE movie_id = ?",
        (int(movie_id),)
    ).fetchone()
    conn.close()
    return row is not None


def get_user_rating(movie_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT rating FROM ratings WHERE movie_id = ?",
        (int(movie_id),)
    ).fetchone()
    conn.close()

    return int(row[0]) if row else None


def add_favorite(movie):
    movie_id = int(movie["id"])
    title = str(movie["title"])

    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO favorites(movie_id, title) VALUES (?, ?)",
        (movie_id, title)
    )
    conn.commit()
    conn.close()


def remove_favorite(movie_id):
    conn = get_connection()
    conn.execute(
        "DELETE FROM favorites WHERE movie_id = ?",
        (int(movie_id),)
    )
    conn.commit()
    conn.close()


def add_watchlist(movie):
    movie_id = int(movie["id"])
    title = str(movie["title"])

    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO watchlist(movie_id, title) VALUES (?, ?)",
        (movie_id, title)
    )
    conn.commit()
    conn.close()


def remove_watchlist(movie_id):
    conn = get_connection()
    conn.execute(
        "DELETE FROM watchlist WHERE movie_id = ?",
        (int(movie_id),)
    )
    conn.commit()
    conn.close()


def save_rating(movie, rating):
    movie_id = int(movie["id"])
    title = str(movie["title"])

    conn = get_connection()
    conn.execute(
        """
        INSERT INTO ratings(movie_id, title, rating)
        VALUES (?, ?, ?)
        ON CONFLICT(movie_id)
        DO UPDATE SET title = excluded.title, rating = excluded.rating
        """,
        (movie_id, title, int(rating))
    )
    conn.commit()
    conn.close()


def add_review(movie, rating, review_text):
    movie_id = int(movie["id"])
    title = str(movie["title"])

    conn = get_connection()
    conn.execute(
        """
        INSERT INTO reviews(movie_id, title, rating, review, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            movie_id,
            title,
            int(rating),
            review_text.strip(),
            datetime.now().strftime("%Y-%m-%d %H:%M")
        )
    )
    conn.commit()
    conn.close()


def delete_review(review_id):
    conn = get_connection()
    conn.execute(
        "DELETE FROM reviews WHERE id = ?",
        (int(review_id),)
    )
    conn.commit()
    conn.close()


# ============================================================
# RECOMMENDATION SYSTEM
# ============================================================

STOPWORDS = {
    "the", "and", "a", "an", "of", "to", "in", "on", "for", "with",
    "from", "is", "are", "as", "by", "at", "his", "her", "their",
    "this", "that", "after", "before", "into", "who", "when", "where",
    "it", "its", "be", "has", "have", "they", "he", "she", "but",
    "while", "about", "through", "one", "two", "new", "up", "out"
}


def text_words(text):
    words = re.findall(r"[a-zA-Z]{3,}", str(text).lower())
    return {
        word for word in words
        if word not in STOPWORDS
    }


def similarity_score(source_text, target_text):
    a = text_words(source_text)
    b = text_words(target_text)

    if not a or not b:
        return 0

    intersection = len(a.intersection(b))
    union = len(a.union(b))

    if union == 0:
        return 0

    return intersection / union


def get_recommendations(movie, limit=6):
    movie_id = int(movie["id"])
    source_title = str(movie["title"])
    source_overview = str(safe_value(movie, "overview", ""))

    candidates = df[df["id"] != movie_id].copy()

    if candidates.empty:
        return candidates

    scores = []

    for _, candidate in candidates.iterrows():
        target_text = (
            str(candidate.get("title", "")) + " " +
            str(candidate.get("overview", ""))
        )

        score = similarity_score(
            source_title + " " + source_overview,
            target_text
        )

        # Small popularity/rating boost
        rating = float(candidate.get("rating", 0) or 0)
        popularity = float(candidate.get("popularity", 0) or 0)

        score += min(rating / 100, 0.10)
        score += min(popularity / 10000, 0.05)

        scores.append(score)

    candidates["recommendation_score"] = scores

    return (
        candidates
        .sort_values(
            ["recommendation_score", "rating", "popularity"],
            ascending=False
        )
        .head(limit)
    )


def get_personal_recommendations(limit=8):
    ratings = get_ratings()

    if ratings.empty:
        return (
            df
            .sort_values(["rating", "popularity"], ascending=False)
            .head(limit)
        )

    liked_ids = ratings[ratings["rating"] >= 7]["movie_id"].tolist()

    if not liked_ids:
        return (
            df
            .sort_values(["rating", "popularity"], ascending=False)
            .head(limit)
        )

    liked_movies = df[df["id"].isin(liked_ids)]

    if liked_movies.empty:
        return (
            df
            .sort_values(["rating", "popularity"], ascending=False)
            .head(limit)
        )

    liked_text = " ".join(
        liked_movies["title"].astype(str).tolist()
        + liked_movies["overview"].astype(str).tolist()
    )

    watched_ids = set(ratings["movie_id"].tolist())

    candidates = df[~df["id"].isin(watched_ids)].copy()

    if candidates.empty:
        return candidates

    scores = []

    for _, candidate in candidates.iterrows():
        candidate_text = (
            str(candidate.get("title", "")) + " " +
            str(candidate.get("overview", ""))
        )

        score = similarity_score(liked_text, candidate_text)

        rating = float(candidate.get("rating", 0) or 0)
        score += min(rating / 100, 0.10)

        scores.append(score)

    candidates["recommendation_score"] = scores

    return (
        candidates
        .sort_values(
            ["recommendation_score", "rating", "popularity"],
            ascending=False
        )
        .head(limit)
    )


# ============================================================
# SESSION STATE
# ============================================================

if "selected_movie_id" not in st.session_state:
    st.session_state.selected_movie_id = None

if "page" not in st.session_state:
    st.session_state.page = "🏠 Home"


def open_details(movie_id):
    st.session_state.selected_movie_id = int(movie_id)
    st.session_state.page = "🎬 Movie Details"


# ============================================================
# CUSTOM UI
# ============================================================

st.markdown(
    """
    <style>
    .movie-title {
        font-size: 20px;
        font-weight: 700;
        margin-top: 6px;
    }

    .movie-info {
        color: #777;
        font-size: 14px;
    }

    .hero {
        padding: 24px;
        border-radius: 18px;
        background: linear-gradient(135deg, #111827, #1f2937);
        color: white;
        margin-bottom: 24px;
    }

    .small-note {
        color: #777;
        font-size: 13px;
    }
    </style>
    """,
    unsafe_allow_html=True
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.title("🎬 MovieVerse")
    st.caption("Your personal movie universe")

    st.divider()

    pages = [
        "🏠 Home",
        "🔎 Discover",
        "🎬 Movie Details",
        "❤️ Favorites",
        "📋 Watchlist",
        "⭐ My Ratings",
        "📝 Reviews",
        "🤖 Recommendations",
        "📊 Analytics"
    ]

    current_index = (
        pages.index(st.session_state.page)
        if st.session_state.page in pages
        else 0
    )

    selected_page = st.radio(
        "Navigation",
        pages,
        index=current_index,
        key="main_navigation"
    )

    if selected_page != st.session_state.page:
        st.session_state.page = selected_page

    st.divider()

    st.metric("🎬 Movies", len(df))

    favorites_count = len(get_favorites())
    watchlist_count = len(get_watchlist())
    ratings_count = len(get_ratings())
    reviews_count = len(get_reviews())

    st.metric("❤️ Favorites", favorites_count)
    st.metric("📋 Watchlist", watchlist_count)
    st.metric("⭐ Ratings", ratings_count)
    st.metric("📝 Reviews", reviews_count)


# ============================================================
# HEADER
# ============================================================

st.title("🎬 MovieVerse")

st.caption(
    "Discover • Search • Rate • Review • Save • Get Recommendations"
)


# ============================================================
# HOME
# ============================================================

if st.session_state.page == "🏠 Home":

    st.markdown(
        """
        <div class="hero">
            <h2>Welcome to MovieVerse 🍿</h2>
            <p>
                Explore movies, save your favorites, build your watchlist,
                rate movies, write reviews and discover recommendations.
            </p>
        </div>
        """,
        unsafe_allow_html=True
    )

    col1, col2, col3, col4 = st.columns(4)

    average_rating = pd.to_numeric(
        df.get("rating", pd.Series(dtype=float)),
        errors="coerce"
    ).mean()

    popularity_movie = (
        df.loc[df["popularity"].idxmax(), "title"]
        if "popularity" in df.columns and not df.empty
        else "N/A"
    )

    with col1:
        st.metric("🎬 Movies Available", len(df))

    with col2:
        st.metric(
            "⭐ Average TMDB Rating",
            f"{average_rating:.1f}/10" if pd.notna(average_rating) else "N/A"
        )

    with col3:
        st.metric("🔥 Most Popular", popularity_movie)

    with col4:
        st.metric("❤️ My Favorites", len(get_favorites()))

    st.divider()

    st.subheader("🔥 Popular Movies")

    popular = (
        df.sort_values("popularity", ascending=False)
        .head(8)
    )

    cols = st.columns(4)

    for index, (_, movie) in enumerate(popular.iterrows()):
        with cols[index % 4]:
            poster = poster_url(movie)

            if poster:
                st.image(poster, use_container_width=True)

            st.markdown(
                f"<div class='movie-title'>{movie['title']}</div>",
                unsafe_allow_html=True
            )

            rating = movie.get("rating", None)

            if pd.notna(rating):
                st.caption(f"⭐ {float(rating):.1f}/10")

            st.button(
                "View Details",
                key=f"home_details_{int(movie['id'])}_{index}",
                on_click=open_details,
                args=(int(movie["id"]),)
            )

    st.divider()

    st.subheader("⭐ Top Rated")

    top_rated = (
        df[df["rating"] > 0]
        .sort_values("rating", ascending=False)
        .head(8)
    )

    cols = st.columns(4)

    for index, (_, movie) in enumerate(top_rated.iterrows()):
        with cols[index % 4]:
            poster = poster_url(movie)

            if poster:
                st.image(poster, use_container_width=True)

            st.markdown(
                f"**{movie['title']}**"
            )

            st.caption(
                f"⭐ {float(movie['rating']):.2f}/10"
            )

            st.button(
                "View Details",
                key=f"top_details_{int(movie['id'])}_{index}",
                on_click=open_details,
                args=(int(movie["id"]),)
            )


# ============================================================
# DISCOVER
# ============================================================

elif st.session_state.page == "🔎 Discover":

    st.header("🔎 Discover Movies")

    search = st.text_input(
        "Search by movie title or description",
        placeholder="Try Interstellar, Spider-Man, Avatar..."
    )

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        min_rating = st.slider(
            "Minimum rating",
            0.0,
            10.0,
            0.0,
            0.1
        )

    with col2:
        language_options = ["All"]

        if "language" in df.columns:
            language_options += sorted(
                [
                    str(x).lower()
                    for x in df["language"].dropna().unique()
                    if str(x).strip()
                ]
            )

        language = st.selectbox(
            "Language",
            language_options
        )

    with col3:
        min_year = int(
            pd.to_numeric(
                df["release_year"],
                errors="coerce"
            ).dropna().min()
        )

        max_year = int(
            pd.to_numeric(
                df["release_year"],
                errors="coerce"
            ).dropna().max()
        )

        year_range = st.slider(
            "Release year",
            min_year,
            max_year,
            (min_year, max_year)
        )

    with col4:
        sort_by = st.selectbox(
            "Sort by",
            [
                "Popularity",
                "Rating",
                "Newest",
                "Oldest",
                "Title"
            ]
        )

    results = df.copy()

    if search.strip():
        search_text = search.strip().lower()

        title_match = results["title"].str.lower().str.contains(
            search_text,
            na=False
        )

        overview_match = results["overview"].str.lower().str.contains(
            search_text,
            na=False
        )

        results = results[title_match | overview_match]

    results = results[
        pd.to_numeric(results["rating"], errors="coerce").fillna(0)
        >= min_rating
    ]

    results = results[
        pd.to_numeric(results["release_year"], errors="coerce")
        .fillna(0)
        .between(year_range[0], year_range[1])
    ]

    if language != "All":
        results = results[
            results["language"].str.lower() == language
        ]

    if sort_by == "Popularity":
        results = results.sort_values(
            "popularity",
            ascending=False
        )
    elif sort_by == "Rating":
        results = results.sort_values(
            "rating",
            ascending=False
        )
    elif sort_by == "Newest":
        results = results.sort_values(
            "release_year",
            ascending=False
        )
    elif sort_by == "Oldest":
        results = results.sort_values(
            "release_year",
            ascending=True
        )
    else:
        results = results.sort_values(
            "title",
            ascending=True
        )

    st.write(f"**{len(results)} movie(s) found**")

    if results.empty:
        st.warning("No movies match your search/filter settings.")
    else:
        display_results = results.head(40)

        for row_index, (_, movie) in enumerate(display_results.iterrows()):

            movie_id = int(movie["id"])

            with st.container(border=True):

                col1, col2, col3 = st.columns([1.1, 3.5, 1.3])

                with col1:
                    poster = poster_url(movie)

                    if poster:
                        st.image(
                            poster,
                            use_container_width=True
                        )
                    else:
                        st.write("🎬")

                with col2:
                    st.subheader(str(movie["title"]))

                    rating = movie.get("rating", None)
                    popularity = movie.get("popularity", None)
                    release_year = movie.get("release_year", None)

                    info = []

                    if pd.notna(rating):
                        info.append(f"⭐ {float(rating):.1f}")

                    if pd.notna(popularity):
                        info.append(
                            f"🔥 {float(popularity):.0f}"
                        )

                    if pd.notna(release_year):
                        info.append(
                            f"📅 {int(release_year)}"
                        )

                    st.caption(" • ".join(info))

                    overview = str(
                        safe_value(movie, "overview", "")
                    )

                    if overview:
                        if len(overview) > 260:
                            overview = overview[:260] + "..."

                        st.write(overview)

                with col3:
                    st.button(
                        "📖 View Details",
                        key=f"discover_details_{movie_id}_{row_index}",
                        on_click=open_details,
                        args=(movie_id,)
                    )

                    if is_favorite(movie_id):
                        st.button(
                            "💔 Remove Favorite",
                            key=f"discover_remove_fav_{movie_id}_{row_index}",
                            on_click=remove_favorite,
                            args=(movie_id,)
                        )
                    else:
                        st.button(
                            "❤️ Favorite",
                            key=f"discover_add_fav_{movie_id}_{row_index}",
                            on_click=add_favorite,
                            args=(movie,)
                        )

                    if is_watchlisted(movie_id):
                        st.button(
                            "❌ Remove Watchlist",
                            key=f"discover_remove_watch_{movie_id}_{row_index}",
                            on_click=remove_watchlist,
                            args=(movie_id,)
                        )
                    else:
                        st.button(
                            "📋 Watchlist",
                            key=f"discover_add_watch_{movie_id}_{row_index}",
                            on_click=add_watchlist,
                            args=(movie,)
                        )


# ============================================================
# MOVIE DETAILS
# ============================================================

elif st.session_state.page == "🎬 Movie Details":

    movie_id = st.session_state.selected_movie_id

    if movie_id is None:
        st.info(
            "Select a movie from Discover or Home to view its details."
        )
    else:
        movie = get_movie(movie_id)

        if movie is None:
            st.error("Movie could not be found.")
        else:
            if st.button("⬅️ Back to Discover"):
                st.session_state.page = "🔎 Discover"
                st.rerun()

            backdrop = backdrop_url(movie)

            if backdrop:
                st.image(
                    backdrop,
                    use_container_width=True
                )

            st.header(f"🎬 {movie['title']}")

            col1, col2 = st.columns([1.2, 2.8])

            with col1:
                poster = poster_url(movie)

                if poster:
                    st.image(
                        poster,
                        use_container_width=True
                    )

            with col2:
                rating = safe_value(movie, "rating")
                popularity = safe_value(movie, "popularity")
                release_date = safe_value(movie, "release_date")
                release_year = safe_value(movie, "release_year")
                language = safe_value(movie, "language")

                m1, m2, m3 = st.columns(3)

                with m1:
                    if rating != "N/A":
                        st.metric(
                            "⭐ TMDB Rating",
                            f"{float(rating):.2f}/10"
                        )
                    else:
                        st.metric("⭐ TMDB Rating", "N/A")

                with m2:
                    if popularity != "N/A":
                        st.metric(
                            "🔥 Popularity",
                            f"{float(popularity):.1f}"
                        )
                    else:
                        st.metric("🔥 Popularity", "N/A")

                with m3:
                    st.metric(
                        "📅 Year",
                        str(release_year)
                    )

                st.write(f"**Release Date:** {release_date}")
                st.write(
                    f"**Language:** {str(language).upper()}"
                    if language != "N/A"
                    else "**Language:** N/A"
                )

                st.divider()

                st.subheader("📝 Overview")

                overview = safe_value(
                    movie,
                    "overview",
                    "No overview available."
                )

                st.write(str(overview))

                st.divider()

                action1, action2 = st.columns(2)

                with action1:
                    if is_favorite(movie_id):
                        st.button(
                            "💔 Remove from Favorites",
                            key=f"details_remove_fav_{movie_id}",
                            on_click=remove_favorite,
                            args=(movie_id,)
                        )
                    else:
                        st.button(
                            "❤️ Add to Favorites",
                            key=f"details_add_fav_{movie_id}",
                            on_click=add_favorite,
                            args=(movie,)
                        )

                with action2:
                    if is_watchlisted(movie_id):
                        st.button(
                            "❌ Remove from Watchlist",
                            key=f"details_remove_watch_{movie_id}",
                            on_click=remove_watchlist,
                            args=(movie_id,)
                        )
                    else:
                        st.button(
                            "📋 Add to Watchlist",
                            key=f"details_add_watch_{movie_id}",
                            on_click=add_watchlist,
                            args=(movie,)
                        )

            # ----------------------------------------------------
            # PERSONAL RATING
            # ----------------------------------------------------

            st.divider()
            st.subheader("⭐ Your Rating")

            existing_rating = get_user_rating(movie_id)

            rating_options = list(range(1, 11))

            selected_rating = st.selectbox(
                "Give this movie a rating from 1 to 10",
                rating_options,
                index=(
                    existing_rating - 1
                    if existing_rating
                    else 7
                ),
                key=f"rating_select_{movie_id}"
            )

            if existing_rating:
                st.info(
                    f"Your current rating: ⭐ {existing_rating}/10"
                )

            if st.button(
                "💾 Save Rating",
                key=f"save_rating_{movie_id}"
            ):
                save_rating(movie, selected_rating)
                st.success(
                    f"Your rating of {selected_rating}/10 was saved!"
                )
                st.rerun()

            # ----------------------------------------------------
            # REVIEWS
            # ----------------------------------------------------

            st.divider()
            st.subheader("📝 Write a Review")

            review_rating = st.selectbox(
                "Review rating",
                list(range(1, 11)),
                index=7,
                key=f"review_rating_{movie_id}"
            )

            review_text = st.text_area(
                "Your review",
                placeholder="What did you think about this movie?",
                key=f"review_text_{movie_id}"
            )

            if st.button(
                "📝 Submit Review",
                key=f"submit_review_{movie_id}"
            ):
                if not review_text.strip():
                    st.warning(
                        "Please write something before submitting."
                    )
                else:
                    add_review(
                        movie,
                        review_rating,
                        review_text
                    )

                    st.success("Review added successfully!")

                    st.session_state[
                        f"review_text_{movie_id}"
                    ] = ""

                    st.rerun()

            # ----------------------------------------------------
            # EXISTING REVIEWS
            # ----------------------------------------------------

            reviews = get_reviews(movie_id)

            st.subheader(
                f"💬 Reviews ({len(reviews)})"
            )

            if reviews.empty:
                st.info(
                    "No reviews yet. Be the first to review this movie!"
                )
            else:
                for _, review in reviews.iterrows():

                    with st.container(border=True):
                        st.write(
                            f"⭐ **{int(review['rating'])}/10**"
                        )

                        st.write(
                            str(review["review"])
                        )

                        st.caption(
                            f"Posted on {review['created_at']}"
                        )

                        st.button(
                            "🗑️ Delete",
                            key=f"delete_review_{int(review['id'])}",
                            on_click=delete_review,
                            args=(int(review["id"]),)
                        )

            # ----------------------------------------------------
            # SIMILAR MOVIES
            # ----------------------------------------------------

            st.divider()
            st.subheader("🤖 You May Also Like")

            recommendations = get_recommendations(
                movie,
                limit=6
            )

            if recommendations.empty:
                st.info(
                    "No recommendations available."
                )
            else:
                cols = st.columns(3)

                for index, (_, recommendation) in enumerate(
                    recommendations.iterrows()
                ):
                    with cols[index % 3]:
                        poster = poster_url(recommendation)

                        if poster:
                            st.image(
                                poster,
                                use_container_width=True
                            )

                        st.write(
                            f"**{recommendation['title']}**"
                        )

                        if pd.notna(
                            recommendation.get("rating")
                        ):
                            st.caption(
                                f"⭐ {float(recommendation['rating']):.1f}/10"
                            )

                        st.button(
                            "View Details",
                            key=(
                                f"similar_{int(recommendation['id'])}"
                                f"_{movie_id}_{index}"
                            ),
                            on_click=open_details,
                            args=(int(recommendation["id"]),)
                        )


# ============================================================
# FAVORITES
# ============================================================

elif st.session_state.page == "❤️ Favorites":

    st.header("❤️ My Favorites")

    favorites = get_favorites()

    if favorites.empty:
        st.info(
            "You haven't added any movies to Favorites yet."
        )
    else:
        st.success(
            f"You have {len(favorites)} favorite movie(s)."
        )

        for row_index, favorite in favorites.iterrows():

            movie_id = int(favorite["movie_id"])
            movie = get_movie(movie_id)

            if movie is None:
                continue

            with st.container(border=True):

                col1, col2, col3 = st.columns([1, 4, 1])

                with col1:
                    poster = poster_url(movie)

                    if poster:
                        st.image(
                            poster,
                            use_container_width=True
                        )

                with col2:
                    st.subheader(
                        str(favorite["title"])
                    )

                    rating = movie.get("rating")

                    if pd.notna(rating):
                        st.caption(
                            f"⭐ {float(rating):.1f}/10"
                        )

                    st.caption(
                        f"Movie ID: {movie_id}"
                    )

                with col3:
                    st.button(
                        "📖 Details",
                        key=f"fav_details_{movie_id}_{row_index}",
                        on_click=open_details,
                        args=(movie_id,)
                    )

                    st.button(
                        "❌ Remove",
                        key=f"fav_remove_{movie_id}_{row_index}",
                        on_click=remove_favorite,
                        args=(movie_id,)
                    )


# ============================================================
# WATCHLIST
# ============================================================

elif st.session_state.page == "📋 Watchlist":

    st.header("📋 My Watchlist")

    watchlist = get_watchlist()

    if watchlist.empty:
        st.info(
            "Your watchlist is empty. Add movies from Discover."
        )
    else:
        st.success(
            f"You have {len(watchlist)} movie(s) in your watchlist."
        )

        for row_index, item in watchlist.iterrows():

            movie_id = int(item["movie_id"])
            movie = get_movie(movie_id)

            if movie is None:
                continue

            with st.container(border=True):

                col1, col2, col3 = st.columns([1, 4, 1])

                with col1:
                    poster = poster_url(movie)

                    if poster:
                        st.image(
                            poster,
                            use_container_width=True
                        )

                with col2:
                    st.subheader(
                        str(item["title"])
                    )

                    rating = movie.get("rating")

                    if pd.notna(rating):
                        st.caption(
                            f"⭐ {float(rating):.1f}/10"
                        )

                    release_year = movie.get("release_year")

                    if pd.notna(release_year):
                        st.caption(
                            f"📅 {int(release_year)}"
                        )

                with col3:
                    st.button(
                        "📖 Details",
                        key=f"watch_details_{movie_id}_{row_index}",
                        on_click=open_details,
                        args=(movie_id,)
                    )

                    st.button(
                        "❌ Remove",
                        key=f"watch_remove_{movie_id}_{row_index}",
                        on_click=remove_watchlist,
                        args=(movie_id,)
                    )


# ============================================================
# MY RATINGS
# ============================================================

elif st.session_state.page == "⭐ My Ratings":

    st.header("⭐ My Ratings")

    ratings = get_ratings()

    if ratings.empty:
        st.info(
            "You haven't rated any movies yet."
        )
    else:
        average_user_rating = ratings["rating"].mean()

        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric(
                "⭐ Movies Rated",
                len(ratings)
            )

        with col2:
            st.metric(
                "📊 Your Average",
                f"{average_user_rating:.1f}/10"
            )

        with col3:
            st.metric(
                "🏆 Highest Rating",
                f"{ratings['rating'].max()}/10"
            )

        st.divider()

        for row_index, item in ratings.iterrows():

            movie_id = int(item["movie_id"])
            movie = get_movie(movie_id)

            with st.container(border=True):

                col1, col2, col3 = st.columns([1, 4, 1])

                with col1:
                    if movie is not None:
                        poster = poster_url(movie)

                        if poster:
                            st.image(
                                poster,
                                use_container_width=True
                            )

                with col2:
                    st.subheader(
                        str(item["title"])
                    )

                    st.write(
                        f"⭐ **Your rating: {int(item['rating'])}/10**"
                    )

                with col3:
                    st.button(
                        "📖 Details",
                        key=f"rating_details_{movie_id}_{row_index}",
                        on_click=open_details,
                        args=(movie_id,)
                    )


# ============================================================
# REVIEWS
# ============================================================

elif st.session_state.page == "📝 Reviews":

    st.header("📝 My Reviews")

    reviews = get_reviews()

    if reviews.empty:
        st.info(
            "You haven't written any reviews yet."
        )
    else:
        st.success(
            f"You have written {len(reviews)} review(s)."
        )

        for _, review in reviews.iterrows():

            movie_id = int(review["movie_id"])

            with st.container(border=True):

                st.subheader(
                    str(review["title"])
                )

                st.write(
                    f"⭐ **{int(review['rating'])}/10**"
                )

                st.write(
                    str(review["review"])
                )

                st.caption(
                    f"Posted on {review['created_at']}"
                )

                col1, col2 = st.columns(2)

                with col1:
                    st.button(
                        "📖 View Movie",
                        key=f"review_movie_{int(review['id'])}",
                        on_click=open_details,
                        args=(movie_id,)
                    )

                with col2:
                    st.button(
                        "🗑️ Delete Review",
                        key=f"review_delete_{int(review['id'])}",
                        on_click=delete_review,
                        args=(int(review["id"]),)
                    )


# ============================================================
# RECOMMENDATIONS
# ============================================================

elif st.session_state.page == "🤖 Recommendations":

    st.header("🤖 Movie Recommendations")

    st.write(
        "Recommendations are generated from your saved ratings "
        "and movie descriptions."
    )

    recommendations = get_personal_recommendations(
        limit=12
    )

    if recommendations.empty:
        st.info(
            "Rate some movies first to improve your recommendations."
        )
    else:
        ratings = get_ratings()

        if ratings.empty:
            st.info(
                "You haven't rated movies yet, so these are "
                "top-rated recommendations."
            )
        else:
            st.success(
                "Your recommendations are personalized from your ratings."
            )

        cols = st.columns(4)

        for index, (_, movie) in enumerate(
            recommendations.iterrows()
        ):
            with cols[index % 4]:

                poster = poster_url(movie)

                if poster:
                    st.image(
                        poster,
                        use_container_width=True
                    )

                st.markdown(
                    f"**{movie['title']}**"
                )

                if pd.notna(movie.get("rating")):
                    st.caption(
                        f"⭐ {float(movie['rating']):.1f}/10"
                    )

                if pd.notna(movie.get("release_year")):
                    st.caption(
                        f"📅 {int(movie['release_year'])}"
                    )

                st.button(
                    "📖 View Details",
                    key=f"recommend_{int(movie['id'])}_{index}",
                    on_click=open_details,
                    args=(int(movie["id"]),)
                )


# ============================================================
# ANALYTICS
# ============================================================

elif st.session_state.page == "📊 Analytics":

    st.header("📊 Movie Analytics")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "🎬 Total Movies",
            len(df)
        )

    with col2:
        avg = pd.to_numeric(
            df["rating"],
            errors="coerce"
        ).mean()

        st.metric(
            "⭐ Average Rating",
            f"{avg:.2f}"
        )

    with col3:
        st.metric(
            "❤️ Favorites",
            len(get_favorites())
        )

    with col4:
        st.metric(
            "📋 Watchlist",
            len(get_watchlist())
        )

    st.divider()

    # --------------------------------------------------------
    # RATING DISTRIBUTION
    # --------------------------------------------------------

    st.subheader("⭐ Rating Distribution")

    rating_series = pd.to_numeric(
        df["rating"],
        errors="coerce"
    ).dropna()

    rating_bins = pd.cut(
        rating_series,
        bins=[0, 2, 4, 6, 8, 10],
        labels=[
            "0–2",
            "2–4",
            "4–6",
            "6–8",
            "8–10"
        ],
        include_lowest=True
    )

    rating_counts = (
        rating_bins
        .value_counts()
        .sort_index()
    )

    st.bar_chart(rating_counts)

    # --------------------------------------------------------
    # MOVIES BY YEAR
    # --------------------------------------------------------

    st.subheader("📅 Movies by Release Year")

    year_counts = (
        pd.to_numeric(
            df["release_year"],
            errors="coerce"
        )
        .dropna()
        .astype(int)
        .value_counts()
        .sort_index()
    )

    st.line_chart(year_counts)

    # --------------------------------------------------------
    # POPULARITY
    # --------------------------------------------------------

    st.subheader("🔥 Most Popular Movies")

    popular_table = (
        df[
            [
                "title",
                "rating",
                "popularity",
                "release_year"
            ]
        ]
        .sort_values(
            "popularity",
            ascending=False
        )
        .head(10)
        .copy()
    )

    popular_table.columns = [
        "Movie",
        "Rating",
        "Popularity",
        "Year"
    ]

    st.dataframe(
        popular_table,
        use_container_width=True,
        hide_index=True
    )

    # --------------------------------------------------------
    # LANGUAGE ANALYSIS
    # --------------------------------------------------------

    if "language" in df.columns:
        st.subheader("🌐 Movies by Language")

        language_counts = (
            df["language"]
            .replace("", pd.NA)
            .dropna()
            .astype(str)
            .str.upper()
            .value_counts()
            .head(15)
        )

        st.bar_chart(language_counts)

    # --------------------------------------------------------
    # PERSONAL ANALYTICS
    # --------------------------------------------------------

    st.divider()
    st.subheader("👤 Your Movie Profile")

    ratings = get_ratings()
    favorites = get_favorites()
    watchlist = get_watchlist()
    reviews = get_reviews()

    p1, p2, p3, p4 = st.columns(4)

    with p1:
        st.metric(
            "⭐ Rated",
            len(ratings)
        )

    with p2:
        st.metric(
            "❤️ Favorites",
            len(favorites)
        )

    with p3:
        st.metric(
            "📋 Watchlist",
            len(watchlist)
        )

    with p4:
        st.metric(
            "📝 Reviews",
            len(reviews)
        )

    if not ratings.empty:
        st.write(
            f"Your average rating is "
            f"**{ratings['rating'].mean():.1f}/10**."
        )

        best_user_rating = ratings[
            ratings["rating"] == ratings["rating"].max()
        ]

        st.write(
            "🏆 Your highest-rated movie(s): "
            + ", ".join(
                best_user_rating["title"].astype(str).tolist()
            )
        )


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    "🎬 MovieVerse • Built with Python • "
    "Streamlit • SQLite • Pandas"
)
