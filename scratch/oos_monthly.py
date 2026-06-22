"""Out-of-sample regime test: run model-divergence per half-month period.

The decisive question: does fading-the-market-when-model-disagrees survive across
DIFFERENT regimes (incl. the May 23 break and any up-trending stretch)?
Compare to blind SELL>=0.85 in each period.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
import math

DB = "/root/kalshi-bot/kalshi-bot-v2/trades.db"


def taker_fee(c, p):
    return math.ceil(0.07 * c * p * (1 - p) * 100) / 100


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


def outcome(snaps):
    near = [s for s in snaps if s["seconds_remaining"] <= 30]
    if not near:
        return None
    return 1 if min(near, key=lambda s: s["seconds_remaining"])["price_change_pct"] > 0 else 0


def entry_snap(snaps, T):
    cand = [s for s in snaps if s["seconds_remaining"] <= T and s["kalshi_yes_bid"] is not None]
    return max(cand, key=lambda x: x["seconds_remaining"]) if cand else None


def run(since, until, thr=0.15, T=300):
    w = load(since, until)
    out = {t: o for t, s in w.items() if (o := outcome(s)) is not None}
    if not out:
        return None
    up_rate = sum(out.values()) / len(out)
    md = defaultdict(lambda: [0, 0, 0.0])  # side -> n, wins, pnl
    blind = [0, 0, 0.0]
    for t, snaps in w.items():
        if t not in out:
            continue
        s = entry_snap(snaps, T)
        if s is None or s["kalshi_yes_ask"] is None:
            continue
        yb, ya, rp = s["kalshi_yes_bid"], s["kalshi_yes_ask"], s["real_prob"]
        edge_sell = (1 - rp) - (1 - yb)
        edge_buy = rp - ya
        if edge_sell >= thr and edge_sell >= edge_buy:
            down = out[t] == 0
            md["SELL"][0]+=1; md["SELL"][1]+=down
            md["SELL"][2]+=(yb if down else -(1-yb)) - taker_fee(1, yb)
        elif edge_buy >= thr:
            up = out[t] == 1
            md["BUY"][0]+=1; md["BUY"][1]+=up
            md["BUY"][2]+=((1-ya) if up else -ya) - taker_fee(1, ya)
        # blind SELL>=0.85 (first cross, honest)
        for ss in snaps:
            if 10 <= ss["seconds_remaining"] <= 900 and ss["kalshi_yes_ask"] \
               and ss["kalshi_yes_ask"] >= 0.85 and ss["kalshi_yes_bid"]:
                down = out[t] == 0; p = ss["kalshi_yes_bid"]
                blind[0]+=1; blind[1]+=down
                blind[2]+=(p if down else -(1-p)) - taker_fee(1, p)
                break
    return up_rate, len(out), md, blind


PERIODS = [
    ("2026-04-15", "2026-05-01", "Apr 15-30"),
    ("2026-05-01", "2026-05-15", "May 1-14"),
    ("2026-05-15", "2026-05-23", "May 15-22 (pre-break)"),
    ("2026-05-23", "2026-06-01", "May 23-31 (post-break)"),
    ("2026-06-01", "2026-06-10", "Jun 1-9"),
    ("2026-06-10", "2026-06-23", "Jun 10-22"),
]

print(f"{'period':<24} {'UPrate':>6} {'win':>5} | "
      f"{'MD-SELL n/t':>14} {'MD-BUY n/t':>14} {'blindSELL85 n/t':>16}")
for since, until, label in PERIODS:
    r = run(since, until)
    if r is None:
        continue
    up_rate, nw, md, blind = r
    sn, _, sp = md["SELL"]; bn, _, bp = md["BUY"]
    bln, _, blp = blind
    def fmt(n, p): return f"{n:>4} {p/n:>+6.3f}" if n else "   -      "
    print(f"{label:<24} {up_rate:>6.3f} {nw:>5} | "
          f"{fmt(sn, sp):>14} {fmt(bn, bp):>14} {fmt(bln, blp):>16}")
