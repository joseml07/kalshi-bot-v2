# CHANGELOG: live-vs-paper diagnosis fixes (2026-05-23)

This batch addresses the root causes identified in
`src/kalshi_bot/explanation.md` (Q0–Q7).
**No `.env` changes.** Sizing, Kelly fraction, and per-side limits are
unchanged — that's a separate operator decision once the code is right.

---

## Q2 — `_evaluate_exits` now reachable on every tick *(highest-impact)*

**File:** `src/kalshi_bot/main.py`

**Before:** `_evaluate_exits` was called once per tick — but only on the
"fresh signal succeeded" branch (after risk check, after `yes_disabled`
gate, after the `submit_result` block). Every `signal is None`, risk-veto,
or skip-orderbook branch `continue`d past it.

**Symptom:** across the live cycle 2026-05-22T15:03Z → 2026-05-23T14:30Z,
46 of 46 closed trades reached settlement and **0** fired a `time_exit`.
The bot's designed edge (T-30s favorable-bid exit) contributed nothing —
the entire +$31.91 live P&L came from being directionally right on
settlements ~54% of the time.

**Fix:** moved the `_evaluate_exits` call to run immediately after the
orderbook freshness check, before any strategy evaluation. The exit only
needs `(filled order for active ticker) + (window) + (fresh orderbook)`,
none of which depend on whether a fresh entry signal exists for this tick.

**Tests:** `tests/test_exits.py::test_time_exit_fires_in_final_30s`
(regression for the bug) and `::test_time_exit_skipped_for_late_entry`
(counter-test — entries placed < 90s before close are correctly held).

---

## Q2 follow-up — `exit_reason` now persists for live settlements

**File:** `src/kalshi_bot/execution/executor.py`

**Before:** `record_settlement()` called `_update_trade_pnl(oid, pnl, entry_fee)`
without an `exit_reason`. Every live trade landed in `trades.db` with
`exit_reason=NULL`, making the exit-type split impossible to derive
post-hoc.

**Fix:** pass `exit_reason="settlement"` from `record_settlement`. The
time_exit path was already correct (`exit_position` writes the `time_exit`
reason on the trade row). orphan_reconciled and exit_sell_cancelled paths
were already labelled.

---

## Q4 — Kalshi settlement strike ingested and used for direction

**Files:**
- `src/kalshi_bot/models/market.py` — added `floor_strike`, `cap_strike`,
  `strike_type`, `expected_expiration_value`, plus a `settlement_strike`
  property.
- `src/kalshi_bot/client/kalshi.py` — `_parse_market` parses strike fields
  (safely Optional — older replay data without strike data is unaffected).
- `src/kalshi_bot/data/window_tracker.py` — `WindowState` and
  `PreviousResult` carry the strike. `set_window` accepts strike/strike_type;
  if the same window is re-set with strike info, the existing entry is
  updated in place. `_close_window` now computes `went_up` as
  `current_price >= strike` (for `strike_type='greater_or_equal'`, which is
  what Kalshi's 15-min crypto markets use), falling back to the legacy
  `current_price >= open_price` when strike is unknown.
- `src/kalshi_bot/main.py` — passes the market's `settlement_strike` and
  `strike_type` to `tracker.set_window`.

**Why this matters:** Kalshi's `/markets/{ticker}` response includes
`floor_strike: 75626.08` and `strike_type: "greater_or_equal"` with a
`yes_sub_title: "Target Price: $75,626.08"`. **Settlement is
strike-based, not open-vs-close.** Empirically the bot's old labeling
matched Kalshi on 62/78 (79.5%) settled tickers — the 16 mismatches were
all small-move windows where the strike (slightly different from
Coinbase's open tick) flipped the outcome.

**Forward-only.** Historical `market_events.result` rows are NOT
rewritten. A separate backfill (re-fetching strikes for old tickers) is
out of scope; once any new windows close, they're labeled with the strike.

**Tests:** `tests/test_window_tracker.py` (4 cases) — strike-based YES
win, strike-based NO win, fallback when strike absent, strike refresh on
re-set.

---

## Q0 — `client_order_id` persisted alongside `order_id`

**Files:** `src/kalshi_bot/execution/executor.py`

**Before:** `trades.order_id` stored what Kalshi's POST
`/portfolio/orders` returned. But the Kalshi portfolio export uses a
DIFFERENT identifier — empirically zero of the 50 live order UUIDs from
the May 22–23 window appear in the Kalshi CSV `Market_Id` column. Future
audits keyed by UUID will silently miss every row.

**Fix:**
1. `TrackedOrder.__init__` now takes `client_order_id`.
2. `submit()` reads `order_resp.get("client_order_id")` (which Kalshi
   echoes back from our POST request) and threads it into `TrackedOrder`.
3. `_log_trade` looks up the tracked order and persists `client_order_id`
   as a new column on `trades`.
4. Schema migration: `ALTER TABLE trades ADD COLUMN client_order_id TEXT`,
   guarded by `contextlib.suppress(sqlite3.OperationalError)` (same
   pattern as `fees`, `route`, `exit_reason`).

**Net:** going forward, both UUIDs are on every row. Existing rows keep
`client_order_id = NULL` (the old data couldn't reconcile anyway). Future
audits can join `client_order_id` to anything Kalshi exports that carries
a bot-side identifier.

---

## Q3 — Silent-feed watchdog on the Kalshi WebSocket

**File:** `src/kalshi_bot/client/kalshi_ws.py`

**Before:** the 2026-05-23T08:00 outage flatlined `messages_total` for
~200 seconds without triggering a reconnect. TCP keepalive didn't notice;
the websockets library kept the connection open. The bot kept polling
`ws.recv()` with a 1-second timeout, getting nothing, and just looping.

**Fix:** added `_last_message_mono` (any received message updates it) and
a watchdog check on every recv timeout. If silent for > 30 seconds
(`_SILENT_FEED_WATCHDOG_S`), close the socket and raise
`ConnectionClosed`; the outer `start()` loop reconnects with a fresh
subscribe.

**Threshold rationale:** 30s is below the worst observed outage (197s,
2026-05-23T08:00) so we recover faster than the failure mode, and above
typical quiet periods on `orderbook_delta` (median age was 0.02s; p99
14.6s). No existing tests broke; the change is in the timeout-branch of
the recv loop only.

---

## What I did NOT change (per instructions)

- `.env` — sizing, Kelly fraction, per-side limits.
- Historical `market_events.result` rows.
- `SWITCH_TO_LIVE.md` — that's an operator document.

---

## Recommended operator follow-up after these code fixes

(Mirrors the recommendation in `explanation.md`. Same content — repeated
here so it's visible alongside the diffs.)

1. **Restart the bot** so the schema migration runs and the new code is
   loaded.
2. **Re-test in paper for a few cycles** to confirm `time_exit` fires
   regularly and `exit_reason` is now populated. Look for the
   `exit_signal` log lines in `bot.log` — there should be many.
3. **Dial sizing back** if you want extra safety while watching the new
   exit path land: `KELLY_FRACTION=0.10`, `MAX_PER_TRADE=$5.00` in `.env`
   until you've seen at least one full session with the fixes live.
4. **(Larger project, separate)** Backfill `market_events.result` for
   historical tickers by re-fetching `/markets/{ticker}` and computing
   `close >= floor_strike`. Then re-run P0 / Test A / Test C with the
   corrected direction column. Until that's done, the backtest's edge
   claim should be considered unvalidated.

---

## Quality gates (all green)

```
$ PYTHONPATH=src .venv/bin/python -m pytest tests/
======================== 101 passed, 1 warning in 2.35s ========================

$ PYTHONPATH=src .venv/bin/python -m mypy --strict src/
Success: no issues found in 38 source files

$ .venv/bin/ruff check src/ tests/
All checks passed!
```

New tests added:

- `tests/test_exits.py::test_time_exit_fires_in_final_30s` — regression
  for Q2.
- `tests/test_exits.py::test_time_exit_skipped_for_late_entry` —
  counter-test for the `entered_at > 90s` gate.
- `tests/test_window_tracker.py` — 4 cases for Q4 strike handling.
