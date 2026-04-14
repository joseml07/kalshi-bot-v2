"""Kalshi fee calculations."""

from __future__ import annotations

import math
from decimal import Decimal


def taker_fee(contracts: int, price: float) -> Decimal:
    """Taker fee: ceil(0.07 * C * P * (1-P) * 100) / 100."""
    raw = math.ceil(0.07 * contracts * price * (1 - price) * 100) / 100
    return Decimal(str(raw))


def maker_fee(contracts: int, price: float) -> Decimal:
    """Maker fee: ceil(0.0175 * C * P * (1-P) * 100) / 100."""
    raw = math.ceil(0.0175 * contracts * price * (1 - price) * 100) / 100
    return Decimal(str(raw))
