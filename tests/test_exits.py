import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from kalshi_bot.main import _evaluate_exits
from kalshi_bot.execution.executor import TrackedOrder, OrderState
from kalshi_bot.strategy.signals import Signal, Side, StrategyName
from kalshi_bot.data.window_tracker import WindowState
from kalshi_bot.models.market import OrderBook, OrderBookLevel


def _signal(
    ticker: str = "KXBTC15M-TEST",
    side: Side = Side.NO,
    price: Decimal = Decimal("0.29"),
    seconds_remaining: int = 120,
) -> Signal:
    return Signal(
        timestamp=datetime.now(timezone.utc) - timedelta(seconds=900 - seconds_remaining),
        strategy=StrategyName.MOMENTUM,
        ticker=ticker,
        symbol="BTC",
        side=side,
        edge=Decimal("0.10"),
        net_edge=Decimal("0.08"),
        kalshi_price=price,
        real_prob=0.3,
        seconds_remaining=seconds_remaining,
    )


def _orderbook(
    ticker: str = "KXBTC15M-TEST",
    yes_bid: Decimal = Decimal("0.40"),
    no_bid: Decimal = Decimal("0.55"),
) -> OrderBook:
    return OrderBook(
        ticker=ticker,
        yes_levels=[OrderBookLevel(price=yes_bid, quantity=100)],
        no_levels=[OrderBookLevel(price=no_bid, quantity=100)],
    )


def _ws_feed(ticker: str, book: OrderBook | None) -> MagicMock:
    """Mock ws_feed whose get_orderbook returns (book, now) or None."""
    feed = MagicMock()
    if book is not None:
        feed.get_orderbook.return_value = (book, datetime.now(timezone.utc))
    else:
        feed.get_orderbook.return_value = None
    return feed


# ---------------------------------------------------------------------------
# time_exit tests — core of the exit strategy
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_time_exit_fires_in_final_30s() -> None:
    """Regression: time_exit must fire when window is in its last 30 seconds.

    The critical case: signal entered at seconds_remaining=120 (>90 eligible),
    and the window close is now 15 seconds away (<30s threshold).
    """
    # Signal created 885 seconds into the window (15s from close)
    sig = _signal(seconds_remaining=120)
    # Adjust timestamp so that close_time is 15s from now
    close_time = datetime.now(timezone.utc) + timedelta(seconds=15)
    sig.timestamp = close_time - timedelta(seconds=sig.seconds_remaining)

    order = TrackedOrder(signal=sig, order_id="TEST-time-exit", contracts=4, price=Decimal("0.45"))
    order.state = OrderState.FILLED

    executor = AsyncMock()
    executor.filled_orders = [order]
    executor.exit_position.return_value = (True, [])

    book = _orderbook(ticker=sig.ticker, yes_bid=Decimal("0.40"), no_bid=Decimal("0.55"))
    ws_feed = _ws_feed(sig.ticker, book)

    await _evaluate_exits(executor=executor, ws_feed=ws_feed, alerter=None, symbol="BTC")

    executor.exit_position.assert_called_once()
    call_kwargs = executor.exit_position.call_args
    assert call_kwargs.kwargs.get("exit_reason") == "time_exit"


@pytest.mark.asyncio
async def test_time_exit_fires_after_window_rolled() -> None:
    """Key regression: time_exit must fire even when ws_feed is from old ticker.

    The old code used window.ticker from the tracker, which switches to the
    new ticker before the old window's orderbook goes stale. This test
    simulates that: get_orderbook is called with the ORDER's ticker (old),
    not the new window's ticker.
    """
    sig = _signal(ticker="KXBTC15M-OLD-TICKER", seconds_remaining=120)
    close_time = datetime.now(timezone.utc) + timedelta(seconds=10)
    sig.timestamp = close_time - timedelta(seconds=sig.seconds_remaining)

    order = TrackedOrder(signal=sig, order_id="TEST-rolled", contracts=2, price=Decimal("0.35"))
    order.state = OrderState.FILLED

    executor = AsyncMock()
    executor.filled_orders = [order]
    executor.exit_position.return_value = (True, [])

    # ws_feed returns the old ticker's orderbook — the current window has
    # already rolled to a new ticker but the old book is still in cache
    book = _orderbook(ticker="KXBTC15M-OLD-TICKER", yes_bid=Decimal("0.38"), no_bid=Decimal("0.58"))
    ws_feed = _ws_feed("KXBTC15M-OLD-TICKER", book)

    await _evaluate_exits(executor=executor, ws_feed=ws_feed, alerter=None, symbol="BTC")

    executor.exit_position.assert_called_once()
    assert executor.exit_position.call_args.kwargs.get("exit_reason") == "time_exit"


@pytest.mark.asyncio
async def test_time_exit_skipped_mid_window() -> None:
    """time_exit must NOT fire when there is plenty of time left (>30s)."""
    sig = _signal(seconds_remaining=120)
    # Close time is 5 minutes away — no exit needed
    close_time = datetime.now(timezone.utc) + timedelta(minutes=5)
    sig.timestamp = close_time - timedelta(seconds=sig.seconds_remaining)

    order = TrackedOrder(signal=sig, order_id="TEST-midwin", contracts=4, price=Decimal("0.45"))
    order.state = OrderState.FILLED

    executor = AsyncMock()
    executor.filled_orders = [order]
    executor.exit_position.return_value = (True, [])

    book = _orderbook(ticker=sig.ticker)
    ws_feed = _ws_feed(sig.ticker, book)

    await _evaluate_exits(executor=executor, ws_feed=ws_feed, alerter=None, symbol="BTC")

    executor.exit_position.assert_not_called()


@pytest.mark.asyncio
async def test_time_exit_skipped_for_late_entry() -> None:
    """Entries placed in the final 90s must NOT time_exit — held to settlement.

    The condition is order.signal.seconds_remaining > 90. Late entries skip it.
    """
    sig = _signal(seconds_remaining=60)  # entered too late
    close_time = datetime.now(timezone.utc) + timedelta(seconds=15)
    sig.timestamp = close_time - timedelta(seconds=sig.seconds_remaining)

    order = TrackedOrder(signal=sig, order_id="TEST-late-entry", contracts=4, price=Decimal("0.45"))
    order.state = OrderState.FILLED

    executor = AsyncMock()
    executor.filled_orders = [order]
    executor.exit_position.return_value = (True, [])

    book = _orderbook(ticker=sig.ticker)
    ws_feed = _ws_feed(sig.ticker, book)

    await _evaluate_exits(executor=executor, ws_feed=ws_feed, alerter=None, symbol="BTC")

    executor.exit_position.assert_not_called()


@pytest.mark.asyncio
async def test_time_exit_skipped_when_orderbook_gone() -> None:
    """If orderbook is None (feed gone), exit is skipped — not crashed."""
    sig = _signal(seconds_remaining=120)
    close_time = datetime.now(timezone.utc) + timedelta(seconds=15)
    sig.timestamp = close_time - timedelta(seconds=sig.seconds_remaining)

    order = TrackedOrder(signal=sig, order_id="TEST-no-ob", contracts=2, price=Decimal("0.40"))
    order.state = OrderState.FILLED

    executor = AsyncMock()
    executor.filled_orders = [order]
    executor.exit_position.return_value = (True, [])

    ws_feed = _ws_feed(sig.ticker, None)  # no orderbook

    await _evaluate_exits(executor=executor, ws_feed=ws_feed, alerter=None, symbol="BTC")

    executor.exit_position.assert_not_called()


# ---------------------------------------------------------------------------
# stop_loss and take_profit disabled tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_take_profit_disabled_holds_winning_position() -> None:
    """Take-profit was disabled 2026-05-23 — a winner mid-window must HOLD."""
    sig = _signal(price=Decimal("0.29"), side=Side.NO, seconds_remaining=120)
    close_time = datetime.now(timezone.utc) + timedelta(minutes=5)
    sig.timestamp = close_time - timedelta(seconds=sig.seconds_remaining)

    order = TrackedOrder(signal=sig, order_id="TEST-tp-hold", contracts=10, price=Decimal("0.29"))
    order.state = OrderState.FILLED

    executor = AsyncMock()
    executor.filled_orders = [order]
    executor.exit_position.return_value = (True, [])

    book = _orderbook(ticker=sig.ticker, yes_bid=Decimal("0.40"), no_bid=Decimal("0.55"))
    ws_feed = _ws_feed(sig.ticker, book)

    await _evaluate_exits(executor=executor, ws_feed=ws_feed, alerter=None, symbol="BTC")

    executor.exit_position.assert_not_called()


@pytest.mark.asyncio
async def test_stop_loss_disabled_holds_through_drawdown() -> None:
    """Stop_loss was reverted 2026-05-24 — a losing position must HOLD."""
    sig = _signal(price=Decimal("0.50"), side=Side.NO, seconds_remaining=60)
    close_time = datetime.now(timezone.utc) + timedelta(minutes=5)
    sig.timestamp = close_time - timedelta(seconds=sig.seconds_remaining)

    order = TrackedOrder(signal=sig, order_id="TEST-sl-hold", contracts=10, price=Decimal("0.50"))
    order.state = OrderState.FILLED

    executor = AsyncMock()
    executor.filled_orders = [order]
    executor.exit_position.return_value = (True, [])

    # NO bid is 0.19 — old stop_loss would have fired (loss 0.31 > threshold)
    book = _orderbook(ticker=sig.ticker, yes_bid=Decimal("0.80"), no_bid=Decimal("0.19"))
    ws_feed = _ws_feed(sig.ticker, book)

    await _evaluate_exits(executor=executor, ws_feed=ws_feed, alerter=None, symbol="BTC")

    executor.exit_position.assert_not_called()


@pytest.mark.asyncio
async def test_stop_loss_disabled_yes_side() -> None:
    """YES-side mirror: stop_loss disabled, losing YES must HOLD."""
    sig = _signal(price=Decimal("0.60"), side=Side.YES, seconds_remaining=60)
    close_time = datetime.now(timezone.utc) + timedelta(minutes=5)
    sig.timestamp = close_time - timedelta(seconds=sig.seconds_remaining)

    order = TrackedOrder(signal=sig, order_id="TEST-sl-yes", contracts=10, price=Decimal("0.60"))
    order.state = OrderState.FILLED

    executor = AsyncMock()
    executor.filled_orders = [order]
    executor.exit_position.return_value = (True, [])

    book = _orderbook(ticker=sig.ticker, yes_bid=Decimal("0.23"), no_bid=Decimal("0.76"))
    ws_feed = _ws_feed(sig.ticker, book)

    await _evaluate_exits(executor=executor, ws_feed=ws_feed, alerter=None, symbol="BTC")

    executor.exit_position.assert_not_called()


@pytest.mark.asyncio
async def test_time_exit_fires_over_stop_loss_check() -> None:
    """time_exit must fire when eligible, regardless of drawdown state."""
    sig = _signal(price=Decimal("0.50"), side=Side.NO, seconds_remaining=120)
    close_time = datetime.now(timezone.utc) + timedelta(seconds=15)
    sig.timestamp = close_time - timedelta(seconds=sig.seconds_remaining)

    order = TrackedOrder(signal=sig, order_id="TEST-priority", contracts=10, price=Decimal("0.50"))
    order.state = OrderState.FILLED

    executor = AsyncMock()
    executor.filled_orders = [order]
    executor.exit_position.return_value = (True, [])

    # Position is losing badly — time_exit should still fire
    book = _orderbook(ticker=sig.ticker, yes_bid=Decimal("0.84"), no_bid=Decimal("0.15"))
    ws_feed = _ws_feed(sig.ticker, book)

    await _evaluate_exits(executor=executor, ws_feed=ws_feed, alerter=None, symbol="BTC")

    executor.exit_position.assert_called_once()
    assert executor.exit_position.call_args.kwargs.get("exit_reason") == "time_exit"
