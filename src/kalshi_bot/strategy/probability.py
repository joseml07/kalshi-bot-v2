"""Logistic probability model for crypto price direction."""

from __future__ import annotations

import math
import statistics


def estimate_up_probability(
    price_change_pct: float,
    seconds_remaining: int,
    k: float = 150.0,
) -> float:
    """Estimate P(price will be UP at window close).

    Uses a logistic function on change/sqrt(time_fraction). Price moves
    scale with sqrt(time) (random walk), so a 0.2% move with 2 min left
    is much more decisive than 0.2% with 10 min left.

    Args:
        price_change_pct: (current - open) / open, e.g. 0.003 = +0.3%
        seconds_remaining: Seconds until window closes (max 900).
        k: Steepness parameter. Higher = more confident predictions.

    Returns:
        Probability between 0 and 1.
    """
    time_fraction = max(seconds_remaining / 900.0, 0.01)
    z = k * price_change_pct / math.sqrt(time_fraction)
    raw = 1.0 / (1.0 + math.exp(-z))
    return max(0.05, min(0.95, raw))


def estimate_k_from_vol(recent_changes: list[float]) -> float:
    """Estimate k from recent realized volatility.

    k ~ 1/sigma_15m.  With fewer than 5 observations the default
    k=150 is returned.  The result is clamped to [50, 400].
    """
    if len(recent_changes) < 5:
        return 150.0
    sigma = statistics.stdev(recent_changes)
    if sigma < 0.0001:
        return 150.0
    k = 1.0 / sigma
    return max(50.0, min(k, 600.0))
