# Post-Loss Fixes — Implementation Guide

## Current State (2026-05-22)
- **Kill switch is ACTIVE** — bot will not trade until deactivated
- Bot is running (PID on port 8082), single instance confirmed
- MAX_CONTRACTS reverted to 10, BANKROLL_OVERRIDE=$20 in .env
- Live PnL: ~+$9 after a -$16 loss in the 1730 window (two NO positions lost)
- Dashboard now has separate Live vs Paper stats (already deployed)

## CRITICAL: Git & Testing Rules
1. **ALWAYS run tests before restarting the bot:** `PYTHONPATH=src pytest tests/ -v`
2. **ALWAYS commit and push after changes:** `git add <files> && git commit -m "..." && git push`
3. **NEVER restart the bot without checking for open positions first:** `sqlite3 trades.db "SELECT * FROM trades WHERE pnl IS NULL;"`
4. **NEVER start a second bot instance.** Always verify single instance: `pgrep -f "kalshi_bot.main" -a | grep -v pgrep` — must show exactly 1 PID
5. **Kill switch:** Deactivate via dashboard admin endpoint ONLY after both fixes below are deployed and tested
6. **Dashboard runs as a SEPARATE process** on port 8082. If you change dashboard.py, you must ALSO restart it: `kill <dashboard_pid> && PYTHONPATH=src nohup .venv/bin/python -u -m kalshi_bot.dashboard >> logs/dashboard.log 2>&1 &`

## Fix 1: Take-Profit Exit with Conviction Gate

### Problem
Bot holds positions to settlement even when sitting on large unrealized gains. In the 1730 window, two NO positions were profitable mid-window but reversed and lost -$16 at settlement. Account hit $50 before crashing back down.

### Current Exit Logic
Only exit is `time_exit` in `_evaluate_exits()` (main.py ~line 1457): sells if position was entered early (>90s remaining) and window is in last 30 seconds. Stop-loss and edge-gone exits were disabled because backtesting showed they destroyed signal edge (converted 112 wins into losses). See memory file `kalshi_bot_exit_fix.md`.

### Implementation
**File: `src/kalshi_bot/main.py` — `_evaluate_exits()` function (around line 1431)**

Add a take-profit check BEFORE the existing time_exit check:

```python
# Take-profit: lock in gains when conviction isn't high.
# current_value = best bid for our side (what we'd sell at)
# If unrealized profit > 50% of entry AND market conviction < 75%,
# sell to lock in profit rather than gambling on settlement.
unrealized_pct = (float(current_value) - float(order.price)) / float(order.price)
if unrealized_pct > 0.50 and float(current_value) < 0.75:
    should_exit = True
    reason = (
        f"take_profit: entry={order.price} now={current_value} "
        f"gain={unrealized_pct:.0%} conviction={float(current_value):.2f}"
    )
```

**Key details:**
- `current_value` is already computed in the function: `best_yes_bid` for YES positions, `best_no_bid` for NO positions
- The 0.75 conviction threshold means: if the market thinks there's >75% chance our side wins, don't sell — let it ride
- The 0.50 profit threshold means: only trigger when we're up at least 50% (bought at 30c, now worth 45c+)
- This does NOT conflict with the backtest findings — those tested stop_loss (selling losers) and edge_gone (selling when edge disappears). Take-profit (selling winners with low conviction) is a different mechanism

**Example scenarios:**
- Bought NO @ 0.29, NO bid now 0.55: gain=90%, conviction=55% → SELL (take profit)
- Bought NO @ 0.29, NO bid now 0.85: gain=193%, conviction=85% → HOLD (high conviction)
- Bought NO @ 0.29, NO bid now 0.35: gain=21%, conviction=35% → HOLD (gain too small)

### Test
Add to `tests/test_executor.py` or create a new test file:
```python
# test that take_profit fires when gain > 50% and conviction < 75%
# test that take_profit does NOT fire when conviction >= 75%
# test that take_profit does NOT fire when gain < 50%
```

## Fix 2: Fresh Orderbook Price at Order Placement

### Problem
Orders fail to fill because the entry price is stale. The signal is generated from an orderbook snapshot at time T, but by the time `enter_position()` places the order (T + hundreds of ms), the ask has moved. The 3c slippage buffer doesn't always cover the gap. Result: 33% of recent orders cancelled as stale.

Evidence from logs:
- BTC NO x33 @ 0.27 (limit 0.30) — CANCELLED (1715 window, post dual-session fix)
- ETH NO x11 @ 0.53 (limit 0.56) — CANCELLED (same window)
- Multiple YES-side cancellations earlier in the session

### Current Flow
1. `evaluate_momentum()` reads orderbook, gets `taker_price = orderbook.best_no_ask` (or best_yes_ask)
2. Signal created with `kalshi_price = taker_price`
3. `enter_position()` receives signal, adds 3c buffer: `entry_price = signal.kalshi_price + TAKER_SLIPPAGE_BUFFER`
4. Order placed at `entry_price` as limit — but the real ask may have moved past this limit

### Implementation
**File: `src/kalshi_bot/execution/executor.py` — `enter_position()` method (live path, around line 220-240)**

Before placing the order, re-read the fresh orderbook and use its ask price instead of the signal's stale price:

```python
# Use fresh orderbook price at placement time instead of signal's stale price
entry_price = signal.kalshi_price
route = signal.route if hasattr(signal, "route") else "taker"
if route != "maker" and self._get_orderbook is not None:
    snapshot = self._get_orderbook(signal.ticker)
    if snapshot is not None:
        fresh_book, received_at = snapshot
        age_s = (datetime.now(timezone.utc) - received_at).total_seconds()
        if age_s < 5.0:  # only use if fresh
            if signal.side.value == "yes":
                fresh_ask = fresh_book.best_yes_ask
            else:
                fresh_ask = fresh_book.best_no_ask
            if fresh_ask is not None:
                entry_price = fresh_ask

if route != "maker":
    entry_price = min(entry_price + TAKER_SLIPPAGE_BUFFER, Decimal("0.99"))
```

**Key details:**
- `self._get_orderbook` is already wired up in the Executor (used by `_fresh_taker_price` for maker-to-taker promotion)
- The 5-second freshness check prevents using a stale fallback book
- If no fresh book available, falls back to signal price (existing behavior)
- Still applies the 3c TAKER_SLIPPAGE_BUFFER on top of the fresh price
- The `datetime` and `timezone` imports are already present in the file
- The `_get_orderbook` callback returns `tuple[OrderBook, datetime] | None`

### Edge re-check
After getting the fresh price, you should verify the edge is still positive before placing:
```python
# Re-check edge with fresh price to avoid overpaying
fresh_price_float = float(entry_price)
if fresh_price_float >= signal.real_prob:
    logger.info("Edge gone at fresh price %s, skipping", entry_price)
    return None
```

### No new tests needed for this
The existing `test_promote_to_taker_uses_fresh_price` test already validates the `_get_orderbook` + fresh price pattern. The change to `enter_position()` uses the same mechanism.

## Restart Procedure

After implementing both fixes:

```bash
# 1. Run ALL tests
PYTHONPATH=src pytest tests/ -v

# 2. Check no open positions
sqlite3 trades.db "SELECT * FROM trades WHERE pnl IS NULL;"

# 3. Verify single instance
pgrep -f "kalshi_bot.main" -a | grep -v pgrep

# 4. Kill and restart bot
kill <bot_pid>
sleep 3
# Verify it's dead:
pgrep -f "kalshi_bot.main" -a | grep -v pgrep  # should be empty
PYTHONPATH=src nohup .venv/bin/python -u -m kalshi_bot.main >> logs/bot.log 2>&1 &
# Verify single instance:
pgrep -f "kalshi_bot.main" -a | grep -v pgrep  # should show exactly 1 PID

# 5. Git commit and push
git add src/kalshi_bot/execution/executor.py src/kalshi_bot/main.py
git commit -m "Add take-profit exit with conviction gate + fresh orderbook pricing"
git push

# 6. Verify bot is healthy
curl -s 'http://127.0.0.1:8082/api/logs/tail?event=HEALTH&n=3' | python3 -m json.tool

# 7. Deactivate kill switch (only after confirming everything works)
# Use dashboard admin endpoint or Telegram bot command
```

## Key Files Reference
- `src/kalshi_bot/execution/executor.py` — order lifecycle, enter_position(), exit_position()
- `src/kalshi_bot/main.py` — _evaluate_exits() (line ~1431), _check_settlements(), main loop
- `src/kalshi_bot/strategy/momentum.py` — signal generation, price computation
- `src/kalshi_bot/models/market.py` — OrderBook model, best_yes_ask/best_no_ask properties
- `src/kalshi_bot/risk/sizing.py` — Kelly sizing, MAX_CONTRACTS=10, MAX_COST=$10
- `tests/test_executor.py` — executor tests
- `tests/test_sizing.py` — sizing tests
- `.env` — runtime config (BANKROLL_OVERRIDE=20, TRADING_MODE=live)

## Known Gotchas
- **Dashboard is a separate process.** Restarting the bot does NOT restart the dashboard. Check `ss -tlnp | grep 8082` to find dashboard PID.
- **Orphan trades:** If the bot restarts with open positions, they become orphans. The `_reconcile_orphans()` method cleans them after 30 min (timestamp bug was fixed — uses strftime with 'T' separator now).
- **.env is in .gitignore.** Config changes there don't get committed. Document them in commit messages.
- **Paper orders use `PAPER-` prefix in order_id.** Live orders use Kalshi's UUID. This is how paper vs live is distinguished in the DB.
- **TAKER_SLIPPAGE_BUFFER is 3c (Decimal("0.03"))** in executor.py. Orders fill at the actual ask, not at the limit — the buffer just ensures the limit is high enough to cross the spread.
