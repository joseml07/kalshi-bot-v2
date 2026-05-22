"""Tests for Late-Window Momentum (LWM) strategy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kalshi_bot.data.window_tracker import WindowState
from kalshi_bot.models.market import OrderBook, OrderBookLevel
from kalshi_bot.strategy.lwm import estimate_p_up, evaluate_lwm
from kalshi_bot.strategy.signals import Side, StrategyName


def _make_window(price_change_pct: float, seconds_remaining: int) -> WindowState:
    open_price = 100_000.0
    current_price = open_price * (1 + price_change_pct)
    now = datetime.now(timezone.utc)
    return WindowState(
        symbol="BTC",
        ticker="KXBTC15M-TEST",
        open_time=now,
        close_time=now + timedelta(seconds=seconds_remaining),
        open_price=open_price,
        current_price=current_price,
    )


def _book(yes_bid: str, no_bid: str) -> OrderBook:
    return OrderBook(
        ticker="KXBTC15M-TEST",
        yes_levels=[OrderBookLevel(price=Decimal(yes_bid), quantity=100)],
        no_levels=[OrderBookLevel(price=Decimal(no_bid), quantity=100)],
    )


def test_estimate_p_up_late_strong_positive() -> None:
    assert estimate_p_up(0.003, 60) == 0.92


def test_estimate_p_up_mid_weak_positive() -> None:
    assert estimate_p_up(0.0001, 200) == 0.62


def test_estimate_p_up_returns_half_for_zero() -> None:
    assert estimate_p_up(0.0, 100) == 0.5


def test_signal_yes_when_strong_positive_drift() -> None:
    w = _make_window(0.005, 120)
    ob = _book(yes_bid="0.55", no_bid="0.40")
    sig = evaluate_lwm(w, w.ticker, ob, edge_threshold=0.01)
    assert sig is not None
    assert sig.side is Side.YES
    assert sig.strategy is StrategyName.LWM


def test_skips_when_outside_time_window() -> None:
    w = _make_window(0.005, 5)  # too late
    ob = _book(yes_bid="0.55", no_bid="0.40")
    assert evaluate_lwm(w, w.ticker, ob) is None


def test_skips_weak_price_move() -> None:
    w = _make_window(0.0001, 120)  # below 0.0003 threshold
    ob = _book(yes_bid="0.55", no_bid="0.40")
    assert evaluate_lwm(w, w.ticker, ob) is None


def test_allows_implied_crossed_book() -> None:
    # yes_bid + no_bid > 1 is a normal Kalshi state (independent YES/NO
    # orderbooks). The strategy must accept it and emit a signal.
    w = _make_window(0.005, 120)
    ob = _book(yes_bid="0.62", no_bid="0.66")  # sum 1.28
    sig = evaluate_lwm(w, w.ticker, ob, edge_threshold=0.01)
    assert sig is not None
    assert sig.side is Side.YES


def test_skips_book_sum_above_hard_ceiling() -> None:
    # Deep implied-cross still tradeable, but a pathological 1.8+ sum
    # signals genuinely broken data and is still rejected.
    w = _make_window(0.005, 120)
    ob = _book(yes_bid="0.95", no_bid="0.95")  # sum 1.90
    assert evaluate_lwm(w, w.ticker, ob, max_book_sum=1.50) is None


def test_skips_one_sided_book() -> None:
    w = _make_window(0.005, 120)
    ob = _book(yes_bid="0.30", no_bid="0.50")  # sum 0.80, below 0.90
    assert evaluate_lwm(w, w.ticker, ob) is None


def test_yes_only_blocks_no_signal_by_default() -> None:
    w = _make_window(-0.005, 120)
    ob = _book(yes_bid="0.40", no_bid="0.55")
    assert evaluate_lwm(w, w.ticker, ob, yes_only=True) is None


def test_no_signal_emitted_when_yes_only_disabled() -> None:
    w = _make_window(-0.005, 120)
    ob = _book(yes_bid="0.40", no_bid="0.55")
    sig = evaluate_lwm(
        w, w.ticker, ob, edge_threshold=0.01, yes_only=False, no_side_edge_bonus=0.0
    )
    assert sig is not None
    assert sig.side is Side.NO


def test_no_side_edge_bonus_blocks_marginal_no_trades() -> None:
    w = _make_window(-0.0006, 200)  # est_prob_no = 1 - 0.28 = 0.72; no_bid 0.55 -> edge 0.17
    ob = _book(yes_bid="0.40", no_bid="0.55")
    sig_low_thr = evaluate_lwm(
        w, w.ticker, ob, edge_threshold=0.05, yes_only=False, no_side_edge_bonus=0.0
    )
    assert sig_low_thr is not None
    sig_high_thr = evaluate_lwm(
        w, w.ticker, ob, edge_threshold=0.05, yes_only=False, no_side_edge_bonus=0.20
    )
    assert sig_high_thr is None


@pytest.mark.parametrize(
    "price_change,expected_route",
    [(0.005, "maker"), (0.005, "maker")],
)
def test_maker_route_preferred(price_change: float, expected_route: str) -> None:
    w = _make_window(price_change, 120)
    ob = _book(yes_bid="0.55", no_bid="0.40")
    sig = evaluate_lwm(w, w.ticker, ob, edge_threshold=0.01)
    assert sig is not None
    assert sig.route == expected_route


def test_taker_price_set_when_maker_route() -> None:
    w = _make_window(0.005, 120)
    ob = _book(yes_bid="0.55", no_bid="0.40")
    sig = evaluate_lwm(w, w.ticker, ob, edge_threshold=0.01)
    assert sig is not None
    assert sig.taker_price is not None


def test_yes_signal_blocked_early() -> None:
    w = _make_window(0.005, 125)
    ob = _book(yes_bid="0.55", no_bid="0.40")
    # Should block because seconds_remaining > 120 (the default yes_decision_max_s)
    assert evaluate_lwm(w, w.ticker, ob, edge_threshold=0.01) is None
    # Should pass if we manually increase yes_decision_max_s to 130
    sig = evaluate_lwm(w, w.ticker, ob, edge_threshold=0.01, yes_decision_max_s=130)
    assert sig is not None
    assert sig.side is Side.YES
