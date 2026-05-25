# Backtest Findings (2026-05-25)

All results from the full backtester (tick-by-tick, real fills, 46 days, $1,000 bankroll).

## Strategy Evolution

| Config | Trades | WR | PnL | Avg/Trade | Max DD |
|--------|--------|-----|------|-----------|--------|
| Momentum static k=150 | 3,342 | 89.7% | $72,768 | $21.77 | $252 |
| Momentum static k=200 | 3,553 | 90.3% | $75,632 | $21.29 | $252 |
| Momentum dynamic k (cap 600) | 3,870 | 91.1% | $79,990 | $20.67 | $256 |
| **+ Mean reversion (combined)** | **4,534** | **90.9%** | **$108,215** | **$23.87** | $306 |
| **+ min_obi=0.20** | **4,165** | **94.6%** | **$105,021** | **$25.22** | **$140** |

## What Each Change Did

- **Dynamic k (+$7K):** Adapts confidence with realized volatility. Quiet market → higher k → more confident. Volatile → lower k → fewer bad entries.
- **Mean reversion (+$28K):** Trades AGAINST momentum when OBI disagrees. 94.9% WR standalone. Captures the ~40% of windows momentum skips.
- **min_obi=0.20 (-$3K but halves DD):** Filters weak OBI signals. Cuts 369 low-quality trades. WR jumps from 90.9% to 94.6%. Max DD drops from $306 to $140.

## Exit Policy

Time_exit only. All other exits (stop_loss, take_profit, edge_gone) reduce PnL:

| Exit Policy | PnL |
|-------------|-----|
| time_exit only | $72,867 |
| + take_profit | $62,261 (-14%) |
| + conviction_stop | $74,606 (+2%) |
| + stop_loss 60% | ~$74,538 (+2%) |

## Side Breakdown (combined, no OBI gate)

| Side | Trades | WR | PnL |
|------|--------|-----|------|
| YES | 2,311 | 91.1% | $55,316 |
| NO | 2,223 | 90.7% | $52,899 |

Both sides equally good in backtest. Live YES underperformance is regime-specific.

## OBI Magnitude Effect (settlement-level WR)

| OBI Strength | Momentum WR | Mean Reversion WR |
|-------------|-------------|-------------------|
| Weak (<0.10) | 58.7% | 45.1% |
| Moderate (0.10-0.30) | 61.8% | 57.1% |
| Strong (0.30-0.50) | 72.4% | 65.3% |
| Very strong (0.50+) | 78.6% | 72.2% |

## Not Worth Pursuing

- **Serial correlation:** Windows are independent. 47.5% same as previous = no signal.
- **Momentum acceleration:** 99% of trades classify as accelerating. No differentiation.
- **LWM:** Previously disabled, didn't generate profits.
- **YES fade (retail overbet):** 48.6% WR, barely profitable. Not worth complexity.

## TODO: Implement

- [ ] Add `MIN_OBI=0.20` to .env and wire into both strategies
- [ ] Consider re-enabling YES side after paper validates combined strategy
- [ ] Paper-validate for 50+ trades before live deployment
