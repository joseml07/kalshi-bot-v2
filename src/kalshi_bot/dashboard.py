"""FastAPI dashboard — P&L, positions, signals, trades."""

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse

from kalshi_bot.execution.executor import DB_PATH

app = FastAPI(title="Kalshi V2 — Momentum Bot Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    db = _get_db()
    try:
        rows = db.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def _session_start() -> str | None:
    rows = _query("SELECT started_at FROM sessions ORDER BY id DESC LIMIT 1")
    return str(rows[0]["started_at"]) if rows else None


@app.get("/api/trades")
def api_trades(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    return _query("SELECT * FROM trades ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))


@app.get("/api/summary")
def api_summary(all: bool = False) -> dict[str, Any]:  # noqa: A002
    ss = None if all else _session_start()
    where = "WHERE 1=1"
    params: tuple[Any, ...] = ()
    if ss:
        where = "WHERE timestamp >= ?"
        params = (ss,)
    rows = _query(
        f"""SELECT
             COUNT(*) as total_trades,
             SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins,
             SUM(CASE WHEN CAST(pnl AS REAL) < 0 THEN 1 ELSE 0 END) as losses,
             SUM(CASE WHEN pnl IS NULL THEN 1 ELSE 0 END) as pending,
             COALESCE(SUM(CAST(pnl AS REAL)), 0) as total_pnl
           FROM trades {where}""",  # noqa: S608
        params,
    )
    summary = rows[0] if rows else {}

    open_rows = _query(
        """SELECT ticker, side, SUM(contracts) as qty,
                  SUM(CAST(price AS REAL) * contracts) as cost
           FROM trades
           WHERE pnl IS NULL OR pnl = '0'
           GROUP BY ticker, side"""
    )
    summary["open_positions"] = open_rows
    summary["total_in_positions"] = sum(r["cost"] for r in open_rows)
    summary["session_active"] = ss is not None
    return summary


@app.get("/api/pnl_history")
def api_pnl_history(all: bool = False) -> list[dict[str, Any]]:  # noqa: A002
    ss = None if all else _session_start()
    where = "WHERE pnl IS NOT NULL"
    params: tuple[Any, ...] = ()
    if ss:
        where += " AND timestamp >= ?"
        params = (ss,)
    return _query(
        f"""SELECT date(timestamp) as day,
                  COALESCE(SUM(CAST(pnl AS REAL)), 0) as pnl,
                  COUNT(*) as trades
           FROM trades
           {where}
           GROUP BY day
           ORDER BY day""",  # noqa: S608
        params,
    )


@app.get("/api/signals")
def api_signals(limit: int = 50) -> list[dict[str, Any]]:
    try:
        return _query("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,))
    except Exception:
        return []


@app.get("/api/stats")
def api_stats(all: bool = False) -> dict[str, Any]:  # noqa: A002
    ss = None if all else _session_start()
    where = "WHERE pnl IS NOT NULL"
    params: tuple[Any, ...] = ()
    if ss:
        where += " AND timestamp >= ?"
        params = (ss,)
    rows = _query(
        f"""SELECT
             COUNT(*) as total_trades,
             SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins,
             SUM(CASE WHEN CAST(pnl AS REAL) < 0 THEN 1 ELSE 0 END) as losses,
             COALESCE(AVG(CAST(pnl AS REAL)), 0) as avg_pnl,
             COALESCE(SUM(CAST(pnl AS REAL)), 0) as total_pnl,
             COALESCE(SUM(CAST(fees AS REAL)), 0) as total_fees
           FROM trades {where}""",  # noqa: S608
        params,
    )
    stats: dict[str, Any] = rows[0] if rows else {}

    total = stats.get("total_trades") or 0
    wins = stats.get("wins") or 0
    stats["win_rate"] = (wins / total) if total > 0 else 0.0

    best = _query(
        f"SELECT ticker, pnl FROM trades {where} "  # noqa: S608
        "ORDER BY CAST(pnl AS REAL) DESC LIMIT 1",
        params,
    )
    worst = _query(
        f"SELECT ticker, pnl FROM trades {where} "  # noqa: S608
        "ORDER BY CAST(pnl AS REAL) ASC LIMIT 1",
        params,
    )
    stats["best_trade"] = best[0] if best else None
    stats["worst_trade"] = worst[0] if worst else None

    stats["by_strategy"] = _query(
        f"""SELECT strategy,
             COUNT(*) as trades,
             SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins,
             COALESCE(SUM(CAST(pnl AS REAL)), 0) as total_pnl
           FROM trades {where}
           GROUP BY strategy""",  # noqa: S608
        params,
    )
    stats["session_active"] = ss is not None
    return stats


@app.get("/api/pnl_rolling")
def api_pnl_rolling(all: bool = False) -> list[dict[str, Any]]:  # noqa: A002
    ss = None if all else _session_start()
    where = "WHERE pnl IS NOT NULL"
    params: tuple[Any, ...] = ()
    if ss:
        where += " AND timestamp >= ?"
        params = (ss,)
    return _query(
        f"""SELECT timestamp, ticker, strategy,
                  CAST(pnl AS REAL) as pnl
           FROM trades {where}
           ORDER BY timestamp""",  # noqa: S608
        params,
    )


@app.get("/api/routes")
def api_routes(all: bool = False) -> list[dict[str, Any]]:  # noqa: A002
    """Performance grouped by execution route (maker/taker/promoted)."""
    ss = None if all else _session_start()
    where = "WHERE pnl IS NOT NULL"
    params: tuple[Any, ...] = ()
    if ss:
        where += " AND timestamp >= ?"
        params = (ss,)
    return _query(
        f"""SELECT route,
                  COUNT(*) as trades,
                  SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins,
                  SUM(CASE WHEN CAST(pnl AS REAL) < 0 THEN 1 ELSE 0 END) as losses,
                  COALESCE(SUM(CAST(pnl AS REAL)), 0) as total_pnl
           FROM trades {where}
           GROUP BY route
           ORDER BY trades DESC""",  # noqa: S608
        params,
    )


@app.get("/api/analyses")
def api_analyses(limit: int = 20) -> list[dict[str, Any]]:
    try:
        return _query("SELECT * FROM window_analyses ORDER BY id DESC LIMIT ?", (limit,))
    except Exception:
        return []


@app.get("/api/windows")
def api_windows(limit: int = 10) -> list[dict[str, Any]]:
    try:
        return _query(
            """SELECT symbol, window_open, window_close,
                      open_price, close_price, price_change_pct,
                      result, signals_count, trades_count, paper_pnl,
                      ai_commentary
               FROM window_analyses ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
    except Exception:
        return []


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    tables = [
        "price_ticks",
        "orderbook_snapshots",
        "window_snapshots",
        "market_events",
        "strategy_evals",
        "trades",
        "signals",
        "window_analyses",
    ]
    result: dict[str, Any] = {}
    for table in tables:
        try:
            rows = _query(f"SELECT timestamp FROM {table} ORDER BY id DESC LIMIT 1")  # noqa: S608
            result[table] = {"last_timestamp": rows[0]["timestamp"] if rows else None}
        except Exception:
            result[table] = {"last_timestamp": None}
    for table in tables:
        try:
            rows = _query(f"SELECT COUNT(*) as cnt FROM {table}")  # noqa: S608
            result[table]["count"] = rows[0]["cnt"] if rows else 0
        except Exception:
            result.setdefault(table, {})["count"] = 0

    # Added for phase-14 observability: pull runtime telemetry from live_state.json
    live_path = Path("live_state.json")
    runtime: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        if live_path.exists():
            runtime = json.loads(live_path.read_text(encoding="utf-8"))

    result["runtime"] = {
        "coinbase_last_tick_age_s": runtime.get("health", {}).get("coinbase_last_tick_age_s"),
        "kalshi_ws_last_update_age_s": runtime.get("health", {}).get("kalshi_ws_last_update_age_s"),
        "db_last_write_age_s": runtime.get("health", {}).get("db_last_write_age_s"),
        "db_last_write_latency_ms": runtime.get("health", {}).get("db_last_write_latency_ms"),
        "api_read_utilization": runtime.get("health", {}).get("api_read_utilization"),
        "api_write_utilization": runtime.get("health", {}).get("api_write_utilization"),
    }
    return result


@app.get("/api/stats_by_symbol")
def api_stats_by_symbol(all: bool = False) -> list[dict[str, Any]]:  # noqa: A002
    ss = None if all else _session_start()
    where = "WHERE pnl IS NOT NULL"
    params: tuple[Any, ...] = ()
    if ss:
        where += " AND timestamp >= ?"
        params = (ss,)
    try:
        return _query(
            f"""SELECT symbol,
                 COUNT(*) as trades,
                 SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins,
                 SUM(CASE WHEN CAST(pnl AS REAL) < 0 THEN 1 ELSE 0 END) as losses,
                 COALESCE(SUM(CAST(pnl AS REAL)), 0) as total_pnl,
                 COALESCE(AVG(CAST(pnl AS REAL)), 0) as avg_pnl,
                 COALESCE(SUM(CAST(fees AS REAL)), 0) as total_fees
               FROM trades {where}
               GROUP BY symbol""",  # noqa: S608
            params,
        )
    except Exception:
        return []


@app.get("/api/price_ticks")
def api_price_ticks(symbol: str = "BTC", limit: int = 100) -> list[dict[str, Any]]:
    try:
        return _query(
            "SELECT * FROM price_ticks WHERE symbol = ? ORDER BY id DESC LIMIT ?",
            (symbol, limit),
        )
    except Exception:
        return []


@app.get("/api/strategy_evals")
def api_strategy_evals(limit: int = 100) -> list[dict[str, Any]]:
    try:
        return _query("SELECT * FROM strategy_evals ORDER BY id DESC LIMIT ?", (limit,))
    except Exception:
        return []


@app.get("/api/session")
def api_session() -> dict[str, Any]:
    rows = _query("SELECT * FROM sessions ORDER BY id DESC LIMIT 1")
    if rows:
        return {
            "active": True,
            "id": rows[0]["id"],
            "started_at": rows[0]["started_at"],
            "label": rows[0]["label"],
        }
    return {"active": False}


def _rows_to_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _csv_response(rows: list[dict[str, Any]], filename: str) -> StreamingResponse:
    content = _rows_to_csv(rows)
    return StreamingResponse(
        io.StringIO(content),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/download/trades.csv")
def download_trades_csv() -> StreamingResponse:
    rows = _query("SELECT * FROM trades ORDER BY id ASC")
    return _csv_response(rows, "trades.csv")


@app.get("/download/pnl.csv")
def download_pnl_csv() -> StreamingResponse:
    rows = _query(
        """SELECT date(timestamp) as day,
                  COUNT(*) as trades,
                  SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins,
                  SUM(CASE WHEN CAST(pnl AS REAL) < 0 THEN 1 ELSE 0 END) as losses,
                  COALESCE(SUM(CAST(pnl AS REAL)), 0) as total_pnl,
                  COALESCE(SUM(CAST(fees AS REAL)), 0) as total_fees
           FROM trades WHERE pnl IS NOT NULL
           GROUP BY day ORDER BY day"""
    )
    return _csv_response(rows, "pnl_history.csv")


@app.get("/download/signals.csv")
def download_signals_csv() -> StreamingResponse:
    rows = _query("SELECT * FROM signals ORDER BY id ASC")
    return _csv_response(rows, "signals.csv")


@app.get("/download/windows.csv")
def download_windows_csv() -> StreamingResponse:
    try:
        rows = _query("SELECT * FROM window_analyses ORDER BY id ASC")
    except Exception:
        rows = []
    return _csv_response(rows, "window_analyses.csv")


@app.get("/download/strategy_evals.csv")
def download_strategy_evals_csv() -> StreamingResponse:
    try:
        rows = _query("SELECT * FROM strategy_evals ORDER BY id ASC")
    except Exception:
        rows = []
    return _csv_response(rows, "strategy_evals.csv")


@app.get("/download/full.json")
def download_full_json() -> StreamingResponse:
    data: dict[str, Any] = {
        "trades": _query("SELECT * FROM trades ORDER BY id ASC"),
        "signals": _query("SELECT * FROM signals ORDER BY id ASC"),
        "pnl_by_day": _query(
            """SELECT date(timestamp) as day,
                      COUNT(*) as trades,
                      COALESCE(SUM(CAST(pnl AS REAL)), 0) as total_pnl,
                      COALESCE(SUM(CAST(fees AS REAL)), 0) as total_fees
               FROM trades WHERE pnl IS NOT NULL GROUP BY day ORDER BY day"""
        ),
    }
    with contextlib.suppress(Exception):
        data["window_analyses"] = _query("SELECT * FROM window_analyses ORDER BY id ASC")
    content = json.dumps(data, default=str)
    return StreamingResponse(
        io.StringIO(content),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=kalshi_bot_export.json"},
    )


@app.get("/api/live")
async def api_live() -> StreamingResponse:
    live_path = Path("live_state.json")

    async def event_stream() -> AsyncIterator[str]:
        while True:
            try:
                payload = live_path.read_text(encoding="utf-8") if live_path.exists() else "{}"
            except Exception:
                payload = "{}"
            yield f"data: {payload}\n\n"
            await asyncio.sleep(2.0)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    """WebSocket live-state stream for low-latency dashboard updates."""
    await websocket.accept()
    live_path = Path("live_state.json")
    try:
        while True:
            try:
                payload = live_path.read_text(encoding="utf-8") if live_path.exists() else "{}"
            except Exception:
                payload = "{}"
            await websocket.send_text(payload)
            await asyncio.sleep(2.0)
    except WebSocketDisconnect:
        return


@app.get("/api/trade/{trade_id}")
def api_trade_detail(trade_id: int) -> dict[str, Any]:
    rows = _query("SELECT * FROM trades WHERE id = ? LIMIT 1", (trade_id,))
    if not rows:
        return {"found": False}
    trade = rows[0]

    sig_rows = _query(
        """SELECT * FROM signals
           WHERE ticker = ? AND side = ?
           ORDER BY ABS(strftime('%s', timestamp) - strftime('%s', ?)) ASC
           LIMIT 1""",
        (str(trade["ticker"]), str(trade["side"]), str(trade["timestamp"])),
    )
    signal = sig_rows[0] if sig_rows else None

    lifecycle = {
        "placed_at": trade.get("timestamp"),
        "route": trade.get("route"),
        "settled": trade.get("pnl") is not None,
    }

    return {
        "found": True,
        "trade": trade,
        "signal": signal,
        "lifecycle": lifecycle,
    }


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    html_path = Path(__file__).parent / "dashboard.html"
    return html_path.read_text(encoding="utf-8")


if __name__ == "__main__":
    import uvicorn

    from kalshi_bot.config import Settings

    settings = Settings()
    uvicorn.run(app, host="0.0.0.0", port=settings.dashboard_port)
