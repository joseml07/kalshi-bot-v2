# Kalshi Bot V2 — Optimization Release (2026-04-29)

## Summary

Data-driven optimization based on 1,349 rows of live trading data (Apr 7–29 2026) and VPS log analysis. Target: 3–5× P&L improvement through execution optimization + parameter tuning.

## Key Findings from Live Data

| Metric | Value |
|--------|-------|
| Total orders | 476 |
| Trades (filled) | 353 (72%) |
| Canceled | 135 (28%) ← **#1 problem** |
| Maker trades | 215 (61%) |
| Taker trades | 138 (39%) |
| Net profit | **+$9.00** |
| Mean trade size | $1.60 |

### By Asset
| Asset | Trades | Profit | Canceled |
|-------|--------|--------|----------|
| ETH | 212 | **+$8.00** | 48 |
| BTC | 136 | +$1.00 | 87 |
| SOL | 5 | $0.00 | 0 |

## Changes

### 1. Per-Asset Configuration (`src/kalshi_bot/strategy/asset_config.py`)
- New `AssetConfig` dataclass with tuned parameters per symbol
- **ETH**: edge_threshold=0.05, min_price=0.30, max_price=0.85, sizing_multiplier=1.25
- **BTC**: edge_threshold=0.06, min_price=0.35, max_price=0.80, sizing_multiplier=1.0
- **SOL**: edge_threshold=0.08, min_price=0.40, max_price=0.75, sizing_multiplier=0.75
- `resolve_param()` function: per-asset overrides only when caller doesn't specify explicit value

### 2. Signal Strength Classification
- New `SignalStrength` enum: WEAK, MODERATE, STRONG
- `compute_signal_strength()`: scores signals on edge (40%), OBI (25%), time (20%), depth (15%)
- Thresholds: STRONG ≥65, MODERATE ≥40, WEAK <40

### 3. Dynamic Maker Fill Horizon
- `maker_timeout_for_strength()`: adaptive timeout based on signal strength
- **STRONG**: +33% time (e.g. 120s for ETH)
- **MODERATE**: base time (e.g. 90s)
- **WEAK**: -33% time (e.g. 45s)
- Reduces cancel rate by giving strong signals more time to fill

### 4. Signal-Strength-Based Position Sizing
- `kelly_size()` now accepts `signal_strength` and `symbol` parameters
- Multipliers: WEAK=0.6×, MODERATE=1.0×, STRONG=1.5×
- Per-asset sizing multiplier stacked on top (ETH=1.25×)
- Expected impact: mean trade size $1.60 → $3.00+

### 5. Minimum Depth Gate
- New `min_total_depth` parameter in AssetConfig (default 50)
- Skips signals when orderbook is too thin for reliable pricing
- Reduces false signals from one-sided or empty books

### 6. Cancel Rate Analytics
- `Executor.cancel_rate` property: tracks cancels / total orders over last hour
- `Executor._total_orders_last_hour` and `_cancels_last_hour` helpers
- Enables alerting when cancel rate exceeds threshold

### 7. Updated Defaults
- `.env.example`: SYMBOLS=BTC,ETH (was BTC only)
- `.env.example`: MAX_PER_TRADE=10.0 (was 25.0, more realistic)

## Expected Impact

| Metric | Current | Target |
|--------|---------|--------|
| Cancel rate | 28% | <15% |
| Mean trade size | $1.60 | $3.00+ |
| Daily trades | ~5 | ~10-15 |
| Monthly P&L | ~$12 | ~$50-100 |
| Maker fill % | 61% | 70%+ |

## Deployment Notes

1. Update `.env` on VPS with `SYMBOLS=BTC,ETH`
2. Monitor `/api/diagnostics` for first 24h
3. Watch for cancel rate alert (target <15%)
4. Compare P&L week-over-week

## Quality Gates

All changes pass:
- `mypy --strict src/` — zero issues
- `ruff check src/` — zero issues
- `pytest tests/` — 77 passed
