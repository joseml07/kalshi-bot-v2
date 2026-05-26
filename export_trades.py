import sqlite3
import csv
import re
import os

def export_trades():
    db_path = 'trades.db'
    output_path = 'trades_summary.csv'
    
    if not os.path.exists(db_path):
        print(f"Error: {db_path} not found.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get all trades
    cursor.execute("SELECT * FROM trades")
    trades = cursor.fetchall()
    
    # Get column names for trades
    cursor.execute("PRAGMA table_info(trades)")
    trade_cols = [col[1] for col in cursor.fetchall()]
    
    # Create a mapping of order_id to signal info
    cursor.execute("SELECT reason, real_prob, seconds_remaining FROM signals WHERE reason LIKE 'order_id=%'")
    signals_data = cursor.fetchall()
    
    signal_map = {}
    for reason, prob, secs in signals_data:
        match = re.search(r'order_id=([^, ]+)', reason)
        if match:
            order_id = match.group(1)
            signal_map[order_id] = {'real_prob': prob, 'seconds_remaining': secs, 'reason': reason}

    # Prepare data for CSV
    csv_data = []
    headers = trade_cols + ['real_prob', 'seconds_remaining', 'signal_reason']
    
    for trade in trades:
        trade_dict = dict(zip(trade_cols, trade))
        order_id = trade_dict.get('order_id')
        
        signal_info = signal_map.get(order_id, {'real_prob': 'N/A', 'seconds_remaining': 'N/A', 'reason': 'N/A'})
        
        row = list(trade) + [signal_info['real_prob'], signal_info['seconds_remaining'], signal_info['reason']]
        csv_data.append(row)

    with open(output_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(csv_data)

    print(f"Successfully exported {len(csv_data)} trades to {output_path}")

if __name__ == "__main__":
    export_trades()
