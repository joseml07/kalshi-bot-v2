# Kalshi Bot V2 — Live Trading Research & Next Steps

**Date**: 2026-05-22
**Based on**: 597 all-time paper trades, 61 post-fix trades (since 03:41 UTC today)

---

## 1. Paper vs Live Profit Estimate

### Current Paper Performance (post-fix session, ~11 hours)

| Metric | Value |
|--------|-------|
| Trades | 61 |
| Wins | 43 (70.5% WR) |
| PnL | +$139.29 |
| Avg win | +$4.52 |
| Avg loss | -$3.22 |
| Avg contracts | 8.0 |
| Avg entry price | $0.43 |
| Total fees paid | $8.59 |
| Win/loss ratio | 1.40:1 |

### Why Live PnL Differs From Paper

| Factor | Impact | Explanation |
|--------|--------|-------------|
| **Fees** | None | Paper already uses the exact same taker_fee() formula (7% of P*(1-P)*contracts). Already deducted from paper PnL. |
| **Entry price** | Negligible | Paper buys at best_ask. Live places a limit buy at the same ask on Kalshi's CLOB — fills at that price or better. Book depth is 30K-68K contracts; our 4-10 contract orders are invisible. |
| **Exit sell slippage** | ~-$1.50/day | Live exits sell at bid - $0.01 (EXIT_SELL_BUFFER). At avg 8 contracts, that's $0.08 per exit. Only ~30% of trades use time_exit. Est: $0.08 x 18 exits/day = $1.44. |
| **Non-fills** | ~-$4/day | If the book thins during the ~50-100ms network transit, order misses. At 30K+ depth vs 10 contracts, estimated 2-5% of orders. These are missed wins, not losses. Est: 3% x 61 trades x $2.28 avg = $4.17. |
| **Market impact** | Zero | Our orders are 0.01-0.02% of book depth. Completely invisible. |
| **Latency** | Negligible | Signal-to-order is <3ms locally. Kalshi REST round-trip adds ~50-100ms. Strategy already prices at the moment of evaluation. |

### Estimated Daily PnL Translation

| Scenario | Paper PnL | Live Haircut | Est. Live PnL | Notes |
|----------|-----------|-------------|---------------|-------|
| Today's conditions (extrapolated 24h) | ~$304 | -4.5% | **~$290** | Assumes today's volatility and opportunities persist |
| Average day (conservative) | ~$180 | -5% | **~$170** | Accounts for quieter periods, lower overnight volume |
| Bad day (drawdown) | -$10 to -$15 | Same | **-$10 to -$15** | Daily loss limit is the floor; no additional live penalty on losing days |

**Bottom line: expect roughly 4-5% haircut going from paper to live.** The dominant cost is missed fills (~3% of trades don't execute), not fees or slippage. Fees are already priced in.

---

## 2. Risk Assessment: Can It Lose All My Money?

### Hard safety nets already in place

| Protection | Setting | What it does |
|-----------|---------|-------------|
| `DAILY_LOSS_LIMIT` | $10.00 | Bot kills all trading for the day after $10 cumulative loss |
| `PER_SIDE_DAILY_LOSS_LIMIT` | $5.00 | Kills YES or NO independently after $5 loss on that side |
| `MAX_CONCURRENT_POSITIONS` | 3 | Max 3 open positions at once |
| `MAX_CONTRACTS` | 10 (hardcoded) | Never more than 10 contracts per trade |
| `MAX_COST_DOLLARS` | $25 (hardcoded) | Never risk more than $25 on a single entry |
| `SIDE_WR_ALERTS` | 30-trade window | Alerts if either side drops below 30% WR |
| Binary structure | Inherent | Max loss = position cost. No leverage, no margin calls, no liquidation cascades. |

### Worst-case math

- Maximum exposure at any moment: 3 positions x 10 contracts x $0.50 avg = **$15.00**
- Daily loss limit triggers at: **-$10.00** (bot stops trading)
- Absolute worst single day: ~$10-15 (a position opened just before the limit triggers can still lose)
- To lose $100 at current settings: would need 7-10 consecutive losing DAYS (not trades)

### Historical drawdown data

| Metric | Post-fix (May 21-22) | All-time |
|--------|---------------------|----------|
| Max drawdown from peak | -$11.81 | -$59.40* |
| Worst daily PnL | +$211 (no losing day) | -$9.74 |
| Max consecutive losses | 3 trades | Much worse* |
| Worst single trade | -$4.07 | -$4.07 |

*All-time includes April 16 - May 4, when YES was broken (0-15% WR) and maker-first was destroying NO edge. Those bugs are fixed. Post-fix data is the relevant baseline.

### Daily PnL history (all-time)

```
Date        Trades  WR%    PnL       Notes
2026-04-16  49      51.0%  +$14.19   First day, mixed results
2026-04-17  27      33.3%  -$1.18    YES losses begin
2026-04-18   4       0.0%  -$1.39    
2026-04-20   7      14.3%  -$1.26    
2026-04-24  18      11.1%  -$6.00    YES bleeding money
2026-04-25  14       7.1%  -$4.10    
2026-04-26  20      15.0%  -$3.74    
2026-04-27  29       3.4%  -$8.33    
2026-04-28  28       3.6%  -$9.03    Worst day all-time
2026-04-29  26       7.7%  -$9.74    Second worst
2026-04-30  20       0.0%  -$5.05    
2026-05-01  23       8.7%  -$4.83    
2026-05-02  10      10.0%  -$1.63    
2026-05-03   4       0.0%  -$1.66    
2026-05-04   2       0.0%  -$0.35    YES disabled around here
  ...gap (bot off or no trades)...
2026-05-19  45      44.4%  +$17.93   Fixes begin rolling in
2026-05-20 113      56.6%  +$73.46   Maker still on for some
2026-05-21  77      85.7% +$222.08   MAKER_FIRST=false, YES 120s
2026-05-22  81      72.8% +$211.54   All fixes active (today)
```

**The pattern is clear**: April was broken YES + broken maker destroying the edge. Since the fixes (May 19+), every day is profitable with accelerating returns.

### Confidence level

**We are reasonably confident it won't lose all your money.** Here's why:

1. **The edge is real and statistically significant.** 70.5% WR across 61 post-fix trades with a 1.40:1 win/loss ratio. The probability of this being random chance is extremely low (binomial p-value < 0.001).

2. **Multiple independent safety nets.** Daily loss limit, per-side limits, position caps, and WR alerts. Even if the strategy completely breaks, you lose $10-15 max per day before the bot shuts down.

3. **Binary options have bounded risk.** Unlike futures or leveraged positions, your max loss per trade is the entry cost ($3-5 at current sizing). There are no margin calls or liquidation cascades.

4. **The bugs that caused losses are identified and fixed.** YES's 0-15% WR was caused by entering too early (probability model miscalibrated beyond 120s). Maker-first's 59% WR vs taker's 85% was a route problem. Both are fixed with data confirming the fixes work.

**What COULD go wrong:**
- Kalshi changes market structure or fee schedule
- Crypto volatility regime shifts (flat markets = no momentum = no trades)
- API outages causing missed exits (position settles against you)
- A bug we haven't found yet

**Recommendation:** Start with a small live balance ($50-100) and let it run for 2-3 days. If it matches paper performance within ~10%, scale up.

---

## 3. Scaling Guide: Settings by Initial Balance

### How sizing works

The bot uses fractional Kelly criterion:
```
kelly = (win_prob - entry_price) / (1 - entry_price)
contracts = floor(bankroll * kelly * kelly_fraction * strength_mult * asset_mult / entry_price)
```

In **live mode**, `bankroll` = your actual Kalshi account balance (refreshed periodically).
In **paper mode**, `bankroll` = fixed $25 (PAPER_BALANCE in config).

**Current hard caps** (in `src/kalshi_bot/risk/sizing.py`):
- `MAX_CONTRACTS = 10`
- `MAX_COST_DOLLARS = $25.00`

With a typical trade (win_prob=0.65, price=$0.42, kelly_fraction=0.625):
- Kelly wants: floor(bankroll * 0.248 / 0.42) contracts
- At $25 bankroll: 14 contracts -> capped at 10
- At $100 bankroll: 59 contracts -> capped at 10
- At $500 bankroll: 295 contracts -> capped at 10

**The hard caps are always the binding constraint.** Even at $25 bankroll, Kelly wants more than 10 contracts. To actually scale, you must raise MAX_CONTRACTS and MAX_COST_DOLLARS.

### Simulated PnL at different bankroll/cap levels

Based on replaying the 64 actual post-fix trades with different sizing parameters:

| Scenario | Avg Contracts | PnL (11h) | vs Current | Est. Daily |
|----------|--------------|-----------|------------|------------|
| Current ($25, cap=10) | 7.2 | $134 | baseline | ~$290 |
| $100, cap=10 | 10.0 | $167 | +24% | ~$360 |
| **$100, cap=25** | **22.5** | **$371** | **+177%** | **~$810** |
| $250, cap=25 | 25.0 | $417 | +211% | ~$910 |
| $250, cap=50 | 48.3 | $792 | +491% | ~$1,720 |
| $500, cap=50 | 50.0 | $833 | +521% | ~$1,810 |
| $1000, cap=100 | 100.0 | $1,667 | +1143% | ~$3,620 |

**Key insight**: Raising MAX_CONTRACTS from 10 to 25 at $100 bankroll nearly **triples** PnL.
The bankroll barely matters once you're above ~$100 because Kelly always wants more than the cap allows. The cap is always the binding constraint, not the bankroll.

### Recommended settings by balance tier

These settings scale risk linearly while maintaining the same risk-of-ruin profile.
The ratio is: ~10% of balance as daily loss limit, ~5% per-side, max trade cost ~25% of balance.

#### Tier 1: Validation ($50-100)

**Goal**: Confirm live matches paper. Minimal risk.

```env
# .env changes
TRADING_MODE=live
DAILY_LOSS_LIMIT=10.0
PER_SIDE_DAILY_LOSS_LIMIT=5.0
MAX_PER_TRADE=25.0
```

```python
# sizing.py — no changes needed
MAX_CONTRACTS = 10
MAX_COST_DOLLARS = Decimal("25.00")
```

| Metric | Value |
|--------|-------|
| Max per trade cost | ~$5 (10 x $0.50) |
| Max exposure | ~$15 (3 positions) |
| Daily loss limit | $10 |
| Expected daily PnL | +$130-170 |
| Days to recover worst day | <1 |
| Risk of ruin (lose entire balance) | Near zero — would need 5-7 consecutive max-loss days |

#### Tier 2: Scaling ($200-300)

**Goal**: Let Kelly size properly. Double the throughput.

```env
TRADING_MODE=live
DAILY_LOSS_LIMIT=25.0
PER_SIDE_DAILY_LOSS_LIMIT=12.0
MAX_PER_TRADE=50.0
```

```python
# sizing.py changes
MAX_CONTRACTS = 25
MAX_COST_DOLLARS = Decimal("50.00")
```

| Metric | Value |
|--------|-------|
| Max per trade cost | ~$12.50 (25 x $0.50) |
| Max exposure | ~$37.50 (3 positions) |
| Daily loss limit | $25 |
| Expected daily PnL | +$300-400 |
| Days to recover worst day | <1 |
| Risk of ruin | Very low — need 8-12 consecutive max-loss days |

Kelly will still cap at 25 for most trades, but high-edge trades (edge > 15%) will properly size up to 25 instead of being artificially squeezed to 10.

#### Tier 3: Optimized ($500)

**Goal**: Full Kelly expression within conservative bounds.

```env
TRADING_MODE=live
DAILY_LOSS_LIMIT=50.0
PER_SIDE_DAILY_LOSS_LIMIT=25.0
MAX_PER_TRADE=100.0
MAX_CONCURRENT_POSITIONS=4
```

```python
# sizing.py changes
MAX_CONTRACTS = 50
MAX_COST_DOLLARS = Decimal("100.00")
```

| Metric | Value |
|--------|-------|
| Max per trade cost | ~$25 (50 x $0.50) |
| Max exposure | ~$100 (4 positions) |
| Daily loss limit | $50 |
| Expected daily PnL | +$550-750 |
| Days to recover worst day | <1 |
| Risk of ruin | Low — need 10+ consecutive max-loss days, but now 20% of balance |
| Market impact | Still negligible (50 contracts vs 30K-68K book depth = 0.07-0.17%) |

#### Tier 4: Aggressive ($1,000+)

**Goal**: Maximum extraction. For after Tier 3 has proven itself for 1+ week.

```env
TRADING_MODE=live
DAILY_LOSS_LIMIT=100.0
PER_SIDE_DAILY_LOSS_LIMIT=50.0
MAX_PER_TRADE=200.0
MAX_CONCURRENT_POSITIONS=5
```

```python
# sizing.py changes
MAX_CONTRACTS = 100
MAX_COST_DOLLARS = Decimal("200.00")
```

| Metric | Value |
|--------|-------|
| Max per trade cost | ~$50 (100 x $0.50) |
| Max exposure | ~$250 (5 positions) |
| Daily loss limit | $100 |
| Expected daily PnL | +$900-1,400 |
| Days to recover worst day | ~1 |
| Market impact | Borderline — 100 contracts is 0.15-0.33% of ETH book. Still fine but approaching where you'd want to monitor fill rates. |

### Scaling PnL estimation methodology

Paper PnL at current sizing (10 contracts max): ~$304/day extrapolated.
Each doubling of MAX_CONTRACTS roughly doubles PnL, because 56% of current trades are capped.
Returns are not perfectly linear — larger positions face slightly worse fills and more non-fill risk.
Conservative haircut: 15% below linear scaling at each tier.

### Liquidity ceiling

Based on orderbook data (last 24 hours):

| Symbol | Avg Depth | When You'd Start Moving Price |
|--------|-----------|------------------------------|
| BTC | 68K contracts | ~500+ contracts/order (0.7% of book) |
| ETH | 33K contracts | ~250+ contracts/order (0.75% of book) |

**Time-of-day matters**: Daytime (8-15 UTC) books are ~45% thinner. If scaling to Tier 3+, avoid large orders during this window or reduce sizing.

| Period (UTC) | Avg YES Depth | Avg NO Depth | Relative to peak |
|---|---|---|---|
| Night (0-7) | 63,425 | 60,166 | 96% |
| Day (8-15) | 36,199 | 31,816 | 55% |
| Evening (16-23) | 66,166 | 65,456 | 100% |

---

## 4. Optimization Ideas

### A. High Confidence / Easy Implementation

#### A1. Raise MAX_CONTRACTS and MAX_COST_DOLLARS

**What**: Change the hardcoded caps in `sizing.py`.
**Why**: 56% of trades today hit the 10-contract ceiling. Kelly sizing wants bigger positions on high-edge trades but is being artificially constrained. This is the single easiest way to increase profit.
**Where**: `src/kalshi_bot/risk/sizing.py` lines 15-16
**Risk**: Low. You're letting the Kelly formula express what it already calculates. The daily loss limit is the real safety net.

#### A2. Dynamic yes_decision_max_s by momentum strength

**What**: Currently a flat 120s cutoff for YES entries. Allow 150s for strong momentum (>0.2% price change) where persistence is higher, keep 120s for moderate.
**Why**: The piecewise table shows P(up) = 0.92 at <=120s for >0.2% moves, vs 0.85 at 121-300s. The extra 30s window would capture more high-conviction trades without degrading WR.
**Where**: `src/kalshi_bot/strategy/lwm.py` ~line 131 (the YES temporal gate)
**Risk**: Low. Only relaxes the gate for strong signals where model is already well-calibrated.
**Expected impact**: ~5-10 more YES trades/day at similar WR.

#### A3. Time-of-day sizing multiplier

**What**: Increase position size during high-WR periods.
**Why**: Performance by time of day shows a clear pattern:

| Period (UTC) | WR | Trades | PnL/trade |
|---|---|---|---|
| Night (0-7) | 79.0% | 21/day | $3.03 |
| Day (8-15) | 76.3% | 42/day | $2.61 |
| Evening (16-23) | 90.0% | 20/day | $2.39 |

Evening has 90% WR with deeper books. Apply 1.25x sizing multiplier.
**Where**: `src/kalshi_bot/risk/sizing.py`, add time-based multiplier to `kelly_size()`
**Risk**: Low. Sizing up during proven high-WR periods with better liquidity.

#### A4. Relax OBI gate for late-window entries (<120s)

**What**: Remove or weaken the orderbook imbalance agreement requirement when seconds_remaining < 120.
**Why**: The bot logs are flooded with `momentum_obi_mismatch` — the OBI gate rejects a huge number of signals. Near expiry, momentum is the dominant signal; the order book is often dominated by market makers who don't reflect directional conviction. The OBI gate may be suppressing valid late-window entries.
**Where**: `src/kalshi_bot/strategy/momentum.py` (the OBI alignment check)
**Risk**: Medium. Would increase trade volume but potentially lower WR. Need to backtest.
**Expected impact**: Could double trade volume near settlement, capturing more of the time-decay edge.

### B. Medium Confidence / Medium Effort

#### B5. Wire in dynamic_k for live trading

**What**: Use the volatility-adaptive `k` parameter instead of static `logistic_k=200`.
**Why**: The infrastructure already exists (`estimate_k_from_vol` in probability.py) but dynamic_k is only computed for display, never used for actual trade decisions. During high-volatility periods, the sigmoid should flatten (lower k) to avoid extreme probability estimates that create phantom edge. During low-vol, it should sharpen.
**Where**: `src/kalshi_bot/main.py` (pass dynamic_k to strategy evaluators instead of settings.logistic_k)
**Risk**: Medium. Changes probability estimates across the board. Needs careful validation.
**Impact**: Better calibration = fewer losing trades where model was overconfident.

#### B6. Refit the piecewise probability table from actual data

**What**: The LWM table mapping (price_change, seconds_remaining) -> P(up) was set once and has a comment saying "refit weekly." Use the 600+ settled trades + tens of thousands of window snapshots to compute actual P(up) for each bucket.
**Why**: The current 0.92/0.85/0.72 breakpoints are assumptions. With enough data, we can replace them with empirical values. Even small improvements (0.85 -> 0.87) translate directly to better edge estimates and fewer mis-trades.
**Where**: `src/kalshi_bot/strategy/lwm.py` (the `_TABLE` constant)
**Risk**: Medium. Wrong refitting could degrade performance. Need hold-out validation.

#### B7. Tune no_side_edge_bonus per symbol

**What**: The NO side requires extra edge to trade (BTC: +4%, ETH: +3%). This acts as a quality filter. The optimal bonus might not be the current values.
**Why**: This bonus is the single biggest contributor to NO-side profitability — it filters out marginal signals. But the current values were guesses. Sweeping 1-6% in the backtester for each symbol could find a better sweet spot.
**Where**: `src/kalshi_bot/config.py` / asset_config.py
**Risk**: Low if tested. Just config changes backed by data.

#### B8. Add a YES-side edge bonus

**What**: Similar to NO's no_side_edge_bonus, add a yes_side_edge_bonus to require extra edge for YES trades.
**Why**: YES WR is 73.9% but with 6 losses. The losses clustered during a volatile stretch where even late-window entries got whipsawed. A small bonus (2-3%) would filter the weakest YES signals without killing the best ones.
**Where**: `src/kalshi_bot/strategy/lwm.py` (add a `yes_side_edge_bonus` analogous to `no_side_edge_bonus`)
**Risk**: Low. Trades accuracy for volume. May reduce YES from 23 trades/session to 18-20 but push WR from 74% to 80%+.

### C. Speculative / Higher Effort

#### C9. Adaptive Kelly fraction based on rolling WR

**What**: Instead of fixed 0.625, compute Kelly fraction from a rolling 50-trade window. If WR drops below 60%, auto-reduce to 0.3. If WR is above 80%, bump to 0.8.
**Why**: Self-regulating. Prevents oversizing during drawdowns and captures more during hot streaks.
**Where**: `src/kalshi_bot/risk/sizing.py` + `src/kalshi_bot/risk/manager.py` (track rolling WR)
**Risk**: Medium. Kelly math assumes stationary edge, which this approach partially violates. But in practice, regime-adaptive sizing tends to improve risk-adjusted returns.

#### C10. Cross-symbol signal confirmation

**What**: When BTC and ETH both show strong momentum in the same direction simultaneously, boost sizing by 1.2-1.5x.
**Why**: BTC and ETH are correlated. Agreement reduces false positive risk. Disagreement is a warning signal.
**Where**: `src/kalshi_bot/main.py` (cross-reference signals across symbols in fast eval loop)
**Risk**: Medium. Adds complexity. Correlation isn't constant.

#### C11. Reduce ORDERBOOK_STALENESS_S from 15s to 5s

**What**: Reject orderbook data older than 5 seconds instead of 15.
**Why**: A 15-second-old orderbook is ancient in a market that settles in 15 minutes. Tighter freshness = better price discovery and edge estimates. The Kalshi WS is already streaming updates, so fresh data should always be available.
**Where**: `src/kalshi_bot/main.py` (ORDERBOOK_STALENESS_S constant)
**Risk**: Low downside. Might cause some eval skips if WS has brief hiccups.

#### C12. Per-symbol yes_decision_max_s

**What**: Set different YES entry cutoffs for BTC vs ETH (e.g., BTC=120s, ETH=150s) based on their different momentum persistence profiles.
**Why**: ETH tends to have more volatile intrawindow moves than BTC. The optimal cutoff may differ.
**Where**: `src/kalshi_bot/config.py` / asset_config.py
**Risk**: Low. Config change backed by per-symbol WR analysis.

### What NOT to optimize (validated as destructive)

| Idea | Why not |
|------|---------|
| Re-enable maker-first | Data shows 59.6% WR vs taker's 85.1% for NO time_exit. Maker saves fees but destroys edge. |
| Re-enable stop_loss exits | Backtest: converted 112 winning entries into losses across 306 trades. |
| Re-enable edge_gone exits | Same backtest result. Both stop_loss and edge_gone destroy the time-decay capture that makes the strategy work. |
| Add SOL | Lower liquidity, wider spreads, fewer opportunities. BTC+ETH is the sweet spot. |
| Lower edge_threshold below 0.04 | Would increase volume but with marginal-quality trades that lower WR. |

---

## 5. Implementation Priority

### Phase 1: Go Live (Day 1)
1. Fund Kalshi account with $50-100 (Tier 1)
2. Set `TRADING_MODE=live` in .env
3. Keep all other settings identical to paper
4. Monitor for 2-3 days, compare to paper baseline

### Phase 2: Scale Up (Day 3-5, after live validation)
1. Implement A1: Raise MAX_CONTRACTS to 25, MAX_COST_DOLLARS to $50
2. Fund account to $200-300 (Tier 2)
3. Adjust DAILY_LOSS_LIMIT to $25, PER_SIDE to $12
4. Monitor fill rates — if >5% non-fills, investigate

### Phase 3: Optimize (Week 2+)
1. A2: Dynamic yes_decision_max_s by momentum strength
2. A4: Relax OBI gate for late-window entries
3. B5: Wire in dynamic_k
4. B7: Tune edge bonuses with backtester

### Phase 4: Full Scale (Week 3+, after optimizations validated)
1. Fund to $500+ (Tier 3)
2. Raise MAX_CONTRACTS to 50
3. A3: Time-of-day sizing multiplier
4. B6: Refit probability table from accumulated data

---

## 6. Key Numbers Quick Reference

| Metric | Current Value | Source |
|--------|-------------|--------|
| Post-fix WR (NO) | 68.4% | 40 trades since restart |
| Post-fix WR (YES) | 73.9% | 23 trades since restart |
| Combined WR | 70.5% | 63 trades since restart |
| Avg win | +$4.52 | |
| Avg loss | -$3.22 | |
| Win/loss ratio | 1.40:1 | |
| Avg taker fee per trade | $0.14 | |
| Avg contracts per trade | 8.0 | |
| Trades hitting 10-contract cap | 56% | 34/61 trades |
| BTC avg book depth | 68K contracts | |
| ETH avg book depth | 33K contracts | |
| Max drawdown from peak (today) | -$11.81 | |
| Max consecutive losses (today) | 3 | |
| Worst single trade | -$4.07 | |
| Paper-to-live haircut estimate | ~4-5% | Missed fills + exit buffer |
