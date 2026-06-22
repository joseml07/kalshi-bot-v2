# Next Steps — Settlement Edge V3 Implementation Plan

## Current State (2026-06-22)
- Bot running SELL>=85c with prev=DOWN gate, Kelly sizing, paper mode
- Balance seeded from real Kalshi balance, growing with P&L
- ~16 settled paper trades, collecting live execution data

---

## Phase 1: Validate Baseline (Now → 50+ paper trades)

**Goal**: Confirm live WR matches predicted 35.8% before going live.

**Decision criteria**:
- WR > 30% after 50 trades → go live
- WR 25-30% → investigate fill quality, delay
- WR < 25% → pause, execution issue likely

**No code changes needed.** Just let it run.

---

## Phase 2: Go Live + Safety (After baseline validated)

### 2a. Rolling WR Auto-Pause
- Track rolling 50-trade WR internally
- If WR drops below 25%: pause new entries, alert via Telegram/Discord
- If WR recovers above 30% over next 20 trades: auto-resume
- Config: `SETTLEMENT_EDGE_REGIME_PAUSE_THRESHOLD=0.25`

### 2b. Daily Loss Limit (already exists in .env)
- `DAILY_LOSS_LIMIT=10.0` → adjust for account size
- At $200 start: consider $30-50 (15-25%)

---

## Phase 3: Multiplier Framework (P&L Upgrade)

**Replace hard gate with dynamic sizing.** Every SELL>=85c signal trades, but size scales by conditions.

### Conditions & Multipliers
| Condition | Effect | Rationale |
|---|---|---|
| prev_window = DOWN | +0.5x | 35.8% → 3.2x edge |
| Best hours (4,13,18,22 UTC) | +0.3x | 27.2% → 2.1x edge |
| Crypto currently DOWN | +0.2x | Contradiction edge |
| YES ask >= 90c | +0.2x | Higher entry = better edge |
| Wide spread + deep book | +0.1x | Market uncertainty premium |
| Balanced depth | +0.1x | Crowd disagreement |
| Regime IN (LOSS→WIN transition) | +1.0x | 42.2% next WR |
| Regime OUT (WIN→LOSS transition) | -0.5x | 13.1% next WR |

**Formula**: `contracts = kelly_base × min(1.0 + sum(multipliers), 3.0)`, floor 0.5x

**Config**: `SETTLEMENT_EDGE_USE_MULTIPLIER=true` (easy toggle)

**Impact**: +108% P&L vs baseline at fixed size, lower per-trade risk on bad signals

---

## Phase 4: Optional Enhancements (Lower Priority)

### 4a. First-Touch Filter
- Only trade the FIRST time a window touches 85c (81.3% WR vs 14.7% on re-touches)
- Requires tracking touch count per window
- Implementation: maintain a set of "already traded" tickers

### 4b. Hour-of-Day Optimization
- Enable best-hours filter if account size justifies lower volume for higher edge
- Config: `SETTLEMENT_EDGE_ALLOWED_HOURS=4,13,18,22`

### 4c. Dashboard Rolling WR Chart
- Add live rolling WR chart for settlement_edge trades on Strategy tab

---

## Files to Modify
| File | Phase | Changes |
|---|---|---|
| `strategy/settlement_edge.py` | 3, 4a | Multiplier logic, touch tracking |
| `config.py` | 2a, 3 | New config fields |
| `main.py` | 2a, 3 | Rolling WR tracker, pass new params |
| `risk/manager.py` | 2a | Auto-pause on regime degradation |
| `dashboard.py` / `.html` | 4c | Rolling WR chart |

---

## Risk Management Philosophy

1. **Never gate** — every signal has some edge. Gating kills volume.
2. **Always multiply** — bet more when edge is strong, less when weak.
3. **Monitor the regime** — rolling WR catches collapses within 50 trades.
4. **Don't overfit** — April's lower WR wasn't "broken," just a different regime. The strategy works in all regimes, just better in some.
