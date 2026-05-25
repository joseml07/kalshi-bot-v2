"""Bitstamp WebSocket price feed client."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import websockets

logger = logging.getLogger(__name__)

BITSTAMP_WS_URL = "wss://ws.bitstamp.net"
CHANNELS = {"live_trades_btcusd": "BTC", "live_trades_ethusd": "ETH", "live_trades_solusd": "SOL"}


class BitstampFeed:
    """Async Bitstamp WebSocket price feed.

    Streams real-time trade prices. Call `get_price(symbol)` to read
    the latest price.
    """

    def __init__(self) -> None:
        self._prices: dict[str, float] = {}
        self._timestamps: dict[str, float] = {}
        self._running = False
        self._ws: Any = None
        self._last_tick_mono: float | None = None

    def get_price(self, symbol: str) -> tuple[float, float] | None:
        """Return (price, age_seconds) or None if no data."""
        price = self._prices.get(symbol)
        ts = self._timestamps.get(symbol)
        if price is None or ts is None:
            return None
        age = time.monotonic() - ts
        return price, age

    async def start(self) -> None:
        self._running = True
        while self._running:
            try:
                await self._connect_and_stream()
            except (websockets.ConnectionClosed, OSError) as e:
                if not self._running:
                    break
                logger.warning("Bitstamp WS disconnected: %s. Reconnecting in 3s...", e)
                await asyncio.sleep(3)
            except Exception:
                if not self._running:
                    break
                logger.exception("Bitstamp WS unexpected error. Reconnecting in 3s...")
                await asyncio.sleep(3)

    async def stop(self) -> None:
        self._running = False
        if self._ws is not None:
            await self._ws.close()

    async def _connect_and_stream(self) -> None:
        async with websockets.connect(BITSTAMP_WS_URL) as ws:
            self._ws = ws
            for channel in CHANNELS:
                subscribe = {
                    "event": "bts:subscribe",
                    "data": {"channel": channel},
                }
                await ws.send(json.dumps(subscribe))
            logger.info("bitstamp_ws_connected")

            async for raw in ws:
                if not self._running:
                    break
                if isinstance(raw, bytes):
                    raw = raw.decode()

                msg: dict[str, Any] = json.loads(raw)
                event = msg.get("event")
                if event != "trade":
                    continue

                channel = msg.get("channel", "")
                symbol = CHANNELS.get(channel)
                if symbol is None:
                    continue

                data = msg.get("data", {})
                price = data.get("price")
                if price is None:
                    continue

                self._prices[symbol] = float(price)
                self._timestamps[symbol] = time.monotonic()
                self._last_tick_mono = time.monotonic()

    @property
    def last_tick_age_s(self) -> float | None:
        if self._last_tick_mono is None:
            return None
        return max(0.0, time.monotonic() - self._last_tick_mono)
