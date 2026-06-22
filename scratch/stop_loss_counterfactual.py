"""Counterfactual stop-loss analysis on CLEAN post-fix data (read-only).

For EVERY entered position since the May-23 crossed-book fix (winners + losers),
simulate: "exit at the first recorded bid <= entry - D, else hold to actual exit."
Compare total PnL under each stop threshold D to what actually happened.

Zero look-ahead: the stop is priced at the bid that actually existed at that
moment; settlement only prices the 'hold' alternative (the actual recorded pnl).
No entry selection: we evaluate an exit overlay on positions actually taken.

If no D beats actual net PnL, stop-loss is dead -- on clean data this time.
"""
import sqlite3

DB = "file:/root/kalshi-bot/kalshi-bot-v2/trades.db?mode=ro"
CUTOFF = "2026-05-24"  # post crossed-book fix (60dee18, May 23)

conn = sqlite3.connect(DB, uri=True)
conn.row_factory = sqlite3.Row

# All entered positions post-fix that have a known outcome (pnl not null).
positions = conn.execute(
    """
    SELECT id, timestamp, ticker, side, contracts,
           CAST(price AS REAL) AS entry, CAST(pnl AS REAL) AS pnl_actual,
           exit_reason,
           CASE WHEN order_id LIKE 'PAPER-%' THEN 'paper' ELSE 'live' END AS mode
    FROM trades
    WHERE timestamp >= ? AND pnl IS NOT NULL
    ORDER BY id
    """,
    (CUTOFF,),
).fetchall()

print(f"Post-fix positions with outcomes: {len(positions)}")

# Stop thresholds: exit when exit-side bid first falls to <= entry - D (loss of D/contract)
DROPS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]

# Pre-load bid trajectory per position (first executable bid <= entry-D after entry).
def first_stop_bid(ticker, side, entry, drop, entry_ts):
    bidcol = "best_yes_bid" if side == "yes" else "best_no_bid"
    row = conn.execute(
        f"""
        SELECT {bidcol} AS bid FROM orderbook_snapshots
        WHERE ticker = ? AND timestamp > ?
          AND {bidcol} IS NOT NULL AND {bidcol} != ''
          AND CAST({bidcol} AS REAL) > 0
          AND CAST({bidcol} AS REAL) <= ?
        ORDER BY timestamp LIMIT 1
        """,  # noqa: S608
        (ticker, entry_ts, entry - drop),
    ).fetchone()
    return float(row["bid"]) if row else None


actual_total = sum(p["pnl_actual"] for p in positions)
print(f"Actual total PnL (hold, as happened): ${actual_total:.2f}\n")
print(f"{'StopΔ(D)':>9} {'triggered':>9} {'of which':>18} {'policyPnL':>10} {'vs actual':>10}")
print(f"{'':>9} {'':>9} {'wins cut / losses cut':>21}")

for d in DROPS:
    policy_total = 0.0
    triggered = 0
    wins_cut = 0   # positions that actually WON but the stop would have exited at a loss
    losses_cut = 0 # positions that actually LOST and the stop exits earlier
    saved = 0.0
    for p in positions:
        stop_bid = first_stop_bid(p["ticker"], p["side"], p["entry"], d, p["timestamp"])
        if stop_bid is None:
            # stop never triggers -> identical to actual
            policy_total += p["pnl_actual"]
            continue
        triggered += 1
        # PnL if we had exited at stop_bid (ignore fees: second-order, same taker model)
        pnl_stop = (stop_bid - p["entry"]) * p["contracts"]
        policy_total += pnl_stop
        if p["pnl_actual"] > 0:
            wins_cut += 1
        else:
            losses_cut += 1
        saved += pnl_stop - p["pnl_actual"]
    delta = policy_total - actual_total
    print(f"{d:>9.2f} {triggered:>9} {wins_cut:>8} / {losses_cut:<10} "
          f"${policy_total:>8.2f} {delta:>+9.2f}")

print("\nInterpretation: a POSITIVE 'vs actual' means the stop-loss would have")
print("improved net PnL on clean post-fix data. Negative means it cuts winners")
print("(that recover) more than it saves on losers -> stop-loss stays dead.")

# ---- Mirror test: TAKE-PROFIT (exit at first bid >= entry + G) ----
def first_tp_bid(ticker, side, entry, gain, entry_ts):
    bidcol = "best_yes_bid" if side == "yes" else "best_no_bid"
    row = conn.execute(
        f"""
        SELECT {bidcol} AS bid FROM orderbook_snapshots
        WHERE ticker = ? AND timestamp > ?
          AND {bidcol} IS NOT NULL AND {bidcol} != ''
          AND CAST({bidcol} AS REAL) >= ?
        ORDER BY timestamp LIMIT 1
        """,  # noqa: S608
        (ticker, entry_ts, entry + gain),
    ).fetchone()
    return float(row["bid"]) if row else None

print(f"\n{'TakeP(G)':>9} {'triggered':>9} {'of which':>18} {'policyPnL':>10} {'vs actual':>10}")
print(f"{'':>9} {'':>9} {'wins lock/ losses lock':>21}")
GAINS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
for g in GAINS:
    policy_total = 0.0
    triggered = wlock = llock = 0
    for p in positions:
        tp_bid = first_tp_bid(p["ticker"], p["side"], p["entry"], g, p["timestamp"])
        if tp_bid is None:
            policy_total += p["pnl_actual"]
            continue
        triggered += 1
        policy_total += (tp_bid - p["entry"]) * p["contracts"]
        if p["pnl_actual"] > 0:
            wlock += 1
        else:
            llock += 1
    delta = policy_total - actual_total
    print(f"{g:>9.2f} {triggered:>9} {wlock:>8} / {llock:<10} "
          f"${policy_total:>8.2f} {delta:>+9.2f}")
print("\nTake-profit POSITIVE vs actual = locking gains early beats holding.")
print("If both stop-loss and take-profit are negative, the settlement leak is")
print("intrinsic variance, not a fixable exit bug.")
conn.close()
