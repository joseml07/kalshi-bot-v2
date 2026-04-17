"""Momentum + orderbook imbalance strategy."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from kalshi_bot.data.window_tracker import WindowState
from kalshi_bot.models.market import OrderBook
from kalshi_bot.strategy.fees import maker_fee, taker_fee
from kalshi_bot.strategy.probability import estimate_up_probability
from kalshi_bot.strategy.signals import Side, Signal, StrategyName

logger = logging.getLogger(__name__)


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _price_in_bounds(price: Decimal, min_price: float, max_price: float) -> bool:
    p = float(price)
    return min_price <= p <= max_price


def evaluate_momentum(
    window: WindowState,
    ticker: str,
    orderbook: OrderBook,
    *,
    edge_threshold: float = 0.06,
    k: float = 150.0,
    min_time: int = 30,
    max_time: int = 480,
    min_price: float = 0.35,
    max_price: float = 0.80,
    maker_first: bool = True,
    contracts: int = 1,
) -> Signal | None:
    """Evaluate momentum + OBI gates and return a trade signal if valid."""
    seconds_remaining = window.seconds_remaining
    if not (min_time <= seconds_remaining <= max_time):
        return None

    momentum = window.momentum_60s
    if momentum is None or momentum == 0.0:
        return None

    imbalance = orderbook.orderbook_imbalance
    mom_sign = _sign(momentum)
    imb_sign = _sign(imbalance)
    if mom_sign == 0 or imb_sign == 0 or mom_sign != imb_sign:
        logger.info(
            "momentum_obi_mismatch ticker=%s momentum=%.5f imbalance=%.0f "
            "mom_sign=%d imb_sign=%d secs=%d",
            ticker,
            momentum,
            imbalance,
            mom_sign,
            imb_sign,
            seconds_remaining,
        )
        return None

    up_prob = estimate_up_probability(
        window.price_change_pct,
        seconds_remaining,
        k=k,
    )

    if mom_sign > 0:
        side = Side.YES
        est_prob = up_prob
        maker_price = orderbook.best_yes_bid
        taker_price = orderbook.best_yes_ask
    else:
        side = Side.NO
        est_prob = 1 - up_prob
        maker_price = orderbook.best_no_bid
        taker_price = orderbook.best_no_ask

    if maker_price is None or taker_price is None:
        return None

    contracts = max(1, contracts)

    if maker_first and _price_in_bounds(maker_price, min_price, max_price):
        maker_fee_total = maker_fee(contracts, float(maker_price))
        maker_net_edge = est_prob - float(maker_price) - float(maker_fee_total / contracts)
        if maker_net_edge >= edge_threshold:
            maker_edge = est_prob - float(maker_price)
            return Signal(
                timestamp=datetime.now(timezone.utc),
                strategy=StrategyName.MOMENTUM,
                ticker=ticker,
                symbol=window.symbol,
                side=side,
                edge=Decimal(str(maker_edge)),
                net_edge=Decimal(str(maker_net_edge)),
                kalshi_price=maker_price,
                real_prob=up_prob,
                seconds_remaining=seconds_remaining,
                contracts=contracts,
                route="maker",
                taker_price=taker_price,
                reason="momentum+obi maker",
            )

    if _price_in_bounds(taker_price, min_price, max_price):
        taker_fee_total = taker_fee(contracts, float(taker_price))
        taker_net_edge = est_prob - float(taker_price) - float(taker_fee_total / contracts)
        if taker_net_edge >= edge_threshold:
            taker_edge = est_prob - float(taker_price)
            return Signal(
                timestamp=datetime.now(timezone.utc),
                strategy=StrategyName.MOMENTUM,
                ticker=ticker,
                symbol=window.symbol,
                side=side,
                edge=Decimal(str(taker_edge)),
                net_edge=Decimal(str(taker_net_edge)),
                kalshi_price=taker_price,
                real_prob=up_prob,
                seconds_remaining=seconds_remaining,
                contracts=contracts,
                route="taker",
                taker_price=taker_price,
                reason="momentum+obi taker",
            )

    return None
