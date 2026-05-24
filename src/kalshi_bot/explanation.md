# Kalshi Bot V2 — Full Architecture & Backtest Data Reference

*Generated 2026-05-23. Describes the bot as deployed on the VPS and the backtest data available for strategy development.*

---

## 1. How the Bot Works: Tick-to-Trade

```
Coinbase WS → asyncio.Queue → _drain_prices → tracker.update_price → eval_trigger
    ↓
_fast_eval_loop → tracker.get_window → ws_feed.get_orderbook → evaluate_momentum
    ↓
Signal passes gates → risk.check → executor.submit → kelly_size → Kalshi REST API
    ↓
TrackedOrder (PENDING → FILLED → time_exit / settlement)
    ↓
check_pending_fills / _check_settlements → record_settlement → DB write
```

### The Three Concurrent Loops

**`_drain_prices`** — Dequeues Coinbase ticks from `asyncio.Queue`, updates `WindowState.current_price` and 60-second price history, fires `eval_trigger.set()` to wake the strategy loop. Only place ticks enter the system.

**`_fast_eval_loop`** — Event-driven on `eval_trigger`. On every tick: (1) checks exits for existing positions, (2) evaluates `evaluate_momentum()` or `evaluate_lwm()`, (3) submits new trades. Runs at Coinbase tick rate (~5/s). The exit check runs BEFORE new signal evaluation — this was the critical Q2 fix (2026-05-23) that prevented all previous trades from getting time exits.

**`_slow_housekeeping_loop`** — Every 5 seconds: polls for order fills (`check_pending_fills`), promotes maker orders to taker, cancels stale orders, checks settlements, refreshes balance/positions/markets from REST API, writes diagnostics.

### The Momentum + OBI Strategy (`strategy/momentum.py`)

Five sequential gates:

1. **Time window**: `seconds_remaining` between `momentum_min_time` (30s) and `momentum_max_time` (480s). Trade too early = no momentum signal. Trade too late = settlement coin flip.

2. **Non-zero momentum**: `momentum_60s` (price change over last 60 seconds) must not be None or 0.0.

3. **OBI sign agreement** (the V2 thesis): `sign(momentum) == sign(orderbook_imbalance)` AND both non-zero. Momentum and order flow must agree. This is the key filter — 99% of ticks fail here.

4. **Minimum depth**: `total_depth >= min_total_depth` (50 contracts default). Thin books have unreliable pricing.

5. **Edge threshold**: Computes `up_prob` via the logistic model, then `net_edge = est_prob - price - fee_per_contract`. Only trades with net_edge >= `edge_threshold` pass. Tries maker first (1.75% fee), falls back to taker (7% fee).

### The Logistic Probability Model (`strategy/probability.py`)

```
z = k * price_change_pct / sqrt(time_fraction)
P(up) = 1 / (1 + e^(-z))   clamped to [0.05, 0.95]
```

- `price_change_pct` = `(current_price - open_price) / open_price`
- `time_fraction` = `seconds_remaining / 900.0`, clamped to min 0.01
- `k` = logistic steepness (default 150.0, live .env = 200.0)
- Higher k = more confident predictions on small moves
- The `1/sqrt(time)` term means a 0.2% move with 2 min left is more significant than 0.2% with 10 min left (random walk property)

### Orderbook Imbalance (`models/market.py`)

```
OBI = (yes_depth - no_depth) / total_depth    range [-1, 1]
```

- Positive = more YES volume resting (bullish sentiment)
- Negative = more NO volume resting (bearish sentiment)
- Best bid/ask are synthetic: `best_yes_ask = 1.0 - best_no_bid` (yes+no must sum to 1.0)

### Fee Math (`strategy/fees.py`)

Both fees use the same Kalshi formula with different rates:
```
fee = ceil(0.07 * contracts * price * (1-price) * 100) / 100    # taker: 7%
fee = ceil(0.0175 * contracts * price * (1-price) * 100) / 100   # maker: 1.75%
```
On a 5-contract trade at $0.50: taker fee = $0.09, maker fee = $0.02. 4.5x difference.

### Kelly Sizing (`risk/sizing.py`)

```
kelly_fraction = (win_prob - price) / (1 - price)
dollars = bankroll * kelly_fraction * strength_mult * asset_mult
contracts = floor(dollars / price)
capped at: min(contracts, MAX_COST_DOLLARS / price, MAX_CONTRACTS)
```

- `MAX_COST_DOLLARS = $10.00`, `MAX_CONTRACTS = 10`
- Strength multipliers: WEAK=0.6, MODERATE=1.0, STRONG=1.5
- Asset multipliers: BTC=1.0, ETH=1.25, SOL=0.75
- Live .env: `KELLY_FRACTION=0.625` (2.5x default), `BANKROLL_OVERRIDE=$20`

### Execution Flow (`execution/executor.py`)

1. **Kelly sizing** — determines contracts
2. **Fresh pricing** — re-fetches ask from WebSocket cache for taker orders (max 5s old)
3. **Slippage buffer** — adds `TAKER_SLIPPAGE_BUFFER` ($0.03) to taker entry price for fill guarantee
4. **Edge re-check** — aborts if `entry_price >= win_prob` (edge evaporated)
5. **Reserve ticker** — `risk.record_fill()` before `await` to prevent duplicate orders
6. **Place order** — `client.place_order()` with retry
7. **Poll fills** — housekeeping polls `GET /portfolio/orders/{id}`, logs to DB on first fill

### Exit Logic (`main.py:1481-1529`)

Two priority-ordered exit rules are active:

1. **Time exit**: If the position was entered with `>90s` remaining AND the window now has `<30s` remaining, sell at `best_bid - $0.01`. This locks in the momentum gain before the binary settlement coin flip.
2. **Stop-loss (Fallback)**: A fallback stop-loss exit rule is active for all entries (especially beneficial for late entries that are ineligible for time exits). It triggers when the unrealized loss per contract is `>= max(exit_stop_loss, entry_price * exit_stop_drawdown)`.
   - `exit_stop_loss` is configured to `$0.10` absolute loss per contract floor.
   - `exit_stop_drawdown` is configured to `0.60` (60% drawdown of the entry price).
   - Parameter sweeps on 2026-05-23 showed this specific configuration significantly improves absolute backtest PnL (+$74,538 vs $72,867) by cutting catastrophic losses on late entries and recycling capital, even with a minor win rate reduction.

### Settlement (`main.py:1454-1478`)

Live mode polls Kalshi's REST API `GET /markets/{ticker}` for status `"determined"` or `"settled"`. Uses the official `result` field ("yes" or "no"). PnL formula:

```
won = (result == side)  # YES buys win if result=="yes", NO buys win if result=="no"
payout = $1 - entry_price if won else -entry_price
pnl = payout * contracts - entry_fee
```

### Complete Risk Gate Stack (`risk/manager.py`)

1. **Kill switch** — file-based emergency stop
2. **Daily loss limit** — halts if daily PnL <= -$limit
3. **Per-side loss limit** — 30-min cooldown on a side after losing $limit
4. **Locked side** — exactly ONE position per ticker per window (blocks all re-entry)
5. **Max concurrent** — max 3 open positions total
6. **Crossed book** — logs warning, does NOT veto (legitimate Kalshi state)

### Per-Asset Configuration (`strategy/asset_config.py`)

| | BTC | ETH | SOL |
|---|---|---|---|
| edge_threshold | 0.06 | 0.05 | 0.08 |
| min/max price | 0.35/0.80 | 0.30/0.85 | 0.40/0.75 |
| sizing_multiplier | 1.0 | 1.25 | 0.75 |
| maker_horizon | 80s | 100s | 60s |
| momentum_min/max | 30/480 | 25/480 | 35/450 |

Live `.env` overrides: `EDGE_THRESHOLD=0.04`, `MIN_TRADE_PRICE=0.25`, `MAX_TRADE_PRICE=0.85`, `LOGISTIC_K=200`, `MAKER_FIRST=false`, `KELLY_FRACTION=0.625`. All per-asset defaults are overridden by explicit `.env` values (when non-None).

---

## 2. Database Schema (`trades.db` — 1.2 GB, 39 days Apr 15 – May 23)

### `trades` — 1,316 rows
Executed and settled trades. Key columns: `timestamp, ticker, symbol, strategy, side, contracts, price, edge, net_edge, pnl, fees, route, exit_reason, client_order_id`.

PAPER- prefixed `order_id` = paper mode simulation. UUID `order_id` = live mode.

### `price_ticks` — 1,478,839 rows (sampled ~5s)
Coinbase mid-market price. Columns: `timestamp, symbol, price`. Indexed on `(symbol, timestamp)`.

### `window_snapshots` — 1,125,769 rows (sampled ~5s)
**The most important backtest table.** Captures the exact state the strategy saw at each decision point: `timestamp, ticker, symbol, seconds_remaining, open_price, current_price, price_change_pct, kalshi_yes_ask, kalshi_yes_bid, kalshi_no_bid, real_prob, dynamic_k, yes_depth, no_depth, momentum_60s`.

### `orderbook_snapshots` — 1,125,769 rows
Full orderbook state: `timestamp, ticker, symbol, best_yes_ask, best_yes_bid, best_no_ask, best_no_bid, yes_depth, no_depth, spread`.

### `strategy_evals` — 1,125,769 rows
Strategy output at each snapshot: `timestamp, ticker, symbol, strategy, seconds_remaining, price_change_pct, kalshi_yes_price, real_prob, edge, net_edge, signal_side, action, reason`.

### `market_events` — 6,580 rows
Window open/close events with ground truth: `timestamp, ticker, symbol, event_type, open_time, close_time, open_price, close_price, result`. `result` = "up" or "down" — determined by Kalshi settlement via `close >= floor_strike`. 6,578 completed windows.

### `signals` — 1,916,090 rows
All signal firings (trades + skips): `timestamp, ticker, symbol, strategy, side, edge, net_edge, kalshi_price, real_prob, seconds_remaining, action, reason`.

### `window_analyses` — 6,032 rows
Post-window AI analysis: `timestamp, symbol, window_open, window_close, open_price, close_price, price_change_pct, result, signals_count, trades_count, paper_pnl, ai_commentary, ai_model`.

---

## 3. The Backtester (`/root/kalshi-backtest/`)

### Architecture

The backtester is a **discrete-event simulator** that replays `trades.db` in strict chronological order. It reads 4 tables (`price_ticks`, `window_snapshots`, `orderbook_snapshots`, `market_events`) merged via `heapq.merge` into a single event stream.

The strategy code is **NOT re-implemented** — it imports and calls the exact same `evaluate_momentum()` and `evaluate_lwm()` functions from the live bot. The only modification is `BacktestWindowState.seconds_remaining`, which replaces `datetime.now()` with the engine's `simulated_now` clock.

### Execution Simulation

- **Taker**: fills at current ask, charged 7% fee, ~100-120ms latency
- **Maker**: fills when book crosses, charged 1.75% fee, ~90s timeout then promotes to taker
- **Exits**: sells at current bid price (assumed infinite liquidity)
- **Slippage**: no adverse slippage modeled — fills at exact limit/ask price

### Caveats (from README)
- Risk manager NOT modeled → ~10x more trades than live
- Maker fill is optimistic (no queue contention)
- Sells assume infinite bid liquidity
- Treat absolute PnL as directional, not exact

### Configurable Exit Policies
The backtester supports priority-ordered exit checks (only first to trigger fires):

| Priority | Rule | Configurable Parameters |
|---|---|---|
| 1 | `time_exit` | seconds_remaining threshold, min entry time |
| 2 | `take_profit` | gain % threshold, conviction max |
| 3 | `conviction_stop` | loss % threshold, opposite side bid threshold |
| 4 | `stop_loss` | drawdown fraction, absolute stop |
| 5 | `edge_gone` | consecutive negative-edge readings |

### Running It
```bash
PYTHONPATH=src .venv/bin/python scripts/ab_exit_policies.py    # A/B exit comparison
PYTHONPATH=src .venv/bin/python scripts/validate_production.py  # replay 306 settled trades
PYTHONPATH=src .venv/bin/python scripts/validate_exit_logic.py  # HOLD vs SCALED exit analysis
```
Uses `/root/kalshi-bot/kalshi-bot-v2/.venv`. Opens `trades.db` read-only.

### Data Volume Summary

| Table | Rows | Time Span |
|---|---|---|
| price_ticks | 1,478,839 | Apr 15 – May 23 (39 days) |
| window_snapshots | 1,125,769 | same |
| orderbook_snapshots | 1,125,769 | same |
| strategy_evals | 1,125,769 | same |
| completed windows | 6,578 | same |
| signals (all) | 1,916,090 | same |
| settled trades | 1,316 | same |