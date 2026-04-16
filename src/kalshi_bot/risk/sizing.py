"""Fractional-Kelly position sizing."""

from __future__ import annotations

import math
from decimal import Decimal

MIN_CONTRACTS = 1
MAX_CONTRACTS = 10
MAX_COST_DOLLARS = Decimal("25.00")

DEFAULT_KELLY_FRACTION = 0.25


def kelly_fraction(win_prob: float, price: float) -> float:
    """Full Kelly fraction for a binary bet.

    For a binary contract bought at `price` that pays $1 on win:
        kelly = (win_prob - price) / (1 - price)
    """
    if price <= 0 or price >= 1:
        return 0.0
    edge = win_prob - price
    if edge <= 0:
        return 0.0
    return edge / (1 - price)


def kelly_size(
    win_prob: float,
    price: float,
    bankroll: Decimal,
    fraction: float = DEFAULT_KELLY_FRACTION,
) -> int:
    """Compute position size using fractional-Kelly criterion.

    Args:
        win_prob: Estimated probability of winning the contract.
        price: Contract price in dollars (0 < price < 1).
        bankroll: Current available bankroll in dollars.
        fraction: Kelly fraction to apply (e.g. 0.25 = quarter-Kelly).

    Returns:
        Number of contracts to buy (between MIN_CONTRACTS and MAX_CONTRACTS),
        or 0 if the bet is not +EV.
    """
    if fraction <= 0:
        return 0
    f = kelly_fraction(win_prob, price)
    if f <= 0:
        return 0

    scaled_f = f * fraction
    dollar_amount = float(bankroll) * scaled_f

    if price <= 0:
        return 0
    contracts = math.floor(dollar_amount / price)

    max_by_cost = math.floor(float(MAX_COST_DOLLARS) / price)
    contracts = min(contracts, max_by_cost, MAX_CONTRACTS)
    if contracts < MIN_CONTRACTS:
        return 0
    return contracts


def quarter_kelly_size(
    win_prob: float,
    price: float,
    bankroll: Decimal,
) -> int:
    """Back-compat wrapper: quarter-Kelly sizing."""
    return kelly_size(win_prob, price, bankroll, fraction=0.25)
