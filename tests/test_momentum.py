"""Tests for momentum + OBI strategy."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kalshi_bot.data.window_tracker import WindowState
from kalshi_bot.models.market import OrderBook, OrderBookLevel
from kalshi_bot.strategy.momentum import evaluate_momentum
from kalshi_bot.strategy.signals import Side


def _make_window(
    price_change_pct: float,
    seconds_remaining: int,
    momentum: float | None = 0.001,
) -> WindowState:
    open_price = 100000.0
    current_price = open_price * (1 + price_change_pct)
    now = datetime.now(timezone.utc)
    ws = WindowState(
        symbol="BTC",
        ticker="KXBTC15M-TEST",
        open_time=now,
        close_time=now + timedelta(seconds=seconds_remaining),
        open_price=open_price,
        current_price=current_price,
    )
    if momentum is not None:
        now_ts = time.time()
        base_price = current_price / (1 + momentum) if momentum != 0 else current_price
        ws.prices_60s.append((now_ts - 60, base_price))
        ws.prices_60s.append((now_ts, current_price))
    return ws


def _make_orderbook(
    yes_bid: float = 0.40,
    no_bid: float = 0.55,
    yes_qty: int = 100,
    no_qty: int = 50,
) -> OrderBook:
    return OrderBook(
        ticker="KXBTC15M-TEST",
        yes_levels=[OrderBookLevel(price=Decimal(str(yes_bid)), quantity=yes_qty)],
        no_levels=[OrderBookLevel(price=Decimal(str(no_bid)), quantity=no_qty)],
    )


def test_no_signal_when_momentum_zero() -> None:
    w = _make_window(0.001, 60, momentum=0.0)
    ob = _make_orderbook()
    assert evaluate_momentum(w, w.ticker, ob) is None


def test_no_signal_when_momentum_none() -> None:
    w = _make_window(0.001, 60, momentum=None)
    ob = _make_orderbook()
    assert evaluate_momentum(w, w.ticker, ob) is None


def test_no_signal_when_signs_disagree() -> None:
    w = _make_window(0.001, 60, momentum=0.001)
    ob = _make_orderbook(yes_qty=50, no_qty=100)
    assert evaluate_momentum(w, w.ticker, ob) is None


def test_yes_signal_bullish_agreement() -> None:
    w = _make_window(0.004, 60, momentum=0.001)
    ob = _make_orderbook(yes_qty=120, no_qty=20)
    sig = evaluate_momentum(w, w.ticker, ob, edge_threshold=0.01)
    assert sig is not None
    assert sig.side == Side.YES


def test_no_signal_bearish_agreement() -> None:
    w = _make_window(-0.004, 60, momentum=-0.001)
    ob = _make_orderbook(yes_bid=0.45, no_bid=0.52, yes_qty=20, no_qty=120)
    sig = evaluate_momentum(w, w.ticker, ob, edge_threshold=0.01)
    assert sig is not None
    assert sig.side == Side.NO


def test_maker_route_preferred() -> None:
    w = _make_window(0.02, 60, momentum=0.005)
    ob = _make_orderbook(yes_bid=0.40, no_bid=0.45, yes_qty=200, no_qty=10)
    sig = evaluate_momentum(w, w.ticker, ob, edge_threshold=0.01)
    assert sig is not None
    assert sig.route == "maker"


def test_taker_fallback() -> None:
    w = _make_window(0.001, 60, momentum=0.001)
    ob = _make_orderbook(yes_bid=0.20, no_bid=0.41, yes_qty=200, no_qty=10)
    sig = evaluate_momentum(
        w,
        w.ticker,
        ob,
        edge_threshold=0.01,
        min_price=0.35,
        max_price=0.95,
        maker_first=True,
    )
    assert sig is not None
    assert sig.route == "taker"


def test_no_signal_below_edge_threshold() -> None:
    w = _make_window(0.0001, 60, momentum=0.0001)
    ob = _make_orderbook(yes_qty=200, no_qty=10)
    assert evaluate_momentum(w, w.ticker, ob, edge_threshold=0.40) is None


@pytest.mark.parametrize("seconds", [5, 800])
def test_no_signal_outside_time_bounds(seconds: int) -> None:
    w = _make_window(0.01, seconds, momentum=0.002)
    ob = _make_orderbook(yes_qty=200, no_qty=10)
    assert evaluate_momentum(w, w.ticker, ob, min_time=30, max_time=480) is None


def test_no_signal_price_out_of_bounds() -> None:
    w = _make_window(0.01, 60, momentum=0.002)
    ob = _make_orderbook(yes_bid=0.10, no_bid=0.89, yes_qty=200, no_qty=10)
    assert evaluate_momentum(w, w.ticker, ob, min_price=0.35, max_price=0.80) is None


def test_taker_price_field_set_for_maker() -> None:
    w = _make_window(0.03, 60, momentum=0.003)
    ob = _make_orderbook(yes_bid=0.42, no_bid=0.45, yes_qty=300, no_qty=5)
    sig = evaluate_momentum(w, w.ticker, ob, edge_threshold=0.01)
    assert sig is not None
    assert sig.route == "maker"
    assert sig.taker_price is not None
