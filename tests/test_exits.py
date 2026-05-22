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
async def test_take_profit_triggers() -> None:
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

    # Market NO bid is 0.55 (gain is (0.55-0.29)/0.29 = 89.6% > 50%, conviction/bid 0.55 < 75%)
    # This should trigger take-profit exit!
    window = _window()
    await _evaluate_exits(
        executor=executor,
        ticker=sig.ticker,
        best_yes_bid=Decimal("0.40"),
        best_no_bid=Decimal("0.55"),
        alerter=None,
        window=window,
    )

    executor.exit_position.assert_called_once()
    args, kwargs = executor.exit_position.call_args
    assert args[0] == order
    assert args[1] == Decimal("0.55")
    assert "take_profit" in kwargs.get("exit_reason", "")

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
