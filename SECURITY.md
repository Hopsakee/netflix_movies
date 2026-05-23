# Security

## Incident — 2026-05-22

The FastHTML session signing key (`.sesskey`, a UUID) and the Plash app-name file (`.plash`) were committed to this repository on 2026-06-05 (commit `fdc887c`) and remained in `origin/main` while the repository was made public on GitHub.

The leaked session key was `ae1ea8b6-640a-4177-8209-f3c25ff34dc0` — anyone who fetched any historical revision of this repo while it was public possesses it. Treat it as fully compromised.

### Remediation taken

1. `.sesskey` and `.plash` rewritten out of all history via `git filter-repo --invert-paths --path .sesskey --path .plash`. Branch refs were force-pushed to `origin/main` on 2026-05-23.
2. `.gitignore` extended to cover `.sesskey`, `.plash`, and `.env.*` (with an allowed `.env.example`).
3. The new production host generates a fresh `.sesskey` on first boot — the leaked UUID is no longer in use anywhere.
4. The deployed app is gated by Authelia at the reverse-proxy layer, so anonymous internet traffic cannot reach the app even before the key was rotated.

### Why this matters even with Authelia in front

The session-signing key is a "defense in depth" secret. If Authelia is ever misconfigured to allow the app to be reached directly (e.g., a vhost goes up without the auth subrequest, or the app is accidentally exposed on an alternate port), an attacker holding the leaked key can mint signed session cookies for any identity the in-app session middleware reads. Rotation closes that residual hole.

## Controls in place

- `.gitignore` excludes anything matching `.sesskey`, `.plash`, `.env` / `.env.*` (except `.env.example`).
- `TMDB_API_KEY` is validated at module load — the process refuses to start if the env var is missing.
- All `requests.get` calls carry `timeout=(5, 30)` and `raise_for_status()` — no hung workers, no silent error-swallowing.
- TMDB pagination is capped at `MAX_PAGES = 5` per route call.
- `get_genres_*` is `@lru_cache`d so each process makes the genre call at most once.
- User-supplied query params (`genre_ids`, `without_genres`, `min_vote`) are validated at the route boundary: id lists are bounded to 20 entries and filtered against the known genre dictionary; `min_vote` is clamped to `[0.0, 10.0]` and rejects NaN / inf.
- Exception handlers return a generic error panel and never include exception detail in the HTML body. Full tracebacks are logged server-side via the `logging` module.
- `fast_app(live=False)` — no livereload websocket exposed in production.

## Pre-commit hygiene (recommended)

The deterministic backstop is a pre-commit hook that blocks UUID-shaped strings and `.env`-shaped files from being committed. Suggested install (run once on a dev machine):

```bash
pip install --user gitleaks  # or: brew install gitleaks
cat > .git/hooks/pre-commit <<'SH'
#!/usr/bin/env bash
exec gitleaks protect --staged --redact --no-banner
SH
chmod +x .git/hooks/pre-commit
```

Or wire `gitleaks` into a GitHub Action (`.github/workflows/gitleaks.yml`) so pushes that contain a fresh secret are caught at PR time.

## Reporting a vulnerability

This is a personal project. If you find an issue, open a GitHub issue or email the maintainer.
