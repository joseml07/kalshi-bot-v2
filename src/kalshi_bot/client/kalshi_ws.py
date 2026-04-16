"""Kalshi WebSocket orderbook feed.

Subscribes to the Kalshi `orderbook_delta` channel and maintains a live,
delta-updated in-memory orderbook for a set of tickers. Produces the same
`OrderBook` model consumed by strategies, so it is a drop-in replacement
for REST `get_orderbook()` calls with a 15-second staleness fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import websockets
import websockets.legacy.client as ws_legacy

from kalshi_bot.client.auth import load_private_key, sign_request
from kalshi_bot.config import Settings
from kalshi_bot.models.market import OrderBook, OrderBookLevel

logger = logging.getLogger(__name__)

# Path used when signing the WebSocket handshake (matches the WS URL path).
_WS_PATH = "/trade-api/ws/v2"

# Seconds to wait before reconnecting after a dropped connection.
_RECONNECT_BACKOFF = 3.0

# Alias: maps integer cent prices → resting quantity.
_LevelMap = dict[int, int]


@dataclass
class _BookState:
    """Mutable in-memory orderbook for a single ticker.

    Prices are stored as integer cents (45 → $0.45) for O(1) delta application
    and unambiguous hashing.
    """

    yes: _LevelMap = field(default_factory=dict)
    no: _LevelMap = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _state_to_orderbook(ticker: str, state: _BookState) -> OrderBook:
    """Convert a _BookState to the canonical OrderBook Pydantic model.

    Integer cent prices are divided by 100 to produce dollar Decimals,
    matching the format returned by the REST `get_orderbook()` call.
    """
    yes_levels = [
        OrderBookLevel(price=Decimal(cents) / 100, quantity=qty)
        for cents, qty in state.yes.items()
        if qty > 0
    ]
    no_levels = [
        OrderBookLevel(price=Decimal(cents) / 100, quantity=qty)
        for cents, qty in state.no.items()
        if qty > 0
    ]
    return OrderBook(ticker=ticker, yes_levels=yes_levels, no_levels=no_levels)


class KalshiOrderbookFeed:
    """Real-time Kalshi orderbook feed via WebSocket.

    Maintains a live cache of `OrderBook` objects for a configurable set of
    tickers. Auto-reconnects on disconnect. Safe to update the ticker set at
    runtime — resubscription happens within ~1 second.

    Usage::

        feed = KalshiOrderbookFeed(settings)
        task = asyncio.create_task(feed.start())
        await feed.set_tickers({"KXBTC15M-26APR101000"})

        result = feed.get_orderbook("KXBTC15M-26APR101000")
        if result is not None:
            orderbook, updated_at = result
    """

    def __init__(
        self,
        settings: Settings,
        eval_trigger: asyncio.Event | None = None,
    ) -> None:
        self._private_key = load_private_key(settings.kalshi_private_key_path)
        self._api_key = settings.kalshi_api_key
        self._ws_url = settings.ws_base_url
        self._eval_trigger = eval_trigger

        # Active ticker subscriptions — updated via set_tickers().
        self._tickers: set[str] = set()

        # In-memory orderbook state per ticker.
        self._books: dict[str, _BookState] = {}

        self._running = False
        self._ws: ws_legacy.WebSocketClientProtocol | None = None

        # Signals _stream_loop that _tickers changed and a resubscribe is needed.
        self._resubscribe_event = asyncio.Event()
        self._last_update_mono: float | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_orderbook(self, ticker: str) -> tuple[OrderBook, datetime] | None:
        """Return the cached orderbook and its last-update timestamp, or None.

        The caller is responsible for checking staleness before trusting the
        returned data.
        """
        state = self._books.get(ticker)
        if state is None:
            return None
        return _state_to_orderbook(ticker, state), state.updated_at

    async def set_tickers(self, tickers: set[str]) -> None:
        """Update the set of subscribed tickers.

        Safe to call from any coroutine while the feed is running.  If the
        ticker set changes, the stream loop will resubscribe within ~1 second.
        Stale book entries for removed tickers are evicted immediately.
        """
        if tickers == self._tickers:
            return
        # Evict books for tickers no longer needed.
        for t in list(self._books):
            if t not in tickers:
                del self._books[t]
        self._tickers = set(tickers)
        self._resubscribe_event.set()

    async def start(self) -> None:
        """Start the feed.  Reconnects automatically on any failure."""
        self._running = True
        while self._running:
            try:
                await self._connect_and_stream()
            except (
                websockets.ConnectionClosed,
                websockets.InvalidURI,
                OSError,
            ) as exc:
                if not self._running:
                    break
                logger.warning(
                    "kalshi_ws_disconnected exc=%s reconnecting_in=%.0fs",
                    exc,
                    _RECONNECT_BACKOFF,
                )
                await asyncio.sleep(_RECONNECT_BACKOFF)
            except Exception:
                if not self._running:
                    break
                logger.exception(
                    "kalshi_ws_unexpected_error reconnecting_in=%.0fs",
                    _RECONNECT_BACKOFF,
                )
                await asyncio.sleep(_RECONNECT_BACKOFF)

    async def stop(self) -> None:
        """Stop the feed and close the connection."""
        self._running = False
        if self._ws is not None:
            await self._ws.close()

    # ------------------------------------------------------------------
    # Internal: connection lifecycle
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """Build RSA-PSS signed headers for the WebSocket handshake."""
        headers = sign_request(self._private_key, "GET", _WS_PATH)
        headers["KALSHI-ACCESS-KEY"] = self._api_key
        return headers

    async def _connect_and_stream(self) -> None:
        """Open the WebSocket, subscribe to current tickers, then stream."""
        headers = self._auth_headers()
        async with ws_legacy.connect(self._ws_url, extra_headers=headers) as ws:
            self._ws = ws
            logger.info("kalshi_ws_connected url=%s", self._ws_url)

            # Subscribe to whatever tickers are active right now.
            if self._tickers:
                await self._send_subscribe(ws, self._tickers, msg_id=1)
            self._resubscribe_event.clear()

            await self._stream_loop(ws)

    async def _stream_loop(self, ws: ws_legacy.WebSocketClientProtocol) -> None:
        """Drain messages from the WebSocket.

        Uses a 1-second recv timeout so the loop can react to ticker changes
        signalled via _resubscribe_event without blocking on a slow market.
        """
        msg_id = 2
        while self._running:
            # Resubscribe if the ticker set changed.
            if self._resubscribe_event.is_set():
                self._resubscribe_event.clear()
                if self._tickers:
                    await self._send_subscribe(ws, self._tickers, msg_id=msg_id)
                    msg_id += 1

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if isinstance(raw, bytes):
                raw = raw.decode()
            await self._handle_message(json.loads(raw))

    async def _send_subscribe(
        self,
        ws: ws_legacy.WebSocketClientProtocol,
        tickers: set[str],
        msg_id: int,
    ) -> None:
        """Send a subscribe command for the given tickers."""
        msg = {
            "id": msg_id,
            "cmd": "subscribe",
            "params": {
                "channels": ["orderbook_delta"],
                "market_tickers": sorted(tickers),
            },
        }
        await ws.send(json.dumps(msg))
        logger.info("kalshi_ws_subscribed tickers=%s", sorted(tickers))

    # ------------------------------------------------------------------
    # Internal: message handling
    # ------------------------------------------------------------------

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        """Dispatch an incoming WebSocket message."""
        msg_type = msg.get("type")
        if msg_type == "orderbook_snapshot":
            self._apply_snapshot(msg.get("msg", {}))
        elif msg_type == "orderbook_delta":
            self._apply_delta(msg.get("msg", {}))
        elif msg_type == "error":
            logger.error("kalshi_ws_error msg=%s", msg)
        # "subscribed", "subscriptions", "heartbeat", etc. are silently ignored.

    def _apply_snapshot(self, data: dict[str, Any]) -> None:
        """Build a full orderbook from a subscription snapshot.

        Snapshot format::

            {
              "channel": "orderbook_delta",
              "market_ticker": "KXBTC15M-...",
              "yes": [["45", "100"], ...],
              "no":  [["55", "200"], ...]
            }

        Prices are integer-cent strings: "45" means $0.45.
        """
        ticker: str = data.get("market_ticker", "")
        if not ticker:
            return

        yes_map: _LevelMap = {}
        for entry in data.get("yes", []):
            cents, qty = int(entry[0]), int(entry[1])
            if qty > 0:
                yes_map[cents] = qty

        no_map: _LevelMap = {}
        for entry in data.get("no", []):
            cents, qty = int(entry[0]), int(entry[1])
            if qty > 0:
                no_map[cents] = qty

        self._books[ticker] = _BookState(
            yes=yes_map,
            no=no_map,
            updated_at=datetime.now(timezone.utc),
        )
        self._last_update_mono = time.monotonic()
        if self._eval_trigger is not None:
            self._eval_trigger.set()
        logger.info(
            "kalshi_ws_snapshot ticker=%s yes_levels=%d no_levels=%d",
            ticker,
            len(yes_map),
            len(no_map),
        )

    def _apply_delta(self, data: dict[str, Any]) -> None:
        """Apply an incremental delta to the cached orderbook.

        Delta format::

            {"market_ticker": "...", "side": "yes", "price": "45", "delta": "-50"}

        A negative delta reduces quantity; if the level reaches zero or below
        it is removed entirely.
        """
        ticker: str = data.get("market_ticker", "")
        state = self._books.get(ticker)
        if state is None:
            # Delta arrived before snapshot — ignore safely; snapshot will follow.
            logger.debug("kalshi_ws_delta_before_snapshot ticker=%s", ticker)
            return

        side: str = data.get("side", "")
        price_cents: int = int(data["price"])
        delta: int = int(data["delta"])

        book = state.yes if side == "yes" else state.no
        new_qty = book.get(price_cents, 0) + delta

        if new_qty <= 0:
            book.pop(price_cents, None)
            if new_qty < 0:
                logger.warning(
                    "kalshi_ws_negative_qty ticker=%s side=%s price=%d qty=%d",
                    ticker,
                    side,
                    price_cents,
                    new_qty,
                )
        else:
            book[price_cents] = new_qty

        state.updated_at = datetime.now(timezone.utc)
        self._last_update_mono = time.monotonic()
        if self._eval_trigger is not None:
            self._eval_trigger.set()

    @property
    def last_update_age_s(self) -> float | None:
        """Seconds since last Kalshi WS book update, or None if none seen."""
        if self._last_update_mono is None:
            return None
        return max(0.0, time.monotonic() - self._last_update_mono)
