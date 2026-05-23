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

# Hard limits on user-supplied query parameters. Anything exceeding these is rejected at the route boundary.
MAX_GENRE_IDS = 20
MIN_RATING = 0.0
MAX_RATING = 10.0

PICO_CSS = "https://cdn.jsdelivr.net/npm/@picocss/pico@2.0.6/css/pico.min.css"
PICO_SRI = "sha384-7P0NVe9LPDbUCAF+fH2R8Egwz1uqNH83Ns/bfJY0fN2XCDBMUI2S9gGzIOIRBKsA"

app, rt = fast_app(live=False, hdrs=[
    Link(rel="stylesheet", href=PICO_CSS, integrity=PICO_SRI, crossorigin="anonymous"),
    Style("""
        .movie-table { width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 0.9em; box-shadow: 0 0 20px rgba(0,0,0,0.1); table-layout: fixed; }
        .movie-table th.title-col { width: 30%; }
        .movie-table th.rating-col { width: 5%; }
        .movie-table th.year-col { width: 5%; }
        .movie-table th.genre-col { width: 10%; }
        .movie-table th.desc-col { width: 50%; }
        .movie-table thead tr { background-color: #2c3e50; color: #fff; text-align: left; }
        .movie-table th, .movie-table td { padding: 10px 12px; }
        .movie-table tbody tr { border-bottom: 1px solid #ddd; }
        .movie-table tbody tr:nth-of-type(even) { background-color: #f3f3f3; }
        .movie-table tbody tr:last-of-type { border-bottom: 2px solid #2c3e50; }
        .movie-table tbody tr:hover { background-color: #e1f5fe; cursor: pointer; }
        .container { max-width: 1200px; margin: 0 auto; padding: 0 1rem; }
        .header { margin: 2rem 0; text-align: center; }
        .genre-tag { display: inline-block; background: #e0e0e0; padding: 2px 8px; border-radius: 10px; font-size: 0.8em; margin: 2px; }
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
        H1("Something went wrong", style="color: #dc3545"),
        P("Could not load the list right now. Please try again in a moment.", style="color: #6c757d"),
        cls="container",
        id=container_id,
    )


def create_table(df, show_meta: bool = True):
    "Create a styled HTML table from the movies dataframe."
    header_cells = [Th('Title'), Th('IMDb')]
    if show_meta:
        header_cells.append(Th('Meta'))
    header_cells += [Th('Year'), Th('Genres', cls='genre-col'), Th('Description', cls='desc-col')]
    thead = Thead(Tr(*header_cells))

    def _missing(v): return v is None or (isinstance(v, float) and pd.isna(v))
    def fmt_imdb(v): return "—" if _missing(v) else f"⭐ {v}"
    def fmt_meta(v): return "—" if _missing(v) else f"🎯 {int(v)}"

    rows = []
    for _, row in df.iterrows():
        genres = Div(
            *[Div(g, cls="genre-tag") for g in row['genres']],
            style="display: flex; flex-direction: column; align-items: flex-start; gap: 4px;",
        )
        cells = [
            Td(row['title'], style="font-weight: bold"),
            Td(fmt_imdb(row.get('imdb_rating'))),
        ]
        if show_meta:
            cells.append(Td(fmt_meta(row.get('metascore'))))
        cells += [
            Td(row['release_date']),
            Td(genres, style="padding: 8px"),
            Td(row['description'], style="white-space: normal; text-overflow: clip;"),
        ]
        rows.append(Tr(*cells, _class="movie-row"))
    return Table(thead, Tbody(*rows), cls="movie-table")


@rt
def index():
    return Titled(
        "🎬 Netflix Movies and Series",
        Div(
            P("Kies welke lijst je wil zien", style="color: #666; margin-top: 0"),
            Div(
                Button("Best movies with genre filter",
                       hx_get=movies.to(min_vote=7), hx_target="#index", hx_swap="innerHTML", hx_push_url="true",
                       style="background-color: #28a745; padding: 10px 15px; cursor: pointer; font-size: 16px; width: auto;"),
                Button("Best series with genre filter",
                       hx_get=series.to(min_vote=7), hx_target="#index", hx_swap="innerHTML", hx_push_url="true",
                       style="background-color: #6f42c1; padding: 10px 15px; cursor: pointer; font-size: 16px; width: auto;"),
                style="display: flex; justify-content: center; gap: 10px;",
            ),
        ),
        id="index",
    )


def _genre_id_for_name(genre_dict: dict, name: str) -> Optional[int]:
    "Reverse-lookup genre id from name. Returns None when not found (was StopIteration before)."
    return next((k for k, v in genre_dict.items() if v == name), None)


def _render_list(route, container_id: str, list_title: str, fetch, genre_dict: dict,
                 min_vote: float, genre_ids: str, without_genres: str):
    "Shared renderer for /movies and /series."
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
                      style=f"cursor: pointer; background-color: {'#d0d0d0' if gid in selected_ids else 'white'}; color: {'black' if gid in selected_ids else '#999'};")
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
                P(f"Filtered for items with TMDB rating ≥ {mv}/10. Ratings shown are from IMDb.",
                  style="color: #666; margin-top: 0"),
                cls="header",
            ),
            FilterGenres(selected_genre_ids),
            Genres(False, sorted_excl_genres),
        ),
        Genres(True, sorted_incl_genres),
        Div(create_table(df), cls="container"),
        id=container_id,
    )


@rt
def series(min_vote: float = 7, genre_ids: Optional[str] = "", without_genres: Optional[str] = ""):
    genre_dict = get_genres_series()

    def FilterGenres(selected_genre_ids: list):
        all_genre_ids = list(genre_dict.keys())
        return Div(P("Genres filtered on: "),
                    Div(*[Div(genre_dict[gid], 
                            hx_get=series.to(min_vote=min_vote, 
                                            genre_ids=(genre_ids + "," if genre_ids else "") + str(gid) if gid not in selected_genre_ids 
                                                    else ','.join([str(g) for g in selected_genre_ids if g != gid]),
                                            without_genres=without_genres),
                            hx_target="#series-container", hx_swap="innerHTML", hx_trigger="click", 
                            cls="genre-tag", 
                            style=f"cursor: pointer; background-color: {'#d0d0d0' if gid in selected_genre_ids else 'white'}; color: {'black' if gid in selected_genre_ids else '#999'};") 
                        for gid in sorted(all_genre_ids, key=lambda x: genre_dict[x])], 
                        style="display: flex; flex-wrap: wrap; align-items: flex-start; gap: 4px;"))

    def Genres(include: bool, genres: list):
        if include:
            return Div(P("Genres available: "), 
                    Div(*[Div(g, hx_get=series.to(min_vote=min_vote, genre_ids=genre_ids,
                                                  without_genres=(without_genres + "," if without_genres else "") + str(next(k for k,v in genre_dict.items() if v==g))),
                                                  hx_target="#series-container", hx_swap="innerHTML", hx_trigger="click", cls="genre-tag", style="cursor: pointer;") for g in genres],
                                                  style="display: flex; flex-wrap: wrap; align-items: flex-start; gap: 4px;"))
        else:
            return Div(P("Genres filtered out: "), 
                    Div(*[Div(g, hx_get=series.to(min_vote=min_vote, genre_ids=genre_ids,
                                                  without_genres=','.join([gid for gid in (without_genres or "").split(',') if gid and int(gid) != next(k for k,v in genre_dict.items() if v==g)])),
                                                  hx_target="#series-container", hx_swap="innerHTML", hx_trigger="click", cls="genre-tag", style="cursor: pointer;") for g in genres],
                                                  style="display: flex; flex-wrap: wrap; align-items: flex-start; gap: 4px;"))


    try:
        filter_out_genre_ids = [int(gid) for gid in (without_genres or "").split(',') if gid]
        filter_on_genre_ids = [int(gid) for gid in (genre_ids or "").split(',') if gid]
        df, sr_filter = get_series(genre_ids=filter_on_genre_ids, no_genre_ids=filter_out_genre_ids, min_vote=min_vote)

        selected_genre_ids = sorted(sr_filter['filt_genres'])
        sorted_incl_genres = sorted(sr_filter['incl_genres'])
        sorted_excl_genres = sorted(sr_filter['excl_genres'])
        return Div(
            Div(Titled("Netflix Series", hx_get=index.to(), hx_target="#index", hx_swap="innerHTML", hx_push_url="true", style="text-decoration: underline; cursor: pointer;")),
            DivFullySpaced(
                Header(
                    H1("🎬 Top Rated Series", style="margin-bottom: 0.5rem"),
                    P(f"Filtered for series with TMDB rating ≥ {min_vote}/10. Ratings shown are from IMDb.", 
                      style="color: #666; margin-top: 0"),
                    cls="header"
                ),
                FilterGenres(selected_genre_ids),
                Genres(False, sorted_excl_genres)),
                Genres(True, sorted_incl_genres),
            Div(
                create_table(df, show_meta=False),
                cls="container"),
            id="series-container"
        )
    except Exception as e:
        return Div(
            H1("Error", style="color: #dc3545"),
            P(f"An error occurred: {str(e)}", style="color: #6c757d"),
            cls="container",
            id="series-container"
        )


@rt
def movies(min_vote: float = 7, genre_ids: Optional[str] = "", without_genres: Optional[str] = ""):
    try:
        return _render_list(movies, "movies-container", "Netflix Movies",
                            get_movies, get_genres_movies(),
                            min_vote, genre_ids or "", without_genres or "")
    except Exception:
        import logging
        logging.exception("movies route failed")
        return _error_panel("movies-container")


if __name__ == "__main__":
    serve()
