"""Is the SELL-YES edge structural or just bearish drift?

Split June calendar days into up-leaning vs down-leaning by that day's UP rate,
then measure SELL>=0.85 honest P&L within each bucket. If the edge only exists
on down days and inverts on up days -> pure drift -> dies on a rally.
"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict

from honest_harness import (DB, JUNE, first_cross, load_windows, taker_fee,
                            window_outcome)


def day_of(ts: str) -> str:
    return ts[:10]


def main():
    windows = load_windows(JUNE)
    outcomes, day_up, day_n = {}, defaultdict(int), defaultdict(int)
    win_day = {}
    for t, snaps in windows.items():
        o = window_outcome(snaps)
        if o is None:
            continue
        outcomes[t] = o
        d = day_of(snaps[0]["timestamp"])
        win_day[t] = d
        day_up[d] += o
        day_n[d] += 1

    # classify days
    print("=== Per-day UP rate ===")
    up_days, down_days = set(), set()
    for d in sorted(day_n):
        rate = day_up[d] / day_n[d]
        tag = "UP-lean" if rate >= 0.45 else ("DOWN-lean" if rate <= 0.40 else "mixed")
        if rate >= 0.45:
            up_days.add(d)
        elif rate <= 0.40:
            down_days.add(d)
        print(f"  {d}  n={day_n[d]:>3}  UP={rate:.2f}  {tag}")
    print(f"\nUP-lean days={len(up_days)} DOWN-lean days={len(down_days)} "
          f"mixed={len(day_n)-len(up_days)-len(down_days)}")
    print()

    def sell85(days, label):
        n = downs = 0
        hon = 0.0
        for t, snaps in windows.items():
            if t not in outcomes or win_day[t] not in days:
                continue
            s = first_cross(snaps, 0.85, 1.0, 10, 900, "kalshi_yes_ask")
            if s is None or s["kalshi_yes_bid"] is None:
                continue
            n += 1
            down = outcomes[t] == 0
            downs += down
            p = s["kalshi_yes_bid"]
            hon += (p if down else -(1 - p)) - taker_fee(1, p)
        if n:
            print(f"  {label:<14} N={n:>4} DOWN%={downs/n*100:>5.1f} "
                  f"P&L={hon:>8.2f} /trade={hon/n:>+7.3f}")

    print("=== SELL YES>=0.85 honest fill, by day regime ===")
    sell85(up_days, "UP-lean days")
    sell85(down_days, "DOWN-lean days")
    sell85(set(day_n), "ALL days")
    print()

    # And the mirror: BUY YES<=0.15 by regime
    def buy15(days, label):
        n = ups = 0
        pnl = 0.0
        for t, snaps in windows.items():
            if t not in outcomes or win_day[t] not in days:
                continue
            s = first_cross(snaps, 0.0, 0.15, 10, 900, "kalshi_yes_ask")
            if s is None or s["kalshi_yes_ask"] is None or s["kalshi_yes_ask"] <= 0:
                continue
            n += 1
            up = outcomes[t] == 1
            ups += up
            ya = s["kalshi_yes_ask"]
            pnl += ((1 - ya) if up else -ya) - taker_fee(1, ya)
        if n:
            print(f"  {label:<14} N={n:>4} UP%={ups/n*100:>5.1f} "
                  f"P&L={pnl:>8.2f} /trade={pnl/n:>+7.3f}")

    print("=== BUY YES<=0.15 honest fill, by day regime ===")
    buy15(up_days, "UP-lean days")
    buy15(down_days, "DOWN-lean days")
    buy15(set(day_n), "ALL days")


if __name__ == "__main__":
    main()
