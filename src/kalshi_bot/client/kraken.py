"""Kraken WebSocket price feed client."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import websockets

logger = logging.getLogger(__name__)

KRAKEN_WS_URL = "wss://ws.kraken.com/v2"
PAIRS = {"BTC/USD": "BTC", "ETH/USD": "ETH", "SOL/USD": "SOL"}


class KrakenFeed:
    """Async Kraken WebSocket price feed.

    Streams real-time ticker prices. Does NOT output PriceTicks — this is
    a standalone price source for composite pricing. Call `get_price(symbol)`
    to read the latest price.
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
                logger.warning("Kraken WS disconnected: %s. Reconnecting in 3s...", e)
                await asyncio.sleep(3)
            except Exception:
                if not self._running:
                    break
                logger.exception("Kraken WS unexpected error. Reconnecting in 3s...")
                await asyncio.sleep(3)

    async def stop(self) -> None:
        self._running = False
        if self._ws is not None:
            await self._ws.close()

    async def _connect_and_stream(self) -> None:
        async with websockets.connect(KRAKEN_WS_URL) as ws:
            self._ws = ws
            subscribe = {
                "method": "subscribe",
                "params": {
                    "channel": "ticker",
                    "symbol": list(PAIRS.keys()),
                },
            }
            await ws.send(json.dumps(subscribe))
            logger.info("kraken_ws_connected")

            async for raw in ws:
                if not self._running:
                    break
                if isinstance(raw, bytes):
                    raw = raw.decode()

                msg: dict[str, Any] = json.loads(raw)
                channel = msg.get("channel")
                if channel != "ticker":
                    continue

                for item in msg.get("data", []):
                    pair = item.get("symbol", "")
                    symbol = PAIRS.get(pair)
                    if symbol is None:
                        continue
                    last = item.get("last")
                    if last is None:
                        continue
                    self._prices[symbol] = float(last)
                    self._timestamps[symbol] = time.monotonic()
                    self._last_tick_mono = time.monotonic()

    @property
    def last_tick_age_s(self) -> float | None:
        if self._last_tick_mono is None:
            return None
        return max(0.0, time.monotonic() - self._last_tick_mono)
