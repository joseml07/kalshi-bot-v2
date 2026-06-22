"""Validate the price_change_pct-sign proxy against Kalshi's ACTUAL settlement
(market_events.result), then re-run model-divergence on TRUE labels, split BUY/SELL.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
import math

DB = "/root/kalshi-bot/kalshi-bot-v2/trades.db"


def taker_fee(c, p):
    return math.ceil(0.07 * c * p * (1 - p) * 100) / 100


def true_results():
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT ticker, result FROM market_events WHERE event_type='close' AND result IN ('up','down')"
    ).fetchall()
    con.close()
    return {t: (1 if r == "up" else 0) for t, r in rows}


def load(since, until):
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT ticker, seconds_remaining, price_change_pct, kalshi_yes_ask,
                  kalshi_yes_bid, real_prob, timestamp FROM window_snapshots
           WHERE timestamp>=? AND timestamp<? ORDER BY ticker, seconds_remaining DESC""",
        (since, until)).fetchall()
    con.close()
    w = defaultdict(list)
    for r in rows:
        w[r["ticker"]].append(r)
    return w


def proxy_outcome(snaps):
    near = [s for s in snaps if s["seconds_remaining"] <= 30]
    if not near:
        return None
    return 1 if min(near, key=lambda s: s["seconds_remaining"])["price_change_pct"] > 0 else 0


def entry_snap(snaps, T):
    cand = [s for s in snaps if s["seconds_remaining"] <= T and s["kalshi_yes_bid"] is not None]
    return max(cand, key=lambda x: x["seconds_remaining"]) if cand else None


truth = true_results()
print(f"market_events settled windows: {len(truth)}  UP_rate={sum(truth.values())/len(truth):.3f}")

# ---- Check 1: proxy vs truth agreement ----
w_all = load("2026-04-15", "2026-06-23")
agree = tot = 0
disagree_coinflip = cf_tot = 0
for t, snaps in w_all.items():
    if t not in truth:
        continue
    px = proxy_outcome(snaps)
    if px is None:
        continue
    tot += 1
    agree += (px == truth[t])
    # near-coinflip: where mid ~0.4-0.6 at T~120
    cand = [s for s in snaps if s["seconds_remaining"] <= 120 and s["kalshi_yes_bid"] is not None]
    if cand:
        s = max(cand, key=lambda x: x["seconds_remaining"])
        m = (s["kalshi_yes_ask"] + s["kalshi_yes_bid"]) / 2
        if 0.35 <= m <= 0.65:
            cf_tot += 1
            disagree_coinflip += (px != truth[t])
print(f"proxy vs TRUE settlement: agree {agree}/{tot} = {agree/tot*100:.2f}%")
print(f"  near-coinflip windows: disagree {disagree_coinflip}/{cf_tot} = "
      f"{(disagree_coinflip/cf_tot*100 if cf_tot else 0):.1f}%")
print()

# ---- Check 2: model-divergence on TRUE labels, split BUY/SELL, per period ----
def run(since, until, labels, thr=0.12, T=300):
    w = load(since, until)
    agg = defaultdict(lambda: [0, 0, 0.0])
    for t, snaps in w.items():
        if t not in labels:
            continue
        s = entry_snap(snaps, T)
        if s is None or s["kalshi_yes_ask"] is None:
            continue
        yb, ya, rp = s["kalshi_yes_bid"], s["kalshi_yes_ask"], s["real_prob"]
        es = (1 - rp) - (1 - yb); eb = rp - ya
        o = labels[t]
        if es >= thr and es >= eb:
            down = o == 0; agg["SELL"][0]+=1; agg["SELL"][1]+=down
            agg["SELL"][2]+=(yb if down else -(1-yb))-taker_fee(1, yb)
        elif eb >= thr:
            up = o == 1; agg["BUY"][0]+=1; agg["BUY"][1]+=up
            agg["BUY"][2]+=((1-ya) if up else -ya)-taker_fee(1, ya)
    return agg

PERIODS = [
    ("2026-04-15", "2026-05-01", "Apr15-30"),
    ("2026-05-01", "2026-05-23", "May1-22"),
    ("2026-05-23", "2026-06-01", "May23-31"),
    ("2026-06-01", "2026-06-23", "Jun1-22"),
]
print("=== Model-divergence on TRUE Kalshi settlement labels (thr=0.12,T-300,honest) ===")
print(f"{'period':<10} | {'SELL n  WR   /trade':>22} | {'BUY  n  WR   /trade':>22}")
for s, u, lab in PERIODS:
    a = run(s, u, truth)
    sn, sw, sp = a["SELL"]; bn, bw, bp = a["BUY"]
    ss = f"{sn:>4} {sw/sn*100:>4.0f}% {sp/sn:>+6.3f}" if sn else "  -"
    bs = f"{bn:>4} {bw/bn*100:>4.0f}% {bp/bn:>+6.3f}" if bn else "  -"
    print(f"{lab:<10} | {ss:>22} | {bs:>22}")
