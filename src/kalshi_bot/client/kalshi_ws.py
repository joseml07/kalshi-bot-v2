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

# If we observe too many malformed deltas for a ticker, force resubscribe.
# Counter decays (does not reset) on good deltas, so bursts accumulate even
# when interleaved with valid traffic — a pure reset-on-good was observed to
# never fire in production because good and bad deltas arrive concurrently.
# Set high because negative_qty no longer contributes (clamped silently); only
# structural anomalies (bad_side, parse_error, missing_fields) count, and
# those are genuinely rare.
_ANOMALY_RESYNC_THRESHOLD = 25

# Minimum seconds between consecutive resyncs for the same ticker, to avoid
# thrashing when the upstream feed is genuinely degraded.
_RESYNC_COOLDOWN_S = 10.0

# If the sum of per-ticker anomaly counters crosses this while any single
# ticker is below its own threshold, escalate to a full resync of all books.
# Kept above the per-ticker threshold so a single hot ticker trips its own
# resync first; burst only fires when multiple tickers are flaking together.
_FULL_RESYNC_BURST_THRESHOLD = 50


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

        # Tickers that have been cleared by a resync and are waiting for a
        # fresh snapshot. Deltas for these tickers are dropped to prevent
        # stale in-flight deltas from the previous subscription from being
        # applied against the new snapshot and producing negative_qty.
        self._awaiting_snapshot: set[str] = set()

        self._running = False
        self._ws: ws_legacy.WebSocketClientProtocol | None = None

        # Signals _stream_loop that _tickers changed and a resubscribe is needed.
        self._resubscribe_event = asyncio.Event()
        self._last_update_mono: float | None = None
        self._anomaly_counts: dict[str, int] = {}
        self._last_seq_by_sid: dict[int, int] = {}
        self._stats: dict[str, int] = {
            "messages_total": 0,
            "messages_snapshot": 0,
            "messages_delta": 0,
            "delta_before_snapshot": 0,
            "delta_dropped_awaiting_snapshot": 0,
            "delta_missing_fields": 0,
            "delta_parse_error": 0,
            "delta_bad_side": 0,
            "negative_qty": 0,
            "sequence_gap": 0,
            "crossed_book": 0,
            "resync_ticker": 0,
            "resync_full": 0,
        }
        self._last_resync_reason: str | None = None
        self._last_resync_ticker: str | None = None
        self._last_resync_mono: float | None = None
        self._last_ticker_resync_mono: dict[str, float] = {}
        # Count of bad deltas observed per ticker since its last resync. Resets
        # on ticker resync (for that ticker) or full resync (all tickers).
        # Growing monotonically between resyncs means the feed is degrading
        # faster than we recover.
        self._bad_deltas_since_last_resync: dict[str, int] = {}

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

    def diagnostics(self) -> dict[str, Any]:
        """Return lightweight WS health diagnostics for dashboards/logging."""
        last_resync_age_s: float | None = None
        if self._last_resync_mono is not None:
            last_resync_age_s = max(0.0, time.monotonic() - self._last_resync_mono)
        return {
            **self._stats,
            "active_books": len(self._books),
            "tracked_tickers": len(self._tickers),
            "last_resync_reason": self._last_resync_reason,
            "last_resync_ticker": self._last_resync_ticker,
            "last_resync_age_s": last_resync_age_s,
        }

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
        # Drop barrier entries for tickers we no longer care about; any new
        # ticker will be added to the barrier when the subscribe fires at
        # reconnect/resubscribe time.
        self._awaiting_snapshot.intersection_update(tickers)
        self._awaiting_snapshot.update(tickers - set(self._books))
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
            self._last_seq_by_sid.clear()
            # Every tracked ticker must receive a fresh snapshot on this new
            # connection before we trust its book again.
            self._awaiting_snapshot.update(self._tickers)
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
        self._stats["messages_total"] += 1
        sid = msg.get("sid")
        seq = msg.get("seq")
        if isinstance(sid, int) and isinstance(seq, int):
            prev = self._last_seq_by_sid.get(sid)
            if prev is not None:
                if seq <= prev:
                    return
                if seq > prev + 1 and msg_type != "orderbook_snapshot":
                    self._stats["sequence_gap"] += 1
                    logger.warning(
                        "kalshi_ws_sequence_gap sid=%s prev=%s seq=%s",
                        sid,
                        prev,
                        seq,
                    )
                    self._schedule_full_resync(
                        f"sequence_gap sid={sid} prev={prev} seq={seq}"
                    )
                    return
            self._last_seq_by_sid[sid] = seq

        if msg_type == "orderbook_snapshot":
            self._stats["messages_snapshot"] += 1
            self._apply_snapshot(msg.get("msg", {}))
        elif msg_type == "orderbook_delta":
            self._stats["messages_delta"] += 1
            self._apply_delta(msg.get("msg", {}))
        elif msg_type == "error":
            logger.error("kalshi_ws_error msg=%s", msg)
        else:
            logger.debug(
                "kalshi_ws_unhandled type=%s keys=%s", msg_type, list(msg.keys())
            )

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

        yes_entries = data.get("yes") or data.get("yes_dollars_fp") or []
        no_entries = data.get("no") or data.get("no_dollars_fp") or []

        yes_map: _LevelMap = {}
        for entry in yes_entries:
            cents, qty = _parse_snapshot_entry(entry)
            if cents is None or qty is None:
                self._mark_anomaly(ticker, "snapshot_yes_parse")
                continue
            if qty > 0:
                yes_map[cents] = qty

        no_map: _LevelMap = {}
        for entry in no_entries:
            cents, qty = _parse_snapshot_entry(entry)
            if cents is None or qty is None:
                self._mark_anomaly(ticker, "snapshot_no_parse")
                continue
            if qty > 0:
                no_map[cents] = qty

        self._books[ticker] = _BookState(
            yes=yes_map,
            no=no_map,
            updated_at=datetime.now(timezone.utc),
        )
        self._awaiting_snapshot.discard(ticker)
        self._anomaly_counts[ticker] = 0
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
        if ticker in self._awaiting_snapshot:
            # Post-resync: drop any delta for this ticker until a fresh
            # snapshot lands. Stale deltas from the pre-resync stream would
            # otherwise be applied against the new book state and produce
            # spurious negative_qty events.
            self._stats["delta_dropped_awaiting_snapshot"] += 1
            return
        state = self._books.get(ticker)
        if state is None:
            # Delta arrived before snapshot — ignore safely; snapshot will follow.
            self._stats["delta_before_snapshot"] += 1
            logger.debug("kalshi_ws_delta_before_snapshot ticker=%s", ticker)
            return

        side: str = data.get("side", "")
        if side not in ("yes", "no"):
            self._stats["delta_bad_side"] += 1
            logger.warning("kalshi_ws_delta_bad_side ticker=%s side=%s", ticker, side)
            self._mark_anomaly(ticker, "bad_side")
            return

        # Support both field names: "price"/"delta" and "price_dollars"/"delta_fp"
        raw_price = data.get("price") or data.get("price_dollars")
        raw_delta = data.get("delta") or data.get("delta_fp")

        if raw_price is None or raw_delta is None:
            self._stats["delta_missing_fields"] += 1
            logger.warning(
                "kalshi_ws_delta_missing_fields ticker=%s keys=%s",
                ticker,
                list(data.keys()),
            )
            self._mark_anomaly(ticker, "missing_fields")
            return

        price_cents = _parse_price_to_cents(raw_price)
        delta = _parse_quantity_to_int(raw_delta)
        if price_cents is None or delta is None:
            self._stats["delta_parse_error"] += 1
            logger.error("kalshi_ws_delta_parse_error ticker=%s data=%s", ticker, data)
            self._mark_anomaly(ticker, "parse_error")
            return

        book = state.yes if side == "yes" else state.no
        new_qty = book.get(price_cents, 0) + delta

        if new_qty <= 0:
            book.pop(price_cents, None)
            if new_qty < 0:
                # Clamp silently. Kalshi occasionally sends deltas that
                # over-subtract (recovered snapshot, network reorder, etc.);
                # this is not a structural corruption and should not cascade
                # into a resync. Still count for observability.
                self._stats["negative_qty"] += 1
        else:
            book[price_cents] = new_qty

        # Cross-prevention: a YES bid at P and a NO bid at Q with P+Q>100
        # is impossible (instant arb).  When a new delta adds/grows a level,
        # trim opposite-side levels that would create a cross.  The newest
        # delta is the freshest information, so the updated side is
        # authoritative and stale opposite levels are removed.
        if new_qty > 0:
            opposite = state.no if side == "yes" else state.yes
            cutoff = 100 - price_cents  # max valid opposite price
            stale = [p for p in opposite if p > cutoff]
            if stale:
                for p in stale:
                    opposite.pop(p)
                self._stats["crossed_book"] += 1

        state.updated_at = datetime.now(timezone.utc)
        self._last_update_mono = time.monotonic()
        if self._eval_trigger is not None:
            self._eval_trigger.set()

    def _mark_anomaly(self, ticker: str, reason: str) -> None:
        """Track WS anomalies and force resubscribe if persistent."""
        count = self._anomaly_counts.get(ticker, 0) + 1
        self._anomaly_counts[ticker] = count
        self._bad_deltas_since_last_resync[ticker] = (
            self._bad_deltas_since_last_resync.get(ticker, 0) + 1
        )
        if count >= _ANOMALY_RESYNC_THRESHOLD:
            self._anomaly_counts[ticker] = 0
            self._schedule_ticker_resync(ticker, reason, trigger_count=count)
            return
        # Escalate to full resync if many tickers are flaky at once — a single
        # ticker may not cross its own threshold but cumulative instability is
        # a strong signal that the whole feed is degraded.
        total = sum(self._anomaly_counts.values())
        if total >= _FULL_RESYNC_BURST_THRESHOLD:
            self._anomaly_counts.clear()
            self._schedule_full_resync(f"anomaly_burst total={total}")

    def _schedule_ticker_resync(
        self, ticker: str, reason: str, trigger_count: int = 0
    ) -> None:
        """Clear one ticker book and request fresh snapshot(s)."""
        now = time.monotonic()
        last = self._last_ticker_resync_mono.get(ticker)
        if last is not None and (now - last) < _RESYNC_COOLDOWN_S:
            return
        since_last_s: float | None = None
        if last is not None:
            since_last_s = now - last
        bad_deltas = self._bad_deltas_since_last_resync.get(ticker, 0)
        self._last_ticker_resync_mono[ticker] = now
        self._bad_deltas_since_last_resync[ticker] = 0
        self._stats["resync_ticker"] += 1
        self._last_resync_reason = reason
        self._last_resync_ticker = ticker
        self._last_resync_mono = now
        self._awaiting_snapshot.add(ticker)
        self._books.pop(ticker, None)
        self._resubscribe_event.set()
        logger.warning(
            "kalshi_ws_resync_ticker ticker=%s reason=%s "
            "anomaly_count=%d bad_deltas_since_last_resync=%d "
            "since_last_resync_s=%s",
            ticker,
            reason,
            trigger_count,
            bad_deltas,
            f"{since_last_s:.1f}" if since_last_s is not None else "none",
        )

    def _schedule_full_resync(self, reason: str) -> None:
        """Clear all books and request fresh snapshots for active tickers."""
        now = time.monotonic()
        since_last_s: float | None = None
        if self._last_resync_mono is not None:
            since_last_s = now - self._last_resync_mono
        total_bad = sum(self._bad_deltas_since_last_resync.values())
        anomaly_total = sum(self._anomaly_counts.values())
        self._stats["resync_full"] += 1
        self._last_resync_reason = reason
        self._last_resync_ticker = None
        self._last_resync_mono = now
        self._bad_deltas_since_last_resync.clear()
        self._awaiting_snapshot.update(self._tickers)
        self._books.clear()
        self._resubscribe_event.set()
        logger.warning(
            "kalshi_ws_resync_full reason=%s anomaly_total=%d "
            "bad_deltas_since_last_resync=%d since_last_resync_s=%s",
            reason,
            anomaly_total,
            total_bad,
            f"{since_last_s:.1f}" if since_last_s is not None else "none",
        )

    @property
    def last_update_age_s(self) -> float | None:
        """Seconds since last Kalshi WS book update, or None if none seen."""
        if self._last_update_mono is None:
            return None
        return max(0.0, time.monotonic() - self._last_update_mono)


def _parse_price_to_cents(raw_price: Any) -> int | None:
    """Parse WS price field to integer cents.

    Accepts both legacy cent integers ("45") and dollar strings ("0.45").
    """
    try:
        value = float(raw_price)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    if value < 1.0:
        return int(round(value * 100))
    return int(round(value))


def _parse_quantity_to_int(raw_qty: Any) -> int | None:
    """Parse WS quantity/delta fields to integer contracts."""
    try:
        return int(round(float(raw_qty)))
    except (TypeError, ValueError):
        return None


def _parse_snapshot_entry(entry: Any) -> tuple[int | None, int | None]:
    """Parse one snapshot level entry [price, qty]."""
    if not isinstance(entry, list) or len(entry) < 2:
        return None, None
    cents = _parse_price_to_cents(entry[0])
    qty = _parse_quantity_to_int(entry[1])
    return cents, qty
