"""Pre-trade risk manager."""

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from kalshi_bot.config import Settings
from kalshi_bot.models.market import OrderBook
from kalshi_bot.strategy.signals import Signal

logger = logging.getLogger(__name__)

KILL_SWITCH_FILE = Path("KILL_SWITCH")


class RiskVetoError(Exception):
    """Raised when a trade is blocked by risk checks."""


class RiskManager:
    """Pre-trade risk gate."""

    def __init__(self, settings: Settings) -> None:
        self._daily_loss_limit = Decimal(str(settings.daily_loss_limit))
        self._max_concurrent = settings.max_concurrent_positions
        self._daily_pnl: Decimal = Decimal("0")
        self._pnl_date: date = _today()
        self._open_position_tickers: set[str] = set()
        # Cooldown: ticker -> monotonic time when cooldown expires (60s after exit)
        self._cooldowns: dict[str, float] = {}
        self._cooldown_seconds = 60.0
        # Lock side per ticker/window to prevent any re-entry in that window
        self._locked_sides: dict[str, str] = {}

        # Per-side daily loss limit. 0 = disabled (matches the pre-feature default).
        ps_limit = float(getattr(settings, "per_side_daily_loss_limit", 0.0) or 0.0)
        self._per_side_daily_loss_limit: Decimal | None = (
            Decimal(str(ps_limit)) if ps_limit > 0 else None
        )
        self._per_side_daily_pnl: dict[str, Decimal] = {"yes": Decimal("0"), "no": Decimal("0")}
        self._per_side_paused_today: set[str] = set()

        # Side degradation monitor (rolling WR alert).
        self._wr_window = int(getattr(settings, "side_wr_alert_window", 30) or 30)
        self._wr_threshold = float(getattr(settings, "side_wr_alert_threshold", 0.30) or 0.30)
        self._wr_alerts_enabled = bool(getattr(settings, "side_wr_alerts_enabled", False))
        self._recent_wins_yes: deque[bool] = deque(maxlen=self._wr_window)
        self._recent_wins_no: deque[bool] = deque(maxlen=self._wr_window)
        # Suppress alert repeats — only re-fire when WR has recovered then dipped again.
        self._wr_alert_state: dict[str, bool] = {"yes": False, "no": False}

    # --- Public API ---

    def check(self, signal: Signal, orderbook: OrderBook | None = None) -> None:
        """Run all pre-trade checks. Raises RiskVetoError if blocked."""
        self._rotate_day()
        self._check_locked_side(signal)
        self._check_kill_switch()
        self._check_daily_loss()
        self._check_per_side_daily_loss(signal)
        self._check_concurrent_positions(signal.ticker)
        if orderbook is not None:
            self._check_crossed_book(orderbook)

    def record_fill(
        self,
        ticker: str,
        side: str | None = None,
    ) -> None:
        """Record that a position was opened."""
        self._open_position_tickers.add(ticker)
        if side:
            self._locked_sides[ticker] = side.lower()

    def release_reservation(self, ticker: str) -> None:
        """Undo a record_fill reservation when the order never reached Kalshi.

        Called by the executor if place_order raised before returning an
        order_id — the ticker must not stay locked or the window is burned.
        """
        self._open_position_tickers.discard(ticker)
        self._locked_sides.pop(ticker, None)

    def record_settlement(
        self, ticker: str, pnl: Decimal, side: str | None = None
    ) -> dict[str, Any]:
        """Record a settled position and its P&L.

        If `side` is provided, also updates per-side daily PnL and rolling WR.
        Returns a dict describing any side-level events that just fired so the
        caller can emit alerts/Telegram notifications without coupling the
        manager to the alerter.
        """
        self._open_position_tickers.discard(ticker)
        # Keep locked side for window lifetime to prevent re-entry
        self._cooldowns[ticker] = time.monotonic() + self._cooldown_seconds
        self._rotate_day()
        self._daily_pnl += pnl

        events: dict[str, Any] = {}
        side_key = side.lower() if side else self._locked_sides.get(ticker)
        if side_key in ("yes", "no"):
            self._per_side_daily_pnl[side_key] = (
                self._per_side_daily_pnl.get(side_key, Decimal("0")) + pnl
            )
            # Per-side loss-limit gate.
            if (
                self._per_side_daily_loss_limit is not None
                and side_key not in self._per_side_paused_today
                and -self._per_side_daily_pnl[side_key] >= self._per_side_daily_loss_limit
            ):
                self._per_side_paused_today.add(side_key)
                events["side_paused"] = {
                    "side": side_key,
                    "daily_pnl": str(self._per_side_daily_pnl[side_key]),
                    "limit": str(self._per_side_daily_loss_limit),
                }
                logger.warning(
                    "per_side_pause side=%s daily_pnl=%s limit=%s",
                    side_key, self._per_side_daily_pnl[side_key],
                    self._per_side_daily_loss_limit,
                )

            # Rolling-WR degradation alert.
            buf = self._recent_wins_yes if side_key == "yes" else self._recent_wins_no
            buf.append(pnl > 0)
            if (
                self._wr_alerts_enabled
                and len(buf) >= self._wr_window
            ):
                wr = sum(1 for w in buf if w) / len(buf)
                if wr < self._wr_threshold and not self._wr_alert_state[side_key]:
                    self._wr_alert_state[side_key] = True
                    events["side_wr_alert"] = {
                        "side": side_key,
                        "win_rate": wr,
                        "window": self._wr_window,
                        "threshold": self._wr_threshold,
                    }
                    logger.warning(
                        "side_wr_degraded side=%s wr=%.2f window=%d threshold=%.2f",
                        side_key, wr, self._wr_window, self._wr_threshold,
                    )
                elif wr >= self._wr_threshold:
                    self._wr_alert_state[side_key] = False

        logger.info("Settlement %s pnl=%s daily_pnl=%s", ticker, pnl, self._daily_pnl)
        return events

    def reset_session(self, clear_pnl: bool = False) -> dict[str, int]:
        """Clear in-memory risk state so the bot can re-enter tickers.

        Clears `_locked_sides` and `_cooldowns` so tickers traded earlier in
        the session can be re-entered. Optionally also resets `_daily_pnl`.
        Does NOT clear `_open_position_tickers` — actual open positions must
        be settled, not forgotten.

        Returns a dict summarising what was cleared.
        """
        cleared_sides = len(self._locked_sides)
        cleared_cooldowns = len(self._cooldowns)
        self._locked_sides.clear()
        self._cooldowns.clear()
        cleared_pnl = 0
        if clear_pnl:
            cleared_pnl = 1 if self._daily_pnl != Decimal("0") else 0
            self._daily_pnl = Decimal("0")
            self._pnl_date = _today()
        logger.info(
            "reset_session cleared_sides=%d cleared_cooldowns=%d clear_pnl=%s",
            cleared_sides,
            cleared_cooldowns,
            clear_pnl,
        )
        return {
            "cleared_locked_sides": cleared_sides,
            "cleared_cooldowns": cleared_cooldowns,
            "cleared_pnl": cleared_pnl,
            "open_positions": len(self._open_position_tickers),
        }

    def sync_positions(self, positions: list[dict[str, Any]]) -> None:
        """Sync open positions from Kalshi API response."""
        self._open_position_tickers = {
            p["ticker"]
            for p in positions
            if p.get("market_position", 0) != 0
            or p.get("yes_position", 0) != 0
            or p.get("no_position", 0) != 0
        }

    @property
    def daily_pnl(self) -> Decimal:
        """Current daily P&L."""
        self._rotate_day()
        return self._daily_pnl

    @property
    def open_position_count(self) -> int:
        """Number of open positions."""
        return len(self._open_position_tickers)

    # --- Internal checks ---

    def _rotate_day(self) -> None:
        today = _today()
        if self._pnl_date != today:
            logger.info("Day rolled: resetting daily P&L (was %s)", self._daily_pnl)
            self._daily_pnl = Decimal("0")
            self._pnl_date = today
            # Reset per-side daily state on rollover.
            self._per_side_daily_pnl = {"yes": Decimal("0"), "no": Decimal("0")}
            self._per_side_paused_today.clear()

    def _check_kill_switch(self) -> None:
        if KILL_SWITCH_FILE.exists():
            raise RiskVetoError("Kill switch file exists")

    def _check_daily_loss(self) -> None:
        if self._daily_pnl <= -self._daily_loss_limit:
            raise RiskVetoError(
                f"Daily loss limit hit: {self._daily_pnl} <= -{self._daily_loss_limit}"
            )

    def _check_per_side_daily_loss(self, signal: Signal) -> None:
        """Per-side daily loss circuit-breaker. Off when limit is unset."""
        if self._per_side_daily_loss_limit is None:
            return
        side = signal.side.value.lower()
        if side in self._per_side_paused_today:
            raise RiskVetoError(
                f"{side} side paused for the day "
                f"(daily_pnl={self._per_side_daily_pnl.get(side, Decimal('0'))}, "
                f"limit={self._per_side_daily_loss_limit})"
            )

    def _check_concurrent_positions(self, ticker: str) -> None:
        if ticker in self._open_position_tickers:
            raise RiskVetoError(f"Already have position in {ticker}")
        cooldown_until = self._cooldowns.get(ticker)
        if cooldown_until is not None and time.monotonic() < cooldown_until:
            raise RiskVetoError(f"Cooldown active for {ticker}")
        if len(self._open_position_tickers) >= self._max_concurrent:
            raise RiskVetoError(f"Max concurrent positions ({self._max_concurrent}) reached")

    def _check_locked_side(self, signal: Signal) -> None:
        """Block any re-entry on a ticker already traded in this window."""
        locked_side = self._locked_sides.get(signal.ticker)
        if locked_side is not None:
            raise RiskVetoError(f"Already traded {signal.ticker} ({locked_side} side)")

    def _check_crossed_book(self, orderbook: OrderBook) -> None:
        """Observe (don't veto) implied-crossed books.

        `best_yes_ask` is synthetic (1 - best_no_bid). On Kalshi, YES bids
        and NO bids are independent orderbooks, so `yes_bid + no_bid > 1`
        is a real market state, not corruption. Real WS corruption is
        caught by the feed-level health counters (delta_before_snapshot,
        negative_qty, sequence_gap) plus the staleness gate.
        """
        best_yes_bid = orderbook.best_yes_bid
        best_no_bid = orderbook.best_no_bid
        if best_yes_bid is None or best_no_bid is None:
            return
        book_sum = float(best_yes_bid + best_no_bid)
        if book_sum > 1.05:
            logger.info(
                "crossed_book_observed yes_bid=%s no_bid=%s sum=%.4f",
                best_yes_bid,
                best_no_bid,
                book_sum,
            )


def _today() -> date:
    """Current UTC date."""
    return datetime.now(timezone.utc).date()
