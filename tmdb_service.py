"""
TMDB data layer for MovieVerse.

This module contains NO Streamlit code on purpose, so it can be tested on its own.
It is responsible for:

  * reading the TMDB credential safely (never logged, never put in error messages)
  * calling the TMDB API with timeouts, retry on rate limits, and clear error kinds
  * matching a local CSV movie to a TMDB movie (by ID first, then title + year)
  * caching responses (in memory + on disk in ./tmdb_cache) with an offline fallback
  * turning raw TMDB JSON into a clean "view" dictionary the UI can render

Nothing here invents data: whenever TMDB (or the local CSV) does not provide a value,
the view contains None / an empty list and the UI shows "Not available".
"""

from __future__ import annotations

import difflib
import json
import math
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

API_URL = "https://api.themoviedb.org/3"
IMAGE_ROOT = "https://image.tmdb.org/t/p"

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "tmdb_cache"

FRESH_SECONDS = 12 * 60 * 60          # cached details are reused for 12 hours
FAILURE_COOLDOWN_SECONDS = 45         # don't hammer TMDB while it is failing / offline

# One request returns details + credits + videos + images + certifications + similar movies.
APPEND_TO_RESPONSE = (
    "credits,videos,images,release_dates,similar,recommendations,external_ids,keywords"
)

PREFERRED_CERT_REGIONS = ("US", "IN", "GB")

SAFE_IMAGE_PATH = re.compile(r"^/[A-Za-z0-9_\-\.]+$")
YOUTUBE_KEY = re.compile(r"^[A-Za-z0-9_\-]{6,20}$")
IMDB_ID = re.compile(r"^tt\d+$")

RELEASE_TYPES = {
    1: "Premiere",
    2: "Theatrical (limited)",
    3: "Theatrical",
    4: "Digital",
    5: "Physical",
    6: "TV",
}

ERROR_MESSAGES = {
    "no_key": (
        "No TMDB API key was found. Add TMDB_API_KEY to the .env file "
        "or to .streamlit/secrets.toml, then restart MovieVerse."
    ),
    "invalid_key": "TMDB rejected the API key. Check that TMDB_API_KEY is correct.",
    "not_found": "This movie could not be matched to a TMDB entry.",
    "rate_limited": "TMDB rate limit reached. Wait a few seconds and try again.",
    "offline": "TMDB could not be reached. Check your internet connection.",
    "timeout": "TMDB took too long to respond.",
    "server": "TMDB is temporarily unavailable.",
    "error": "TMDB returned an unexpected response.",
}


# ------------------------------------------------------------------
# Credential handling
# ------------------------------------------------------------------

def load_env_credential():
    """
    Return TMDB_API_KEY from the environment or from a local .env file.
    The value is only returned to the caller - it is never printed or stored.
    """
    value = os.environ.get("TMDB_API_KEY", "").strip()
    if value:
        return value

    env_file = BASE_DIR / ".env"
    try:
        text = env_file.read_text(encoding="utf-8-sig")
    except OSError:
        return ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        name, _, val = line.partition("=")
        if name.strip() == "TMDB_API_KEY":
            return val.strip().strip('"').strip("'").strip()

    return ""


# ------------------------------------------------------------------
# Low-level HTTP
# ------------------------------------------------------------------

class TMDBError(Exception):
    """A TMDB request failed. `kind` is one of the keys of ERROR_MESSAGES."""

    def __init__(self, kind, message=None):
        self.kind = kind
        self.message = message or ERROR_MESSAGES.get(kind, ERROR_MESSAGES["error"])
        super().__init__(self.message)


# Indirections so tests can replace the network and the sleep.
_urlopen = urllib.request.urlopen
_sleep = time.sleep


def _build_request(endpoint, params, credential):
    params = dict(params or {})
    headers = {"accept": "application/json", "User-Agent": "MovieVerse/1.0"}

    # v4 Read Access Tokens are long JWTs sent as Bearer tokens; v3 keys are 32 chars.
    if credential.startswith("eyJ") or len(credential) > 100:
        headers["Authorization"] = f"Bearer {credential}"
    else:
        params["api_key"] = credential

    url = f"{API_URL}{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    return urllib.request.Request(url, headers=headers)


def _retry_after_seconds(error):
    try:
        return max(0.0, float(error.headers.get("Retry-After", "1")))
    except (TypeError, ValueError, AttributeError):
        return 1.0


def tmdb_get(endpoint, credential, params=None, retries=2, timeout=10):
    """
    GET a TMDB endpoint and return parsed JSON.
    Raises TMDBError (never leaks the URL or the key in the message).
    """
    if not credential:
        raise TMDBError("no_key")

    request = _build_request(endpoint, params, credential)
    attempt = 0

    while True:
        try:
            with _urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))

        except urllib.error.HTTPError as error:
            code = error.code

            if code in (401, 403):
                raise TMDBError("invalid_key")
            if code == 404:
                raise TMDBError("not_found")

            if code == 429 or code >= 500:
                if attempt < retries:
                    attempt += 1
                    wait = _retry_after_seconds(error) if code == 429 else 1.0
                    _sleep(min(wait, 5.0))
                    continue
                raise TMDBError("rate_limited" if code == 429 else "server")

            raise TMDBError("error", f"TMDB returned HTTP {code}.")

        except urllib.error.URLError as error:
            if isinstance(getattr(error, "reason", None), (socket.timeout, TimeoutError)):
                raise TMDBError("timeout")
            raise TMDBError("offline")

        except (socket.timeout, TimeoutError):
            raise TMDBError("timeout")

        except (ConnectionError, OSError):
            raise TMDBError("offline")

        except ValueError:
            raise TMDBError("error")


# ------------------------------------------------------------------
# Small text / number helpers
# ------------------------------------------------------------------

def norm_title(text):
    return re.sub(r"[\W_]+", " ", str(text or "").lower()).strip()


def title_similarity(a, b):
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def year_of(value):
    match = re.match(r"^\s*(\d{4})", str(value or ""))
    return int(match.group(1)) if match else None


def image_url(path, size="w500"):
    """Return a TMDB image URL, or None when the path is missing or looks unsafe."""
    if not path or not isinstance(path, str) or not SAFE_IMAGE_PATH.match(path):
        return None
    return f"{IMAGE_ROOT}/{size}{path}"


def format_date(value):
    """'2026-07-29' -> 'July 29, 2026' (None when missing/invalid)."""
    try:
        d = datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    return f"{d:%B} {d.day}, {d.year}"


def format_runtime(minutes):
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return None
    if minutes <= 0:
        return None
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    return f"{hours}h" if hours else f"{mins}m"


def format_money(value):
    """TMDB uses 0 for 'unknown', so 0 is reported as not available (None)."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return f"${value:,}" if value > 0 else None


def _to_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _to_int(value):
    number = _to_float(value)
    return None if number is None else int(number)


def _clean_text(value):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    return text or None


# ------------------------------------------------------------------
# Matching a local CSV movie to TMDB
# ------------------------------------------------------------------

def is_same_movie(csv_title, csv_year, data):
    """
    Check that a TMDB movie fetched by ID really is the CSV movie.
    Protects against a CSV ID that is not a TMDB ID and happens to belong to another film.
    """
    title = _clean_text(csv_title)
    if not title or norm_title(title) in ("", "unknown title"):
        return True  # nothing to verify against

    similarity = max(
        title_similarity(title, data.get("title")),
        title_similarity(title, data.get("original_title")),
    )

    tmdb_year = year_of(data.get("release_date"))
    csv_year = _to_int(csv_year)

    if csv_year is not None and tmdb_year is not None:
        if abs(csv_year - tmdb_year) > 1:
            return False
        return similarity >= 0.6

    return similarity >= 0.85


def search_tmdb_id(title, release_year, credential):
    """Search TMDB by title (+ year) and return the best matching TMDB ID."""
    title = _clean_text(title)
    if not title:
        raise TMDBError("not_found")

    year = _to_int(release_year)
    base = {"query": title, "language": "en-US", "include_adult": "false", "page": 1}

    results = []
    if year:
        results = tmdb_get("/search/movie", credential, {**base, "year": year}).get("results") or []
    if not results:
        results = tmdb_get("/search/movie", credential, base).get("results") or []
    if not results:
        raise TMDBError("not_found")

    best_id, best_score, best_similarity = None, -1.0, 0.0

    for item in results[:20]:
        if not isinstance(item, dict) or not item.get("id"):
            continue

        similarity = max(
            title_similarity(title, item.get("title")),
            title_similarity(title, item.get("original_title")),
        )
        item_year = year_of(item.get("release_date"))

        year_bonus = 0.0
        if year and item_year == year:
            year_bonus = 0.3
        elif year and item_year and abs(item_year - year) == 1:
            year_bonus = 0.1

        popularity = _to_float(item.get("popularity")) or 0.0
        score = similarity + year_bonus + min(math.log10(1 + popularity) / 20, 0.1)

        if score > best_score:
            best_id, best_score, best_similarity = int(item["id"]), score, similarity

    if best_id is None or best_similarity < 0.6:
        raise TMDBError("not_found")

    return best_id


def fetch_details_by_id(tmdb_id, credential):
    return tmdb_get(
        f"/movie/{int(tmdb_id)}",
        credential,
        {
            "append_to_response": APPEND_TO_RESPONSE,
            "language": "en-US",
            "include_image_language": "en,null",
            "include_video_language": "en,null",
        },
    )


def _supplement(data, credential):
    """
    Best-effort extras. Failures here never break the page:
      * franchise/collection parts
      * videos/images in the movie's original language when the English-only request was empty
    """
    collection = None
    info = data.get("belongs_to_collection")
    if isinstance(info, dict) and info.get("id"):
        try:
            collection = tmdb_get(
                f"/collection/{int(info['id'])}", credential, {"language": "en-US"}
            )
        except TMDBError:
            collection = None

    original = _clean_text(data.get("original_language"))
    has_videos = bool((data.get("videos") or {}).get("results"))
    has_images = bool(
        (data.get("images") or {}).get("posters") or (data.get("images") or {}).get("backdrops")
    )

    if original and original != "en" and not (has_videos and has_images):
        try:
            extra = tmdb_get(
                f"/movie/{int(data['id'])}",
                credential,
                {
                    "append_to_response": "videos,images",
                    "language": "en-US",
                    "include_video_language": f"{original},en,null",
                    "include_image_language": f"{original},en,null",
                },
            )
            if not has_videos and (extra.get("videos") or {}).get("results"):
                data["videos"] = extra["videos"]
            if not has_images and extra.get("images"):
                data["images"] = extra["images"]
        except TMDBError:
            pass

    return collection


def _fetch_payload(movie_id, title, release_year, credential):
    data = None
    matched_by = None

    try:
        candidate = fetch_details_by_id(movie_id, credential)
        if candidate.get("id") and is_same_movie(title, release_year, candidate):
            data, matched_by = candidate, "TMDB ID"
    except TMDBError as error:
        if error.kind != "not_found":
            raise

    if data is None:
        tmdb_id = search_tmdb_id(title, release_year, credential)
        data = fetch_details_by_id(tmdb_id, credential)
        matched_by = "title and year search"

    collection = _supplement(data, credential)

    return {
        "stored_at": time.time(),
        "tmdb_id": int(data["id"]),
        "matched_by": matched_by,
        "data": data,
        "collection": collection,
    }


# ------------------------------------------------------------------
# Caching (memory + disk) and the public entry point
# ------------------------------------------------------------------

_MEMORY_CACHE = {}
_FAILURES = {}


def _cache_file(movie_id):
    return CACHE_DIR / f"movie_{int(movie_id)}.json"


def _valid_payload(payload):
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("data"), dict)
        and payload["data"].get("id")
        and isinstance(payload.get("stored_at"), (int, float))
    )


def _read_cache(movie_id):
    payload = _MEMORY_CACHE.get(movie_id)
    if _valid_payload(payload):
        return payload

    try:
        payload = json.loads(_cache_file(movie_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    if _valid_payload(payload):
        _MEMORY_CACHE[movie_id] = payload
        return payload
    return None


def _write_cache(movie_id, payload):
    _MEMORY_CACHE[movie_id] = payload
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        temp = _cache_file(movie_id).with_suffix(".tmp")
        temp.write_text(json.dumps(payload), encoding="utf-8")
        temp.replace(_cache_file(movie_id))
    except OSError:
        pass  # caching is an optimisation, never a requirement


def forget_failure(movie_id):
    """Used by the 'Retry TMDB' button: allow an immediate new request."""
    try:
        _FAILURES.pop(int(movie_id), None)
    except (TypeError, ValueError):
        pass


def _result(status, payload=None, reason=None, cached=False):
    payload = payload or {}
    return {
        "status": status,              # "ok" | "stale" | "unavailable"
        "reason": reason,              # error kind when status != "ok"
        "message": ERROR_MESSAGES.get(reason, "") if reason else "",
        "data": payload.get("data"),
        "collection": payload.get("collection"),
        "tmdb_id": payload.get("tmdb_id"),
        "matched_by": payload.get("matched_by"),
        "stored_at": payload.get("stored_at"),
        "cached": cached,
    }


def get_movie_details(movie_id, title, release_year, credential):
    """
    Return a result dict (see _result). Never raises.

      ok          - fresh data (live or from a recent cache)
      stale       - TMDB failed, an older saved copy is shown instead
      unavailable - nothing from TMDB; the UI falls back to local CSV data
    """
    try:
        movie_id = int(movie_id)
    except (TypeError, ValueError):
        return _result("unavailable", reason="not_found")

    cached = _read_cache(movie_id)
    now = time.time()

    if cached and now - cached["stored_at"] < FRESH_SECONDS:
        return _result("ok", cached, cached=True)

    def fallback(kind):
        if cached:
            return _result("stale", cached, reason=kind, cached=True)
        return _result("unavailable", reason=kind)

    if not credential:
        return fallback("no_key")

    failure = _FAILURES.get(movie_id)
    if failure and now - failure[0] < FAILURE_COOLDOWN_SECONDS:
        return fallback(failure[1])

    try:
        payload = _fetch_payload(movie_id, title, release_year, credential)
    except TMDBError as error:
        _FAILURES[movie_id] = (now, error.kind)
        return fallback(error.kind)
    except Exception:
        _FAILURES[movie_id] = (now, "error")
        return fallback("error")

    _FAILURES.pop(movie_id, None)
    _write_cache(movie_id, payload)
    return _result("ok", payload)


# ------------------------------------------------------------------
# Turning TMDB JSON into display data
# ------------------------------------------------------------------

def certification_info(release_dates):
    """Return (certification, region, {region: certification}) from TMDB release_dates."""
    by_region = {}
    for entry in (release_dates or {}).get("results") or []:
        region = entry.get("iso_3166_1")
        for item in entry.get("release_dates") or []:
            cert = _clean_text(item.get("certification"))
            if cert and region and region not in by_region:
                by_region[region] = cert

    for region in PREFERRED_CERT_REGIONS:
        if region in by_region:
            return by_region[region], region, by_region

    if by_region:
        region = next(iter(by_region))
        return by_region[region], region, by_region

    return None, None, {}


def release_date_rows(release_dates):
    rows = []
    for entry in (release_dates or {}).get("results") or []:
        region = entry.get("iso_3166_1") or "??"
        for item in entry.get("release_dates") or []:
            raw = str(item.get("release_date") or "")
            rows.append(
                {
                    "Country": region,
                    "Type": RELEASE_TYPES.get(item.get("type"), "Other"),
                    "Date": format_date(raw) or "—",
                    "Certification": _clean_text(item.get("certification")) or "—",
                    "Note": _clean_text(item.get("note")) or "",
                    "_sort": raw,
                }
            )
    rows.sort(key=lambda r: (r["Country"] != "US", r["Country"], r["_sort"]))
    for row in rows:
        row.pop("_sort", None)
    return rows


def merge_cast(cast):
    """Order by billing, merge repeated people (one person, several roles)."""
    people, order = {}, []

    ranked = sorted(
        [p for p in (cast or []) if isinstance(p, dict)],
        key=lambda p: p.get("order") if isinstance(p.get("order"), int) else 10**6,
    )

    for person in ranked:
        name = _clean_text(person.get("name")) or _clean_text(person.get("original_name"))
        if not name:
            continue

        key = person.get("id") if person.get("id") is not None else name
        character = _clean_text(person.get("character"))

        if key in people:
            if character and character not in people[key]["characters"]:
                people[key]["characters"].append(character)
            continue

        people[key] = {
            "id": person.get("id"),
            "name": name,
            "characters": [character] if character else [],
            "profile_path": person.get("profile_path"),
        }
        order.append(key)

    return [people[key] for key in order]


KEY_CREW_GROUPS = [
    "Directors",
    "Writers",
    "Producers",
    "Executive Producers",
    "Cinematographers",
    "Editors",
    "Music Composers",
    "Production Designers",
]

DEPARTMENT_ORDER = [
    "Directing", "Production", "Art", "Costume & Make-Up", "Camera",
    "Lighting", "Sound", "Visual Effects", "Editing", "Crew", "Actors",
]


def _key_group_for(job, department):
    job_l = (job or "").lower()
    dept = department or ""

    if job_l == "director":
        return "Directors"
    if dept == "Writing":
        return "Writers"
    if dept == "Production" and "executive producer" in job_l:
        return "Executive Producers"
    if dept == "Production" and "producer" in job_l:
        return "Producers"
    if job_l in ("director of photography", "cinematography", "cinematographer"):
        return "Cinematographers"
    if dept == "Editing" and "editor" in job_l:
        return "Editors"
    if "composer" in job_l or job_l in ("original music composer", "music"):
        return "Music Composers"
    if job_l in ("production design", "production designer"):
        return "Production Designers"
    return None


def group_crew(crew):
    """
    Group the actual TMDB crew credits. Within one group a person appears once,
    with all of their jobs combined (e.g. "Screenplay, Story").
    Returns a list of {"label", "is_key", "people": [{"id","name","jobs","profile_path"}]}.
    """
    groups = {}

    def add(label, is_key, person, job):
        group = groups.setdefault(label, {"label": label, "is_key": is_key, "people": {}})
        name = _clean_text(person.get("name")) or _clean_text(person.get("original_name"))
        if not name:
            return
        key = person.get("id") if person.get("id") is not None else name
        entry = group["people"].setdefault(
            key,
            {"id": person.get("id"), "name": name, "jobs": [], "profile_path": person.get("profile_path")},
        )
        if job and job not in entry["jobs"]:
            entry["jobs"].append(job)

    for person in crew or []:
        if not isinstance(person, dict):
            continue
        job = _clean_text(person.get("job"))
        department = _clean_text(person.get("department")) or "Other"

        key_label = _key_group_for(job, department)
        if key_label:
            add(key_label, True, person, job)
        else:
            add(department, False, person, job)

    ordered = []
    for label in KEY_CREW_GROUPS:
        if label in groups:
            ordered.append(groups.pop(label))

    others = sorted(
        groups.values(),
        key=lambda g: (
            DEPARTMENT_ORDER.index(g["label"]) if g["label"] in DEPARTMENT_ORDER else 99,
            g["label"],
        ),
    )
    ordered.extend(others)

    return [
        {
            "label": g["label"],
            "is_key": g["is_key"],
            "people": list(g["people"].values()),
        }
        for g in ordered
        if g["people"]
    ]


def organize_videos(videos):
    """Only YouTube videos with a valid key. Official first, then newest first."""
    items = []
    for video in videos or []:
        if not isinstance(video, dict):
            continue
        key = str(video.get("key") or "")
        if video.get("site") != "YouTube" or not YOUTUBE_KEY.match(key):
            continue
        items.append(
            {
                "key": key,
                "name": _clean_text(video.get("name")) or "Untitled video",
                "type": _clean_text(video.get("type")) or "Video",
                "official": bool(video.get("official")),
                "published": format_date(video.get("published_at")),
                "_raw": str(video.get("published_at") or ""),
                "url": f"https://www.youtube.com/watch?v={key}",
            }
        )

    items.sort(key=lambda v: v["_raw"], reverse=True)
    items.sort(key=lambda v: not v["official"])  # stable: official first
    for item in items:
        item.pop("_raw", None)

    return {
        "trailers": [v for v in items if v["type"] == "Trailer"],
        "teasers": [v for v in items if v["type"] == "Teaser"],
        "others": [v for v in items if v["type"] not in ("Trailer", "Teaser")],
    }


def organize_images(images):
    def clean(entries):
        seen, out = set(), []
        for img in entries or []:
            path = img.get("file_path") if isinstance(img, dict) else None
            if not path or path in seen or not SAFE_IMAGE_PATH.match(path):
                continue
            seen.add(path)
            out.append(
                {
                    "path": path,
                    "width": img.get("width"),
                    "height": img.get("height"),
                    "votes": _to_float(img.get("vote_average")) or 0.0,
                    "vote_count": _to_int(img.get("vote_count")) or 0,
                }
            )
        out.sort(key=lambda i: (i["votes"], i["vote_count"]), reverse=True)
        return out

    images = images or {}
    return {
        "posters": clean(images.get("posters")),
        "backdrops": clean(images.get("backdrops")),
    }


def related_movies(data):
    """Recommended + similar movies from TMDB, de-duplicated."""
    own_id = data.get("id")
    seen, out = {own_id}, []

    for source, label in (("recommendations", "Recommended"), ("similar", "Similar")):
        for item in (data.get(source) or {}).get("results") or []:
            if not isinstance(item, dict) or not item.get("id") or item["id"] in seen:
                continue
            title = _clean_text(item.get("title"))
            if not title:
                continue
            seen.add(item["id"])
            out.append(
                {
                    "id": item["id"],
                    "title": title,
                    "year": year_of(item.get("release_date")),
                    "vote_average": _to_float(item.get("vote_average")),
                    "poster_path": item.get("poster_path"),
                    "source": label,
                }
            )
    return out[:24]


def collection_parts(collection, own_id):
    parts = []
    for item in (collection or {}).get("parts") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        title = _clean_text(item.get("title"))
        if not title:
            continue
        parts.append(
            {
                "id": item["id"],
                "title": title,
                "year": year_of(item.get("release_date")),
                "release_date": str(item.get("release_date") or ""),
                "vote_average": _to_float(item.get("vote_average")),
                "poster_path": item.get("poster_path"),
                "is_current": item["id"] == own_id,
            }
        )
    parts.sort(key=lambda p: (p["release_date"] == "", p["release_date"]))
    return parts


def build_view(csv, bundle):
    """
    Merge TMDB data (when available) with the local CSV row into one dictionary.
    `csv` is a plain dict with the CSV columns; `bundle` is the result of get_movie_details.
    Every value TMDB doesn't provide falls back to the CSV or stays None.
    """
    csv = csv or {}
    data = (bundle or {}).get("data") or {}
    has_tmdb = bool(data)

    csv_year = _to_int(csv.get("release_year"))
    release_raw = _clean_text(data.get("release_date")) or _clean_text(csv.get("release_date"))
    year = year_of(release_raw) or csv_year

    certification, cert_region, all_certs = certification_info(data.get("release_dates"))

    spoken = [
        _clean_text(l.get("english_name")) or _clean_text(l.get("name"))
        for l in data.get("spoken_languages") or []
        if isinstance(l, dict)
    ]
    spoken = [s for s in spoken if s]

    original_code = _clean_text(data.get("original_language")) or _clean_text(csv.get("language"))
    original_language = None
    if original_code:
        original_language = original_code.upper()
        for lang in data.get("spoken_languages") or []:
            if isinstance(lang, dict) and lang.get("iso_639_1") == original_code:
                original_language = _clean_text(lang.get("english_name")) or original_language
                break

    homepage = _clean_text(data.get("homepage"))
    if homepage and urllib.parse.urlparse(homepage).scheme not in ("http", "https"):
        homepage = None

    imdb_id = _clean_text((data.get("external_ids") or {}).get("imdb_id")) or _clean_text(data.get("imdb_id"))
    imdb_url = f"https://www.imdb.com/title/{imdb_id}/" if imdb_id and IMDB_ID.match(imdb_id) else None

    tmdb_id = (bundle or {}).get("tmdb_id")

    collection = None
    info = data.get("belongs_to_collection")
    if isinstance(info, dict) and info.get("id"):
        collection = {
            "id": info["id"],
            "name": _clean_text(info.get("name")) or "Collection",
            "poster_path": info.get("poster_path"),
            "parts": collection_parts((bundle or {}).get("collection"), data.get("id")),
        }

    adult = data.get("adult")
    adult_text = "Yes" if adult is True else ("No" if adult is False else None)

    credits = data.get("credits") or {}
    keywords = [
        _clean_text(k.get("name"))
        for k in (data.get("keywords") or {}).get("keywords") or []
        if isinstance(k, dict)
    ]

    return {
        "source": "tmdb" if has_tmdb else "csv",
        "tmdb_id": tmdb_id,
        "tmdb_url": f"https://www.themoviedb.org/movie/{tmdb_id}" if tmdb_id else None,
        "imdb_url": imdb_url,
        "title": _clean_text(data.get("title")) or _clean_text(csv.get("title")) or "Unknown Title",
        "original_title": _clean_text(data.get("original_title")),
        "tagline": _clean_text(data.get("tagline")),
        "overview": _clean_text(data.get("overview")) or _clean_text(csv.get("overview")),
        "release_date_raw": release_raw,
        "release_date_text": format_date(release_raw),
        "year": year,
        "runtime_text": format_runtime(data.get("runtime")),
        "genres": [g.get("name") for g in data.get("genres") or [] if isinstance(g, dict) and g.get("name")],
        "keywords": [k for k in keywords if k][:20],
        "vote_average": _to_float(data.get("vote_average")) if has_tmdb and data.get("vote_count") else _to_float(csv.get("rating")),
        "vote_count": _to_int(data.get("vote_count")) if has_tmdb else None,
        "popularity": _to_float(data.get("popularity")) if has_tmdb else _to_float(csv.get("popularity")),
        "original_language": original_language,
        "spoken_languages": spoken,
        "status": _clean_text(data.get("status")),
        "certification": certification,
        "certification_region": cert_region,
        "all_certifications": all_certs,
        "adult": adult_text,
        "budget_text": format_money(data.get("budget")),
        "revenue_text": format_money(data.get("revenue")),
        "companies": [
            {
                "name": _clean_text(c.get("name")) or "Unknown",
                "logo_path": c.get("logo_path"),
                "country": _clean_text(c.get("origin_country")),
            }
            for c in data.get("production_companies") or []
            if isinstance(c, dict)
        ],
        "countries": [
            _clean_text(c.get("name")) or _clean_text(c.get("iso_3166_1"))
            for c in data.get("production_countries") or []
            if isinstance(c, dict)
        ],
        "homepage": homepage,
        "collection": collection,
        "poster_path": _clean_text(data.get("poster_path")) or _clean_text(csv.get("poster_path")),
        "backdrop_path": _clean_text(data.get("backdrop_path")) or _clean_text(csv.get("backdrop_path")),
        "cast": merge_cast(credits.get("cast")),
        "crew_groups": group_crew(credits.get("crew")),
        "crew_total": len([c for c in credits.get("crew") or [] if isinstance(c, dict)]),
        "videos": organize_videos((data.get("videos") or {}).get("results")),
        "images": organize_images(data.get("images")),
        "related": related_movies(data) if has_tmdb else [],
        "release_rows": release_date_rows(data.get("release_dates")),
    }
