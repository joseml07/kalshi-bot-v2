"""Historical data recorder for backtesting.

Collects price ticks, orderbook snapshots, market events, and strategy
evaluations into SQLite tables alongside the existing trades/signals data.
All writes are fire-and-forget — errors are logged but never propagate to
the trading loop.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

# Minimum seconds between sampled price ticks per symbol
PRICE_SAMPLE_INTERVAL = 5.0

# Minimum seconds between orderbook snapshots per ticker
OB_SAMPLE_INTERVAL = 5.0

# Minimum seconds between window state snapshots per ticker
SNAPSHOT_SAMPLE_INTERVAL = 5.0


class DataRecorder:
    """Append-only recorder for historical market data."""

    def __init__(self, db_path: str = "trades.db") -> None:
        self._db = sqlite3.connect(db_path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._init_tables()
        # Monotonic timestamps for sampling control
        self._last_price: dict[str, float] = {}  # symbol -> mono time
        self._last_ob: dict[str, float] = {}  # ticker -> mono time
        self._last_snapshot: dict[str, float] = {}  # ticker -> mono time
        self._last_write_mono: float | None = None
        self._last_write_latency_ms: float | None = None

    # ------------------------------------------------------------------
    # Public API — all methods catch exceptions internally
    # ------------------------------------------------------------------

    def record_price_tick(
        self,
        symbol: str,
        price: float,
        ts: datetime | None = None,
    ) -> None:
        """Record a sampled Coinbase price tick."""
        now_mono = time.monotonic()
        last = self._last_price.get(symbol, 0.0)
        if now_mono - last < PRICE_SAMPLE_INTERVAL:
            return
        self._last_price[symbol] = now_mono
        timestamp = (ts or datetime.now(timezone.utc)).isoformat()
        self._safe_execute(
            "INSERT INTO price_ticks (timestamp, symbol, price) VALUES (?, ?, ?)",
            (timestamp, symbol, price),
        )

    def record_orderbook_snapshot(
        self,
        ticker: str,
        symbol: str,
        best_yes_ask: Decimal | None,
        best_yes_bid: Decimal | None,
        best_no_ask: Decimal | None,
        best_no_bid: Decimal | None,
        yes_depth: int,
        no_depth: int,
        spread: Decimal | None,
    ) -> None:
        """Record a periodic orderbook snapshot."""
        now_mono = time.monotonic()
        last = self._last_ob.get(ticker, 0.0)
        if now_mono - last < OB_SAMPLE_INTERVAL:
            return
        self._last_ob[ticker] = now_mono
        timestamp = datetime.now(timezone.utc).isoformat()
        self._safe_execute(
            """INSERT INTO orderbook_snapshots
               (timestamp, ticker, symbol, best_yes_ask, best_yes_bid,
                best_no_ask, best_no_bid, yes_depth, no_depth, spread)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                timestamp,
                ticker,
                symbol,
                str(best_yes_ask) if best_yes_ask is not None else None,
                str(best_yes_bid) if best_yes_bid is not None else None,
                str(best_no_ask) if best_no_ask is not None else None,
                str(best_no_bid) if best_no_bid is not None else None,
                yes_depth,
                no_depth,
                str(spread) if spread is not None else None,
            ),
        )

    def record_market_event(
        self,
        ticker: str,
        symbol: str,
        event_type: str,
        open_time: str,
        close_time: str,
        open_price: float = 0.0,
        close_price: float = 0.0,
        result: str = "",
    ) -> None:
        """Record a market lifecycle event (open, close, settle)."""
        timestamp = datetime.now(timezone.utc).isoformat()
        self._safe_execute(
            """INSERT INTO market_events
               (timestamp, ticker, symbol, event_type, open_time, close_time,
                open_price, close_price, result)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                timestamp,
                ticker,
                symbol,
                event_type,
                open_time,
                close_time,
                open_price,
                close_price,
                result,
            ),
        )

    def record_window_snapshot(
        self,
        ticker: str,
        symbol: str,
        seconds_remaining: int,
        open_price: float,
        current_price: float,
        price_change_pct: float,
        kalshi_yes_ask: float,
        kalshi_yes_bid: float | None,
        kalshi_no_bid: float | None,
        real_prob: float,
        dynamic_k: float,
        yes_depth: int,
        no_depth: int,
        momentum_60s: float | None = None,
    ) -> None:
        """Record a per-poll window state snapshot for backtesting.

        This is the single most important table for replay: it captures
        everything the strategy saw at each decision point.
        """
        now_mono = time.monotonic()
        last = self._last_snapshot.get(ticker, 0.0)
        if now_mono - last < SNAPSHOT_SAMPLE_INTERVAL:
            return
        self._last_snapshot[ticker] = now_mono
        timestamp = datetime.now(timezone.utc).isoformat()
        self._safe_execute(
            """INSERT INTO window_snapshots
               (timestamp, ticker, symbol, seconds_remaining,
                open_price, current_price, price_change_pct,
                kalshi_yes_ask, kalshi_yes_bid, kalshi_no_bid,
                real_prob, dynamic_k, yes_depth, no_depth, momentum_60s)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                timestamp,
                ticker,
                symbol,
                seconds_remaining,
                open_price,
                current_price,
                price_change_pct,
                kalshi_yes_ask,
                kalshi_yes_bid,
                kalshi_no_bid,
                real_prob,
                dynamic_k,
                yes_depth,
                no_depth,
                momentum_60s,
            ),
        )

    def record_strategy_eval(
        self,
        ticker: str,
        symbol: str,
        strategy: str,
        seconds_remaining: int,
        price_change_pct: float,
        kalshi_yes_price: float,
        real_prob: float,
        edge: float,
        net_edge: float,
        signal_side: str,
        action: str,
        reason: str = "",
    ) -> None:
        """Record a full strategy evaluation (signal or no-signal)."""
        timestamp = datetime.now(timezone.utc).isoformat()
        self._safe_execute(
            """INSERT INTO strategy_evals
               (timestamp, ticker, symbol, strategy, seconds_remaining,
                price_change_pct, kalshi_yes_price, real_prob, edge,
                net_edge, signal_side, action, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                timestamp,
                ticker,
                symbol,
                strategy,
                seconds_remaining,
                price_change_pct,
                kalshi_yes_price,
                real_prob,
                edge,
                net_edge,
                signal_side,
                action,
                reason,
            ),
        )

    def close(self) -> None:
        """Close the database connection."""
        with contextlib.suppress(Exception):
            self._db.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _safe_execute(self, sql: str, params: tuple[Any, ...]) -> None:
        """Execute SQL, swallowing errors so the trading loop is never affected."""
        start = time.perf_counter()
        try:
            self._db.execute(sql, params)
            self._db.commit()
            self._last_write_mono = time.monotonic()
            self._last_write_latency_ms = (time.perf_counter() - start) * 1000.0
        except Exception:
            logger.debug("data_recorder_write_failed", exc_info=True)

    @property
    def last_write_age_s(self) -> float | None:
        """Seconds since last successful DB write, or None if no writes."""
        if self._last_write_mono is None:
            return None
        return max(0.0, time.monotonic() - self._last_write_mono)

    @property
    def last_write_latency_ms(self) -> float | None:
        """Latency of the most recent successful DB write (milliseconds)."""
        return self._last_write_latency_ms

    def _init_tables(self) -> None:
        """Create historical data tables if they don't exist."""
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS price_ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                price REAL NOT NULL
            )"""
        )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS orderbook_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                symbol TEXT NOT NULL,
                best_yes_ask TEXT,
                best_yes_bid TEXT,
                best_no_ask TEXT,
                best_no_bid TEXT,
                yes_depth INTEGER NOT NULL,
                no_depth INTEGER NOT NULL,
                spread TEXT
            )"""
        )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS market_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                symbol TEXT NOT NULL,
                event_type TEXT NOT NULL,
                open_time TEXT NOT NULL,
                close_time TEXT NOT NULL,
                open_price REAL NOT NULL DEFAULT 0.0,
                close_price REAL NOT NULL DEFAULT 0.0,
                result TEXT NOT NULL DEFAULT ''
            )"""
        )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS strategy_evals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                seconds_remaining INTEGER NOT NULL,
                price_change_pct REAL NOT NULL,
                kalshi_yes_price REAL NOT NULL,
                real_prob REAL NOT NULL,
                edge REAL NOT NULL,
                net_edge REAL NOT NULL,
                signal_side TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT ''
            )"""
        )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS window_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                symbol TEXT NOT NULL,
                seconds_remaining INTEGER NOT NULL,
                open_price REAL NOT NULL,
                current_price REAL NOT NULL,
                price_change_pct REAL NOT NULL,
                kalshi_yes_ask REAL NOT NULL,
                kalshi_yes_bid REAL,
                kalshi_no_bid REAL,
                real_prob REAL NOT NULL,
                dynamic_k REAL NOT NULL,
                yes_depth INTEGER NOT NULL,
                no_depth INTEGER NOT NULL,
                momentum_60s REAL
            )"""
        )
        # Indexes for backtesting queries
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_price_ticks_symbol_ts "
            "ON price_ticks (symbol, timestamp)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ob_snapshots_ticker_ts "
            "ON orderbook_snapshots (ticker, timestamp)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_market_events_ticker "
            "ON market_events (ticker, event_type)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_strategy_evals_ticker_ts "
            "ON strategy_evals (ticker, timestamp)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_window_snapshots_ticker_ts "
            "ON window_snapshots (ticker, timestamp)"
        )
        self._db.commit()
