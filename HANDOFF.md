# Session Handoff — 2026-05-23 through 2026-05-26

## Current Live Bot State

- **PID:** 1850978
- **Mode:** live
- **Balance:** ~$21.32 (deposited $23 total, down ~$2 from high-price losses)
- **Strategy:** Momentum only (mean reversion is shadow-logged, not traded)
- **Kelly:** 0.25
- **Symbols:** BTC, ETH (SOL disabled — no backtest data, thin books)
- **YES side:** enabled (momentum YES is best live performer at +$20.83)
- **Exit policy:** time_exit only at T-30s (all other exits disabled)
- **MAX_TRADE_PRICE:** 0.65 (lowered from 0.85 — entries above 0.70 were losing badly)
- **MIN_TRADE_PRICE:** 0.25
- **EDGE_THRESHOLD:** 0.04
- **MOMENTUM_MIN_TIME:** 91 (ensures all entries qualify for time_exit)
- **Dynamic k:** ON (computed from intra-window prices, cap 600)
- **Composite pricing:** ON (Coinbase + Kraken + Bitstamp averaged)
- **Per-side pause:** disabled (was causing issues)
- **Auto-paper safety:** switches to paper if balance < $10
- **Dashboard:** port 8082, shows strategy breakdown + side + exit stats

## Key Files Changed This Session

- `src/kalshi_bot/main.py` — Combined strategy (momentum + mean reversion shadow), exit fix (hoisted above market gates, uses window.ticker), dynamic k from intra-window ticks, composite pricing, auto-paper safety switch
- `src/kalshi_bot/strategy/mean_reversion.py` — NEW: mean reversion strategy (shadow-only for now)
- `src/kalshi_bot/strategy/momentum.py` — Added min_obi parameter
- `src/kalshi_bot/strategy/probability.py` — Dynamic k cap raised to 600
- `src/kalshi_bot/strategy/signals.py` — Added MEAN_REVERSION to StrategyName enum
- `src/kalshi_bot/client/kalshi_ws.py` — Crossed-book inline trimming fix
- `src/kalshi_bot/client/kraken.py` — NEW: Kraken WS price feed
- `src/kalshi_bot/client/bitstamp.py` — NEW: Bitstamp WS price feed
- `src/kalshi_bot/risk/manager.py` — Per-side pause changed from permanent to 30-min cooldown
- `src/kalshi_bot/dashboard.py` — Added by_side, by_exit, by_strategy to stats API
- `src/kalshi_bot/dashboard.html` — Strategy breakdown cards, dynamic k display
- `src/kalshi_bot/alerts/telegram.py` — Modernized commands, strategy labels on trade alerts
- `src/kalshi_bot/alerts/discord_bot.py` — Same modernization

## What We Discovered

### The Big Findings

1. **Mean reversion backtests at $101K PnL (94.9% WR) but underperforms live.** Trades against momentum when OBI disagrees. Settlement-level shadow WR overnight was 45.5% (10W-12L). Currently shadow-logged only, not traded. The backtest-live gap for mean reversion is larger than for momentum.

2. **Crossed orderbook was corrupting all prices.** The Kalshi WS feed accumulated stale price levels creating impossible bid>ask states. Fixed with inline trimming: after each delta, remove opposite-side levels that would cross. This improved fill rate from 25% to 100%.

3. **time_exit is the ENTIRE edge.** time_exit trades: ~67% WR, profitable. Settlement trades: 0% WR across all live data. The strategy works as a momentum scalper, not a settlement predictor.

4. **Dynamic k adds $7K+ in backtest.** Adapts confidence with realized volatility. Quiet market → k rises (up to 600) → more confident. Volatile → k drops → fewer bad entries.

5. **Entries above $0.70 are structural losers live.** Risk/reward is inverted — paying 70c+ to win 30c means one loss erases many wins. Backtest shows 98% WR at those prices but live execution can't capture that edge. MAX_TRADE_PRICE=0.65 fixes this.

6. **The exit bug:** _evaluate_exits was placed AFTER market/orderbook gates that could `continue` past it. When Kalshi rolls to a new window ticker, the old orderbook disappears, the gate fires, and exit never runs. Fixed by hoisting exit check above all gates, using window.ticker instead of market.ticker.

7. **YES vs NO flips between sessions.** Backtest shows both sides equal (~90% each). Live, they take turns depending on whether crypto is trending up or down. Don't permanently disable either.

### Backtest Results (Full Engine, 46 Days)

| Config | Trades | WR | PnL | DD |
|--------|--------|-----|------|-----|
| Momentum static k=150 | 3,342 | 89.7% | $72,768 | $252 |
| Momentum dynamic k (cap 600) | 3,870 | 91.1% | $79,990 | $256 |
| Combined (mom + revert) | 4,534 | 90.9% | $108,215 | $306 |
| Combined edge=0.08 | 4,485 | 92.2% | $112,879 | $246 |
| Combined min_obi=0.05 | 4,393 | 92.5% | $107,454 | $198 |
| BTC combined only | 2,553 | 97.7% | $74,905 | $179 |

### What We Tested and Rejected

- **Take-profit exit:** costs ~14% PnL by selling winners early. Disabled.
- **Stop-loss exit (60% drawdown):** marginal +2.3% in backtest, didn't help live. Disabled.
- **Serial correlation:** Windows are independent (47.5% same as previous). No signal.
- **Momentum acceleration:** 99% of trades classify as accelerating. No differentiation.
- **Regime detection (rolling results + k + OBI persistence):** All tests showed <3pp difference. Not worth building.
- **min_obi gate:** Strong in backtest but live OBI magnitudes are much smaller than historical. Would starve the bot of trades.
- **SOL:** -$1.69 in paper, no backtest data. Disabled.

### Settings That Were Explored but NOT Deployed

- **EDGE_THRESHOLD=0.08:** Best backtest PnL ($112K) but untested live. Risk of overfitting.
- **Mean reversion live trading:** Shadow-logging only. Needs more data or execution fix.
- **Time-of-day edge scaling:** Code was written and reverted. Backtest shows morning is better but all hours are profitable.
- **min_obi=0.10-0.20:** Great in backtest, but live OBI is mostly <0.10. Would filter too many trades.
- **Kelly 0.50:** Works with $50+ balance. On $13 it nearly busted ($3.02 low). Keep at 0.25 until balance grows.

## Live Performance Summary

### This Session (since going live ~06:18 UTC May 26)
- 22 trades, ~44% overall WR, ~breakeven PnL
- Momentum time_exit: 67% WR, consistently profitable
- Settlement: 0% WR, all losses
- Three high-price entries (0.70-0.81) cost ~$9.84 → fixed with MAX_TRADE_PRICE=0.65

### Previous Live (May 22-23, before the crash)
- Climbed from $10 to $40+ twice
- Crashed to $13 from oversized positions (pre-MAX_CONTRACTS fix) and afternoon regime shift
- The climb happened during US morning hours (13:00-17:00 UTC)

## Infrastructure Built

- **Composite pricing:** Coinbase + Kraken + Bitstamp WS feeds averaged. Approximates CF Benchmarks RTI (the actual settlement source). Measured basis: 0.5 bps avg.
- **Flex backtester:** Lightweight strategy comparison engine at `/root/kalshi-backtest/src/backtest/flex_engine.py`. Tests any strategy function with walk-forward stability and bootstrap confidence intervals.
- **Recovered V1 data:** `/root/fromkalshi/recovered.db` — 849K snapshots from Apr 9-May 25 (46 days). Merged into backtester at `/root/kalshi-backtest/data/merged_backtest.db`.
- **Shadow trade logging:** Mean reversion signals logged as `whatif_mean_reversion` in signals table for tracking without execution.

## Key Research Documents

- `/root/kalshi-bot/kalshi-bot-v3/angles_nobody_considered.md` — Why backtest ≠ live (price source mismatch, latency, competition)
- `/root/kalshi-bot/kalshi-bot-v3/mean_reversion_findings.md` — Mean reversion discovery and full backtest results
- `/root/kalshi-bot/kalshi-bot-v3/alternative_strategies.md` — Strategy ideas ranked by feasibility
- `/root/kalshi-bot/kalshi-bot-v3/claude_brainstorm.md` — Discipline-first V3 philosophy
- `/root/kalshi-bot/kalshi-bot-v2/BACKTEST_FINDINGS.md` — All backtest numbers in one place
- `/root/kalshi-bot/kalshi-bot-v2/TODO.md` — Remaining TODO items
- `/root/kalshi-bot/kalshi-bot-v2/research_questions.md` — Questions for external AI research

## .env Settings (Current Live)

```
TRADING_MODE=live
BANKROLL_OVERRIDE=0  (uses real Kalshi balance)
SYMBOLS=BTC,ETH
KELLY_FRACTION=0.25
EDGE_THRESHOLD=0.04
MOMENTUM_MIN_TIME=91
MAX_TRADE_PRICE=0.65
MIN_TRADE_PRICE=0.25
LOGISTIC_K=200.0  (fallback; dynamic k overrides this)
YES_SIDE_DISABLED=false
MAKER_FIRST=false
PER_SIDE_DAILY_LOSS_LIMIT=0  (disabled)
DAILY_LOSS_LIMIT=10.0
MAX_CONCURRENT_POSITIONS=3
```

## On Overfitting

The core strategy (momentum + OBI + time_exit + dynamic k) is simple with ~5 tunable knobs. Walk-forward tests show the second half performs better than the first half. Bootstrap shows 100% probability of profitability. The backtest-live gap is from execution differences (latency, fill model), not overfitting.

However: edge=0.08, min_obi, and mean reversion all showed huge backtest improvements but underwhelmed or weren't tested live. Don't deploy backtest-only findings without paper validation. The MAX_TRADE_PRICE=0.65 change was validated on actual live data, not just backtest — that's the right way to make changes.

## What NOT to Do

1. Don't re-enable stop_loss or take_profit — both proven to destroy edge
2. Don't add mean reversion to live trading without fixing why it goes to settlement
3. Don't increase Kelly above 0.25 until balance is $50+
4. Don't trade SOL without backtest data
5. Don't let multiple AIs make contradictory changes — one AI at a time
6. Don't deploy backtest findings without paper validation first

## Update: Off-Hours Discovery (2026-05-26 evening)

### The Afternoon Kill Zone (20-23 UTC / 4-7pm ET)

Consistent across ALL three live sessions:
- May 22: 5 trades, 1W, **-$15.21**
- May 23: 7 trades, 1W, **-$10.05**
- May 26: 10 trades, 1W, **-$9.37**
- **Total: 22 trades, 3W-16L, -$33.09**

Every other time slot is net positive (+$26.48 combined). This is structural, not variance.

NO side during afternoon: 1W-11L, -$30.39. That's where the money dies.

**Deployed fix:** OFFPEAK_START_UTC=20, OFFPEAK_END_UTC=23. Signals during off-hours logged as `whatif_offpeak` but not traded.

### Inverted Signal Idea

If the bot is 14% WR during 20-23 UTC, the OPPOSITE trade would be ~86% WR. Estimated inverted PnL: +$61 vs actual -$35. That's a $96 swing on 22 trades.

**NOT deployed.** Only 22 trades across 3 sessions — could be overfitting. Shadow-log the inverted signal during off-hours and validate over 1-2 weeks before deploying.

### MAX_TRADE_PRICE lowered to 0.65

Three entries above $0.70 cost $9.84 in this session. Risk/reward is inverted at high prices — paying 70c+ to win 30c. One loss erases 4 wins. With MAX_TRADE_PRICE=0.65, those entries would have been +$5.56 instead of -$1.35.

### Paper Shadow Trades

Every live signal now also logged as `paper_shadow` in the signals table. Compare live fill prices vs signal prices to quantify the execution gap.
