"""
Analyze OBI magnitude vs outcome.

For every trade, look up the window_snapshot closest to entry time,
extract yes_depth, no_depth, compute OBI magnitude = abs(obi),
and bucket by OBI magnitude to see win rate per bucket.

Also test: if we only traded when |OBI| > threshold, what would WR be?
"""
from __future__ import annotations

import sqlite3
from typing import Any

DB = sqlite3.connect("/root/kalshi-bot/kalshi-bot-v2/trades.db")
DB.row_factory = sqlite3.Row

def get_obi_at_entry(ticker: str, trade_ts: str) -> float | None:
    """Find the orderbook_snapshot closest to the trade timestamp."""
    row = DB.execute("""
        SELECT yes_depth, no_depth FROM orderbook_snapshots
        WHERE ticker=? AND timestamp <= ?
        ORDER BY timestamp DESC LIMIT 1
    """, (ticker, trade_ts)).fetchone()
    if row is None:
        return None
    yd = int(row["yes_depth"])
    nd = int(row["no_depth"])
    if yd + nd == 0:
        return None
    obi = (yd - nd) / (yd + nd)
    return obi

def main():
    trades = DB.execute("""
        SELECT * FROM trades
        WHERE pnl IS NOT NULL AND pnl != ''
        AND timestamp > '2026-04-15'
        ORDER BY timestamp
    """).fetchall()

    # Bucket by absolute OBI
    buckets: dict[str, dict[str, Any]] = {
        "0.00-0.05": {"trades": 0, "wins": 0, "pnl": 0.0},
        "0.05-0.10": {"trades": 0, "wins": 0, "pnl": 0.0},
        "0.10-0.20": {"trades": 0, "wins": 0, "pnl": 0.0},
        "0.20-0.40": {"trades": 0, "wins": 0, "pnl": 0.0},
        "0.40-1.00": {"trades": 0, "wins": 0, "pnl": 0.0},
    }

    # Also separate by OBI sign agreement
    obi_sign_wrong = {"trades": 0, "wins": 0, "pnl": 0.0}

    no_obi = 0
    for t in trades:
        trade = dict(t)
        obi = get_obi_at_entry(trade["ticker"], trade["timestamp"])
        if obi is None:
            no_obi += 1
            continue

        abs_obi = abs(obi)
        won = float(trade["pnl"]) > 0
        pnl = float(trade["pnl"])

        # Bucket
        if abs_obi < 0.05:
            key = "0.00-0.05"
        elif abs_obi < 0.10:
            key = "0.05-0.10"
        elif abs_obi < 0.20:
            key = "0.10-0.20"
        elif abs_obi < 0.40:
            key = "0.20-0.40"
        else:
            key = "0.40-1.00"

        buckets[key]["trades"] += 1
        buckets[key]["pnl"] += pnl
        if won:
            buckets[key]["wins"] += 1

        # Did OBI sign agree with trade side?
        side = trade["side"]
        expected_obi_sign = 1 if side == "yes" else -1
        if (obi > 0) != (expected_obi_sign > 0):
            obi_sign_wrong["trades"] += 1
            obi_sign_wrong["pnl"] += pnl
            if won:
                obi_sign_wrong["wins"] += 1

    print("OBI Magnitude vs Win Rate")
    print("=========================")
    print(f"Trades with OBI data: {sum(b['trades'] for b in buckets.values())}  (no OBI: {no_obi})")
    print()
    print(f"  {'Bucket':>12s} | {'Trades':>7s} | {'WR':>6s} | {'Total P&L':>10s} | {'Avg P&L':>8s}")
    print(f"  {'-'*12} | {'-'*7} | {'-'*6} | {'-'*10} | {'-'*8}")

    for key in ["0.00-0.05", "0.05-0.10", "0.10-0.20", "0.20-0.40", "0.40-1.00"]:
        b = buckets[key]
        n = b["trades"]
        if n == 0:
            continue
        wr = b["wins"] / n * 100
        avg = b["pnl"] / n
        print(f"  {key:>12s} | {n:>7d} | {wr:>5.1f}% | ${b['pnl']:>9.2f} | ${avg:>7.3f}")

    print()
    print(f"Trades where OBI sign DISAGREED with trade side: {obi_sign_wrong['trades']}")
    if obi_sign_wrong["trades"] > 0:
        wr_wrong = obi_sign_wrong["wins"] / obi_sign_wrong["trades"] * 100
        print(f"  WR: {wr_wrong:.1f}%  P&L: ${obi_sign_wrong['pnl']:.2f}")

    # Cumulative filter: only trade if |OBI| > threshold
    print()
    print("Cumulative: WR if we only traded with |OBI| > threshold")
    print()
    all_entries = []
    for t in trades:
        trade = dict(t)
        obi = get_obi_at_entry(trade["ticker"], trade["timestamp"])
        if obi is not None:
            all_entries.append({"pnl": float(trade["pnl"]), "won": float(trade["pnl"]) > 0, "abs_obi": abs(obi)})

    all_entries.sort(key=lambda x: x["abs_obi"])
    for cutoff in [0.0, 0.02, 0.05, 0.08, 0.10, 0.15, 0.20, 0.30]:
        filtered = [e for e in all_entries if e["abs_obi"] > cutoff]
        n = len(filtered)
        if n == 0:
            continue
        wr = sum(1 for e in filtered if e["won"]) / n * 100
        pnl = sum(e["pnl"] for e in filtered)
        avg = pnl / n
        pct_kept = n / len(all_entries) * 100
        print(f"  |OBI| > {cutoff:4.2f}: {n:>5d} trades ({pct_kept:>4.0f}%) | {wr:>5.1f}% WR | ${pnl:>9.2f} | ${avg:>6.3f}/trade")

main()
