"""
Analyze exit timing: what if we exited at T-N seconds instead of holding to settlement?

Uses window_snapshots + market_events to reconstruct "if we had a position,
what bid price would we have gotten at T-30, T-60, T-90, T-120?" and
compares against settlement P&L.

Exit simulation:
- For each window that has a matching trade, find the window_snapshot closest to T-N
- Use best_yes_bid (for YES) or best_no_bid (for NO) as exit price
- Compute P&L: (exit_price - entry_price) * contracts - fees
"""
from __future__ import annotations

import sqlite3
import sys
from decimal import Decimal, ROUND_CEILING
from typing import Any

DB = sqlite3.connect("/root/kalshi-bot/kalshi-bot-v2/trades.db")
DB.row_factory = sqlite3.Row

def taker_fee(contracts: int, price: float) -> Decimal:
    raw = 0.07 * contracts * price * (1 - price)
    return Decimal(str(round(raw * 100 + 0.999999, 0))) / 100

def maker_fee(contracts: int, price: float) -> Decimal:
    raw = 0.0175 * contracts * price * (1 - price)
    return Decimal(str(round(raw * 100 + 0.999999, 0))) / 100

def get_actual_result(ticker: str) -> str | None:
    row = DB.execute(
        "SELECT result FROM market_events WHERE ticker=? AND event_type='close' AND result IN ('up','down')",
        (ticker,)
    ).fetchone()
    return row["result"] if row else None

def get_snapshot_near(ticker: str, target_secs: int) -> dict[str, Any] | None:
    """Find the snapshot closest to <target_secs> seconds remaining for this ticker."""
    row = DB.execute("""
        SELECT * FROM window_snapshots
        WHERE ticker=? AND seconds_remaining <= ?
        ORDER BY ABS(seconds_remaining - ?) LIMIT 1
    """, (ticker, target_secs + 5, target_secs)).fetchone()
    return dict(row) if row else None

def compute_exit_pnl(trade: dict, exit_price: float) -> tuple[float, float]:
    """Return (gross_pnl_per_contract, net_pnl)."""
    c = int(trade["contracts"])
    entry = float(trade["price"])
    side = trade["side"]
    route = trade.get("route", "taker")

    entry_fee = float(taker_fee(c, entry) if route != "maker" else maker_fee(c, entry))

    if side == "yes":
        gross_per = exit_price - entry
    else:
        gross_per = exit_price - entry

    gross = gross_per * c
    exit_fee = float(taker_fee(c, exit_price))
    net = gross - entry_fee - exit_fee
    return gross_per, net

def settlement_pnl(trade: dict, result: str) -> float:
    """What would PnL be if held to settlement?"""
    c = int(trade["contracts"])
    entry = float(trade["price"])
    side = trade["side"]
    route = trade.get("route", "taker")
    entry_fee = float(taker_fee(c, entry) if route != "maker" else maker_fee(c, entry))

    won = (result == "up" and side == "yes") or (result == "down" and side == "no")
    if won:
        gross = (1.0 - entry) * c
    else:
        gross = -entry * c
    return gross - entry_fee


def main():
    trades = DB.execute("""
        SELECT * FROM trades
        WHERE pnl IS NOT NULL AND pnl != ''
        AND timestamp > '2026-04-15'
        ORDER BY timestamp
    """).fetchall()

    results: dict[str, dict[str, Any]] = {}
    thresholds = [30, 60, 90, 120]

    for thresh in thresholds:
        results[f"t{thresh}"] = {"trades": 0, "wins": 0, "pnl": 0.0, "fee_saved": 0.0}

    results["settlement"] = {"trades": 0, "wins": 0, "pnl": 0.0}

    skipped_no_snapshot = 0
    skipped_no_result = 0

    for t in trades:
        trade = dict(t)
        ticker = trade["ticker"]
        result_str = get_actual_result(ticker)
        if result_str is None:
            skipped_no_result += 1
            continue

        # Settlement P&L
        settle = settlement_pnl(trade, result_str)
        results["settlement"]["trades"] += 1
        results["settlement"]["pnl"] += settle
        if settle > 0:
            results["settlement"]["wins"] += 1

        # Check each exit threshold
        for thresh in thresholds:
            snap = get_snapshot_near(ticker, thresh)
            if snap is None:
                skipped_no_snapshot += 1
                continue

            side = trade["side"]
            bid_raw = snap.get("kalshi_yes_bid") if side == "yes" else snap.get("kalshi_no_bid")
            if bid_raw is None:
                skipped_no_snapshot += 1
                continue
            bid_price = float(bid_raw)
            if bid_price <= 0.01:
                continue

            gross_per, net = compute_exit_pnl(trade, bid_price)
            results[f"t{thresh}"]["trades"] += 1
            results[f"t{thresh}"]["pnl"] += net
            if net > 0:
                results[f"t{thresh}"]["wins"] += 1

    print("Exit Timing Analysis")
    print("=====================")
    print(f"Total trades analyzed: {results['settlement']['trades']}")
    print(f"Skipped (no snapshot): {skipped_no_snapshot}")
    print(f"Skipped (no result): {skipped_no_result}")
    print()

    for key in ["t30", "t60", "t90", "t120", "settlement"]:
        r = results[key]
        n = r["trades"]
        if n == 0:
            continue
        wr = r["wins"] / n * 100
        avg = r["pnl"] / n
        label = key.replace("t", "T-") + "s" if key != "settlement" else "Settlement"
        print(f"  {label:>14s}: {n:>5d} trades | {wr:>5.1f}% WR | ${r['pnl']:>9.2f} total | ${avg:>6.3f}/trade")

    print()
    # Compare to settlement for each threshold
    settle_total = results["settlement"]["pnl"]
    for thresh in thresholds:
        key = f"t{thresh}"
        delta = results[key]["pnl"] - settle_total
        print(f"  T-{thresh:>3d}s vs Settlement: ${delta:+.2f}")

main()
