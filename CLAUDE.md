# Kalshi V2 — Momentum + Mean Reversion Trading Bot

## What This Is

Async Python bot that trades Kalshi's 15-minute crypto up/down binary contracts (KXBTC15M, KXETH15M). Uses momentum + orderbook imbalance (OBI) as a dual-gate signal, with dynamic k volatility-aware probability model. Mean reversion strategy exists but is shadow-logged only (not traded live).

## CRITICAL RULES

1. **Read HANDOFF.md first** — contains current bot state, all findings, and what NOT to do.
2. **ONE AI at a time.** Don't make changes if another AI has uncommitted work.
3. **Run tests before restarting:** `PYTHONPATH=src pytest tests/ -v`
4. **Never restart with open positions:** `sqlite3 trades.db "SELECT * FROM trades WHERE pnl IS NULL;"`
5. **Commit and push after every change.** Include backtest evidence in commit messages.
6. **Don't re-enable stop_loss or take_profit** — both proven to destroy edge.
7. **Don't deploy backtest findings without paper validation** — backtest ≠ live.

## Current Live Config (.env)

```
TRADING_MODE=live
SYMBOLS=BTC,ETH               (SOL disabled — no backtest data)
KELLY_FRACTION=0.25
EDGE_THRESHOLD=0.04
MOMENTUM_MIN_TIME=91           (ensures all entries qualify for time_exit)
MAX_TRADE_PRICE=0.65           (entries above 0.70 lose money live)
MIN_TRADE_PRICE=0.25
YES_SIDE_DISABLED=false
OFFPEAK_START_UTC=20           (no trading 20-23 UTC — consistently loses)
OFFPEAK_END_UTC=23
MAKER_FIRST=false
DAILY_LOSS_LIMIT=10.0
PER_SIDE_DAILY_LOSS_LIMIT=0    (disabled)
```

## Architecture

```
Coinbase WS  ─┐
Kraken WS    ─┼→ composite price → window_tracker → momentum/probability
Bitstamp WS  ─┘
                                    ↓
                   evaluate_momentum (OBI agreement → trade WITH momentum)
                   evaluate_mean_reversion (OBI disagreement → shadow log only)
                                    ↓
                   risk gates → Kelly sizing → Kalshi REST order
                                    ↓
                   time_exit at T-30s (the ONLY profitable exit)
```

## Key Files

```
src/kalshi_bot/
  main.py                — eval loop, exit logic, off-hours gating, shadow trades
  config.py              — all .env settings with defaults
  strategy/momentum.py   — momentum + OBI agreement signal
  strategy/mean_reversion.py — OBI disagreement signal (shadow-only)
  strategy/probability.py — logistic P(up) with dynamic k (cap 600)
  client/coinbase.py     — primary price feed
  client/kraken.py       — secondary feed for composite
  client/bitstamp.py     — secondary feed for composite
  client/kalshi_ws.py    — orderbook with crossed-book trimming
  execution/executor.py  — order lifecycle, settlement tracking
  risk/manager.py        — daily loss, per-side cooldown, kill switch
  risk/sizing.py         — Kelly sizing, MAX_CONTRACTS=10
  alerts/telegram.py     — commands: /status /stats /config /window etc.
  alerts/discord_bot.py  — same commands for Discord
  dashboard.py           — REST API + strategy/side/exit breakdowns
  dashboard.html         — web UI with dynamic k display
tests/                   — 105 tests
```

## Key Commands

- **Run bot**: `PYTHONPATH=src python -m kalshi_bot.main`
- **Tests**: `PYTHONPATH=src pytest tests/ -v`
- **Dashboard**: `PYTHONPATH=src python -m kalshi_bot.dashboard` (port 8082)

## What Works (Proven Live)

- Momentum + OBI agreement with dynamic k
- time_exit at T-30s (67% WR live, only profitable exit)
- Composite pricing (Coinbase + Kraken + Bitstamp)
- Crossed-book inline trimming
- Off-hours gating (20-23 UTC)
- MAX_TRADE_PRICE=0.65

## What Doesn't Work (Proven Live)

- Mean reversion (94.9% backtest, ~45% live shadow — huge gap)
- Take-profit exit (costs 14% PnL)
- Stop-loss exit (destroys edge)
- Entries above $0.70 (risk/reward inverted)
- Trading 20-23 UTC (3W-16L, -$33 across all live sessions)
- Settlement trades (0% WR across all live data)
- SOL (thin books, no backtest data)

## Backtest Data

- **Full backtester:** `/root/kalshi-backtest/` (tick-by-tick, real fills)
- **Merged data:** `/root/kalshi-backtest/data/merged_backtest.db` (46 days, 1.29M snapshots)
- **Flex backtester:** `/root/kalshi-backtest/src/backtest/flex_engine.py` (fast strategy comparison)
- **V1 recovered data:** `/root/fromkalshi/recovered.db`

## Research & Strategy Docs

- `HANDOFF.md` — current state, findings, what NOT to do
- `BACKTEST_FINDINGS.md` — all backtest numbers
- `TODO.md` — remaining work items
- `research_questions.md` — external research needed
- `/root/kalshi-bot/kalshi-bot-v3/` — V3 brainstorms and strategy research

## Shadow Trade Logging

Three types of shadow trades logged in the `signals` table:
- `whatif_mean_reversion` — mean reversion signals (would have traded)
- `whatif_offpeak` — signals during 20-23 UTC (blocked)
- `paper_shadow` — logged alongside every live trade for fill comparison

Query example: `SELECT * FROM signals WHERE action = 'whatif_offpeak' AND timestamp >= '2026-05-26';`

## Known Issues

- Settlement trades always lose (0% WR live). time_exit catches most but not all — some trades enter late or the exit check misses the window transition.
- Kalshi WS goes stale for 15-30s during window transitions (normal — new ticker subscription takes time).
- Backtest shows 91% WR but live is ~50%. Gap is from execution differences (latency, fills, settlement source mismatch).
