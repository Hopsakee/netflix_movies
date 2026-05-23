"""Thin wrapper around TMDB's discover API.

All calls are bounded (timeout, page cap), cached where the response is static (genre lists),
and fail-loud on missing credentials.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from threading import Lock
from typing import Optional

import pandas as pd
import requests
from dotenv import load_dotenv

from imdb_data import get_ratings_by_id

load_dotenv()

TMDB_API_KEY = os.getenv("TMDB_API_KEY")
if not TMDB_API_KEY:
    raise RuntimeError("TMDB_API_KEY is not set. Refusing to start without an API key.")

TMDB_BASE = "https://api.themoviedb.org/3"
HTTP_TIMEOUT = (5, 30)   # (connect, read) — single slow upstream call must not hang a worker indefinitely.
MAX_PAGES = 5            # cap pagination so a single request can't trigger an unbounded TMDB crawl.
DEFAULT_PROVIDER = 8     # Netflix on TMDB's watch-providers map.
WATCH_REGION = "NL"
TOP_RESULTS = 30
RESULT_TTL_SEC = 1800    # 30 minutes — Netflix's top-30 doesn't change minute-to-minute.


# (cache_key) -> (expiry_timestamp, (df, mv_filter))
_result_cache: dict = {}
_result_cache_lock = Lock()


def _cache_get(key):
    with _result_cache_lock:
        hit = _result_cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        if hit:
            _result_cache.pop(key, None)
    return None


def _cache_put(key, value):
    with _result_cache_lock:
        _result_cache[key] = (time.time() + RESULT_TTL_SEC, value)


def _headers() -> dict:
    return {"accept": "application/json", "Authorization": f"Bearer {TMDB_API_KEY}"}


@lru_cache(maxsize=1)
def get_genres_movies() -> dict:
    "Fetch the TMDB movie-genre dictionary. Cached for the lifetime of the process."
    r = requests.get(f"{TMDB_BASE}/genre/movie/list", params={"language": "en"},
                     headers=_headers(), timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return {g["id"]: g["name"] for g in r.json().get("genres", [])}


@lru_cache(maxsize=1)
def get_genres_series() -> dict:
    "Fetch the TMDB tv-genre dictionary. Cached for the lifetime of the process."
    r = requests.get(f"{TMDB_BASE}/genre/tv/list", params={"language": "en"},
                     headers=_headers(), timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return {g["id"]: g["name"] for g in r.json().get("genres", [])}


def _discover_params(genre_ids: Optional[list[int]], no_genre_ids: Optional[list[int]],
                     min_vote: Optional[float], provider: int) -> dict:
    "Build the TMDB /discover query-string parameter dict."
    params: dict = {
        "include_adult": "false",
        "include_video": "false",
        "sort_by": "vote_average.desc",
        "vote_count.gte": 1000,
        "watch_region": WATCH_REGION,
        "with_watch_providers": provider,
    }
    if genre_ids:
        params["with_genres"] = ",".join(str(int(g)) for g in genre_ids)
    if no_genre_ids:
        params["without_genres"] = ",".join(str(int(g)) for g in no_genre_ids)
    if min_vote is not None:
        params["vote_average.gte"] = min_vote
    return params


@lru_cache(maxsize=4096)
def _get_imdb_id(tmdb_id: int, kind: str) -> Optional[str]:
    "Fetch the IMDb id for a TMDB id. kind is 'movie' or 'tv'. Cached in-process — TMDB→IMDb mapping is permanent."
    url = f"{TMDB_BASE}/{kind}/{tmdb_id}/external_ids"
    try:
        r = requests.get(url, headers=_headers(), timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json().get("imdb_id") or None
    except Exception:
        return None


def _enrich_with_imdb(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Add 'imdb_rating' and 'metascore' columns by looking up each row's
    IMDb id (via TMDB external_ids) and then its ratings (via imdbapi.dev).
    Lookups run in parallel; missing values become None."""
    if len(df) == 0:
        df["imdb_rating"] = None
        df["metascore"] = None
        return df

    tmdb_ids = df["tmdb_id"].tolist()
    # TMDB tolerates parallelism; imdbapi.dev rate-limits hard (Cloudflare),
    # so keep its pool small and rely on retry-with-backoff in get_ratings_by_id.
    with ThreadPoolExecutor(max_workers=10) as pool:
        imdb_ids = list(pool.map(lambda i: _get_imdb_id(i, kind), tmdb_ids))
    with ThreadPoolExecutor(max_workers=3) as pool:
        ratings = list(pool.map(
            lambda tt: get_ratings_by_id(tt) if tt else
                       {"user_rating": None, "metascore": None},
            imdb_ids))

    df = df.copy()
    df["imdb_rating"] = [r.get("user_rating") for r in ratings]
    df["metascore"]   = [r.get("metascore")   for r in ratings]
    return df


def _fetch_pages(path: str, params: dict) -> list:
    "Fetch up to MAX_PAGES of TMDB results for the given /discover path."
    results: list = []
    total_pages = 1
    page = 1
    while page <= min(total_pages, MAX_PAGES):
        page_params = {**params, "page": page}
        r = requests.get(f"{TMDB_BASE}{path}", params=page_params,
                         headers=_headers(), timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("results", []))
        total_pages = data.get("total_pages", 1)
        page += 1
    return results


def get_movies(genre_ids: Optional[list[int]] = None, no_genre_ids: Optional[list[int]] = None,
               min_vote: Optional[float] = None, provider: int = DEFAULT_PROVIDER):
    "Fetch movies filtered by genre, minimum vote average, and watch provider."
    cache_key = ("movies", tuple(sorted(genre_ids or [])), tuple(sorted(no_genre_ids or [])),
                 min_vote, provider)
    cached = _cache_get(cache_key)
    if cached is not None:
        df_cached, mv_filter = cached
        return df_cached.copy(), mv_filter

    genre_dict = get_genres_movies()
    params = _discover_params(genre_ids, no_genre_ids, min_vote, provider)
    all_results = _fetch_pages("/discover/movie", params)

    df = pd.DataFrame(all_results)
    if len(df) == 0:
        all_genres: set = set()
        df = pd.DataFrame(columns=["title", "vote_average", "release_date", "genres", "description"])
    else:
        if "overview" in df.columns:
            df = df.rename(columns={"overview": "description"})
        df = df.rename(columns={"id": "tmdb_id"})
        cols = ["tmdb_id", "title", "vote_average", "genre_ids", "release_date", "description"]
        df = df[cols].copy()
        df["genres"] = df["genre_ids"].apply(lambda ids: [genre_dict.get(gid, "Unknown") for gid in ids])
        df = df.drop(columns=["genre_ids"])
        df["vote_average"] = df["vote_average"].round(1)
        df["release_date"] = pd.to_datetime(df["release_date"], errors="coerce").dt.year
        df = df.sort_values("vote_average", ascending=False)[:TOP_RESULTS]
        df = _enrich_with_imdb(df, kind="movie")
        df = df.sort_values("imdb_rating", ascending=False, na_position="last")
        all_genres = set()
        for gs in df["genres"]:
            all_genres.update(gs)

    no_genres = {genre_dict.get(gid, "Unknown") for gid in no_genre_ids} if no_genre_ids else set()
    mv_filter = {"min_rating": min_vote, "incl_genres": all_genres,
                 "excl_genres": no_genres, "filt_genres": genre_ids}
    _cache_put(cache_key, (df.copy(), mv_filter))
    return df, mv_filter


def get_series(genre_ids: Optional[list[int]] = None, no_genre_ids: Optional[list[int]] = None,
               min_vote: Optional[float] = None, provider: int = DEFAULT_PROVIDER):
    "Fetch series filtered by genre, minimum vote average, and watch provider."
    cache_key = ("series", tuple(sorted(genre_ids or [])), tuple(sorted(no_genre_ids or [])),
                 min_vote, provider)
    cached = _cache_get(cache_key)
    if cached is not None:
        df_cached, mv_filter = cached
        return df_cached.copy(), mv_filter

    genre_dict = get_genres_series()
    params = _discover_params(genre_ids, no_genre_ids, min_vote, provider)
    all_results = _fetch_pages("/discover/tv", params)

    df = pd.DataFrame(all_results)
    if len(df) == 0:
        all_genres: set = set()
        df = pd.DataFrame(columns=["name", "vote_average", "first_air_date", "genres", "description"])
    else:
        if "overview" in df.columns:
            df = df.rename(columns={"overview": "description"})
        df = df.rename(columns={"id": "tmdb_id"})
        cols = ["tmdb_id", "name", "vote_average", "genre_ids", "first_air_date", "description"]
        df = df[cols].copy()
        df["genres"] = df["genre_ids"].apply(lambda ids: [genre_dict.get(gid, "Unknown") for gid in ids])
        df["vote_average"] = df["vote_average"].round(1)
        df["release_date"] = pd.to_datetime(df["first_air_date"], errors="coerce").dt.year
        df["title"] = df["name"]
        df = df.drop(columns=["genre_ids", "first_air_date", "name"])
        df = df.sort_values("vote_average", ascending=False)[:TOP_RESULTS]
        df = _enrich_with_imdb(df, kind="tv")
        df = df.sort_values("imdb_rating", ascending=False, na_position="last")
        all_genres = set()
        for gs in df["genres"]:
            all_genres.update(gs)

    no_genres = {genre_dict.get(gid, "Unknown") for gid in no_genre_ids} if no_genre_ids else set()
    mv_filter = {"min_rating": min_vote, "incl_genres": all_genres,
                 "excl_genres": no_genres, "filt_genres": genre_ids}
    _cache_put(cache_key, (df.copy(), mv_filter))
    return df, mv_filter


if __name__ == "__main__":
    df, _ = get_movies()
    print(df.head())
