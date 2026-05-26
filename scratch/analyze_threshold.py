import sqlite3
from datetime import datetime

db_path = '/root/kalshi-bot/kalshi-bot-v2/trades.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Query trades executed on 2026-05-26 after 06:18:00 UTC
query = """
SELECT timestamp, ticker, strategy, side, contracts, price, edge, net_edge, pnl, exit_reason
FROM trades
WHERE timestamp >= '2026-05-26T06:18:00'
ORDER BY timestamp ASC;
"""
cursor.execute(query)
trades = cursor.fetchall()

print(f"Total trades since 06:18:00 UTC: {len(trades)}")
print("-" * 80)

def analyze_subset(filtered_trades, label):
    total_trades = len(filtered_trades)
    if total_trades == 0:
        print(f"\n{label}: No trades found.")
        return
    
    total_pnl = 0.0
    wins = 0
    losses = 0
    ties = 0
    
    for t in filtered_trades:
        pnl_val = float(t[8]) if t[8] is not None else 0.0
        total_pnl += pnl_val
        if pnl_val > 0:
            wins += 1
        elif pnl_val < 0:
            losses += 1
        else:
            ties += 1
            
    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
    print(f"\n{label}:")
    print(f"  Count: {total_trades}")
    print(f"  PnL: ${total_pnl:.2f}")
    print(f"  Win Rate: {win_rate:.1f}% ({wins} W, {losses} L, {ties} T)")

# 1. Actual trades (which was run with EDGE_THRESHOLD=0.04)
analyze_subset(trades, "Actual (EDGE_THRESHOLD=0.04)")

# 2. Trades filtering by net_edge >= 0.08
net_edge_08_trades = [t for t in trades if float(t[7]) >= 0.08]
analyze_subset(net_edge_08_trades, "Filtered (net_edge >= 0.08)")

# 3. Trades filtering by edge >= 0.08
edge_08_trades = [t for t in trades if float(t[6]) >= 0.08]
analyze_subset(edge_08_trades, "Filtered (edge >= 0.08)")

# Print detailed list of actual trades and which ones would be filtered out
print("\n" + "="*80)
print(f"{'Timestamp':<25} | {'Ticker':<22} | {'Strategy':<14} | {'Net Edge':<8} | {'PnL':<6} | {'Exit':<10}")
print("="*80)
for t in trades:
    ts, ticker, strat, side, contracts, price, edge, net_edge, pnl, exit_reason = t
    net_edge_val = float(net_edge)
    pnl_val = float(pnl) if pnl is not None else 0.0
    status = "KEEP" if net_edge_val >= 0.08 else "FILTER"
    print(f"{ts[11:19]:<25} | {ticker:<22} | {strat:<14} | {net_edge_val:.4f} | {pnl_val:>6.2f} | {exit_reason:<10} | {status}")

conn.close()
