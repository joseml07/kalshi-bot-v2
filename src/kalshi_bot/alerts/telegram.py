"""Temporary Telegram alerter stubs.

Phase 7 replaces this file with full command/alert implementation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

from kalshi_bot.strategy.signals import Signal

CommandHandler = Callable[[], Awaitable[str]]
ArgCommandHandler = Callable[[list[str]], Awaitable[str]]


class TelegramAlerter:
    """No-op telegram alerter used until phase 7."""

    def __init__(self, bot_token: str, chat_id: str, discord_webhook_url: str = "") -> None:
        _ = (bot_token, chat_id, discord_webhook_url)

    def register(self, name: str, handler: CommandHandler) -> None:
        _ = (name, handler)

    def register_with_args(self, name: str, handler: ArgCommandHandler) -> None:
        _ = (name, handler)

    async def poll_commands(self) -> None:
        return None

    async def bot_started(self, mode: str) -> None:
        _ = mode

    async def bot_stopped(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def trade_placed(self, signal: Signal, contracts: int, order_id: str) -> None:
        _ = (signal, contracts, order_id)

    async def trade_exited(self, ticker: str, side: str, contracts: int, reason: str) -> None:
        _ = (ticker, side, contracts, reason)

    async def trade_settled(self, ticker: str, won: bool, pnl: Decimal) -> None:
        _ = (ticker, won, pnl)

    async def window_analyzed(
        self,
        symbol: str,
        open_time: Any,
        close_time: Any,
        open_price: float,
        close_price: float,
        signals_in_window: int,
        trades_in_window: int,
        paper_pnl: float,
        commentary: str,
    ) -> None:
        _ = (
            symbol,
            open_time,
            close_time,
            open_price,
            close_price,
            signals_in_window,
            trades_in_window,
            paper_pnl,
            commentary,
        )


def _noop_command(*_args: Any, **_kwargs: Any) -> CommandHandler:
    async def handler() -> str:
        return ""

    return handler


def _noop_arg_command(*_args: Any, **_kwargs: Any) -> ArgCommandHandler:
    async def handler(_argv: list[str]) -> str:
        return ""

    return handler


make_analysis_command = _noop_command
make_balance_command = _noop_command
make_cleardata_command = _noop_command
make_config_command = _noop_command
make_data_command = _noop_command
make_ip_command = _noop_command
make_kill_command = _noop_command
make_newsession_command = _noop_command
make_pnl_command = _noop_command
make_positions_command = _noop_command
make_resume_command = _noop_command
make_set_command = _noop_arg_command
make_signals_command = _noop_command
make_stats_command = _noop_command
make_status_command = _noop_command
make_symbols_command = _noop_command
make_trades_command = _noop_command
make_window_command = _noop_command
