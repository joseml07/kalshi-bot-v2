#!/bin/bash

# Configuration
VENV=".venv/bin/activate"
PYTHON_BIN=".venv/bin/python"
LOG_DIR="logs"
export PYTHONPATH=src

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Check if already running
if pgrep -f "kalshi_bot.main" > /dev/null; then
    echo "Bot is already running. Use ./restart.sh if you want to restart."
    exit 1
fi

# Start Bot
echo "Starting Kalshi Bot..."
nohup $PYTHON_BIN -u -m kalshi_bot.main > "$LOG_DIR/bot.out" 2>&1 &
BOT_PID=$!
echo "Bot started with PID: $BOT_PID"

# Start Dashboard
echo "Starting Dashboard..."
nohup $PYTHON_BIN -u -m kalshi_bot.dashboard > "$LOG_DIR/dashboard.out" 2>&1 &
DASH_PID=$!
echo "Dashboard started with PID: $DASH_PID"

echo "------------------------------------------------"
echo "Bot logs: tail -f $LOG_DIR/bot.out"
echo "Dash logs: tail -f $LOG_DIR/dashboard.out"
echo "------------------------------------------------"
