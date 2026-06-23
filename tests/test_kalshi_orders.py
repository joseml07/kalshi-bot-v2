"""Unit tests for KalshiClient.place_order V2 endpoint mapping.

Kalshi deprecated the legacy POST /portfolio/orders endpoint (410
deprecated_v1_order_endpoint). place_order now targets the V2 endpoint
POST /portfolio/events/orders with a single YES-book bid/ask model. These
tests pin the (action, yes/no side, price) -> (book side, yes price) mapping
that was verified against the live API on 2026-06-23.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from kalshi_bot.client.kalshi import KalshiClient


def _client_with_capture() -> tuple[KalshiClient, dict[str, Any]]:
    """Build a KalshiClient that captures the request instead of sending it."""
    client = KalshiClient.__new__(KalshiClient)  # skip __init__ (no keys/network)
    captured: dict[str, Any] = {}

    async def fake_request(method: str, path: str, *, json_body=None, **_kw: Any):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = json_body
        return {
            "order_id": "oid-1",
            "client_order_id": json_body["client_order_id"],
            "fill_count": "0.00",
            "remaining_count": json_body["count"],
            "ts_ms": 1,
        }

    client._request = fake_request  # type: ignore[method-assign]
    return client, captured


@pytest.mark.asyncio
async def test_place_order_uses_v2_endpoint() -> None:
    client, captured = _client_with_capture()
    await client.place_order("KXBTC15M-X", "buy", "yes", Decimal("0.45"), 3)
    assert captured["method"] == "POST"
    assert captured["path"] == "/portfolio/events/orders"
    body = captured["body"]
    assert body["count"] == "3"
    assert body["time_in_force"] == "good_till_canceled"
    assert "self_trade_prevention_type" in body
    assert body["client_order_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "side", "price", "exp_book_side", "exp_price"),
    [
        ("buy", "yes", "0.45", "bid", "0.45"),
        ("sell", "yes", "0.60", "ask", "0.60"),
        ("buy", "no", "0.07", "ask", "0.93"),   # buy NO == sell YES @ 1-p
        ("sell", "no", "0.30", "bid", "0.70"),  # sell NO == buy YES @ 1-p
    ],
)
async def test_place_order_side_price_mapping(
    action: str, side: str, price: str, exp_book_side: str, exp_price: str
) -> None:
    client, captured = _client_with_capture()
    await client.place_order("KXBTC15M-X", action, side, Decimal(price), 1)
    body = captured["body"]
    assert body["side"] == exp_book_side
    assert body["price"] == exp_price


@pytest.mark.asyncio
async def test_place_order_clamps_to_tradeable_band() -> None:
    client, captured = _client_with_capture()
    # buy NO @ 0.005 -> yes_price 0.995 -> clamped to 0.99
    await client.place_order("KXBTC15M-X", "buy", "no", Decimal("0.005"), 1)
    assert captured["body"]["price"] == "0.99"
    # buy YES @ 0.0 -> clamped up to 0.01
    await client.place_order("KXBTC15M-X", "buy", "yes", Decimal("0.0"), 1)
    assert captured["body"]["price"] == "0.01"


@pytest.mark.asyncio
async def test_place_order_rejects_unknown_side() -> None:
    client, _ = _client_with_capture()
    with pytest.raises(ValueError):
        await client.place_order("KXBTC15M-X", "buy", "maybe", Decimal("0.5"), 1)
