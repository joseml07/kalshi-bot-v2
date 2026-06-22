# Edge Analysis & Strategy Theorizing

Analysis of trades.db (2,754 trades, 2.3M signal evals, Apr 16 – Jun 17, 2026) to determine if a profitable BTC 15-min Kalshi bot is possible and what edge sources exist.

---

## 1. The Brutal Truth: Regime-Dependent Edge

The bot's momentum strategy has a cumulative +$304 P&L, but this is deceptive. The entire profit comes from a 10-day golden period.

### Monthly P&L

| Month | Trades | P&L | Win Rate |
|-------|--------|-----|----------|
| Apr 2026 | 115 | +$12 | 29.6% |
| **May 2026** | 755 | **+$523** | 52.2% |
| Jun 2026 (17d) | 1,044 | **-$231** | 46.0% |

The bot peaked at **+$561 on May 25** and has since given back $257 (46% drawdown from peak).

### The Regime Split (NO-side, Momentum, BTC)

| Period | Trades | Win Rate | P&L | Avg Entry | Avg Edge |
|--------|--------|----------|-----|-----------|----------|
| Pre-May 25 | 409 | **64.1%** | **+$554** | 0.425 | 0.125 |
| Post-May 25 | 683 | **45.4%** | **-$92** | 0.465 | 0.129 |

Entry edge and price were nearly identical between the two periods. **The signal didn't change — the market structure did.** During the golden period, BTC ranged between $74K-$78K. After May 25, BTC crashed from $77K to $59K with daily ranges expanding from 1.2-3.3% to 4-8%.

### Why NO at >$0.55 Is Mathematically Hopeless

When buying NO at $0.61 with a 59% win rate:
- Win: $1.00 - $0.61 - fees ≈ +$0.35
- Loss: -$0.61

Even at 59% WR: (0.59 × $0.35) - (0.41 × $0.61) = $0.207 - $0.250 = **-$0.043 per trade**. The profit ceiling is too low above $0.55 — you need a >66% win rate just to break even.

---

## 2. Kelly Sizing: The Hidden Destroyer

Post-peak BTC NO-side by contract size:

| Contracts | Trades | Win Rate | P&L |
|-----------|--------|----------|-----|
| 1 | 119 | 42.9% | -$7.84 |
| 2 | 44 | 54.5% | +$2.98 |
| 3 | 28 | 53.6% | -$1.42 |
| 4 | 28 | 50.0% | +$1.27 |
| 5-9 | 70 | ~40% | -$28.34 |
| **10 (max)** | **25** | **24.0%** | **-$40.36** |

**The Kelly criterion is sizing into losing trades.** Pre-peak, edge >0.10 meant 70.5% WR and max sizing was genius. Post-peak, edge >0.10 means 39.7% WR and max sizing is a disaster.

### Counterfactual: Capped Sizing

| Sizing | Pre-peak P&L | Post-peak P&L |
|--------|-------------|---------------|
| Uncapped Kelly | +$365.68 | -$105.68 |
| Max 3 contracts | +$150.14 | -$49.67 |
| Fixed 1 contract | +$52.77 | **-$24.44** |

With fixed 1-contract, the bot is essentially flat post-crash (-$24 on 590 trades, ~-$0.04/trade). Kelly takes a flat strategy and turns it into a loser by concentrating risk on a broken signal.

---

## 3. OBI Crowd-Fading: The Most Robust Signal

Order Book Imbalance (yes_depth / total_depth), BTC NO-side:

### Pre-peak

| OBI Regime | Trades | Win Rate | P&L |
|------------|--------|----------|-----|
| Very strong NO skew (OBI<0.15) | 199 | 78.9% | +$389 |
| Strong NO skew (0.15-0.25) | 55 | **92.7%** | +$161 |
| Moderate NO skew (0.25-0.35) | 82 | 82.9% | +$244 |
| Balanced | 362 | 61.9% | +$568 |
| Strong YES skew (0.65-0.85) | 92 | 57.6% | +$111 |

The more extreme the crowd is buying YES (low OBI ratio), the better fading them with NO performs. Clean monotonic relationship.

### Post-peak

| OBI Regime | Trades | Win Rate | P&L |
|------------|--------|----------|-----|
| Very strong NO skew | 6 | 0.0% | -$17 |
| Strong NO skew | 21 | 47.6% | -$23 |
| Moderate NO skew (0.25-0.35) | 94 | **62.8%** | -$6 |
| Balanced | 1,052 | 42.2% | -$263 |
| Strong YES skew | 112 | 44.6% | +$4 |
| Very strong YES skew (0.85+) | 11 | 63.6% | +$0.12 |

The OBI signal degraded post-peak but **still holds positive win rates** at extremes. The fade-the-panic signal (bet NO when the crowd frantically buys YES at moderate prices) was near-breakeven even during the crash.

### Fade Strategy Post-Peak

| OBI Fade Signal | Side | Trades | Win Rate | P&L |
|-----------------|------|--------|----------|-----|
| Crowd buys YES → bet NO | NO | 121 | 57.0% | -$45.79 |
| Crowd buys YES → bet NO (price 0.45-0.55) | NO | 29 | **65.5%** | **+$15.80** |
| Crowd buys YES → bet NO (price >0.55) | NO | 65 | **70.8%** | +$0.21 |

When the orderbook is stacked with YES buyers AND price is moderate (0.45-0.55), fading wins 2/3 of the time even post-crash. At high prices (>$0.55), the win rate is even higher (70.8%) but profit margin is razor-thin due to the payout cap.

---

## 4. Window-to-Window Momentum

BTC 15-min windows show extreme short-term autocorrelation (post-peak data):

| Prior Window | Next Up% | Next Down% | Avg Move |
|-------------|----------|------------|----------|
| Strong down (>0.2%) | 0.2% | **99.8%** | -0.67% |
| Weak down | 0.1% | **99.9%** | -0.21% |
| Flat (±0.05%) | 48.5% | 50.6% | ~0.00% |
| Weak up | **99.1%** | 0.1% | +0.20% |
| Strong up (>0.2%) | **98.8%** | 0.2% | +0.69% |

Window-level momentum is highly predictive. However, the Kalshi market almost certainly prices this in — after a strong up window, the YES price for the next window is likely already >$0.90, leaving no edge after fees.

### Window Distribution

~82-93% of all 15-min BTC windows are "flat" (±0.2%). Only 8-18% have meaningful directional moves. This means:
- Most of the time, betting NO is directionally correct (BTC doesn't go up)
- But the Kalshi market knows this and prices YES accordingly low
- The bot's edge only exists when the Kalshi price disagrees with the true flat probability

---

## 4.5 Post-Entry Reversion: The Mechanism That Died

### Slippage Analysis (directional, seconds after entry)

| Period | Side | Mid Slippage | Directional Move | Interpretation |
|--------|------|-------------|------------------|----------------|
| Pre-peak | NO | -$0.20 | **+$0.26** | Price reverted in our favor |
| Pre-peak | YES | -$0.26 | **+$0.26** | Price reverted in our favor |
| Post-peak | NO | -$0.06 | **-$0.007** | Flat — no reversion |
| Post-peak | YES | +$0.006 | **-$0.006** | Flat — no reversion |

Pre-peak, entry caused a temporary price dislocation that immediately reverted. The bot was essentially getting compensated for providing liquidity even though it used market orders. Post-peak, the reversion stopped — the price moved to a new equilibrium and stayed there. **This is the mechanism-level explanation of the regime shift**: the market stopped being mean-reverting at the sub-minute timescale.

### Win/Loss Magnitude Distribution (Post-Peak BTC NO)

| Category | Trades | P&L | Avg Contracts | Avg Entry |
|----------|--------|-----|---------------|-----------|
| Huge loss (<-$2) | 127 | -$103.79 | 2.3 | $0.454 |
| Big loss (-$2 to -$0.50) | 51 | -$147.00 | **7.5** | $0.430 |
| Small win ($0 to $0.50) | 42 | +$14.14 | 1.4 | $0.517 |
| Big win ($0.51 to $2) | 69 | +$75.38 | 2.9 | $0.500 |
| Huge win (>$2) | 25 | +$87.56 | **7.2** | $0.422 |

The distribution is bimodal: trades are either small (1-3 contracts, near breakeven) or large (7-10 contracts, high variance net loser). Kelly creates a harmful bimodal distribution where the 7.5-contract losers (-$147) outweigh the 7.2-contract winners (+$87).

### Entry Timing Within Window

Post-peak BTC NO by seconds remaining:

| Entry Timing | Trades | Win Rate | P&L |
|-------------|--------|----------|-----|
| Mid-window (5-10m left) | 220 | **48.6%** | -$22.37 |
| Late (2-5m left) | 87 | 34.5% | -$38.29 |
| Very late (1-2m left) | 7 | 14.3% | -$13.05 |

Pre-peak BTC NO contrast:
| Mid-window (5-10m) | 221 | 49.8% | +$331.73 |
| Late (2-5m) | 21 | **71.4%** | +$36.34 |

Post-peak, entering later in the window is disastrous (14-35% WR) — mean reversion needs time to work and it no longer does. Pre-peak, late entries were the most profitable (71.4% WR) — reversion was so strong it worked with <5 minutes remaining.

### Spread vs Outcome (Post-Peak BTC)

| Spread | Trades (NO) | Win Rate | P&L |
|--------|------------|----------|-----|
| Tight (<0.02) | 646 | 44.7% | -$158.55 |
| Medium (0.02-0.05) | 38 | 28.9% | -$9.46 |

The overwhelming majority of trades occur in tight spreads. Medium spread trades have significantly worse outcomes, suggesting wider spreads signal adverse conditions. Pre-peak, tight spread NO was 70% WR with +$743.

### BTC vs ETH: Opposite Crowd Dynamics

Post-peak NO-side, fading the crowd:

| Asset | Strategy | Trades | Win Rate | P&L |
|-------|----------|--------|----------|-----|
| **BTC** | Fade crowd (crowd=YES, bet=NO) | 127 | 46.3% | **+$4.10** |
| BTC | Follow crowd (crowd=NO, bet=NO) | 121 | 57.0% | -$45.79 |
| **ETH** | Fade crowd (crowd=YES, bet=NO) | 276 | 54.3% | -$59.21 |
| ETH | Follow crowd (crowd=NO, bet=NO) | 309 | 46.0% | **+$38.61** |

BTC and ETH behave oppositely. On BTC, fading the crowd works; on ETH, following the crowd works. A per-asset OBI strategy would need to be tuned separately for each symbol.

### Cross-Asset Buddy Agreement Rate

Number of simultaneous same-direction signals for BTC+ETH (within 60s):

| Period | Buddy Signals | Avg Edge |
|--------|--------------|----------|
| Pre-peak | 215 (BTC↔ETH) | 0.131 |
| Post-peak | **93** | 0.135 |

The buddy agreement rate dropped by **57%** post-peak. BTC and ETH decoupled during the crash, making cross-asset confirmation rarer and less useful as a filter.

---

## 5. Novel Edge Sources (Quant-Inspired)

### 5.1 Flow Toxicity Detection (VPIN-style)

Track large order hits on the Kalshi orderbook. If someone consistently buys YES aggressively right before BTC pumps, they're informed. Build a toxicity score:
- Monitor orderbook for large marketable orders (>$500 notional)
- Track correlation between large YES buys and subsequent BTC price moves
- When toxicity is high (informed traders present), either follow them or stay out
- When toxicity is low (noise traders), fade the crowd

### 5.2 Spot Volume Breakout Confirmation

Instead of reacting to Kalshi prices, react to Coinbase spot volume:
- Track rolling median volume per 15-min window
- When spot volume hits 3x median, it often precedes a directional move
- Only take trades when there's "real" spot volume backing the direction
- This filters out noise entries during low-volume chop

### 5.3 Fade the Panic (Pure Behavioral)

A standalone strategy requiring no edge calculation:
- Monitor OBI (yes_depth / total_depth)
- When OBI < 0.25 (crowd frantic buying YES) → bet NO
- When OBI > 0.75 (crowd frantic buying NO) → bet YES
- Only enter when price is 0.40-0.55 (decent risk/reward)
- Fixed 1-contract size
- Post-crash performance on this signal alone: 63.6% WR at extremes

### 5.4 BTC Range Percentile Regime Gate

The simplest and highest-impact fix:
- Track BTC's rolling 4-hour high-low range as % of price
- Compute percentile vs last 30 days
- When range percentile < 50 (calm/ranging): trade normally
- When range percentile > 75 (trending/crashing): go flat
- During the golden period: most days were in the bottom quartile
- During the crash: most days were in the top quartile

### 5.5 Cross-Timeframe Vol Arb

Kalshi has BTC contracts at multiple timeframes (15min, 1hr, 4hr, daily):
- The 15-min implied vol should be consistent with longer timeframe vols
- If the 15-min YES price implies higher vol than the 1hr YES price implies, the 15-min contract is overpriced → bet NO
- This is essentially a vol term-structure arbitrage
- Requires multiple timeframe data but has no directional exposure

### 5.6 Deribit Options Vol Comparison

Deribit BTC options provide an independent vol estimate:
- ATM 0-DTE options give a market-implied probability for short-term moves
- If Deribit implies a 15-min move probability of 0.45 but Kalshi YES is at 0.52, bet NO
- This is a cross-venue vol arb
- Requires Deribit data feed (available via their WebSocket API)

### 5.7 Exchange Flow Imbalance

Crypto exchange net flows are leading indicators:
- Large BTC inflows to exchanges → upcoming selling pressure
- Large BTC outflows from exchanges → upcoming buying pressure
- Gate all trades by flow direction: if inflows are high, only allow NO bets; if outflows are high, only allow YES bets
- Data available from Glassnode, CryptoQuant, or on-chain APIs

### 5.8 Capped Sizing with Regime-Aware Multiplier

Replace Kelly with a regime-aware sizing scheme:
- Base size: 1 contract
- Multiplier based on regime confidence (not edge magnitude):
  - Low vol regime + OBI confirmation + flat momentum: 2-3 contracts
  - Any single signal missing: 1 contract
  - High vol regime: flat (0 contracts)
- This removes the "sizing into losers" problem while still scaling up when conditions align

### 5.9 Settlement Avoidance

Settlement exits cost -$169 total (0.9% win rate). The bot must NEVER hold to settlement:
- Exit at T-60s instead of T-30s (current time_exit)
- If price is unfavorable at T-120s, exit immediately (cut losses early)
- The data shows: winning time_exits at ~$0.48 avg entry, losing at ~$0.45 — the edge is small enough that waiting longer doesn't help

### 5.10 The 3-Contract Max Rule

The simplest change with highest impact:
- Cap ALL positions at 3 contracts regardless of edge
- This alone turns post-crash from -$106 to -$50
- Combined with a vol gate, the strategy likely becomes net profitable

### 5.11 Self-Detecting Reversion Detector

The most adaptive approach: let the bot detect in real-time whether the strategy still works:
- For each closed trade, compute `(exit_price - entry_price) * sign(edge)` — was the directional bet correct?
- Track rolling win rate over last N trades (e.g., N=50)
- When rolling WR > 55%: trade normally (reversion is active)
- When rolling WR < 45%: pause trading (reversion is dead, regime shifted)
- When rolling WR 45-55%: trade at 1-contract max (uncertain regime)
- This self-calibrates to any regime without needing to identify WHY conditions changed

### 5.12 Volatility Is Not Enough — Need Directional Regime

The data shows the bot lost money on calm days too (June 12: BTC range 2.5%, lost -$27; June 13: BTC range 2.0%, lost -$1). A pure volatility gate would not have caught these. What matters is whether the market is **mean-reverting** or **trending/drifting**, not just how volatile it is. Potential leading indicators:
- **Hurst exponent** over rolling windows (H<0.5 = mean-reverting, H>0.5 = trending)
- **Autocorrelation of 15-min returns** (positive = trending, negative = mean-reverting)
- **Variance ratio test** (ratio of long-horizon variance to short-horizon variance)
- **Number of consecutive same-direction windows** (streak length >3 = trending regime)

### 5.13 Per-Asset Crowd Dynamics

BTC crowd is contrarian (fade them), ETH crowd is momentum (follow them). This might reflect different trader populations on each market. A unified strategy should:
- On BTC: bet against extreme OBI readings (fade the crowd)
- On ETH: bet with extreme OBI readings (follow the crowd)
- Size independently per asset based on each strategy's rolling performance

### 5.14 Real-Probability Trustworthiness Score

The real_prob from the Coinbase model was the core of the edge pre-peak but became unreliable post-peak. Build a trustworthiness score:
- Track `|real_prob - actual_outcome|` on a rolling basis
- When the model's prediction error is low: weight the edge signal heavily
- When prediction error is high: reduce position size or rely on OBI-only
- This prevents the bot from betting on a broken probability model

---

## 6. SOL Meme Coins: Why Not

- No structural edge — you're competing against insiders, snipers, and MEV searchers
- No data to backtest — you can't quantify if a strategy has positive expectancy
- 99% of meme coins go to zero — the winners are lottery tickets
- Tax complexity — every swap is a taxable event
- Latency arms race — the bots winning this game have microsecond execution

**Prediction markets have a genuine structural edge** — the crowd is systematically biased. The question is whether the edge is large enough to overcome fees and whether you can detect when it's real vs. noise.

---

## 7. Practical Path Forward

### Immediate (Zero New Code)
1. **Cap all positions at 3 contracts max** — reduces post-crash drawdown from -$106 to -$50
2. **Kill YES-side entirely** — YES has never been profitable in any period; NO-only saves $157
3. **Exit at T-60s minimum, with T-120s early exit if underwater** — late entries (<2m remaining) have 14-35% WR

### Short-term (Week of Work)
1. **Build self-detecting reversion detector** — track rolling N-trade win rate; pause when <45%; this is more adaptive than fixed vol gates
2. **Add per-asset OBI strategy** — BTC fades the crowd, ETH follows the crowd
3. **Run pure OBI fade strategy** as parallel paper signal (no edge model needed, single contract)
4. **Add Hurst exponent or autocorrelation regime gate** — detect trending vs mean-reverting, not just volatile vs calm

### Medium-term (Explore)
1. Integrate Deribit vol data for cross-venue edge confirmation
2. Build flow toxicity monitor on Kalshi orderbook (VPIN-style)
3. Cross-timeframe vol arb between 15-min and 1hr Kalshi contracts
4. Real-probability trustworthiness score to weight edge signal dynamically

### If All Else Fails
The Kalshi 15-min BTC market may be too efficient at current scale. The edge is real but conditional and thin. Consider:
- **Longer timeframes** (1hr, 4hr) where behavioral biases are larger and fees matter less
- **Different assets** where the crowd is less sophisticated (non-crypto Kalshi markets?)
- **Pure OBI approach** — drop the Coinbase probability model entirely and trade only orderbook extremes
- **Market making** — provide liquidity during tight-spread regimes instead of taking it

### What We Know For Certain
- The edge exists but is regime-dependent (mean-reverting markets only)
- Kelly sizing is counterproductive when the signal is noisy
- OBI crowd-fading is the single most robust signal across all regimes
- The post-entry reversion effect is the mechanism-level indicator of whether the strategy will work
- Settlement must be avoided at all costs
- YES-side has negative expectancy in all conditions tested

---

## 8. Key Numbers

| Metric | Value |
|--------|-------|
| Total trades | 2,754 |
| Total gross P&L | +$249.34 |
| Total fees paid | -$191.19 |
| P&L after fees | **+$58.15** |
| Best day | May 21: +$222.08 |
| Worst day | Jun 5: -$46.58 |
| Best single trade | Jun 12: +$7.25 (ETH NO, 10ct, $0.25 entry) |
| Worst single trade | Jun 3: -$0.12 (BTC NO, 1ct) |
| Settlement WR | 0.9% |
| Time exit WR | 51.0% |
| BTC NO WR (all-time) | 53.8% |
| BTC YES WR (all-time) | 18.7% |
| Kelly 10ct WR (post-peak) | 24.0% |
| OBI fade WR (extreme, post-peak) | 63.6% |
| Post-entry reversion (pre-peak) | +$0.26/trade in our favor |
| Post-entry reversion (post-peak) | -$0.007/trade (flat/dead) |
| Buddy agreement drop (post-peak) | -57% (215→93 same-direction signals) |
| BTC 1ct fixed-size P&L (post-peak) | -$24.44 (essentially flat) |
| ETH NO avg P&L (post-peak) | -$0.05/trade (nearly breakeven) |
| BTC NO avg P&L (post-peak) | -$0.23/trade |
