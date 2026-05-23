"""Track 15-minute trading windows for crypto contracts."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from kalshi_bot.models.price import PriceTick

logger = logging.getLogger(__name__)


@dataclass
class WindowState:
    """State of a single 15-minute window."""

    symbol: str
    ticker: str
    open_time: datetime
    close_time: datetime
    open_price: float
    current_price: float
    prices_60s: deque[tuple[float, float]] = field(
        default_factory=lambda: deque(maxlen=600)
    )
    # Kalshi settlement strike (and comparison type). When non-None, used as
    # the reference for direction labeling instead of open_price. See Q4 of
    # explanation.md for why this matters.
    strike: float | None = None
    strike_type: str | None = None

    @property
    def seconds_remaining(self) -> int:
        """Seconds until this window closes."""
        now = datetime.now(timezone.utc)
        remaining = (self.close_time - now).total_seconds()
        return max(0, int(remaining))

    @property
    def price_change_pct(self) -> float:
        """Current price change vs open, as a fraction."""
        if self.open_price == 0:
            return 0.0
        return (self.current_price - self.open_price) / self.open_price

    @property
    def momentum_60s(self) -> float | None:
        """Price change over the last 60 seconds, as a fraction.

        Returns None if insufficient data.
        """
        if len(self.prices_60s) < 2:
            return None
        now_ts = self.prices_60s[-1][0]
        cutoff = now_ts - 60.0
        # Find oldest price within 60s window
        for ts, price in self.prices_60s:
            if ts >= cutoff:
                if price == 0:
                    return None
                return (self.current_price - price) / price
        return None


@dataclass
class PreviousResult:
    """Outcome of a previous 15-minute window."""

    symbol: str
    went_up: bool
    close_time: datetime
    open_price: float = 0.0
    close_price: float = 0.0
    strike: float | None = None


class WindowTracker:
    """Tracks active 15-minute windows and previous results per symbol.

    Windows are set exclusively from Kalshi market data (open_time and
    close_time), not guessed from UTC boundaries.
    """

    def __init__(self) -> None:
        self._windows: dict[str, WindowState] = {}
        self._previous: dict[str, PreviousResult] = {}
        self._latest_prices: dict[str, float] = {}
        self._recent_changes: dict[str, deque[float]] = {}
        # Buffer of closed windows for reliable transition detection
        self._closed_queue: list[tuple[str, WindowState]] = []

    def get_window(self, symbol: str) -> WindowState | None:
        """Get the current window state for a symbol, or None."""
        win = self._windows.get(symbol)
        if win is None:
            return None
        if win.seconds_remaining <= 0:
            self._close_window(symbol)
            return None
        return win

    def get_previous_result(self, symbol: str) -> PreviousResult | None:
        """Get the previous window result for a symbol."""
        return self._previous.get(symbol)

    def update_price(self, tick: PriceTick) -> None:
        """Process an incoming price tick.

        Only updates an existing window -- does NOT create a new window.
        Windows must be created via ``set_window`` using Kalshi market data.
        """
        symbol = tick.symbol
        self._latest_prices[symbol] = tick.price

        win = self._windows.get(symbol)

        if win is not None and win.seconds_remaining <= 0:
            self._close_window(symbol)
            win = None

        if win is None:
            return

        win.current_price = tick.price
        win.prices_60s.append((tick.timestamp.timestamp(), tick.price))

    def set_window(
        self,
        symbol: str,
        ticker: str,
        open_time: datetime,
        close_time: datetime,
        open_price: float = 0.0,
        strike: float | None = None,
        strike_type: str | None = None,
    ) -> None:
        """Set a window from Kalshi market data.

        If a window already exists for this symbol with the same close_time,
        the strike/strike_type are refreshed in place (Kalshi can update
        these between snapshots) and existing accumulated price data is
        preserved.  Otherwise the old window is closed and a new one is
        created.

        When *open_price* is 0.0, the most recent Coinbase price for this
        symbol is used instead (if available).

        *strike* and *strike_type* come from the Kalshi market metadata
        (``floor_strike`` / ``strike_type``) and are used by
        ``_close_window`` to determine direction. When *strike* is None
        the legacy ``current_price >= open_price`` fallback applies.
        """
        existing = self._windows.get(symbol)
        if existing is not None and existing.close_time == close_time:
            # Refresh strike metadata in case it was unknown at first set.
            if strike is not None:
                existing.strike = strike
            if strike_type is not None:
                existing.strike_type = strike_type
            return  # same window, keep accumulated state

        if existing is not None:
            self._close_window(symbol)

        if open_price == 0.0:
            open_price = self._latest_prices.get(symbol, 0.0)

        if open_price == 0.0:
            logger.debug("Skipping window creation for %s — no price data yet", symbol)
            return

        self._windows[symbol] = WindowState(
            symbol=symbol,
            ticker=ticker,
            open_time=open_time,
            close_time=close_time,
            open_price=open_price,
            current_price=open_price,
            strike=strike,
            strike_type=strike_type,
        )
        logger.info(
            "Window from market: %s open=%.2f strike=%s open_time=%s close=%s",
            symbol,
            open_price,
            f"{strike:.2f}" if strike is not None else "n/a",
            open_time.isoformat(),
            close_time.isoformat(),
        )

    def pop_closed_windows(self) -> list[tuple[str, WindowState]]:
        """Drain and return windows closed since the last call.

        Each entry is ``(symbol, closed_window_state)``.  The caller is
        responsible for running analysis on the returned windows.
        """
        result = list(self._closed_queue)
        self._closed_queue.clear()
        return result

    def record_previous_result(
        self, symbol: str, went_up: bool, close_time: datetime
    ) -> None:
        """Manually record a previous window result."""
        self._previous[symbol] = PreviousResult(symbol=symbol, went_up=went_up, close_time=close_time)

    def get_recent_changes(self, symbol: str) -> list[float]:
        """Return recent completed window price changes for a symbol."""
        q = self._recent_changes.get(symbol)
        if q is None:
            return []
        return list(q)

    def _close_window(self, symbol: str) -> None:
        """Close a window and record the result.

        Direction (``went_up``) is determined by the Kalshi settlement
        strike when available, since Kalshi settles
        ``close >= floor_strike`` (for ``strike_type='greater_or_equal'``),
        not ``close >= open``. Falls back to the legacy open-comparison
        when no strike was captured (older replay data, paper sims that
        haven't been re-fed market metadata).
        """
        win = self._windows.pop(symbol, None)
        if win is None:
            return
        if win.strike is not None:
            # Kalshi's KX{BTC,ETH}15M markets are 'greater_or_equal':
            # YES wins iff close >= floor_strike. We support
            # 'less_or_equal' too in case Kalshi adds it.
            if win.strike_type == "less_or_equal":
                went_up = win.current_price <= win.strike
            else:
                went_up = win.current_price >= win.strike
            ref_label, ref_val = "strike", win.strike
        else:
            went_up = win.current_price >= win.open_price
            ref_label, ref_val = "open", win.open_price
        self._previous[symbol] = PreviousResult(
            symbol=symbol,
            went_up=went_up,
            close_time=win.close_time,
            open_price=win.open_price,
            close_price=win.current_price,
            strike=win.strike,
        )
        # Record price change for realized volatility estimation
        if win.open_price > 0:
            change = (win.current_price - win.open_price) / win.open_price
            if symbol not in self._recent_changes:
                self._recent_changes[symbol] = deque(maxlen=20)
            self._recent_changes[symbol].append(change)
        # Buffer for analysis — skip phantom windows (open=close or open=0)
        if win.open_price > 0 and win.current_price != win.open_price:
            self._closed_queue.append((symbol, win))
        logger.info(
            "Window closed: %s went_up=%s (close=%.2f vs %s=%.2f)",
            symbol,
            went_up,
            win.current_price,
            ref_label,
            ref_val,
        )
