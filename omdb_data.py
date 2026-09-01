"""
OMDB (https://www.omdbapi.com) — a third-party API that aggregates ratings
(IMDb, Rotten Tomatoes, Metacritic) for a title, keyed by IMDb id. Free tier:
1,000 requests/day, needs a key from https://www.omdbapi.com/apikey.aspx.

Replaces the earlier imdb_data.py, which called the unofficial imdbapi.dev
mirror. That domain stopped resolving entirely (DNS dead, confirmed
2026-08-31 — dig and curl both return nothing) sometime after it was wired
in on 2026-05-23. OMDB is a real, still-operating service with a documented
free tier, rather than an undocumented scrape-workaround.

`imdbRating` in OMDB's response IS IMDb's own rating — OMDB's whole purpose
is surfacing it without needing IMDb's paid official API. Named `omdb_rating`
here anyway, deliberately: the previous module's naming ("IMDb ratings",
built on `imdbapi.dev`) is exactly what caused the confusion about whether a
paid IMDb API was ever involved. Naming this after the service actually
being called avoids repeating that.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

OMDB_API_KEY = os.getenv("OMDB_API_KEY")
if not OMDB_API_KEY:
    raise RuntimeError("OMDB_API_KEY is not set. Refusing to start without an API key.")

OMDB_URL = "https://www.omdbapi.com/"


def _num(v) -> Optional[float]:
    "OMDB returns 'N/A' for a missing value, and ratings/scores as strings."
    if v in (None, "N/A"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


@lru_cache(maxsize=2048)
def get_ratings_by_imdb_id(imdb_id: str) -> dict:
    """Return {omdb_rating, metascore} for an IMDb id, via OMDB.

    Values are None when missing/unavailable. Never raises — returns a dict
    of Nones on error, so it can be used safely in bulk lookups. Results are
    cached in-process so repeated table renders are free, and a failure is
    cached too, so a genre re-toggle never re-pays a failing lookup.
    """
    try:
        r = requests.get(OMDB_URL, params={"apikey": OMDB_API_KEY, "i": imdb_id}, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("OMDB lookup failed for imdb_id=%s: %r", imdb_id, e)
        return {"omdb_rating": None, "metascore": None}

    if data.get("Response") == "False":
        log.warning("OMDB returned no result for imdb_id=%s: %s", imdb_id, data.get("Error"))
        return {"omdb_rating": None, "metascore": None}

    return {
        "omdb_rating": _num(data.get("imdbRating")),
        "metascore":   _num(data.get("Metascore")),
    }
