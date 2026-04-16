"""Discord webhook alerter for bot events."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

import httpx

from kalshi_bot.strategy.signals import Signal

logger = logging.getLogger(__name__)


class DiscordWebhookAlerter:
    """Sends bot alerts to a Discord webhook.

    This is intentionally one-way (alerts only). For first launch, this is the
    fastest and most reliable option versus a full interactive Discord bot.
    """

    def __init__(self, webhook_url: str) -> None:
        self._webhook_url = webhook_url
        self._client = httpx.AsyncClient(timeout=10.0)

    async def poll_commands(self) -> None:
        """No-op for interface parity with Telegram alerter."""
        return None

    async def close(self) -> None:
        await self._client.aclose()

    async def _send(self, text: str) -> None:
        if not self._webhook_url:
            return
        try:
            resp = await self._client.post(self._webhook_url, json={"content": text})
            if resp.status_code not in (200, 204):
                logger.warning("Discord send failed: %s %s", resp.status_code, resp.text)
        except Exception:
            logger.exception("Discord send error")

    async def bot_started(self, mode: str) -> None:
        await self._send(f"🟢 **Bot Started**\nMode: `{mode}`")

    async def bot_stopped(self) -> None:
        await self._send("🛑 **Bot Stopped**")

    async def trade_placed(self, signal: Signal, contracts: int, order_id: str) -> None:
        await self._send(
            "📈 **Trade Placed**\n"
            f"Ticker: `{signal.ticker}`\n"
            f"Side: **{signal.side.value.upper()}** x{contracts}\n"
            f"Price: ${signal.kalshi_price}\n"
            f"Edge: {signal.net_edge:.1%}\n"
            f"Route: `{signal.route}`\n"
            f"Order: `{order_id}`"
        )

    async def trade_exited(self, ticker: str, side: str, contracts: int, reason: str) -> None:
        await self._send(
            "🚪 **Position Exited**\n"
            f"Ticker: `{ticker}`\n"
            f"Side: **{side.upper()}** x{contracts}\n"
            f"Reason: {reason}"
        )

    async def trade_settled(self, ticker: str, won: bool, pnl: Decimal) -> None:
        result = "WIN" if won else "LOSS"
        sign = "+" if pnl >= 0 else ""
        await self._send(
            f"✅ **Settled [{result}]**\n"
            f"Ticker: `{ticker}`\n"
            f"P&L: {sign}${pnl}"
        )

    async def trade_failed(
        self, ticker: str, side: str, contracts: int, reason: str
    ) -> None:
        await self._send(
            "⚠️ **Trade didn't go through**\n"
            f"Ticker: `{ticker}`\n"
            f"Side: **{side.upper()}** x{contracts}\n"
            f"Reason: {reason}"
        )

    async def window_analyzed(
        self,
        symbol: str,
        open_time: datetime,
        close_time: datetime,
        open_price: float,
        close_price: float,
        signals_in_window: int,
        trades_in_window: int,
        paper_pnl: float,
        commentary: str,
    ) -> None:
        price_change_pct = (close_price - open_price) / open_price if open_price > 0 else 0.0
        result = "UP" if close_price >= open_price else "DOWN"
        body = (
            f"🧠 **Window Analysis** {symbol} {open_time.strftime('%H:%M')}-{close_time.strftime('%H:%M')} UTC\n"
            f"Δ: {price_change_pct:+.4%} {result}\n"
            f"Signals: {signals_in_window} | Trades: {trades_in_window} | P&L: ${paper_pnl:+.4f}"
        )
        if commentary:
            body += f"\n{commentary}"
        await self._send(body)
