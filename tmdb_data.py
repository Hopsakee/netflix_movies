"""Thin wrapper around TMDB's discover API.

All calls are bounded (timeout, page cap), cached where the response is static (genre lists),
and fail-loud on missing credentials.
"""
import os
from functools import lru_cache
from typing import Optional

import pandas as pd
import requests
from dotenv import load_dotenv

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
        cols = ["title", "vote_average", "genre_ids", "release_date", "description"]
        df = df[cols].copy()
        df["genres"] = df["genre_ids"].apply(lambda ids: [genre_dict.get(gid, "Unknown") for gid in ids])
        df = df.drop(columns=["genre_ids"])
        df["vote_average"] = df["vote_average"].round(1)
        df["release_date"] = pd.to_datetime(df["release_date"], errors="coerce").dt.year
        df = df.sort_values("vote_average", ascending=False)[:TOP_RESULTS]
        all_genres = set()
        for gs in df["genres"]:
            all_genres.update(gs)

    no_genres = {genre_dict.get(gid, "Unknown") for gid in no_genre_ids} if no_genre_ids else set()
    mv_filter = {"min_rating": min_vote, "incl_genres": all_genres,
                 "excl_genres": no_genres, "filt_genres": genre_ids}
    return df, mv_filter


def get_series(genre_ids: Optional[list[int]] = None, no_genre_ids: Optional[list[int]] = None,
               min_vote: Optional[float] = None, provider: int = DEFAULT_PROVIDER):
    "Fetch series filtered by genre, minimum vote average, and watch provider."
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
        cols = ["name", "vote_average", "genre_ids", "first_air_date", "description"]
        df = df[cols].copy()
        df["genres"] = df["genre_ids"].apply(lambda ids: [genre_dict.get(gid, "Unknown") for gid in ids])
        df = df.drop(columns=["genre_ids"])
        df["vote_average"] = df["vote_average"].round(1)
        df["release_date"] = pd.to_datetime(df["first_air_date"], errors="coerce").dt.year
        df["title"] = df["name"]
        df = df.sort_values("vote_average", ascending=False)[:TOP_RESULTS]
        all_genres = set()
        for gs in df["genres"]:
            all_genres.update(gs)

    no_genres = {genre_dict.get(gid, "Unknown") for gid in no_genre_ids} if no_genre_ids else set()
    mv_filter = {"min_rating": min_vote, "incl_genres": all_genres,
                 "excl_genres": no_genres, "filt_genres": genre_ids}
    return df, mv_filter


if __name__ == "__main__":
    df, _ = get_movies()
    print(df.head())
