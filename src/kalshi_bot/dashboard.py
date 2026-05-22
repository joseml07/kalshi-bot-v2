"""FastAPI dashboard — P&L, positions, signals, trades."""

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import json
import sqlite3
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import (
    Body,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse

from kalshi_bot.alerts.control import (
    SettingError,
    current_settings,
    mutate_setting,
    settable_keys,
)
from kalshi_bot.config import Settings
from kalshi_bot.control_channel import (
    activate_kill_switch,
    deactivate_kill_switch,
    enqueue,
    kill_switch_active,
)
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


def _latest_id(table: str) -> int:
    rows = _query(f"SELECT COALESCE(MAX(id), 0) as max_id FROM {table}")  # noqa: S608
    if not rows:
        return 0
    return int(rows[0].get("max_id") or 0)


def _session_start() -> str | None:
    rows = _query("SELECT started_at FROM sessions ORDER BY id DESC LIMIT 1")
    return str(rows[0]["started_at"]) if rows else None


def _read_live_state() -> dict[str, Any]:
    live_path = Path("live_state.json")
    if not live_path.exists():
        return {}
    with contextlib.suppress(Exception):
        data = json.loads(live_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    return {}


@app.get("/api/trades")
def api_trades(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    return _query(
        "SELECT * FROM trades ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
    )


@app.get("/api/summary")
def api_summary(all: bool = False) -> dict[str, Any]:  # noqa: A002
    ss = None if all else _session_start()
    # Exclude cancel_stale rows (pnl='0' AND fees IS NULL) — those orders
    # never filled, so they shouldn't count toward trade stats.
    where = "WHERE NOT (pnl = '0' AND fees IS NULL)"
    params: tuple[Any, ...] = ()
    if ss:
        where = "WHERE timestamp >= ? AND NOT (pnl = '0' AND fees IS NULL)"
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

    # Open position = placed and not yet finalized. cancel_stale writes
    # pnl='0' with fees=NULL for orders that timed out without filling;
    # those are NOT positions, so we exclude them by requiring BOTH pnl
    # and fees to be NULL (the state between _log_trade and settle/exit).
    # No session timestamp filter here: open-ness is a lifecycle property,
    # not a time property. A trade placed before a restart is still held.
    open_rows = _query(
        """SELECT ticker, side, SUM(contracts) as qty,
                  SUM(CAST(price AS REAL) * contracts) as cost
           FROM trades
           WHERE pnl IS NULL AND fees IS NULL
           GROUP BY ticker, side""",
    )
    summary["open_positions"] = open_rows
    summary["total_in_positions"] = sum(r["cost"] for r in open_rows)
    summary["session_active"] = ss is not None
    runtime = _read_live_state()
    summary["trading_mode"] = runtime.get("trading_mode", "unknown")
    summary["balance"] = runtime.get("balance")

    # Split PnL by paper vs live (paper order_ids start with "PAPER-")
    split_rows = _query(
        f"""SELECT
             CASE WHEN order_id LIKE 'PAPER-%%' THEN 'paper' ELSE 'live' END as mode,
             COUNT(*) as trades,
             SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins,
             SUM(CASE WHEN CAST(pnl AS REAL) < 0 THEN 1 ELSE 0 END) as losses,
             COALESCE(SUM(CAST(pnl AS REAL)), 0) as pnl
           FROM trades {where}
           GROUP BY mode""",  # noqa: S608
        params,
    )
    for row in split_rows:
        mode = row["mode"]
        summary[f"{mode}_trades"] = row["trades"]
        summary[f"{mode}_wins"] = row["wins"]
        summary[f"{mode}_losses"] = row["losses"]
        summary[f"{mode}_pnl"] = row["pnl"]

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
                  COUNT(*) as trades,
                  SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins,
                  SUM(CASE WHEN CAST(pnl AS REAL) <= 0 THEN 1 ELSE 0 END) as losses
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
        return _query(
            "SELECT * FROM window_analyses ORDER BY id DESC LIMIT ?", (limit,)
        )
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
    runtime = _read_live_state()

    result["runtime"] = {
        "coinbase_last_tick_age_s": runtime.get("health", {}).get(
            "coinbase_last_tick_age_s"
        ),
        "kalshi_ws_last_update_age_s": runtime.get("health", {}).get(
            "kalshi_ws_last_update_age_s"
        ),
        "coinbase_stale": runtime.get("health", {}).get("coinbase_stale"),
        "kalshi_ws_stale": runtime.get("health", {}).get("kalshi_ws_stale"),
        "db_last_write_age_s": runtime.get("health", {}).get("db_last_write_age_s"),
        "db_last_write_latency_ms": runtime.get("health", {}).get(
            "db_last_write_latency_ms"
        ),
        "api_read_per_sec": runtime.get("health", {}).get("api_read_per_sec"),
        "api_write_per_sec": runtime.get("health", {}).get("api_write_per_sec"),
        "api_read_utilization": runtime.get("health", {}).get("api_read_utilization"),
        "api_write_utilization": runtime.get("health", {}).get("api_write_utilization"),
        "signal_counters_window_start": runtime.get("health", {}).get(
            "signal_counters_window_start"
        ),
        "signal_counters_hour": runtime.get("health", {}).get(
            "signal_counters_hour", {}
        ),
        "kalshi_ws": runtime.get("health", {}).get("kalshi_ws", {}),
    }
    return result


@app.get("/api/diagnostics")
def api_diagnostics(limit: int = 30) -> dict[str, Any]:
    """Condensed incident snapshot for fast debugging.

    Aggregates runtime health + recent DB activity + risk block reasons.
    """
    max_limit = max(10, min(limit, 200))

    runtime = _read_live_state()

    recent_trades = _query(
        """SELECT id, timestamp, ticker, symbol, side, contracts, price, route, pnl, fees
           FROM trades ORDER BY id DESC LIMIT ?""",
        (max_limit,),
    )
    recent_signals = _query(
        """SELECT id, timestamp, ticker, symbol, side, action, reason,
                  edge, net_edge, seconds_remaining
           FROM signals ORDER BY id DESC LIMIT ?""",
        (max_limit,),
    )
    risk_blocks = _query(
        """SELECT reason,
                  COUNT(*) as count,
                  MAX(timestamp) as last_seen
           FROM signals
           WHERE action = 'skip_risk'
           GROUP BY reason
           ORDER BY count DESC
           LIMIT 12"""
    )
    action_counts = _query(
        """SELECT action, COUNT(*) as count
           FROM (
             SELECT action FROM signals ORDER BY id DESC LIMIT ?
           )
           GROUP BY action
           ORDER BY count DESC""",
        (max_limit,),
    )

    last_trade = recent_trades[0] if recent_trades else None
    last_signal = recent_signals[0] if recent_signals else None

    incident_flags: list[str] = []
    health = runtime.get("health", {}) if isinstance(runtime, dict) else {}
    ws_diag = health.get("kalshi_ws", {}) if isinstance(health, dict) else {}
    if health.get("coinbase_stale"):
        incident_flags.append("coinbase_stale")
    if health.get("kalshi_ws_stale"):
        incident_flags.append("kalshi_ws_stale")
    if float(ws_diag.get("negative_qty", 0) or 0) > 100:
        incident_flags.append("ws_negative_qty_high")
    if float(ws_diag.get("delta_missing_fields", 0) or 0) > 20:
        incident_flags.append("ws_missing_fields_high")
    if float(ws_diag.get("resync_full", 0) or 0) > 0:
        incident_flags.append("ws_resync_full_seen")
    if float(ws_diag.get("resync_ticker", 0) or 0) > 0:
        incident_flags.append("ws_resync_ticker_seen")
    if float(health.get("eval_stale_symbols", 0) or 0) > 0:
        incident_flags.append("eval_loop_stalled")

    endpoint_catalog = {
        "health": ["/api/health", "/api/diagnostics", "/api/live", "/ws/live"],
        "trades": ["/api/trades", "/api/summary", "/api/stats", "/api/routes"],
        "signals": [
            "/api/signals",
            "/api/strategy_evals",
            "/api/windows",
            "/api/analyses",
            "/api/why_not_trading",
        ],
        "control": [
            "/api/settings",
            "/api/reset",
            "/api/kill",
            "/api/resume",
            "/api/kill_switch",
        ],
        "export": ["/api/export/state", "/api/export/changes", "/download/full.json"],
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session_start": _session_start(),
        "kill_switch_active": kill_switch_active(),
        "summary": api_summary(),
        "stats": api_stats(),
        "health": api_health(),
        "runtime_snapshot": runtime,
        "incident_flags": incident_flags,
        "latest": {
            "trade": last_trade,
            "signal": last_signal,
        },
        "signal_actions_recent": action_counts,
        "risk_block_reasons": risk_blocks,
        "recent_trades": recent_trades,
        "recent_signals": recent_signals,
        "endpoint_catalog": endpoint_catalog,
    }


@app.get("/api/export/state")
def api_export_state() -> dict[str, Any]:
    """Lightweight export cursor state for remote incremental sync."""
    tables = [
        "trades",
        "signals",
        "strategy_evals",
        "price_ticks",
        "orderbook_snapshots",
        "window_snapshots",
        "market_events",
        "window_analyses",
    ]
    state: dict[str, Any] = {"tables": {}, "session_start": _session_start()}
    for table in tables:
        with contextlib.suppress(Exception):
            state["tables"][table] = {
                "max_id": _latest_id(table),
                "count": _query(f"SELECT COUNT(*) as cnt FROM {table}")[0]["cnt"],  # noqa: S608
            }
    return state


@app.get("/api/export/changes")
def api_export_changes(
    since_trade_id: int = 0,
    since_signal_id: int = 0,
    since_eval_id: int = 0,
    since_tick_id: int = 0,
    since_ob_id: int = 0,
    since_window_id: int = 0,
    since_event_id: int = 0,
    since_analysis_id: int = 0,
    limit: int = 2000,
) -> dict[str, Any]:
    """Incremental export for syncing VPS data back to local dev."""
    max_limit = max(100, min(limit, 10000))

    def _rows(table: str, since_id: int) -> list[dict[str, Any]]:
        return _query(
            f"SELECT * FROM {table} WHERE id > ? ORDER BY id ASC LIMIT ?",  # noqa: S608
            (since_id, max_limit),
        )

    cursor: dict[str, int] = {
        "trades": since_trade_id,
        "signals": since_signal_id,
        "strategy_evals": since_eval_id,
        "price_ticks": since_tick_id,
        "orderbook_snapshots": since_ob_id,
        "window_snapshots": since_window_id,
        "market_events": since_event_id,
        "window_analyses": since_analysis_id,
    }

    data: dict[str, list[dict[str, Any]]] = {
        "trades": _rows("trades", since_trade_id),
        "signals": _rows("signals", since_signal_id),
        "strategy_evals": _rows("strategy_evals", since_eval_id),
        "price_ticks": _rows("price_ticks", since_tick_id),
        "orderbook_snapshots": _rows("orderbook_snapshots", since_ob_id),
        "window_snapshots": _rows("window_snapshots", since_window_id),
        "market_events": _rows("market_events", since_event_id),
        "window_analyses": _rows("window_analyses", since_analysis_id),
    }

    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cursor": cursor,
        "data": data,
    }

    next_cursor: dict[str, int] = {}
    for table, rows in data.items():
        if rows:
            next_cursor[table] = int(rows[-1]["id"])
        else:
            next_cursor[table] = int(cursor[table])
    payload["next_cursor"] = next_cursor
    return payload


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


@app.get("/api/breakdowns")
def api_breakdowns(all: bool = False) -> dict[str, Any]:  # noqa: A002
    """Aggregated analytics for the Insights tab.

    Returns six breakdowns over the settled-trades set in one payload so the
    frontend can render the whole Insights view from a single fetch:

      by_side          : yes vs no — count, wins, pnl, fees
      by_symbol        : BTC / ETH / SOL — same fields
      by_side_symbol   : 2D matrix (side × symbol) — surfaces YES-on-BTC type asymmetries
      by_exit_reason   : counts + pnl per exit_reason value (NULL = held to settlement)
      by_hour_et       : 24 buckets in America/New_York local time
      by_dow_et        : 7 buckets, Sun=0..Sat=6, ET local time

    The ?all=true query param toggles session vs. all-time aggregation, mirroring
    /api/summary and /api/stats. Cancelled rows (pnl='0' AND fees IS NULL) are
    excluded everywhere — they never filled and would skew the totals.

    All time-bucket aggregation happens client-side from the underlying SQL
    rows because SQLite's strftime cannot do tz conversion. We hand back raw
    timestamps + pnl per trade and let the JS bucket into ET locally.
    """
    ss = None if all else _session_start()
    where = "WHERE NOT (pnl = '0' AND fees IS NULL) AND pnl IS NOT NULL"
    params: tuple[Any, ...] = ()
    if ss:
        where = (
            "WHERE NOT (pnl = '0' AND fees IS NULL) AND pnl IS NOT NULL "
            "AND timestamp >= ?"
        )
        params = (ss,)

    def _agg(group_cols: str) -> list[dict[str, Any]]:
        """Run a GROUP BY against the settled-trade where-clause."""
        try:
            return _query(
                f"""SELECT {group_cols},
                     COUNT(*) as n,
                     SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins,
                     SUM(CASE WHEN CAST(pnl AS REAL) < 0 THEN 1 ELSE 0 END) as losses,
                     COALESCE(SUM(CAST(pnl AS REAL)), 0) as pnl,
                     COALESCE(SUM(CAST(fees AS REAL)), 0) as fees
                   FROM trades {where}
                   GROUP BY {group_cols.split(',')[0]}"""
                if "," not in group_cols
                else f"""SELECT {group_cols},
                     COUNT(*) as n,
                     SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins,
                     SUM(CASE WHEN CAST(pnl AS REAL) < 0 THEN 1 ELSE 0 END) as losses,
                     COALESCE(SUM(CAST(pnl AS REAL)), 0) as pnl,
                     COALESCE(SUM(CAST(fees AS REAL)), 0) as fees
                   FROM trades {where}
                   GROUP BY {group_cols}""",  # noqa: S608
                params,
            )
        except Exception:
            return []

    # exit_reason GROUP BY needs to coalesce NULL into a stable bucket.
    try:
        by_exit_reason = _query(
            f"""SELECT COALESCE(exit_reason, 'settlement') as exit_reason,
                 COUNT(*) as n,
                 SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins,
                 SUM(CASE WHEN CAST(pnl AS REAL) < 0 THEN 1 ELSE 0 END) as losses,
                 COALESCE(SUM(CAST(pnl AS REAL)), 0) as pnl
               FROM trades {where}
               GROUP BY COALESCE(exit_reason, 'settlement')""",  # noqa: S608
            params,
        )
    except Exception:
        by_exit_reason = []

    # Raw timestamp+pnl pull for client-side ET bucketing. Keep payload small
    # by selecting just the two columns we need.
    try:
        raw_time_rows = _query(
            f"SELECT timestamp, CAST(pnl AS REAL) as pnl FROM trades {where} "
            "ORDER BY timestamp",
            params,
        )
    except Exception:
        raw_time_rows = []

    return {
        "view": "all-time" if all else "session",
        "session_start": ss,
        "by_side": _agg("side"),
        "by_symbol": _agg("symbol"),
        "by_side_symbol": _agg("side, symbol"),
        "by_exit_reason": by_exit_reason,
        "time_series": raw_time_rows,  # for hour/day-of-week + drawdown + rolling WR
    }


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
        data["window_analyses"] = _query(
            "SELECT * FROM window_analyses ORDER BY id ASC"
        )
    content = json.dumps(data, default=str)
    return StreamingResponse(
        io.StringIO(content),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=kalshi_bot_export.json"},
    )


@app.get("/download/explanation.md")
def download_explanation() -> StreamingResponse:
    """Download the edge analysis report as Markdown."""
    md_path = Path(__file__).parent / "explanation.md"
    if not md_path.exists():
        raise HTTPException(status_code=404, detail="explanation.md not found")
    content = md_path.read_text(encoding="utf-8")
    return StreamingResponse(
        io.StringIO(content),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=explanation.md"},
    )


@app.get("/api/live")
async def api_live() -> StreamingResponse:
    live_path = Path("live_state.json")

    async def event_stream() -> AsyncIterator[str]:
        while True:
            try:
                payload = (
                    live_path.read_text(encoding="utf-8")
                    if live_path.exists()
                    else "{}"
                )
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
                payload = (
                    live_path.read_text(encoding="utf-8")
                    if live_path.exists()
                    else "{}"
                )
            except Exception:
                payload = "{}"
            await websocket.send_text(payload)
            await asyncio.sleep(2.0)
    except WebSocketDisconnect:
        return


@app.get("/api/why_not_trading")
def api_why_not_trading() -> dict[str, Any]:
    """Per-symbol synthesized strategy state and likely trade blocker."""
    state = _read_live_state()
    symbols_state = state.get("symbols", {}) if isinstance(state, dict) else {}
    if not isinstance(symbols_state, dict):
        symbols_state = {}

    configured: set[str] = set()
    with contextlib.suppress(Exception):
        configured = {
            s.strip()
            for s in _settings().symbols.split(",")
            if isinstance(s, str) and s.strip()
        }
    active = {str(s).strip() for s in symbols_state.keys() if str(s).strip()}
    all_symbols = sorted(configured | active)

    rows: list[dict[str, Any]] = []
    for symbol in all_symbols:
        entry = symbols_state.get(symbol, {})
        ticker = entry.get("ticker") if isinstance(entry, dict) else None
        window = {
            "open": entry.get("open_price") if isinstance(entry, dict) else None,
            "current": entry.get("current_price") if isinstance(entry, dict) else None,
            "price_change_pct": entry.get("price_change_pct")
            if isinstance(entry, dict)
            else None,
            "seconds_remaining": entry.get("seconds_remaining")
            if isinstance(entry, dict)
            else None,
            "momentum_60s": entry.get("momentum_60s")
            if isinstance(entry, dict)
            else None,
        }
        orderbook = {
            "yes_bid": entry.get("kalshi_yes_bid") if isinstance(entry, dict) else None,
            "yes_ask": entry.get("kalshi_yes_ask") if isinstance(entry, dict) else None,
            "imbalance": entry.get("orderbook_imbalance")
            if isinstance(entry, dict)
            else None,
            "age_s": entry.get("orderbook_age_s") if isinstance(entry, dict) else None,
        }
        last_eval = entry.get("last_eval") if isinstance(entry, dict) else None
        likely_block = entry.get("likely_block") if isinstance(entry, dict) else None
        if not isinstance(last_eval, dict):
            last_eval = {"result": None, "at": None}
        rows.append(
            {
                "symbol": symbol,
                "ticker": ticker,
                "window": window,
                "orderbook": orderbook,
                "last_eval": {
                    "result": last_eval.get("result"),
                    "at": last_eval.get("at"),
                },
                "likely_block": likely_block,
            }
        )

    return {
        "updated_at": state.get("updated_at") if isinstance(state, dict) else None,
        "rows": rows,
    }


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

    # cancel_stale writes pnl='0' with fees=NULL on timeout — NOT a real
    # settlement. A real settle/exit writes both pnl AND fees.
    pnl_raw = trade.get("pnl")
    fees_raw = trade.get("fees")
    if pnl_raw is None:
        status = "pending"
    elif fees_raw is None:
        status = "cancelled"
    else:
        status = "settled"
    lifecycle = {
        "placed_at": trade.get("timestamp"),
        "route": trade.get("route"),
        "status": status,
        "settled": status == "settled",
    }

    return {
        "found": True,
        "trade": trade,
        "signal": signal,
        "lifecycle": lifecycle,
    }


_settings_singleton: Settings | None = None


def _settings() -> Settings:
    global _settings_singleton
    if _settings_singleton is None:
        _settings_singleton = Settings()
    return _settings_singleton


def _require_admin(request: Request) -> None:
    """Reject write requests without a valid admin key."""
    expected = _settings().dashboard_admin_key
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="DASHBOARD_ADMIN_KEY is not configured; admin endpoints disabled",
        )
    provided = request.headers.get("X-Admin-Key", "")
    if provided != expected:
        raise HTTPException(status_code=401, detail="invalid admin key")


@app.get("/api/balance")
def api_balance() -> dict[str, Any]:
    """Kalshi account balance + trading mode, read from live_state.json."""
    data = _read_live_state()
    if not data:
        return {"balance": None, "balance_age_s": None, "trading_mode": None}
    return {
        "balance": data.get("balance"),
        "balance_age_s": data.get("balance_age_s"),
        "trading_mode": data.get("trading_mode"),
        "kelly_fraction": data.get("kelly_fraction"),
        "updated_at": data.get("updated_at"),
    }


@app.get("/api/settings")
def api_settings_get() -> dict[str, Any]:
    """Current runtime-mutable settings + keys the user can change."""
    return {
        "settings": current_settings(_settings()),
        "allowed_keys": settable_keys(),
    }


@app.post("/api/settings")
def api_settings_post(
    request: Request,
    body: dict[str, Any] = Body(...),  # noqa: B008
) -> dict[str, Any]:
    """Queue a setting change for the bot loop to apply."""
    _require_admin(request)
    key = body.get("key")
    value = body.get("value")
    if not isinstance(key, str):
        raise HTTPException(status_code=400, detail="'key' must be a string")

    # Validate immediately against the dashboard's own Settings copy so
    # the user gets fast feedback; the bot applies the change via the queue.
    try:
        alias, coerced = mutate_setting(_settings(), key, value)
    except SettingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    req_id = enqueue("set", {"key": alias, "value": coerced})
    return {"ok": True, "request_id": req_id, "key": alias, "value": coerced}


@app.post("/api/reset")
def api_reset(
    request: Request,
    clear_pnl: bool = False,
) -> dict[str, Any]:
    """Queue a risk-state reset. Bot clears locked sides and cooldowns."""
    _require_admin(request)
    req_id = enqueue("reset", {"clear_pnl": bool(clear_pnl)})
    return {"ok": True, "request_id": req_id, "clear_pnl": bool(clear_pnl)}


@app.post("/api/kill")
def api_kill(request: Request) -> dict[str, Any]:
    """Activate the kill switch (blocks all new trades)."""
    _require_admin(request)
    activate_kill_switch()
    return {"ok": True, "kill_switch_active": True}


@app.post("/api/resume")
def api_resume(request: Request) -> dict[str, Any]:
    """Remove the kill switch."""
    _require_admin(request)
    existed = deactivate_kill_switch()
    return {"ok": True, "kill_switch_active": False, "was_active": existed}


@app.get("/api/kill_switch")
def api_kill_switch_state() -> dict[str, Any]:
    return {"kill_switch_active": kill_switch_active()}


_BOT_LOG_PATH = Path("logs/bot.log")
_LOG_SCAN_CAP_BYTES = 2 * 1024 * 1024  # never scan more than 2 MB per request
_LOG_CHUNK_BYTES = 64 * 1024
_LOG_LEVEL_ORDER = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}


def _iter_log_lines_reverse(max_bytes: int) -> tuple[list[str], int]:
    """Return up to max_bytes of the newest bot.log lines, oldest-first.

    Reads the file backwards in 64 KB chunks until either the requested byte
    budget is exhausted or the file begins. Keeps partial-line fragments
    across chunk boundaries so we never emit a truncated JSON line.
    """
    if not _BOT_LOG_PATH.exists():
        return [], 0
    cap = min(max_bytes, _LOG_SCAN_CAP_BYTES)
    with _BOT_LOG_PATH.open("rb") as f:
        f.seek(0, io.SEEK_END)
        file_end = f.tell()
        pos = file_end
        buf = b""
        collected: list[str] = []
        while pos > 0 and (file_end - pos) < cap:
            read_size = min(_LOG_CHUNK_BYTES, pos)
            pos -= read_size
            f.seek(pos)
            chunk = f.read(read_size)
            buf = chunk + buf
            # We may be mid-line at the left boundary if pos > 0; hold the
            # first fragment until the next iteration completes it.
            parts = buf.split(b"\n")
            if pos > 0:
                buf = parts[0]
                complete = parts[1:]
            else:
                buf = b""
                complete = parts
            for raw in reversed(complete):
                if not raw.strip():
                    continue
                try:
                    collected.append(raw.decode("utf-8", errors="replace"))
                except Exception:
                    continue
        scanned = file_end - pos
    collected.reverse()
    return collected, scanned


def _parse_log_line(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _match_log_entry(
    entry: dict[str, Any],
    event_filters: list[str],
    min_level: int,
    since_iso: str | None,
) -> bool:
    event = str(entry.get("event", ""))
    if event_filters and not any(f in event for f in event_filters):
        return False
    if min_level > 0:
        level = str(entry.get("level", "info")).lower()
        if _LOG_LEVEL_ORDER.get(level, 20) < min_level:
            return False
    if since_iso is not None:
        ts = entry.get("timestamp")
        if not isinstance(ts, str) or ts < since_iso:
            return False
    return True


@app.get("/api/logs/tail")
def api_logs_tail(
    n: int = 100,
    event: str | None = None,
    level: str | None = None,
    since: str | None = None,
    max_bytes: int = _LOG_SCAN_CAP_BYTES,
) -> dict[str, Any]:
    """Return the most recent matching JSON log lines.

    Reverse-scans `logs/bot.log` in 64 KB chunks, bounded by `max_bytes`
    (hard-capped at 2 MB). Intended for AI-driven incident diagnosis — keep
    requests narrow with `event=` / `since=` filters rather than pulling raw.
    """
    n = max(1, min(n, 500))
    max_bytes = max(_LOG_CHUNK_BYTES, min(max_bytes, _LOG_SCAN_CAP_BYTES))
    event_filters: list[str] = []
    if event:
        event_filters = [e.strip() for e in event.split(",") if e.strip()]
    min_level = _LOG_LEVEL_ORDER.get(level.lower(), 0) if level else 0

    lines, scanned = _iter_log_lines_reverse(max_bytes)
    matching: list[dict[str, Any]] = []
    for raw in reversed(lines):  # newest → oldest
        entry = _parse_log_line(raw)
        if entry is None:
            continue
        if not _match_log_entry(entry, event_filters, min_level, since):
            continue
        matching.append(entry)
        if len(matching) >= n:
            break
    matching.reverse()  # oldest → newest to read top-down
    return {
        "lines": matching,
        "scanned_bytes": scanned,
        "truncated": scanned >= max_bytes,
        "returned": len(matching),
    }


@app.get("/api/logs/stats")
def api_logs_stats(max_bytes: int = _LOG_SCAN_CAP_BYTES) -> dict[str, Any]:
    """Histogram of event names in the recent log window.

    Gives a fast "what's happening right now" view without transferring raw
    lines — emits counts keyed by event prefix (up to the first space, so
    `kalshi_ws_negative_qty ticker=X side=...` collapses to
    `kalshi_ws_negative_qty`).
    """
    max_bytes = max(_LOG_CHUNK_BYTES, min(max_bytes, _LOG_SCAN_CAP_BYTES))
    lines, scanned = _iter_log_lines_reverse(max_bytes)
    counts: dict[str, int] = {}
    first_ts: str | None = None
    last_ts: str | None = None
    for raw in lines:
        entry = _parse_log_line(raw)
        if entry is None:
            continue
        event = str(entry.get("event", "")).split(" ", 1)[0]
        if not event:
            continue
        counts[event] = counts.get(event, 0) + 1
        ts = entry.get("timestamp")
        if isinstance(ts, str):
            if first_ts is None or ts < first_ts:
                first_ts = ts
            if last_ts is None or ts > last_ts:
                last_ts = ts
    span_s: float | None = None
    if first_ts and last_ts:
        with contextlib.suppress(Exception):
            span_s = (
                datetime.fromisoformat(last_ts) - datetime.fromisoformat(first_ts)
            ).total_seconds()
    sorted_counts = dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))
    return {
        "scanned_bytes": scanned,
        "window_start": first_ts,
        "window_end": last_ts,
        "window_spans_seconds": span_s,
        "events": sorted_counts,
    }


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    html_path = Path(__file__).parent / "dashboard.html"
    return html_path.read_text(encoding="utf-8")


@app.get("/finance", response_class=HTMLResponse)
def finance() -> str:
    html_path = Path(__file__).parent / "finance.html"
    return html_path.read_text(encoding="utf-8")


if __name__ == "__main__":
    import uvicorn

    from kalshi_bot.config import Settings

    settings = Settings()
    uvicorn.run(app, host="0.0.0.0", port=settings.dashboard_port)
