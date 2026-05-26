# V2 Salvage — TODO (Session 2026-05-24/25)

## What We Found This Session

### The Big Discovery: Backtest-Live Gap
- Backtest: 89% WR, $72K PnL on 3,300 trades
- Live/paper: 40-55% WR
- Gap is NOT the strategy — it's plumbing (price source, latency, fill model)

### Settlement Source Mismatch (Angle 1)
- Kalshi settles on **CF Benchmarks RTI** (multi-exchange composite, 60s trimmed average)
- Bot uses Coinbase as sole price feed — wrong target
- CF Benchmarks API is paywalled (institutional license required)
- Kraken + Bitstamp WS feeds alongside Coinbase would approximate CF RTI for free
- Cross-exchange basis is estimated 5-15bps — enough to erode 1-3% of edge per trade

### Latency Eating Edge (Angle 2)
- Signal-to-fill pipeline: ~150-200ms
- Kalshi REST latency from NYC1 VPS: 44-86ms
- In volatile moments (when signals fire), 200ms of latency can erase entire 4% edge
- Fresh orderbook re-read is in-memory (not network), so removing it saves ~1ms not 50ms

### Morning Alpha (Angle 3)
- Both climbs to $40 happened during US morning (13:00-17:00 UTC)
- Backtest: 93-95% WR at 13-16 UTC vs 83-85% other hours
- 2.7x expected value per trade during golden hours
- Consider time-of-day edge scaling (2x threshold off-peak) — researched but NOT yet implemented

### time_exit Is The Entire Edge (Confirmed)
- time_exit trades: 50% WR, profitable (+$5.34 over 34 paper trades)
- Settlement trades: 10% WR, disaster (-$6.41 over 10 paper trades)
- YES + settlement: 0-7 (zero wins in paper data)
- min_time=91 prevents most settlement-bound entries — currently active

### Side Asymmetry (Paper vs Backtest Conflict)
- Paper: NO 50% WR (+$2.81), YES 36% WR (-$3.88)
- Backtest: NO 89.3%, YES 90.1% — both sides equally good
- Conclusion: YES underperformance is likely regime-specific, not structural
- Don't disable YES based on small sample — gather more data

### BTC vs ETH (Reversed From Expectations)
- Live had BTC at 33% WR, ETH at 71% — but sample was tiny
- Backtest: BTC 96.4% WR (!), ETH 81.6% — BTC is the stronger asset historically
- Recent paper: BTC 50% WR, +$0.84 — fine
- Don't drop BTC — it was probably the crossed orderbook that hurt it

### Kelly Fraction
- Sweep showed Kelly doesn't affect WR (only sizing)
- Currently at 0.25 (quarter Kelly) — good balance
- Previous 0.1 was too conservative, 0.625 was too aggressive

### Edge Threshold
- .env has 0.04, backtest default is 0.06
- 0.04 generates MORE total PnL ($74K vs $72K) by taking more trades
- Current 0.04 is fine

---

## TODO List (Priority Order)

### Quick Wins (V2, < 1 hour each)
- [ ] **Time-of-day edge scaling** — raise edge threshold outside 13:00-20:00 UTC. Code is written (reverted for now), just needs to be re-enabled when ready.
- [ ] **AWS US-East migration** — user has free student credits. Saves ~30ms latency to Kalshi. Pure ops, no code changes.
- [ ] **Log Coinbase-vs-Kalshi basis** — on every eval tick, log the difference between Coinbase-derived probability and Kalshi mid-price. Quantifies the CF RTI mismatch without any price feed changes.

### Medium Effort (V2, few hours each)
- [ ] **Add Kraken WS feed** — second price source alongside Coinbase. Approximates CF RTI. ~2 hours to build a Kraken WS client (similar to existing Coinbase client).
- [ ] **Add Bitstamp WS feed** — third price source. ~1 hour (copy Kraken pattern).
- [ ] **Composite price averaging** — average Coinbase + Kraken + Bitstamp for momentum/probability calculations. ~30 min once feeds exist.
- [ ] **Settlement path simulator** — in the final 90s, replace logistic probability with Monte Carlo on the 60s trimmed average. Better late-window pricing.

### V3 Territory (Bigger Redesign)
- [ ] **Pre-staged order submission** — pre-compute order params when momentum is building, submit instantly on signal confirmation. Saves ~20ms.
- [ ] **Polymarket 5-min crypto integration** — cross-venue signals or arbitrage.
- [ ] **ML probability filter** — LightGBM trained on 6.5K labeled windows as a FILTER on top of the logistic model (not a replacement). Only after we have clean data.
- [ ] **Market making mode** — Avellaneda-Stoikov passive quoting. Requires sub-100ms cancel/replace.

---

## Current Bot State (as of 2026-05-25 03:30 UTC)
- Mode: paper
- PID: 1760000
- Symbols: BTC, ETH
- Kelly: 0.25
- Edge threshold: 0.04
- Min time: 91 (all entries qualify for time_exit)
- Exit policy: time_exit only (stop_loss reverted, take_profit disabled)
- Orderbook: crossed-book fix active
- Per-side pause: 30-min cooldown

## Key Files
- `/root/kalshi-bot/kalshi-bot-v3/angles_nobody_considered.md` — full analysis of the backtest-live gap
- `/root/kalshi-bot/kalshi-bot-v3/claude_brainstorm.md` — discipline-first V3 philosophy
- `/root/kalshi-backtest/scripts/ab_conviction_exits.py` — exit policy A/B test script
