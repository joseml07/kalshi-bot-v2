"""Coinbase WebSocket price feed client."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from datetime import datetime
from typing import Any

import websockets
from websockets.legacy.client import WebSocketClientProtocol

from kalshi_bot.models.price import PriceTick

COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"
PRODUCTS = ["BTC-USD", "ETH-USD", "SOL-USD"]
# Map Coinbase product IDs to our symbols
SYMBOL_MAP = {"BTC-USD": "BTC", "ETH-USD": "ETH", "SOL-USD": "SOL"}

logger = logging.getLogger(__name__)


class CoinbaseFeed:
    """Async Coinbase WebSocket price feed.

    Streams real-time prices and places PriceTick objects onto an async queue.
    Auto-reconnects on disconnect.
    """

    def __init__(
        self,
        queue: asyncio.Queue[PriceTick],
        products: list[str] | None = None,
    ) -> None:
        self._queue = queue
        self._products = products or PRODUCTS
        self._running = False
        self._ws: WebSocketClientProtocol | None = None
        self._last_tick_mono: float | None = None

    async def start(self) -> None:
        """Start the feed. Reconnects automatically on failure."""
        self._running = True
        while self._running:
            try:
                await self._connect_and_stream()
            except (
                websockets.ConnectionClosed,
                websockets.InvalidURI,
                OSError,
            ) as e:
                if not self._running:
                    break
                logger.warning("Coinbase WS disconnected: %s. Reconnecting in 3s...", e)
                await asyncio.sleep(3)

    async def stop(self) -> None:
        """Stop the feed and close the connection."""
        self._running = False
        if self._ws is not None:
            await self._ws.close()

    async def _connect_and_stream(self) -> None:
        """Connect to Coinbase and stream ticker messages."""
        async with websockets.connect(COINBASE_WS_URL) as ws:
            self._ws = ws
            subscribe_msg = json.dumps(
                {
                    "type": "subscribe",
                    "channels": [{"name": "ticker", "product_ids": self._products}],
                }
            )
            await ws.send(subscribe_msg)

            async for raw_msg in ws:
                if not self._running:
                    break

                if isinstance(raw_msg, bytes):
                    raw_msg = raw_msg.decode()

                msg: dict[str, Any] = json.loads(raw_msg)
                if msg.get("type") != "ticker":
                    continue

                product_id = msg.get("product_id", "")
                symbol = SYMBOL_MAP.get(product_id)
                if symbol is None:
                    continue

                price_str = msg.get("price")
                time_str = msg.get("time")
                if price_str is None or time_str is None:
                    continue

                tick = PriceTick(
                    symbol=symbol,
                    price=float(price_str),
                    timestamp=datetime.fromisoformat(time_str.replace("Z", "+00:00")),
                )
                self._last_tick_mono = time.monotonic()
                try:
                    self._queue.put_nowait(tick)
                except asyncio.QueueFull:
                    # Drop oldest tick if queue is full
                    with contextlib.suppress(asyncio.QueueEmpty):
                        self._queue.get_nowait()
                    self._queue.put_nowait(tick)

    @property
    def last_tick_age_s(self) -> float | None:
        """Seconds since last Coinbase tick, or None if never."""
        if self._last_tick_mono is None:
            return None
        return max(0.0, time.monotonic() - self._last_tick_mono)
