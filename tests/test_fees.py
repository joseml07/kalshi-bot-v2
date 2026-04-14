"""Fee calculation tests."""

from __future__ import annotations

from decimal import Decimal

from kalshi_bot.strategy.fees import maker_fee, taker_fee


def test_taker_fee_at_50_cents() -> None:
    assert taker_fee(1, 0.50) == Decimal("0.02")
    assert taker_fee(10, 0.50) == Decimal("0.18")


def test_maker_fee_at_50_cents() -> None:
    assert maker_fee(1, 0.50) == Decimal("0.01")
    assert maker_fee(10, 0.50) == Decimal("0.05")
