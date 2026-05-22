"""Late-Window Momentum (LWM) strategy with per-asset tuning."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from decimal import Decimal

from kalshi_bot.data.window_tracker import WindowState
from kalshi_bot.models.market import OrderBook
from kalshi_bot.strategy.asset_config import (
    compute_signal_strength,
    maker_timeout_for_strength,
    resolve_param,
)
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
    edge_threshold: float | None = None,
    decision_min_s: int = 30,
    decision_max_s: int = 540,
    yes_decision_max_s: int = 120,
    min_price_change: float | None = None,
    min_book_sum: float = 0.90,
    max_book_sum: float = 1.50,
    min_price: float | None = None,
    max_price: float | None = None,
    yes_only: bool | None = None,
    no_side_edge_bonus: float | None = None,
    maker_first: bool = True,
    contracts: int = 1,
) -> Signal | None:
    """Evaluate LWM gates and return a trade signal if valid.

    Per-asset overrides are resolved from ``DEFAULT_ASSET_CONFIGS``.
    """
    symbol = window.symbol

    # Resolve per-asset overrides (only when caller didn't specify)
    eff_edge_threshold = edge_threshold if edge_threshold is not None else resolve_param(symbol, 0.06, "edge_threshold")
    eff_min_price_change = min_price_change if min_price_change is not None else resolve_param(symbol, 0.0003, "lwm_min_price_change")
    eff_yes_only = yes_only if yes_only is not None else resolve_param(symbol, True, "lwm_yes_only")
    eff_no_side_bonus = no_side_edge_bonus if no_side_edge_bonus is not None else resolve_param(symbol, 0.04, "lwm_no_side_edge_bonus")
    eff_min_price = min_price if min_price is not None else resolve_param(symbol, 0.05, "min_trade_price")
    eff_max_price = max_price if max_price is not None else resolve_param(symbol, 0.95, "max_trade_price")

    seconds_remaining = window.seconds_remaining
    if not (decision_min_s <= seconds_remaining <= decision_max_s):
        return None

    pc = window.price_change_pct
    if abs(pc) < eff_min_price_change:
        return None

    yes_bid = orderbook.best_yes_bid
    no_bid = orderbook.best_no_bid
    if yes_bid is None or no_bid is None:
        return None
    book_sum = float(yes_bid + no_bid)
    if not (min_book_sum <= book_sum <= max_book_sum):
        logger.debug(
            "lwm_book_gate_skip ticker=%s yes_bid=%s no_bid=%s sum=%.4f",
            ticker, yes_bid, no_bid, book_sum,
        )
        return None

    side = Side.YES if pc > 0 else Side.NO
    if side is Side.NO and eff_yes_only:
        return None

    # YES is only predictive in the last 2 minutes. Early positive momentum
    # reverts ~90% of the time (see yes_side_investigation.md).
    if side is Side.YES and seconds_remaining > yes_decision_max_s:
        return None

    if side is Side.YES:
        maker_price = orderbook.best_yes_bid
        taker_price = orderbook.best_yes_ask
        est_prob = estimate_p_up(pc, seconds_remaining)
        side_edge_thr = eff_edge_threshold
    else:
        maker_price = orderbook.best_no_bid
        taker_price = orderbook.best_no_ask
        est_prob = 1.0 - estimate_p_up(pc, seconds_remaining)
        side_edge_thr = eff_edge_threshold + eff_no_side_bonus

    if maker_price is None or taker_price is None:
        return None

    contracts = max(1, contracts)

    # Compute signal strength for adaptive execution
    raw_net_edge = 0.0
    if maker_first and eff_min_price <= float(maker_price) <= eff_max_price:
        fee_total = maker_fee(contracts, float(maker_price))
        raw_net_edge = est_prob - float(maker_price) - float(fee_total / contracts)

    if raw_net_edge < side_edge_thr and eff_min_price <= float(taker_price) <= eff_max_price:
        fee_total = taker_fee(contracts, float(taker_price))
        raw_net_edge = est_prob - float(taker_price) - float(fee_total / contracts)

    strength = compute_signal_strength(
        net_edge=raw_net_edge,
        obi=orderbook.orderbook_imbalance,
        seconds_remaining=seconds_remaining,
        total_depth=orderbook.total_depth,
    )

    if maker_first and eff_min_price <= float(maker_price) <= eff_max_price:
        fee_total = maker_fee(contracts, float(maker_price))
        net_edge = est_prob - float(maker_price) - float(fee_total / contracts)
        if net_edge >= side_edge_thr:
            edge = est_prob - float(maker_price)
            maker_timeout = maker_timeout_for_strength(
                strength, symbol, global_horizon=90
            )
            return Signal(
                timestamp=datetime.now(timezone.utc),
                strategy=StrategyName.LWM,
                ticker=ticker,
                symbol=symbol,
                side=side,
                edge=Decimal(str(edge)),
                net_edge=Decimal(str(net_edge)),
                kalshi_price=maker_price,
                real_prob=est_prob,
                seconds_remaining=seconds_remaining,
                contracts=contracts,
                route="maker",
                taker_price=taker_price,
                reason=f"lwm maker pc={pc:.5f} secs={seconds_remaining} strength={strength.value} timeout={maker_timeout}s",
                signal_strength=strength,
            )

    if eff_min_price <= float(taker_price) <= eff_max_price:
        fee_total = taker_fee(contracts, float(taker_price))
        net_edge = est_prob - float(taker_price) - float(fee_total / contracts)
        if net_edge >= side_edge_thr:
            edge = est_prob - float(taker_price)
            return Signal(
                timestamp=datetime.now(timezone.utc),
                strategy=StrategyName.LWM,
                ticker=ticker,
                symbol=symbol,
                side=side,
                edge=Decimal(str(edge)),
                net_edge=Decimal(str(net_edge)),
                kalshi_price=taker_price,
                real_prob=est_prob,
                seconds_remaining=seconds_remaining,
                contracts=contracts,
                route="taker",
                taker_price=taker_price,
                reason=f"lwm taker pc={pc:.5f} secs={seconds_remaining} strength={strength.value}",
                signal_strength=strength,
            )

    return None
