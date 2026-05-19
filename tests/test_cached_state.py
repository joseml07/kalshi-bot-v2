"""Tests for main-loop cached state helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from kalshi_bot.main import CachedState
from kalshi_bot.models.market import Market


class _ClientStub:
    def __init__(self, balances: list[Decimal]) -> None:
        self._balances = balances
        self.calls = 0

    async def get_balance(self) -> Decimal:
        self.calls += 1
        if self.calls <= len(self._balances):
            return self._balances[self.calls - 1]
        return self._balances[-1]


async def test_cached_state_refresh_balance_respects_ttl() -> None:
    cached = CachedState()
    client = _ClientStub([Decimal("100.00"), Decimal("200.00")])

    await cached.refresh_balance(client, ttl_s=10.0)
    await cached.refresh_balance(client, ttl_s=10.0)

    assert client.calls == 1
    assert cached.balance == Decimal("100.00")


async def test_cached_state_refresh_balance_updates_on_fetch() -> None:
    cached = CachedState()
    client = _ClientStub([Decimal("123.45")])

    await cached.refresh_balance(client, ttl_s=0.0)

    assert client.calls == 1
    assert cached.balance == Decimal("123.45")


async def test_cached_state_refresh_balance_uses_simulated_balance() -> None:
    cached = CachedState()
    client = _ClientStub([Decimal("123.45")])

    await cached.refresh_balance(client, simulated_balance=Decimal("25.00"))

    assert client.calls == 0
    assert cached.balance == Decimal("25.00")


def test_cached_state_get_market_returns_none_when_missing() -> None:
    cached = CachedState()

    assert cached.get_market("BTC") is None


def test_cached_state_get_market_returns_cached_market() -> None:
    cached = CachedState()
    market = Market(
        ticker="KXBTC15M-TEST",
        series_ticker="KXBTC15M",
        title="test",
        status="open",
        open_time=datetime.now(timezone.utc),
        close_time=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    cached._markets["BTC"] = market

    assert cached.get_market("BTC") == market
