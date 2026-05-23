# JamesFlix

Small FastHTML app that lists the top-rated movies and series on Netflix-NL via TMDB's `/discover` API.

## Running locally

```bash
uv sync
cp .env.example .env  # then put your TMDB Bearer token in .env
uv run python main.py
```

The app starts on the port FastHTML picks (default `5001`). Visit `http://localhost:5001`.

## Deployment

Hosted on a Hetzner VM behind Authelia. The app itself does no authentication — Authelia gates every request before it reaches uvicorn. Do not add in-app auth here.

### Secrets contract

- `.env` is required at runtime and is **never** committed.
- The app generates its own session-signing key (`.sesskey`) on first boot. `.sesskey` is **never** committed — it is per-host runtime state. If a `.sesskey` ever appears in git, rotate it immediately (delete the file; FastHTML regenerates on next boot) and scrub git history.

## Security

This repo had a security incident on 2026-05-22 — see `SECURITY.md` for the incident timeline and the prevention controls now in place.
