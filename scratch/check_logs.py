import json
from datetime import datetime

log_file = "/root/kalshi-bot/kalshi-bot-v2/logs/bot.log"
print("Scanning logs...")

count = 0
with open(log_file, "r") as f:
    for line in f:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
            
        # Check if the line has ticker, seconds_remaining
        ticker = data.get("ticker")
        secs = data.get("seconds_remaining")
        event = data.get("event")
        
        if secs is not None and secs <= 120:
            print(f"Time: {data.get('timestamp')} | Ticker: {ticker} | Secs: {secs} | Event: {event} | Best YES Ask: {data.get('best_yes_ask')} | Bid: {data.get('best_yes_bid')}")
            count += 1
            if count > 100:
                print("Too many results, stopping.")
                break
