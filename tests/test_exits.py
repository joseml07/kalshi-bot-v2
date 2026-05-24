import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock

from kalshi_bot.main import _evaluate_exits
from kalshi_bot.execution.executor import TrackedOrder, OrderState
from kalshi_bot.strategy.signals import Signal, Side, StrategyName
from kalshi_bot.data.window_tracker import WindowState

def _signal(ticker: str = "KXBTC15M-TEST", side: Side = Side.NO, price: Decimal = Decimal("0.29")) -> Signal:
    return Signal(
        timestamp=datetime.now(timezone.utc),
        strategy=StrategyName.MOMENTUM,
        ticker=ticker,
        symbol="BTC",
        side=side,
        edge=Decimal("0.10"),
        net_edge=Decimal("0.08"),
        kalshi_price=price,
        real_prob=0.3,
        seconds_remaining=120,
    )

def _window(ticker: str = "KXBTC15M-TEST") -> WindowState:
    return WindowState(
        symbol="BTC",
        ticker=ticker,
        open_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        close_time=datetime.now(timezone.utc) + timedelta(minutes=10),
        open_price=100.0,
        current_price=101.0,
    )

@pytest.mark.asyncio
async def test_take_profit_disabled_holds_winning_position() -> None:
    # Take-profit was disabled 2026-05-23 — backtest showed it cost ~14% PnL.
    # A winner mid-window should now be HELD to settlement, not sold.
    sig = _signal(price=Decimal("0.29"), side=Side.NO)
    order = TrackedOrder(
        signal=sig,
        order_id="TEST-123",
        contracts=10,
        price=Decimal("0.29"),
    )
    order.state = OrderState.FILLED

    executor = AsyncMock()
    executor.filled_orders = [order]
    executor.exit_position.return_value = (True, [])

    # Position is up ~90% with conviction 0.55 — old take_profit would have
    # exited.  Now it must hold to settlement.
    window = _window()
    await _evaluate_exits(
        executor=executor,
        ticker=sig.ticker,
        best_yes_bid=Decimal("0.40"),
        best_no_bid=Decimal("0.55"),
        alerter=None,
        window=window,
    )

    executor.exit_position.assert_not_called()

@pytest.mark.asyncio
async def test_take_profit_does_not_trigger_high_conviction() -> None:
    # Set up order with entry price 0.29 (NO side)
    sig = _signal(price=Decimal("0.29"), side=Side.NO)
    order = TrackedOrder(
        signal=sig,
        order_id="TEST-123",
        contracts=10,
        price=Decimal("0.29"),
    )
    order.state = OrderState.FILLED

    # Mock Executor
    executor = AsyncMock()
    executor.filled_orders = [order]
    executor.exit_position.return_value = (True, [])

    # Market NO bid is 0.85 (gain > 50%, but conviction/bid 0.85 >= 75%)
    # This should NOT trigger take-profit!
    window = _window()
    await _evaluate_exits(
        executor=executor,
        ticker=sig.ticker,
        best_yes_bid=Decimal("0.10"),
        best_no_bid=Decimal("0.85"),
        alerter=None,
        window=window,
    )

    executor.exit_position.assert_not_called()

@pytest.mark.asyncio
async def test_take_profit_does_not_trigger_low_gain() -> None:
    # Set up order with entry price 0.29 (NO side)
    sig = _signal(price=Decimal("0.29"), side=Side.NO)
    order = TrackedOrder(
        signal=sig,
        order_id="TEST-123",
        contracts=10,
        price=Decimal("0.29"),
    )
    order.state = OrderState.FILLED

    # Mock Executor
    executor = AsyncMock()
    executor.filled_orders = [order]
    executor.exit_position.return_value = (True, [])

    # Market NO bid is 0.35 (gain is (0.35-0.29)/0.29 = 20.7% <= 50%)
    # This should NOT trigger take-profit!
    window = _window()
    await _evaluate_exits(
        executor=executor,
        ticker=sig.ticker,
        best_yes_bid=Decimal("0.60"),
        best_no_bid=Decimal("0.35"),
        alerter=None,
        window=window,
    )

    executor.exit_position.assert_not_called()


@pytest.mark.asyncio
async def test_time_exit_fires_in_final_30s() -> None:
    """Regression for the Q2 control-flow bug.

    Entered with seconds_remaining=120 (>90, eligible), now at T-15s
    (window.seconds_remaining < 30). time_exit must fire — the live cycle
    had 0 of these fire across 46 settled trades because _evaluate_exits
    was only being reached via the signal-eval branch, which almost never
    produces a signal in the last 30s of a window.
    """
    sig = _signal(price=Decimal("0.45"), side=Side.NO)
    # Force the entry-time gate: order was placed with secs_remaining=120
    sig.seconds_remaining = 120  # type: ignore[misc]
    order = TrackedOrder(
        signal=sig,
        order_id="TEST-time-exit",
        contracts=4,
        price=Decimal("0.45"),
    )
    order.state = OrderState.FILLED

    executor = AsyncMock()
    executor.filled_orders = [order]
    executor.exit_position.return_value = (True, [])

    # Build a window that closes in 15 seconds (T-15s < 30s threshold).
    window = WindowState(
        symbol="BTC",
        ticker=sig.ticker,
        open_time=datetime.now(timezone.utc) - timedelta(minutes=14, seconds=45),
        close_time=datetime.now(timezone.utc) + timedelta(seconds=15),
        open_price=75000.0,
        current_price=75200.0,
    )

    await _evaluate_exits(
        executor=executor,
        ticker=sig.ticker,
        best_yes_bid=Decimal("0.40"),
        best_no_bid=Decimal("0.55"),
        alerter=None,
        window=window,
    )

    # time_exit must fire: bot owns NO, sells at best_no_bid = 0.55.
    executor.exit_position.assert_called_once()
    call_kwargs = executor.exit_position.call_args
    assert call_kwargs.kwargs.get("exit_reason") == "time_exit"


@pytest.mark.asyncio
async def test_time_exit_skipped_for_late_entry() -> None:
    """Counter-test: entries placed in the final 90s should NOT time_exit.

    The condition is ``order.signal.seconds_remaining > 90`` (entered with
    enough runway). Late entries are held to settlement.
    """
    sig = _signal(price=Decimal("0.45"), side=Side.NO)
    sig.seconds_remaining = 60  # type: ignore[misc]   # entered too late
    order = TrackedOrder(
        signal=sig,
        order_id="TEST-late-entry",
        contracts=4,
        price=Decimal("0.45"),
    )
    order.state = OrderState.FILLED

    executor = AsyncMock()
    executor.filled_orders = [order]
    executor.exit_position.return_value = (True, [])

    window = WindowState(
        symbol="BTC",
        ticker=sig.ticker,
        open_time=datetime.now(timezone.utc) - timedelta(minutes=14, seconds=45),
        close_time=datetime.now(timezone.utc) + timedelta(seconds=15),
        open_price=75000.0,
        current_price=75200.0,
    )

    await _evaluate_exits(
        executor=executor,
        ticker=sig.ticker,
        best_yes_bid=Decimal("0.40"),
        best_no_bid=Decimal("0.55"),
        alerter=None,
        window=window,
    )

    executor.exit_position.assert_not_called()


@pytest.mark.asyncio
async def test_stop_loss_disabled_holds_through_drawdown() -> None:
    # Stop_loss was reverted 2026-05-24 — original 35-day backtest showed it
    # destroys edge, and the live afternoon-slide losses it was meant to
    # prevent were full settlement losses that stop_loss couldn't catch.
    # A losing position past the would-be drawdown threshold must now HOLD.
    sig = _signal(price=Decimal("0.50"), side=Side.NO)
    sig.seconds_remaining = 60  # entered too late for time_exit
    order = TrackedOrder(
        signal=sig,
        order_id="TEST-stop-loss-fire",
        contracts=10,
        price=Decimal("0.50"),
    )
    order.state = OrderState.FILLED

    executor = AsyncMock()
    executor.filled_orders = [order]
    executor.exit_position.return_value = (True, [])

    # Market NO bid is 0.19 — old stop_loss would have fired (loss 0.31 > 0.30).
    # Now we hold to settlement.
    window = _window()
    await _evaluate_exits(
        executor=executor,
        ticker=sig.ticker,
        best_yes_bid=Decimal("0.80"),
        best_no_bid=Decimal("0.19"),
        alerter=None,
        window=window,
    )

    executor.exit_position.assert_not_called()


@pytest.mark.asyncio
async def test_stop_loss_skipped_when_under_drawdown() -> None:
    # Set up order with entry price 0.50 (NO side)
    # Effective threshold = max(0.10, 0.30) = 0.30
    sig = _signal(price=Decimal("0.50"), side=Side.NO)
    sig.seconds_remaining = 60
    order = TrackedOrder(
        signal=sig,
        order_id="TEST-stop-loss-skip",
        contracts=10,
        price=Decimal("0.50"),
    )
    order.state = OrderState.FILLED

    executor = AsyncMock()
    executor.filled_orders = [order]
    executor.exit_position.return_value = (True, [])

    # Market NO bid is 0.21 -> loss per contract = 0.50 - 0.21 = 0.29 < 0.30 (threshold)
    # Stop loss should NOT fire!
    window = _window()
    await _evaluate_exits(
        executor=executor,
        ticker=sig.ticker,
        best_yes_bid=Decimal("0.78"),
        best_no_bid=Decimal("0.21"),
        alerter=None,
        window=window,
    )

    executor.exit_position.assert_not_called()


@pytest.mark.asyncio
async def test_stop_loss_disabled_holds_through_drawdown_yes_side() -> None:
    # YES-side mirror of the NO-side test above.  Stop_loss is disabled.
    sig = _signal(price=Decimal("0.60"), side=Side.YES)
    sig.seconds_remaining = 60
    order = TrackedOrder(
        signal=sig,
        order_id="TEST-stop-loss-yes",
        contracts=10,
        price=Decimal("0.60"),
    )
    order.state = OrderState.FILLED

    executor = AsyncMock()
    executor.filled_orders = [order]
    executor.exit_position.return_value = (True, [])

    # Market YES bid is 0.23 — old stop_loss would have fired (loss 0.37 > 0.36).
    # Now we hold to settlement.
    window = _window()
    await _evaluate_exits(
        executor=executor,
        ticker=sig.ticker,
        best_yes_bid=Decimal("0.23"),
        best_no_bid=Decimal("0.76"),
        alerter=None,
        window=window,
    )

    executor.exit_position.assert_not_called()


@pytest.mark.asyncio
async def test_time_exit_priority_over_stop_loss() -> None:
    # Set up order eligible for both time_exit and stop_loss:
    # entered at seconds_remaining=120, now at seconds_remaining=15
    # Entry price 0.50, current bid 0.15 (loss 0.35 >= 0.30 threshold)
    sig = _signal(price=Decimal("0.50"), side=Side.NO)
    sig.seconds_remaining = 120
    order = TrackedOrder(
        signal=sig,
        order_id="TEST-priority",
        contracts=10,
        price=Decimal("0.50"),
    )
    order.state = OrderState.FILLED

    executor = AsyncMock()
    executor.filled_orders = [order]
    executor.exit_position.return_value = (True, [])

    window = WindowState(
        symbol="BTC",
        ticker=sig.ticker,
        open_time=datetime.now(timezone.utc) - timedelta(minutes=14, seconds=45),
        close_time=datetime.now(timezone.utc) + timedelta(seconds=15),
        open_price=75000.0,
        current_price=75200.0,
    )

    await _evaluate_exits(
        executor=executor,
        ticker=sig.ticker,
        best_yes_bid=Decimal("0.84"),
        best_no_bid=Decimal("0.15"),
        alerter=None,
        window=window,
    )

    executor.exit_position.assert_called_once()
    call_kwargs = executor.exit_position.call_args
    assert call_kwargs.kwargs.get("exit_reason") == "time_exit"
