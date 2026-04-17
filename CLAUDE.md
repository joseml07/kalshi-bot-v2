# Kalshi V2 — Momentum + OBI Trading Bot

## What This Is

Async Python bot that trades Kalshi's 15-minute crypto up/down binary contracts (KXBTC15M, KXETH15M, KXSOL15M). V2 replaces V1's broken contrarian logistic model with a momentum + orderbook imbalance strategy that rides trends instead of fading them.

## Reference Directories (READ-ONLY — do NOT edit)

- `/home/psypolatic/kalshi/new/` — V1 bot (full source, git repo). Copy infrastructure from here.
- `/home/psypolatic/kalshi/thebacktester/btc_backtester/` — Backtester that proved the momentum strategy works (62.6% WR, +$281 net at 10 contracts).

## Project Layout

```
src/kalshi_bot/
  main.py              — async event loop: Coinbase feed -> strategies -> risk -> execute
  config.py            — pydantic-settings from .env
  client/kalshi.py     — async REST client (httpx), rate limiter, auth
  client/coinbase.py   — WebSocket price feed -> async queue
  client/auth.py       — RSA-PSS signing for Kalshi API
  client/kalshi_ws.py  — Kalshi WebSocket orderbook feed
  client/openrouter.py — OpenRouter LLM client for AI analysis
  models/market.py     — Market, OrderBook, OrderBookLevel models
  models/price.py      — PriceTick model
  analysis/window_analyzer.py — post-window AI analysis pipeline
  data/window_tracker.py — tracks 15-min windows, open/current price, momentum
  data/recorder.py     — historical data recorder for backtesting
  strategy/momentum.py   — NEW: momentum + OBI dual-gate strategy
  strategy/probability.py — logistic model for P(up) given price change + time
  strategy/fees.py       — Kalshi fee math (taker: 7%, maker: 1.75%)
  strategy/signals.py    — Signal dataclass
  execution/executor.py  — order lifecycle: maker-first, place, poll, cancel, exit, settle
  risk/manager.py        — pre-trade gates: daily loss, concurrent positions, kill switch
  risk/sizing.py         — quarter-Kelly position sizing
  alerts/telegram.py     — Telegram bot: alerts + interactive commands
  dashboard.py           — FastAPI REST API for trade data
  dashboard.html         — dark-themed web UI
  logging_config.py      — structlog JSON logging with file rotation
tests/                   — pytest suite, run with: PYTHONPATH=src pytest tests/ -v
```

## Key Commands

- **Run bot**: `PYTHONPATH=src python -m kalshi_bot.main`
- **Tests**: `PYTHONPATH=src pytest tests/ -v`
- **Type check**: `PYTHONPATH=src mypy --strict src/`
- **Lint**: `ruff check src/`
- **All checks**: `PYTHONPATH=src mypy --strict src/ && ruff check src/ && PYTHONPATH=src pytest tests/ -v`

## Quality Gates (must pass after every change)

1. `mypy --strict` — zero issues
2. `ruff check` — zero issues
3. `pytest` — all tests pass

## Tech Stack

- Python 3.10+ (use `from __future__ import annotations` everywhere)
- httpx (async HTTP), websockets 12.0 (legacy API), pydantic v2, structlog
- pytest + pytest-asyncio for tests
- SQLite for trade logging (trades.db)

## V2 Strategy: Momentum + OBI

### Entry Logic
- Compute 60-second momentum from Coinbase price ticks
- Compute orderbook imbalance: `yes_depth - no_depth`
- Trade ONLY when `sign(momentum) == sign(imbalance)` (agreement = confirmation)
- Positive momentum + positive OBI -> buy YES
- Negative momentum + negative OBI -> buy NO

### Edge Gate
- Estimate probability using logistic model
- Edge = `est_prob - entry_price - (fee / contracts)`
- Only trade if edge >= 0.06

### Maker-First Execution
- Place limit order at best bid (maker fee: 1.75%)
- Wait up to 90 seconds for fill
- If unfilled, cancel and re-place at best ask (taker fee: 7%)
- Track route (maker/taker/taker_promoted) for analytics

### Key Differences from V1
- V1 faded moves (contrarian); V2 rides moves (momentum)
- V1 used taker-only; V2 prefers maker orders (75% fee reduction)
- V1 had inverted model confidence filter; V2 trades with model direction
- V1 had broken OBI as separate strategy; V2 uses OBI as confirmation gate

## Kalshi 15-Min Market Structure

- Series: KXBTC15M, KXETH15M, KXSOL15M
- Contract: "Will [crypto] price at close be higher than at open?"
- Settlement: binary $0 or $1 based on price at close vs open
- Markets roll every 15 minutes; open/close times come from GET /markets
- Orderbook format: `orderbook_fp` with `yes_dollars`/`no_dollars` arrays of `[price_str, qty_str]`
- Taker fee: ceil(0.07 * contracts * price * (1-price) * 100) / 100
- Maker fee: ceil(0.0175 * contracts * price * (1-price) * 100) / 100

## V1 Bugs to Avoid

1. **Multiple trades per window** — V1's `locked_side` only blocked the opposite side. V2 must block ALL re-entry on a ticker once traded.
2. **SQLite string/float confusion** — V1 stored prices as TEXT and forgot to CAST. Always use `CAST(col AS REAL)` in SQL queries.
3. **Phantom windows** — V1 created windows with `open_price=0`. Skip if no price data.
4. **Paper settlement race** — V1 could double-settle. Check order state before settling.
5. **Exit sell price** — V1 used `best_bid - $0.01` (guaranteed slippage). V2 should sell at `best_bid`.
6. **Config deploy failures** — V1's $0.27 floor was set in code but never deployed. Use `.env` as single source of truth with sane defaults in code.

## Investigating live VPS state

The bot runs at `http://137.184.144.30` (port 80, no auth on read endpoints). Use `curl` from Bash — do NOT ask the user for tarballs.

Start with `GET /api/diagnostics` for the one-shot incident snapshot. For raw forensic log access:

- `GET /api/logs/tail?n=100&event=<substrings>&level=warning&since=<iso>` — reverse-scans `logs/bot.log` in 64 KB chunks, bounded by a 2 MB scan cap. `n` is capped at 500. `event` accepts a comma-separated list (OR match on substring). Returns `{lines, scanned_bytes, truncated, returned}`.
- `GET /api/logs/stats` — event-name histogram over the recent log window. One-shot "what's happening right now" without transferring raw lines.

Look at the `HEALTH` heartbeat (fired every 60 s) for a scannable session timeline: `curl '.../api/logs/tail?event=HEALTH&n=30'`.

Write endpoints (trades, kill switch, settings) stay admin-key gated and intentionally out of AI hands — the user is the only one who should touch those.

## Git Workflow

This is a standalone git repo. The implementation plan is in PLAN.md.
Each phase from PLAN.md should be ONE commit with a descriptive message.
