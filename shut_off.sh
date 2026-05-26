#!/bin/bash

set -u

graceful_stop() {
    local name="$1"
    local pattern="$2"
    local timeout_s="$3"
    local pids=()
    local still_running=()
    local pid=""
    local elapsed=0

    while IFS= read -r pid; do
        pids+=("$pid")
    done < <(pgrep -f "$pattern" || true)

    if [ "${#pids[@]}" -eq 0 ]; then
        echo "$name is not running."
        return
    fi

    echo "Stopping $name gracefully (SIGTERM): ${pids[*]}"
    for pid in "${pids[@]}"; do
        kill -TERM "$pid" 2>/dev/null || true
    done

    while [ "$elapsed" -lt "$timeout_s" ]; do
        still_running=()
        for pid in "${pids[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                still_running+=("$pid")
            fi
        done

        if [ "${#still_running[@]}" -eq 0 ]; then
            echo "$name stopped cleanly."
            return
        fi

        sleep 1
        elapsed=$((elapsed + 1))
    done

    echo "$name did not stop within ${timeout_s}s. Forcing stop (SIGKILL): ${still_running[*]}"
    for pid in "${still_running[@]}"; do
        kill -KILL "$pid" 2>/dev/null || true
    done
}

echo "Shutting down Kalshi services..."
graceful_stop "Bot" "kalshi_bot.main" 30
graceful_stop "Dashboard" "kalshi_bot.dashboard" 15
echo "Shutdown complete."
