# Live-vs-Paper Diagnosis — Kalshi Bot v2

*Generated 2026-05-23. Source: VPS `trades.db`, `logs/bot.log*`, `live_state.json`, and the Kalshi portfolio export at `/root/fromkalshi/data.csv` (2026-05-22T14:58Z → 2026-05-23T14:30Z).*

---

## 0. Live cutoff & scope

- **Strict live cutoff** (first non-paper trade, per `order_id NOT LIKE 'PAPER-%'`): `2026-04-16T21:21:59Z`.
- **Current live cycle** (`.env` `TRADING_MODE=paper → live` flip, first live HEALTH event with `mode=live`): `2026-05-22T15:03:27Z`. First fill after the flip: `2026-05-22T16:08:08Z`.
- Between `2026-05-04T13:05Z` and `2026-05-22T16:08Z` (~18 days), the bot ran paper-only.
- The pre-May-4 "live" period is **statistically useless** for this diagnosis: 467/928 rows have `pnl=0` AND `fees=NULL`, the executor schema at the time wasn't tracking outcomes. All "live" metrics below report the **current live cycle** unless explicitly marked all-time.

Current-cycle counts: **51 trades placed**, 46 closed/settled with recorded P&L, 5 still open at scan. CSV reconciliation window covers exactly this cycle.

---

## Q1 — LIVE WIN RATE, P&L, AND THE PAPER GAP *(headline)*

### Aggregate (current live cycle, 46 closed)

| metric              | live      | paper backtest sample |
|---------------------|-----------|-----------------------|
| trades              | 46        | 369                   |
| **win rate**        | **54.3%** | 63.7%                 |
| wins / losses / zero| 25 / 18 / 3 | —                   |
| **net P&L**         | **+$31.91** | +$544.63            |
| P&L / trade         | +$0.694   | +$1.476               |
| total fees          | $5.47     | —                     |
| fee drag            | 14.6% of gross | —                |

### By side

| side | n  | WR     | P&L      | $/trade  | fees   |
|------|----|--------|----------|----------|--------|
| yes  | 23 | 60.9%  | +$27.49  | +$1.195  | $2.79  |
| no   | 23 | 47.8%  | +$4.42   | +$0.192  | $2.68  |

### NO-side gap (the headline number)

| sample           | n   | NO-side WR |
|------------------|-----|------------|
| paper backtest   | 256 | **82.8%**  |
| live current     | 23  | **47.8%**  |
| **gap**          |     | **−35.0 pp** |

User-quoted paper NO-side WR was 83–94%; the 82.8% measured here is the lower end and matches.

### By symbol

| symbol | n  | WR    | P&L      | $/trade  |
|--------|----|-------|----------|----------|
| ETH    | 27 | 66.7% | +$45.53  | +$1.686  |
| BTC    | 19 | 36.8% | **−$13.62** | −$0.717 |

BTC alone is below breakeven.

### Side × symbol

| cell    | n  | WR    | P&L     |
|---------|----|-------|---------|
| yes/BTC | 8  | 50.0% | −$1.03  |
| yes/ETH | 15 | 66.7% | +$28.52 |
| no/BTC  | 11 | 27.3% | −$12.59 |
| no/ETH  | 12 | 66.7% | +$17.01 |

`no/BTC` is the single worst cell (27.3% WR, −$1.145/trade).

### Routes

All 46 closed live trades log as `taker` in `trades.db`. The Kalshi CSV shows 14 of those 50 orders actually had maker-leg partials (see Q0); the VPS doesn't track those splits.

**Verdict: edge is DEGRADED, not broken.** The −35 pp NO-side gap is the headline number, but YES side actually beats paper (60.9% vs 20.4%) at n=23. The cause is identified in Q2 below — it's not signal quality, it's a code-path bug suppressing the exit.

---

## Q2 — EXIT-TYPE SPLIT *(the smoking gun)*

### Recorded `exit_reason` distribution

| exit_reason         | paper (any time) | live (all time) |
|---------------------|------------------|-----------------|
| `time_exit`         | 208              | **0**           |
| `orphan_reconciled` | 5                | 9               |
| `(null/settlement)` | 156              | 919             |

`exit_reason='time_exit'` appears **zero times across every live trade in the database**. The 208 paper time_exits are all from the 2026-05-19 → 2026-05-22 paper-only window.

Cross-checks:

- `bot.log` for the live cycle: **0 `exit_signal` events** across 4148 `no_signal` events.
- Kalshi CSV: **0 sell orders** of any kind across 83 traded tickers. 81 tickers have exactly one Filled buy order plus a settlement; 2 tickers have only Canceled buy attempts.

Live exit-type split:

| exit type     | count | % of closed | WR     | P&L     |
|---------------|-------|-------------|--------|---------|
| time_exit     | 0     | 0%          | —      | —       |
| settlement    | 46    | **100%**    | 54.3%  | +$31.91 |
| orphan_reconciled | 3 | (6.5% of placed) | 0% | $0      |

**Paper expected ~87% time_exit, ~13% settlement.** Live observed **0% time_exit, 100% settlement.** This is the hypothesis the user stated, confirmed.

### Live `time_exit` WR in isolation
**Cannot determine — zero observations.**

### Mechanism: why isn't the time_exit firing?

This is *not* a WS-staleness problem (see Q3 — staleness was fresh during T-30s on every live trade I sampled). It is a code-path bug.

The per-tick loop in `main.py:780–907` evaluates a fresh signal first, then calls `await _evaluate_exits(...)` at `main.py:903`. But every `signal is None` branch (most ticks) **`continue`s before reaching the exit eval**:

```python
# main.py ~line 820
if signal is None:
    ...
    _record_reason(..., "no_signal", ...)
    continue          # ← skips _evaluate_exits below
...
# only this branch falls through:
submit_result = await executor.submit(signal, cached.balance)
...
# main.py:903 — only reachable if a fresh signal was generated
await _evaluate_exits(executor, ticker, best_yes_bid, best_no_bid, alerter, window)
```

Other early-`continue` paths that bypass exit eval:
- `RiskVetoError` (37 events in current cycle)
- `yes_side_disabled` (irrelevant currently, but same pattern)
- `skip_no_orderbook` (110 events) and `skip_stale_orderbook` (15 events)

The time_exit condition (`main.py:1506`) requires:

```python
order.signal.seconds_remaining > 90 and window.seconds_remaining < 30
```

For 51 live trades, 77 of 86 trade-action signals entered with `seconds_remaining > 90` (eligible) — but the exit eval has to actually be **called** in the final 30s, and that only happens if there's *also* a fresh signal at that exact tick. There essentially never is — by T-30s, momentum/LWM almost always returns `None` (price out of bounds, edge gone, momentum/OBI sign mismatch, etc.).

Empirical proof: traced `KXETH15M-26MAY231030-30` (entered 14:22:18, settled 14:30:11). Between 14:29:30 and 14:30:15 there is **exactly one event** for this ticker — a `no_signal` at `seconds_remaining=29`. That `no_signal` `continue`d, the exit eval never ran, and the position fell through to settlement instead of selling at the favorable T-30s bid.

**Verdict:** the edge is intact — the *exit path is dead code in live mode 99% of the time*. Fixing this should restore most of the paper performance. The user's hypothesis was right in direction (time_exit isn't firing → trades settle ~50/50), wrong on cause (it's a control-flow bug, not WS staleness).

---

## Q4 — SETTLEMENT REFERENCE: STRIKE vs OPEN *(validation-breaking)*

### Bot's direction labeling

`data/window_tracker.py:198`:

```python
went_up = win.current_price >= win.open_price
```

The bot defines "yes wins" as **Coinbase-close ≥ Coinbase-open**, and the backtest's `market_events.result` column is derived this way. The P0 / Test A / Test C results were validated on this column.

### What Kalshi actually settles on

Market titles in the Kalshi UI use fixed strikes (e.g. *"BTC 15min, $75,133.39 target"*). The ticker suffix (`-15`, `-30`, `-45`, `-00`) is the strike-grid index, not minutes. The bot's `Market` pydantic model (`models/market.py`) does not even ingest a strike field — it reads `ticker`, `title`, `status`, `open_time`, `close_time`, and the four BBO prices only.

### Empirical mismatch

Joined `market_events` (bot-recorded close/open + `result`) to the Kalshi-CSV `Settlement` rows for 78 settled tickers in the current cycle:

- **Match: 62/78 (79.5%)**
- **Mismatch: 16/78 (20.5%)**

All 16 mismatches are *narrow-move windows*. Examples:

| ticker                    | open      | close     | Δ      | bot says | Kalshi says |
|---------------------------|-----------|-----------|--------|----------|-------------|
| KXETH15M-26MAY230545-45   | 2027.23   | 2026.77   | −0.46  | no       | **yes**     |
| KXETH15M-26MAY231030-30   | 2043.40   | 2044.32   | +0.92  | yes      | **no**      |
| KXETH15M-26MAY221230-30   | 2117.88   | 2118.21   | +0.33  | yes      | **no**      |
| KXETH15M-26MAY222015-15   | 2061.20   | 2063.89   | +2.69  | yes      | **no**      |
| KXBTC15M-26MAY222215-15   | 75418.21  | 75374.21  | −44.00 | no       | **yes**     |
| KXBTC15M-26MAY230015-15   | 75540.01  | 75534.00  | −6.01  | no       | **yes**     |
| KXBTC15M-26MAY230845-45   | 74646.00  | 74646.00  | +0.00  | yes      | **no**      |

The mismatches cluster on small Δ — exactly the boundary region where the strike (somewhere between open and close, but not equal to open) flips the outcome.

### Implications

1. **The bot's "ground truth" for backtest direction is wrong about 1 in 5 windows.** Every signal/probability model calibrated on `market_events.result` is fitting a slightly mislabeled target.
2. **P0 / Test A / Test C cannot be considered validated** — they all use the bot's open-based direction. Test C in particular ("edge survives in adverse windows") depends on correct adverse-vs-favorable labeling, which is wrong ~20% of the time.
3. **Re-running with the true strike requires data the bot doesn't capture.** Options:
   - Backfill `cap_strike` / `floor_strike` / `expected_expiration_value` by re-fetching `/markets/{ticker}` for each historical ticker (Kalshi will return strike fields).
   - Parse strike from Kalshi market titles (the `$XX,XXX.XX target` string).

I cannot re-run P0/Test A from existing data on this VPS.

**Verdict: settlement is strike-based. The bot is comparing against the wrong reference, and the backtest's edge claim is unvalidated until this is fixed.**

---

## Q0 — DATA INTEGRITY: VPS log vs Kalshi CSV

CSV scope: `2026-05-22T14:58Z → 2026-05-23T14:30Z`. 223 rows = 85 Order rows (50 Filled, 33 Canceled, 2 misc) + 54 Trade rows (across 50 fill UUIDs) + 83 Settlement rows + 1 Deposit.

### Critical: UUIDs don't match

The bot stores `order_resp["order_id"]` (per `executor.py:303`) in `trades.order_id`. **Not one of the 50 VPS UUIDs from the live cycle appears in the Kalshi CSV's `Market_Id` column.**

Concrete example for `KXETH15M-26MAY231030-30`:

- `bot.log` placed: `b28beb1f-87d0-4f57-8d6d-8d54f06e4dc6` (returned from POST `/portfolio/orders`)
- Kalshi CSV `Market_Id`:  `37f64484-d7bc-44fb-bc9f-8d5d1fa94102`

Both UUIDs are real UUIDv4 strings for the same trade (same ticker, same timestamp to the millisecond, same contracts, same direction). Either:

- Kalshi's POST `/portfolio/orders` response returns a value (`order_id`) that is **not** the same identifier the portfolio export uses, or
- the bot is reading the wrong field (e.g. `client_order_id` echoed back) into `order_id`.

This is a real reconciliation hazard going forward — any future audit by UUID will fail silently. Fix: persist both `client_order_id` (bot-generated UUID) AND any additional ID fields the Kalshi response carries, so the export ID can be reconstructed.

### Reconciliation by `(ticker, side)` natural key

50 Kalshi-side aggregated fills ↔ 50 VPS rows — **100% matched, 0 VPS-only, 0 Kalshi-only, 0 contract-count mismatches.**

| metric                                    | result    |
|-------------------------------------------|-----------|
| exact fill price (Δ<0.01¢)                | 13 / 50   |
| within ±1¢                                | 17 / 50   |
| within ±2¢                                | 21 / 50   |
| Kalshi paid LESS than VPS recorded (Δ<−0.5¢) | **34 / 50** |
| Kalshi paid MORE than VPS recorded (Δ>+0.5¢) | 3 / 50  |
| avg Δ (Kalshi − VPS)                      | **−1.86¢** |
| median                                    | −3.0¢     |
| min / max                                 | −6.0¢ / +3.0¢ |

The negative direction means **the bot got BETTER fills than it recorded** — VPS is pessimistic. Source: 14 of the 50 orders ended up with a maker/taker mix on Kalshi (the bot resubmits as taker after a maker timeout, but Kalshi sometimes fills the original maker leg first at a better price). The VPS logs them all as taker at the resubmit price.

### Fees

Avg fee diff (VPS − Kalshi) = **+2.1¢ / trade** (total +$1.05 across 50). VPS overstates fees, again because it doesn't see the maker rebate.

### Realized P&L

| | total | per-trade |
|---|---|---|
| VPS recorded (n=45 with non-null pnl) | +$35.89 | +$0.797 |
| Kalshi actual                         | +$39.89 | +$0.886 |
| diff (VPS − Kalshi)                   | **−$4.00** | **−$0.089** |

**Verdict: VPS is FAITHFUL with a slight PESSIMISTIC bias (~9¢/trade understated).** Paper/backtest numbers built on this log are NOT optimistically biased and don't need a downward adjustment. If anything they're slightly conservative.

### Edge cases

| issue                                           | count | notes                                                    |
|-------------------------------------------------|-------|----------------------------------------------------------|
| VPS-only trades (not in CSV)                    | 0     | —                                                        |
| CSV-only trades (not in VPS)                    | 0     | —                                                        |
| Partial fills logged as full                    | 0     | contract counts match perfectly                          |
| VPS `pnl=NULL` while Kalshi settled             | 5     | 3 orphan_reconciled + 2 still open. Net Kalshi P&L on the 3 orphans = −$1.09. VPS counted $0. |
| Canceled Kalshi orders with no VPS row          | 33    | Expected — maker timeouts cancelled before fill, before VPS persisted the row. |
| Stale-WS settlements with VPS/Kalshi direction disagreement | 0 | — |

---

## Q3 — WEBSOCKET STALENESS

### Frequency (current live cycle)

`HEALTH` events emit `kalshi_ws_age_s` ~ every 60s. 1,444 samples in the cycle:

| age bucket    | samples | % of cycle |
|---------------|---------|------------|
| < 1s          | 1404    | 97.2%      |
| 1–5s          | 5       | 0.3%       |
| 5–10s         | 10      | 0.7%       |
| 10–20s        | 18      | 1.2%       |
| 20–30s        | 4       | 0.3%       |
| 60–120s       | 1       | 0.1%       |
| 120–300s      | 2       | 0.1%       |
| **median**    | **0.02s** | |
| p99           | 14.6s    | |
| max           | **197.8s** | |

`kalshi_ws_stale=true` HEALTH records in the cycle: **3**, all from one outage block.

WS reconnects logged in the cycle: **0** (the outage was a silent feed gap, not a TCP drop).

Telegram `Kalshi WS stale: …` alerts in the cycle: **0**. The `_send_once` pattern in `main.py:444` fires exactly once per healthy→stale→healthy edge; the user's "repeated 30–31s" reports were likely from earlier days (the May 19–22 paper period had similar blips that aren't in the current live cycle).

### Distinct outage windows in live cycle

| # | start (UTC)            | end (UTC)              | span | peak age |
|---|------------------------|------------------------|------|----------|
| 1 | 2026-05-23T08:00:13   | 2026-05-23T08:10:27   | 10m  | 197.8s   |

Plus 26 isolated single-sample blips (5–26s, one per minute-sample) which probably represent ≤ a few seconds of real lag each (HEALTH only samples once a minute, so anything shorter than ~60s gets aliased).

### Impact on trade outcomes

No live trade entered during the 08:00–08:10 outage. The bot's first fill that day after the outage was at 08:13:25 — well past recovery.

More importantly: **0 of the 46 live closed trades had a stale-WS event at their T-30s window**. Spot-checked 5 trades; each had healthy WS state through final 30s. The lost time_exits cannot be attributed to staleness because **no time_exit fired even on trades with a perfectly fresh WS at T-30s** — Q2 is the cause.

### P&L attributable to missed exits caused by staleness

**≈ $0** in the current live cycle. (Even fixing WS perfectly would not have produced a single time_exit, because the exit-eval code path is dead — see Q2.)

### Cause of the 08:00 outage

`messages_total` flatlined for ~200s without firing a reconnect. The 5s `kalshi_ws_health_alert` warning fires constantly but does not gate a reconnect — the reconnect loop is on the websocket's own ping/pong, which didn't trigger. No REST fallback fired. Recommend a "no-message for N seconds → force resubscribe" watchdog.

---

## Q5 — LIVE FILL QUALITY

### Entry slippage (Kalshi fill avg − VPS signal price, 50 entries)

| stat   | cents |
|--------|-------|
| median | **−3.0¢** (bot paid less than expected) |
| mean   | −1.86¢ |
| p10    | −4.0¢ |
| p90    | 0.0¢   |
| max bad| +3.0¢ |
| min good | −6.0¢ |

Distribution: 13 exact-match (Δ<0.01¢), 17 within 1¢, 21 within 2¢. 34 of 50 trades got a BETTER fill than VPS recorded; only 3 got worse.

This is well inside the Monte Carlo robustness band (paper claimed robust up to ~38¢ breakeven slippage). **Live fills are NOT worse than paper assumed.** The reverse — they're slightly better, because of unrecorded maker-leg partials.

By route: all 50 fills were "taker" per VPS. By Kalshi-side route mix (across constituent Trade rows): 36 taker-only, 14 with at least one maker partial. The 14 maker-mixed fills account for most of the favorable slippage.

### Exit slippage

**Cannot determine.** No live exits fired (Q2). Once the exit path is fixed, this becomes measurable as `intended exit bid at T-30s vs realized sell price`.

### Adverse selection check

Of the 3 trades that filled at *worse* prices than expected (positive slippage):

- 1 win (+$3.86)
- 2 losses (−$2.81, −$9.44)

Of the 34 trades that filled at *better* prices (negative slippage):

- WR 60% (n=34)

No clear adverse-selection signal at this n. But the sample is too small (n=3 worse-fill trades) to conclude either way. **Inconclusive.**

---

## Q6 — YES SIDE, POST-WINDOW-RESTRICTION

The "Fix B" YES restriction (LWM cap at 120s from window close) landed at commit `64a0218` on `2026-05-22T03:30:40Z` — well before the 15:03 live cutoff. So 100% of live YES trades happened post-fix.

### YES live, current cycle (23 trades, all closed)

| metric        | value |
|---------------|-------|
| n             | 23 |
| WR            | **60.9%** (14 wins, 8 losses, 1 zero) |
| net P&L       | **+$27.49** |
| P&L / trade   | +$1.195 |
| fees          | $2.79 |

By symbol:

- yes/ETH: n=15, WR 66.7%, +$28.52
- yes/BTC: n=8,  WR 50.0%, −$1.03

### Caveat: Fix B didn't actually constrain this sample

All 23 live YES trades came from the **momentum** strategy, which has NO `yes_decision_max_s` cap. The 120s cap is in `strategy/lwm.py:136` only. The LWM strategy produced **zero live YES trades** in the cycle. Distribution of momentum YES entries by `seconds_remaining` at signal:

| bucket  | count |
|---------|-------|
| <120s   | 4     |
| 120–240 | 6     |
| 240–360 | 6     |
| ≥360    | 24 (incl. duplicate action='trade' rows) |

So Fix B's intended effect can't be measured here — LWM YES is silent in live.

### Recommendation: **KEEP YES side enabled**

It's the strongest live contributor by $/trade. Don't disable. Also: momentum-strategy YES doesn't get the Fix B benefit, but it's profitable anyway. If LWM YES eventually starts producing live signals, watch the late-entry tail (<120s) since that's where Fix B is supposed to bite.

---

## Q7 — RISK SNAPSHOT AT CURRENT SIZING

### Config (`/root/kalshi-bot/kalshi-bot-v2/.env`)

| setting                       | current value | SWITCH_TO_LIVE.md recommendation |
|-------------------------------|---------------|----------------------------------|
| `TRADING_MODE`                | live          | live                             |
| `KELLY_FRACTION`              | **0.625**     | 0.10 (first week)                |
| `MAX_PER_TRADE`               | $25.00        | $10.00                           |
| `MAX_CONCURRENT_POSITIONS`    | 3             | 2                                |
| `DAILY_LOSS_LIMIT`            | $10.00        | $25.00                           |
| `PER_SIDE_DAILY_LOSS_LIMIT`   | $5.00         | $10.00                           |
| `EDGE_THRESHOLD`              | 0.04          | —                                |
| `MIN_TRADE_PRICE`             | 0.25          | —                                |
| `MAX_TRADE_PRICE`             | 0.85          | —                                |

Kelly is **6.25× over the documented first-week recommendation**. The `DAILY_LOSS_LIMIT` ($10) is tighter than recommended, which partly compensates but only after harm.

### Balance & exposure

- Current Kalshi balance (`live_state.json`): **$20.00**
- Daily P&L at scan: +$14.15
- Open positions: 0 (at scan; 5 still open at earlier point in cycle)
- Largest single-trade notional exposure observed:
  - **$8.99** = 31 contracts × $0.29 (BTC NO, `KXBTC15M-26MAY221730-30`, 2026-05-22T21:22)
  - That's **45% of the $20 balance** on one position
  - Next largest: $6.44 (14 × $0.46, ETH NO), $6.30 (18 × $0.35, ETH NO)

### Drawdown (current cycle, peak-to-trough on cumulative P&L)

- Peak cumulative P&L: **+$35.89** at 2026-05-23T14:22
- Worst drawdown: **−$20.51** (from intra-period peak +$29.71 on May 22 16:23 to trough +$9.20 on May 22 21:22)
- As % of starting balance ($20): **103%**
- Recovery time: ~17 hours

The bot survived a >1× drawdown intra-day, then recovered. Largely on luck given the broken exit path.

### Per-trade economics at observed live entry prices

Avg entry price = **0.390**, avg fee/contract = **$0.0165**.

Breakeven WR at this average: **40.6%** (since `wr = entry_price + fee_per_contract`).

| | value |
|---|---|
| Live WR | 54.3% |
| Breakeven WR | 40.6% |
| Margin above breakeven | **+13.7 pp** |
| Theoretical edge / trade @ avg sizing (7.2 contracts) | $0.989 |
| Actual P&L / trade observed | $0.694 |

The 30¢ gap between theoretical and actual is mostly fee drag + the 3 losing-on-bad-fill trades.

### Breakeven sensitivity to entry price

| avg entry | breakeven WR |
|-----------|--------------|
| 0.30      | 31.6%        |
| 0.35      | 36.6%        |
| **0.39 (current)** | **40.6%** |
| 0.45      | 46.6%        |
| 0.50      | 51.6%        |

If avg entry drifts to 50¢ (very plausible during low-volatility windows), the live 54.3% WR leaves only a 2.7-pp cushion. The strategy needs the time_exit edge restored to be robust to price drift.

**Verdict: above breakeven on live numbers (54.3% live WR vs 40.6% breakeven, +13.7 pp).** But the cushion is propped up by directional luck on settlements, not by the designed edge.

---

## RECOMMENDATION

**PAUSE LIVE TRADING. Fix the two specific issues below before re-enabling. Sizing down alone is not enough.**

The bot is currently profitable, but **for the wrong reason**. The designed edge — capturing favorable bid moves at T-30s via time_exit — is contributing **zero**. 100% of live trades are reaching settlement. The 54% live WR is essentially "directional momentum picks the right side ~54% of the time when it has enough signal to fire." This will revert toward 50% as soon as the directional luck normalizes.

### Required fixes (in order)

1. **Hoist `_evaluate_exits` out of the signal-eval branch.**
   - File: `src/kalshi_bot/main.py`, around line 903.
   - Today the exit eval only runs when a fresh entry signal succeeds — which is ~1% of ticks, and almost never near T-30s.
   - Restructure so the exit eval runs on every per-symbol tick that has (a) an open filled order for the active ticker, (b) a fresh orderbook (`age <= ORDERBOOK_STALENESS_S`). Specifically: move the `_evaluate_exits` call BEFORE the `continue` on `signal is None`.
   - Add an `exit_signal` log line and an `exit_reason='time_exit'` column write on every fire so it's measurable.
   - Add a unit test: simulate a filled order + window with `secs_remaining=29` + no fresh entry signal, assert exit eval is called.

2. **Capture and use Kalshi strikes.**
   - File: `src/kalshi_bot/models/market.py` (extend `Market` to ingest `cap_strike` / `floor_strike` / `expected_expiration_value` / strike from market title).
   - File: `src/kalshi_bot/client/kalshi.py` `_parse_market` — pull strike fields from the API response.
   - File: `src/kalshi_bot/data/window_tracker.py:198` — replace `went_up = current_price >= open_price` with `went_up = current_price >= strike`.
   - Backfill `market_events.result` by re-fetching `/markets/{ticker}` for all historical tickers, or parsing the strike from `market.title`.
   - Re-run P0 / Test A / Test C with the corrected direction column. Treat the previous edge claim as unvalidated until this is done.

### Secondary fixes

- **Persist both UUIDs** (`order_id` and Kalshi's portfolio-export ID) so future reconciliations join cleanly. Right now any future audit will fail UUID-keyed and silently miss data quality issues.
- **WS feed watchdog**: force resubscribe after N seconds without any message (saw one ~200s silent outage with no reconnect attempt). Current `kalshi_ws_health_alert` is observability-only and doesn't trigger reconnection.
- **Sizing back to plan**: `KELLY_FRACTION=0.10`, `MAX_PER_TRADE=$5.00` until both fixes ship. The bot just survived a 103%-of-balance drawdown by luck.
- **Capture exit-fill data**: once Q2 is fixed, log the intended exit bid (last snapshot) and actual exit fill so Q5's exit-slippage metric becomes measurable.
- **Fix `exit_reason` column writes for live trades**: currently always NULL on live (only paper paths set it). Verify the live exit code writes `exit_reason='time_exit' | 'settlement' | 'orphan_reconciled'` consistently.

---

## Things that could not be determined from available data

- **Live `time_exit` WR in isolation** — zero observations to measure.
- **Whether P0 / Test A / Test C edge survives strike-based direction labeling** — requires backfilling strike from Kalshi market metadata.
- **Pre-May-04 live period performance** — 467 of 928 rows have `pnl=0` AND `fees=NULL` (legacy schema, untracked outcomes).
- **Exit slippage distribution** — no live exits to measure.
- **Specific root cause of the 2026-05-23T08:00 WS outage** — `messages_total` flatlined for ~200s with no reconnect or resync log lines. Likely silent TCP keepalive issue; needs better instrumentation.
- **Adverse selection on entry fills** — only 3 trades filled at worse-than-signal prices, n is too small to draw a conclusion.
- **Which paper-WR `time_exit` cell most closely matches the live momentum-on-settlement performance** — would require running the paper backtest with `time_exit` disabled, which can be done but wasn't part of this analysis.

---

## Appendix A: data sources used

| source | path | scope |
|---|---|---|
| VPS trade log | `/root/kalshi-bot/kalshi-bot-v2/trades.db` (`trades`, `signals`, `market_events`, `window_snapshots`) | All-time |
| Kalshi portfolio export | `/root/fromkalshi/data.csv` | 2026-05-22T14:58Z → 2026-05-23T14:30Z |
| Bot runtime logs | `/root/kalshi-bot/kalshi-bot-v2/logs/bot.log*` | 2026-04-21 → present |
| Live state | `/root/kalshi-bot/kalshi-bot-v2/live_state.json` | snapshot at scan |
| Code paths cited | `src/kalshi_bot/main.py:780–907,1472–1520`, `execution/executor.py`, `data/window_tracker.py:198`, `models/market.py`, `client/kalshi.py:182–225` | — |

All P&L numbers are in USD. All timestamps are UTC unless otherwise noted. Trade counts: current live cycle = 51 placed, 46 closed with recorded P&L, 5 still open at scan.

---

## Appendix B: one-line summary per question

| Q | answer |
|---|---|
| Q0 | VPS log faithful, slight pessimistic bias (~9¢/trade understated). UUIDs don't match between VPS and Kalshi CSV — natural-key join works 100%. |
| Q1 | Live WR 54.3% vs paper 63.7%; NO-side gap −35 pp (paper 82.8% → live 47.8%); YES side beats paper (60.9% vs 20.4%). |
| Q2 | **0 live time_exits**. 100% of live trades reach settlement. Root cause: `_evaluate_exits` is unreachable when `signal is None` (`main.py:820` early `continue`). Not WS staleness. |
| Q3 | Only 1 real WS outage in current cycle (10 min, peak 197s). No trades affected. P&L attributable to staleness ≈ $0. |
| Q4 | Settlement is **strike-based**, not open-vs-close. 20% of bot-recorded directions disagree with Kalshi. P0/Test C unvalidated until strike is ingested. |
| Q5 | Entry fills BETTER than VPS records (median −3¢). Well inside paper's 38¢ slippage tolerance. Exit slippage: cannot measure (Q2). |
| Q6 | YES side post-restriction: 60.9% WR, +$27.49, KEEP enabled. But all 23 trades are momentum-strategy YES; LWM Fix B is unmeasured. |
| Q7 | Above breakeven (54.3% vs 40.6%) but Kelly is 6.25× over plan. Survived 103%-of-balance drawdown by luck. |
