"""
A tiny 'API' for IMDb, built using the technique from
https://youtu.be/rOaaibIFf8o (find the internal endpoints a site already
uses and call them directly).

Two endpoints are used:

1. https://v3.sg.media-imdb.com/suggestion/{first_letter}/{query}.json
   IMDb's own auto-complete endpoint. No auth, no WAF, returns the IMDb
   title id (tt...) for a movie name.

2. https://api.imdbapi.dev/titles/{tt_id}
   A public mirror of IMDb's GraphQL data. Returns aggregateRating
   (the IMDb user rating) and metacritic.score (the Metascore).
   Used because www.imdb.com/title/... is protected by AWS WAF and
   cannot be fetched with a plain HTTP client.

Usage:
    from imdb_data import get_movie_ratings
    get_movie_ratings("Inception")
    # -> {'imdb_id': 'tt1375666', 'title': 'Inception', 'year': 2010,
    #     'user_rating': 8.8, 'metascore': 74}
"""

from __future__ import annotations

import logging
import re
import time
import requests
from functools import lru_cache
from typing import Optional

log = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

SEARCH_URL = "https://v3.sg.media-imdb.com/suggestion/{letter}/{slug}.json"
TITLE_URL  = "https://api.imdbapi.dev/titles/{tt_id}"


def _slugify(name: str) -> str:
    "IMDb's suggestion endpoint expects a lowercased, _-separated slug."
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "_"


def search_imdb(name: str, only_movies: bool = True, limit: int = 5) -> list[dict]:
    """Search IMDb by title name. Returns a list of {id, title, year, kind}."""
    slug = _slugify(name)
    url = SEARCH_URL.format(letter=slug[0], slug=slug)
    r = requests.get(url, headers={"User-Agent": _UA}, timeout=10)
    r.raise_for_status()
    out = []
    for hit in r.json().get("d", []):
        tt = hit.get("id", "")
        if not tt.startswith("tt"):
            continue                                  # skip name/celeb hits
        kind = hit.get("qid", "")
        if only_movies and kind not in {"movie", "tvMovie"}:
            continue
        out.append({"id": tt, "title": hit.get("l"),
                    "year": hit.get("y"), "kind": kind})
        if len(out) >= limit:
            break
    return out


def get_title_details(tt_id: str, max_retries: int = 2) -> dict:
    """Fetch full title details (incl. user rating + Metascore) for an IMDb id.

    Retries with exponential backoff on HTTP 429 (rate limit) — imdbapi.dev
    is fronted by Cloudflare and rejects concurrent bursts. Kept short
    (2 retries, 0.3s base) on purpose: this runs synchronously inside an
    HTTP request for up to 30 titles at a time (see tmdb_data._enrich_with_imdb),
    so a slow/rate-limited upstream must fail fast rather than stall every
    genre-filter click for 10+ seconds.
    """
    url = TITLE_URL.format(tt_id=tt_id)
    delay = 0.3
    for attempt in range(max_retries):
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=10)
        if r.status_code == 429:
            log.warning("imdbapi.dev rate-limited tt_id=%s (attempt %d/%d)", tt_id, attempt + 1, max_retries)
            time.sleep(delay)
            delay *= 2
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()                     # final 429 → raise
    return r.json()                          # pragma: no cover


@lru_cache(maxsize=2048)
def get_ratings_by_id(tt_id: str) -> dict:
    """Return just {user_rating, vote_count, metascore} for an IMDb id.

    Values are None when missing/unavailable. Never raises — returns a dict
    of Nones on error, so it can be used safely in bulk lookups. Results
    are cached in-process so repeated table renders are free (a failure is
    cached too, so a genre re-toggle never re-pays a slow/failing lookup).
    """
    try:
        data = get_title_details(tt_id)
    except Exception as e:
        log.warning("imdbapi.dev lookup failed for tt_id=%s: %r", tt_id, e)
        return {"user_rating": None, "vote_count": None, "metascore": None}

    # imdbapi.dev's documented shape nests the rating under "rating"; some responses
    # observed in the wild use "ratingsSummary" instead. Try both rather than assume —
    # this endpoint is unofficial and undocumented, so its shape can drift without notice.
    rating = data.get("rating") or data.get("ratingsSummary") or {}
    meta   = data.get("metacritic") or {}
    out = {
        "user_rating": rating.get("aggregateRating"),
        "vote_count":  rating.get("voteCount"),
        "metascore":   meta.get("score"),
    }
    if out["user_rating"] is None:
        log.warning("imdbapi.dev returned no rating for tt_id=%s; response keys: %s", tt_id, list(data.keys()))
    return out


def get_movie_ratings(name: str) -> Optional[dict]:
    """High-level helper: search by movie name and return its ratings.

    Returns None if no movie was found.
    """
    hits = search_imdb(name, only_movies=True, limit=1)
    if not hits:
        return None
    hit = hits[0]
    data = get_title_details(hit["id"])
    rating = (data.get("rating") or {}).get("aggregateRating")
    votes  = (data.get("rating") or {}).get("voteCount")
    meta   = (data.get("metacritic") or {}).get("score")
    return {
        "imdb_id":     hit["id"],
        "title":       data.get("primaryTitle") or hit["title"],
        "year":        data.get("startYear") or hit["year"],
        "user_rating": rating,
        "vote_count":  votes,
        "metascore":   meta,
        "imdb_url":    f"https://www.imdb.com/title/{hit['id']}/",
    }


if __name__ == "__main__":
    import json, sys
    query = " ".join(sys.argv[1:]) or "Inception"
    print(json.dumps(get_movie_ratings(query), indent=2))
