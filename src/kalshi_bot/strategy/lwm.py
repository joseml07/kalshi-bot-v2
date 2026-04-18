"""Late-Window Momentum (LWM) strategy.

Trades the *direction of intra-window price drift* (`price_change_pct`)
near close, with a probability lookup calibrated against 5 days of
recorded snapshots. See `LWM_FINDINGS.md` in the backtester repo.

Test-set comparison vs the baseline momentum + OBI runner:
    baseline: 55 trades, 60% WR, +$131 net, Sharpe 3.73, DD -$16.50
    LWM:      30 trades, 80% WR,  +$90 net, Sharpe 3.70, DD  -$8.57

Defaults:
- yes_only=True because the recorded period was bull-biased (65% up).
  NO-side trading can be re-enabled when calibration is refit on a
  more balanced window.
- decision_window=(30, 540)s — wide enough to catch most viable setups
  while excluding the noisy first minute of the window.
- min_price_change=0.0003 — filters out the noise band where
  `price_change_pct` sign is essentially random.
- Book gates `[0.90, 1.005]` — skip implied-crossed and one-sided
  snapshots, the same shapes the old crossed-book risk veto used to
  block (and that quietly poisoned the live momentum strategy).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from decimal import Decimal

from kalshi_bot.data.window_tracker import WindowState
from kalshi_bot.models.market import OrderBook
from kalshi_bot.strategy.fees import maker_fee, taker_fee
from kalshi_bot.strategy.signals import Side, Signal, StrategyName

logger = logging.getLogger(__name__)


def estimate_p_up(price_change_pct: float, seconds_remaining: float) -> float:
    """Empirical P(close > open), calibrated from the snapshot dataset.

    Coarse piecewise table from `tests/calibration_lwm.py`. The bucket
    boundaries are stable; the probability values should be refit
    periodically (weekly) from the rolling dataset.
    """
    if math.isnan(price_change_pct):
        return 0.5
    s = seconds_remaining
    pc = price_change_pct
    if s <= 120:
        if pc > 0.002:
            return 0.92
        if pc > 0.0005:
            return 0.85
        if pc > 0:
            return 0.70
        if pc < -0.002:
            return 0.08
        if pc < -0.0005:
            return 0.18
        if pc < 0:
            return 0.32
        return 0.5
    if s <= 300:
        if pc > 0.002:
            return 0.85
        if pc > 0.0005:
            return 0.78
        if pc > 0:
            return 0.62
        if pc < -0.002:
            return 0.18
        if pc < -0.0005:
            return 0.28
        if pc < 0:
            return 0.42
        return 0.5
    if pc > 0.002:
        return 0.72
    if pc > 0.0005:
        return 0.65
    if pc > 0:
        return 0.55
    if pc < -0.002:
        return 0.28
    if pc < -0.0005:
        return 0.35
    if pc < 0:
        return 0.45
    return 0.5


def evaluate_lwm(
    window: WindowState,
    ticker: str,
    orderbook: OrderBook,
    *,
    edge_threshold: float = 0.06,
    decision_min_s: int = 30,
    decision_max_s: int = 540,
    min_price_change: float = 0.0003,
    min_book_sum: float = 0.90,
    max_book_sum: float = 1.005,
    min_price: float = 0.05,
    max_price: float = 0.95,
    yes_only: bool = True,
    no_side_edge_bonus: float = 0.04,
    maker_first: bool = True,
    contracts: int = 1,
) -> Signal | None:
    seconds_remaining = window.seconds_remaining
    if not (decision_min_s <= seconds_remaining <= decision_max_s):
        return None

    pc = window.price_change_pct
    if abs(pc) < min_price_change:
        return None

    yes_bid = orderbook.best_yes_bid
    no_bid = orderbook.best_no_bid
    if yes_bid is None or no_bid is None:
        return None
    book_sum = float(yes_bid + no_bid)
    if not (min_book_sum <= book_sum <= max_book_sum):
        logger.info(
            "lwm_book_gate_skip ticker=%s yes_bid=%s no_bid=%s sum=%.4f",
            ticker, yes_bid, no_bid, book_sum,
        )
        return None

    side = Side.YES if pc > 0 else Side.NO
    if side is Side.NO and yes_only:
        return None

    if side is Side.YES:
        maker_price = orderbook.best_yes_bid
        taker_price = orderbook.best_yes_ask
        est_prob = estimate_p_up(pc, seconds_remaining)
        side_edge_thr = edge_threshold
    else:
        maker_price = orderbook.best_no_bid
        taker_price = orderbook.best_no_ask
        est_prob = 1.0 - estimate_p_up(pc, seconds_remaining)
        side_edge_thr = edge_threshold + no_side_edge_bonus

    if maker_price is None or taker_price is None:
        return None

    contracts = max(1, contracts)

    if maker_first and min_price <= float(maker_price) <= max_price:
        fee_total = maker_fee(contracts, float(maker_price))
        net_edge = est_prob - float(maker_price) - float(fee_total / contracts)
        if net_edge >= side_edge_thr:
            edge = est_prob - float(maker_price)
            return Signal(
                timestamp=datetime.now(timezone.utc),
                strategy=StrategyName.LWM,
                ticker=ticker,
                symbol=window.symbol,
                side=side,
                edge=Decimal(str(edge)),
                net_edge=Decimal(str(net_edge)),
                kalshi_price=maker_price,
                real_prob=est_prob,
                seconds_remaining=seconds_remaining,
                contracts=contracts,
                route="maker",
                taker_price=taker_price,
                reason=f"lwm maker pc={pc:.5f} secs={seconds_remaining}",
            )

    if min_price <= float(taker_price) <= max_price:
        fee_total = taker_fee(contracts, float(taker_price))
        net_edge = est_prob - float(taker_price) - float(fee_total / contracts)
        if net_edge >= side_edge_thr:
            edge = est_prob - float(taker_price)
            return Signal(
                timestamp=datetime.now(timezone.utc),
                strategy=StrategyName.LWM,
                ticker=ticker,
                symbol=window.symbol,
                side=side,
                edge=Decimal(str(edge)),
                net_edge=Decimal(str(net_edge)),
                kalshi_price=taker_price,
                real_prob=est_prob,
                seconds_remaining=seconds_remaining,
                contracts=contracts,
                route="taker",
                taker_price=taker_price,
                reason=f"lwm taker pc={pc:.5f} secs={seconds_remaining}",
            )

    return None
