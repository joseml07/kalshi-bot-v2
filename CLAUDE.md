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

## Current Config (.env)

<!-- Updated 2026-05-28 — currently running PAPER mode for shadow data collection -->
```
TRADING_MODE=paper             (paper mode; P&L vetoes bypassed, signals observed freely)
SYMBOLS=BTC,ETH               (SOL disabled — no backtest data)
KELLY_FRACTION=0.25
EDGE_THRESHOLD=0.04
MOMENTUM_MIN_TIME=91           (ensures all entries qualify for time_exit)
MAX_TRADE_PRICE=0.65           (entries above 0.70 lose money live)
MIN_TRADE_PRICE=0.25
YES_SIDE_DISABLED=false        (YES 34.2% WR, -$10.95 live — pending paper validation for NO-only)
OFFPEAK_START_UTC=20           (no trading 20-23 UTC — consistently loses)
OFFPEAK_END_UTC=23
MAKER_FIRST=false
DAILY_LOSS_LIMIT=10.0          (only enforced in live mode — paper bypasses this)
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
tests/                   — 110 tests
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
- Settlement trades (~1.4% WR — market moves so hard the book empties; not a mechanical exit failure)
- SOL (thin books, no backtest data)
- **YES side (34.2% WR, -$10.95 all-time live)** — NO side (75.5% WR, +$460) is the entire profit engine
- Box filter / regime detection — WR flat across chop/moderate/trending (73/74/72%); useless

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

<!-- Updated 2026-05-28 — 14 shadow types; all per-order exit shadows deduped via TrackedOrder.shadow_fired -->
Logged in the `signals` table (action column):

**Strategy/gate shadows:**
- `whatif_mean_reversion` — mean reversion signals (would have traded)
- `whatif_offpeak` — signals during 20-23 UTC (blocked)
- `whatif_inverted_offpeak` — inverted version of off-peak signals (logs opposite side)
- `paper_shadow` — logged alongside every paper/live trade for fill comparison

**Exit shadows (fire at most once per order via `TrackedOrder.shadow_fired`):**
- `whatif_momentum_exit` — momentum reversed during hold (fires when T>90s)
- `whatif_prob_decay_exit` — dynamic-k win probability dropped ≥15pp vs entry
- `whatif_convergence_75` — position value hit ≥0.75c during hold (profit lock check)

**Entry quality shadows (logged after paper_shadow, one per signal):**
- `whatif_edge_08` — signal passes stricter edge_threshold=0.08
- `whatif_obi_strong` — OBI magnitude > 0.30
- `whatif_sweet_spot` — price in 0.45-0.55 (market near 50/50)
- `whatif_strong_move` — momentum_60s > 2× threshold
- `whatif_ghost_mode` — would pause (consecutive losses ≥ 2 for that symbol)
- `whatif_buddy_disagree` — cross-asset BTC/ETH momentum disagrees with signal
- `whatif_tight_spread` — YES spread ≤ 0.03 (tight book = better fills)
- `whatif_prior_window_agrees` — previous 15m window moved same direction as signal
- `whatif_price_65_75` — would trigger if MAX_TRADE_PRICE raised to 0.75

Query examples:
```sql
SELECT action, COUNT(*), SUM(CASE WHEN win THEN 1 ELSE 0 END) FROM signals
WHERE timestamp >= date('now', '-7 days') GROUP BY action;

SELECT * FROM signals WHERE action = 'whatif_ghost_mode' AND timestamp >= '2026-05-28';
```

## Known Issues

- Settlement trades always lose (~1.4% WR live). These are structurally different from time_exit trades — the market moves so far against the position that Kalshi empties the book before exit. Not fixable with a better exit mechanism; the position was wrong from entry.
- Kalshi WS goes stale for 15-30s during window transitions (normal — new ticker subscription takes time).
- Backtest shows 91% WR but live is ~50%. Gap is from execution differences (latency, fills, settlement source mismatch).
- YES side is a persistent loser (34.2% WR, -$10.95 all-time). NO side (75.5% WR) carries the bot. Root cause unknown — possibly retail YES bias on crypto contracts, or asymmetric fill quality.
- Paper mode was previously blocked by daily loss limit ($10). Fixed 2026-05-28: P&L vetoes are now bypassed in paper mode.

## Risk Manager Paper Mode Behavior

`RiskManager.check()` skips `_check_daily_loss` and `_check_per_side_daily_loss` when `trading_mode == "paper"`. Kill switch, concurrent position limits, cooldowns, and locked-side checks all still apply. This was fixed in commit `045f30c` after the bot went silent for hours after hitting the $10 daily loss limit while in paper mode.
