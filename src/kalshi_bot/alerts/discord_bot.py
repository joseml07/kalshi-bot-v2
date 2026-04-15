"""Interactive Discord bot (slash commands + chart attachments)."""

# mypy: disable-error-code=untyped-decorator

from __future__ import annotations

import logging
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable

import discord
import httpx
from discord import app_commands

from kalshi_bot.alerts.charts import (
    chart_daily,
    chart_edge,
    chart_pnl,
    chart_routes,
    chart_scatter,
    chart_winrate,
)
from kalshi_bot.strategy.signals import Signal

logger = logging.getLogger(__name__)

CommandBuilder = Callable[[], Awaitable[str]]
ArgCommandBuilder = Callable[[list[str]], Awaitable[str]]


class DiscordBotAlerter:
    """Discord bot wrapper providing alerts and slash commands."""

    def __init__(self, bot_token: str, channel_id: str) -> None:
        self._token = bot_token
        self._channel_id = int(channel_id)
        intents = discord.Intents.default()
        self._client = discord.Client(intents=intents)
        self._tree = app_commands.CommandTree(self._client)
        self._channel: Any | None = None

        self._commands: dict[str, CommandBuilder] = {}
        self._arg_commands: dict[str, ArgCommandBuilder] = {}

        @self._client.event
        async def on_ready() -> None:
            logger.info("discord_bot_ready user=%s", self._client.user)
            self._channel = self._client.get_channel(self._channel_id)
            if self._channel:
                logger.info(
                    "discord_channel_acquired name=%s",
                    self._channel.name if hasattr(self._channel, "name") else "unknown",
                )
            else:
                logger.warning(
                    "discord_channel_not_found_in_cache id=%s", self._channel_id
                )
            try:
                await self._tree.sync()
            except Exception:
                logger.exception("discord_sync_failed")

    def register(self, name: str, handler: CommandBuilder) -> None:
        self._commands[name] = handler

    def register_with_args(self, name: str, handler: ArgCommandBuilder) -> None:
        self._arg_commands[name] = handler

    def register_default_slash_commands(self) -> None:
        """Registers phase-12 slash command surface."""

        async def _send_text(interaction: discord.Interaction, text: str) -> None:
            if interaction.response.is_done():
                await interaction.followup.send(text)
            else:
                await interaction.response.send_message(text)

        @self._tree.command(name="status", description="Bot status")
        async def status_cmd(interaction: discord.Interaction) -> None:
            handler = self._commands.get("status")
            if handler is None:
                await _send_text(interaction, "Status command not registered.")
                return
            await _send_text(interaction, await handler())

        @self._tree.command(name="pnl", description="Daily PnL")
        async def pnl_cmd(interaction: discord.Interaction) -> None:
            handler = self._commands.get("pnl")
            if handler is None:
                await _send_text(interaction, "PnL command not registered.")
                return
            await _send_text(interaction, await handler())

        @self._tree.command(name="stats", description="Session statistics")
        async def stats_cmd(interaction: discord.Interaction) -> None:
            handler = self._commands.get("stats")
            if handler is None:
                await _send_text(interaction, "Stats command not registered.")
                return
            await _send_text(interaction, await handler())

        @self._tree.command(name="trades", description="Last 10 trades")
        async def trades_cmd(interaction: discord.Interaction) -> None:
            handler = self._commands.get("trades")
            if handler is None:
                await _send_text(interaction, "Trades command not registered.")
                return
            await _send_text(interaction, await handler())

        @self._tree.command(name="signals", description="Last 10 signals")
        async def signals_cmd(interaction: discord.Interaction) -> None:
            handler = self._commands.get("signals")
            if handler is None:
                await _send_text(interaction, "Signals command not registered.")
                return
            await _send_text(interaction, await handler())

        @self._tree.command(name="window", description="Window state")
        async def window_cmd(interaction: discord.Interaction) -> None:
            handler = self._commands.get("window")
            if handler is None:
                await _send_text(interaction, "Window command not registered.")
                return
            await _send_text(interaction, await handler())

        @self._tree.command(name="config", description="Runtime config")
        async def config_cmd(interaction: discord.Interaction) -> None:
            handler = self._commands.get("config")
            if handler is None:
                await _send_text(interaction, "Config command not registered.")
                return
            await _send_text(interaction, await handler())

        @self._tree.command(name="maker", description="Maker routing metrics")
        async def maker_cmd(interaction: discord.Interaction) -> None:
            handler = self._commands.get("maker")
            if handler is None:
                await _send_text(interaction, "Maker command not registered.")
                return
            await _send_text(interaction, await handler())

        @self._tree.command(name="kill", description="Activate kill switch")
        async def kill_cmd(interaction: discord.Interaction) -> None:
            handler = self._commands.get("kill")
            if handler is None:
                await _send_text(interaction, "Kill command not registered.")
                return
            await _send_text(interaction, await handler())

        @self._tree.command(name="resume", description="Deactivate kill switch")
        async def resume_cmd(interaction: discord.Interaction) -> None:
            handler = self._commands.get("resume")
            if handler is None:
                await _send_text(interaction, "Resume command not registered.")
                return
            await _send_text(interaction, await handler())

        @self._tree.command(name="newsession", description="Start new session")
        async def newsession_cmd(interaction: discord.Interaction) -> None:
            handler = self._commands.get("newsession")
            if handler is None:
                await _send_text(interaction, "newsession not registered.")
                return
            await _send_text(interaction, await handler())

        @self._tree.command(name="analysis", description="Last window analyses")
        async def analysis_cmd(interaction: discord.Interaction) -> None:
            handler = self._commands.get("analysis")
            if handler is None:
                await _send_text(interaction, "analysis not registered.")
                return
            await _send_text(interaction, await handler())

        @self._tree.command(name="data", description="Data collection table counts")
        async def data_cmd(interaction: discord.Interaction) -> None:
            handler = self._commands.get("data")
            if handler is None:
                await _send_text(interaction, "data not registered.")
                return
            await _send_text(interaction, await handler())

        @self._tree.command(
            name="symbols", description="Enabled symbols + window state"
        )
        async def symbols_cmd(interaction: discord.Interaction) -> None:
            handler = self._commands.get("symbols")
            if handler is None:
                await _send_text(interaction, "symbols not registered.")
                return
            await _send_text(interaction, await handler())

        @self._tree.command(name="ip", description="Server public IP and dashboard URL")
        async def ip_cmd(interaction: discord.Interaction) -> None:
            handler = self._commands.get("ip")
            if handler is None:
                await _send_text(interaction, "ip not registered.")
                return
            await _send_text(interaction, await handler())

        @self._tree.command(name="set", description="Set runtime config key")
        @app_commands.describe(key="Setting key", value="Value")
        async def set_cmd(
            interaction: discord.Interaction, key: str, value: str
        ) -> None:
            handler = self._arg_commands.get("set")
            if handler is None:
                await _send_text(interaction, "set command not registered.")
                return
            await _send_text(interaction, await handler([key, value]))

        @self._tree.command(name="chart", description="Render a performance chart")
        @app_commands.describe(
            kind="One of: pnl, winrate, scatter, routes, edge, daily",
        )
        async def chart_cmd(interaction: discord.Interaction, kind: str) -> None:
            kind_norm = kind.strip().lower()
            chart_map = {
                "pnl": chart_pnl,
                "winrate": chart_winrate,
                "scatter": chart_scatter,
                "routes": chart_routes,
                "edge": chart_edge,
                "daily": chart_daily,
            }
            renderer = chart_map.get(kind_norm)
            if renderer is None:
                await _send_text(
                    interaction,
                    "Unknown chart. Use: pnl, winrate, scatter, routes, edge, daily",
                )
                return

            await interaction.response.defer()
            image = renderer()
            tmp = Path("/tmp") / image.filename
            tmp.write_bytes(image.content)
            try:
                file = discord.File(fp=tmp, filename=image.filename)
                await interaction.followup.send(file=file)
            finally:
                tmp.unlink(missing_ok=True)

    async def start(self) -> None:
        await self._client.start(self._token)

    async def stop(self) -> None:
        await self._client.close()

    async def poll_commands(self) -> None:
        """Parity no-op; commands handled internally by discord.py."""
        return None

    async def close(self) -> None:
        await self.stop()

    async def _send(self, text: str, embed: discord.Embed | None = None) -> None:
        channel = self._channel
        if channel is None:
            channel = self._client.get_channel(self._channel_id)
        if channel is None:
            try:
                channel = await self._client.fetch_channel(self._channel_id)
            except Exception:
                logger.warning(
                    "discord_channel_unavailable channel_id=%s", self._channel_id
                )
                return
            self._channel = channel

        send = getattr(channel, "send", None)
        if send is None:
            logger.warning(
                "discord_channel_not_messageable channel_id=%s", self._channel_id
            )
            return

        try:
            if embed is not None:
                await send(content=text if text else None, embed=embed)
            else:
                await send(text)
        except Exception:
            logger.exception("discord_send_failed")

    async def bot_started(self, mode: str) -> None:
        embed = discord.Embed(
            title="Bot Started", description=f"Mode: `{mode}`", color=0x3FB950
        )
        await self._send("", embed)

    async def bot_stopped(self) -> None:
        embed = discord.Embed(title="Bot Stopped", color=0xF85149)
        await self._send("", embed)

    async def trade_placed(self, signal: Signal, contracts: int, order_id: str) -> None:
        embed = discord.Embed(title="Trade Placed", color=0x3FB950)
        embed.add_field(name="Ticker", value=signal.ticker, inline=True)
        embed.add_field(
            name="Side", value=f"{signal.side.value.upper()} x{contracts}", inline=True
        )
        embed.add_field(name="Price", value=f"${signal.kalshi_price}", inline=True)
        embed.add_field(name="Edge", value=f"{signal.net_edge:.1%}", inline=True)
        embed.add_field(name="Route", value=signal.route, inline=True)
        embed.add_field(name="Order", value=order_id, inline=False)
        await self._send("", embed)

    async def trade_exited(
        self, ticker: str, side: str, contracts: int, reason: str
    ) -> None:
        embed = discord.Embed(title="Position Exited", color=0xD29922)
        embed.add_field(name="Ticker", value=ticker, inline=True)
        embed.add_field(name="Side", value=f"{side.upper()} x{contracts}", inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        await self._send("", embed)

    async def trade_settled(self, ticker: str, won: bool, pnl: Decimal) -> None:
        color = 0x3FB950 if won else 0xF85149
        result = "WIN" if won else "LOSS"
        embed = discord.Embed(title=f"Settled [{result}]", color=color)
        embed.add_field(name="Ticker", value=ticker, inline=True)
        sign = "+" if pnl >= 0 else ""
        embed.add_field(name="PnL", value=f"{sign}${pnl}", inline=True)
        await self._send("", embed)

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
        change_pct = (close_price - open_price) / open_price if open_price > 0 else 0.0
        color = 0x3FB950 if change_pct >= 0 else 0xF85149
        embed = discord.Embed(
            title=f"Window Analysis {symbol}",
            description=f"{open_time:%H:%M}-{close_time:%H:%M} UTC",
            color=color,
        )
        embed.add_field(name="Δ", value=f"{change_pct:+.4%}", inline=True)
        embed.add_field(name="Signals", value=str(signals_in_window), inline=True)
        embed.add_field(name="Trades", value=str(trades_in_window), inline=True)
        embed.add_field(name="Paper PnL", value=f"${paper_pnl:+.4f}", inline=True)
        if commentary:
            embed.add_field(name="AI", value=commentary[:1024], inline=False)
        await self._send("", embed)


def _get_session_start(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT started_at FROM sessions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return str(row[0]) if row else None


def make_status_command(
    risk_manager: Any, executor: Any, client: Any, settings: Any
) -> CommandBuilder:
    async def handler() -> str:
        try:
            balance = await client.get_balance()
        except Exception:
            balance = Decimal("?")
        kill = "YES" if Path("KILL_SWITCH").exists() else "no"
        return (
            f"**Bot Status**\n"
            f"Mode: {settings.trading_mode} | Env: {settings.kalshi_env}\n"
            f"Balance: ${balance}\n"
            f"Daily P&L: ${risk_manager.daily_pnl}\n"
            f"Open positions: {risk_manager.open_position_count}\n"
            f"Pending orders: {len(executor.pending_orders)}\n"
            f"Kill switch: {kill}"
        )

    return handler


def make_pnl_command(risk_manager: Any) -> CommandBuilder:
    async def handler() -> str:
        pnl = risk_manager.daily_pnl
        sign = "+" if pnl >= 0 else ""
        return f"**Daily P&L**\nP&L: {sign}${pnl}\nOpen positions: {risk_manager.open_position_count}"

    return handler


def make_balance_command(client: Any) -> CommandBuilder:
    async def handler() -> str:
        try:
            balance = await client.get_balance()
            return f"**Balance**\n${balance}"
        except Exception:
            return "Failed to fetch balance."

    return handler


def make_positions_command(client: Any) -> CommandBuilder:
    async def handler() -> str:
        try:
            positions = await client.get_positions()
        except Exception:
            return "Failed to fetch positions."
        if not positions:
            return "**Positions**\nNo open positions."
        lines = ["**Positions**"]
        for p in positions[:10]:
            ticker = p.get("ticker", "?")
            qty = p.get("position_fp", "0")
            lines.append(f"x{qty} `{ticker}`")
        return "\n".join(lines)

    return handler


def make_trades_command(db_path: str = "trades.db") -> CommandBuilder:
    async def handler() -> str:
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT 10"
            ).fetchall()
            conn.close()
        except Exception:
            return "No trade data yet."
        if not rows:
            return "**Trades**\nNo trades yet."
        lines = ["**Recent Trades**"]
        for r in rows:
            pnl_str = f"${r['pnl']}" if r["pnl"] else "pending"
            lines.append(
                f"{r['side'].upper()} x{r['contracts']} `{r['ticker']}` {pnl_str}"
            )
        return "\n".join(lines)

    return handler


def make_stats_command(db_path: str = "trades.db") -> CommandBuilder:
    async def handler() -> str:
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            session_start = _get_session_start(conn)
            where = "WHERE pnl IS NOT NULL"
            params: tuple[Any, ...] = ()
            if session_start:
                where += " AND timestamp >= ?"
                params = (session_start,)
            row = conn.execute(
                f"""SELECT
                     COUNT(*) as total,
                     SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins,
                     SUM(CASE WHEN CAST(pnl AS REAL) < 0 THEN 1 ELSE 0 END) as losses,
                     AVG(CAST(pnl AS REAL)) as avg_pnl,
                     SUM(CAST(pnl AS REAL)) as total_pnl,
                     COALESCE(SUM(CAST(fees AS REAL)), 0) as total_fees
                   FROM trades {where}""",  # noqa: S608
                params,
            ).fetchone()
            route_rows = conn.execute(
                """SELECT route, COUNT(*) as cnt,
                          SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins
                   FROM trades WHERE pnl IS NOT NULL GROUP BY route"""
            ).fetchall()
            conn.close()
        except Exception:
            return "No trade data yet."

        total = row["total"] or 0
        wins = row["wins"] or 0
        losses = row["losses"] or 0
        win_rate = (wins / total * 100) if total else 0.0
        avg_pnl = row["avg_pnl"] or 0.0
        total_pnl = row["total_pnl"] or 0.0
        fees = row["total_fees"] or 0.0
        route_bits: list[str] = []
        for rr in route_rows:
            cnt = rr["cnt"] or 0
            rwins = rr["wins"] or 0
            wr = int((rwins / cnt) * 100) if cnt else 0
            route_bits.append(f"{rr['route']}: {cnt} ({wr}% WR)")

        return (
            f"**Session Stats**\n"
            f"Trades: {total} ({wins}W/{losses}L)\n"
            f"Win rate: {win_rate:.1f}%\n"
            f"Avg P&L: ${avg_pnl:.2f}\n"
            f"Net P&L: ${total_pnl:.2f}\n"
            f"Fees: ${fees:.2f}\n"
            + ("Routes: " + " | ".join(route_bits) if route_bits else "")
        )

    return handler


def make_signals_command(db_path: str = "trades.db") -> CommandBuilder:
    async def handler() -> str:
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM signals ORDER BY id DESC LIMIT 10"
            ).fetchall()
            conn.close()
        except Exception:
            return "No signal data yet."
        if not rows:
            return "No signals evaluated yet."
        lines = ["**Recent Signals**"]
        for r in rows:
            lines.append(
                f"[{r['action']}] {r['side'].upper()} `{r['ticker'][-12:]}` edge={float(r['net_edge']) * 100:.1f}% {r['seconds_remaining']}s"
            )
        return "\n".join(lines)

    return handler


def make_maker_command(db_path: str = "trades.db") -> CommandBuilder:
    async def handler() -> str:
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            total_maker = conn.execute(
                "SELECT COUNT(*) as c FROM trades WHERE route = 'maker'"
            ).fetchone()["c"]
            promoted = conn.execute(
                "SELECT COUNT(*) as c FROM trades WHERE route = 'taker_promoted'"
            ).fetchone()["c"]
            pure_taker = conn.execute(
                "SELECT COUNT(*) as c FROM trades WHERE route = 'taker'"
            ).fetchone()["c"]
            conn.close()
        except Exception:
            return "Maker stats unavailable."
        attempts = int(total_maker)
        maker_fills = max(0, int(total_maker) - int(promoted))
        fill_rate = (maker_fills / attempts * 100) if attempts else 0.0
        return (
            "**Maker Routing**\n"
            f"Maker attempts: {attempts}\n"
            f"Maker fills (est): {maker_fills}\n"
            f"Promoted: {promoted}\n"
            f"Pure taker: {pure_taker}\n"
            f"Fill rate: {fill_rate:.1f}%"
        )

    return handler


def make_config_command(settings: Any) -> CommandBuilder:
    async def handler() -> str:
        return (
            "**Current Config**\n"
            f"Mode: {settings.trading_mode} | Env: {settings.kalshi_env}\n"
            f"Symbols: {settings.symbols}\n"
            f"Edge threshold: {settings.edge_threshold:.2f}\n"
            f"Time window: {settings.momentum_min_time}-{settings.momentum_max_time}s\n"
            f"Price range: ${settings.min_trade_price:.2f}-${settings.max_trade_price:.2f}\n"
            f"Maker first: {'ON' if settings.maker_first else 'off'}\n"
            f"Maker horizon: {settings.maker_fill_horizon_s}s\n"
            f"Exit stop loss: ${settings.exit_stop_loss:.2f}/contract"
        )

    return handler


_SETTABLE: dict[str, type] = {
    "edge_threshold": float,
    "exit_stop_loss": float,
    "min_time": int,
    "max_time": int,
    "logistic_k": float,
    "symbols": str,
    "min_price": float,
    "max_price": float,
    "maker_first": bool,
    "maker_fill_horizon_s": int,
}

_SETTING_MAP: dict[str, str] = {
    "edge_threshold": "edge_threshold",
    "exit_stop_loss": "exit_stop_loss",
    "min_time": "momentum_min_time",
    "max_time": "momentum_max_time",
    "logistic_k": "logistic_k",
    "symbols": "symbols",
    "min_price": "min_trade_price",
    "max_price": "max_trade_price",
    "maker_first": "maker_first",
    "maker_fill_horizon_s": "maker_fill_horizon_s",
}


def make_set_command(settings: Any) -> ArgCommandBuilder:
    async def handler(args: list[str]) -> str:
        if len(args) < 2:
            return "Usage: /set <key> <value>"
        key, raw_value = args[0].lower(), args[1]
        cast = _SETTABLE.get(key)
        if cast is None:
            return f"Unknown key '{key}'"
        try:
            if cast is bool:
                value: bool | int | float | str = raw_value.lower() in (
                    "true",
                    "1",
                    "yes",
                    "on",
                )
            else:
                value = cast(raw_value)
        except ValueError:
            return f"Invalid value '{raw_value}' for {key}"
        attr = _SETTING_MAP[key]
        object.__setattr__(settings, attr, value)
        return f"Set {key} = {value}"

    return handler


def make_kill_command() -> CommandBuilder:
    async def handler() -> str:
        Path("KILL_SWITCH").touch()
        return "Kill switch activated."

    return handler


def make_resume_command() -> CommandBuilder:
    async def handler() -> str:
        ks = Path("KILL_SWITCH")
        if ks.exists():
            ks.unlink()
            return "Kill switch removed."
        return "Kill switch not active."

    return handler


def make_newsession_command(db_path: str = "trades.db") -> CommandBuilder:
    async def handler() -> str:
        try:
            from datetime import datetime, timezone

            conn = sqlite3.connect(db_path)
            now = datetime.now(timezone.utc).isoformat()
            cur = conn.execute(
                "INSERT INTO sessions (started_at, label) VALUES (?, ?)", (now, None)
            )
            sid = cur.lastrowid
            conn.commit()
            conn.close()
            return f"New session #{sid} started."
        except Exception as exc:
            return f"Failed to start session: {exc}"

    return handler


def make_window_command(tracker: Any) -> CommandBuilder:
    async def handler() -> str:
        symbols = ["BTC", "ETH", "SOL"]
        lines = ["**Active Windows**"]
        found = False
        for sym in symbols:
            win = tracker.get_window(sym)
            if win is None:
                continue
            found = True
            lines.append(
                f"{sym}: ${win.current_price:,.2f} ({win.price_change_pct:+.4%}) {win.seconds_remaining}s left"
            )
        if not found:
            lines.append("No active windows.")
        return "\n".join(lines)

    return handler


def make_analysis_command(db_path: str = "trades.db") -> CommandBuilder:
    async def handler() -> str:
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM window_analyses ORDER BY id DESC LIMIT 5"
            ).fetchall()
            conn.close()
        except Exception:
            return "No analyses logged yet."
        if not rows:
            return "No analyses found."
        lines = ["**Last 5 Window Analyses**"]
        for r in rows:
            lines.append(
                f"{r['symbol']} {r['window_open'][11:16]}-{r['window_close'][11:16]} Δ {r['price_change_pct']:+.2%} {str(r['result']).upper()}"
            )
        return "\n".join(lines)

    return handler


def make_data_command(db_path: str = "trades.db") -> CommandBuilder:
    async def handler() -> str:
        try:
            conn = sqlite3.connect(db_path)
            tables = {
                "trades": "Trades",
                "signals": "Signals",
                "price_ticks": "Price Ticks",
                "orderbook_snapshots": "OB Snapshots",
                "window_snapshots": "Window Snaps",
                "market_events": "Market Events",
                "strategy_evals": "Strategy Evals",
                "window_analyses": "AI Analyses",
            }
            lines = ["**Data Collection**"]
            for table, label in tables.items():
                try:
                    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
                except sqlite3.OperationalError:
                    count = 0
                lines.append(f"{label}: {count:,}")
            conn.close()
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    return handler


def make_symbols_command(tracker: Any, settings: Any) -> CommandBuilder:
    async def handler() -> str:
        active = {s.strip() for s in settings.symbols.split(",")}
        all_symbols = ["BTC", "ETH", "SOL"]
        lines = ["**Symbols**"]
        for sym in all_symbols:
            enabled = "ON" if sym in active else "OFF"
            win = tracker.get_window(sym)
            if win is not None:
                status = (
                    f"${win.current_price:,.2f} "
                    f"({win.price_change_pct:+.4%}) {win.seconds_remaining}s left"
                )
            else:
                status = "no window"
            lines.append(f"{sym} [{enabled}] {status}")
        return "\n".join(lines)

    return handler


def make_ip_command() -> CommandBuilder:
    async def handler() -> str:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get("https://api.ipify.org")
                ip = resp.text.strip()
            return f"**Server Info**\nIP: `{ip}`\nDashboard: http://{ip}"
        except Exception:
            return "Failed to fetch public IP."

    return handler
