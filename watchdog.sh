#!/bin/bash
# watchdog.sh — auto-restart kalshi bot if it dies
cd /root/kalshi-bot/kalshi-bot-v2

while true; do
    if ! pgrep -f "kalshi_bot.main" > /dev/null; then
        echo "[$(date)] Bot dead, restarting..."
        pkill -9 -f "kalshi_bot" 2>/dev/null
        sleep 2
        PYTHONPATH=src nohup .venv/bin/python -m kalshi_bot.main > logs/bot.out 2>&1 &
        echo "[$(date)] Bot restarted with PID $!"
        if ! pgrep -f "kalshi_bot.dashboard" > /dev/null; then
            nohup .venv/bin/python -u -m kalshi_bot.dashboard > logs/dashboard.out 2>&1 &
            echo "[$(date)] Dashboard restarted"
        fi
    fi
    sleep 30
done
