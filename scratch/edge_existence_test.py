"""Edge-existence test — is there ANY exploitable edge, after fees, on clean data?

The question that gates all v3 work: does momentum / OBI / the model predict the
15-min binary outcome with enough edge to beat Kalshi's fees? Answered WITHOUT a
backtester and WITHOUT look-ahead.

Design (the clean inverse of the flex-engine sin):
  * One decision point per window = the first snapshot in the entry zone
    (91-480s remaining), exactly where the bot decides.
  * Features (momentum_60s, OBI, price, model real_prob) are read AT that point.
  * Outcome = the ACTUAL settlement result (up/down), which happens strictly later.
  * Feature precedes outcome => no look-ahead. No simulated fills => no fill fantasy.

For each candidate signal we compute, per contract, normalized to a $1 stake:
    gross_edge = realized_win_rate - avg_entry_price        (EV before fees)
    net_edge   = gross_edge - avg_entry_fee                 (EV after the MINIMUM fee)
A signal has real, exploitable edge only if net_edge > 0 AND gross_edge exceeds
~2 standard errors (i.e. it's distinguishable from zero given the sample).

If nothing clears that bar, this signal family is dead on this instrument and v3
should change the instrument/role, not just the strategy. If something does, that's
a lead worth building the real (clean-data, realistic-fill) backtester to confirm.
"""
from __future__ import annotations

import math
import sqlite3

DB = "file:/root/kalshi-bot/kalshi-bot-v2/trades.db?mode=ro"
CUTOFF = "2026-05-24"          # post crossed-book fix => clean books
ENTRY_MIN, ENTRY_MAX = 91, 480  # the bot's entry zone (seconds remaining)


def taker_fee(price: float) -> float:
    """Kalshi taker fee per 1 contract (matches strategy/fees.py)."""
    return math.ceil(0.07 * price * (1.0 - price) * 100) / 100


def load_decisions() -> list[dict]:
    conn = sqlite3.connect(DB, uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT ticker, symbol, seconds_remaining, momentum_60s, price_change_pct,
               kalshi_yes_ask, kalshi_yes_bid, kalshi_no_bid, real_prob,
               yes_depth, no_depth, result
        FROM (
            SELECT ws.*, me.result,
                   ROW_NUMBER() OVER (PARTITION BY ws.ticker
                                      ORDER BY ws.seconds_remaining DESC) rn
            FROM window_snapshots ws
            JOIN market_events me
              ON ws.ticker = me.ticker AND me.event_type = 'close'
            WHERE ws.timestamp >= ?
              AND ws.seconds_remaining BETWEEN ? AND ?
              AND ws.momentum_60s IS NOT NULL
              AND ws.kalshi_yes_ask IS NOT NULL
              AND ws.kalshi_yes_bid IS NOT NULL
              AND me.result IN ('up','down')
        ) WHERE rn = 1
        """,
        (CUTOFF, ENTRY_MIN, ENTRY_MAX),
    ).fetchall()
    conn.close()

    out = []
    for r in rows:
        yes_ask = r["kalshi_yes_ask"]
        yes_bid = r["kalshi_yes_bid"]
        no_bid = r["kalshi_no_bid"] if r["kalshi_no_bid"] is not None else (1.0 - yes_ask)
        # discard the rare still-crossed book
        if yes_bid + no_bid > 1.02:
            continue
        yd, nd = r["yes_depth"], r["no_depth"]
        obi = (yd - nd) / (yd + nd) if (yd + nd) > 0 else 0.0
        out.append({
            "symbol": r["symbol"],
            "mom": r["momentum_60s"],
            "pcp": r["price_change_pct"],
            "yes_ask": yes_ask,
            "no_ask": 1.0 - yes_bid,   # buying NO lifts (1 - best yes bid)
            "real_prob": r["real_prob"],
            "obi": obi,
            "up": 1 if r["result"] == "up" else 0,
        })
    return out


def evaluate(rows: list[dict], label: str, pick) -> None:
    """pick(row) -> 'yes' | 'no' | None. Prints the edge decomposition."""
    n = wins = 0
    price_sum = fee_sum = 0.0
    for row in rows:
        side = pick(row)
        if side is None:
            continue
        price = row["yes_ask"] if side == "yes" else row["no_ask"]
        if not (0.05 < price < 0.95):      # ignore degenerate/settled books
            continue
        won = (side == "yes" and row["up"] == 1) or (side == "no" and row["up"] == 0)
        n += 1
        wins += won
        price_sum += price
        fee_sum += taker_fee(price)
    if n < 15:
        print(f"  {label:34s}  n={n:<4d} (too few to judge)")
        return
    wr = wins / n
    avg_price = price_sum / n
    avg_fee = fee_sum / n
    gross = wr - avg_price            # EV/contract before fees
    net = gross - avg_fee             # EV/contract after minimum (entry) fee
    se = math.sqrt(wr * (1 - wr) / n)  # std error of the win rate
    z = gross / se if se > 0 else 0.0
    flag = "  <-- EDGE?" if (net > 0 and abs(z) > 2) else ""
    print(f"  {label:34s}  n={n:<4d} WR={wr*100:4.1f}%  "
          f"price={avg_price:.3f}  gross={gross:+.3f}  fee={avg_fee:.3f}  "
          f"net={net:+.3f}  (z={z:+.1f}){flag}")


def main() -> None:
    rows = load_decisions()
    print(f"Clean post-fix decision points (one per window): {len(rows)}")
    base_up = sum(r["up"] for r in rows) / max(len(rows), 1)
    print(f"Base rate P(up) across all windows: {base_up*100:.1f}%\n")

    # --- 1. Is the MARKET itself efficient? (price vs realized up-rate) ---
    print("1. MARKET CALIBRATION  (entry YES price decile -> realized P(up))")
    print("   If realized ~= price, the market is efficient and there's no naive mispricing.")
    buckets: dict[int, list[int]] = {}
    for r in rows:
        b = int(r["yes_ask"] * 10)
        buckets.setdefault(b, []).append(r["up"])
    for b in sorted(buckets):
        ups = buckets[b]
        if len(ups) < 10:
            continue
        print(f"   price {b/10:.1f}-{b/10+0.1:.1f}:  n={len(ups):<4d} "
              f"realized P(up)={sum(ups)/len(ups)*100:4.1f}%")

    # --- 2. Do our SIGNALS have edge after fees? ---
    print("\n2. SIGNAL EDGE  (gross = WR - price ; net = gross - fee ; need net>0 & |z|>2)")

    def momentum(thr):
        def f(r):
            if r["mom"] > thr:
                return "yes"
            if r["mom"] < -thr:
                return "no"
            return None
        return f

    def mom_and_obi(r):  # the bot's dual gate: OBI agrees with momentum
        if r["mom"] > 0 and r["obi"] > 0:
            return "yes"
        if r["mom"] < 0 and r["obi"] < 0:
            return "no"
        return None

    def mom_obi_priced(r):  # dual gate + the bot's price band 0.25-0.65
        side = mom_and_obi(r)
        if side is None:
            return None
        price = r["yes_ask"] if side == "yes" else r["no_ask"]
        return side if 0.25 <= price <= 0.65 else None

    def model_edge(r):  # the dynamic-k model's own call (real_prob vs price), edge>=0.04
        if r["real_prob"] - r["yes_ask"] >= 0.04:
            return "yes"
        if (1 - r["real_prob"]) - r["no_ask"] >= 0.04:
            return "no"
        return None

    evaluate(rows, "momentum sign (any)", momentum(0.0))
    evaluate(rows, "momentum > 0.0005", momentum(0.0005))
    evaluate(rows, "momentum > 0.001", momentum(0.001))
    evaluate(rows, "momentum + OBI agree", mom_and_obi)
    evaluate(rows, "mom + OBI + price 0.25-0.65", mom_obi_priced)
    evaluate(rows, "model real_prob edge >= 0.04", model_edge)

    print("\nVERDICT: an 'EDGE?' flag = net>0 and gross is >2 SE from zero -> worth a")
    print("real backtest. No flags = no exploitable edge in this signal family on this")
    print("instrument; v3 should change the instrument or role, not just the strategy.")


if __name__ == "__main__":
    main()
