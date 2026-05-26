# V2 Implementation Plan

## Instructions for the Coding Agent

You are building the V2 Kalshi trading bot in `/home/psypolatic/kalshi/v2/`.

**Rules:**
- You CAN read files from `/home/psypolatic/kalshi/new/` (V1 bot) and `/home/psypolatic/kalshi/thebacktester/btc_backtester/` (backtester) for reference.
- You MUST NOT edit any files outside `/home/psypolatic/kalshi/v2/`.
- The git repo is already initialized in `/home/psypolatic/kalshi/v2/` on branch `main`.
- Each phase below is ONE commit. Commit after completing each phase.
- All quality gates (`mypy --strict`, `ruff check`, `pytest`) must pass after every commit.
- Use `from __future__ import annotations` at the top of every Python file.
- Read the CLAUDE.md file first for full project context.

---

## Phase 0: Project Skeleton

**Commit:** `Initial project skeleton: pyproject.toml, .env.example, __init__.py files`

### Files to create:

**`pyproject.toml`**
```toml
[project]
name = "kalshi-bot-v2"
version = "2.0.0"
requires-python = ">=3.10"
dependencies = [
    "httpx>=0.27",
    "websockets>=12.0,<14.0",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "structlog>=24.0",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "mypy>=1.10",
    "ruff>=0.4",
]

[tool.mypy]
strict = true
plugins = ["pydantic.mypy"]

[tool.ruff]
target-version = "py310"

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

**`.env.example`**
```
KALSHI_API_KEY=your_api_key
KALSHI_PRIVATE_KEY_PATH=./kalshi_key.pem
KALSHI_ENV=demo
TRADING_MODE=paper
SYMBOLS=BTC
EDGE_THRESHOLD=0.06
MOMENTUM_MIN_TIME=30
MOMENTUM_MAX_TIME=480
MIN_TRADE_PRICE=0.35
MAX_TRADE_PRICE=0.80
MAKER_FIRST=true
MAKER_FILL_HORIZON_S=90
LOGISTIC_K=150.0
EXIT_STOP_LOSS=0.10
DAILY_LOSS_LIMIT=25.0
MAX_PER_TRADE=25.0
MAX_CONCURRENT_POSITIONS=3
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DISCORD_WEBHOOK_URL=
OPENROUTER_API_KEY=
OPENROUTER_MODEL=minimax/minimax-m2.7
DASHBOARD_PORT=8080
```

**`.gitignore`**
```
__pycache__/
*.pyc
.env
*.db
*.pem
KILL_SWITCH
live_state.json
*.log
.mypy_cache/
.ruff_cache/
.pytest_cache/
```

Create empty `__init__.py` in:
- `src/kalshi_bot/`
- `src/kalshi_bot/client/`
- `src/kalshi_bot/data/`
- `src/kalshi_bot/strategy/`
- `src/kalshi_bot/execution/`
- `src/kalshi_bot/risk/`
- `src/kalshi_bot/alerts/`
- `src/kalshi_bot/analysis/`
- `src/kalshi_bot/models/`
- `tests/`

---

## Phase 1: Infrastructure (copy from V1 with fixes)

**Commit:** `Port V1 infrastructure: clients, models, feeds, logging, data recorder`

Copy these files from V1 (`/home/psypolatic/kalshi/new/src/kalshi_bot/`). Apply the listed modifications:

### 1A. Copy as-is (no changes needed):
- `client/auth.py` — RSA-PSS signing
- `client/coinbase.py` — Coinbase WebSocket feed
- `client/kalshi_ws.py` — Kalshi orderbook WebSocket
- `client/openrouter.py` — OpenRouter LLM client
- `models/price.py` — PriceTick model
- `data/window_tracker.py` — WindowTracker + WindowState (momentum_60s already built in)
- `data/recorder.py` — DataRecorder for backtesting data
- `strategy/fees.py` — fee calculations
- `strategy/probability.py` — logistic model (keep for edge calc, used by momentum strategy)
- `logging_config.py` — structlog setup
- `analysis/window_analyzer.py` — AI window analysis

### 1B. Copy with modifications:

**`client/kalshi.py`** — Copy from V1. No functional changes needed.

**`models/market.py`** — Copy from V1, then ADD these three properties to the `OrderBook` class after `best_yes_ask`:

```python
@property
def best_no_bid(self) -> Decimal | None:
    """Best (highest) NO bid price."""
    if not self.no_levels:
        return None
    return max(lv.price for lv in self.no_levels)

@property
def best_no_ask(self) -> Decimal | None:
    """Best NO ask = 1 - best YES bid (binary contract)."""
    if not self.yes_levels:
        return None
    return Decimal("1") - max(lv.price for lv in self.yes_levels)

@property
def orderbook_imbalance(self) -> float:
    """Raw OBI: yes_depth - no_depth. Positive = bullish."""
    yes_vol = sum(lv.quantity for lv in self.yes_levels)
    no_vol = sum(lv.quantity for lv in self.no_levels)
    return float(yes_vol - no_vol)
```

**`strategy/signals.py`** — Copy from V1, then:
1. Add `MOMENTUM = "momentum"` to `StrategyName` enum (keep old values for DB compat)
2. Add two fields to `Signal`:
   ```python
   route: str = "taker"           # "maker" or "taker"
   taker_price: Decimal | None = None  # fallback price for maker timeout
   ```

---

## Phase 2: Config

**Commit:** `V2 config: momentum strategy settings, maker-first execution`

**`config.py`** — Write NEW, do NOT copy V1. The V1 config has dead settings and wrong defaults.

```python
"""Application configuration via environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Bot configuration loaded from .env file or environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Kalshi API
    kalshi_api_key: str = Field(description="Kalshi API key")
    kalshi_private_key_path: Path = Field(description="Path to RSA private key PEM file")
    kalshi_env: str = Field(default="demo", pattern=r"^(demo|prod)$")

    # Trading mode
    trading_mode: str = Field(default="paper", pattern=r"^(paper|live)$")

    # Risk limits
    daily_loss_limit: float = Field(default=25.0)
    max_per_trade: float = Field(default=25.0)
    max_concurrent_positions: int = Field(default=3)

    # Momentum strategy
    edge_threshold: float = Field(default=0.06)
    momentum_min_time: int = Field(default=30)
    momentum_max_time: int = Field(default=480)
    min_trade_price: float = Field(default=0.35)
    max_trade_price: float = Field(default=0.80)
    logistic_k: float = Field(default=150.0)
    symbols: str = Field(default="BTC")

    # Maker-first execution
    maker_first: bool = Field(default=True)
    maker_fill_horizon_s: int = Field(default=90)

    # Exit management
    exit_stop_loss: float = Field(default=0.10)

    # Telegram alerts (optional)
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")

    # OpenRouter AI Analysis
    openrouter_api_key: str = Field(default="")
    openrouter_model: str = Field(default="minimax/minimax-m2.7")

    # Dashboard
    dashboard_port: int = Field(default=8080)

    # Discord webhook (optional)
    discord_webhook_url: str = Field(default="")

    @property
    def rest_base_url(self) -> str:
        if self.kalshi_env == "demo":
            return "https://demo-api.kalshi.co/trade-api/v2"
        return "https://api.elections.kalshi.com/trade-api/v2"

    @property
    def ws_base_url(self) -> str:
        if self.kalshi_env == "demo":
            return "wss://demo-api.kalshi.co/trade-api/ws/v2"
        return "wss://api.elections.kalshi.com/trade-api/ws/v2"
```

---

## Phase 3: Strategy Module

**Commit:** `New momentum+OBI strategy: dual-gate with maker-first pricing`

**`strategy/momentum.py`** — Write NEW. This is the core of V2. Reference the backtester at `/home/psypolatic/kalshi/thebacktester/btc_backtester/src/simulator.py` for the logic.

The strategy function `evaluate_momentum()` must:
1. Check `seconds_remaining` is within `[min_time, max_time]`
2. Get `momentum_60s` from the `WindowState` — return `None` if `None` or `0.0`
3. Compute `orderbook_imbalance` from the `OrderBook` (the new property)
4. **Gate: `sign(momentum) == sign(imbalance)`** — return `None` if they disagree
5. Determine side: positive -> YES, negative -> NO
6. Compute `est_prob`: for YES use `estimate_up_probability(...)`, for NO use `1 - estimate_up_probability(...)`
7. Get maker price (`best_yes_bid` for YES, `best_no_bid` for NO) and taker price (`best_yes_ask` for YES, `best_no_ask` for NO)
8. **Try maker first**: compute `edge = est_prob - maker_price - (maker_fee / contracts)`. If `edge >= threshold` and price in bounds, use maker route
9. **Taker fallback**: compute `edge = est_prob - taker_price - (taker_fee / contracts)`. If `edge >= threshold` and price in bounds, use taker route
10. Return `Signal` with `route` and `taker_price` fields set, or `None`

Important: the `contracts` parameter should come from the caller (sizing happens in the executor, but the strategy needs it for per-contract fee math). Default to `1` for the edge gate check in the strategy. The executor will re-check sizing.

See the full function signature and implementation in the CLAUDE.md file.

---

## Phase 4: Risk & Sizing

**Commit:** `V2 risk manager and sizing: simplified, no OBI allocation`

**`risk/sizing.py`** — Copy from V1, change `MAX_CONTRACTS = 10`.

**`risk/manager.py`** — Copy from V1, then simplify:
1. Remove `_obi_allocation` field and the `available_bankroll_for_strategy()` method.
2. Remove `_committed` dict (was for per-strategy allocation tracking).
3. Keep: kill switch, daily loss limit, concurrent positions, cooldown, locked sides.
4. The `_locked_sides` mechanism must block ALL re-entry per ticker (not just opposite side). This fixes V1 bug where multiple trades entered the same window. Check: in `_check_locked_side()`, if the ticker has ANY locked side, block the trade.
5. Remove `_check_min_edge()` — edge gating is done in the strategy, not in risk manager.

---

## Phase 5: Executor with Maker-First Logic

**Commit:** `Maker-first executor: place at bid, timeout, promote to taker`

**`execution/executor.py`** — Copy from V1, then make these changes:

### 5A. Update `TrackedOrder`:
Add fields:
```python
self.route: str = signal.route if hasattr(signal, 'route') else "taker"
self.taker_price: Decimal | None = getattr(signal, 'taker_price', None)
self.maker_timeout: int = 90  # configurable via signal
```

### 5B. Add `promote_to_taker()` method:
```python
async def promote_to_taker(self) -> None:
    """Cancel maker orders past their fill horizon and re-place as taker."""
    now = time.monotonic()
    to_promote: list[str] = []
    for oid, order in self._orders.items():
        if order.state != OrderState.PENDING:
            continue
        if order.route != "maker":
            continue
        if order.taker_price is None:
            continue
        if now - order.placed_at <= order.maker_timeout:
            continue
        to_promote.append(oid)

    for oid in to_promote:
        order = self._orders[oid]
        if oid.startswith("PAPER-"):
            # Paper mode: just cancel the maker, log the timeout
            order.state = OrderState.CANCELLED
            self._risk.record_settlement(order.signal.ticker, Decimal("0"))
            self._update_trade_pnl(oid, Decimal("0"))
            logger.info("[PAPER] Maker timeout %s — cancelled (no taker re-entry in paper)", oid)
            continue

        try:
            await self._client.cancel_order(oid)
            order.state = OrderState.CANCELLED
            logger.info("Maker timeout — cancelled %s", oid)
        except Exception:
            # Cancel failed -> probably already filled
            order.state = OrderState.FILLED
            order.fill_time = now
            self._risk.record_fill(order.signal.ticker, side=order.signal.side.value)
            logger.info("Maker order %s already filled on cancel attempt", oid)
            continue

        # Place taker order
        try:
            taker_resp = await self._client.place_order(
                ticker=order.signal.ticker,
                action="buy",
                side=order.signal.side.value,
                price_dollars=order.taker_price,
                count=order.contracts,
            )
            new_oid = str(taker_resp["order_id"])
            new_order = TrackedOrder(
                signal=order.signal,
                order_id=new_oid,
                contracts=order.contracts,
                price=order.taker_price,
            )
            new_order.route = "taker_promoted"
            self._orders[new_oid] = new_order
            self._log_trade(order.signal, new_oid, order.contracts, order.taker_price, None)
            logger.info("Promoted to taker: %s -> %s @ %s", oid, new_oid, order.taker_price)
        except Exception:
            logger.exception("Taker promotion failed for %s", oid)
            self._risk.record_settlement(order.signal.ticker, Decimal("0"))
```

### 5C. Add `route` column to trades table:
In `_init_db()`, add a `route` column to the trades table:
```sql
CREATE TABLE IF NOT EXISTS trades (
    ... existing columns ...,
    route TEXT DEFAULT 'taker'
)
```
And add the migration for existing DBs:
```python
with contextlib.suppress(sqlite3.OperationalError):
    conn.execute("ALTER TABLE trades ADD COLUMN route TEXT DEFAULT 'taker'")
```

### 5D. Update `_log_trade()`:
Include the route in the INSERT.

### 5E. Fix V1 exit bug:
In `exit_position()`, change the sell price from `sell_price - Decimal("0.01")` to just `sell_price` (best bid). The $0.01 haircut was causing guaranteed slippage.

---

## Phase 6: Main Loop

**Commit:** `Wire momentum strategy into main loop with maker-first execution`

**`main.py`** — This is the most complex file. Copy V1's structure but replace the strategy layer.

### Key changes from V1:

1. **Imports**: Replace `price_lag`, `consensus`, `orderbook_imbalance` imports with `from kalshi_bot.strategy.momentum import evaluate_momentum`

2. **Strategy evaluation** (in `_trade_cycle()`): Replace the ~50-line block that calls `evaluate_price_lag`, `evaluate_consensus`, `evaluate_orderbook_imbalance` with a single call:
   ```python
   signal = evaluate_momentum(
       window,
       ticker,
       orderbook,
       edge_threshold=settings.edge_threshold,
       k=settings.logistic_k,
       min_time=settings.momentum_min_time,
       max_time=settings.momentum_max_time,
       min_price=settings.min_trade_price,
       max_price=settings.max_trade_price,
       maker_first=settings.maker_first,
   )
   ```

3. **Add maker promotion** after `check_pending_fills()`:
   ```python
   await executor.promote_to_taker()
   ```

4. **Simplify bankroll**: Remove `available_bankroll_for_strategy()` — pass `bankroll` directly to `executor.submit()`.

5. **Remove dead code**: No more `obi_signal`, `consensus`, `block_model_confident`, or `allowed_sides` logic.

6. **Keep everything else from V1**: Window analysis, settlements, exits, data recording, live state JSON, telegram commands, adaptive polling.

### Keep these V1 features exactly:
- `_drain_prices()` — Coinbase tick processing
- `_check_settlements()` — market settlement detection
- `_evaluate_exits()` — exit on edge gone, stop loss, time. Change hysteresis from 2 to 3.
- `_settle_paper_positions()` — paper mode settlement
- `_run_window_analysis()` — AI analysis of closed windows
- Window analysis rate limiting (1 per hour per symbol)
- `DataRecorder` integration for backtesting data
- `live_state.json` output for dashboard SSE
- Shutdown signal handling
- WS orderbook feed with staleness fallback to REST

---

## Phase 7: Telegram & Dashboard

**Commit:** `Port Telegram commands and web dashboard for V2`

### 7A. Telegram (`alerts/telegram.py`)

Copy from V1 (`/home/psypolatic/kalshi/new/src/kalshi_bot/alerts/telegram.py`), then update:

1. **`/config` command**: Change to show V2 settings:
   ```python
   f"Edge threshold: {settings.edge_threshold:.2f}\n"
   f"Time window: {settings.momentum_min_time}-{settings.momentum_max_time}s\n"
   f"Price range: ${settings.min_trade_price:.2f}-${settings.max_trade_price:.2f}\n"
   f"Maker first: {'ON' if settings.maker_first else 'off'}\n"
   f"Maker fill horizon: {settings.maker_fill_horizon_s}s\n"
   ```

2. **`_SETTABLE` dict**: Replace with V2 keys:
   ```python
   _SETTABLE: dict[str, type] = {
       "edge_threshold": float,
       "exit_stop_loss": float,
       "min_time": int,
       "max_time": int,
       "logistic_k": float,
       "symbols": str,
       "min_price": float,
       "max_price": float,
       "maker_first": bool,
       "maker_fill_horizon_s": int,
   }
   ```

3. **`_SETTING_MAP` dict**: Update to match new Settings fields:
   ```python
   _SETTING_MAP: dict[str, str] = {
       "edge_threshold": "edge_threshold",
       "exit_stop_loss": "exit_stop_loss",
       "min_time": "momentum_min_time",
       "max_time": "momentum_max_time",
       "logistic_k": "logistic_k",
       "symbols": "symbols",
       "min_price": "min_trade_price",
       "max_price": "max_trade_price",
       "maker_first": "maker_first",
       "maker_fill_horizon_s": "maker_fill_horizon_s",
   }
   ```

4. **`trade_placed` alert**: Include `route` in the alert text:
   ```python
   f"Route: {signal.route}\n"
   ```

5. **`/stats` command**: Add maker fill rate tracking. After the existing stats query, add:
   ```sql
   SELECT route, COUNT(*) as cnt,
          SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins
   FROM trades WHERE pnl IS NOT NULL GROUP BY route
   ```
   Display as: `Maker: 50 (62% WR) | Taker: 10 (50% WR)`

6. **New `/maker` command** (optional but valuable): Show maker fill stats:
   - Maker attempts vs fills vs timeouts
   - Average time to fill
   - Fill rate percentage

### 7B. Dashboard (`dashboard.py` and `dashboard.html`)

Copy both files from V1 (`/home/psypolatic/kalshi/new/src/kalshi_bot/dashboard.py` and `dashboard.html`).

Changes to `dashboard.py`:
- Update any V1 strategy references in API responses (e.g., if it shows "price_lag" strategy name, make sure "momentum" works too)
- The dashboard reads from SQLite so it will mostly work as-is since the table schemas are the same

Changes to `dashboard.html`:
- Update the page title from "Kalshi Trading Bot" to "Kalshi V2 — Momentum Bot" (or similar)
- If the UI shows strategy-specific info (like "Price Lag" labels), update to "Momentum"

---

## Phase 8: Tests

**Commit:** `Comprehensive test suite for momentum strategy, executor, and risk`

### `tests/test_momentum.py` — strategy tests

```python
"""Tests for momentum + OBI strategy."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from kalshi_bot.data.window_tracker import WindowState
from kalshi_bot.models.market import OrderBook, OrderBookLevel
from kalshi_bot.strategy.momentum import evaluate_momentum
from kalshi_bot.strategy.signals import Side


def _make_window(
    price_change_pct: float, seconds_remaining: int, momentum: float | None = 0.001,
) -> WindowState:
    """Create a test WindowState."""
    open_price = 100000.0
    current_price = open_price * (1 + price_change_pct)
    ws = WindowState(
        symbol="BTC",
        ticker="KXBTC15M-TEST",
        open_time=datetime.now(timezone.utc),
        close_time=datetime.now(timezone.utc),
        open_price=open_price,
        current_price=current_price,
    )
    # Inject momentum for testing
    if momentum is not None:
        import time
        now = time.time()
        base_price = current_price / (1 + momentum) if momentum != 0 else current_price
        ws.prices_60s.append((now - 60, base_price))
        ws.prices_60s.append((now, current_price))
    return ws


def _make_orderbook(
    yes_bid: float = 0.40, no_bid: float = 0.55,
    yes_qty: int = 100, no_qty: int = 50,
) -> OrderBook:
    """Create a test OrderBook."""
    return OrderBook(
        ticker="KXBTC15M-TEST",
        yes_levels=[OrderBookLevel(price=Decimal(str(yes_bid)), quantity=yes_qty)],
        no_levels=[OrderBookLevel(price=Decimal(str(no_bid)), quantity=no_qty)],
    )
```

Write test cases for:
1. `test_no_signal_when_momentum_zero` — momentum=0 -> None
2. `test_no_signal_when_momentum_none` — momentum=None -> None
3. `test_no_signal_when_signs_disagree` — positive momentum, negative OBI -> None
4. `test_yes_signal_bullish_agreement` — positive momentum + positive OBI -> YES signal
5. `test_no_signal_bearish_agreement` — negative momentum + negative OBI -> NO signal
6. `test_maker_route_preferred` — when maker edge passes, route="maker"
7. `test_taker_fallback` — when maker edge fails but taker passes, route="taker"
8. `test_no_signal_below_edge_threshold` — edge too low -> None
9. `test_no_signal_outside_time_bounds` — too early or too late -> None
10. `test_no_signal_price_out_of_bounds` — price below min or above max -> None
11. `test_taker_price_field_set_for_maker` — maker signal includes taker_price for fallback

### `tests/test_fees.py` — fee calculation tests
```python
def test_taker_fee_at_50_cents():
    assert taker_fee(1, 0.50) == Decimal("0.02")
    assert taker_fee(10, 0.50) == Decimal("0.18")

def test_maker_fee_at_50_cents():
    assert maker_fee(1, 0.50) == Decimal("0.01")
    assert maker_fee(10, 0.50) == Decimal("0.05")
```

### `tests/test_sizing.py` — quarter-Kelly tests
Copy from V1 if they exist. Verify MAX_CONTRACTS=10 works.

### `tests/test_risk.py` — risk manager tests
Test:
- Kill switch blocks trades
- Daily loss limit blocks trades
- Concurrent position limit works
- Cooldown after exit works
- Locked side blocks ALL re-entry (not just opposite side)
- Side locking persists for the window lifetime

---

## Phase 9: Final Cleanup

**Commit:** `Final cleanup: verify all quality gates, update .env.example`

1. Run `PYTHONPATH=src mypy --strict src/` — fix all issues
2. Run `ruff check src/` — fix all issues
3. Run `PYTHONPATH=src pytest tests/ -v` — all tests pass
4. Verify `.env.example` matches all Settings fields
5. Verify CLAUDE.md is accurate

---

## Post-Implementation: Deployment Checklist

After all phases are committed:

1. `git log --oneline` should show 9 clean commits (phases 0-9)
2. Copy `.env.example` to `.env` and fill in API keys
3. Copy private key PEM file
4. Run in paper mode: `PYTHONPATH=src python -m kalshi_bot.main`
5. Monitor via Telegram `/status`, `/stats`, `/window`
6. Track for 100+ trades over 7+ days before considering live mode

## Summary: What V2 Does NOT Have (by design)

- No `price_lag.py` — the broken contrarian strategy is dead
- No `consensus.py` — never worked, always disabled
- No `orderbook_imbalance.py` as separate strategy — OBI is now a gate inside momentum.py
- No `calibrate.py` — grid search for k is irrelevant with momentum approach
- No `block_model_confident` — this was the inverted filter that caused V1 losses
- No `allowed_sides` — V2 trades both sides based on momentum direction
- No `obi_allocation` — there's only one strategy, no allocation needed
- No `min_net_edge` separate from `edge_threshold` — one threshold, post-fee
