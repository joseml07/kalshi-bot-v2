"""Regression tests for window_tracker strike-based direction labeling.

The bot historically computed direction as ``close >= open`` (Coinbase).
Kalshi actually settles ``close >= floor_strike``, with a strike that
differs from the Coinbase tick at window open by small amounts. ~20% of
windows had the bot's direction flipped relative to Kalshi truth on
narrow-move windows. See Q4 in explanation.md.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_bot.data.window_tracker import WindowTracker


def _times() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    open_time = now - timedelta(minutes=14, seconds=59)
    close_time = now - timedelta(seconds=1)
    return open_time, close_time


def test_strike_based_direction_yes_wins_when_above_strike() -> None:
    tracker = WindowTracker()
    open_time, close_time = _times()
    tracker.set_window(
        "BTC",
        ticker="KXBTC15M-TEST",
        open_time=open_time,
        close_time=close_time,
        open_price=75000.0,
        strike=74950.0,
        strike_type="greater_or_equal",
    )
    # Force a current_price > strike but BELOW open — open-based logic
    # would flip this to "no", strike-based correctly says "yes".
    win = tracker._windows["BTC"]
    win.current_price = 74960.0  # below open (75000), above strike (74950)
    tracker._close_window("BTC")
    prev = tracker.get_previous_result("BTC")
    assert prev is not None
    assert prev.went_up is True, "close above strike → yes wins (regardless of open)"


def test_strike_based_direction_no_wins_when_below_strike() -> None:
    tracker = WindowTracker()
    open_time, close_time = _times()
    tracker.set_window(
        "BTC",
        ticker="KXBTC15M-TEST",
        open_time=open_time,
        close_time=close_time,
        open_price=75000.0,
        strike=75100.0,
        strike_type="greater_or_equal",
    )
    win = tracker._windows["BTC"]
    # Above open but BELOW strike — open-based logic would say yes,
    # strike-based correctly says no.
    win.current_price = 75050.0
    tracker._close_window("BTC")
    prev = tracker.get_previous_result("BTC")
    assert prev is not None
    assert prev.went_up is False, "close below strike → no wins"


def test_falls_back_to_open_when_strike_missing() -> None:
    tracker = WindowTracker()
    open_time, close_time = _times()
    tracker.set_window(
        "BTC",
        ticker="KXBTC15M-TEST",
        open_time=open_time,
        close_time=close_time,
        open_price=75000.0,
    )
    win = tracker._windows["BTC"]
    win.current_price = 75001.0
    tracker._close_window("BTC")
    prev = tracker.get_previous_result("BTC")
    assert prev is not None
    assert prev.went_up is True
    assert prev.strike is None


def test_strike_refreshes_on_same_window_set() -> None:
    """Kalshi can refine strike between first and later set_window calls."""
    tracker = WindowTracker()
    open_time, close_time = _times()
    tracker.set_window(
        "BTC", ticker="KXBTC15M-TEST",
        open_time=open_time, close_time=close_time, open_price=75000.0,
    )
    assert tracker._windows["BTC"].strike is None
    tracker.set_window(
        "BTC", ticker="KXBTC15M-TEST",
        open_time=open_time, close_time=close_time, open_price=75000.0,
        strike=74950.0, strike_type="greater_or_equal",
    )
    assert tracker._windows["BTC"].strike == 74950.0
    assert tracker._windows["BTC"].strike_type == "greater_or_equal"
