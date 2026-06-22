"""Settlement edge strategy — sell expensive YES near expiry.

Based on exhaustive data analysis (STRATEGY_RESEARCH.md):
- SELL YES >= 0.85 at any time, hold to settlement
- Edge persists across regime changes (May 23 collapse)
- June 2026: 1,859 trades, +$123.70, avg $0.067/trade
- Best hours (4, 13, 18, 22 UTC): 303 trades, avg $0.142
- Optional crypto direction filter: sell when crypto is DOWN
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from kalshi_bot.data.window_tracker import WindowState
from kalshi_bot.models.market import OrderBook
from kalshi_bot.strategy.asset_config import (
    SignalStrength,
)
from kalshi_bot.strategy.fees import taker_fee
from kalshi_bot.strategy.probability import estimate_up_probability
from kalshi_bot.strategy.signals import Side, Signal, StrategyName

logger = logging.getLogger(__name__)


def evaluate_settlement_edge(
    window: WindowState,
    ticker: str,
    orderbook: OrderBook,
    *,
    sell_threshold: float = 0.85,
    edge_threshold: float = 0.02,
    min_time: int = 10,
    max_time: int = 900,
    allowed_hours: list[int] | None = None,
    require_crypto_down: bool = False,
    crypto_down_threshold: float = -0.001,
    require_prev_down: bool = False,
    prev_window_went_up: bool | None = None,
    use_multiplier: bool = False,
    min_total_depth: int = 100,
    max_spread: float = 0.03,
    maker_first: bool = False,
    contracts: int = 1,
    k: float = 150.0,
    kelly_win_rate: float = 0.0,
) -> Signal | None:
    """Evaluate sell-expensive-YES settlement edge.

    Signal fires when kalshi_yes_ask >= sell_threshold within the
    allowed time window. Position is held to settlement (no stop-loss,
    no take-profit). Edge comes from the market overpricing certainty
    — contracts at 85c+ resolve DOWN more often than implied.

    Optional filters:
    - allowed_hours: restrict to specific UTC hours (e.g. [4, 13, 18, 22])
    - require_crypto_down: only sell if crypto is already down in window
    - max_spread: skip if bid/ask spread exceeds threshold
    """
    symbol = window.symbol
    seconds_remaining = window.seconds_remaining

    if not (min_time <= seconds_remaining <= max_time):
        return None

    # Time-of-day gate
    if allowed_hours is not None:
        current_hour = datetime.now(timezone.utc).hour
        if current_hour not in allowed_hours:
            return None

    # Crypto direction filter
    if require_crypto_down:
        pct = window.price_change_pct
        if pct > crypto_down_threshold:  # Not down enough
            return None

    # Previous-window filter: only sell after a DOWN window
    # Data shows prev DOWN -> 35.8% WR (3.2x baseline edge)
    if require_prev_down:
        if prev_window_went_up is None:
            return None  # no previous window data yet
        if prev_window_went_up:
            return None  # previous window was UP, skip

    # Price gate — only sell expensive YES
    taker_price = orderbook.best_yes_ask
    if taker_price is None:
        return None
    if float(taker_price) < sell_threshold:
        return None
    if float(taker_price) >= 1.0:
        return None

    # Depth gate
    if orderbook.total_depth < min_total_depth:
        logger.debug(
            "settlement_edge_depth_gate ticker=%s depth=%d min=%d",
            ticker, orderbook.total_depth, min_total_depth,
        )
        return None

    # Spread gate
    if orderbook.best_yes_bid is not None and max_spread > 0:
        spread = float(taker_price) - float(orderbook.best_yes_bid)
        if spread > max_spread:
            logger.debug(
                "settlement_edge_spread_gate ticker=%s spread=%.4f max=%.2f",
                ticker, spread, max_spread,
            )
            return None

    # Compute real probability
    up_prob = estimate_up_probability(
        window.price_change_pct,
        seconds_remaining,
        k=k,
    )

    # We sell YES (= buy NO). The edge is: prob DOWN - NO_price.
    # NO price ≈ 1 - YES_ask (approximate; actual NO_ask used if available)
    no_price = orderbook.best_no_ask
    if no_price is not None:
        entry_price = float(no_price)
        side = Side.NO
    else:
        entry_price = 1.0 - float(taker_price)
        side = Side.NO

    est_down_prob = 1.0 - up_prob
    raw_edge = est_down_prob - entry_price

    # Fee-adjusted edge
    taker_fee_total = taker_fee(contracts, entry_price)
    net_edge = raw_edge - float(taker_fee_total / contracts)

    if net_edge < edge_threshold:
        return None

    strength = SignalStrength.MODERATE
    if net_edge >= 0.08:
        strength = SignalStrength.STRONG

    # Compute sizing multiplier from edge conditions
    sizing_mult = 1.0
    if use_multiplier:
        if prev_window_went_up is False:  # prev was DOWN
            sizing_mult += 0.5
        if allowed_hours and datetime.now(timezone.utc).hour in allowed_hours:
            sizing_mult += 0.3
        if window.price_change_pct is not None and window.price_change_pct < 0:
            sizing_mult += 0.2
        if float(taker_price) >= 0.90:
            sizing_mult += 0.2
        if orderbook.best_yes_bid is not None:
            spread = float(taker_price) - float(orderbook.best_yes_bid)
            if spread > 0.02 and orderbook.total_depth >= 500:
                sizing_mult += 0.1
        yes_vol = sum(lv.quantity for lv in orderbook.yes_levels) if orderbook.yes_levels else 0
        no_vol = sum(lv.quantity for lv in orderbook.no_levels) if orderbook.no_levels else 0
        ratio = yes_vol / no_vol if no_vol > 0 else 0
        if 0.5 <= ratio <= 2.0:
            sizing_mult += 0.1
        sizing_mult = max(0.5, min(sizing_mult, 3.0))

    # Kelly sizing: use empirical win rate if configured, else model estimate
    if kelly_win_rate > 0:
        kelly_real_prob = 1.0 - kelly_win_rate  # win_prob for NO = DOWN WR
    else:
        kelly_real_prob = up_prob  # fallback to model

    return Signal(
        timestamp=datetime.now(timezone.utc),
        strategy=StrategyName.SETTLEMENT_EDGE,
        ticker=ticker,
        symbol=symbol,
        side=side,
        edge=Decimal(str(round(raw_edge, 6))),
        net_edge=Decimal(str(round(net_edge, 6))),
        kalshi_price=Decimal(str(entry_price)),
        real_prob=kelly_real_prob,
        seconds_remaining=seconds_remaining,
        contracts=contracts,
        route="taker",
        taker_price=Decimal(str(entry_price)),
        reason=f"settlement_edge sell_yes>={sell_threshold} net_edge={net_edge:.4f} mult={sizing_mult:.1f}x",
        signal_strength=strength,
        sizing_multiplier=sizing_mult,
    )
