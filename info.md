# Kalshi V2 Diagnostic Info (info.md)

This file is a **quick-reference for humans + AI** to diagnose bot issues fast.

---

## 1) Fast Triage (2–5 minutes)

1. Check process startup logs in `logs/bot.log`:
   - `kalshi_ws_connected`
   - `kalshi_ws_subscribed`
   - `paper_trading_mode` (if paper)
2. Check runtime state in `live_state.json`:
   - `health.coinbase_stale`
   - `health.kalshi_ws_stale`
   - `health.signal_counters_hour`
   - `health.kalshi_ws` (WS diagnostics counters)
3. Check trade path signals:
   - `risk_blocked` (reason bucket)
   - `exit_signal`
   - `Settlement ... daily_pnl=...`
4. If no trades, inspect WS health counters:
   - `negative_qty`, `delta_missing_fields`, `delta_parse_error`, `resync_ticker`, `resync_full`
5. Confirm dashboard API is alive:
   - `GET /api/health`
   - `GET /api/summary`

---

## 2) Core Runtime Flow (functions)

### Main loop (`src/kalshi_bot/main.py`)
- `run_bot(settings)`
  - Initializes clients, feeds, risk, executor, tracker, recorder, alerters.
- `_drain_prices(...)`
  - Drains Coinbase ticks into `WindowTracker`, records sampled ticks.
- `_fast_eval_loop(...)`
  - Hot path: evaluate strategy + risk + submit orders.
- `_slow_housekeeping_loop(...)`
  - Settlements, exits, market refresh, live_state updates, health checks, analysis triggers.
- `_evaluate_exits(...)`
  - Stop-loss/edge/time exit logic.
- `_settle_paper_positions(...)`
  - Settlement logic in paper mode.
- `_run_window_analysis(...)`
  - Builds per-window context and writes analysis to DB.

### WS orderbook feed (`src/kalshi_bot/client/kalshi_ws.py`)
- `KalshiOrderbookFeed.start()` / `stop()`
- `set_tickers(tickers)`
- `get_orderbook(ticker)`
- `diagnostics()` ← key for troubleshooting
- Internal handlers:
  - `_handle_message`
  - `_apply_snapshot`
  - `_apply_delta`
  - `_schedule_ticker_resync`
  - `_schedule_full_resync`

### Execution and risk
- `Executor.submit(...)`
- `Executor.check_pending_fills()`
- `Executor.promote_to_taker()`
- `Executor.cancel_stale()`
- `Executor.record_settlement(...)`
- `Executor.exit_position(...)`
- `RiskManager.check(signal)`
- `RiskManager.record_fill(...)`
- `RiskManager.record_settlement(...)`
- `RiskManager.reset_session(...)`

---

## 3) Runtime API endpoints (dashboard)

Source: `src/kalshi_bot/dashboard.py`

### Trading/metrics
- `GET /api/trades`
- `GET /api/summary`
- `GET /api/pnl_history`
- `GET /api/signals`
- `GET /api/stats`
- `GET /api/pnl_rolling`
- `GET /api/routes`
- `GET /api/stats_by_symbol`
- `GET /api/trade/{trade_id}`

### Analysis/windows/data
- `GET /api/analyses`
- `GET /api/windows`
- `GET /api/price_ticks`
- `GET /api/strategy_evals`
- `GET /api/session`

### Health/export/streaming
- `GET /api/health`
- `GET /api/export/state`
- `GET /api/export/changes`
- `GET /api/live` (SSE)
- `GET /ws/live` (WebSocket)

### Admin/runtime controls
- `GET /api/balance`
- `GET /api/settings`
- `POST /api/settings` (requires `X-Admin-Key`)
- `POST /api/reset` (requires `X-Admin-Key`)
- `POST /api/kill` (requires `X-Admin-Key`)
- `POST /api/resume` (requires `X-Admin-Key`)
- `GET /api/kill_switch`

### Downloads
- `GET /download/trades.csv`
- `GET /download/pnl.csv`
- `GET /download/signals.csv`
- `GET /download/windows.csv`
- `GET /download/strategy_evals.csv`
- `GET /download/full.json`

---

## 4) `live_state.json` diagnostic fields

Top-level keys:
- `updated_at`
- `daily_pnl`
- `open_positions`
- `balance`
- `balance_age_s`
- `trading_mode`
- `kelly_fraction`
- `symbols` (per-symbol runtime snapshot)
- `health`

### `health` important fields
- `coinbase_last_tick_age_s`
- `kalshi_ws_last_update_age_s`
- `coinbase_stale`
- `kalshi_ws_stale`
- `db_last_write_age_s`
- `db_last_write_latency_ms`
- `api_read_per_sec`, `api_write_per_sec`
- `api_read_utilization`, `api_write_utilization`
- `signal_counters_window_start`
- `signal_counters_hour` (top skip/trade reasons)
- `kalshi_ws` (detailed WS diagnostics object)

### `health.kalshi_ws` diagnostics
- `messages_total`
- `messages_snapshot`
- `messages_delta`
- `delta_before_snapshot`
- `delta_missing_fields`
- `delta_parse_error`
- `delta_bad_side`
- `negative_qty`
- `sequence_gap`
- `resync_ticker`
- `resync_full`
- `active_books`
- `tracked_tickers`
- `last_resync_reason`
- `last_resync_ticker`
- `last_resync_age_s`

---

## 5) High-signal log events and meanings

### Main loop / control
- `risk_blocked` → signal generated but vetoed by risk manager.
- `exit_signal` → exit criteria hit.
- `housekeeping_cycle_error` → critical housekeeping exception.
- `window_analysis_failed` → post-window analysis failed.
- `kalshi_ws_health_alert` → WS diagnostics crossed warning thresholds.

### WS feed
- `kalshi_ws_connected`
- `kalshi_ws_subscribed`
- `kalshi_ws_error`
- `kalshi_ws_delta_missing_fields`
- `kalshi_ws_delta_parse_error`
- `kalshi_ws_delta_bad_side`
- `kalshi_ws_negative_qty`
- `kalshi_ws_sequence_gap`
- `kalshi_ws_resync_ticker`
- `kalshi_ws_resync_full`

### Execution/risk/PnL
- `Placed ... order_id=...`
- `Confirmed fill: ...`
- `Cancelled stale order ...`
- `Taker promotion failed ...`
- `Settlement ... pnl=... daily_pnl=...`

### AI analysis visibility (if enabled)
- `OpenRouter response from ...`
- `AI analysis complete for ...`
- `AI analysis returned empty ...`

---

## 6) Common failure patterns → likely cause

1. **No trades + high `skip_no_orderbook` or `kalshi_ws_stale=true`**
   - Likely WS feed degradation or no fresh snapshots.
2. **Rapidly rising `negative_qty` / `delta_missing_fields`**
   - Likely upstream schema drift or malformed message handling.
3. **Only `risk_blocked` with `Already traded ...`**
   - Normal in-window lock behavior after a trade on same ticker.
4. **`daily_pnl` below loss limit + risk vetoes**
   - Daily loss gate activated.
5. **`housekeeping_cycle_error` repeating**
   - Immediate code/runtime issue; inspect stack trace first.

---

## 7) Diagnostic files to inspect first

- `logs/bot.log` (primary structured runtime events)
- `live_state.json` (current health + counters)
- `trades.db` (tables: `trades`, `signals`, `strategy_evals`, `window_analyses`, etc.)
- `logs/dashboard.out` (dashboard endpoint access/use)

---

## 8) Useful commands

- Run bot:
  - `PYTHONPATH=src python -m kalshi_bot.main`
- Run tests:
  - `PYTHONPATH=src pytest tests/ -v`
- Type check:
  - `PYTHONPATH=src mypy --strict src/`
- Lint:
  - `ruff check src/`
- Full checks:
  - `PYTHONPATH=src mypy --strict src/ && ruff check src/ && PYTHONPATH=src pytest tests/ -v`

---

## 9) Notes for AI-assisted diagnosis

When asked to debug quickly, prioritize in this order:
1. `live_state.json` health + `health.kalshi_ws`
2. Recent `risk_blocked` reason distribution
3. WS anomaly events (`missing_fields`, `parse_error`, `negative_qty`, `resync_*`)
4. Settlement/exit behavior (`exit_signal`, `Settlement`)
5. DB row growth in `signals` and `strategy_evals` (strategy running vs blocked)

This order usually identifies feed vs risk vs execution issues in under 5 minutes.
