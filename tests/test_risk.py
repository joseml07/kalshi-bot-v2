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


# --- Per-side gates (added with the framework for the real-money switch) ---

def _settings_with_per_side(limit: float = 10.0, wr_enabled: bool = True) -> Settings:
    return Settings(
        kalshi_api_key="k",
        kalshi_private_key_path="./kalshi_key.pem",
        daily_loss_limit=100.0,  # large; per-side limit should bind first
        max_concurrent_positions=10,
        per_side_daily_loss_limit=limit,
        side_wr_alert_window=5,
        side_wr_alert_threshold=0.40,
        side_wr_alerts_enabled=wr_enabled,
    )


def test_per_side_daily_loss_pauses_only_that_side() -> None:
    rm = RiskManager(_settings_with_per_side(limit=10.0))
    # Lose $12 on yes (over the $10 per-side limit)
    rm.record_settlement("KX-1", Decimal("-7"), side="yes")
    evs = rm.record_settlement("KX-2", Decimal("-5"), side="yes")
    assert evs.get("side_paused", {}).get("side") == "yes"

    # Yes blocked, no still allowed
    with pytest.raises(RiskVetoError):
        rm.check(_signal("KX-3", side=Side.YES))
    rm.check(_signal("KX-4", side=Side.NO))


def test_per_side_disabled_when_limit_zero() -> None:
    s = Settings(
        kalshi_api_key="k", kalshi_private_key_path="./kalshi_key.pem",
        daily_loss_limit=100.0, max_concurrent_positions=10,
        per_side_daily_loss_limit=0.0,
    )
    rm = RiskManager(s)
    rm.record_settlement("KX-1", Decimal("-50"), side="yes")
    rm.check(_signal("KX-2", side=Side.YES))  # should NOT raise


def test_side_wr_alert_fires_once_then_recovers() -> None:
    rm = RiskManager(_settings_with_per_side(limit=1000.0))  # only WR matters

    def streak(prefix: str, n: int, win: bool) -> list[dict]:
        pnl = Decimal("1") if win else Decimal("-1")
        return [rm.record_settlement(f"{prefix}-{i}", pnl, side="yes") for i in range(n)]

    # First losing streak should fire the alert exactly once across the streak.
    alerts1 = [e for e in streak("KX-l1", 5, False) if "side_wr_alert" in e]
    assert len(alerts1) == 1
    assert alerts1[0]["side_wr_alert"]["side"] == "yes"

    # Another loss after the latch must NOT re-fire.
    assert "side_wr_alert" not in rm.record_settlement("KX-l1-x", Decimal("-1"), side="yes")

    # Winning streak clears the latch.
    streak("KX-w", 5, True)
    # Second losing streak should re-fire exactly once.
    alerts2 = [e for e in streak("KX-l2", 5, False) if "side_wr_alert" in e]
    assert len(alerts2) == 1
    assert alerts2[0]["side_wr_alert"]["side"] == "yes"


def test_wr_alert_disabled_when_setting_off() -> None:
    rm = RiskManager(_settings_with_per_side(limit=1000.0, wr_enabled=False))
    for i in range(5):
        evs = rm.record_settlement(f"KX-yes-{i}", Decimal("-1"), side="yes")
    assert "side_wr_alert" not in evs
