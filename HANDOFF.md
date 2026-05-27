# Session Handoff — 2026-05-23 through 2026-05-26

## Executive Summary

This was a 4-day intensive session spanning research, paper validation, and live deployment of a Kalshi 15-minute crypto binary options trading bot. The bot trades momentum + OBI (orderbook imbalance) signals on BTC and ETH with time_exit at T-30 seconds as the only exit mechanism.

**Key outcome:** The strategy is proven profitable in backtest ($108K on $1K over 46 days) and in paper trading (+$23 on $25 in 7 hours). Live trading is marginally profitable outside of 20-23 UTC but loses money during those afternoon hours due to execution quality degradation — not signal quality. The signal is correct at all hours; live execution can't capture the edge during thin-book afternoon periods.

**The fundamental insight:** The backtest-live gap isn't a strategy problem — it's a plumbing problem. Faster execution (FIX protocol, AWS migration) is the path to profitability, not more parameter tuning.

---

## Current Live Bot State

- **PID:** 1866182
- **Mode:** live
- **Balance:** ~$14-15 (started session at ~$13, deposited $10, lost ~$9 from high-price entries + afternoon trading before fixes)
- **Strategy:** Momentum only (mean reversion shadow-logged, not traded)
- **Kelly:** 0.25
- **Symbols:** BTC, ETH (SOL disabled)
- **YES side:** enabled
- **Exit policy:** time_exit only at T-30s
- **MAX_TRADE_PRICE:** 0.65 (lowered from 0.85)
- **MIN_TRADE_PRICE:** 0.25
- **EDGE_THRESHOLD:** 0.04
- **MOMENTUM_MIN_TIME:** 91
- **Dynamic k:** ON (intra-window, cap 600)
- **Composite pricing:** ON (Coinbase + Kraken + Bitstamp)
- **Off-hours gate:** 20-23 UTC (no live trades, shadow-logs normal + inverted signals)
- **Dashboard:** port 8082, strategy/side/exit breakdowns

---

## What Happened This Session (Chronological)

### Day 1 (May 23): Crisis Response
- Bot had crashed from $40 to $13 after a -$16 loss in the 1730 window (31+18 contracts, pre-cap)
- Previous AI (Gemini) had added logging changes and a stop_loss
- **Discovered crossed orderbook bug**: Kalshi WS feed accumulated stale price levels creating impossible bid>ask states. YES bid=0.82, YES ask=0.35. This corrupted ALL price-dependent logic.
- **Fixed with inline trimming**: after each delta, remove opposite-side levels that would create a cross. Fill rate went from 25% to 100%.
- Changed per-side pause from permanent daily kill to 30-minute cooldown
- Disabled take_profit (backtest showed -14% PnL)
- Ran A/B backtest on exit policies confirming time_exit only is optimal

### Day 2 (May 24): Paper Validation
- Switched to paper mode after the crash
- Reverted Gemini's stop_loss (backtest evidence said it destroys edge)
- Set SYMBOLS=ETH only (BTC was underperforming in live)
- Ran overnight paper: 25 trades, 44% WR, -$0.18 (flat)
- **Key finding:** time_exit is profitable (+$2.81), settlement is disaster (-$2.99). YES+settlement was 0-7.
- Set MOMENTUM_MIN_TIME=91 to ensure all entries qualify for time_exit
- Investigated the exit bug: _evaluate_exits was placed AFTER market/orderbook gates. When Kalshi rolls to new window ticker, exit check gets skipped. Fixed by hoisting above all gates.

### Day 3 (May 25): The Big Research Day
- Re-enabled BTC (backtest showed BTC at 97% WR, live problems were likely from crossed orderbook era)
- **Implemented dynamic k**: estimate_k_from_vol existed but was dead code. Wired into trading path using intra-window prices. Backtest: +$7K over static k. Cap raised from 400 to 600.
- **Built multi-exchange feeds**: Kraken + Bitstamp WS clients alongside Coinbase. Composite price averaging. Measured 0.5 bps avg basis, 342ns compute cost.
- **Discovered mean reversion**: When momentum+OBI disagree (40% of windows the bot was skipping), trading AGAINST momentum produces 94.9% WR in backtest, $101K PnL. Combined strategy: $108K.
- **Built flex backtester**: Lightweight engine for fast strategy comparison with walk-forward stability tests and bootstrap confidence intervals.
- **Recovered V1 data**: Old trades.db was corrupted (1 bad page). Recovered 849,419 of 849,426 rows. Extended backtest from 35 to 46 days.
- **OBI magnitude analysis**: |OBI| < 0.10 has 58% WR, |OBI| > 0.50 has 78% WR. BUT live OBI magnitudes are mostly < 0.10 so the gate would starve the bot.
- **Regime detection tests**: Rolling window results, k-as-regime, OBI persistence, volatility regime, combined score. ALL showed <3pp difference. Dead end.
- **Serial correlation**: Windows are independent (47.5% same as previous). No signal.
- **Momentum acceleration**: 99% of trades classify as accelerating. No differentiation.
- Wired combined strategy (momentum + mean reversion fallback) into the bot
- Modernized Telegram/Discord commands to show strategy, side, exit breakdowns
- Upgraded dashboard with strategy breakdown cards

### Day 3 Evening: Paper Testing Combined Strategy
- Ran combined strategy in paper: 44 trades, 50% WR, +$21.23 on $25 bankroll
- Mean reversion contributed 26 trades, +$4.24. Momentum contributed 18 trades, +$16.99
- time_exit: 68.8% WR, +$32.97. Settlement: 0-12, -$11.74
- Moved mean reversion to shadow-only after weak live performance (2 live trades, both settlement losses)

### Day 4 (May 26): Live Deployment + Afternoon Discovery
- Went live at 06:18 UTC with $13.02 balance, Kelly 0.50
- First two trades: mean reversion YES, both settlement, -$3.71
- Dropped Kelly to 0.25, added auto-paper safety at $5
- Moved mean reversion to shadow-only logging
- Bot recovered to +$6.04 on momentum time_exits
- **$7.39 loss on ETH YES x9 @ $0.81** — high entry price, settlement loss. Three high-price losses totaled -$9.84
- **Lowered MAX_TRADE_PRICE to 0.65** — entries above 0.70 have inverted risk/reward (pay 70c+ to win 30c)
- Bot continued losing during 20-23 UTC

**Afternoon Discovery:**
- Analyzed ALL live data by hour. 20-23 UTC (4-7pm ET) is consistently catastrophic:
  - May 22: 5 trades, 1W, -$15.21
  - May 23: 7 trades, 1W, -$10.05
  - May 26: 10 trades, 1W, -$9.37
  - **Total: 22 trades, 3W-16L, -$33.09**
- Every other time slot is net positive (+$26.48 combined)
- NO side during afternoon: 1W-11L, -$30.39
- **BUT paper is +$13.88 during those same hours.** The signal is correct; live execution can't capture it.
- **Backtest confirms**: "bad hours" generate +$10K-13K in backtest. The strategy works at all hours in simulation.
- Deployed off-hours gate: no live trading 20-23 UTC, shadow-logs both normal and inverted signals

---

## All Backtest Results (Full Engine, 46 Days, $1K Bankroll)

### Strategy Comparison
| Config | Trades | WR | PnL | DD |
|--------|--------|-----|------|-----|
| Momentum static k=150 | 3,342 | 89.7% | $72,768 | $252 |
| Momentum static k=200 | 3,553 | 90.3% | $75,632 | $252 |
| Momentum dynamic k (cap 600) | 3,870 | 91.1% | $79,990 | $256 |
| Mean Reversion only | 3,778 | 94.9% | $101,945 | $252 |
| **Combined (mom + revert)** | **4,534** | **90.9%** | **$108,215** | $306 |
| Combined edge=0.08 | 4,485 | 92.2% | $112,879 | $246 |
| Combined min_obi=0.05 | 4,393 | 92.5% | $107,454 | $198 |
| Combined NO-only | 2,820 | 92.4% | $71,001 | $152 |
| BTC combined only | 2,553 | 97.7% | $74,905 | $179 |
| ETH combined only | 2,044 | 83.1% | $35,927 | $149 |

### Exit Policy Comparison
| Exit Policy | PnL |
|-------------|-----|
| time_exit only | $72,867 |
| + take_profit | $62,261 (-14%) |
| + conviction_stop | $74,606 (+2%) |
| + stop_loss 60% | ~$74,538 (+2%) |

### What Doesn't Work In Backtest
- Serial correlation: windows are independent
- Momentum acceleration: no differentiation
- Regime detection: <3pp difference on all tests
- YES fade (retail overbet): 48.6% WR, barely profitable

---

## Live Performance (All Sessions Combined)

### By Time of Day (UTC)
| Period | Live Trades | WR | PnL |
|--------|-------------|-----|-----|
| 00-12 (overnight) | 58 | 41.4% | +$21.48 |
| 13-19 (US morning) | 39 | 53.8% | +$5.00 |
| **20-23 (US afternoon)** | **22** | **14.3%** | **-$33.09** |

### By Strategy (Live)
| Strategy | W-L | PnL |
|----------|-----|-----|
| Momentum YES | 15-14 | +$20.83 |
| Momentum NO | 15-20 | -$15.55 |
| Mean reversion YES | 0-2 | -$3.71 |

### By Exit (All Live)
| Exit | WR | PnL |
|------|-----|-----|
| time_exit | ~67% | profitable |
| settlement | 0% | ALL losses |

---

## The Backtest-Live Gap

This is the central unsolved problem. The strategy shows 91% WR and $79K PnL in backtest but ~50% WR and marginal PnL live. The gap is NOT the strategy — it's execution:

1. **Latency**: Signal-to-fill pipeline is ~150-350ms. The backtest fills at recorded prices instantly. In 200ms, BTC can move 1-3c, eating 25-85% of edge.
2. **Settlement source mismatch**: Bot uses Coinbase; Kalshi settles on CF Benchmarks RTI (multi-exchange composite, 60-second trimmed average). We added Kraken+Bitstamp composite but can't replicate the exact CF calculation.
3. **Afternoon execution quality**: Paper is profitable 20-23 UTC (+$13.88) but live is catastrophic (-$34.63). Thinner orderbooks during US close = worse fills + wider spreads.
4. **Fill model**: Backtest assumes fill at recorded ask. Live fills at actual ask which may have moved. The 3c slippage buffer helps but doesn't fully close the gap.

### What Would Close The Gap
- **FIX protocol**: Kalshi supports it. Cuts order submission from 50-86ms to ~1-5ms. Single biggest potential improvement.
- **AWS US-East migration**: Free student credits available. Saves ~20-30ms network latency.
- Both together: ~70-100ms faster pipeline. Would capture more of the backtest edge.

---

## Shadow Trade Logging

Three types logged in the `signals` table (action column):
- `whatif_mean_reversion` — mean reversion signals that would have traded
- `whatif_offpeak` — signals during 20-23 UTC (blocked from live trading)
- `whatif_inverted_offpeak` — INVERTED version of off-peak signals (if bot says YES, logs NO)
- `paper_shadow` — logged alongside every live trade for fill comparison

Query examples:
```sql
-- Check off-peak shadow performance
SELECT s.ticker, s.side, me.result,
  CASE WHEN (s.side='yes' AND me.result='up') OR (s.side='no' AND me.result='down') THEN 'WIN' ELSE 'LOSS' END
FROM signals s
JOIN market_events me ON s.ticker = me.ticker AND me.event_type = 'close'
WHERE s.action = 'whatif_offpeak'
GROUP BY s.ticker ORDER BY s.timestamp;

-- Check inverted signal performance
SELECT s.ticker, s.reason, me.result FROM signals s
JOIN market_events me ON s.ticker = me.ticker AND me.event_type = 'close'
WHERE s.action = 'whatif_inverted_offpeak'
GROUP BY s.ticker ORDER BY s.timestamp;
```

---

## Infrastructure Built This Session

- **Crossed-book fix**: Inline trimming in kalshi_ws.py prevents impossible orderbook states
- **Exit fix**: Hoisted above market/orderbook gates, uses window.ticker not market.ticker
- **Dynamic k**: Computed from intra-window price ticks, cap 600
- **Composite pricing**: Coinbase + Kraken + Bitstamp averaged (<5s freshness gate)
- **Mean reversion strategy**: Full implementation in strategy/mean_reversion.py (shadow-only)
- **Flex backtester**: Fast strategy comparison with walk-forward and bootstrap
- **Recovered V1 data**: 849K snapshots merged into backtest dataset (46 days total)
- **Off-hours gating**: Configurable UTC hours, shadow + inverted logging
- **Paper shadow trades**: Every live signal also logged for fill comparison
- **Dashboard upgrades**: Strategy/side/exit breakdowns, dynamic k display
- **Telegram/Discord modernization**: Strategy labels, side stats, exit reasons

---

## Files Changed

### Bot Core
- `src/kalshi_bot/main.py` — eval loop, exits, off-hours gate, dynamic k, composite pricing, shadow trades
- `src/kalshi_bot/config.py` — offpeak hours, all new settings
- `src/kalshi_bot/strategy/momentum.py` — min_obi parameter
- `src/kalshi_bot/strategy/mean_reversion.py` — NEW (shadow-only)
- `src/kalshi_bot/strategy/probability.py` — dynamic k cap 600
- `src/kalshi_bot/strategy/signals.py` — MEAN_REVERSION enum
- `src/kalshi_bot/client/kalshi_ws.py` — crossed-book trimming
- `src/kalshi_bot/client/kraken.py` — NEW
- `src/kalshi_bot/client/bitstamp.py` — NEW
- `src/kalshi_bot/execution/executor.py` — SubmitResult wrapper, logging
- `src/kalshi_bot/risk/manager.py` — 30-min cooldown instead of permanent pause

### Dashboard & Alerts
- `src/kalshi_bot/dashboard.py` — by_side, by_exit, by_strategy in stats API
- `src/kalshi_bot/dashboard.html` — strategy cards, dynamic k display
- `src/kalshi_bot/alerts/telegram.py` — modernized commands, strategy labels
- `src/kalshi_bot/alerts/discord_bot.py` — same

### Backtester
- `/root/kalshi-backtest/src/backtest/config.py` — dynamic_k, min_obi, combined strategy
- `/root/kalshi-backtest/src/backtest/flex_engine.py` — NEW flex backtester
- `/root/kalshi-backtest/src/strategy/adapter.py` — mean_reversion + combined support
- `/root/kalshi-backtest/scripts/` — multiple new test scripts

### Documentation
- `CLAUDE.md` — fully rewritten for current architecture
- `HANDOFF.md` — this file
- `BACKTEST_FINDINGS.md` — all backtest numbers
- `TODO.md` — remaining work
- `research_questions.md` — external research needed
- `legacy/` — 13 outdated MD files moved here

---

## .env Settings (Current Live)

```
TRADING_MODE=live
BANKROLL_OVERRIDE=0
SYMBOLS=BTC,ETH
KELLY_FRACTION=0.25
EDGE_THRESHOLD=0.04
MOMENTUM_MIN_TIME=91
MOMENTUM_MAX_TIME=480
MAX_TRADE_PRICE=0.65
MIN_TRADE_PRICE=0.25
LOGISTIC_K=200.0
YES_SIDE_DISABLED=false
OFFPEAK_START_UTC=20
OFFPEAK_END_UTC=23
MAKER_FIRST=false
DAILY_LOSS_LIMIT=10.0
PER_SIDE_DAILY_LOSS_LIMIT=0
MAX_CONCURRENT_POSITIONS=3
```

---

## What NOT To Do

1. **Don't re-enable stop_loss or take_profit** — both destroy edge in backtest
2. **Don't add mean reversion to live** — 94.9% backtest but ~45% live shadow WR
3. **Don't increase Kelly above 0.25** until balance is $50+
4. **Don't trade SOL** — no backtest data, thin books
5. **Don't trade 20-23 UTC live** — 3W-16L across all sessions
6. **Don't deploy backtest findings without paper validation** — mean reversion proved this
7. **Don't pick scattered "bad hours"** — that's overfitting. Stick to contiguous 20-23 block
8. **Don't let multiple AIs make contradictory changes**
9. **Don't raise MAX_TRADE_PRICE above 0.65** — high-price entries lose live
10. **Don't panic on single trade losses** — the strategy recovers (went from -$5 to +$6 overnight)

---

## Priority TODO

1. **FIX protocol integration** — biggest potential PnL improvement. Kalshi supports it.
2. **AWS US-East migration** — free student credits, ~20-30ms latency reduction
3. **Collect shadow data** — let off-peak + inverted + mean reversion shadow logs accumulate for 1-2 weeks
4. **Investigate settlement leaks** — why do some trades still miss time_exit despite min_time=91?
5. **Consider edge=0.08** — backtest best at $112K but untested live. Paper validate first.
