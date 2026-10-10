import streamlit as st
import pandas as pd
import sqlite3
import os
import re
import html
from collections import Counter
from datetime import datetime

import tmdb_service  # TMDB requests, matching, caching (see tmdb_service.py)

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

DB_NAME = "movieverse.db"
CSV_NAME = "movies.csv"

# TMDB image server is only used to display the poster paths
IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
BACKDROP_BASE = "https://image.tmdb.org/t/p/w1280"


def get_tmdb_api_key():
    """
    Read the TMDB credential without ever hardcoding or displaying it.
    Order: .streamlit/secrets.toml -> TMDB_API_KEY environment variable -> local .env file.
    """
    try:
        key = str(st.secrets.get("TMDB_API_KEY", "")).strip()
        if key:
            return key
    except Exception:
        pass

    return tmdb_service.load_env_credential()


def tmdb_image(path, size="w500"):
    if not path:
        return None
    return f"https://image.tmdb.org/t/p/{size}{path}"


def format_runtime(minutes):
    if not minutes:
        return "N/A"
    minutes = int(minutes)
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m" if hours else f"{mins}m"


def review_date(value):
    if value is None:
        return "Date unavailable"
    try:
        if pd.isna(value):
            return "Date unavailable"
    except Exception:
        pass
    text = str(value).strip()
    return text if text else "Date unavailable"

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

    # ========================================================
    # DATABASE MIGRATION
    # Fix older movieverse.db files that do not have
    # the created_at column in the reviews table.
    # This preserves existing Favorites, Watchlist, Ratings
    # and Reviews instead of requiring deletion of the DB.
    # ========================================================
    review_columns = [
        row[1]
        for row in cur.execute(
            "PRAGMA table_info(reviews)"
        ).fetchall()
    ]

    if "created_at" not in review_columns:
        cur.execute(
            "ALTER TABLE reviews ADD COLUMN created_at TEXT"
        )

        cur.execute(
            """
            UPDATE reviews
            SET created_at = ?
            WHERE created_at IS NULL
            """,
            (datetime.now().strftime("%Y-%m-%d %H:%M"),)
        )

    conn.commit()
    conn.close()


create_database()


# ============================================================
# LOAD MOVIES
# ============================================================

@st.cache_data
def load_movies():
    if not os.path.exists(CSV_NAME):
        return pd.DataFrame()

    try:
        data = pd.read_csv(CSV_NAME)
    except Exception as e:
        st.error(f"Could not read {CSV_NAME}: {e}")
        return pd.DataFrame()

    # Clean duplicate rows and normalize column names
    data.columns = [str(c).strip() for c in data.columns]

    if "id" in data.columns:
        data["id"] = pd.to_numeric(data["id"], errors="coerce")

    for column in ["rating", "popularity", "release_year"]:
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")

    if "title" in data.columns:
        data["title"] = data["title"].fillna("Unknown Title").astype(str)

    if "overview" in data.columns:
        data["overview"] = data["overview"].fillna("").astype(str)

    if "language" in data.columns:
        data["language"] = data["language"].fillna("").astype(str)

    # Keep first occurrence when the same movie ID appears twice
    if "id" in data.columns:
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
# MOVIE DETAILS PAGE (TMDB) - DATA HELPERS AND RENDERING
# All TMDB requests, matching and caching live in tmdb_service.py.
# ============================================================

def _esc(value):
    return html.escape(str(value), quote=True)


def _build_local_index():
    """Index the local CSV so TMDB movies can be linked back to MovieVerse movies."""
    titles = {}
    by_title_year = {}

    valid = df.dropna(subset=["id"])
    for movie_id, title, year in valid[["id", "title", "release_year"]].itertuples(index=False):
        movie_id = int(movie_id)
        titles[movie_id] = str(title)
        year = int(year) if pd.notna(year) else None
        by_title_year.setdefault(
            (tmdb_service.norm_title(title), year),
            movie_id
        )

    return titles, by_title_year


LOCAL_TITLES, LOCAL_BY_TITLE_YEAR = _build_local_index()


def find_local_movie_id(tmdb_id, title, year):
    """Return the MovieVerse (CSV) ID for a TMDB movie, or None if it is not in the library."""
    try:
        tid = int(tmdb_id)
    except (TypeError, ValueError):
        tid = None

    if (
        tid in LOCAL_TITLES
        and tmdb_service.title_similarity(LOCAL_TITLES[tid], title) >= 0.6
    ):
        return tid

    return LOCAL_BY_TITLE_YEAR.get(
        (tmdb_service.norm_title(title), year)
    )


def csv_movie_dict(movie):
    """Plain dict of the local CSV row (NaN -> None) used as fallback data."""
    result = {}
    for column in [
        "title", "release_date", "release_year", "rating", "popularity",
        "overview", "poster_path", "backdrop_path", "language"
    ]:
        value = movie[column] if column in movie.index else None
        result[column] = None if (value is None or pd.isna(value)) else value
    return result


def load_tmdb_bundle(movie):
    """Fetch (or load from cache) the TMDB details. Never raises."""
    try:
        year = pd.to_numeric(movie.get("release_year"), errors="coerce")
        year = int(year) if pd.notna(year) else None

        return tmdb_service.get_movie_details(
            int(movie["id"]),
            str(movie["title"]),
            year,
            get_tmdb_api_key()
        )
    except Exception:
        return {
            "status": "unavailable",
            "reason": "error",
            "message": tmdb_service.ERROR_MESSAGES["error"],
            "data": None,
            "collection": None,
            "tmdb_id": None,
            "matched_by": None,
            "stored_at": None,
            "cached": False,
        }


def render_tmdb_status(bundle, movie_id):
    status = bundle.get("status")

    if status == "ok":
        if bundle.get("matched_by") == "title and year search":
            st.caption(
                "ℹ️ This movie's ID is not a TMDB ID, so it was matched "
                f"on TMDB by title and release year (TMDB #{bundle.get('tmdb_id')})."
            )
        return

    if status == "stale":
        saved = datetime.fromtimestamp(bundle["stored_at"]).strftime("%b %d, %Y %H:%M")
        st.warning(
            f"{bundle.get('message', '')} Showing the copy saved on {saved}."
        )
    else:
        st.warning(
            f"{bundle.get('message', '')} Showing the basic details "
            "from your local movie data instead."
        )

    if bundle.get("reason") not in ("no_key", "invalid_key"):
        st.button(
            "🔄 Retry TMDB",
            key=f"retry_tmdb_{movie_id}",
            on_click=tmdb_service.forget_failure,
            args=(movie_id,)
        )


def _na(text="Not available"):
    return f'<span class="mv-na">{_esc(text)}</span>'


def render_hero(view):
    backdrop = tmdb_service.image_url(view["backdrop_path"], "w1280")
    poster = tmdb_service.image_url(view["poster_path"], "w500")

    style = (
        f'<style>.mv-hero{{background-image:url("{backdrop}");}}</style>'
        if backdrop else ""
    )

    title = _esc(view["title"])

    poster_html = (
        f'<img class="mv-hero-poster" src="{poster}" alt="Poster of {title}">'
        if poster else '<div class="mv-hero-poster mv-ph">🎬</div>'
    )

    year_html = (
        f' <span class="mv-year">({view["year"]})</span>' if view["year"] else ""
    )

    original_html = ""
    if view["original_title"] and (
        tmdb_service.norm_title(view["original_title"])
        != tmdb_service.norm_title(view["title"])
    ):
        original_html = (
            f'<div class="mv-orig">Original title: {_esc(view["original_title"])}</div>'
        )

    tagline_html = (
        f'<div class="mv-tagline">“{_esc(view["tagline"])}”</div>'
        if view["tagline"] else ""
    )

    meta = []
    if view["release_date_text"]:
        meta.append(_esc(view["release_date_text"]))
    if view["certification"]:
        meta.append(f'<span class="mv-cert">{_esc(view["certification"])}</span>')
    if view["runtime_text"]:
        meta.append(_esc(view["runtime_text"]))
    if view["status"]:
        meta.append(_esc(view["status"]))
    meta_html = (
        '<div class="mv-meta">'
        + '<span class="mv-dot"> • </span>'.join(meta)
        + "</div>"
    ) if meta else ""

    chips_html = "".join(
        f'<span class="mv-chip">{_esc(g)}</span>' for g in view["genres"]
    )

    score_bits = []
    if view["vote_average"] is not None:
        score_bits.append(f'⭐ <b>{view["vote_average"]:.1f}</b>/10')
    if view["vote_count"]:
        score_bits.append(f'{view["vote_count"]:,} votes')
    if view["popularity"] is not None:
        score_bits.append(f'🔥 {view["popularity"]:,.1f} popularity')
    score_html = (
        '<div class="mv-score">' + " &nbsp;·&nbsp; ".join(score_bits) + "</div>"
    ) if score_bits else ""

    st.markdown(
        style
        + '<div class="mv-hero"><div class="mv-hero-inner">'
        + poster_html
        + '<div class="mv-hero-text">'
        + f'<div class="mv-title">{title}{year_html}</div>'
        + original_html
        + tagline_html
        + meta_html
        + f'<div class="mv-chips">{chips_html}</div>'
        + score_html
        + "</div></div></div>",
        unsafe_allow_html=True
    )


def _facts_html(facts):
    cells = []
    for label, value in facts:
        cells.append(
            '<div class="mv-fact">'
            f'<div class="mv-fact-label">{_esc(label)}</div>'
            f'<div class="mv-fact-value">{value if value else _na()}</div>'
            "</div>"
        )
    return '<div class="mv-facts">' + "".join(cells) + "</div>"


def _link_html(url, text):
    return (
        f'<a href="{_esc(url)}" target="_blank" rel="noopener noreferrer">'
        f"{_esc(text)}</a>"
    )


def render_overview(view):
    st.subheader("📝 Synopsis")
    st.write(view["overview"] or "No synopsis available.")

    certification = None
    if view["certification"]:
        certification = _esc(view["certification"])
        if view["certification_region"]:
            certification += f' <span class="mv-na">({_esc(view["certification_region"])})</span>'

    rating = (
        f'⭐ {view["vote_average"]:.1f} / 10'
        if view["vote_average"] is not None else None
    )

    facts = [
        ("Release date", _esc(view["release_date_text"] or view["release_date_raw"] or "") or None),
        ("Release year", _esc(view["year"]) if view["year"] else None),
        ("Runtime", _esc(view["runtime_text"]) if view["runtime_text"] else None),
        ("Status", _esc(view["status"]) if view["status"] else None),
        ("Age certification", certification),
        ("Adult content", _esc(view["adult"]) if view["adult"] else None),
        ("TMDB rating", _esc(rating) if rating else None),
        ("Vote count", f'{view["vote_count"]:,}' if view["vote_count"] else None),
        ("Popularity", f'{view["popularity"]:,.1f}' if view["popularity"] is not None else None),
        ("Original language", _esc(view["original_language"]) if view["original_language"] else None),
        ("Spoken languages", _esc(", ".join(view["spoken_languages"])) if view["spoken_languages"] else None),
        ("Budget", _esc(view["budget_text"]) if view["budget_text"] else None),
        ("Revenue", _esc(view["revenue_text"]) if view["revenue_text"] else None),
        (
            "Collection / franchise",
            _esc(view["collection"]["name"]) if view["collection"] else None
        ),
        (
            "Official website",
            _link_html(view["homepage"], "Visit website ↗") if view["homepage"] else None
        ),
    ]

    st.markdown(_facts_html(facts), unsafe_allow_html=True)

    if view["source"] == "csv":
        st.caption(
            "Only the details stored in your local movie data are shown. "
            "Everything else is marked “Not available” instead of being guessed."
        )

    if view["genres"]:
        st.markdown("**🎭 Genres**")
        st.markdown(
            "".join(f'<span class="mv-chip mv-chip-body">{_esc(g)}</span>' for g in view["genres"]),
            unsafe_allow_html=True
        )

    if view["keywords"]:
        st.markdown("**🏷️ Keywords**")
        st.markdown(
            "".join(f'<span class="mv-chip mv-chip-body">{_esc(k)}</span>' for k in view["keywords"]),
            unsafe_allow_html=True
        )

    links = []
    if view["tmdb_url"]:
        links.append(_link_html(view["tmdb_url"], "TMDB page ↗"))
    if view["imdb_url"]:
        links.append(_link_html(view["imdb_url"], "IMDb page ↗"))
    if links:
        st.markdown(" &nbsp;|&nbsp; ".join(links), unsafe_allow_html=True)


def people_grid_html(people):
    cards = []
    for person in people:
        name = _esc(person["name"])
        photo = tmdb_service.image_url(person.get("profile_path"), "w185")

        if photo:
            photo_html = f'<img src="{photo}" loading="lazy" alt="{name}">'
        else:
            initials = "".join(w[0] for w in person["name"].split()[:2]).upper() or "?"
            photo_html = f'<div class="mv-ph">{_esc(initials)}</div>'

        role = ", ".join(person["characters"])

        cards.append(
            '<div class="mv-person">'
            + photo_html
            + f'<div class="mv-pname">{name}</div>'
            + f'<div class="mv-prole">{_esc(role) if role else "Role not listed"}</div>'
            + "</div>"
        )

    return '<div class="mv-people">' + "".join(cards) + "</div>"


CREW_ICONS = {
    "Directors": "🎬",
    "Writers": "✍️",
    "Producers": "🎟️",
    "Executive Producers": "💼",
    "Cinematographers": "📷",
    "Editors": "✂️",
    "Music Composers": "🎼",
    "Production Designers": "🏛️",
    "Directing": "🎬",
    "Production": "🎟️",
    "Art": "🎨",
    "Costume & Make-Up": "👗",
    "Camera": "📷",
    "Lighting": "💡",
    "Sound": "🔊",
    "Visual Effects": "✨",
    "Editing": "✂️",
    "Crew": "🛠️",
    "Actors": "🤸",
}

# Labels where repeating the job title next to every name adds nothing.
HIDE_JOB_FOR = {
    "Directors", "Cinematographers", "Editors",
    "Production Designers", "Executive Producers"
}


def crew_card_html(group, limit=None):
    people = group["people"] if limit is None else group["people"][:limit]
    show_jobs = group["label"] not in HIDE_JOB_FOR or not group["is_key"]

    rows = []
    for person in people:
        jobs = ", ".join(person["jobs"])
        job_html = (
            f'<span class="mv-crew-job">{_esc(jobs)}</span>'
            if (show_jobs and jobs) else ""
        )
        rows.append(
            f'<div class="mv-crew-row"><span>{_esc(person["name"])}</span>{job_html}</div>'
        )

    icon = CREW_ICONS.get(group["label"], "🎞️")
    return (
        '<div class="mv-crew-card">'
        f'<div class="mv-crew-title">{icon} {_esc(group["label"])} '
        f'<span class="mv-na">({len(group["people"])})</span></div>'
        + "".join(rows)
        + "</div>"
    )


def render_cast_crew(view):
    cast = view["cast"]

    st.subheader(f"🎭 Cast ({len(cast)})" if cast else "🎭 Cast")

    if not cast:
        st.info("Cast information is not available for this movie.")
    else:
        visible = 18
        st.markdown(people_grid_html(cast[:visible]), unsafe_allow_html=True)

        if len(cast) > visible:
            with st.expander(f"👥 View the complete cast ({len(cast)} credited)"):
                st.markdown(people_grid_html(cast), unsafe_allow_html=True)

    st.subheader("🎥 Crew")

    groups = view["crew_groups"]

    if not groups:
        st.info("Crew credits are not available for this movie.")
        return

    st.caption(f"{view['crew_total']} crew credits listed on TMDB")

    key_groups = [g for g in groups if g["is_key"]]
    other_groups = [g for g in groups if not g["is_key"]]

    preview = 8
    columns = st.columns(2)

    for index, group in enumerate(key_groups):
        with columns[index % 2]:
            st.markdown(crew_card_html(group, preview), unsafe_allow_html=True)

            if len(group["people"]) > preview:
                with st.expander(
                    f"Show all {len(group['people'])} — {group['label']}"
                ):
                    st.markdown(crew_card_html(group), unsafe_allow_html=True)

    if other_groups:
        st.markdown("#### 🛠️ All other crew, by department")

        for group in other_groups:
            icon = CREW_ICONS.get(group["label"], "🎞️")
            with st.expander(f"{icon} {group['label']} ({len(group['people'])})"):
                st.markdown(crew_card_html(group), unsafe_allow_html=True)


def render_production(view):
    st.subheader("🏢 Production companies")

    if view["companies"]:
        cards = []
        for company in view["companies"]:
            logo = tmdb_service.image_url(company["logo_path"], "w185")
            logo_html = (
                f'<img src="{logo}" loading="lazy" alt="{_esc(company["name"])}">'
                if logo else '<div class="mv-co-ph">🏢</div>'
            )
            country = (
                f'<div class="mv-prole">{_esc(company["country"])}</div>'
                if company["country"] else ""
            )
            cards.append(
                '<div class="mv-co">'
                f'<div class="mv-co-logo">{logo_html}</div>'
                f'<div class="mv-pname">{_esc(company["name"])}</div>'
                f"{country}</div>"
            )
        st.markdown(
            '<div class="mv-companies">' + "".join(cards) + "</div>",
            unsafe_allow_html=True
        )
    else:
        st.info("Production company information is not available.")

    st.subheader("🌍 Production countries")

    if view["countries"]:
        st.markdown(
            "".join(f'<span class="mv-chip mv-chip-body">{_esc(c)}</span>' for c in view["countries"]),
            unsafe_allow_html=True
        )
    else:
        st.write("Not available")

    st.subheader("💰 Budget & revenue")

    st.markdown(
        _facts_html([
            ("Budget", _esc(view["budget_text"]) if view["budget_text"] else None),
            ("Revenue", _esc(view["revenue_text"]) if view["revenue_text"] else None),
        ]),
        unsafe_allow_html=True
    )
    st.caption("TMDB reports 0 when a figure is unknown; those are shown as “Not available”.")

    collection = view["collection"]
    if collection:
        st.subheader("🎞️ Collection / franchise")
        st.write(f"This movie belongs to **{collection['name']}**.")

    if view["homepage"]:
        st.markdown(
            "**🌐 Official website:** " + _link_html(view["homepage"], view["homepage"]),
            unsafe_allow_html=True
        )

    rows = view["release_rows"]
    if rows:
        with st.expander(f"📅 Release dates & certifications by country ({len(rows)})"):
            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True
            )


def _video_links_html(videos):
    items = []
    for video in videos:
        extra = " • ".join(
            part for part in [
                video["type"],
                "Official" if video["official"] else None,
                video["published"]
            ] if part
        )
        items.append(
            f"<li>{_link_html(video['url'], video['name'])} "
            f'<span class="mv-na">— {_esc(extra)}</span></li>'
        )
    return "<ul>" + "".join(items) + "</ul>"


def _render_video_group(videos, per_group=4):
    columns = st.columns(2)
    for index, video in enumerate(videos[:per_group]):
        with columns[index % 2]:
            st.video(video["url"])
            caption = video["name"]
            if video["official"]:
                caption += " • Official"
            if video["published"]:
                caption += f" • {video['published']}"
            st.caption(caption)

    if len(videos) > per_group:
        with st.expander(f"More ({len(videos) - per_group})"):
            st.markdown(_video_links_html(videos[per_group:]), unsafe_allow_html=True)


def render_videos(view):
    videos = view["videos"]

    if not (videos["trailers"] or videos["teasers"] or videos["others"]):
        st.info("No YouTube videos are listed on TMDB for this movie.")
        return

    st.subheader("🎞️ Trailers")
    if videos["trailers"]:
        _render_video_group(videos["trailers"])
    else:
        st.write("No trailers listed.")

    st.subheader("👀 Teasers")
    if videos["teasers"]:
        _render_video_group(videos["teasers"])
    else:
        st.write("No teasers listed.")

    if videos["others"]:
        with st.expander(
            f"📼 Clips, featurettes & behind the scenes ({len(videos['others'])})"
        ):
            st.markdown(_video_links_html(videos["others"]), unsafe_allow_html=True)


def _gallery_html(images, thumb_size, css_class):
    tiles = []
    for image in images:
        thumb = tmdb_service.image_url(image["path"], thumb_size)
        full = tmdb_service.image_url(image["path"], "original")
        if not thumb:
            continue
        tiles.append(
            f'<a href="{full}" target="_blank" rel="noopener noreferrer">'
            f'<img src="{thumb}" loading="lazy" alt="Movie image"></a>'
        )
    return f'<div class="mv-gal {css_class}">' + "".join(tiles) + "</div>"


def render_gallery(view):
    posters = view["images"]["posters"]
    backdrops = view["images"]["backdrops"]

    if not posters and not backdrops:
        st.info("No extra posters or backdrops are available for this movie.")
        return

    st.subheader(f"🖼️ Posters ({len(posters)})")
    if posters:
        st.markdown(_gallery_html(posters[:8], "w342", "mv-gal-posters"), unsafe_allow_html=True)
        if len(posters) > 8:
            with st.expander(f"View all posters ({min(len(posters), 40)})"):
                st.markdown(_gallery_html(posters[:40], "w342", "mv-gal-posters"), unsafe_allow_html=True)
    else:
        st.write("No posters available.")

    st.subheader(f"🌄 Backdrops ({len(backdrops)})")
    if backdrops:
        st.markdown(_gallery_html(backdrops[:6], "w780", "mv-gal-backdrops"), unsafe_allow_html=True)
        if len(backdrops) > 6:
            with st.expander(f"View all backdrops ({min(len(backdrops), 40)})"):
                st.markdown(_gallery_html(backdrops[:40], "w780", "mv-gal-backdrops"), unsafe_allow_html=True)
    else:
        st.write("No backdrops available.")

    st.caption("Click an image to open it in full size.")


def render_movie_cards(items, key_prefix, current_movie_id, per_row=4):
    """Poster cards. Movies that are in the local library get a View Details button."""
    for start in range(0, len(items), per_row):
        columns = st.columns(per_row)

        for offset, item in enumerate(items[start:start + per_row]):
            index = start + offset

            with columns[offset]:
                poster = tmdb_service.image_url(item.get("poster_path"), "w342")

                if poster:
                    st.image(poster, use_container_width=True)
                else:
                    st.markdown("🎬 *No poster*")

                st.markdown(f"**{item['title']}**")

                bits = []
                if item.get("year"):
                    bits.append(f"📅 {item['year']}")
                if item.get("vote_average"):
                    bits.append(f"⭐ {item['vote_average']:.1f}")
                if bits:
                    st.caption(" • ".join(bits))

                if item.get("is_current"):
                    st.caption("▶️ You are viewing this movie")
                    continue

                local_id = find_local_movie_id(item["id"], item["title"], item.get("year"))

                if local_id is not None and local_id != current_movie_id:
                    st.button(
                        "View Details",
                        key=f"{key_prefix}_{item['id']}_{current_movie_id}_{index}",
                        on_click=open_details,
                        args=(local_id,)
                    )
                else:
                    st.markdown(
                        f"[View on TMDB ↗](https://www.themoviedb.org/movie/{int(item['id'])})"
                    )


def render_related(view, movie_id):
    collection = view["collection"]

    if collection:
        st.subheader(f"🎞️ {collection['name']}")

        if collection["parts"]:
            render_movie_cards(collection["parts"], "collection", movie_id)
        else:
            st.info("The other movies in this collection could not be loaded.")

    st.subheader("🍿 Similar & recommended movies")

    if view["related"]:
        render_movie_cards(view["related"][:12], "tmdbsimilar", movie_id)

        if len(view["related"]) > 12:
            with st.expander(f"Show more ({len(view['related']) - 12})"):
                render_movie_cards(view["related"][12:], "tmdbsimilarmore", movie_id)

        st.caption(
            "Movies that are not in your MovieVerse library link to their TMDB page."
        )
    else:
        st.info("TMDB did not return similar movies for this title.")


def render_movie_extended(view, movie_id):
    """The tabbed body of the details page."""
    (
        tab_overview,
        tab_cast,
        tab_production,
        tab_videos,
        tab_gallery,
        tab_related,
    ) = st.tabs([
        "📖 Overview",
        "🎭 Cast & Crew",
        "🏢 Production",
        "🎞️ Videos",
        "🖼️ Gallery",
        "🍿 Similar & Collection",
    ])

    unavailable = view["source"] == "csv"

    with tab_overview:
        render_overview(view)

    with tab_cast:
        if unavailable:
            st.info("Cast and crew come from TMDB, which is unavailable right now.")
        else:
            render_cast_crew(view)

    with tab_production:
        if unavailable:
            st.info("Production details come from TMDB, which is unavailable right now.")
        else:
            render_production(view)

    with tab_videos:
        if unavailable:
            st.info("Trailers come from TMDB, which is unavailable right now.")
        else:
            render_videos(view)

    with tab_gallery:
        if unavailable:
            st.info("Images come from TMDB, which is unavailable right now.")
        else:
            render_gallery(view)

    with tab_related:
        if unavailable:
            st.info("Similar movies and collections come from TMDB, which is unavailable right now.")
        else:
            render_related(view, movie_id)


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

    /* ---------- Movie details page ---------- */
    .mv-hero {
        position: relative;
        border-radius: 18px;
        overflow: hidden;
        margin: 4px 0 14px 0;
        color: #fff;
        background: #111827 center top / cover no-repeat;
    }
    .mv-hero-inner {
        display: flex;
        gap: 28px;
        align-items: flex-end;
        min-height: 400px;
        padding: 36px 32px;
        background: linear-gradient(90deg, rgba(10,14,25,0.96) 0%,
                    rgba(10,14,25,0.80) 45%, rgba(10,14,25,0.40) 100%);
    }
    .mv-hero-poster {
        width: 230px;
        min-width: 230px;
        aspect-ratio: 2 / 3;
        object-fit: cover;
        border-radius: 12px;
        box-shadow: 0 14px 40px rgba(0,0,0,0.65);
        background: #1f2937;
    }
    .mv-hero-text { flex: 1; min-width: 0; }
    .mv-hero .mv-title { font-size: 2.3rem; font-weight: 800; line-height: 1.12; margin: 0 0 4px 0; padding: 0; color: #fff; }
    .mv-year { font-weight: 400; opacity: 0.75; }
    .mv-orig { font-size: 0.95rem; opacity: 0.75; margin-bottom: 6px; }
    .mv-tagline { font-style: italic; font-size: 1.05rem; opacity: 0.9; margin: 6px 0 10px 0; }
    .mv-meta { font-size: 0.95rem; opacity: 0.92; margin-bottom: 8px; }
    .mv-dot { opacity: 0.6; }
    .mv-cert {
        border: 1px solid rgba(255,255,255,0.7);
        border-radius: 5px;
        padding: 0 7px;
        font-weight: 600;
    }
    .mv-chips { margin: 6px 0 12px 0; }
    .mv-chip {
        display: inline-block;
        padding: 3px 13px;
        margin: 3px 6px 3px 0;
        border-radius: 999px;
        background: rgba(255,255,255,0.16);
        font-size: 0.85rem;
    }
    .mv-chip-body {
        background: rgba(128,128,128,0.18);
        color: inherit;
    }
    .mv-score { font-size: 1.05rem; }

    .mv-na { opacity: 0.55; font-weight: 400; }

    .mv-facts {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
        gap: 12px;
        margin: 10px 0 16px 0;
    }
    .mv-fact {
        background: rgba(128,128,128,0.10);
        border: 1px solid rgba(128,128,128,0.22);
        border-radius: 12px;
        padding: 12px 14px;
    }
    .mv-fact-label {
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        opacity: 0.65;
    }
    .mv-fact-value { font-size: 1.02rem; font-weight: 650; margin-top: 3px; word-break: break-word; }

    .mv-people {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(125px, 1fr));
        gap: 14px;
        margin: 8px 0 12px 0;
    }
    .mv-person img, .mv-person .mv-ph {
        width: 100%;
        aspect-ratio: 2 / 3;
        object-fit: cover;
        border-radius: 10px;
        display: block;
    }
    .mv-ph {
        display: flex;
        align-items: center;
        justify-content: center;
        background: linear-gradient(135deg, #374151, #1f2937);
        color: #d1d5db;
        font-size: 1.8rem;
        font-weight: 700;
    }
    .mv-pname { font-weight: 650; font-size: 0.92rem; margin-top: 6px; line-height: 1.2; }
    .mv-prole { font-size: 0.8rem; opacity: 0.65; line-height: 1.25; }

    .mv-crew-card {
        background: rgba(128,128,128,0.08);
        border: 1px solid rgba(128,128,128,0.2);
        border-radius: 12px;
        padding: 12px 16px;
        margin-bottom: 10px;
    }
    .mv-crew-title { font-weight: 700; margin-bottom: 6px; }
    .mv-crew-row {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        padding: 3px 0;
        border-top: 1px solid rgba(128,128,128,0.15);
        font-size: 0.93rem;
    }
    .mv-crew-job { opacity: 0.6; font-size: 0.82rem; text-align: right; }

    .mv-companies {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
        gap: 12px;
        margin-bottom: 12px;
    }
    .mv-co {
        background: rgba(128,128,128,0.08);
        border: 1px solid rgba(128,128,128,0.2);
        border-radius: 12px;
        padding: 12px;
        text-align: center;
    }
    .mv-co-logo {
        background: #f3f4f6;
        border-radius: 8px;
        height: 64px;
        display: flex;
        align-items: center;
        justify-content: center;
        margin-bottom: 8px;
        padding: 6px;
    }
    .mv-co-logo img { max-width: 100%; max-height: 52px; object-fit: contain; }
    .mv-co-ph { font-size: 1.6rem; }

    .mv-gal { display: grid; gap: 12px; margin: 8px 0 12px 0; }
    .mv-gal-posters { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); }
    .mv-gal-backdrops { grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); }
    .mv-gal img { width: 100%; border-radius: 10px; display: block; }

    @media (max-width: 760px) {
        .mv-hero-inner {
            flex-direction: column;
            align-items: center;
            text-align: center;
            padding: 22px 16px;
            min-height: 0;
        }
        .mv-hero-poster { width: 170px; min-width: 170px; }
        .mv-hero .mv-title { font-size: 1.7rem; }
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

    # Keep the sidebar radio separate from programmatic navigation.
    # This is important because "View Details" changes st.session_state.page
    # from a button callback. The old code immediately changed it back to the
    # radio's previous value, making View Details appear not to work.
    if "main_navigation" not in st.session_state:
        st.session_state.main_navigation = st.session_state.page

    def sidebar_navigation_changed():
        st.session_state.page = st.session_state.main_navigation

    st.radio(
        "Navigation",
        pages,
        key="main_navigation",
        on_change=sidebar_navigation_changed
    )

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

            # ----------------------------------------------------
            # TMDB DETAILS (hero, actions and tabbed information)
            # ----------------------------------------------------

            with st.spinner("Loading movie details from TMDB..."):
                tmdb_bundle = load_tmdb_bundle(movie)

            movie_view = tmdb_service.build_view(
                csv_movie_dict(movie),
                tmdb_bundle
            )

            render_hero(movie_view)
            render_tmdb_status(tmdb_bundle, movie_id)

            action1, action2, _action_space = st.columns([1, 1, 2])

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

            render_movie_extended(movie_view, movie_id)

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
                            f"Posted on {review_date(review['created_at'])}"
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
                    f"Posted on {review_date(review['created_at'])}"
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
