# Kalshi V2 — Momentum + OBI Trading Bot

Production paper-trading bot for Kalshi 15-minute crypto binaries.

## What it does

- Streams live Coinbase prices (BTC/ETH/SOL)
- Tracks active 15-minute windows
- Trades when **60s momentum** and **orderbook imbalance** agree
- Uses **maker-first entry** with taker promotion fallback
- Applies risk gates (kill switch, daily loss limit, cooldown, re-entry lock)
- Logs trades/signals + backtesting datasets to SQLite
- Sends Telegram/Discord alerts
- Serves a modern web dashboard with live SSE updates

## How to run (step-by-step)

### 0) Prerequisites

- Python 3.10+
- Linux/macOS shell
- Kalshi API key + RSA private key PEM

### 1) Install deps

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

### 2) Configure environment

```bash
cp .env.example .env
```

Fill in `.env` at minimum:

- `KALSHI_API_KEY`
- `KALSHI_PRIVATE_KEY_PATH`
- `KALSHI_ENV=demo`
- `TRADING_MODE=paper`

Optional:

- Telegram (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`)
- Discord (`DISCORD_WEBHOOK_URL`)
- OpenRouter (`OPENROUTER_API_KEY`)

### 3) Run quality gates

```bash
PYTHONPATH=src mypy --strict src/
ruff check src/
PYTHONPATH=src pytest tests/ -v
```

### 4) Start the bot (paper mode)

```bash
PYTHONPATH=src python -m kalshi_bot.main
```

### 5) Start the dashboard

```bash
PYTHONPATH=src python -m kalshi_bot.dashboard
```

Then open:

- `http://localhost:8080` (or `DASHBOARD_PORT`)

### 6) Typical runtime workflow

- Keep bot process running in terminal A.
- Keep dashboard process running in terminal B.
- Use Telegram commands (if configured): `/status`, `/stats`, `/maker`, `/window`.
- Check dashboard route analytics for maker/taker behavior.

### 7) Stop safely

- Press `Ctrl+C` in bot terminal.
- Bot handles graceful shutdown and closes clients/db connections.

### 8) VPS paper-testing checklist (recommended)

- Run in `KALSHI_ENV=prod` + `TRADING_MODE=paper`
- Restrict inbound ports to trusted IPs (dashboard/API should not be public-open)
- Keep `.env` and key PEM out of git
- Use a process manager (`systemd` or `pm2`) with auto-restart
- Persist and back up `trades.db` and `live_state.json`
- Set up basic monitoring: process alive, disk space, dashboard `/api/health`

### 9) Exporting VPS data back to local dev

Dashboard backend includes incremental export APIs:

- `GET /api/export/state` — current max IDs / counts per table
- `GET /api/export/changes` — row deltas since provided cursors

Example pull:

```bash
curl "http://<vps-host>:8080/api/export/state"
curl "http://<vps-host>:8080/api/export/changes?since_trade_id=0&since_signal_id=0"
```

You can also use dashboard buttons:

- **Export Cursor**
- **Export Changes**

These download JSON snapshots you can import/analyze locally while bot keeps running on VPS.

### 10) Telegram rollout readiness

Telegram support is already implemented. To enable on VPS:

```env
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=<token>
TELEGRAM_CHAT_ID=<chat_id>
```

Keep Discord enabled/disabled per your preference. Start with paper mode while validating alert flow.

---

## Release notes

### Plan phases 1–9

- `PHASE9_FINAL_CLEANUP.md`

### Additional user-requested phase

- `CHANGELOG_PHASE10.md`
  - UI modernization + route analytics
  - Documents explicit deviations from the original 0–9 plan

---

## Operational readiness notes

Current status is **paper-trading ready**, contingent on:

1. Valid Kalshi API credentials + private key path
2. Network access to Coinbase/Kalshi/OpenRouter/Telegram (as configured)
3. Running in `TRADING_MODE=paper` for burn-in period

Recommended before live trading:

- collect 100+ paper trades
- verify maker/taker route quality in dashboard
- monitor `/stats`, `/maker`, and dashboard route analytics
