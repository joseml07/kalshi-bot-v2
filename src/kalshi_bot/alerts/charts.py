"""Chart generation utilities for Discord bot attachments."""

from __future__ import annotations

import io
import sqlite3
from dataclasses import dataclass
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


@dataclass
class ChartImage:
    """Rendered chart as in-memory PNG."""

    filename: str
    content: bytes


def _query(db_path: str, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        return rows
    finally:
        conn.close()


def _fig_to_png(filename: str) -> ChartImage:
    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", dpi=130)
    plt.close()
    return ChartImage(filename=filename, content=buf.getvalue())


def chart_pnl(db_path: str = "trades.db") -> ChartImage:
    rows = _query(
        db_path,
        "SELECT timestamp, CAST(pnl AS REAL) AS pnl FROM trades WHERE pnl IS NOT NULL ORDER BY timestamp",
    )
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(10, 4))
    if not rows:
        ax.text(0.5, 0.5, "No settled trades yet", ha="center", va="center")
        ax.axis("off")
        return _fig_to_png("pnl.png")

    cumulative: list[float] = []
    total = 0.0
    for r in rows:
        total += float(r["pnl"] or 0.0)
        cumulative.append(total)

    color = "#3fb950" if cumulative[-1] >= 0 else "#f85149"
    ax.plot(range(len(cumulative)), cumulative, color=color, linewidth=2)
    ax.axhline(0, color="#888", linewidth=1, linestyle="--")
    ax.set_title("Cumulative PnL")
    ax.set_xlabel("Trade #")
    ax.set_ylabel("USD")
    return _fig_to_png("pnl.png")


def chart_winrate(db_path: str = "trades.db", window: int = 20) -> ChartImage:
    rows = _query(
        db_path,
        "SELECT CAST(pnl AS REAL) AS pnl FROM trades WHERE pnl IS NOT NULL ORDER BY timestamp",
    )
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(10, 4))
    if not rows:
        ax.text(0.5, 0.5, "No settled trades yet", ha="center", va="center")
        ax.axis("off")
        return _fig_to_png("winrate.png")

    outcomes = [1 if float(r["pnl"] or 0.0) > 0 else 0 for r in rows]
    rolling: list[float] = []
    for i in range(len(outcomes)):
        left = max(0, i - window + 1)
        segment = outcomes[left : i + 1]
        rolling.append(sum(segment) / len(segment) * 100)

    ax.plot(range(len(rolling)), rolling, color="#58a6ff", linewidth=2)
    ax.set_ylim(0, 100)
    ax.axhline(50, color="#888", linewidth=1, linestyle="--")
    ax.set_title(f"Rolling Win Rate ({window} trades)")
    ax.set_xlabel("Trade #")
    ax.set_ylabel("Win rate %")
    return _fig_to_png("winrate.png")


def chart_scatter(db_path: str = "trades.db") -> ChartImage:
    rows = _query(
        db_path,
        """SELECT s.seconds_remaining as sec_left, CAST(t.pnl AS REAL) as pnl, t.side
           FROM trades t JOIN signals s ON s.ticker = t.ticker
           WHERE t.pnl IS NOT NULL AND s.action IN ('trade','paper_trade')""",
    )
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(10, 4))
    if not rows:
        ax.text(0.5, 0.5, "No joined signal/trade data yet", ha="center", va="center")
        ax.axis("off")
        return _fig_to_png("scatter.png")

    yes_x, yes_y, no_x, no_y = [], [], [], []
    for r in rows:
        side = str(r["side"])
        if side == "yes":
            yes_x.append(float(r["sec_left"] or 0.0))
            yes_y.append(float(r["pnl"] or 0.0))
        else:
            no_x.append(float(r["sec_left"] or 0.0))
            no_y.append(float(r["pnl"] or 0.0))

    ax.scatter(yes_x, yes_y, color="#3fb950", alpha=0.75, label="YES")
    ax.scatter(no_x, no_y, color="#f85149", alpha=0.75, label="NO")
    ax.axhline(0, color="#888", linewidth=1, linestyle="--")
    ax.set_title("Trade Scatter: Seconds Remaining vs PnL")
    ax.set_xlabel("Seconds remaining at entry")
    ax.set_ylabel("PnL")
    ax.legend()
    return _fig_to_png("scatter.png")


def chart_routes(db_path: str = "trades.db") -> ChartImage:
    rows = _query(
        db_path,
        """SELECT route,
                  COUNT(*) as trades,
                  SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins
           FROM trades WHERE pnl IS NOT NULL GROUP BY route""",
    )
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(10, 4))
    if not rows:
        ax.text(0.5, 0.5, "No route data yet", ha="center", va="center")
        ax.axis("off")
        return _fig_to_png("routes.png")

    labels = [str(r["route"] or "unknown") for r in rows]
    trades = [int(r["trades"] or 0) for r in rows]
    wins = [int(r["wins"] or 0) for r in rows]
    wr = [(w / t * 100) if t else 0.0 for w, t in zip(wins, trades)]

    bars = ax.bar(labels, trades, color=["#58a6ff", "#3fb950", "#f85149", "#d29922"][: len(labels)])
    for i, bar in enumerate(bars):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.1, f"{wr[i]:.0f}% WR", ha="center")
    ax.set_title("Route Breakdown")
    ax.set_ylabel("Settled trades")
    return _fig_to_png("routes.png")


def chart_edge(db_path: str = "trades.db") -> ChartImage:
    rows = _query(
        db_path,
        "SELECT CAST(net_edge AS REAL) as edge, CAST(pnl AS REAL) as pnl FROM trades WHERE pnl IS NOT NULL",
    )
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(10, 4))
    if not rows:
        ax.text(0.5, 0.5, "No settled trades yet", ha="center", va="center")
        ax.axis("off")
        return _fig_to_png("edge.png")

    x = [float(r["edge"] or 0.0) * 100 for r in rows]
    y = [float(r["pnl"] or 0.0) for r in rows]
    colors = ["#3fb950" if p >= 0 else "#f85149" for p in y]

    ax.scatter(x, y, c=colors, alpha=0.75)
    ax.axhline(0, color="#888", linewidth=1, linestyle="--")
    ax.set_title("Edge vs Outcome")
    ax.set_xlabel("Net edge at entry (%)")
    ax.set_ylabel("PnL")
    return _fig_to_png("edge.png")


def chart_daily(db_path: str = "trades.db") -> ChartImage:
    rows = _query(
        db_path,
        """SELECT date(timestamp) as day, COALESCE(SUM(CAST(pnl AS REAL)), 0) as total_pnl
           FROM trades WHERE pnl IS NOT NULL GROUP BY day ORDER BY day""",
    )
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(10, 4))
    if not rows:
        ax.text(0.5, 0.5, "No daily data yet", ha="center", va="center")
        ax.axis("off")
        return _fig_to_png("daily.png")

    days = [str(r["day"]) for r in rows]
    pnl = [float(r["total_pnl"] or 0.0) for r in rows]
    colors = ["#3fb950" if v >= 0 else "#f85149" for v in pnl]
    ax.bar(days, pnl, color=colors)
    ax.axhline(0, color="#888", linewidth=1, linestyle="--")
    ax.set_title("Daily PnL")
    ax.set_ylabel("USD")
    ax.tick_params(axis="x", rotation=30)
    return _fig_to_png("daily.png")
