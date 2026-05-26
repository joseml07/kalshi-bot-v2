#!/bin/bash

echo "Stopping all kalshi_bot processes..."
pkill -f "kalshi_bot.main"
pkill -f "kalshi_bot.dashboard"

sleep 1

# Ensure everything is dead
pgrep -f "python.*kalshi_bot" | xargs kill -9 2>/dev/null

echo "Restarting..."
./run.sh
