# Speed Optimization Plan — Event-Driven Architecture

## Problem

The bot has two real-time WebSocket feeds (Coinbase prices, Kalshi orderbook deltas) but the main loop is poll-based with a 2-second sleep. This means:

- **Worst-case latency: 2 seconds** between a meaningful price/orderbook change and strategy evaluation
- **7-10 REST calls per cycle** that mostly return unchanged data (balance, positions, get_open_markets × 3, settlement checks)
- **~4 req/s** against Kalshi's 20 req/s read limit, wasted on redundant fetches

In the last 60 seconds of a 15-min window, a 0.1% BTC move shifts P(up) from 50% to 72%. A 2-second delay means someone else takes the liquidity at the good price.

## Goal

React to WebSocket events within **<100ms** instead of polling every 2 seconds. Reduce REST calls from ~4/s to ~0.5/s by caching slow-changing data.

## Architecture Change

### Before (poll-based):
```
_poll_and_trade loop:
    _trade_cycle()          # ALL work: REST calls + strategy eval + recording
    asyncio.sleep(2.0)      # wait regardless of what happened
```

### After (event-driven):
```
Two concurrent loops:

FAST LOOP (event-driven, <100ms latency):
    wait for eval_trigger (set by Coinbase tick OR Kalshi delta)
    for each symbol with active window:
        evaluate strategy (using cached orderbook + window state)
        if signal passes risk: submit order

SLOW LOOP (timer-based, every 5s):
    check_pending_fills()
    promote_to_taker()
    cancel_stale()
    check_settlements()
    refresh balance (if stale)
    refresh market tickers (if window changed)
    record data snapshots
    write live_state.json
```

## Implementation

### Step 1: Add eval trigger to feeds

**File: `client/coinbase.py`**

Add an optional `asyncio.Event` parameter. Set it when a tick is processed.

```python
class CoinbaseFeed:
    def __init__(
        self,
        queue: asyncio.Queue[PriceTick],
        products: list[str] | None = None,
        eval_trigger: asyncio.Event | None = None,   # NEW
    ) -> None:
        ...
        self._eval_trigger = eval_trigger

    # In _connect_and_stream, after queue.put_nowait(tick):
    if self._eval_trigger is not None:
        self._eval_trigger.set()
```

**File: `client/kalshi_ws.py`**

Same pattern. Set the trigger on snapshot and delta.

```python
class KalshiOrderbookFeed:
    def __init__(self, settings: Settings, eval_trigger: asyncio.Event | None = None) -> None:
        ...
        self._eval_trigger = eval_trigger

    # At the end of _apply_snapshot and _apply_delta:
    if self._eval_trigger is not None:
        self._eval_trigger.set()
```

### Step 2: Add per-symbol eval throttle

Prevent the strategy from evaluating the same symbol 50 times per second during high-frequency orderbook activity. Use a simple monotonic timestamp check.

```python
MIN_EVAL_INTERVAL_S = 0.2  # 200ms minimum between evals per symbol
_last_eval: dict[str, float] = {}  # symbol -> monotonic time
```

In the fast loop, before evaluating:
```python
now = time.monotonic()
last = _last_eval.get(symbol, 0.0)
if now - last < MIN_EVAL_INTERVAL_S:
    continue
_last_eval[symbol] = now
```

### Step 3: Cache slow-changing data

**Market tickers** — `get_open_markets()` returns the same data for 15 minutes. Cache per-series, refresh only when the cached window's close_time has passed.

Add to `_trade_cycle` / the slow loop:
```python
_market_cache: dict[str, tuple[Market, float]] = {}  # series -> (market, mono_time)
MARKET_CACHE_TTL_S = 30.0  # refresh every 30s, not every 2s

def _get_active_market(series: str) -> Market | None:
    cached = _market_cache.get(series)
    now = time.monotonic()
    if cached is not None:
        market, fetched_at = cached
        if now - fetched_at < MARKET_CACHE_TTL_S and market.close_time > datetime.now(timezone.utc):
            return market
    # Fetch fresh
    markets = await client.get_open_markets(series)
    ...
    _market_cache[series] = (market, now)
    return market
```

**Balance** — Only changes on fills/settlements. Cache it, refresh in the slow loop or after a trade event.

```python
_cached_balance: Decimal = Decimal("0")
_balance_mono: float = 0.0
BALANCE_CACHE_TTL_S = 10.0

# In slow loop:
if time.monotonic() - _balance_mono > BALANCE_CACHE_TTL_S:
    _cached_balance = await client.get_balance()
    _balance_mono = time.monotonic()
```

**Positions** — Same as balance. Refresh every 10s in the slow loop, not every 2s.

### Step 4: Split _trade_cycle into fast and slow paths

**File: `main.py`**

Replace the single `_poll_and_trade` function with two concurrent tasks.

```python
async def _fast_eval_loop(
    tracker: WindowTracker,
    risk: RiskManager,
    executor: Executor,
    settings: Settings,
    shutdown: asyncio.Event,
    eval_trigger: asyncio.Event,
    alerter: ...,
    ws_feed: KalshiOrderbookFeed,
    cached: CachedState,  # holds balance, market tickers, etc.
) -> None:
    """React to WebSocket events. Evaluates strategy within <100ms of a state change."""
    last_eval: dict[str, float] = {}
    last_risk_block: dict[str, str] = {}

    while not shutdown.is_set():
        # Wait for a WS event or 5s timeout (fallback)
        eval_trigger.clear()
        try:
            await asyncio.wait_for(eval_trigger.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass  # periodic fallback eval

        active_symbols = {s.strip() for s in settings.symbols.split(",")}
        now_mono = time.monotonic()

        for symbol in active_symbols:
            # Throttle: max 5 evals/sec per symbol
            if now_mono - last_eval.get(symbol, 0.0) < 0.2:
                continue

            window = tracker.get_window(symbol)
            if window is None:
                continue

            market = cached.get_market(symbol)
            if market is None:
                continue
            ticker = market.ticker

            # Get orderbook from WS cache (no REST call)
            ob_result = ws_feed.get_orderbook(ticker)
            if ob_result is None:
                continue
            orderbook, ob_ts = ob_result
            age = (datetime.now(timezone.utc) - ob_ts).total_seconds()
            if age > ORDERBOOK_STALENESS_S:
                continue  # stale — slow loop will handle REST fallback

            kalshi_yes_price = orderbook.best_yes_ask
            if kalshi_yes_price is None:
                continue

            last_eval[symbol] = now_mono

            # Evaluate strategy (pure computation, no I/O)
            signal = evaluate_momentum(
                window, ticker, orderbook,
                edge_threshold=settings.edge_threshold,
                k=settings.logistic_k,
                min_time=settings.momentum_min_time,
                max_time=settings.momentum_max_time,
                min_price=settings.min_trade_price,
                max_price=settings.max_trade_price,
                maker_first=settings.maker_first,
            )

            if signal is None:
                continue

            try:
                risk.check(signal)
            except RiskVetoError:
                continue

            # Submit order (this IS I/O but it's the trade — we want it fast)
            await executor.submit(signal, cached.balance)
            if alerter:
                await alerter.trade_placed(signal, ...)


async def _slow_housekeeping_loop(
    client: KalshiClient,
    tracker: WindowTracker,
    risk: RiskManager,
    executor: Executor,
    settings: Settings,
    shutdown: asyncio.Event,
    alerter: ...,
    recorder: DataRecorder | None,
    ws_feed: KalshiOrderbookFeed,
    cached: CachedState,
) -> None:
    """Periodic housekeeping: fills, settlements, balance, recording. Every 5s."""
    while not shutdown.is_set():
        # Order lifecycle management
        await executor.check_pending_fills()
        await executor.promote_to_taker()
        await executor.cancel_stale()

        # Settlements
        if executor._dry_run:
            await _settle_paper_positions(executor, tracker, alerter)
        await _check_settlements(client, executor, alerter)

        # Refresh cached data
        await cached.refresh_balance(client)
        await cached.refresh_positions(client, risk)
        await cached.refresh_markets(client, tracker, settings)

        # Exit evaluation (needs current orderbook)
        for symbol in cached.active_symbols:
            ...  # _evaluate_exits logic

        # Data recording
        if recorder:
            ...  # record snapshots

        # Write live state
        _write_live_state(...)

        # Window analysis on close
        for symbol, closed_window in tracker.pop_closed_windows():
            ...

        await asyncio.sleep(5.0)
```

### Step 5: Wire it up in run_bot()

```python
async def run_bot(settings: Settings) -> None:
    ...
    eval_trigger = asyncio.Event()

    feed = CoinbaseFeed(price_queue, eval_trigger=eval_trigger)
    ws_feed = KalshiOrderbookFeed(settings, eval_trigger=eval_trigger)
    cached = CachedState()

    ...

    fast_task = asyncio.create_task(
        _fast_eval_loop(tracker, risk, executor, settings, shutdown, eval_trigger, alerter, ws_feed, cached)
    )
    slow_task = asyncio.create_task(
        _slow_housekeeping_loop(client, tracker, risk, executor, settings, shutdown, alerter, recorder, ws_feed, cached)
    )
    drain_task = asyncio.create_task(
        _drain_prices(price_queue, tracker, shutdown, recorder, eval_trigger)
    )
    ...
```

## What NOT to Change

- **Strategy logic** (`momentum.py`) — unchanged. The function is pure computation, already fast.
- **Risk manager** — unchanged. Pure in-memory checks.
- **Executor** — unchanged. Order placement is the real I/O we want on the fast path.
- **WebSocket feed internals** — already efficient with integer-cent delta application.
- **Coinbase feed** — already streaming. Just add the trigger.

## Expected Impact

| Metric | Before | After |
|--------|--------|-------|
| Eval latency after price change | ~1-2s avg | <100ms |
| REST calls per second | ~4/s | ~0.5/s |
| Strategy evals per minute | ~30 | event-driven (hundreds when active, near-zero when idle) |
| Rate limit utilization | ~20% | ~3% |

## CachedState Helper

Simple class to hold cached slow-changing data with TTL-based refresh:

```python
class CachedState:
    def __init__(self) -> None:
        self.balance: Decimal = Decimal("0")
        self._balance_at: float = 0.0
        self._markets: dict[str, tuple[Market, float]] = {}
        self.active_symbols: set[str] = set()

    async def refresh_balance(self, client: KalshiClient, ttl: float = 10.0) -> None:
        if time.monotonic() - self._balance_at < ttl:
            return
        try:
            self.balance = await client.get_balance()
            self._balance_at = time.monotonic()
        except Exception:
            pass

    async def refresh_markets(self, client: KalshiClient, tracker: WindowTracker, settings: Settings, ttl: float = 30.0) -> None:
        ...

    def get_market(self, symbol: str) -> Market | None:
        ...
```

## Quality Gates

After implementation:
- `PYTHONPATH=src mypy --strict src/` — zero issues
- `ruff check src/` — zero issues
- `PYTHONPATH=src pytest tests/ -v` — all tests pass
- Strategy logic unchanged — no new test cases needed for momentum.py
- Add test for CachedState TTL behavior
- Add test that eval_trigger.set() is called on feed events
