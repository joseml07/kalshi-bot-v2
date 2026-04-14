"""Temporary executor stub for phase 1 imports."""

from __future__ import annotations

DB_PATH = "trades.db"


class Executor:
    """Minimal executor interface used by phase 1 modules."""

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
        """Stub no-op for type checking during phase 1."""
        _ = (
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
        )
