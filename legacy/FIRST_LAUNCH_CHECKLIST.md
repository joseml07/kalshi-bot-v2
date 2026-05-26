# First Launch Checklist (Paper + Real Balance)

## Current launch profile

- `KALSHI_ENV=prod`
- `TRADING_MODE=paper`
- `SYMBOLS=BTC`
- Telegram disabled
- Discord webhook alerting enabled (webhook URL still needed)
- OpenRouter enabled with `deepseek/deepseek-chat`

## Why this is safe

`TRADING_MODE=paper` keeps execution in paper path (no real order placement), while
`KALSHI_ENV=prod` lets balance/risk use your real account balance for realistic sizing.

## Required before launch

1. Fill `DISCORD_WEBHOOK_URL` in `.env`.
2. Confirm private key exists:
   - `/home/psypolatic/kalshi/new/kalshi_private_key.pem`
3. Ensure your Kalshi API key has read and trade permissions.

## Start commands

Terminal A:

```bash
PYTHONPATH=src python -m kalshi_bot.main
```

Terminal B:

```bash
PYTHONPATH=src python -m kalshi_bot.dashboard
```

## Smoke-check after start (first 2-5 minutes)

- Dashboard opens at `http://localhost:8080`
- Live cards show BTC window and model fields updating
- `logs/bot.log` has no repeated exceptions
- Discord receives startup and subsequent alerts (when events occur)

## Optional next phase

Build Phase 11 Discord bot (slash commands + graph snapshots).
For first launch, webhook is intentionally simpler and lower-risk.
