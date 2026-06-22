#!/bin/bash
set -e
cd /root/kalshi-bot/kalshi-bot-v2

echo "=== Killing all kalshi_bot processes ==="
pkill -9 -f "kalshi_bot" 2>/dev/null || true
sleep 2

# Verify nothing left
if pgrep -f "kalshi_bot" > /dev/null; then
    echo "WARNING: processes still alive, force killing..."
    pgrep -f "kalshi_bot" | xargs kill -9 2>/dev/null || true
    sleep 1
fi

echo "=== Starting bot ==="
PYTHONPATH=src nohup .venv/bin/python -m kalshi_bot.main > logs/bot.out 2>&1 &
BOT_PID=$!
echo "Bot PID: $BOT_PID"

echo "=== Starting dashboard ==="
nohup .venv/bin/python -u -m kalshi_bot.dashboard > logs/dashboard.out 2>&1 &
DASH_PID=$!
echo "Dashboard PID: $DASH_PID"

sleep 5

# Verify
if kill -0 $BOT_PID 2>/dev/null; then
    echo "Bot running OK"
    tail -2 logs/bot.out | grep -E "balance|startup" || true
else
    echo "ERROR: Bot failed to start! Check logs/bot.out"
fi

if kill -0 $DASH_PID 2>/dev/null; then
    echo "Dashboard running OK on :8082"
else
    echo "ERROR: Dashboard failed! Check logs/dashboard.out"
fi

echo "---"
echo "Bot logs: tail -f logs/bot.out"
echo "Dash logs: tail -f logs/dashboard.out"
