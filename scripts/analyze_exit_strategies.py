"""
Analyze: what if we exit when the price has moved favorably (profit lock)
or cut when it moves against us (loss stop)?

Tests:
1. Profit lock: if bid exceeds entry + X, exit immediately
2. Trailing stop: if bid drops Y from peak since entry, exit
3. Hard stop: if bid drops below entry - Z, exit
"""
from __future__ import annotations

import sqlite3
from typing import Any

DB = sqlite3.connect("/root/kalshi-bot/kalshi-bot-v2/trades.db")
DB.row_factory = sqlite3.Row

def get_snapshots_from_entry(ticker: str, entry_ts: str) -> list[dict]:
    """Get all window_snapshots after entry for this ticker, ordered by time."""
    rows = DB.execute("""
        SELECT * FROM window_snapshots
        WHERE ticker=? AND timestamp >= ?
        ORDER BY timestamp
    """, (ticker, entry_ts)).fetchall()
    return [dict(r) for r in rows]

def main():
    trades = DB.execute("""
        SELECT t.*, me.result as actual_result
        FROM trades t
        LEFT JOIN market_events me ON t.ticker = me.ticker AND me.event_type = 'close'
        WHERE t.pnl IS NOT NULL AND t.pnl != ''
        AND t.timestamp > '2026-04-15'
        ORDER BY t.timestamp
    """).fetchall()

    results = {
        "time_exit_30": {"trades": 0, "wins": 0, "pnl": 0.0},
        "profit_lock_15c": {"trades": 0, "wins": 0, "pnl": 0.0},
        "profit_lock_10c": {"trades": 0, "wins": 0, "pnl": 0.0},
        "profit_lock_20c": {"trades": 0, "wins": 0, "pnl": 0.0},
        "stop_loss_10c": {"trades": 0, "wins": 0, "pnl": 0.0},
        "stop_loss_15c": {"trades": 0, "wins": 0, "pnl": 0.0},
        "trail_5c": {"trades": 0, "wins": 0, "pnl": 0.0},
        "settlement": {"trades": 0, "wins": 0, "pnl": 0.0},
    }

    for t in trades:
        trade = dict(t)
        ticker = trade["ticker"]
        side = trade["side"]
        entry_price = float(trade["price"])
        contracts = int(trade["contracts"])
        route = trade.get("route", "taker")

        # Simplified fee: just entry fee
        if route == "maker":
            raw_fee = 0.0175 * contracts * entry_price * (1 - entry_price)
        else:
            raw_fee = 0.07 * contracts * entry_price * (1 - entry_price)
        entry_fee = round(raw_fee * 100 + 0.0001) / 100

        # Settlement as baseline
        result_str = trade.get("actual_result", "")
        if not result_str:
            continue
        won = (side == "yes" and result_str == "up") or (side == "no" and result_str == "down")
        settle_pnl = ((1.0 - entry_price) if won else -entry_price) * contracts - entry_fee
        results["settlement"]["trades"] += 1
        results["settlement"]["pnl"] += settle_pnl
        if settle_pnl > 0:
            results["settlement"]["wins"] += 1

        # Get the price trajectory
        snaps = get_snapshots_from_entry(ticker, trade["timestamp"])
        if not snaps:
            continue

        # For each snapshot, compute the bid price for this side
        peak_bid = 0.0
        exited_time_30 = False
        exited_profit_15 = False
        exited_profit_10 = False
        exited_profit_20 = False
        exited_stop_10 = False
        exited_stop_15 = False
        exited_trail_5 = False
        trail_high_water = 0.0

        for snap in snaps:
            bid_raw = snap.get("kalshi_yes_bid") if side == "yes" else snap.get("kalshi_no_bid")
            if bid_raw is None:
                continue
            bid = float(bid_raw)
            secs_remaining = int(snap["seconds_remaining"])
            peak_bid = max(peak_bid, bid)
            trail_high_water = max(trail_high_water, bid)

            # Time exit at T-30
            if not exited_time_30 and secs_remaining <= 30:
                taker_exit_fee = round(0.07 * contracts * bid * (1 - bid) * 100 + 0.0001) / 100
                net = (bid - entry_price) * contracts - entry_fee - taker_exit_fee
                results["time_exit_30"]["trades"] += 1
                results["time_exit_30"]["pnl"] += net
                if net > 0:
                    results["time_exit_30"]["wins"] += 1
                exited_time_30 = True

            # Profit lock at 15c gain
            if not exited_profit_15 and bid - entry_price >= 0.15:
                taker_exit_fee = round(0.07 * contracts * bid * (1 - bid) * 100 + 0.0001) / 100
                net = (bid - entry_price) * contracts - entry_fee - taker_exit_fee
                results["profit_lock_15c"]["trades"] += 1
                results["profit_lock_15c"]["pnl"] += net
                if net > 0:
                    results["profit_lock_15c"]["wins"] += 1
                exited_profit_15 = True

            # Profit lock at 10c gain
            if not exited_profit_10 and bid - entry_price >= 0.10:
                taker_exit_fee = round(0.07 * contracts * bid * (1 - bid) * 100 + 0.0001) / 100
                net = (bid - entry_price) * contracts - entry_fee - taker_exit_fee
                results["profit_lock_10c"]["trades"] += 1
                results["profit_lock_10c"]["pnl"] += net
                if net > 0:
                    results["profit_lock_10c"]["wins"] += 1
                exited_profit_10 = True

            # Profit lock at 20c gain
            if not exited_profit_20 and bid - entry_price >= 0.20:
                taker_exit_fee = round(0.07 * contracts * bid * (1 - bid) * 100 + 0.0001) / 100
                net = (bid - entry_price) * contracts - entry_fee - taker_exit_fee
                results["profit_lock_20c"]["trades"] += 1
                results["profit_lock_20c"]["pnl"] += net
                if net > 0:
                    results["profit_lock_20c"]["wins"] += 1
                exited_profit_20 = True

            # Hard stop at 10c loss
            if not exited_stop_10 and entry_price - bid >= 0.10:
                taker_exit_fee = round(0.07 * contracts * bid * (1 - bid) * 100 + 0.0001) / 100
                net = (bid - entry_price) * contracts - entry_fee - taker_exit_fee
                results["stop_loss_10c"]["trades"] += 1
                results["stop_loss_10c"]["pnl"] += net
                if net > 0:
                    results["stop_loss_10c"]["wins"] += 1
                exited_stop_10 = True

            # Hard stop at 15c loss
            if not exited_stop_15 and entry_price - bid >= 0.15:
                taker_exit_fee = round(0.07 * contracts * bid * (1 - bid) * 100 + 0.0001) / 100
                net = (bid - entry_price) * contracts - entry_fee - taker_exit_fee
                results["stop_loss_15c"]["trades"] += 1
                results["stop_loss_15c"]["pnl"] += net
                if net > 0:
                    results["stop_loss_15c"]["wins"] += 1
                exited_stop_15 = True

            # Trailing stop: exit if dropped 5c from peak
            if not exited_trail_5 and trail_high_water - bid >= 0.05 and trail_high_water > entry_price + 0.05:
                taker_exit_fee = round(0.07 * contracts * bid * (1 - bid) * 100 + 0.0001) / 100
                net = (bid - entry_price) * contracts - entry_fee - taker_exit_fee
                results["trail_5c"]["trades"] += 1
                results["trail_5c"]["pnl"] += net
                if net > 0:
                    results["trail_5c"]["wins"] += 1
                exited_trail_5 = True

        # If no exit rule fired, that trade falls through to settlement
    print("Exit Strategy Comparison")
    print("========================")
    print()
    print(f"  {'Exit Rule':>20s} | {'Trades':>7s} | {'WR':>6s} | {'Total P&L':>10s} | {'vs Settle':>10s}")
    print(f"  {'-'*20} | {'-'*7} | {'-'*6} | {'-'*10} | {'-'*10}")

    settle_total = results["settlement"]["pnl"]
    for key in ["time_exit_30", "profit_lock_10c", "profit_lock_15c", "profit_lock_20c", "stop_loss_10c", "stop_loss_15c", "trail_5c", "settlement"]:
        r = results[key]
        n = r["trades"]
        if n == 0:
            continue
        wr = r["wins"] / n * 100
        delta = r["pnl"] - settle_total
        print(f"  {key:>20s} | {n:>7d} | {wr:>5.1f}% | ${r['pnl']:>9.2f} | ${delta:>+9.2f}")

main()
