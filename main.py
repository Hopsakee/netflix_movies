"""JamesFlix — public list of top-rated Netflix-NL movies & series, gated by Authelia at the reverse-proxy layer.

The app itself is unauthenticated by design: Authelia handles identity before the request reaches uvicorn.
Do NOT add in-app auth here; that's a deployment-layer concern.
"""
from typing import Optional
import math
import os

import pandas as pd
from fastcore.all import *
from fasthtml.common import *
from monsterui.all import *
from dotenv import load_dotenv

from tmdb_data import get_movies, get_series, get_genres_movies, get_genres_series

load_dotenv()

# Session signing key, passed explicitly. Without it FastHTML's `get_key` falls back to
# creating a `.sesskey` file in the working directory (fasthtml/core.py), and /app is
# root-owned while the container runs as `appuser` — that write raises PermissionError
# during import and crash-loops the container. Fail loud, as tmdb_data.py does.
SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET is not set. Refusing to start without a session key.")

# Hard limits on user-supplied query parameters. Anything exceeding these is rejected at the route boundary.
MAX_GENRE_IDS = 20
MIN_RATING = 0.0
MAX_RATING = 10.0

PICO_CSS = "https://cdn.jsdelivr.net/npm/@picocss/pico@2.0.6/css/pico.min.css"
PICO_SRI = "sha384-7P0NVe9LPDbUCAF+fH2R8Egwz1uqNH83Ns/bfJY0fN2XCDBMUI2S9gGzIOIRBKsA"

# Plain light theme, chosen for readability over any aesthetic — the earlier dark
# restyle fought Pico's own component defaults (headings, table) instead of
# replacing them, so contrast broke in places these overrides didn't reach.
# Every text-bearing element below gets an explicit color rather than relying
# on inheritance, specifically so that mistake can't repeat.
JF_BG = "#f7f7f5"
JF_SURFACE = "#ffffff"
JF_SURFACE_ALT = "#f4f6f7"
JF_TEXT = "#1a1a1a"
JF_MUTED = "#5f6368"
JF_ACCENT = "#2c3e50"
JF_ACCENT_INK = "#ffffff"
JF_BORDER = "#e2e2e2"
JF_DANGER = "#c0392b"

app, rt = fast_app(live=False, secret_key=SESSION_SECRET, hdrs=[
    Link(rel="stylesheet", href=PICO_CSS, integrity=PICO_SRI, crossorigin="anonymous"),
    Style(f"""
        html {{ color-scheme: light; }}
        body {{ background: {JF_BG} !important; color: {JF_TEXT} !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
        h1, h2, h3, h4 {{ color: {JF_TEXT} !important; font-weight: 700; }}
        a {{ color: {JF_ACCENT} !important; }}
        p {{ color: {JF_TEXT}; }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 0 1.5rem; }}
        .header {{ margin: 2.5rem 0 1.5rem; text-align: center; }}
        .movie-table {{ width: 100%; border-collapse: collapse; margin: 1.5rem 0; font-size: 0.92em; table-layout: fixed;
                        background: {JF_SURFACE}; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
        .movie-table th.title-col {{ width: 28%; }}
        .movie-table th.year-col {{ width: 5%; }}
        .movie-table th.genre-col {{ width: 10%; }}
        .movie-table th.desc-col {{ width: 44%; }}
        .movie-table thead tr {{ background: {JF_ACCENT}; }}
        .movie-table thead th {{ color: {JF_ACCENT_INK} !important; text-align: left; font-weight: 600; }}
        .movie-table th, .movie-table td {{ padding: 12px 14px; color: {JF_TEXT}; }}
        .movie-table tbody tr {{ border-bottom: 1px solid {JF_BORDER}; }}
        .movie-table tbody tr:nth-of-type(even) {{ background-color: {JF_SURFACE_ALT}; }}
        .movie-table tbody tr:hover {{ background-color: #eef4f8; cursor: pointer; }}
        .genre-tag {{ display: inline-block; background: {JF_SURFACE_ALT}; border: 1px solid {JF_BORDER}; color: {JF_TEXT};
                      padding: 3px 10px; border-radius: 10px; font-size: 0.8em; margin: 2px; }}
    """)
])


def _parse_id_list(raw: Optional[str]) -> list[int]:
    "Parse a user-supplied comma-separated id list. Drop non-numeric / non-positive entries. Cap length."
    if not raw:
        return []
    out: list[int] = []
    for tok in raw.split(','):
        tok = tok.strip()
        if not tok:
            continue
        try:
            val = int(tok)
        except ValueError:
            continue
        if val <= 0:
            continue
        out.append(val)
        if len(out) >= MAX_GENRE_IDS:
            break
    return out


def _validate_rating(min_vote: float) -> float:
    "Clamp the user-supplied minimum rating to a sane range. Reject NaN / inf."
    if not math.isfinite(min_vote):
        return 7.0
    return max(MIN_RATING, min(MAX_RATING, float(min_vote)))


def _error_panel(container_id: str) -> "FT":
    "Generic error panel — never includes exception detail."
    return Div(
        H1("Something went wrong", style=f"color: {JF_DANGER}"),
        P("Could not load the list right now. Please try again in a moment.", style=f"color: {JF_MUTED}"),
        cls="container",
        id=container_id,
    )


def create_table(df, show_meta: bool = True):
    "Create a styled HTML table from the movies dataframe. TMDB and OMDB ratings are shown as separate columns — never blended into one number, and never labelled 'IMDb' (that naming is what caused the confusion this replaced)."
    header_cells = [Th('Title', cls='title-col'), Th('TMDB'), Th('OMDB')]
    if show_meta:
        header_cells.append(Th('Meta'))
    header_cells += [Th('Year', cls='year-col'), Th('Genres', cls='genre-col'), Th('Description', cls='desc-col')]
    thead = Thead(Tr(*header_cells))

    def _missing(v): return v is None or (isinstance(v, float) and pd.isna(v))
    def fmt_tmdb(v): return "—" if _missing(v) else f"📺 {v}"
    def fmt_omdb(v): return "—" if _missing(v) else f"⭐ {v}"
    def fmt_meta(v): return "—" if _missing(v) else f"🎯 {int(v)}"

    rows = []
    for _, row in df.iterrows():
        genres = Div(
            *[Div(g, cls="genre-tag") for g in row['genres']],
            style="display: flex; flex-direction: column; align-items: flex-start; gap: 4px;",
        )
        cells = [
            Td(row['title'], cls='title-col', style="font-weight: 600"),
            Td(fmt_tmdb(row.get('vote_average'))),
            Td(fmt_omdb(row.get('omdb_rating'))),
        ]
        if show_meta:
            cells.append(Td(fmt_meta(row.get('metascore'))))
        cells += [
            Td(row['release_date'], cls='year-col'),
            Td(genres, cls='genre-col', style="padding: 8px"),
            Td(row['description'], cls='desc-col', style="white-space: normal; text-overflow: clip;"),
        ]
        rows.append(Tr(*cells, _class="movie-row"))
    return Table(thead, Tbody(*rows), cls="movie-table")


@rt
def index():
    return Titled(
        "🎬 Netflix Movies and Series",
        Div(
            P("Kies welke lijst je wil zien", style=f"color: {JF_MUTED}; margin-top: 0"),
            Div(
                Button("Best movies with genre filter",
                       hx_get=movies.to(min_vote=7), hx_target="#index", hx_swap="innerHTML", hx_push_url="true",
                       style=f"background-color: {JF_ACCENT}; color: {JF_ACCENT_INK}; border: none; padding: 12px 20px; cursor: pointer; font-size: 16px; width: auto;"),
                Button("Best series with genre filter",
                       hx_get=series.to(min_vote=7), hx_target="#index", hx_swap="innerHTML", hx_push_url="true",
                       style=f"background-color: transparent; color: {JF_ACCENT}; border: 1px solid {JF_ACCENT}; padding: 12px 20px; cursor: pointer; font-size: 16px; width: auto;"),
                style="display: flex; justify-content: center; gap: 16px; margin-top: 1rem;",
            ),
        ),
        id="index",
    )


def _genre_id_for_name(genre_dict: dict, name: str) -> Optional[int]:
    "Reverse-lookup genre id from name. Returns None when not found (was StopIteration before)."
    return next((k for k, v in genre_dict.items() if v == name), None)


def _render_list(route, container_id: str, list_title: str, fetch, genre_dict: dict,
                 min_vote: float, genre_ids: str, without_genres: str,
                 show_meta: bool = True):
    "Shared renderer for /movies and /series. `show_meta` toggles the Metascore column (movies only)."
    filter_on = _parse_id_list(genre_ids)
    filter_out = _parse_id_list(without_genres)
    filter_on = [gid for gid in filter_on if gid in genre_dict]
    filter_out = [gid for gid in filter_out if gid in genre_dict]
    mv = _validate_rating(min_vote)

    df, sr_filter = fetch(genre_ids=filter_on, no_genre_ids=filter_out, min_vote=mv)

    # Use the validated `filter_on` list for the selected-state, NOT the raw `filt_genres` from sr_filter —
    # stale ids from bookmarks must not poison the toggle-off URL builder below.
    selected_genre_ids = sorted(filter_on)
    sorted_incl_genres = sorted(sr_filter['incl_genres'])
    sorted_excl_genres = sorted(sr_filter['excl_genres'])

    def FilterGenres(selected_ids: list):
        all_ids = list(genre_dict.keys())
        return Div(
            P("Genres filtered on: "),
            Div(
                *[Div(genre_dict[gid],
                      hx_get=route.to(
                          min_vote=mv,
                          genre_ids=(genre_ids + "," if genre_ids else "") + str(gid) if gid not in selected_ids
                                    else ','.join([str(g) for g in selected_ids if g != gid]),
                          without_genres=without_genres,
                      ),
                      hx_target=f"#{container_id}", hx_swap="innerHTML", hx_trigger="click",
                      cls="genre-tag",
                      style=(f"cursor: pointer; background-color: {JF_ACCENT}; color: {JF_ACCENT_INK}; border-color: {JF_ACCENT};"
                             if gid in selected_ids else
                             f"cursor: pointer; background-color: transparent; color: {JF_MUTED}; border-color: {JF_BORDER};"))
                  for gid in sorted(all_ids, key=lambda x: genre_dict[x])],
                style="display: flex; flex-wrap: wrap; align-items: flex-start; gap: 4px;",
            ),
        )

    def Genres(include: bool, genres: list):
        if include:
            return Div(
                P("Genres available: "),
                Div(
                    *[Div(g,
                          hx_get=route.to(
                              min_vote=mv,
                              genre_ids=genre_ids,
                              without_genres=(without_genres + "," if without_genres else "") + str(_genre_id_for_name(genre_dict, g))
                                              if _genre_id_for_name(genre_dict, g) is not None else without_genres,
                          ),
                          hx_target=f"#{container_id}", hx_swap="innerHTML", hx_trigger="click",
                          cls="genre-tag", style="cursor: pointer;")
                      for g in genres if _genre_id_for_name(genre_dict, g) is not None],
                    style="display: flex; flex-wrap: wrap; align-items: flex-start; gap: 4px;",
                ),
            )
        gid_for = lambda g: _genre_id_for_name(genre_dict, g)
        return Div(
            P("Genres filtered out: "),
            Div(
                *[Div(g,
                      hx_get=route.to(
                          min_vote=mv,
                          genre_ids=genre_ids,
                          without_genres=','.join([wid for wid in (without_genres or "").split(',')
                                                   if wid and gid_for(g) is not None and int(wid) != gid_for(g)]),
                      ),
                      hx_target=f"#{container_id}", hx_swap="innerHTML", hx_trigger="click",
                      cls="genre-tag", style="cursor: pointer;")
                  for g in genres if gid_for(g) is not None],
                style="display: flex; flex-wrap: wrap; align-items: flex-start; gap: 4px;",
            ),
        )

    return Div(
        Div(Titled(list_title,
                   hx_get=index.to(), hx_target="#index", hx_swap="innerHTML", hx_push_url="true",
                   style="text-decoration: underline; cursor: pointer;")),
        DivFullySpaced(
            Header(
                H1(f"🎬 Top Rated {list_title.split()[-1]}", style="margin-bottom: 0.5rem"),
                P(f"TMDB rating ≥ {mv}/10 · TMDB and OMDB ratings shown separately",
                  style=f"color: {JF_MUTED}; margin-top: 0"),
                cls="header",
            ),
            FilterGenres(selected_genre_ids),
            Genres(False, sorted_excl_genres),
        ),
        Genres(True, sorted_incl_genres),
        Div(create_table(df, show_meta=show_meta), cls="container"),
        id=container_id,
    )


@rt
def series(min_vote: float = 7, genre_ids: Optional[str] = "", without_genres: Optional[str] = ""):
    try:
        return _render_list(series, "series-container", "Netflix Series",
                            get_series, get_genres_series(),
                            min_vote, genre_ids or "", without_genres or "",
                            show_meta=False)
    except Exception:
        import logging
        logging.exception("series route failed")
        return _error_panel("series-container")


@rt
def movies(min_vote: float = 7, genre_ids: Optional[str] = "", without_genres: Optional[str] = ""):
    try:
        return _render_list(movies, "movies-container", "Netflix Movies",
                            get_movies, get_genres_movies(),
                            min_vote, genre_ids or "", without_genres or "",
                            show_meta=True)
    except Exception:
        import logging
        logging.exception("movies route failed")
        return _error_panel("movies-container")


if __name__ == "__main__":
    # Port comes from the deploy config, matching hopswiki-web and pkw-web
    # (app/config.py in both). The explicit default keeps a missing APP_PORT
    # from falling through to fasthtml's own 5001, which Caddy does not dial.
    serve(port=int(os.getenv("APP_PORT", "8081")), reload=False)
