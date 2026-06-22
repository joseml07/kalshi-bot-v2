"""Model-divergence strategy, honest fills, two-sided, regime-split.

At entry (first snap <= ENTRY_T seconds remaining):
  edge_sell = (1-real_prob) - no_cost  where no_cost = 1 - yes_bid  (sell YES @ bid)
  edge_buy  = real_prob - yes_ask                                   (buy YES @ ask)
Take whichever passes threshold; one trade/window; hold to expiry; net taker fee.

Split by up/down-leaning days to test regime robustness vs the blind-SELL drift bet.
"""
from __future__ import annotations

from collections import defaultdict

from honest_harness import load_windows, taker_fee, window_outcome, JUNE


def main():
    windows = load_windows(JUNE)
    out, ts0 = {}, {}
    day_up, day_n = defaultdict(int), defaultdict(int)
    for t, snaps in windows.items():
        o = window_outcome(snaps)
        if o is None:
            continue
        out[t] = o
        ts0[t] = snaps[0]["timestamp"]
        d = snaps[0]["timestamp"][:10]
        day_up[d] += o
        day_n[d] += 1
    up_days = {d for d in day_n if day_up[d]/day_n[d] >= 0.45}

    def entry_snap(snaps, T):
        cand = [s for s in snaps if s["seconds_remaining"] <= T and s["kalshi_yes_bid"] is not None]
        return max(cand, key=lambda x: x["seconds_remaining"]) if cand else None

    for ENTRY_T in (300, 120):
        print(f"=== Model-divergence @ T<={ENTRY_T}s, honest fills ===")
        print(f"{'thr':>5} {'side':>5} {'N':>5} {'WR':>6} {'P&L':>8} {'/trade':>7} | "
              f"{'down-day/t':>10} {'up-day/t':>9}")
        for thr in (0.05, 0.10, 0.15):
            agg = defaultdict(lambda: [0, 0, 0.0])  # key -> n, wins, pnl
            for t, snaps in windows.items():
                if t not in out:
                    continue
                s = entry_snap(snaps, ENTRY_T)
                if s is None:
                    continue
                yb, ya, rp = s["kalshi_yes_bid"], s["kalshi_yes_ask"], s["real_prob"]
                if ya is None:
                    continue
                no_cost = 1 - yb
                edge_sell = (1 - rp) - no_cost
                edge_buy = rp - ya
                regime = "up" if ts0[t][:10] in up_days else "down"
                if edge_sell >= thr and edge_sell >= edge_buy:
                    down = out[t] == 0
                    p = yb
                    pnl = (p if down else -(1-p)) - taker_fee(1, p)
                    for k in ("SELL", f"SELL_{regime}"):
                        agg[k][0]+=1; agg[k][1]+=down; agg[k][2]+=pnl
                elif edge_buy >= thr:
                    up = out[t] == 1
                    pnl = ((1-ya) if up else -ya) - taker_fee(1, ya)
                    for k in ("BUY", f"BUY_{regime}"):
                        agg[k][0]+=1; agg[k][1]+=up; agg[k][2]+=pnl
            for side in ("SELL", "BUY"):
                n, w, p = agg[side]
                dn, dw, dp = agg[f"{side}_down"]
                un, uw, up = agg[f"{side}_up"]
                if n:
                    print(f"{thr:>5.2f} {side:>5} {n:>5} {w/n*100:>5.1f}% {p:>8.2f} "
                          f"{p/n:>+7.3f} | {(dp/dn if dn else 0):>+10.3f} "
                          f"{(up/un if un else 0):>+9.3f}")
        print()


if __name__ == "__main__":
    main()
