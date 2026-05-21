"""Chart generation utilities for Discord bot attachments."""

from __future__ import annotations

import calendar as _calendar
import io
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


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


def chart_calendar(
    db_path: str = "trades.db",
    year: int | None = None,
    month: int | None = None,
) -> ChartImage:
    """Render a month-grid P&L calendar like trading journal software."""
    rows = _query(
        db_path,
        """SELECT date(timestamp) as day,
                  COALESCE(SUM(CAST(pnl AS REAL)), 0) as total_pnl,
                  COUNT(*) as trades,
                  SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins
           FROM trades WHERE pnl IS NOT NULL GROUP BY day ORDER BY day""",
    )
    by_day: dict[str, dict[str, Any]] = {}
    for r in rows:
        by_day[str(r["day"])] = {
            "pnl": float(r["total_pnl"]),
            "trades": int(r["trades"]),
            "wins": int(r["wins"]),
        }

    # Default to most recent month with data
    if year is None or month is None:
        if by_day:
            last = sorted(by_day.keys())[-1]
            d = date.fromisoformat(last)
            year, month = d.year, d.month
        else:
            today = date.today()
            year, month = today.year, today.month

    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.set_xlim(0, 7)
    ax.set_ylim(-7, 1)
    ax.axis("off")
    ax.set_aspect("equal")

    month_name = _calendar.month_name[month]
    ax.text(3.5, 0.6, f"{month_name} {year}", ha="center", va="center",
            fontsize=16, fontweight="bold", color="white")

    # Weekday headers
    for i, name in enumerate(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]):
        ax.text(i + 0.5, 0.05, name, ha="center", va="center",
                fontsize=8, color="#9ba7b9")

    # Compute max absolute PnL for intensity scaling
    days_in_month = _calendar.monthrange(year, month)[1]
    max_abs = 0.0
    month_total = 0.0
    for d in range(1, days_in_month + 1):
        key = f"{year}-{month:02d}-{d:02d}"
        if key in by_day:
            abs_pnl = abs(by_day[key]["pnl"])
            if abs_pnl > max_abs:
                max_abs = abs_pnl
            month_total += by_day[key]["pnl"]
    if max_abs == 0:
        max_abs = 1.0

    first_dow = _calendar.monthrange(year, month)[0]
    # Python Monday=0; we need Sunday=0
    first_dow = (first_dow + 1) % 7

    for d in range(1, days_in_month + 1):
        col = (first_dow + d - 1) % 7
        row = (first_dow + d - 1) // 7
        x = col
        y = -row - 0.5

        key = f"{year}-{month:02d}-{d:02d}"
        info = by_day.get(key)

        if info is None:
            face = "#1a2030"
            pnl_text = ""
            meta = ""
        else:
            pnl = info["pnl"]
            intensity = min(1.0, abs(pnl) / max_abs) * 0.6 + 0.1
            if pnl > 0:
                face = (0.25, 0.73, 0.31, intensity)
            elif pnl < 0:
                face = (0.97, 0.32, 0.29, intensity)
            else:
                face = "#1a2030"
            sign = "+" if pnl >= 0 else ""
            pnl_text = f"{sign}${pnl:.0f}"
            meta = f"{info['trades']}t {info['wins']}w"

        rect = FancyBboxPatch(
            (x + 0.04, y - 0.42), 0.92, 0.84,
            boxstyle="round,pad=0.04", facecolor=face,
            edgecolor="#2d3748", linewidth=0.5,
        )
        ax.add_patch(rect)
        ax.text(x + 0.14, y + 0.28, str(d), ha="left", va="center",
                fontsize=7, color="#9ba7b9")
        if pnl_text:
            ax.text(x + 0.5, y - 0.02, pnl_text, ha="center", va="center",
                    fontsize=9, fontweight="bold",
                    color="#56d364" if info["pnl"] >= 0 else "#ff8882")
        if meta:
            ax.text(x + 0.5, y - 0.26, meta, ha="center", va="center",
                    fontsize=6.5, color="#9ba7b9")

    # Monthly summary bar
    sign = "+" if month_total >= 0 else ""
    color = "#3fb950" if month_total >= 0 else "#f85149"
    ax.text(3.5, -((first_dow + days_in_month - 1) // 7) - 1.3,
            f"Month total: {sign}${month_total:.2f}",
            ha="center", va="center", fontsize=11, fontweight="bold", color=color)

    return _fig_to_png("calendar.png")
