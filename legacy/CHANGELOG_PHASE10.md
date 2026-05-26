# Phase 10 — UI Modernization + Plan Deviations

Date: 2026-04-14

## Why this phase exists

User-requested extension beyond original `PLAN.md` to make website and bot interactions cleaner, modern, and more functional.

## Scope added (deviation from original plan)

This phase is intentionally outside the original 0–9 plan and adds product polish:

1. Modernized dashboard visual design with a cleaner information hierarchy.
2. Added execution route analytics endpoint and UI visualization.
3. Improved live telemetry emphasis (status chips, update heartbeat, stronger live-window cards).
4. Simplified interaction model (session/all-time toggle + single command-center view).

## Backend/API changes

### `src/kalshi_bot/dashboard.py`

- Added new endpoint:
  - `GET /api/routes?all=true|false`
  - Returns grouped performance by route (`maker`, `taker`, `taker_promoted`) including:
    - `trades`
    - `wins`
    - `losses`
    - `total_pnl`

No existing endpoint contracts were removed.

## Frontend changes

### `src/kalshi_bot/dashboard.html`

Replaced previous multipage-tab style UI with a modern command-center layout:

- Gradient dark theme with glassy header and compact telemetry badges.
- KPI card grid (P&L, WR, trade count, fees, best/worst, etc.).
- Enhanced live window cards with progress bars and momentum-friendly metrics.
- Route analytics pills showing route trade counts, win-rate, and total P&L.
- Cleaner, denser trades and signals tables.
- Session vs all-time toggle preserved.
- Uses SSE `/api/live` and periodic static refresh for aggregates.

## Behavior compatibility notes

- Existing dashboard API endpoints remain available.
- Telegram and bot core logic were not changed in this phase.
- This phase is UI + observability focused.

## Quality gates

All gates executed and passing after phase 10 edits:

- `PYTHONPATH=src mypy --strict src/`
- `ruff check src/`
- `PYTHONPATH=src pytest tests/ -v`

