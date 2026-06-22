"""Two forward-tradeable angles:

A) Cross-window persistence (same symbol): does prior window direction predict
   the next, beyond what the Kalshi price reflects? -> adaptive regime-following.

B) Model calibration: is real_prob better calibrated than the market price in June?
   If real_prob - market predicts outcome cleanly, the 'model edge died' claim is wrong.

C) Adaptive day-regime strategy: observe first K windows of a symbol-day, then bet
   the running-majority direction on the rest. Honest fills. Beats blind NO?
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict

from honest_harness import (DB, JUNE, first_cross, load_windows, taker_fee,
                            window_outcome)


def parse_dt(ticker):
    # KXBTC15M-26JUN211915-15 -> sort key by the date+time chunk
    return ticker.split("-")[1]


def main():
    windows = load_windows(JUNE)
    out, ts0 = {}, {}
    for t, snaps in windows.items():
        o = window_outcome(snaps)
        if o is None:
            continue
        out[t] = o
        ts0[t] = snaps[0]["timestamp"]

    # ---- A. Cross-window persistence per symbol ----
    print("=== A. Same-symbol window-to-window persistence (June) ===")
    for sym in ("BTC", "ETH"):
        seq = sorted([t for t in out if t.startswith(f"KX{sym}")],
                     key=lambda t: ts0[t])
        trans = defaultdict(lambda: [0, 0])  # prev_outcome -> [n, ups]
        s2 = defaultdict(lambda: [0, 0])     # last2 same -> next
        for i in range(1, len(seq)):
            prev, cur = out[seq[i-1]], out[seq[i]]
            trans[prev][0] += 1
            trans[prev][1] += cur
            if i >= 2 and out[seq[i-2]] == prev:  # two in a row same
                s2[prev][0] += 1
                s2[prev][1] += cur
        base = sum(out[t] for t in seq) / len(seq)
        print(f" {sym}: base UP={base:.3f} n={len(seq)}")
        for p in (0, 1):
            n, u = trans[p]
            if n:
                print(f"   prev={'UP' if p else 'DOWN':<4} -> next UP={u/n:.3f} (n={n})")
        for p in (0, 1):
            n, u = s2[p]
            if n:
                print(f"   2x {'UP' if p else 'DOWN':<4}  -> next UP={u/n:.3f} (n={n})")
    print()

    # ---- B. Model vs market calibration at T~300s ----
    print("=== B. Model (real_prob) vs market(mid) calibration @ T~300s ===")
    print(f"{'pred bin':>9} | {'MARKET realUP':>14} (n) | {'MODEL realUP':>13} (n)")
    mkt = defaultdict(lambda: [0, 0])
    mdl = defaultdict(lambda: [0, 0])
    for t, snaps in windows.items():
        if t not in out:
            continue
        cand = [s for s in snaps if s["seconds_remaining"] <= 300]
        if not cand:
            continue
        s = max(cand, key=lambda x: x["seconds_remaining"])
        a, b = s["kalshi_yes_ask"], s["kalshi_yes_bid"]
        if a is not None and b is not None:
            m = (a + b) / 2
            mkt[min(int(m*10), 9)][0] += 1
            mkt[min(int(m*10), 9)][1] += out[t]
        rp = s["real_prob"]
        mdl[min(int(rp*10), 9)][0] += 1
        mdl[min(int(rp*10), 9)][1] += out[t]
    for bb in range(10):
        mn, mu = mkt[bb]
        dn, du = mdl[bb]
        ms = f"{mu/mn:.3f} ({mn})" if mn >= 20 else "  -"
        ds = f"{du/dn:.3f} ({dn})" if dn >= 20 else "  -"
        print(f"  {bb*10:>2}-{bb*10+10:<3} | {ms:>14} | {ds:>13}")
    print("  (well-calibrated = realUP matches bin midpoint)")
    print()

    # ---- C. Adaptive day-regime: observe first K, bet running majority ----
    print("=== C. Adaptive day-regime strategy (honest fills, entry @ first snap<=300s) ===")
    # group by (symbol, day)
    def keyfn(t):
        return (t[:7], ts0[t][:10])
    groups = defaultdict(list)
    for t in out:
        groups[keyfn(t)].append(t)
    for K in (3, 4, 5):
        n = wins = 0
        pnl = 0.0
        flat = 0
        for g, ts in groups.items():
            ts = sorted(ts, key=lambda t: ts0[t])
            up = dn = 0
            for i, t in enumerate(ts):
                if i < K:  # observation phase
                    up += out[t]; dn += (1 - out[t]); continue
                lean = up - dn
                if lean == 0:
                    flat += 1
                    up += out[t]; dn += (1 - out[t]); continue
                snaps = windows[t]
                s = first_cross(snaps, 0.0, 1.0, 280, 320, "kalshi_yes_ask") \
                    or first_cross(snaps, 0.0, 1.0, 200, 400, "kalshi_yes_ask")
                if s and s["kalshi_yes_ask"] and s["kalshi_yes_bid"]:
                    ya, yb = s["kalshi_yes_ask"], s["kalshi_yes_bid"]
                    if lean < 0:  # bet DOWN: sell YES at bid
                        p = yb; down = out[t] == 0
                        pnl += (p if down else -(1-p)) - taker_fee(1, p)
                        wins += down
                    else:  # bet UP: buy YES at ask
                        up_w = out[t] == 1
                        pnl += ((1-ya) if up_w else -ya) - taker_fee(1, ya)
                        wins += up_w
                    n += 1
                up += out[t]; dn += (1 - out[t])
        if n:
            print(f"  K={K}: N={n:>4} WR={wins/n*100:>5.1f}% P&L={pnl:>8.2f} "
                  f"/trade={pnl/n:>+7.3f}  (flat skipped={flat})")


if __name__ == "__main__":
    main()
