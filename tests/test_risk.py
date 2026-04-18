"""Risk manager tests."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from kalshi_bot.config import Settings
from kalshi_bot.models.market import OrderBook, OrderBookLevel
from kalshi_bot.risk.manager import KILL_SWITCH_FILE, RiskManager, RiskVetoError
from kalshi_bot.strategy.signals import Side, Signal, StrategyName


def _book(yes_bid: str, no_bid: str) -> OrderBook:
    return OrderBook(
        ticker="KXBTC15M-TEST",
        yes_levels=[OrderBookLevel(price=Decimal(yes_bid), quantity=100)],
        no_levels=[OrderBookLevel(price=Decimal(no_bid), quantity=100)],
    )


def _settings() -> Settings:
    return Settings(
        kalshi_api_key="k",
        kalshi_private_key_path="./kalshi_key.pem",
        daily_loss_limit=25.0,
        max_concurrent_positions=1,
    )


def _signal(ticker: str = "KXBTC15M-TEST", side: Side = Side.YES) -> Signal:
    return Signal(
        timestamp=datetime.now(timezone.utc),
        strategy=StrategyName.MOMENTUM,
        ticker=ticker,
        symbol="BTC",
        side=side,
        edge=Decimal("0.10"),
        net_edge=Decimal("0.08"),
        kalshi_price=Decimal("0.50"),
        real_prob=0.6,
        seconds_remaining=120,
    )


def test_kill_switch_blocks_trades(tmp_path: pytest.TempPathFactory) -> None:
    rm = RiskManager(_settings())
    KILL_SWITCH_FILE.touch()
    with pytest.raises(RiskVetoError):
        rm.check(_signal())
    KILL_SWITCH_FILE.unlink(missing_ok=True)


def test_daily_loss_limit_blocks_trades() -> None:
    rm = RiskManager(_settings())
    rm.record_settlement("KXBTC15M-TEST", Decimal("-30"))
    with pytest.raises(RiskVetoError):
        rm.check(_signal())


def test_concurrent_position_limit_works() -> None:
    rm = RiskManager(_settings())
    rm.record_fill("KXBTC15M-1", side="yes")
    with pytest.raises(RiskVetoError):
        rm.check(_signal("KXBTC15M-2"))


def test_cooldown_after_exit_works() -> None:
    rm = RiskManager(_settings())
    t = "KXBTC15M-TEST"
    rm.record_fill(t, side="yes")
    rm.record_settlement(t, Decimal("0"))
    with pytest.raises(RiskVetoError):
        rm.check(_signal(t))


def test_locked_side_blocks_all_reentry() -> None:
    rm = RiskManager(_settings())
    t = "KXBTC15M-TEST"
    rm.record_fill(t, side="yes")
    with pytest.raises(RiskVetoError):
        rm.check(_signal(t, side=Side.YES))
    with pytest.raises(RiskVetoError):
        rm.check(_signal(t, side=Side.NO))


def test_side_locking_persists_for_window_lifetime() -> None:
    rm = RiskManager(_settings())
    t = "KXBTC15M-TEST"
    rm.record_fill(t, side="no")
    rm.record_settlement(t, Decimal("1.0"))

    time.sleep(0.01)
    with pytest.raises(RiskVetoError):
        rm.check(_signal(t, side=Side.YES))


def test_implied_crossed_book_does_not_veto() -> None:
    """yes_bid + no_bid > 1 is a real Kalshi state, not corruption.

    The synthetic yes_ask = 1 - no_bid will fall below the live yes_bid;
    the old check vetoed these and starved the bot of trades. Now we
    only observe.
    """
    rm = RiskManager(_settings())
    book = _book(yes_bid="0.62", no_bid="0.66")  # sum 1.28
    rm.check(_signal(), book)


def test_uncrossed_book_does_not_veto() -> None:
    rm = RiskManager(_settings())
    book = _book(yes_bid="0.45", no_bid="0.50")  # sum 0.95
    rm.check(_signal(), book)
