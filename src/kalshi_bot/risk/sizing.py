"""Quarter-Kelly position sizing."""

from __future__ import annotations

import math
from decimal import Decimal

# Hard limits
MIN_CONTRACTS = 1
MAX_CONTRACTS = 10
MAX_COST_DOLLARS = Decimal("25.00")


def kelly_fraction(win_prob: float, price: float) -> float:
    """Full Kelly fraction for a binary bet.

    For a binary contract bought at `price` that pays $1 on win:
        profit_if_win = 1 - price
        loss_if_lose  = price
        kelly = (win_prob * profit_if_win - (1 - win_prob) * loss_if_lose) / profit_if_win
             = (win_prob * (1 - price) - (1 - win_prob) * price) / (1 - price)
             = (win_prob - price) / (1 - price)
    """
    if price <= 0 or price >= 1:
        return 0.0
    edge = win_prob - price
    if edge <= 0:
        return 0.0
    return edge / (1 - price)


def quarter_kelly_size(
    win_prob: float,
    price: float,
    bankroll: Decimal,
) -> int:
    """Compute position size using quarter-Kelly criterion.

    Args:
        win_prob: Estimated probability of winning the contract.
        price: Contract price in dollars (0 < price < 1).
        bankroll: Current available bankroll in dollars.

    Returns:
        Number of contracts to buy (between MIN_CONTRACTS and MAX_CONTRACTS),
        or 0 if the bet is not +EV.
    """
    f = kelly_fraction(win_prob, price)
    if f <= 0:
        return 0

    quarter_f = f / 4.0
    dollar_amount = float(bankroll) * quarter_f

    # Cost per contract is the price
    if price <= 0:
        return 0
    contracts = math.floor(dollar_amount / price)

    # Enforce max cost
    max_by_cost = math.floor(float(MAX_COST_DOLLARS) / price)
    contracts = min(contracts, max_by_cost)

    # Clamp to bounds
    contracts = min(contracts, MAX_CONTRACTS)
    if contracts < MIN_CONTRACTS:
        return 0

    return contracts
