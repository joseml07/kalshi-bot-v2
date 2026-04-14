"""Pre-trade risk manager."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from kalshi_bot.config import Settings
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

    # --- Public API ---

    def check(self, signal: Signal) -> None:
        """Run all pre-trade checks. Raises RiskVetoError if blocked."""
        self._rotate_day()
        self._check_locked_side(signal)
        self._check_kill_switch()
        self._check_daily_loss()
        self._check_concurrent_positions(signal.ticker)

    def record_fill(
        self,
        ticker: str,
        side: str | None = None,
    ) -> None:
        """Record that a position was opened."""
        self._open_position_tickers.add(ticker)
        if side:
            self._locked_sides[ticker] = side.lower()

    def record_settlement(self, ticker: str, pnl: Decimal) -> None:
        """Record a settled position and its P&L."""
        self._open_position_tickers.discard(ticker)
        # Keep locked side for window lifetime to prevent re-entry
        self._cooldowns[ticker] = time.monotonic() + self._cooldown_seconds
        self._rotate_day()
        self._daily_pnl += pnl
        logger.info("Settlement %s pnl=%s daily_pnl=%s", ticker, pnl, self._daily_pnl)

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

    def _check_kill_switch(self) -> None:
        if KILL_SWITCH_FILE.exists():
            raise RiskVetoError("Kill switch file exists")

    def _check_daily_loss(self) -> None:
        if self._daily_pnl <= -self._daily_loss_limit:
            raise RiskVetoError(
                f"Daily loss limit hit: {self._daily_pnl} <= -{self._daily_loss_limit}"
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


def _today() -> date:
    """Current UTC date."""
    return datetime.now(timezone.utc).date()
