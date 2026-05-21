"""Order executor — place, track, cancel, and log orders."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from kalshi_bot.client.kalshi import KalshiClient
from kalshi_bot.config import Settings
from kalshi_bot.models.market import OrderBook
from kalshi_bot.risk.manager import RiskManager
from kalshi_bot.risk.sizing import DEFAULT_KELLY_FRACTION, kelly_size
from kalshi_bot.strategy.fees import maker_fee, taker_fee
from kalshi_bot.strategy.asset_config import maker_timeout_for_strength
from kalshi_bot.strategy.signals import Signal

logger = logging.getLogger(__name__)

ORDER_TIMEOUT_SECONDS = 60
DB_PATH = "trades.db"

# Promote-to-taker safety gates. An earlier version blindly re-placed at the
# stale maker-era taker_price, which for a momentum strategy is the worst
# possible moment: the market has run away from us by 90s, the edge is gone,
# and we were still paying the taker fee. These constants let us abort instead.
PROMOTE_MIN_NET_EDGE = 0.02
PROMOTE_FRESH_ORDERBOOK_MAX_AGE_S = 5.0
PROMOTE_BIG_PRICE_MOVE = 0.03

# Taker execution improvements
TAKER_SLIPPAGE_BUFFER = Decimal("0.02")  # pay up to 2c worse to guarantee fill
EXIT_SELL_BUFFER = Decimal("0.01")       # sell 1c below bid to guarantee exit fill
MIN_PROMOTE_DEPTH = 15                   # contracts visible before taker promo
TAKER_FILL_HORIZON_S = 120               # longer timeout for taker orders
MAX_PROMOTE_RETRIES = 1


class OrderState(str, Enum):
    """Lifecycle state of a tracked order."""

    PENDING = "pending"
    FILLED = "filled"
    EXITING = "exiting"
    CANCELLED = "cancelled"
    SETTLED = "settled"


class TrackedOrder:
    """An order being managed by the executor."""

    def __init__(
        self,
        signal: Signal,
        order_id: str,
        contracts: int,
        price: Decimal,
    ) -> None:
        self.signal = signal
        self.order_id = order_id
        self.contracts = contracts
        self.price = price
        self.intended_price: Decimal = price
        self.state = OrderState.PENDING
        self.placed_at = time.monotonic()
        self.fill_time: float | None = None
        self.pnl: Decimal | None = None
        self.negative_edge_count: int = 0
        self.timeout: int = (
            20 if signal.seconds_remaining < 120 else ORDER_TIMEOUT_SECONDS
        )
        self.route: str = signal.route if hasattr(signal, "route") else "taker"
        self.taker_price: Decimal | None = getattr(signal, "taker_price", None)
        self.maker_timeout: int = maker_timeout_for_strength(
            signal.signal_strength, signal.symbol, global_horizon=90
        )
        # Exit sell tracking (populated when state transitions to EXITING)
        self.exit_order_id: str | None = None
        self.exit_price: Decimal | None = None
        self.exit_reason: str | None = None

    @property
    def fee_per_contract(self) -> float:
        """Fee per contract in dollars based on execution route."""
        if self.route == "maker":
            total = maker_fee(self.contracts, float(self.price))
        else:
            total = taker_fee(self.contracts, float(self.price))
        return float(total / self.contracts)


class Executor:
    """Manages order lifecycle: place, monitor, cancel stale, log."""

    def __init__(
        self,
        client: KalshiClient,
        risk: RiskManager,
        *,
        dry_run: bool = False,
        db_path: str = DB_PATH,
        settings: Settings | None = None,
    ) -> None:
        self._client = client
        self._risk = risk
        self._dry_run = dry_run
        self._orders: dict[str, TrackedOrder] = {}
        self._db = _init_db(db_path)
        self._settings = settings
        self._get_orderbook: (
            Callable[[str], tuple[OrderBook, datetime] | None] | None
        ) = None
        self._reconcile_orphans()

    def attach_orderbook_source(
        self,
        fn: Callable[[str], tuple[OrderBook, datetime] | None],
    ) -> None:
        """Register a live orderbook lookup used for promote-time edge checks.

        The callable returns the latest cached (orderbook, received_at) pair
        for a ticker, or None if nothing has been received. `ws_feed.get_orderbook`
        on `KalshiOrderbookFeed` is the production wiring — keeping this as a
        plain callable avoids coupling the executor to the WS feed class.
        """
        self._get_orderbook = fn

    def _reconcile_orphans(self) -> None:
        """Mark old trades with pnl=NULL as orphans.

        Any trades in the DB with pnl IS NULL AND fees IS NULL older than
        30 minutes whose order_id is NOT in the live _orders dict are
        orphans whose markets have long since settled. Mark them pnl='0'
        so the dashboard no longer shows them as open positions.

        Safe to call both at startup (empty _orders) and mid-session
        (skips orders the executor is still actively tracking).
        """
        # Exclude order_ids that are still tracked in memory
        active_oids = set(self._orders.keys())
        if active_oids:
            placeholders = ",".join("?" for _ in active_oids)
            sql = f"""UPDATE trades
                      SET pnl = '0', exit_reason = 'orphan_reconciled'
                      WHERE pnl IS NULL AND fees IS NULL
                        AND timestamp < datetime('now', '-30 minutes')
                        AND order_id NOT IN ({placeholders})"""  # noqa: S608
            updated = self._db.execute(sql, tuple(active_oids)).rowcount
        else:
            updated = self._db.execute(
                """UPDATE trades
                   SET pnl = '0', exit_reason = 'orphan_reconciled'
                   WHERE pnl IS NULL AND fees IS NULL
                     AND timestamp < datetime('now', '-30 minutes')""",
            ).rowcount
        if updated:
            self._db.commit()
            logger.info("reconcile_orphans cleaned=%d stale trades", updated)

    async def submit(self, signal: Signal, bankroll: Decimal) -> TrackedOrder | None:
        """Size, place, and track an order for the given signal.

        Returns the TrackedOrder on success, or None if sizing returns 0
        or dry_run is active.
        """
        price = float(signal.kalshi_price)
        win_prob = (
            signal.real_prob if signal.side.value == "yes" else 1 - signal.real_prob
        )
        fraction = (
            self._settings.kelly_fraction
            if self._settings is not None
            else DEFAULT_KELLY_FRACTION
        )
        contracts = kelly_size(
            win_prob, price, bankroll, fraction=fraction,
            signal_strength=signal.signal_strength, symbol=signal.symbol,
        )
        if contracts == 0:
            logger.debug("Sizing returned 0 contracts for %s — skipping", signal.ticker)
            return None

        if self._dry_run:
            logger.info(
                "[PAPER] Would place %s %s x%d @ %s on %s (edge=%s)",
                signal.side.value,
                signal.ticker,
                contracts,
                signal.kalshi_price,
                signal.strategy.value,
                signal.net_edge,
            )
            order_id = f"PAPER-{int(time.time() * 1000)}"
            tracked = TrackedOrder(
                signal=signal,
                order_id=order_id,
                contracts=contracts,
                price=signal.kalshi_price,
            )
            tracked.state = OrderState.FILLED
            tracked.fill_time = time.monotonic()
            self._orders[order_id] = tracked
            self._risk.record_fill(signal.ticker, side=signal.side.value)
            self._log_trade(signal, order_id, contracts, signal.kalshi_price, None)
            return tracked

        # Reserve the ticker BEFORE awaiting place_order. If we wait until
        # the order_id comes back, a concurrent eval tick can slip past the
        # risk gate and submit a duplicate order while we're still awaiting
        # the HTTP response — the 2026-04-16 live incident placed ~25
        # duplicate orders against a single signal for exactly this reason.
        self._risk.record_fill(signal.ticker, side=signal.side.value)
        try:
            order_resp = await self._client.place_order(
                ticker=signal.ticker,
                action="buy",
                side=signal.side.value,
                price_dollars=signal.kalshi_price,
                count=contracts,
            )
        except Exception:
            self._risk.release_reservation(signal.ticker)
            raise
        order_id = str(order_resp["order_id"])
        tracked = TrackedOrder(
            signal=signal,
            order_id=order_id,
            contracts=contracts,
            price=signal.kalshi_price,
        )
        self._orders[order_id] = tracked
        self._log_trade(signal, order_id, contracts, signal.kalshi_price, None)
        logger.info(
            "Placed %s %s x%d @ %s order_id=%s",
            signal.side.value,
            signal.ticker,
            contracts,
            signal.kalshi_price,
            order_id,
        )
        return tracked

    async def check_pending_fills(self) -> None:
        """Poll Kalshi for pending orders and mark filled ones.

        Only calls record_fill on the risk manager when an order is
        confirmed filled by the API.
        """
        for oid, order in list(self._orders.items()):
            if order.state != OrderState.PENDING:
                continue
            if oid.startswith("PAPER-"):
                continue
            try:
                api_order = await self._client.get_order(oid)
            except Exception:
                continue
            status = api_order.get("status", "")
            if status in ("executed", "filled"):
                fill_price = api_order.get("price")
                if fill_price:
                    order.price = Decimal(str(fill_price))

                now_mono = time.monotonic()
                order.state = OrderState.FILLED
                order.fill_time = now_mono
                self._risk.record_fill(
                    order.signal.ticker, side=order.signal.side.value
                )
                slippage = float(order.price) - float(order.intended_price)
                logger.info(
                    "maker_filled",
                    extra={
                        "order_id": oid,
                        "ticker": order.signal.ticker,
                        "route": order.route,
                        "intended": float(order.intended_price),
                        "filled": float(order.price),
                        "slippage": slippage,
                        "latency_s": now_mono - order.placed_at,
                        "contracts": order.contracts,
                    },
                )
                logger.info(
                    "Confirmed fill: %s (%s) @ %s",
                    oid,
                    order.signal.ticker,
                    order.price,
                )

        # --- Poll EXITING orders (exit sells awaiting fill confirmation) ---
        for oid, order in list(self._orders.items()):
            if order.state != OrderState.EXITING:
                continue
            if order.exit_order_id is None:
                continue
            try:
                api_order = await self._client.get_order(order.exit_order_id)
            except Exception:
                continue
            status = api_order.get("status", "")
            if status in ("executed", "filled"):
                actual_price = api_order.get("price")
                sell_price = (
                    Decimal(str(actual_price))
                    if actual_price
                    else order.exit_price or Decimal("0")
                )
                pnl_per = sell_price - order.price
                raw_pnl = pnl_per * order.contracts
                if order.route == "maker":
                    entry_fee = maker_fee(order.contracts, float(order.price))
                else:
                    entry_fee = taker_fee(order.contracts, float(order.price))
                exit_fee = taker_fee(order.contracts, float(sell_price))
                total_fees = entry_fee + exit_fee
                exit_pnl = raw_pnl - total_fees

                order.pnl = exit_pnl
                order.state = OrderState.CANCELLED
                self._risk.record_settlement(
                    order.signal.ticker, exit_pnl, side=order.signal.side.value,
                )
                self._update_trade_pnl(
                    oid, exit_pnl, total_fees, order.exit_reason,
                )
                logger.info(
                    "exit_sell_confirmed sell_oid=%s ticker=%s pnl=%s",
                    order.exit_order_id,
                    order.signal.ticker,
                    exit_pnl,
                )
            elif status == "cancelled":
                # Market settled or exchange killed the sell order.
                # If record_settlement already handled this (SETTLED state),
                # skip. Otherwise mark as cancelled with pnl=0.
                if order.state == OrderState.EXITING:
                    order.state = OrderState.CANCELLED
                    order.pnl = Decimal("0")
                    self._risk.record_settlement(
                        order.signal.ticker, Decimal("0"),
                        side=order.signal.side.value,
                    )
                    self._update_trade_pnl(oid, Decimal("0"))
                    logger.info(
                        "exit_sell_cancelled sell_oid=%s ticker=%s",
                        order.exit_order_id,
                        order.signal.ticker,
                    )

    async def promote_to_taker(self) -> list[TrackedOrder]:
        """Cancel maker orders past their fill horizon and re-place as taker.

        New defensive logic:
        - Depth gate: skip if < MIN_PROMOTE_DEPTH contracts visible
        - Size reduction: downsize to min(contracts, depth//2) on thin books
        - Slippage buffer: pay +2c to cross spread aggressively
        - Retry: one immediate retry if first taker placement fails
        - Longer timeout: taker orders get TAKER_FILL_HORIZON_S (120s)
        """
        now = time.monotonic()
        to_promote: list[str] = []
        for oid, order in self._orders.items():
            if order.state != OrderState.PENDING:
                continue
            if order.route != "maker":
                continue
            if order.taker_price is None:
                continue
            if now - order.placed_at <= order.maker_timeout:
                continue
            to_promote.append(oid)

        failed: list[TrackedOrder] = []
        for oid in to_promote:
            order = self._orders[oid]
            if oid.startswith("PAPER-"):
                order.state = OrderState.CANCELLED
                self._risk.record_settlement(order.signal.ticker, Decimal("0"))
                self._update_trade_pnl(oid, Decimal("0"))
                logger.info(
                    "[PAPER] Maker timeout %s — cancelled (no taker re-entry in paper)",
                    oid,
                )
                continue

            try:
                await self._client.cancel_order(oid)
                order.state = OrderState.CANCELLED
                logger.info("Maker timeout — cancelled %s", oid)
            except Exception:
                order.state = OrderState.FILLED
                order.fill_time = now
                self._risk.record_fill(
                    order.signal.ticker, side=order.signal.side.value
                )
                logger.info("Maker order %s already filled on cancel attempt", oid)
                continue

            stale_taker_price = order.taker_price
            if stale_taker_price is None:
                self._risk.record_settlement(order.signal.ticker, Decimal("0"))
                self._update_trade_pnl(oid, Decimal("0"))
                continue

            fresh_taker_price = self._fresh_taker_price(order)
            if fresh_taker_price is None:
                logger.info(
                    "skip_taker_promote_no_orderbook",
                    extra={
                        "order_id": oid,
                        "ticker": order.signal.ticker,
                        "side": order.signal.side.value,
                        "stale_taker_price": float(stale_taker_price),
                    },
                )
                self._risk.record_settlement(order.signal.ticker, Decimal("0"))
                self._update_trade_pnl(oid, Decimal("0"))
                continue

            fresh_fee_per = float(
                taker_fee(order.contracts, float(fresh_taker_price))
            ) / order.contracts
            fresh_net_edge = (
                order.signal.real_prob
                - float(fresh_taker_price)
                - fresh_fee_per
            )

            price_move = abs(float(fresh_taker_price) - float(stale_taker_price))
            if price_move > PROMOTE_BIG_PRICE_MOVE:
                logger.info(
                    "taker_promote_big_price_move",
                    extra={
                        "order_id": oid,
                        "ticker": order.signal.ticker,
                        "stale": float(stale_taker_price),
                        "fresh": float(fresh_taker_price),
                        "move": price_move,
                    },
                )

            if fresh_net_edge < PROMOTE_MIN_NET_EDGE:
                logger.info(
                    "skip_taker_promote_edge_gone",
                    extra={
                        "order_id": oid,
                        "ticker": order.signal.ticker,
                        "side": order.signal.side.value,
                        "stale_taker_price": float(stale_taker_price),
                        "fresh_taker_price": float(fresh_taker_price),
                        "real_prob": order.signal.real_prob,
                        "fresh_net_edge": fresh_net_edge,
                        "min_net_edge": PROMOTE_MIN_NET_EDGE,
                    },
                )
                self._risk.record_settlement(order.signal.ticker, Decimal("0"))
                self._update_trade_pnl(oid, Decimal("0"))
                continue

            # --- NEW: depth gate + size reduction ---
            available_depth = self._get_available_depth(order)
            if available_depth is not None and available_depth < MIN_PROMOTE_DEPTH:
                logger.info(
                    "skip_taker_promote_thin_book",
                    extra={
                        "order_id": oid,
                        "ticker": order.signal.ticker,
                        "side": order.signal.side.value,
                        "wanted": order.contracts,
                        "available": available_depth,
                        "min_required": MIN_PROMOTE_DEPTH,
                    },
                )
                self._risk.record_settlement(order.signal.ticker, Decimal("0"))
                self._update_trade_pnl(oid, Decimal("0"))
                continue

            # Reduce size if book is thinner than desired
            target_contracts = order.contracts
            if available_depth is not None:
                target_contracts = min(order.contracts, max(1, available_depth // 2))
                if target_contracts < order.contracts:
                    logger.info(
                        "taker_promote_size_reduction",
                        extra={
                            "order_id": oid,
                            "ticker": order.signal.ticker,
                            "original": order.contracts,
                            "reduced_to": target_contracts,
                            "available_depth": available_depth,
                        },
                    )

            # --- Place taker order (with retry) ---
            for attempt in range(MAX_PROMOTE_RETRIES + 1):
                try:
                    taker_resp = await self._client.place_order(
                        ticker=order.signal.ticker,
                        action="buy",
                        side=order.signal.side.value,
                        price_dollars=fresh_taker_price,
                        count=target_contracts,
                    )
                    new_oid = str(taker_resp["order_id"])
                    new_order = TrackedOrder(
                        signal=order.signal,
                        order_id=new_oid,
                        contracts=target_contracts,
                        price=fresh_taker_price,
                    )
                    new_order.route = "taker_promoted"
                    # Give taker orders more time to fill (configurable)
                    new_order.timeout = (
                        self._settings.taker_fill_horizon_s
                        if self._settings is not None
                        else TAKER_FILL_HORIZON_S
                    )
                    self._orders[new_oid] = new_order
                    self._log_trade(
                        order.signal,
                        new_oid,
                        target_contracts,
                        fresh_taker_price,
                        None,
                    )
                    logger.info(
                        "Promoted to taker: %s -> %s @ %s x%d (fresh, stale was %s, edge=%.4f)",
                        oid,
                        new_oid,
                        fresh_taker_price,
                        target_contracts,
                        stale_taker_price,
                        fresh_net_edge,
                    )
                    break  # success
                except Exception:
                    if attempt < MAX_PROMOTE_RETRIES:
                        logger.warning(
                            "Taker promotion attempt %d failed for %s, retrying...",
                            attempt + 1,
                            oid,
                        )
                        await asyncio.sleep(0.5)
                        continue
                    logger.exception("Taker promotion failed for %s", oid)
                    self._risk.record_settlement(order.signal.ticker, Decimal("0"))
                    self._update_trade_pnl(oid, Decimal("0"))
                    failed.append(order)
        return failed

    def _get_available_depth(self, order: TrackedOrder) -> int | None:
        """Return available contracts on the ask side for the desired direction.

        YES taker buys lift YES asks, which are synthetic: best_yes_ask = 1 - max(no_bid).
        So YES ask liquidity lives in no_levels, and vice versa.
        """
        if self._get_orderbook is None:
            return None
        snapshot = self._get_orderbook(order.signal.ticker)
        if snapshot is None:
            return None
        fresh_book, _ = snapshot
        if order.signal.side.value == "yes":
            return sum(lv.quantity for lv in fresh_book.no_levels)
        else:
            return sum(lv.quantity for lv in fresh_book.yes_levels)

    def _fresh_taker_price(self, order: TrackedOrder) -> Decimal | None:
        """Compute the current taker price from the cached orderbook.

        Returns None when no orderbook source is wired, no snapshot is
        cached, the snapshot is older than PROMOTE_FRESH_ORDERBOOK_MAX_AGE_S,
        or the relevant side lacks liquidity.

        Applies TAKER_SLIPPAGE_BUFFER (2c) to aggressively cross the spread
        and guarantee a fill on thin Kalshi books.
        """
        if self._get_orderbook is None:
            return None
        snapshot = self._get_orderbook(order.signal.ticker)
        if snapshot is None:
            return None
        fresh_book, received_at = snapshot
        age_s = (datetime.now(timezone.utc) - received_at).total_seconds()
        if age_s > PROMOTE_FRESH_ORDERBOOK_MAX_AGE_S:
            return None
        if order.signal.side.value == "yes":
            ask = fresh_book.best_yes_ask
        else:
            ask = fresh_book.best_no_ask
        if ask is None:
            return None
        # Pay up to 2c worse than best ask to guarantee fill
        return min(Decimal("0.99"), ask + TAKER_SLIPPAGE_BUFFER)

    async def cancel_stale(self) -> list[TrackedOrder]:
        """Cancel orders that have been pending longer than ORDER_TIMEOUT_SECONDS.

        If the cancel returns 404 (order already filled), mark as filled
        instead of cancelled — do NOT remove from risk tracker.

        Returns the list of orders that were cancelled because they never
        filled, so callers can send user-facing "trade didn't go through"
        alerts.
        """
        now = time.monotonic()
        to_cancel: list[str] = []
        for oid, order in self._orders.items():
            if oid.startswith("PAPER-"):
                continue
            if (
                order.state == OrderState.PENDING
                and now - order.placed_at > order.timeout
            ):
                to_cancel.append(oid)

        cancelled: list[TrackedOrder] = []
        for oid in to_cancel:
            order = self._orders[oid]
            try:
                await self._client.cancel_order(oid)
                order.state = OrderState.CANCELLED
                self._risk.record_settlement(order.signal.ticker, Decimal("0"))
                self._update_trade_pnl(oid, Decimal("0"))
                logger.info("Cancelled stale order %s (%s)", oid, order.signal.ticker)
                cancelled.append(order)
            except Exception:
                order.state = OrderState.FILLED
                order.fill_time = order.placed_at
                self._risk.record_fill(
                    order.signal.ticker, side=order.signal.side.value
                )
                logger.info(
                    "Order %s (%s) already filled or settled — registered fill",
                    oid,
                    order.signal.ticker,
                )
        return cancelled

    def record_settlement(self, ticker: str, result: str) -> list[dict[str, Any]]:
        """Record that a market settled. Update P&L for matching orders.

        Returns any risk-manager side events (per-side pause, WR degradation)
        that fired during settlement so the caller can dispatch alerts.
        """
        events: list[dict[str, Any]] = []
        for oid, order in self._orders.items():
            if order.signal.ticker != ticker:
                continue
            if order.state not in (OrderState.FILLED, OrderState.PENDING, OrderState.EXITING):
                continue
            won = (
                result == "yes" if order.signal.side.value == "yes" else result == "no"
            )
            payout = Decimal("1") - order.price if won else -order.price
            if order.route == "maker":
                entry_fee = maker_fee(order.contracts, float(order.price))
            else:
                entry_fee = taker_fee(order.contracts, float(order.price))
            pnl = payout * order.contracts - entry_fee
            order.pnl = pnl
            order.state = OrderState.SETTLED
            evs = self._risk.record_settlement(ticker, pnl, side=order.signal.side.value)
            if evs:
                events.append(evs)
            self._update_trade_pnl(oid, pnl, entry_fee)
            logger.info(
                "Settled %s side=%s result=%s won=%s pnl=%s fees=%s",
                ticker,
                order.signal.side.value,
                result,
                won,
                pnl,
                entry_fee,
            )
        return events

    async def exit_position(
        self,
        order: TrackedOrder,
        current_market_price: Decimal = Decimal("0"),
        exit_reason: str | None = None,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Sell to exit a filled position.

        Returns (exited, risk_events) so callers can forward side_paused /
        side_wr_alert events to the alerter — mirroring record_settlement.
        """
        if order.state != OrderState.FILLED:
            return False, []

        sell_price = current_market_price if current_market_price > 0 else Decimal("0")
        pnl_per_contract = sell_price - order.price
        raw_pnl = pnl_per_contract * order.contracts
        if order.route == "maker":
            entry_fee = maker_fee(order.contracts, float(order.price))
        else:
            entry_fee = taker_fee(order.contracts, float(order.price))
        exit_fee = taker_fee(order.contracts, float(sell_price))
        total_fees = entry_fee + exit_fee
        exit_pnl = raw_pnl - total_fees

        # Live exit: sell 1c below bid to guarantee fill near settlement.
        # Limit sells fill at best available, so this only costs a penny if
        # the top bid level gets pulled between our read and order placement.
        sell_order_price = max(Decimal("0.01"), sell_price - EXIT_SELL_BUFFER)
        events: list[dict[str, Any]] = []

        if self._dry_run:
            logger.info(
                "[PAPER] Would sell %s %s x%d @ ~%s to exit (est pnl=%s fees=%s)",
                order.signal.side.value,
                order.signal.ticker,
                order.contracts,
                sell_price,
                exit_pnl,
                total_fees,
            )
            order.pnl = exit_pnl
            order.state = OrderState.CANCELLED
            evs = self._risk.record_settlement(order.signal.ticker, exit_pnl, side=order.signal.side.value)
            if evs:
                events.append(evs)
            self._update_trade_pnl(order.order_id, exit_pnl, total_fees, exit_reason)
            return True, events

        try:
            sell_resp = await self._client.place_order(
                ticker=order.signal.ticker,
                action="sell",
                side=order.signal.side.value,
                price_dollars=sell_order_price,
                count=order.contracts,
            )
            sell_oid = str(sell_resp["order_id"])
            order.exit_order_id = sell_oid
            order.exit_price = sell_order_price
            order.exit_reason = exit_reason
            order.state = OrderState.EXITING
            logger.info(
                "Exit sell placed: %s %s x%d @ %s sell_oid=%s",
                order.signal.side.value,
                order.signal.ticker,
                order.contracts,
                sell_order_price,
                sell_oid,
            )
            return True, []
        except Exception:
            logger.exception("exit_sell_failed", extra={"ticker": order.signal.ticker})
            return False, []

    def mark_filled(self, order_id: str) -> None:
        """Mark an order as filled (called when polling confirms fill)."""
        order = self._orders.get(order_id)
        if order is not None and order.state == OrderState.PENDING:
            order.state = OrderState.FILLED
            order.fill_time = time.monotonic()

    @property
    def pending_orders(self) -> list[TrackedOrder]:
        """All currently pending orders."""
        return [o for o in self._orders.values() if o.state == OrderState.PENDING]

    @property
    def filled_orders(self) -> list[TrackedOrder]:
        """All currently filled (unsettled) orders."""
        return [o for o in self._orders.values() if o.state == OrderState.FILLED]

    @property
    def settled_orders(self) -> list[TrackedOrder]:
        """All settled orders."""
        return [o for o in self._orders.values() if o.state == OrderState.SETTLED]

    @property
    def active_tickers(self) -> set[str]:
        """Tickers with pending, filled, or exiting (unsettled) orders."""
        return {
            o.signal.ticker
            for o in self._orders.values()
            if o.state in (OrderState.PENDING, OrderState.FILLED, OrderState.EXITING)
        }

    @property
    def cancel_rate(self) -> float:
        """Cancel rate over the last hour (cancels / total orders placed)."""
        total = self._total_orders_last_hour
        if total == 0:
            return 0.0
        return self._cancels_last_hour / total

    @property
    def _total_orders_last_hour(self) -> int:
        """Count orders placed in the last hour."""
        cutoff = time.monotonic() - 3600
        return sum(
            1 for o in self._orders.values()
            if o.placed_at > cutoff
        )

    @property
    def _cancels_last_hour(self) -> int:
        """Count cancelled orders in the last hour."""
        cutoff = time.monotonic() - 3600
        return sum(
            1 for o in self._orders.values()
            if o.state == OrderState.CANCELLED and o.placed_at > cutoff
        )

    def log_signal(self, signal: Signal, action: str, reason: str = "") -> None:
        """Log a signal evaluation to the signals table."""
        self._db.execute(
            """INSERT INTO signals
               (timestamp, ticker, symbol, strategy, side, edge, net_edge,
                kalshi_price, real_prob, seconds_remaining, action, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                signal.ticker,
                signal.symbol,
                signal.strategy.value,
                signal.side.value,
                str(signal.edge),
                str(signal.net_edge),
                str(signal.kalshi_price),
                signal.real_prob,
                signal.seconds_remaining,
                action,
                reason,
            ),
        )
        self._db.commit()

    def _log_trade(
        self,
        signal: Signal,
        order_id: str,
        contracts: int,
        price: Decimal,
        pnl: Decimal | None,
    ) -> None:
        self._db.execute(
            """INSERT INTO trades
               (timestamp, order_id, ticker, symbol, strategy, side,
                contracts, price, edge, net_edge, pnl, route)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                order_id,
                signal.ticker,
                signal.symbol,
                signal.strategy.value,
                signal.side.value,
                contracts,
                str(price),
                str(signal.edge),
                str(signal.net_edge),
                str(pnl) if pnl is not None else None,
                getattr(signal, "route", "taker"),
            ),
        )
        self._db.commit()

    def _update_trade_pnl(
        self,
        order_id: str,
        pnl: Decimal,
        fees: Decimal | None = None,
        exit_reason: str | None = None,
    ) -> None:
        sets = ["pnl = ?"]
        params: list[object] = [str(pnl)]
        if fees is not None:
            sets.append("fees = ?")
            params.append(str(fees))
        if exit_reason is not None:
            sets.append("exit_reason = ?")
            params.append(exit_reason)
        params.append(order_id)
        self._db.execute(
            f"UPDATE trades SET {', '.join(sets)} WHERE order_id = ?",
            params,
        )
        self._db.commit()

    def log_window_analysis(
        self,
        symbol: str,
        window_open: str,
        window_close: str,
        open_price: float,
        close_price: float,
        price_change_pct: float,
        result: str,
        signals_count: int,
        trades_count: int,
        paper_pnl: float,
        ai_commentary: str,
        ai_model: str,
    ) -> None:
        """Log a window analysis result to the database."""
        with contextlib.suppress(Exception):
            self._db.execute(
                """INSERT INTO window_analyses (
                    timestamp, symbol, window_open, window_close,
                    open_price, close_price, price_change_pct, result,
                    signals_count, trades_count, paper_pnl, ai_commentary, ai_model
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    symbol,
                    window_open,
                    window_close,
                    open_price,
                    close_price,
                    price_change_pct,
                    result,
                    signals_count,
                    trades_count,
                    paper_pnl,
                    ai_commentary,
                    ai_model,
                ),
            )
            self._db.commit()

    def start_new_session(self, label: str = "") -> int:
        """Create a new session marker. Returns the session ID."""
        cur = self._db.execute(
            "INSERT INTO sessions (started_at, label) VALUES (?, ?)",
            (datetime.now(timezone.utc).isoformat(), label or None),
        )
        self._db.commit()
        return cur.lastrowid or 0

    def current_session_start(self) -> str | None:
        """Return the started_at timestamp of the most recent session, or None."""
        row = self._db.execute(
            "SELECT started_at FROM sessions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return str(row[0]) if row else None

    async def close(self) -> None:
        """Close the database connection."""
        self._db.close()


def _init_db(path: str) -> sqlite3.Connection:
    """Create trades database and table if needed."""
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            order_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            side TEXT NOT NULL,
            contracts INTEGER NOT NULL,
            price TEXT NOT NULL,
            edge TEXT NOT NULL,
            net_edge TEXT NOT NULL,
            pnl TEXT,
            fees TEXT,
            route TEXT DEFAULT 'taker'
        )"""
    )
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE trades ADD COLUMN fees TEXT")
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE trades ADD COLUMN route TEXT DEFAULT 'taker'")
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE trades ADD COLUMN exit_reason TEXT")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            side TEXT NOT NULL,
            edge TEXT NOT NULL,
            net_edge TEXT NOT NULL,
            kalshi_price TEXT NOT NULL,
            real_prob REAL NOT NULL,
            seconds_remaining INTEGER NOT NULL,
            action TEXT NOT NULL,
            reason TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS window_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            window_open TEXT NOT NULL,
            window_close TEXT NOT NULL,
            open_price REAL NOT NULL,
            close_price REAL NOT NULL,
            price_change_pct REAL NOT NULL,
            result TEXT NOT NULL,
            signals_count INTEGER NOT NULL,
            trades_count INTEGER NOT NULL,
            paper_pnl REAL,
            ai_commentary TEXT,
            ai_model TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            label TEXT
        )"""
    )
    conn.commit()
    return conn
