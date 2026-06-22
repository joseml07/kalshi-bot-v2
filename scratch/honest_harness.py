"""Honest backtest harness — see advisor plan.

Properties:
1. One trade per window, bucketed by ENTRY price (first snapshot crossing trigger).
2. Realistic fills: SELL YES fills at yes_bid; BUY YES fills at yes_ask. Real taker fee.
3. Clean outcome: window must have a snapshot with seconds_remaining <= 30.
4. Direction/price read only at entry time.
5. Calibration curve: realized P(UP) vs implied (mid) at a fixed observation time.
"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict

DB = "/root/kalshi-bot/kalshi-bot-v2/trades.db"
JUNE = "2026-06-01"


def taker_fee(contracts: int, price: float) -> float:
    return math.ceil(0.07 * contracts * price * (1 - price) * 100) / 100


def load_windows(since: str):
    """Return dict ticker -> list of snapshot rows sorted by seconds_remaining DESC."""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT ticker, symbol, seconds_remaining, price_change_pct,
                  kalshi_yes_ask, kalshi_yes_bid, kalshi_no_bid,
                  real_prob, yes_depth, no_depth, timestamp
           FROM window_snapshots WHERE timestamp >= ? ORDER BY ticker, seconds_remaining DESC""",
        (since,),
    ).fetchall()
    con.close()
    w = defaultdict(list)
    for r in rows:
        w[r["ticker"]].append(r)
    return w


def window_outcome(snaps):
    """UP=1/DOWN=0 from closest-to-expiry snapshot, requiring one <=30s. None if unclean."""
    near = [s for s in snaps if s["seconds_remaining"] <= 30]
    if not near:
        return None
    last = min(near, key=lambda s: s["seconds_remaining"])
    return 1 if last["price_change_pct"] > 0 else 0


def first_cross(snaps, lo, hi, min_time, max_time, field="kalshi_yes_ask"):
    """First snapshot (earliest = highest seconds_remaining) where lo <= field < hi and in time band."""
    for s in snaps:  # already sorted seconds_remaining DESC = chronological
        if not (min_time <= s["seconds_remaining"] <= max_time):
            continue
        v = s[field]
        if v is None:
            continue
        if lo <= v < hi:
            return s
    return None


def mid(s):
    a, b = s["kalshi_yes_ask"], s["kalshi_yes_bid"]
    if a is None or b is None:
        return a if b is None else b
    return (a + b) / 2.0


def main():
    windows = load_windows(JUNE)
    outcomes = {}
    for t, snaps in windows.items():
        o = window_outcome(snaps)
        if o is not None:
            outcomes[t] = o
    clean = {t: windows[t] for t in outcomes}
    print(f"June windows total={len(windows)} clean(w/ <=30s)={len(clean)} "
          f"UP_rate={sum(outcomes.values())/len(outcomes):.3f}")
    print()

    # ---- 1. SELL YES >= threshold, optimistic (ask) vs honest (bid) fill ----
    print("=== SELL YES (=buy NO), hold to expiry, June, one trade/window ===")
    print(f"{'thresh':>6} {'N':>5} {'DOWN%':>6} {'optP&L':>8} {'opt/t':>7} {'honP&L':>8} {'hon/t':>7} {'avgSprd':>7}")
    for thr in (0.80, 0.85, 0.90, 0.95):
        n = downs = 0
        opt = hon = 0.0
        spreads = []
        for t, snaps in clean.items():
            s = first_cross(snaps, thr, 1.0, 10, 900, "kalshi_yes_ask")
            if s is None:
                continue
            yb, ya = s["kalshi_yes_bid"], s["kalshi_yes_ask"]
            if yb is None:
                continue
            n += 1
            down = outcomes[t] == 0
            downs += down
            spreads.append(ya - yb)
            # optimistic: sell at ask
            p_opt = ya
            opt += (p_opt if down else -(1 - p_opt)) - taker_fee(1, p_opt)
            # honest: sell at bid
            p_hon = yb
            hon += (p_hon if down else -(1 - p_hon)) - taker_fee(1, p_hon)
        if n:
            print(f"{thr:>6.2f} {n:>5} {downs/n*100:>5.1f}% {opt:>8.2f} {opt/n:>7.3f} "
                  f"{hon:>8.2f} {hon/n:>7.3f} {sum(spreads)/len(spreads):>7.3f}")
    print()

    # ---- 2. BUY YES <= threshold (longshot YES), honest fill at ask ----
    print("=== BUY YES <= threshold (cheap YES), hold to expiry, honest fill at ask ===")
    print(f"{'thresh':>6} {'N':>5} {'UP%':>6} {'P&L':>8} {'/trade':>7}")
    for thr in (0.05, 0.10, 0.15, 0.20):
        n = ups = 0
        pnl = 0.0
        for t, snaps in clean.items():
            s = first_cross(snaps, 0.0, thr, 10, 900, "kalshi_yes_ask")
            if s is None:
                continue
            ya = s["kalshi_yes_ask"]
            if ya is None or ya <= 0:
                continue
            n += 1
            up = outcomes[t] == 1
            ups += up
            pnl += ((1 - ya) if up else -ya) - taker_fee(1, ya)
        if n:
            print(f"{thr:>6.2f} {n:>5} {ups/n*100:>5.1f}% {pnl:>8.2f} {pnl/n:>7.3f}")
    print()

    # ---- 3. Calibration curve at a fixed observation time (T~=120s) ----
    for obs in (300, 120, 60):
        print(f"=== Calibration @ T~{obs}s (first snap <= {obs}s), bucket by MID ===")
        print(f"{'mid bin':>10} {'N':>5} {'implied':>8} {'realUP':>7} {'diff':>7}")
        buckets = defaultdict(lambda: [0, 0, 0.0])  # n, ups, implied_sum
        for t, snaps in clean.items():
            cand = [s for s in snaps if s["seconds_remaining"] <= obs]
            if not cand:
                continue
            s = max(cand, key=lambda x: x["seconds_remaining"])
            m = mid(s)
            if m is None:
                continue
            b = int(m * 10)  # 0..9
            b = min(b, 9)
            buckets[b][0] += 1
            buckets[b][1] += outcomes[t]
            buckets[b][2] += m
        for b in sorted(buckets):
            n, ups, isum = buckets[b]
            if n < 20:
                continue
            imp = isum / n
            real = ups / n
            print(f"{b*10:>3}-{b*10+10:>2}c {n:>10} {imp:>8.3f} {real:>7.3f} {real-imp:>+7.3f}")
        print()


if __name__ == "__main__":
    main()
