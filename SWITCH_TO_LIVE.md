# SWITCH_TO_LIVE checklist

Use this when ready to flip from `trading_mode=paper` to `trading_mode=live`.
Every step is gated by a config flag — nothing in this list executes
automatically. Read each block, then change the corresponding `.env` value.

---

## 0. Pre-flight (before changing anything)

- [ ] Bot has been running stably in paper for at least 7 days with the
      current strategy/config.
- [ ] Most recent backtester sweep was generated against trades.db **after**
      the exit-fix landed (commit `7663b45` or later).
- [ ] Inspect the live paper performance in the dashboard's per-side panel.
      Both `yes` and `no` should be net-positive. If one side is deeply
      negative, **do not flip live**; investigate or pause that side first.
- [ ] `KILL_SWITCH` file should NOT exist in the repo root.
- [ ] `live_state.json` matches actual paper-mode state.

## 1. Risk circuit-breakers — enable the per-side cap

Edit `.env`:

```
# Halt EITHER yes OR no for the rest of the day if its realized
# daily PnL goes below -PER_SIDE_DAILY_LOSS_LIMIT. Recommended start: $10.
# When 0.0, this gate is disabled (current default — keep at 0 in paper).
PER_SIDE_DAILY_LOSS_LIMIT=10.0
```

Implementation: `risk/manager.py:_check_per_side_daily_loss`. Resets at UTC
midnight via `_rotate_day`. Pauses only the offending side; the other side
keeps trading.

Sanity check before live: `pytest tests/test_risk.py::test_per_side_daily_loss_pauses_only_that_side`.

## 2. Side-degradation alerts

```
# When the rolling win-rate on a side drops below the threshold over the
# last N trades, fire a Telegram/Discord alert via the existing
# alerter.trade_exited("SYSTEM", ...) channel.
SIDE_WR_ALERTS_ENABLED=true
SIDE_WR_ALERT_WINDOW=30
SIDE_WR_ALERT_THRESHOLD=0.30
```

Alerts do NOT auto-disable the side — they're observability only. If you
want a side disabled when degraded, increase `PER_SIDE_DAILY_LOSS_LIMIT`
restriction (smaller value = pauses sooner) or pause the side manually via
a Telegram/Discord command.

## 3. Position sizing

The default Kelly fraction is `0.25`. **Lower it** for the first real
session — sizing errors in paper don't burn real cash, sizing errors live
do. Suggested first-live settings:

```
KELLY_FRACTION=0.10        # one-quarter of paper sizing
MAX_PER_TRADE=10.0         # was 25 in paper; clamp first-week notional
MAX_CONCURRENT_POSITIONS=2
DAILY_LOSS_LIMIT=25.0      # bot-wide; per-side cap above is in addition
```

## 4. Flip the trading-mode flag

```
TRADING_MODE=live
PAPER_BALANCE=25.0         # ignored when TRADING_MODE=live, but keep set
```

When `TRADING_MODE=live`, `refresh_balance()` queries the real Kalshi
account every refresh — `paper_balance` is bypassed automatically.
(See `main.py:464` and `main.py:506`.)

## 5. Restart

```
cd /root/kalshi-bot/kalshi-bot-v2
./shut_off.sh && ./run.sh
```

Wait for the `HEALTH balance=<real>` log line. If `balance=0` or
`balance=null` you have an auth/key issue — abort and fix before trading.

## 6. First-hour monitoring

Open these in parallel:

- Bot dashboard: <http://137.184.144.30:8082/>
- Backtester (for comparison): <http://137.184.144.30:8088/>
- Telegram / Discord (alerts will route here)
- `tail -f /root/kalshi-bot/kalshi-bot-v2/logs/bot.log | grep -E 'HEALTH|exit_signal|side_paused|side_wr_degraded'`

Watch for the **first** real fill, not the first signal. The first signal
might be slow because of orderbook depth.

## 7. Roll-back

If anything looks wrong:

```
touch /root/kalshi-bot/kalshi-bot-v2/KILL_SWITCH    # stops new trades
./shut_off.sh                                       # graceful stop
```

Then in `.env` set `TRADING_MODE=paper`, remove KILL_SWITCH, restart. All
open Kalshi positions stay open and need manual flatten via the Kalshi UI
or a settle-only run.

---

## Reference: config flags added with the framework

| flag                          | default | purpose                                              |
|-------------------------------|--------:|------------------------------------------------------|
| `PER_SIDE_DAILY_LOSS_LIMIT`   | `0.0`   | Pause one side after losing this much in a UTC day  |
| `SIDE_WR_ALERTS_ENABLED`      | `false` | Emit Telegram/Discord alert on degraded side WR     |
| `SIDE_WR_ALERT_WINDOW`        | `30`    | Trades in rolling window                            |
| `SIDE_WR_ALERT_THRESHOLD`     | `0.30`  | Threshold below which alert fires                   |

`0.0` / `false` defaults preserve current paper behavior — the framework
is dormant until you flip these explicitly.
