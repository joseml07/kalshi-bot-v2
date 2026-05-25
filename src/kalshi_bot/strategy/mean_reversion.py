"""Mean reversion strategy: trade AGAINST momentum when OBI disagrees.

When momentum and orderbook imbalance point in opposite directions,
the orderbook (resting liquidity / "smart money") is more likely to be
right.  The momentum move is noise that will revert.

Fires on the ~40% of snapshots that the momentum strategy skips due to
sign disagreement.  Can run alongside momentum as a second signal source.
"""
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


def evaluate_mean_reversion(
    window: WindowState,
    ticker: str,
    orderbook: OrderBook,
    *,
    edge_threshold: float = 0.06,
    k: float = 150.0,
    min_time: int = 91,
    max_time: int = 480,
    min_price: float = 0.25,
    max_price: float = 0.80,
    contracts: int = 1,
) -> Signal | None:
    """Evaluate mean reversion signal: trade AGAINST momentum when OBI disagrees."""
    seconds_remaining = window.seconds_remaining
    if not (min_time <= seconds_remaining <= max_time):
        return None

    momentum = window.momentum_60s
    if momentum is None or momentum == 0.0:
        return None

    imbalance = orderbook.orderbook_imbalance
    mom_sign = _sign(momentum)
    imb_sign = _sign(imbalance)

    # Only fire when signs DISAGREE (opposite of momentum strategy)
    if mom_sign == 0 or imb_sign == 0 or mom_sign == imb_sign:
        return None

    # Minimum depth gate
    if orderbook.total_depth < 50:
        return None

    up_prob = estimate_up_probability(
        window.price_change_pct,
        seconds_remaining,
        k=k,
    )

    # INVERT: bet against momentum, with the orderbook
    if mom_sign > 0:
        # Momentum up but OBI down → fade to NO (OBI is right)
        side = Side.NO
        est_prob = 1 - up_prob
        taker_price = orderbook.best_no_ask
    else:
        # Momentum down but OBI up → fade to YES (OBI is right)
        side = Side.YES
        est_prob = up_prob
        taker_price = orderbook.best_yes_ask

    if taker_price is None:
        return None

    p = float(taker_price)
    if not (min_price <= p <= max_price):
        return None

    contracts = max(1, contracts)
    taker_fee_total = taker_fee(contracts, p)
    net_edge = est_prob - p - float(taker_fee_total / contracts)

    # Use OBI magnitude as confidence boost — stronger disagreement = more conviction
    obi_boost = abs(imbalance) * 0.03
    net_edge += obi_boost

    if net_edge < edge_threshold:
        return None

    edge = est_prob - p
    return Signal(
        timestamp=datetime.now(timezone.utc),
        strategy=StrategyName.MEAN_REVERSION,
        ticker=ticker,
        symbol=window.symbol,
        side=side,
        edge=Decimal(str(edge)),
        net_edge=Decimal(str(net_edge)),
        kalshi_price=taker_price,
        real_prob=up_prob,
        seconds_remaining=seconds_remaining,
        contracts=contracts,
        route="taker",
        taker_price=taker_price,
        reason=f"mean_reversion obi={imbalance:.3f} mom={momentum:.5f}",
    )
