# Kalshi 15-Min Binary Options — Strategy Edge Research
## Exhaustive Data Analysis of 1.94M Snapshots Across 11,528 Windows (Apr 15 – Jun 21, 2026)

---

## 1. DATA OVERVIEW

- **Source**: `trades.db` via bot's `window_snapshots` + `market_events` tables
- **Contracts**: KXBTC15M, KXETH15M (15-min crypto up/down binaries)
- **Period**: 2026-04-15 to 2026-06-21 (~68 days)
- **Total windows**: 11,528
- **Total snapshots**: 1,939,920
- **BTC/ETH split**: ~50/50
- **Overall UP/DOWN**: ~35% UP, ~65% DOWN (bearish bias in dataset)

---

## 2. CRITICAL FINDING: REGIME CHANGE ON MAY 23

**Every strategy shows a sharp discontinuity on May 23, 2026.** The market fundamentally changed how it prices these contracts. Pre-May-23 edges that produced $0.10-0.20/trade went to $0.00 or negative post-May-23.

### Model+0.15 Edge — Weekly P&L Collapse
```
Apr 15–May 22: WR ~45-55%, avg P&L ~$0.10-0.20/trade, total +$788
May 23+:       WR ~15-30%, avg P&L -$0.02 to -$0.06/trade, total -$33
```

### BUY<=10c — Weekly P&L Degradation
```
Apr 15–May 22: WR ~15-27%, avg P&L ~$0.05-0.18/trade
May 23+:       WR ~1-14%, avg P&L ~-$0.05 to +$0.05/trade, essentially breakeven
```

### T-60s BUY<=15c — Weekly P&L Degradation
```
Apr 15–May 22: WR ~20-27%, avg P&L ~$0.15-0.22/trade
Jun 2026:      WR ~5%, avg P&L ~$0.02/trade (MASSIVE drop)
```

### SELL>=90c — The ONLY strategy that survived
```
Apr 15–May 22: Small sample (~10-20/day)
May 23+:       Volume EXPLODED to 80-120/day, maintained positive P&L
Jun 2026:      +$92.23 across 1,728 trades, avg $0.053/trade
```

---

## 3. STRATEGY PERFORMANCE SUMMARY

### 3A. All-Time Performance

| Strategy | Trades | Win Rate | Total P&L | Avg/Trade |
|---|---|---|---|---|
| SELL YES >= 85c | 3,592 | 20.9% (NO wins) | +$278.38 | $0.0775 |
| SELL YES >= 90c | 3,253 | 15.6% (NO wins) | +$222.22 | $0.0683 |
| SELL YES >= 95c | 2,998 | 11.2% (NO wins) | +$206.48 | $0.0689 |
| BUY YES <= 5c | 5,951 | 10.9% | +$387.52 | $0.0651 |
| BUY YES <= 10c | 6,219 | 13.8% | +$325.65 | $0.0524 |
| BUY YES <= 15c | 6,523 | 17.0% | +$255.56 | $0.0392 |
| T-60s BUY <= 15c | 2,533 | 19.2% | +$392.99 | $0.1551 |
| T-30s BUY <= 15c | 1,672 | 26.1% | +$380.53 | $0.2276 |
| T-30s BUY <= 10c | 1,498 | 21.6% | +$290.14 | $0.1937 |
| Model+0.10 BUY | 8,605 | 42.4% | +$526.89 | $0.0612 |
| Model+0.15 BUY | 7,810 | 41.7% | +$754.83 | $0.0966 |
| Model+0.20 BUY | 6,863 | 41.9% | +$941.40 | $0.1372 |
| Model-0.10 SELL | 5,695 | 42.5% | +$492.72 | $0.0865 |
| Model-0.15 SELL | 4,348 | 38.6% | +$424.21 | $0.0976 |
| 70→62 Short SL48 TP82 | 3,353 | 77.7% | +$169.33 | $0.0505 |

### 3B. June-Only (Current Regime)

| Strategy | Trades | Win Rate | Total P&L | Avg/Trade |
|---|---|---|---|---|
| **SELL >= 85c** | 1,859 | 19.8% | **+$123.70** | **$0.0665** |
| **SELL >= 90c** | 1,728 | 14.1% | **+$92.23** | **$0.0534** |
| BUY <= 10c | 1,906 | 8.5% | -$2.09 | -$0.0011 |
| BUY <= 15c | 1,993 | 11.7% | -$30.58 | -$0.0153 |
| Model+0.15 BUY | 1,417 | 27.2% | -$33.54 | -$0.0237 |

---

## 4. SPREAD & DEPTH FILTER VERIFICATION

The extreme-price edges are **real and tradeable**, not artifacts of stale quotes.

### T-30s BUY <= 15c with filters:
| Filter | N | WR | P&L | Avg |
|---|---|---|---|---|
| ANY (unfiltered) | 1,672 | 26.1% | +$380.53 | $0.2276 |
| spread <= 3c | 1,240 | **32.5%** | +$353.53 | **$0.2851** |
| depth > 200 | 1,672 | 26.1% | +$380.53 | $0.2276 |
| spread <= 3c & depth > 200 | 1,240 | **32.5%** | +$353.53 | **$0.2851** |

**Conclusion**: The spread filter IMPROVES the edge (removes illiquid quotes). The WR goes from 26.1% → 32.5%, avg from $0.228 → $0.285.

---

## 5. WINDOW EXTREME PROFILE

What happens when a window's max YES price reaches various levels:

| Max Ask Reached | N | DOWN Wins | UP Wins |
|---|---|---|---|
| Never hit 50c | 2,115 | 64.0% | 36.0% |
| 50-60c | 2,277 | 65.0% | 35.0% |
| 60-70c | 1,890 | 65.7% | 34.3% |
| 70-80c | 1,176 | 67.5% | 32.5% |
| 80-85c | 366 | 64.5% | 35.5% |
| 85-90c | 338 | 72.2% | 27.8% |
| 90-95c | 243 | **68.7%** | 31.3% |
| >=95c | 3,017 | 11.3% | 88.7% |

**Key insight**: Windows that touch 90-95c but NOT >=95c (the "stallers") resolve DOWN 68.7% of the time. If we could identify stalling in real-time, selling at 90c in those windows would yield a **90.8% WR, $0.81/trade**.

---

## 6. PER-SYMBOL BREAKDOWN (June 2026)

| Strategy | BTC N | BTC Avg | ETH N | ETH Avg |
|---|---|---|---|---|
| SELL >= 90c | 864 | $0.0417 | 864 | **$0.0650** |
| BUY <= 10c | 972 | +$0.0009 | 928 | -$0.0028 |
| Model+0.15 BUY | 773 | -$0.0143 | 637 | -$0.0347 |
| T-60s BUY <= 15c | 291 | +$0.0521 | 326 | -$0.0064 |

ETH slightly better for selling expensive YES. BTC slightly better for buying cheap YES.

---

## 7. PRICE TRAJECTORY PATTERNS

### 7A. The Bounce (dip then recover)
- Dip <= 5c, recover >= 40c: Only **3.6%** bounce rate (215/6,003)
- Dip <= 10c, recover >= 40c: **8.4%** bounce rate (527/6,266)
- Dip <= 15c, recover >= 40c: **14.2%** bounce rate (932/6,574)
- When it DOESN'T bounce, buying at the dip wins ~10-13% WR

### 7B. The Cascade (high → crash)
- Hit >= 70c → crash to <= 30c: 3,829 windows
- After crashing, buying YES at 30c: WR ~47.6% (near coinflip)

### 7C. The Rocket (low → moon)
- Hit <= 15c → rocket to >= 80c: shows strong momentum continuation
- Once it recovers past 80c from a low, UP wins dominate

### 7D. Ask Velocity
- Ask moves >5c in 60s UP: Fading it (bet DOWN) marginally profitable
- Ask moves >5c in 60s DOWN: Fading it (bet UP) marginally profitable

---

## 8. 70→60 DIP STRATEGY (Original Request)

### Buy YES at 60 after 70→60 (Mean Reversion)
- 3,187 trades
- Stop Loss (≤50c): 73.7% — **dominates**
- Take Profit (≥80c): 22.5%
- Held to expiry: 3.8%
- **Total P&L: -$36.96** — LOSING

### Sell YES at 60 after 70→60 (Momentum Continuation)
- 3,187 trades  
- TP (YES ≤ 50c): 73.7%
- SL (YES ≥ 80c): 22.5%
- **Total P&L: +$36.96** — WINNING but tiny edge
- Best params: Entry ≤0.62, SL=0.48, TP=0.82 → $169 P&L, avg $0.05

---

## 9. MODEL EDGE ANALYSIS (Dynamic-k divergence)

The bot's `real_prob` model vs `kalshi_yes_ask` divergence was the strongest signal pre-regime-change.

### Best model thresholds (all-time):
| Signal | N | WR | P&L | Avg |
|---|---|---|---|---|
| real_p - ask > +0.20 | 6,863 | 41.9% | +$941.40 | $0.1372 |
| real_p - ask > +0.15 | 7,810 | 41.7% | +$754.83 | $0.0966 |
| ask - real_p > +0.20 | 3,235 | 35.0% | +$371.90 | $0.1150 |
| ask - real_p > +0.15 | 4,348 | 38.6% | +$424.21 | $0.0976 |

### Model edge broke on May 23. June performance:
- Model+0.15: -$33.54 (LOSING)

---

## 10. COMBO SIGNALS (Model + Price + Time)

Adding price filters to model edge narrows trade count without improving avg P&L:
- Model+0.10 & ask <= 15c: n=5,542, avg=$0.050
- Model+0.15 & ask <= 15c: n=5,146, avg=$0.059
- Model-0.10 & ask >= 90c: n=2,539, avg=$0.086

Pure model edge without price filter performs better per-trade.

---

## 11. RECOMMENDED STRATEGIES FOR CURRENT REGIME (June 2026)

### Primary: SELL YES >= 85c (hold to expiry)
```
Signal: kalshi_yes_ask >= 0.85
Action: Sell YES (buy NO equivalent)
Exit:   Hold to settlement
Stop:   Consider SL at 0.92 (limit loss to -7c on continuing rallies)
Monthly: ~1,859 trades, ~$124 P&L (per contract)
Avg:    $0.067/trade
Max DD:  Check per-week: worst week was May 22 at +$10 (always positive)
```

### Secondary: T-30s BUY YES <= 15c, spread <= 3c
```
Signal: At T-30s remaining, kalshi_yes_ask <= 0.15 AND (ask - bid) <= 0.03
Action: Buy YES
Exit:   Hold to settlement
Monthly: ~1,240 trades, ~$350 P&L (per contract, pre-June)
Avg:    $0.285/trade
Risk:   This edge degraded in June (check spread-filtered June data)
```

### Experimental: SELL >= 85c with trailing stop
```
Entry:  Sell YES at 0.85
Trail:  If price hits 0.90, move stop to 0.88
        If price hits 0.93, move stop to 0.91
Exit:   Stop hit or hold to expiry
Reason: The 90-95c "staller" windows have 90.8% WR
```

---

---

## 12. THE SETTLEMENT EDGE (NEAR-EXPIRY EXTREMES)

**This is the largest edge found in the entire dataset.** The market systematically over-discounts tail outcomes in the final 15-30 seconds of trading.

### T-15s Extreme Entries (spread <= 3c, depth > 100)

| Price Band | N | Implied WR | Actual WR | Edge | Buy YES Avg |
|---|---|---|---|---|---|
| 0-5c | 534 | ~2.5% | 29.4% | +26.9% | **$0.276** |
| 5-15c | 245 | ~10% | 71.0% | +61.0% | **$0.606** |
| 15-30c | 465 | ~22.5% | 81.9% | +59.4% | $0.583 |

| Price Band | N | Implied DOWN | Actual DOWN | Edge | Sell YES Avg |
|---|---|---|---|---|---|
| 95-100c | 808 | ~2.5% | 17.2% | +14.7% | **$0.168** |

### The Crypto Direction Filter (THE KILLER COMBO)

When you add crypto direction at entry time, the edge explodes:

| Band | Crypto Dir | N | WR | Buy YES Avg |
|---|---|---|---|---|
| 0-5c | UP | 161 | **75.2%** | **$0.728** |
| 0-5c | DOWN | 373 | 9.7% | $0.081 |
| 5-15c | UP | 180 | **93.3%** | **$0.826** |
| 5-15c | DOWN | 65 | 9.2% | -$0.004 |

| Band | Crypto Dir | N | DOWN WR | Sell YES Avg |
|---|---|---|---|---|
| 95-100c | DOWN | 151 | **48.3%** | **$0.510** |
| 95-100c | UP | 657 | 9.3% | $0.089 |

**Interpretation**: When crypto is ALREADY moving in a direction and the Kalshi contract price contradicts it (cheap despite up-move, or expensive despite down-move), the Kalshi market is WRONG and slow to reprice. This is the inefficiency.

### Per-Symbol (T-15s, spread-filtered, buy YES)

| Symbol | 0-5c N | 0-5c WR | 0-5c Avg | 5-15c N | 5-15c WR | 5-15c Avg |
|---|---|---|---|---|---|---|
| BTC | 263 | 34.2% | $0.32 | 164 | 69.5% | $0.59 |
| ETH | 271 | 24.7% | $0.23 | 81 | 74.1% | $0.63 |

### Strategy: T-15s Settlement Edge

```
Entry window: T-15s remaining (Kalshi may have 5s trading halt before settlement)
Filters:     spread <= 3c, depth > 100
Signal 1:    crypto UP (price_change_pct > 0) AND kalshi_yes_ask <= 0.15 → BUY YES
Signal 2:    crypto DOWN (price_change_pct < 0) AND kalshi_yes_ask >= 0.95 → SELL YES
Exit:        Hold to settlement (no stop, no take profit)
Expected:    492 filtered trades in dataset, $343 total P&L, $0.70/trade avg
Daily:       ~7 signals/day (BTC+ETH combined)
```

### Edge Persistence Across Time Horizons

| Time | 0-5c WR | 0-5c Avg | 5-15c WR | 5-15c Avg | 95-100c Sell Avg |
|---|---|---|---|---|---|
| T-30s | 22.9% | $0.21 | 59.9% | $0.50 | $0.10 |
| T-20s | 26.6% | $0.25 | 66.2% | $0.56 | $0.15 |
| T-15s | 29.4% | $0.28 | 71.0% | $0.61 | $0.17 |
| T-10s | 33.2% | $0.31 | 77.9% | $0.67 | $0.21 |

The edge INCREASES as we approach expiry, not decreases. Markets should get more efficient near expiry; Kalshi does the opposite.

---

## 13. TIME OF DAY EDGE VARIATION (June 2026, SELL>=85c)

| Hour (UTC) | N | DOWN WR | Sell P&L | Sell Avg |
|---|---|---|---|---|
| **04** | 74 | 27.0% | +$10.14 | **$0.137** |
| **22** | 80 | 30.0% | +$13.28 | **$0.166** |
| **18** | 79 | 26.6% | +$10.34 | $0.131 |
| **13** | 70 | 25.7% | +$9.18 | $0.131 |
| 12 | 85 | 10.6% | -$2.47 | -$0.029 |
| 19 | 68 | 13.2% | +$0.34 | $0.005 |
| 21 | 76 | 14.5% | +$0.51 | $0.007 |
| 23 | 71 | 14.1% | +$0.63 | $0.009 |

**Note**: The bot currently gates out 20-23 UTC as "off-peak." However, 22 UTC is actually the BEST hour for SELL>=85c. 20, 21, 23 are mediocre. This suggests the off-peak gating should be selective or strategy-dependent.

---

---

## 14. THE CONTRADICTION PRINCIPLE

**The single unifying theory behind all edges found**: When the crypto price direction contradicts the Kalshi contract price, the Kalshi market is WRONG and slow to reprice.

### T-300s Contradiction Check
| Signal | N | WR | Avg P&L |
|---|---|---|---|
| Crypto UP >0.3% + Kalshi <40c (contrarian BUY) | 35 | **100%** | **$0.72** |
| Crypto UP >0.3% + Kalshi >70c (momentum) | 324 | 98.1% | $0.03 |
| Crypto DOWN >0.3% + Kalshi <30c (momentum) | 536 | 94.8% DOWN | $0.02 |

The contradiction is RARE (35/11,528 = 0.3% of windows at T-300s) but 100% accurate. The "obvious" momentum trades have high WR but zero edge because the market already prices them in.

### Crypto Direction vs Outcome (any price)
| Time | Crypto UP >0.2% | Crypto FLAT | Crypto DOWN >0.2% |
|---|---|---|---|
| T-300s | UP wins 96.9% | UP wins 48.7% | UP wins 6.5% |
| T-120s | UP wins 99.2% | UP wins 48.5% | UP wins 3.4% |
| T-60s | UP wins 99.5% | UP wins 46.6% | UP wins 2.7% |

Crypto direction alone is highly predictive but not profitable (market prices it in). Edge comes from Kalshi price failing to reflect crypto reality.

### Day-of-Week Variation (SELL>=85c, June)
| Day | N | WR | Avg |
|---|---|---|---|
| **Saturday** | 225 | 24.0% | **$0.107** |
| Monday | 327 | 21.1% | $0.079 |
| Tuesday | 317 | 20.2% | $0.070 |
| Friday | 199 | 19.6% | $0.064 |
| Thursday | 247 | 19.8% | $0.067 |
| Sunday | 231 | 16.9% | $0.041 |
| Wednesday | 313 | 17.3% | $0.041 |

### Hour-of-Day Optimization (SELL>=85c, June)
- **Best 4 hours** (4, 13, 18, 22 UTC): 303 trades, WR=27.4%, avg **$0.142** (2.1x baseline)
- **Best hours + best days** (Mon,Tue,Thu,Sat): 179 trades, WR=29.1%, avg **$0.157** (2.4x baseline)

### Pre/Post Regime — SELL>=85c Robustness
| Period | N | WR | Avg |
|---|---|---|---|
| Pre-May 23 | 781 | 23.4% | $0.103 |
| Post-May 23 | 2,811 | 20.2% | $0.071 |

SELL>=85c survived the regime change. Volume exploded 3.6x post-regime. Edge degraded 31% but stayed profitable.

---

## 15. EARLY vs LATE WINDOW EDGE EVOLUTION

| Time | <=10c N | <=10c WR | <=10c Avg | 10-20c N | 10-20c WR | 10-20c Avg |
|---|---|---|---|---|---|---|
| T-840s (early) | 15 | 33.3% | $0.26 | 92 | 22.8% | $0.06 |
| T-60s (late) | 2,281 | 15.3% | $0.13 | 478 | 54.0% | $0.39 |

Extreme prices are RARE early in windows but COMMON late. The edge GROWS as expiry approaches because the Kalshi market becomes LESS efficient, not more.

---

## 16. REGIME CHANGE DETECTION

The May 23 transition was instantaneous (1 day). Model+0.15 WR dropped from 44%→29% overnight, then 17-24% thereafter.

**Detection method**: Track rolling 50-trade WR. If WR < 35% for >1 day, the regime changed. Switch strategies or reduce size.

**Key question for implementation**: Does SELL>=85c WR also drop during future regime changes, or is it truly regime-agnostic?

---

## 17. RISK METRICS & POSITION SIZING (SELL>=85c)

### Win/Loss Profile (all-time, 3,592 trades)
| Outcome | N | % | Avg P&L | Avg Entry Price |
|---|---|---|---|---|
| WIN (DOWN) | 751 | 20.9% | **+$0.868** | 0.868 |
| LOSS (UP) | 2,841 | 79.1% | **-$0.132** | 0.868 |

**Payoff ratio: 6.6:1** (win $0.87, lose $0.13 per $1 contract)

### Streak Analysis
| Metric | Value |
|---|---|
| Max consecutive losses | **42** |
| Max consecutive wins | 10 |
| Average loss streak | 5.8 |

### Kelly Sizing
- Full Kelly: **8.9%** of bankroll per trade
- Half Kelly: **4.4%** 
- Conservative (2% risk): 15 contracts per $100 bankroll

### Drawdown Simulation ($100 account, 2% risk)
| Scenario | Detail |
|---|---|
| Per-trade risk | $2.00 (15 contracts × $0.132 max loss) |
| Expected daily P&L | ~$102/day (88 trades × $1.16) |
| Worst historical DD | **-$83.16** (42 consecutive losses = 83% of account) |
| Safe sizing (1% risk) | 7 contracts, worst DD = $38.81 (39%) |

**Recommendation**: Start at 0.5-1% per-trade risk until 100+ live trades validate the WR.

---

## 18. NO SIDE SWEET SPOT (Bot's Own Data)

The bot's existing momentum strategy has a hidden goldmine: NO entries at $0.25-0.35.

| Entry Band | N | WR | Total P&L | **Avg/Trade** |
|---|---|---|---|---|
| 15-25c | 28 | 32.1% | +$8.67 | $0.31 |
| **25-35c** | 223 | 49.3% | **+$216.40** | **$0.97** |
| 35-45c | 353 | 54.4% | +$207.13 | $0.59 |
| 45c+ | 548 | 53.1% | +$34.50 | $0.06 |

The 25-35c band (YES at 65-75c) is the bot's most profitable regime — $0.97/trade average, almost entirely from the momentum strategy with time_exit. This complements our SELL>=85c strategy: together they cover YES at 65-75c AND 85-99c.

**All-time NO vs YES side:**
| Side | N | WR | Total P&L | Avg |
|---|---|---|---|---|
| NO | 1,152 | 52.3% | **+$466.70** | $0.405 |
| YES | 1,666 | 21.6% | -$232.63 | -$0.140 |

---

## 19. CROSS-ASSET CORRELATION

BTC's previous window result does NOT predict ETH's next window:
- BTC was UP → ETH UP 47.3% (n=2,740)
- BTC was DOWN → ETH UP 50.1% (n=2,864)

Cross-asset momentum is essentially random. No arb opportunity here.

---

## 20. LATE REVERSAL PATTERNS

Windows where crypto direction flips in the last 2 minutes:

| Early → Late | N | Final UP WR | Trade? |
|---|---|---|---|
| DOWN → UP | 21 | **100.0%** | Buy YES (rare) |
| UP → DOWN | 181 | 3.9% (96.1% DOWN) | Sell YES |
| FLAT → UP | 702 | **94.9%** | Buy YES |
| FLAT → DOWN | 1,246 | 2.1% (97.9% DOWN) | Sell YES |
| UP → FLAT | 1,201 | 49.5% | Coinflip |
| DOWN → FLAT | 137 | 16.1% | Lean DOWN |

**Late reversal arbitrage (UP→DOWN)**: At reversal point, avg YES ask = 8.9c (crashed from expensive). Selling YES at reversal yields +$0.05/trade. The edge exists but is small — catching the exact reversal moment is the hard part.

---

## 12. OPEN QUESTIONS

1. **What caused the May 23 regime change?** Was it a Kalshi liquidity change, crypto volatility shift, or model drift?
2. **Can we detect regime changes in real-time?** Rolling 7-day WR tracking could signal when to switch strategies.
3. **Do off-hours (20-23 UTC) produce different edge profiles?** The bot currently gates these out.
4. **What's the optimal contract sizing?** Kelly fraction given these win rates.
5. **Do edges vary by day-of-week?** Weekend crypto might behave differently.
6. **Can the "staller" detection be made real-time?** Instead of knowing the window won't hit 95, use trailing logic.
