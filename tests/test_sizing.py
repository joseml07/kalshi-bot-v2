"""Fractional-Kelly sizing tests."""

from __future__ import annotations

from decimal import Decimal

from kalshi_bot.risk.sizing import (
    MAX_CONTRACTS,
    kelly_fraction,
    kelly_size,
    quarter_kelly_size,
)


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


def test_kelly_size_scales_with_fraction() -> None:
    # Modest edge + small bankroll so caps don't bite.
    bankroll = Decimal("100")
    win_prob, price = 0.55, 0.50
    quarter = kelly_size(win_prob, price, bankroll, fraction=0.25)
    half = kelly_size(win_prob, price, bankroll, fraction=0.50)
    assert quarter >= 1
    assert half >= 2 * quarter - 1  # floor() means near-linear, not exact


def test_kelly_size_zero_fraction_returns_zero() -> None:
    assert kelly_size(0.9, 0.1, Decimal("100"), fraction=0.0) == 0


def test_quarter_kelly_size_matches_kelly_size_quarter() -> None:
    bankroll = Decimal("500")
    assert quarter_kelly_size(0.7, 0.4, bankroll) == kelly_size(
        0.7, 0.4, bankroll, fraction=0.25
    )
