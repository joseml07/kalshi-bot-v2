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
        max_attempts: int = 3,
        timeout_override: float | None = None,
    ) -> Any:
        """Make an authenticated API request with rate limiting and retries."""
        limiter = self._write_limiter if is_write else self._read_limiter
        url = f"{self._base_url}{path}"
        last_resp: httpx.Response | None = None
        last_error: Exception | None = None

        for attempt in range(max_attempts):
            await limiter.acquire()
            if is_write:
                self._record_write_call()
            else:
                self._record_read_call()
            headers = self._auth_headers(method, f"/trade-api/v2{path}")
            try:
                resp = await self._client.request(
                    method, url, headers=headers, params=params, json=json_body,
                    timeout=timeout_override,
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
        *,
        time_in_force: str = "good_till_canceled",
        max_attempts: int = 3,
        timeout_override: float | None = None,
    ) -> dict[str, Any]:
        """Place a limit order via the Kalshi V2 create-order endpoint.

        Kalshi deprecated the legacy ``POST /portfolio/orders`` endpoint — it
        now returns ``410 deprecated_v1_order_endpoint``. The V2 endpoint
        ``POST /portfolio/events/orders`` uses a single YES-book model where
        ``side`` is ``bid``/``ask`` and ``price`` is the YES-leg price. We map
        the bot's (action, yes/no side, side-price) into that model:

            buy  yes @ p  -> bid, yes_price = p
            sell yes @ p  -> ask, yes_price = p
            buy  no  @ p  -> ask, yes_price = 1 - p   (selling YES == buying NO)
            sell no  @ p  -> bid, yes_price = 1 - p

        Args:
            ticker: Market ticker.
            action: "buy" or "sell".
            side: "yes" or "no".
            price_dollars: Price as decimal dollars for the given side.
            count: Number of contracts.
            time_in_force: Kalshi TIF (default good_till_canceled — a resting
                limit, matching the legacy ``type: limit`` lifecycle the
                executor polls and cancels).
            max_attempts: Number of retry attempts (default 3).
            timeout_override: Per-request timeout in seconds (None = client default).

        Returns:
            Flat V2 order response: order_id, client_order_id, fill_count,
            remaining_count, ts_ms (NOT nested under "order").
        """
        p = Decimal(str(price_dollars))
        if side == "yes":
            yes_price = p
            book_side = "bid" if action == "buy" else "ask"
        elif side == "no":
            yes_price = Decimal("1") - p
            book_side = "ask" if action == "buy" else "bid"
        else:
            raise ValueError(f"unknown side: {side!r}")
        # Clamp to the tradeable 1c-99c band (fixed-point dollar string).
        yes_price = max(Decimal("0.01"), min(Decimal("0.99"), yes_price))
        body: dict[str, Any] = {
            "ticker": ticker,
            "side": book_side,
            "count": str(int(count)),
            "price": f"{yes_price:.2f}",
            "time_in_force": time_in_force,
            "self_trade_prevention_type": "taker_at_cross",
            "client_order_id": str(uuid.uuid4()),
        }
        result = await self._request(
            "POST", "/portfolio/events/orders", json_body=body, is_write=True,
            max_attempts=max_attempts, timeout_override=timeout_override,
        )
        return result

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
    def _opt_dec(key: str) -> Decimal | None:
        v = data.get(key)
        if v is None or v == "":
            return None
        try:
            return Decimal(str(v))
        except Exception:
            return None

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
        floor_strike=_opt_dec("floor_strike"),
        cap_strike=_opt_dec("cap_strike"),
        strike_type=data.get("strike_type") or None,
        expected_expiration_value=_opt_dec("expected_expiration_value"),
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
