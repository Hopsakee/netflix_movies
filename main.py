"""JamesFlix — public list of top-rated Netflix-NL movies & series, gated by Authelia at the reverse-proxy layer.

The app itself is unauthenticated by design: Authelia handles identity before the request reaches uvicorn.
Do NOT add in-app auth here; that's a deployment-layer concern.
"""
from typing import Optional
import logging
import math
import os

import pandas as pd
from fasthtml.common import *
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
DEFAULT_MIN_VOTE = 7.0
RATING_STEPS = [6.0, DEFAULT_MIN_VOTE, 7.5, 8.0, 8.5]

FONTS_CSS = ("https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,800"
             "&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@500;600&display=swap")

# Dark "screening room" theme with NO CSS framework underneath. The earlier
# restyles broke because Pico's own component defaults (table, headings, its
# automatic dark mode) kept overriding ours, so text ended up light-on-light.
# Pico is now switched off (`pico=False`) and every element below is styled
# from these tokens alone — nothing to fight. All text/ground pairs are ≥ 4.5:1.
CSS = """
:root {
  --bg: #101114; --topbar: #14161a; --panel: #17191e; --surface: #191b20; --raised: #1e2026; --chip: #24272e;
  --border: #2a2d34; --border-mid: #2e323a; --border-strong: #3a3e47;
  --text: #f3f1ec; --text-strong: #f7f5f0; --control: #e4e5e9; --tag-text: #d5d7dd; --body: #c9ccd3;
  --muted: #a9adb6; --faint: #8a8f99;
  --accent: #f2b544; --accent-ink: #1a1406; --tmdb: #8fd6cc;
  --out-bg: #3a1f22; --out-border: #e0707a; --out-text: #f6c9cd;
  --display: 'Bricolage Grotesque', system-ui, sans-serif;
  --sans: 'IBM Plex Sans', system-ui, sans-serif;
  --mono: 'IBM Plex Mono', ui-monospace, monospace;
}
html { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 16px/1.5 var(--sans); }
a { color: var(--accent); }
a:focus-visible, button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.htmx-request #app, #app.htmx-request { opacity: .6; transition: opacity .15s; }

.topbar { border-bottom: 1px solid var(--border); background: var(--topbar); }
.wrap { max-width: 1180px; margin: 0 auto; padding: 0 24px; }
.topbar .wrap { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 16px; padding-block: 18px; }
.brand svg { color: var(--accent); }
.brand { display: flex; align-items: center; gap: 12px; color: var(--text); text-decoration: none; cursor: pointer; }
.brand-name { font: 800 26px/1 var(--display); letter-spacing: -.02em; }
.brand-sub { font-size: 13px; color: var(--muted); margin-top: 4px; }
.tabs { display: flex; padding: 4px; background: var(--raised); border: 1px solid var(--border-mid); border-radius: 999px; }
.tab { border-radius: 999px; padding: 10px 22px; min-height: 44px; display: inline-flex; align-items: center;
       font-weight: 600; font-size: 15px; color: var(--body); text-decoration: none; cursor: pointer; }
.tab.on { background: var(--accent); color: var(--accent-ink); }

main.wrap { padding-top: 40px; padding-bottom: 80px; }
.intro { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 16px 32px; margin-bottom: 28px; }
h1 { margin: 0; font: 800 clamp(34px, 5vw, 52px)/1.05 var(--display); letter-spacing: -.03em; color: var(--text-strong); }
.lede { margin: 10px 0 0; color: var(--muted); font-size: 15px; }
.label { font-size: 13px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); }
.rating-picker { display: flex; flex-direction: column; gap: 8px; }
.pills { display: flex; flex-wrap: wrap; gap: 6px; }
.pill { min-width: 52px; min-height: 44px; padding: 0 14px; border-radius: 10px; display: inline-flex; align-items: center;
        justify-content: center; font: 600 15px var(--mono); background: var(--raised); color: var(--control);
        border: 1px solid var(--border-mid); text-decoration: none; cursor: pointer; }
.pill.on { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }

.panel { background: var(--panel); border: 1px solid var(--border); border-radius: 16px; padding: 20px 20px 22px; margin-bottom: 28px; }
.panel-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 8px 24px; margin-bottom: 14px; }
.panel h2 { margin: 0; font-size: 15px; font-weight: 600; color: var(--text); }
.legend { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 18px; font-size: 13px; color: var(--muted); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.swatch { width: 12px; height: 12px; border-radius: 4px; }
.swatch.in { background: var(--accent); }
.swatch.out { background: var(--out-bg); border: 1.5px solid var(--out-border); }
.reset { color: var(--accent); font-weight: 600; text-underline-offset: 3px; cursor: pointer; padding: 6px 0; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip { display: inline-flex; align-items: center; gap: 6px; min-height: 40px; padding: 0 14px; border-radius: 999px;
        font-size: 14px; font-weight: 500; color: var(--control); border: 1.5px solid var(--border-strong);
        text-decoration: none; cursor: pointer; }
.chip:hover { border-color: var(--muted); }
.chip.in { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); font-weight: 600; }
.chip.out { background: var(--out-bg); color: var(--out-text); border-color: var(--out-border); }
.chip.out .chip-name { text-decoration: line-through; }

.meta-row { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 12px; font-size: 14px; color: var(--muted); }
.titles { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
.title-card { display: flex; flex-wrap: wrap; gap: 16px 24px; align-items: flex-start; background: var(--surface);
              border: 1px solid var(--border); border-radius: 16px; padding: 22px 24px; }
.title-main { flex: 999 1 460px; min-width: 0; display: flex; gap: 20px; }
.title-body { min-width: 0; }
.rank { flex: 0 0 40px; font: 600 20px var(--mono); color: var(--faint); padding-top: 3px; }
.title-card h3 { margin: 0; font: 600 23px/1.2 var(--display); letter-spacing: -.01em; color: var(--text-strong); }
.year { font: 400 16px var(--sans); color: var(--muted); margin-left: 6px; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0 12px; }
.tag { font-size: 12.5px; font-weight: 500; color: var(--tag-text); background: var(--chip); border-radius: 6px; padding: 3px 9px; }
.desc { margin: 0; color: var(--body); font-size: 15.5px; line-height: 1.6; max-width: 68ch; }
.scores { flex: 1 1 200px; display: flex; gap: 10px; justify-content: flex-end; }
.score { flex: 1 1 0; max-width: 112px; background: var(--bg); border: 1px solid var(--border-mid); border-radius: 12px; padding: 10px 12px; }
.score-src { font-size: 11.5px; font-weight: 600; letter-spacing: .08em; }
.score-src.tmdb { color: var(--tmdb); }
.score-src.omdb { color: var(--accent); }
.score-val { font: 600 26px/1.2 var(--mono); color: var(--text-strong); }
.score-val small { font-size: 13px; color: var(--faint); }
.empty { padding: 56px 24px; text-align: center; border: 1px dashed var(--border-strong); border-radius: 16px; color: var(--body); }
.empty strong { display: block; font: 600 22px var(--display); color: var(--text); }
.footnote { margin: 40px 0 0; font-size: 13px; color: var(--faint); }

.choices { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin-top: 32px; }
.choice { display: block; padding: 28px; background: var(--surface); border: 1px solid var(--border); border-radius: 16px;
          color: var(--text); text-decoration: none; cursor: pointer; }
.choice:hover { border-color: var(--accent); }
.choice strong { display: block; font: 600 26px/1.2 var(--display); color: var(--text-strong); }
.choice span { display: block; margin-top: 8px; color: var(--muted); }
.error h1 { color: var(--out-text); }

@media (max-width: 560px) {
  .title-card { padding: 18px; }
  .title-main { gap: 12px; }
  .rank { flex-basis: 28px; font-size: 16px; }
  .scores { justify-content: flex-start; }
}
"""

app, rt = fast_app(live=False, pico=False, secret_key=SESSION_SECRET, hdrs=[
    Link(rel="preconnect", href="https://fonts.googleapis.com"),
    Link(rel="preconnect", href="https://fonts.gstatic.com", crossorigin=""),
    Link(rel="stylesheet", href=FONTS_CSS),
    Style(CSS),
])

FILM_ICON = NotStr(
    '<svg aria-hidden="true" width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/>'
    '<path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/></svg>')
CHECK_ICON = NotStr(
    '<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5L20 7"/></svg>')
CROSS_ICON = NotStr(
    '<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="3" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>')


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
        return DEFAULT_MIN_VOTE
    return max(MIN_RATING, min(MAX_RATING, float(min_vote)))


def _nav(url: str, *c, **kw):
    "An <a> that swaps the whole #app via htmx, but still works as a plain link."
    return A(*c, href=url, hx_get=url, hx_target="#app", hx_swap="outerHTML", hx_push_url="true", **kw)


def _shell(title: str, active: Optional[str], *content):
    "Top bar + main column. Every route returns this whole block so htmx swaps and full loads look identical."
    def tab(label, key, url):
        return _nav(url, label, cls="tab on" if active == key else "tab",
                    aria_current="page" if active == key else None)
    return Title(title), Div(
        Header(Div(
            _nav(index.to(),
                 FILM_ICON,
                 Div(Div("JamesFlix", cls="brand-name"), Div("Top rated on Netflix NL", cls="brand-sub")),
                 cls="brand"),
            Nav(tab("Movies", "movies", movies.to(min_vote=DEFAULT_MIN_VOTE)),
                tab("Series", "series", series.to(min_vote=DEFAULT_MIN_VOTE)),
                cls="tabs", aria_label="List"),
            cls="wrap"), cls="topbar"),
        Main(*content, cls="wrap"),
        id="app",
    )


def _error_panel(kind: str):
    "Generic error panel — never includes exception detail."
    return _shell("JamesFlix", kind, Div(
        H1("Something went wrong"),
        P("Could not load the list right now. Please try again in a moment.", cls="lede"),
        cls="error"))


def _missing(v): return v is None or pd.isna(v)


def _score(source: str, value):
    "One rating box. TMDB and OMDB are always shown separately — never blended, never labelled 'IMDb'."
    text = "—" if _missing(value) else f"{float(value):.1f}"
    return Div(Div(source.upper(), cls=f"score-src {source}"),
               Div(text, Small("/10") if text != "—" else "", cls="score-val"),
               cls="score", aria_label=f"{source.upper()} rating {text}")


def title_list(df):
    "Ranked cards, one per title. Metascore is fetched but not displayed — one rating per source, no derived third."
    if len(df) == 0:
        return Div(Strong("Nothing matches these filters"), P("Lower the minimum rating or clear a genre."), cls="empty")
    items = []
    for i, (_, row) in enumerate(df.iterrows(), start=1):
        year = row.get('release_date')
        items.append(Li(
            Div(Div(f"{i:02d}", cls="rank", aria_hidden="true"),
                Div(H3(row['title'], Span("" if _missing(year) else str(int(year)), cls="year")),
                    Div(*[Span(g, cls="tag") for g in row['genres']], cls="tags"),
                    P(row['description'], cls="desc") if row.get('description') else None,
                    cls="title-body"),
                cls="title-main"),
            Div(_score("tmdb", row.get('vote_average')), _score("omdb", row.get('omdb_rating')), cls="scores"),
            cls="title-card"))
    return Ol(*items, cls="titles")


@rt
def index():
    return _shell("JamesFlix", None,
        H1("What are we watching?"),
        P("The best-rated movies and series on Netflix NL, with TMDB and OMDB ratings side by side.", cls="lede"),
        Div(_nav(movies.to(min_vote=DEFAULT_MIN_VOTE), Strong("Movies"), Span("Top rated films, filter by genre"), cls="choice"),
            _nav(series.to(min_vote=DEFAULT_MIN_VOTE), Strong("Series"), Span("Top rated shows, filter by genre"), cls="choice"),
            cls="choices"))


def _render_list(route, kind: str, fetch, genre_dict: dict,
                 min_vote: float, genre_ids: str, without_genres: str):
    "Shared renderer for /movies and /series."
    filter_on = [gid for gid in _parse_id_list(genre_ids) if gid in genre_dict]
    filter_out = [gid for gid in _parse_id_list(without_genres) if gid in genre_dict and gid not in filter_on]
    mv = _validate_rating(min_vote)

    df, _ = fetch(genre_ids=filter_on, no_genre_ids=filter_out, min_vote=mv)

    # URLs are always rebuilt from the validated id lists, never from the raw query string —
    # stale ids from old bookmarks must not leak into the next link.
    def url(vote=mv, on=filter_on, out=filter_out):
        return route.to(min_vote=vote, genre_ids=",".join(map(str, on)), without_genres=",".join(map(str, out)))

    def chip(gid: int):
        "Genre chips cycle: off → must have → hidden → off."
        name = genre_dict[gid]
        if gid in filter_on:
            state, icon, nxt = "in", CHECK_ICON, url(on=[g for g in filter_on if g != gid], out=filter_out + [gid])
            label = f"{name}: required. Click to hide it instead."
        elif gid in filter_out:
            state, icon, nxt = "out", CROSS_ICON, url(out=[g for g in filter_out if g != gid])
            label = f"{name}: hidden. Click to clear."
        else:
            state, icon, nxt = "", "", url(on=filter_on + [gid])
            label = f"{name}: click to require it."
        return _nav(nxt, icon, Span(name, cls="chip-name"), cls=f"chip {state}".strip(), aria_label=label)

    rating_pills = [_nav(url(vote=r), f"{r:.1f}+", cls="pill on" if r == mv else "pill",
                         aria_current="true" if r == mv else None) for r in RATING_STEPS]
    return _shell(f"JamesFlix · {kind.title()}", kind,
        Div(Div(H1(f"Best {kind} right now"),
                P("TMDB and OMDB ratings are shown side by side — never blended.", cls="lede")),
            Div(Span("Minimum TMDB rating", cls="label"), Div(*rating_pills, cls="pills"), cls="rating-picker"),
            cls="intro"),
        Section(
            Div(H2("Genres"),
                Div(Span(Span(cls="swatch in"), "Click once: must have"),
                    Span(Span(cls="swatch out"), "Twice: hide"),
                    Span("Third click clears"),
                    _nav(url(on=[], out=[]), "Reset genres", cls="reset") if (filter_on or filter_out) else None,
                    cls="legend"),
                cls="panel-head"),
            Div(*[chip(gid) for gid in sorted(genre_dict, key=genre_dict.get)], cls="chips"),
            cls="panel", aria_label="Genre filter"),
        Div(Span(f"{len(df)} {kind}"), Span("Sorted by OMDB rating"), cls="meta-row"),
        title_list(df),
        P("Data from TMDB (availability on Netflix NL) and OMDB. Lists refresh every 30 minutes.", cls="footnote"),
    )


@rt
def series(min_vote: float = DEFAULT_MIN_VOTE, genre_ids: Optional[str] = "", without_genres: Optional[str] = ""):
    try:
        return _render_list(series, "series", get_series, get_genres_series(),
                            min_vote, genre_ids or "", without_genres or "")
    except Exception:
        logging.exception("series route failed")
        return _error_panel("series")


@rt
def movies(min_vote: float = DEFAULT_MIN_VOTE, genre_ids: Optional[str] = "", without_genres: Optional[str] = ""):
    try:
        return _render_list(movies, "movies", get_movies, get_genres_movies(),
                            min_vote, genre_ids or "", without_genres or "")
    except Exception:
        logging.exception("movies route failed")
        return _error_panel("movies")


if __name__ == "__main__":
    # Port comes from the deploy config, matching hopswiki-web and pkw-web
    # (app/config.py in both). The explicit default keeps a missing APP_PORT
    # from falling through to fasthtml's own 5001, which Caddy does not dial.
    serve(port=int(os.getenv("APP_PORT", "8081")), reload=False)
