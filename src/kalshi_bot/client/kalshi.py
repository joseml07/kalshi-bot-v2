"""Async Kalshi REST API client."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from decimal import Decimal
from typing import Any

import httpx

from kalshi_bot.client.auth import load_private_key, sign_request
from kalshi_bot.config import Settings
from kalshi_bot.models.market import Market, OrderBook, OrderBookLevel


class RateLimiter:
    """Token bucket rate limiter."""

    def __init__(self, rate: float, burst: int) -> None:
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens < 1:
                wait = (1 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._tokens = 0
            else:
                self._tokens -= 1


class KalshiClient:
    """Async client for the Kalshi REST API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._private_key = load_private_key(settings.kalshi_private_key_path)
        self._api_key = settings.kalshi_api_key
        self._base_url = settings.rest_base_url
        self._read_limiter = RateLimiter(rate=20, burst=20)
        self._write_limiter = RateLimiter(rate=10, burst=10)
        self._client = httpx.AsyncClient(timeout=10.0)
        self._read_calls_recent: deque[float] = deque()
        self._write_calls_recent: deque[float] = deque()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        """Generate auth headers for a request."""
        headers = sign_request(self._private_key, method, path)
        headers["KALSHI-ACCESS-KEY"] = self._api_key
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        is_write: bool = False,
    ) -> Any:
        """Make an authenticated API request with rate limiting and retries."""
        limiter = self._write_limiter if is_write else self._read_limiter
        url = f"{self._base_url}{path}"
        last_resp: httpx.Response | None = None
        last_error: Exception | None = None

        for attempt in range(3):
            await limiter.acquire()
            if is_write:
                self._record_write_call()
            else:
                self._record_read_call()
            headers = self._auth_headers(method, f"/trade-api/v2{path}")
            try:
                resp = await self._client.request(
                    method, url, headers=headers, params=params, json=json_body
                )
            except httpx.RequestError as exc:
                last_error = exc
                wait = 2**attempt
                await asyncio.sleep(wait)
                continue

            last_resp = resp

            if resp.status_code == 429:
                wait = 2**attempt
                await asyncio.sleep(wait)
                continue

            if resp.status_code >= 500:
                wait = 2**attempt
                await asyncio.sleep(wait)
                continue

            resp.raise_for_status()
            if resp.status_code == 204:
                return None
            return resp.json()

        if last_resp is not None:
            last_resp.raise_for_status()
        if last_error is not None:
            raise last_error
        raise RuntimeError("Kalshi request failed without response")

    # --- Market endpoints ---

    async def get_open_markets(self, series_ticker: str) -> list[Market]:
        """Fetch all open markets for a series."""
        markets: list[Market] = []
        cursor: str | None = None

        while True:
            params: dict[str, str] = {
                "series_ticker": series_ticker,
                "status": "open",
                "limit": "100",
            }
            if cursor:
                params["cursor"] = cursor

            data = await self._request("GET", "/markets", params=params)
            for m in data["markets"]:
                markets.append(_parse_market(m))

            cursor = data.get("cursor")
            if not cursor:
                break

        return markets

    async def get_market(self, ticker: str) -> Market:
        """Fetch a single market by ticker."""
        data = await self._request("GET", f"/markets/{ticker}")
        return _parse_market(data["market"])

    async def get_orderbook(self, ticker: str) -> OrderBook:
        """Fetch the order book for a market."""
        data = await self._request("GET", f"/markets/{ticker}/orderbook")
        ob = data.get("orderbook_fp", data.get("orderbook", {}))
        return OrderBook(
            ticker=ticker,
            yes_levels=_parse_fp_levels(ob.get("yes_dollars", [])),
            no_levels=_parse_fp_levels(ob.get("no_dollars", [])),
        )

    # --- Portfolio endpoints ---

    async def get_balance(self) -> Decimal:
        """Get the account balance in dollars."""
        data = await self._request("GET", "/portfolio/balance")
        # API returns balance in cents; convert to dollars
        return Decimal(str(data["balance"])) / 100

    async def get_positions(self) -> list[dict[str, Any]]:
        """Get all current positions."""
        data = await self._request("GET", "/portfolio/positions")
        positions: list[dict[str, Any]] = data.get("market_positions", [])
        return positions

    async def place_order(
        self,
        ticker: str,
        action: str,
        side: str,
        price_dollars: Decimal,
        count: int,
    ) -> dict[str, Any]:
        """Place a limit order.

        Args:
            ticker: Market ticker.
            action: "buy" or "sell".
            side: "yes" or "no".
            price_dollars: Price as decimal dollars (e.g., Decimal("0.45")).
            count: Number of contracts.

        Returns:
            Order response dict.
        """
        body: dict[str, Any] = {
            "ticker": ticker,
            "action": action,
            "side": side,
            "type": "limit",
            "yes_price_dollars": str(price_dollars) if side == "yes" else None,
            "no_price_dollars": str(price_dollars) if side == "no" else None,
            "count": count,
            "client_order_id": str(uuid.uuid4()),
        }
        # Remove None price field
        body = {k: v for k, v in body.items() if v is not None}
        result = await self._request(
            "POST", "/portfolio/orders", json_body=body, is_write=True
        )
        order: dict[str, Any] = result["order"]
        return order

    async def get_order(self, order_id: str) -> dict[str, Any]:
        """Fetch a single order by ID."""
        data = await self._request("GET", f"/portfolio/orders/{order_id}")
        order: dict[str, Any] = data["order"]
        return order

    async def cancel_order(self, order_id: str) -> None:
        """Cancel an open order."""
        await self._request("DELETE", f"/portfolio/orders/{order_id}", is_write=True)

    def _trim_calls(self, bucket: deque[float], window_s: float) -> None:
        now = time.monotonic()
        while bucket and (now - bucket[0]) > window_s:
            bucket.popleft()

    def _record_read_call(self) -> None:
        self._read_calls_recent.append(time.monotonic())
        self._trim_calls(self._read_calls_recent, 60.0)

    def _record_write_call(self) -> None:
        self._write_calls_recent.append(time.monotonic())
        self._trim_calls(self._write_calls_recent, 60.0)

    def api_utilization(self) -> dict[str, float]:
        """Approximate read/write utilization vs configured limits.

        Returns values in [0, 1+] where 1.0 means at configured limit.
        """
        self._trim_calls(self._read_calls_recent, 1.0)
        read_per_sec = float(len(self._read_calls_recent))
        self._trim_calls(self._write_calls_recent, 1.0)
        write_per_sec = float(len(self._write_calls_recent))

        read_util = read_per_sec / 20.0
        write_util = write_per_sec / 10.0
        return {
            "read_per_sec": read_per_sec,
            "write_per_sec": write_per_sec,
            "read_utilization": read_util,
            "write_utilization": write_util,
        }


def _parse_market(data: dict[str, Any]) -> Market:
    """Parse raw API market data into a Market model."""
    return Market(
        ticker=data["ticker"],
        series_ticker=data.get("series_ticker", ""),
        title=data.get("title", ""),
        status=data["status"],
        open_time=data["open_time"],
        close_time=data["close_time"],
        yes_ask=Decimal(data["yes_ask"]) if data.get("yes_ask") else None,
        yes_bid=Decimal(data["yes_bid"]) if data.get("yes_bid") else None,
        no_ask=Decimal(data["no_ask"]) if data.get("no_ask") else None,
        no_bid=Decimal(data["no_bid"]) if data.get("no_bid") else None,
        volume=data.get("volume", 0),
    )


def _parse_fp_levels(levels: list[list[str]]) -> list[OrderBookLevel]:
    """Parse fixed-point orderbook levels: [[price_str, qty_str], ...]."""
    result: list[OrderBookLevel] = []
    for entry in levels:
        result.append(
            OrderBookLevel(
                price=Decimal(entry[0]),
                quantity=int(Decimal(entry[1])),
            )
        )
    return result
