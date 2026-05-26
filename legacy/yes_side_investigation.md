# YES Side Investigation: Why 0% Win Rate on Time Exit?

## Executive Summary

The YES side loses on **every metric**: 0% WR on time_exit (47 trades, 0 wins), 9.9% WR
on settlement (263 trades, 26 wins). Meanwhile, NO time_exit is 86.1% WR and generates
nearly all profit. The code treats YES and NO symmetrically — there are no bugs in the
execution path. The root cause is a **strategy-level problem**: the bot's probability
model systematically overestimates P(up) when positive momentum is present, causing it
to buy YES at prices that reflect correct market consensus, not mispriced edge. The
momentum signal is directionally asymmetric: negative momentum (dips) persist through
settlement while positive momentum (rises) revert.

---

## Data Analysis

### 1. Side × Exit Performance

| Side | Exit      | Trades | Wins | WR    | PnL      | Avg Entry | Avg Edge |
|------|-----------|--------|------|-------|----------|-----------|----------|
| NO   | time_exit | 159    | 136  | 86.1% | +$397.86 | $0.424    | 0.1016   |
| NO   | settle    | 63     | 48   | 76.2% | +$43.48  | $0.407    | 0.1415   |
| YES  | time_exit | 47     | 0    | 0.0%  | -$52.65  | $0.412    | 0.1056   |
| YES  | settle    | 263    | 26   | 9.9%  | -$62.50  | $0.435    | 0.1468   |

Entry prices and estimated edges are nearly identical between sides. The model claims
10-14% edge for both. But for YES, that "edge" is an illusion.

### 2. The Smoking Gun: Implied Exit Prices

For YES time_exit trades, computing the exit price from PnL:

```
exit_price = entry_price + (pnl + fees) / contracts
```

| Entry | PnL    | Contracts | Implied Exit | Ticker               |
|-------|--------|-----------|-------------|----------------------|
| 0.32  | -2.69  | 8         | $0.00       | KXETH15M-26MAY200730 |
| 0.32  | -3.36  | 10        | $0.00       | KXETH15M-26MAY200715 |
| 0.33  | -3.11  | 9         | $0.00       | KXBTC15M-26MAY200715 |
| 0.30  | -3.15  | 10        | $0.00       | KXETH15M-26MAY200700 |
| 0.32  | -2.35  | 7         | $0.00       | KXBTC15M-26MAY200700 |
| 0.42  | -0.57  | 4         | $0.31       | KXETH15M-26MAY200630 |
| 0.31  | -2.93  | 9         | $0.00       | KXBTC15M-26MAY200630 |

**YES exit prices are ~$0.00 in the vast majority of cases.** With 30 seconds remaining,
the market is pricing YES at zero — the crypto has fallen below open and the market is
certain it will stay down. The "positive momentum" that triggered the YES entry has
completely reversed by exit time.

Compare to NO time_exit:

| Entry | PnL   | Contracts | Implied Exit | Ticker               |
|-------|-------|-----------|-------------|----------------------|
| 0.34  | +3.19 | 10        | $0.69       | KXBTC15M-26MAY212300 |
| 0.35  | +6.34 | 10        | $1.00       | KXBTC15M-26MAY212245 |
| 0.40  | +5.62 | 10        | $0.97       | KXBTC15M-26MAY212230 |
| 0.36  | +5.80 | 10        | $0.96       | KXBTC15M-26MAY212145 |
| 0.33  | +6.54 | 10        | $1.00       | KXBTC15M-26MAY212130 |

NO exits at $0.69-$1.00. The negative momentum persisted → crypto stayed below open →
NO converged toward $1.

### 3. Signal Volume Asymmetry

From the signals table:

| Side | paper_trade | trade | skip_risk | skip_sizing |
|------|-------------|-------|-----------|-------------|
| YES  | 105,125     | 918   | 686,948   | 609,330     |
| NO   | 532         | 258   | 134,313   | 256,410     |

**YES generates 200x more paper trades than NO.** The model almost always sees "positive
edge" for YES because:
- The logistic model overestimates P(up) when momentum is positive
- YES prices on Kalshi are typically low (0.30-0.44), creating apparent edge
- The low edge threshold (0.04) lets marginal signals through

### 4. Time-of-Day Analysis

YES loses money in **every single hour** of the day (24/24 hours negative). This is not a
time-of-day effect. It's structural.

NO wins money in **every single hour** (24/24 hours positive).

### 5. Entry Timing Distribution

| Timing Bucket    | YES Traded | NO Traded |
|------------------|-----------|-----------|
| 0-120s (late)    | 18,859    | 81        |
| 120-300s (mid)   | 41,660    | 79        |
| 300-540s (early) | 45,524    | 631       |

YES signals fire across the entire time range (early entries dominate). NO signals are
concentrated in the early window (300-540s). The late-window YES signals at 0-120s
should be the strongest per the LWM probability table, but even they lose.

### 6. The 26 YES Settlement Wins

The rare YES wins share a distinctive pattern:

| Entry | PnL   | Route | Secs | Model P(up) | Net Edge |
|-------|-------|-------|------|-------------|----------|
| 0.71  | +0.55 | taker | 50   | 0.782       | 0.052    |
| 0.80  | +0.37 | taker | 68   | 0.861       | 0.041    |
| 0.60  | +0.76 | taker | 51   | 0.675       | 0.055    |
| 0.66  | +1.29 | taker | 74   | 0.739       | 0.059    |
| 0.58  | +0.01 | maker | 456  | 0.650       | 0.060    |

The winners are either (a) late entries at high YES prices (0.60-0.80, where momentum is
already confirmed near settlement) or (b) early entries at lower prices that happened to
settle correctly. The average winning YES trade has entry price $0.60 vs the average
loser at $0.42.

---

## Code Audit Findings

### Finding 1: Code is Symmetric — No YES/NO Bug

**LWM strategy** (`strategy/lwm.py:129-142`):
```python
side = Side.YES if pc > 0 else Side.NO
if side is Side.YES:
    maker_price = orderbook.best_yes_bid
    taker_price = orderbook.best_yes_ask
    est_prob = estimate_p_up(pc, seconds_remaining)
else:
    maker_price = orderbook.best_no_bid
    taker_price = orderbook.best_no_ask
    est_prob = 1.0 - estimate_p_up(pc, seconds_remaining)
```

**Momentum strategy** (`strategy/momentum.py:106-115`): Same symmetric structure.

**Executor** (`execution/executor.py:220-227`): `place_order(side=signal.side.value,
price_dollars=signal.kalshi_price)` — symmetric.

**Kalshi client** (`client/kalshi.py:204-205`):
```python
"yes_price_dollars": str(price_dollars) if side == "yes" else None,
"no_price_dollars": str(price_dollars) if side == "no" else None,
```
Correct — sends the appropriate price field for each side.

**Exit evaluation** (`main.py:1422-1429`):
```python
if order.signal.side.value == "yes":
    current_value = best_yes_bid    # Correct: sell YES at YES bid
else:
    current_value = best_no_bid     # Correct: sell NO at NO bid
```

**Exit PnL** (`execution/executor.py:700-709`):
```python
pnl_per_contract = sell_price - order.price
raw_pnl = pnl_per_contract * order.contracts
```
Symmetric — works correctly for both sides.

**Fee computation** (`strategy/fees.py`): `price * (1 - price)` is symmetric around 0.5.
Both sides pay the same fee at the same price level.

**Verdict: There is no code bug causing the YES losses.** The execution path, pricing,
PnL computation, and fee calculation all handle YES and NO identically.

### Finding 2: Probability Model Overestimates P(up) for YES

The LWM `estimate_p_up` table (`strategy/lwm.py:23-74`):

| Seconds Left | Price Change   | P(up) | Implied YES Edge at $0.35 |
|-------------|----------------|-------|---------------------------|
| >300        | 0 < pc < 0.05% | 0.55  | 0.55 - 0.35 = 0.20       |
| >300        | 0.05% < pc     | 0.65  | 0.65 - 0.35 = 0.30       |
| >300        | >0.2%          | 0.72  | 0.72 - 0.35 = 0.37       |

The model sees 20-37% "edge" on YES at early entry times with any positive momentum.
But the actual settlement rate for these YES trades is **9.9%** — the model is off by
5-7x. The market price of $0.35 was far more accurate than the model's P(up) = 0.55-0.72.

For NO, the situation is different. With negative momentum:

| Seconds Left | Price Change    | P(down) = 1-P(up) | Implied NO Edge at $0.35 |
|-------------|-----------------|---------------------|--------------------------|
| >300        | -0.05% < pc < 0 | 0.55                | 0.55 - 0.35 = 0.20      |
| >300        | pc < -0.05%     | 0.72                | 0.72 - 0.35 = 0.37      |

Same edge levels, but NO's 76-86% WR shows the model is approximately correct for NO.
**The calibration table is accurate for negative momentum but wildly wrong for positive.**

The logistic model in `probability.py` has a similar issue: `k=200` (from .env) makes it
very confident about small price changes, producing P(up) estimates of 0.48-0.54 that
still show edge against market prices in the 0.30-0.44 range.

### Finding 3: No Edge Bonus for YES (Structural Disadvantage)

In `lwm.py:142`: `side_edge_thr = eff_edge_threshold + eff_no_side_bonus`

NO has a **+4% edge bonus** (higher threshold to clear), which ironically helps by
filtering out marginal NO signals. YES uses the base 4% threshold, letting in the
flood of phantom-edge signals that the model generates.

### Finding 4: Signal Generation is Not Gatekept by Momentum Strength

The LWM strategy checks `abs(pc) >= min_price_change` (default 0.03% from config)
but has **no minimum momentum magnitude gate**. A tiny positive price change of 0.03%
with 480 seconds remaining triggers a YES signal if the orderbook conditions are met.
At s > 300, the model gives P(up) = 0.55 for any pc > 0, creating "edge" against
any YES price below $0.51.

---

## Theories Evaluated

### Theory 1: Price Inversion Bug
**REJECTED.** Code audit confirms all YES/NO price handling is correct. YES buys at
YES bid (maker) or YES ask (taker). NO buys at NO bid/ask. Exit sells at the correct
bid for each side.

### Theory 2: Probability Model Asymmetry
**CONFIRMED.** The model overestimates P(up) for positive momentum but is approximately
correct for negative momentum. The piecewise LWM table and the logistic model both
produce P(up) > 0.50 for any positive price change, creating phantom edge against the
(correctly priced) market.

### Theory 3: Exit Price Bug for YES
**REJECTED.** The exit was previously using ASK instead of BID, but this was fixed.
The current code correctly uses `best_yes_bid` for YES exits. However, the implied
exit prices of ~$0.00 show that the YES bid is genuinely at zero near settlement —
the momentum reversed. This is a market outcome, not a code bug.

### Theory 4: Market Microstructure Asymmetry
**CONFIRMED.** In crypto 15-minute binary options:
- **Negative momentum (dips) persist**: When crypto dips mid-window, it tends to stay
  below open through settlement. This is consistent with mean-reversion being weaker
  on the downside (panic selling, liquidity withdrawal).
- **Positive momentum (rises) revert**: When crypto rises mid-window, it frequently
  gives up the gains before settlement. This could reflect profit-taking dynamics,
  market makers fading the move, or simply the random walk nature of small positive
  moves over short windows.

This asymmetry means the time-decay capture strategy only works for NO: buy NO during
a dip, ride the convergence as the market becomes more certain the dip will hold.
For YES, the convergence goes the wrong way — the rise reverts, and YES goes to zero.

### Theory 5: Momentum Sign Convention Error
**REJECTED.** Positive momentum (price rising) → YES signal. Negative momentum →
NO signal. Convention is correct throughout the stack. The issue is that positive
momentum is not predictive of settlement direction.

### Theory 6: Edge Calculation Asymmetry
**PARTIALLY CONFIRMED.** The edge calculation itself is symmetric: `est_prob - price -
fee`. But the inputs are asymmetric: `est_prob` is systematically wrong for YES
(model overestimates P(up)), while market `price` is correct. The model "sees" 10-20%
edge where none exists.

---

## Root Causes (Ranked)

### Root Cause 1: Positive Momentum is Not Predictive at Early Entry Times

The bot enters YES trades with 400-480 seconds remaining (6-8 minutes before settlement).
At these timescales, a small positive price change (0.03-0.2%) is essentially noise —
it provides no predictive power about where the price will be 6-8 minutes later. The
crypto price mean-reverts, and by settlement (or by the 30-second exit window), the
move has disappeared.

For NO, the same appears true on paper, but the asymmetry is real: dips at 6-8 minutes
out tend to persist. This may be a genuine market inefficiency (panic/capitulation
dynamics in crypto) or may be sample-period bias (the May 2026 sample may be
downtrend-biased).

### Root Cause 2: The Probability Model is Miscalibrated for P(up)

The LWM calibration table assigns P(up) = 0.55-0.72 for positive momentum at s > 300.
Actual observed P(up) for these YES trades is ~10%. The model is wrong by 5-7x.

The table may have been calibrated on data where late-window (s < 120) entries dominated,
where positive momentum IS predictive (P(up) = 0.85-0.92 in the table). But the strategy
enters across the full 30-540 second range, and the early-window P(up) estimates are
not validated against this bot's actual entry timing distribution.

### Root Cause 3: Too Many Marginal YES Signals Pass the Edge Gate

The base edge threshold (4%) combined with the model's inflated P(up) estimates
means virtually any positive momentum creates a YES signal. The signals table shows
105,125 YES paper_trades vs 532 NO — a 200:1 ratio. The NO side edge bonus (4% extra
threshold) and the structural rarity of NO signals means only the strongest NO trades
get through. YES is flooded with marginal-to-negative-EV signals.

---

## Recommended Fixes (Implementation-Ready)

Each fix below includes exact file paths, line numbers, and code diffs. They are
independent — apply any combination. **Fix B is the highest-impact, lowest-risk
change.** After applying any fix, re-enable YES trading by setting
`YES_SIDE_DISABLED=false` in `.env`.

### CRITICAL: Rules for the implementing AI

1. **Only modify the files listed in each fix.** Do not refactor, rename, or "improve"
   surrounding code. Do not add docstrings or comments beyond what is specified.
2. **All three quality gates MUST pass after every change — no exceptions:**
   ```bash
   PYTHONPATH=src mypy --strict src/
   ruff check src/
   PYTHONPATH=src pytest tests/ -v
   ```
3. **Commit each fix separately** with a descriptive message. Do NOT combine fixes
   into one commit. Do NOT amend existing commits. Push after all commits are done.
4. **Do not modify .env** — config changes are the user's responsibility.
5. **Do not touch the NO side logic.** NO is profitable. Any change that could affect
   NO behavior is out of scope.
6. **If a test fails, fix the issue — do not skip or delete the test.**
7. **Start with Fix B only** unless the user explicitly asks for additional fixes.
8. Read `CLAUDE.md` at the project root for project conventions before starting.

---

### Fix B (RECOMMENDED): Restrict YES Entries to Last 120 Seconds

**Why:** The probability table is only accurate for s <= 120 (P(up) = 0.85-0.92 for
strong momentum). The 26 YES wins are clustered at late entries. Early YES entries
(s > 300) have ~10% actual win rate vs the model's 55-72%.

**Confidence: HIGH.** Cheapest change, biggest impact.

**File:** `src/kalshi_bot/strategy/lwm.py`

At line 109-111, the time gate applies uniformly to both sides:
```python
    seconds_remaining = window.seconds_remaining
    if not (decision_min_s <= seconds_remaining <= decision_max_s):
        return None
```

**Change:** Add a YES-specific max time. After line 129 (where `side` is determined),
add a gate that rejects early YES signals:

```python
    side = Side.YES if pc > 0 else Side.NO
    if side is Side.NO and eff_yes_only:
        return None

    # YES is only predictive in the last 2 minutes. Early positive momentum
    # reverts ~90% of the time (see yes_side_investigation.md).
    YES_DECISION_MAX_S = 120
    if side is Side.YES and seconds_remaining > YES_DECISION_MAX_S:
        return None
```

Insert these 4 lines between the existing lines 131 and 133 (between the `eff_yes_only`
check and the `if side is Side.YES:` price lookup block).

To make this configurable instead of hardcoded, add a parameter:

**File:** `src/kalshi_bot/config.py` — add field:
```python
    lwm_yes_decision_max_s: int = Field(default=120)
```

**File:** `src/kalshi_bot/strategy/lwm.py` — add parameter to `evaluate_lwm()`:
```python
def evaluate_lwm(
    ...
    yes_decision_max_s: int = 120,
    ...
) -> Signal | None:
```

And change the gate to use it:
```python
    if side is Side.YES and seconds_remaining > yes_decision_max_s:
        return None
```

**File:** `src/kalshi_bot/main.py` — pass it in both call sites (fast eval ~line 750
and housekeeping ~line 1215):
```python
    signal = evaluate_lwm(
        ...
        yes_decision_max_s=settings.lwm_yes_decision_max_s,
        ...
    )
```

---

### Fix D: Add YES-Side Edge Bonus

**Why:** The `no_side_edge_bonus = 0.04` accidentally makes NO profitable by filtering
marginal signals. Apply the same concept to YES with a higher bar.

**Confidence: MEDIUM.** Band-aid, but immediately reduces YES signal volume.

**File:** `src/kalshi_bot/strategy/lwm.py`, lines 133-142

Current code:
```python
    if side is Side.YES:
        maker_price = orderbook.best_yes_bid
        taker_price = orderbook.best_yes_ask
        est_prob = estimate_p_up(pc, seconds_remaining)
        side_edge_thr = eff_edge_threshold
    else:
        maker_price = orderbook.best_no_bid
        taker_price = orderbook.best_no_ask
        est_prob = 1.0 - estimate_p_up(pc, seconds_remaining)
        side_edge_thr = eff_edge_threshold + eff_no_side_bonus
```

**Change** the YES branch to also add a bonus:
```python
    if side is Side.YES:
        maker_price = orderbook.best_yes_bid
        taker_price = orderbook.best_yes_ask
        est_prob = estimate_p_up(pc, seconds_remaining)
        side_edge_thr = eff_edge_threshold + eff_yes_side_bonus
    else:
        ...
```

Add `yes_side_edge_bonus` parameter to `evaluate_lwm()` with default 0.10.
Add `lwm_yes_side_edge_bonus: float = Field(default=0.10)` to config.py.
Resolve it like the NO bonus is resolved (line 105 pattern).

---

### Fix A: Recalibrate P(up) from Settled Trade Data

**Why:** The LWM table values are wrong for early entries. Refit from actual data.

**Confidence: HIGH** in concept but requires careful execution.

**Step 1:** Run this SQL to get actual win rates by bucket:
```sql
SELECT
  CASE
    WHEN s.seconds_remaining <= 120 THEN 'late'
    WHEN s.seconds_remaining <= 300 THEN 'mid'
    ELSE 'early'
  END as timing,
  CASE
    WHEN CAST(s.edge AS REAL) - (CAST(s.kalshi_price AS REAL)) > 0.002 THEN 'strong_pos'
    WHEN CAST(s.edge AS REAL) - (CAST(s.kalshi_price AS REAL)) > 0.0005 THEN 'mod_pos'
    WHEN CAST(s.edge AS REAL) - (CAST(s.kalshi_price AS REAL)) > 0 THEN 'weak_pos'
    WHEN CAST(s.edge AS REAL) - (CAST(s.kalshi_price AS REAL)) < -0.002 THEN 'strong_neg'
    WHEN CAST(s.edge AS REAL) - (CAST(s.kalshi_price AS REAL)) < -0.0005 THEN 'mod_neg'
    ELSE 'weak_neg'
  END as momentum_bucket,
  COUNT(*) as n,
  SUM(CASE WHEN CAST(t.pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins,
  ROUND(SUM(CASE WHEN CAST(t.pnl AS REAL) > 0 THEN 1.0 ELSE 0.0 END) / COUNT(*), 3) as actual_p_up
FROM trades t
JOIN signals s ON s.ticker = t.ticker AND s.side = 'yes'
  AND s.action IN ('trade','paper_trade')
WHERE t.side = 'yes' AND t.exit_reason IS NULL
  AND t.pnl IS NOT NULL AND NOT (t.pnl = '0' AND t.fees IS NULL)
GROUP BY timing, momentum_bucket
ORDER BY timing, momentum_bucket;
```

**Step 2:** Update the return values in `estimate_p_up()` (`lwm.py:23-74`) to match
the actual `actual_p_up` values from the query. The structure stays the same — just
replace the hardcoded probability numbers.

**Note:** This fix requires enough data per bucket to be statistically meaningful
(at least 20-30 trades per bucket). Check `n` column before trusting the values.

---

### Fix C: Require Stronger Momentum for YES

**Why:** YES signals fire on 0.03% price moves (noise). Require 0.1%+ for YES.

**Confidence: MEDIUM.**

**File:** `src/kalshi_bot/strategy/lwm.py`

After line 114 (`if abs(pc) < eff_min_price_change: return None`), add:
```python
    # YES requires stronger momentum — weak positive moves are noise
    YES_MIN_PRICE_CHANGE = 0.001  # 0.1%
    if pc > 0 and pc < YES_MIN_PRICE_CHANGE:
        return None
```

Or make it configurable via `lwm_yes_min_price_change` in config.py.

---

### Fix E: Validate Against Backtester Entry Timing

**Why:** The backtester reportedly showed YES ≈ NO performance. If it used different
entry timing (e.g., only late-window entries at s < 120), the backtester result is
valid but only for late entries — the live bot's early entries were never backtested.

**How:** Check the backtester config at `/home/psypolatic/kalshi/thebacktester/`.
Look for the `decision_max_s` or equivalent parameter. If it's 120, that confirms
Fix B is the right approach.

---

### Fix F: Test Momentum Persistence Before Entry

**Why:** A momentary blip shouldn't trigger a trade. Require sustained momentum.

**Confidence: MEDIUM.** Logical but needs the `momentum_30s` field added to WindowState.

**File:** `src/kalshi_bot/data/window_tracker.py` — add a `momentum_30s` property
(same as `momentum_60s` but using a 30-second lookback window).

**File:** `src/kalshi_bot/strategy/lwm.py` — before generating a YES signal, check:
```python
    if side is Side.YES:
        m30 = window.momentum_30s
        m60 = window.momentum_60s
        if m30 is None or m60 is None:
            return None
        # Momentum weakening = reversion likely
        if m30 < m60 * 0.5:
            return None
```

---

## Summary

There is no code bug. The YES side loses because the probability model is wrong about
positive momentum at early entry times, the edge gate is too permissive for YES, and
crypto market microstructure makes positive momentum less persistent than negative
momentum over 15-minute windows. The NO side works because dips persist and the model
happens to be approximately correct for P(down).

The simplest immediate win is to either restrict YES entries to the last 120 seconds
(where the calibration is actually valid) or to refit the probability table from the
bot's own settled trade data.
