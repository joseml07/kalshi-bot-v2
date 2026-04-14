"""Quarter-Kelly sizing tests."""

from __future__ import annotations

from decimal import Decimal

from kalshi_bot.risk.sizing import MAX_CONTRACTS, kelly_fraction, quarter_kelly_size


def test_kelly_fraction_non_positive_edge() -> None:
    assert kelly_fraction(0.4, 0.5) == 0.0


def test_kelly_fraction_positive_edge() -> None:
    frac = kelly_fraction(0.6, 0.4)
    assert frac > 0


def test_quarter_kelly_size_capped_at_max_contracts() -> None:
    size = quarter_kelly_size(0.95, 0.05, Decimal("10000"))
    assert size == MAX_CONTRACTS == 10


def test_quarter_kelly_size_returns_zero_when_not_plus_ev() -> None:
    assert quarter_kelly_size(0.4, 0.5, Decimal("100")) == 0
