from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
import structlog

from kalshi_bot.strategy.signals import Signal

logger = structlog.get_logger()


class MultiAlerter:
    """Dispatches alerts to multiple alerters."""

    def __init__(self, alerters: list[Any]) -> None:
        self.alerters = [a for a in alerters if a is not None]

    def __bool__(self) -> bool:
        return bool(self.alerters)

    async def bot_started(self, mode: str) -> None:
        logger.info("multi_alerter_bot_started", count=len(self.alerters))
        await asyncio.gather(
            *(a.bot_started(mode) for a in self.alerters if hasattr(a, "bot_started"))
        )

    async def bot_stopped(self) -> None:
        await asyncio.gather(
            *(a.bot_stopped() for a in self.alerters if hasattr(a, "bot_stopped"))
        )

    async def trade_placed(self, signal: Signal, contracts: int, order_id: str) -> None:
        await asyncio.gather(
            *(
                a.trade_placed(signal, contracts, order_id)
                for a in self.alerters
                if hasattr(a, "trade_placed")
            )
        )

    async def trade_exited(
        self, ticker: str, side: str, contracts: int, reason: str
    ) -> None:
        await asyncio.gather(
            *(
                a.trade_exited(ticker, side, contracts, reason)
                for a in self.alerters
                if hasattr(a, "trade_exited")
            )
        )

    async def trade_settled(self, ticker: str, won: bool, pnl: Decimal) -> None:
        await asyncio.gather(
            *(
                a.trade_settled(ticker, won, pnl)
                for a in self.alerters
                if hasattr(a, "trade_settled")
            )
        )

    async def window_analyzed(self, *args: Any, **kwargs: Any) -> None:
        await asyncio.gather(
            *(
                a.window_analyzed(*args, **kwargs)
                for a in self.alerters
                if hasattr(a, "window_analyzed")
            )
        )

    async def close(self) -> None:
        await asyncio.gather(*(a.close() for a in self.alerters if hasattr(a, "close")))
