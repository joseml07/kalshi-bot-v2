"""Telegram bot — alerts + interactive commands."""

from __future__ import annotations

import asyncio
import calendar as _calendar
import html
import json
import re
import sqlite3
from collections.abc import Callable, Coroutine
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import structlog

from kalshi_bot.strategy.signals import Signal

logger = structlog.get_logger()

TELEGRAM_API = "https://api.telegram.org"


def _html_to_discord(text: str) -> str:
    """Convert Telegram HTML formatting to Discord markdown."""
    text = text.replace("<b>", "**").replace("</b>", "**")
    text = text.replace("<code>", "`").replace("</code>", "`")
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = re.sub(r"<[^>]+>", "", text)
    return text


def _escape_html(value: Any) -> str:
    """Escape dynamic content for Telegram HTML parse mode."""
    return html.escape(str(value), quote=False)


CommandHandler = Callable[[], Coroutine[Any, Any, str]]
ArgCommandHandler = Callable[[list[str]], Coroutine[Any, Any, str]]


class TelegramAlerter:
    """Sends alerts and handles interactive commands via Telegram Bot API."""

    def __init__(self, bot_token: str, chat_id: str, discord_webhook_url: str = "") -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._discord_url = discord_webhook_url
        self._client = httpx.AsyncClient(timeout=10.0)
        self._offset = 0
        self._commands: dict[str, CommandHandler] = {}
        self._arg_commands: dict[str, ArgCommandHandler] = {}
        self._polling = False
        self._allowed_updates = [
            "message",
            "edited_message",
            "channel_post",
            "edited_channel_post",
        ]
        self._webhook_cleared = False

    def register(self, name: str, handler: CommandHandler) -> None:
        self._commands[name] = handler

    def register_with_args(self, name: str, handler: ArgCommandHandler) -> None:
        self._arg_commands[name] = handler

    async def poll_commands(self) -> None:
        self._polling = True
        asyncio.create_task(self._ensure_webhook_disabled())
        logger.info("telegram_poll_started")
        while self._polling:
            try:
                updates = await self._get_updates()
                for update in updates:
                    await self._handle_update(update)
                if not updates:
                    await asyncio.sleep(1)
            except Exception:
                logger.exception("Telegram poll error")
                await asyncio.sleep(5)

    async def _get_updates(self) -> list[dict[str, Any]]:
        url = f"{TELEGRAM_API}/bot{self._bot_token}/getUpdates"
        try:
            params = {
                "offset": str(self._offset),
                "timeout": "30",
                "allowed_updates": json.dumps(self._allowed_updates),
            }
            async with httpx.AsyncClient(timeout=40.0) as client:
                resp = await client.get(url, params=params)
            if resp.status_code != 200:
                logger.warning(
                    "telegram_get_updates_failed",
                    status=resp.status_code,
                    response=resp.text,
                )
                return []
            data = resp.json()
            if not data.get("ok", True):
                logger.warning("telegram_get_updates_not_ok", response=data)
                return []
            results: list[dict[str, Any]] = data.get("result", [])
            if results:
                logger.info(
                    "telegram_updates_received",
                    count=len(results),
                    max_update_id=results[-1].get("update_id"),
                )
            return results
        except httpx.TimeoutException:
            return []
        except Exception:
            logger.exception("Telegram getUpdates error")
            return []

    async def _ensure_webhook_disabled(self) -> None:
        if self._webhook_cleared:
            return
        url = f"{TELEGRAM_API}/bot{self._bot_token}/deleteWebhook"
        try:
            resp = await self._client.post(
                url,
                json={"drop_pending_updates": True},
                timeout=10.0,
            )
            if resp.status_code != 200:
                logger.warning(
                    "telegram_delete_webhook_failed",
                    status=resp.status_code,
                    response=resp.text,
                )
            else:
                data = resp.json()
                if not data.get("ok", True):
                    logger.warning("telegram_delete_webhook_not_ok", response=data)
        except Exception:
            logger.exception("telegram_delete_webhook_error")
        self._webhook_cleared = True

    async def _handle_update(self, update: dict[str, Any]) -> None:
        update_id: int = update["update_id"]
        self._offset = update_id + 1

        message = (
            update.get("message")
            or update.get("edited_message")
            or update.get("channel_post")
            or update.get("edited_channel_post")
        )
        if not message:
            logger.info("telegram_update_no_message", update=update)
            return

        chat_id = str(message.get("chat", {}).get("id", ""))
        text: str = message.get("text", "").strip()
        logger.info("telegram_received_message", chat_id=chat_id, expected_chat_id=self._chat_id, text=text)
        if chat_id != self._chat_id:
            return

        if not text.startswith("/"):
            return

        parts = text.split()
        cmd = parts[0].split("@")[0].lstrip("/").lower()
        args = parts[1:]

        if cmd == "help":
            await self._send(self._help_text())
            return

        arg_handler = self._arg_commands.get(cmd)
        if arg_handler is not None:
            try:
                response = await arg_handler(args)
                await self._send(response)
            except Exception:
                logger.exception("Command /%s failed", cmd)
                await self._send(f"Error running /{cmd}")
            return

        handler = self._commands.get(cmd)
        if handler is None:
            await self._send(f"Unknown command: /{cmd}\nSend /help to see available commands.")
            return

        try:
            response = await handler()
            await self._send(response)
        except Exception:
            logger.exception("Command /%s failed", cmd)
            await self._send(f"Error running /{cmd}")

    def _help_text(self) -> str:
        lines = ["<b>Available Commands</b>\n"]
        descriptions: dict[str, str] = {
            "status": "Bot status, balance, positions",
            "pnl": "Daily P&amp;L breakdown",
            "stats": "All-time win rate, avg P&amp;L, best/worst",
            "maker": "Maker routing and fill metrics",
            "signals": "Last 10 evaluated signals",
            "trades": "Last 10 trades",
            "analysis": "Recent 15-min window AI analyses",
            "balance": "Account balance",
            "positions": "Open positions",
            "config": "Show current settings",
            "window": "Active 15-min window state per symbol",
            "data": "Historical data collection stats",
            "symbols": "Active symbols and window state",
            "set": "Change a setting: /set &lt;key&gt; &lt;value&gt;",
            "newsession": "Start new tracking session (preserves old data)",
            "cleardata": "Wipe all trades and signals from database",
            "kill": "Activate kill switch (halt trading)",
            "resume": "Remove kill switch (resume trading)",
            "calendar": "Monthly P&amp;L calendar: /calendar [YYYY MM]",
            "ip": "Show server IP and dashboard URL",
            "help": "Show this message",
        }
        all_cmds = list(self._commands.keys()) + list(self._arg_commands.keys()) + ["help"]
        for cmd in [
            "status", "pnl", "calendar", "stats", "maker", "signals", "trades", "analysis", "window",
            "data", "symbols", "balance", "positions", "config", "set", "newsession",
            "cleardata", "kill", "resume", "help",
        ]:
            if cmd in all_cmds:
                lines.append(f"/{cmd} — {descriptions.get(cmd, '')}")
        return "\n".join(lines)

    async def _send(self, text: str) -> None:
        url = f"{TELEGRAM_API}/bot{self._bot_token}/sendMessage"
        try:
            payload = {"chat_id": self._chat_id, "text": text, "parse_mode": "HTML"}
            resp = await self._client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning("telegram_send_failed", status=resp.status_code, response=resp.text)
            else:
                logger.info("telegram_send_success")
        except Exception:
            logger.exception("telegram_send_error")

    async def _send_discord(self, text: str) -> None:
        if not self._discord_url:
            return
        try:
            resp = await self._client.post(self._discord_url, json={"content": _html_to_discord(text)})
            if resp.status_code not in (200, 204):
                logger.warning("Discord send failed: %s %s", resp.status_code, resp.text)
        except Exception:
            logger.exception("Discord send error")

    async def trade_placed(self, signal: Signal, contracts: int, order_id: str) -> None:
        text = (
            f"<b>Trade Placed</b>\n"
            f"Ticker: <code>{_escape_html(signal.ticker)}</code>\n"
            f"Side: {_escape_html(signal.side.value.upper())} x{contracts}\n"
            f"Price: ${signal.kalshi_price}\n"
            f"Edge: {signal.net_edge:.1%}\n"
            f"Strategy: {_escape_html(signal.strategy.value)}\n"
            f"Route: {_escape_html(signal.route)}\n"
            f"Order: <code>{_escape_html(order_id)}</code>"
        )
        await self._send(text)
        await self._send_discord(text)

    async def trade_exited(self, ticker: str, side: str, contracts: int, reason: str) -> None:
        text = (
            f"<b>EXIT</b>\n"
            f"Ticker: <code>{_escape_html(ticker)}</code>\n"
            f"Side: {_escape_html(side.upper())} x{contracts}\n"
            f"Reason: {_escape_html(reason)}"
        )
        await self._send(text)
        await self._send_discord(text)

    async def trade_settled(self, ticker: str, won: bool, pnl: Decimal) -> None:
        result = "WIN" if won else "LOSS"
        sign = "+" if pnl >= 0 else ""
        text = (
            f"<b>Settled [{_escape_html(result)}]</b>\n"
            f"Ticker: <code>{_escape_html(ticker)}</code>\n"
            f"P&L: {sign}${pnl}"
        )
        await self._send(text)
        await self._send_discord(text)

    async def trade_failed(
        self, ticker: str, side: str, contracts: int, reason: str
    ) -> None:
        text = (
            f"<b>Trade didn't go through</b>\n"
            f"Ticker: <code>{_escape_html(ticker)}</code>\n"
            f"Side: {_escape_html(side.upper())} x{contracts}\n"
            f"Reason: {_escape_html(reason)}"
        )
        await self._send(text)
        await self._send_discord(text)

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

        text = (
            f"<b>Window Analysis:</b> {_escape_html(symbol)} {open_time.strftime('%H:%M')}-{close_time.strftime('%H:%M')} UTC\n"
            f"Δ: {price_change_pct:+.4%} {result}\n"
            f"Signals: {signals_in_window} | Trades: {trades_in_window} | P&L: ${paper_pnl:+.4f}\n\n"
        )
        if commentary:
            text += f"<b>AI:</b> {_escape_html(commentary)}"
        else:
            text += "<i>No AI commentary available.</i>"

        await self._send(text)
        await self._send_discord(text)

    async def bot_started(self, mode: str) -> None:
        text = f"<b>Bot Started</b>\nMode: {mode}"
        await self._send(text)
        await self._send_discord(text)

    async def bot_stopped(self) -> None:
        text = "<b>Bot Stopped</b>"
        await self._send(text)
        await self._send_discord(text)

    async def close(self) -> None:
        self._polling = False
        await self._client.aclose()


def make_status_command(risk_manager: Any, executor: Any, client: Any, settings: Any) -> CommandHandler:
    async def handler() -> str:
        if settings.trading_mode == "paper":
            balance: Any = f"{settings.paper_balance:.2f}"
        else:
            try:
                balance = await client.get_balance()
            except Exception:
                balance = "?"
        mode = settings.trading_mode
        env = settings.kalshi_env
        kill = "YES" if Path("KILL_SWITCH").exists() else "no"
        return (
            f"<b>Bot Status</b>\n"
            f"Mode: {_escape_html(mode)} | Env: {_escape_html(env)}\n"
            f"Balance: ${balance}\n"
            f"Daily P&L: ${risk_manager.daily_pnl}\n"
            f"Open positions: {risk_manager.open_position_count}\n"
            f"Pending orders: {len(executor.pending_orders)}\n"
            f"Kill switch: {kill}"
        )

    return handler


def make_pnl_command(risk_manager: Any) -> CommandHandler:
    async def handler() -> str:
        pnl = risk_manager.daily_pnl
        sign = "+" if pnl >= 0 else ""
        return f"<b>Daily P&L</b>\nP&L: {sign}${pnl}\nOpen positions: {risk_manager.open_position_count}"

    return handler


def make_balance_command(client: Any, settings: Any) -> CommandHandler:
    async def handler() -> str:
        if settings.trading_mode == "paper":
            return f"<b>Balance</b>\n${settings.paper_balance:.2f}"
        try:
            balance = await client.get_balance()
            return f"<b>Balance</b>\n${balance}"
        except Exception:
            return "Failed to fetch balance."

    return handler


def make_positions_command(client: Any) -> CommandHandler:
    async def handler() -> str:
        try:
            positions = await client.get_positions()
        except Exception:
            return "Failed to fetch positions."
        if not positions:
            return "<b>Positions</b>\nNo open positions."
        lines = ["<b>Positions</b>"]
        total_exposure = Decimal("0")
        total_fees = Decimal("0")
        for p in positions[:10]:
            ticker = _escape_html(p.get("ticker", "?"))
            qty = _escape_html(p.get("position_fp", "0"))
            cost = _escape_html(p.get("total_traded_dollars", "0"))
            fees = _escape_html(p.get("fees_paid_dollars", "0"))
            total_exposure += Decimal(cost)
            total_fees += Decimal(fees)
            lines.append(f"  x{qty} <code>{ticker}</code>\n    cost=${cost} fees=${fees}")
        lines.append(f"\nTotal: ${total_exposure} + ${total_fees} fees")
        return "\n".join(lines)

    return handler


def make_trades_command(db_path: str = "trades.db") -> CommandHandler:
    async def handler() -> str:
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 10").fetchall()
            conn.close()
        except Exception:
            return "No trade data yet."
        if not rows:
            return "<b>Trades</b>\nNo trades yet."
        lines = ["<b>Recent Trades</b>"]
        for r in rows:
            t = r["timestamp"][11:19] if r["timestamp"] else "?"
            pnl_str = f"${r['pnl']}" if r["pnl"] else "pending"
            side = _escape_html(r["side"].upper())
            ticker = _escape_html(r["ticker"])
            lines.append(f"  {t} {side} x{r['contracts']} <code>{ticker}</code> {pnl_str}")
        return "\n".join(lines)

    return handler


def _get_session_start(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT started_at FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
    return str(row[0]) if row else None


def make_stats_command(db_path: str = "trades.db") -> CommandHandler:
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
            best = conn.execute(
                f"SELECT ticker, pnl FROM trades {where} "  # noqa: S608
                "ORDER BY CAST(pnl AS REAL) DESC LIMIT 1",
                params,
            ).fetchone()
            worst = conn.execute(
                f"SELECT ticker, pnl FROM trades {where} "  # noqa: S608
                "ORDER BY CAST(pnl AS REAL) ASC LIMIT 1",
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
        total_fees = row["total_fees"] or 0.0
        sign = "+" if total_pnl >= 0 else ""

        route_bits: list[str] = []
        for rr in route_rows:
            cnt = rr["cnt"] or 0
            rwins = rr["wins"] or 0
            rwr = int((rwins / cnt) * 100) if cnt else 0
            label = _escape_html(str(rr["route"] or "unknown").replace("_", " ").title())
            route_bits.append(f"{label}: {cnt} ({rwr}% WR)")

        label = "Session Stats" if session_start else "All-Time Stats"
        lines = [
            f"<b>{label}</b>",
            f"Trades: {total} ({wins}W / {losses}L)",
            f"Win rate: {win_rate:.1f}%",
            f"Avg P&amp;L: ${avg_pnl:.2f}",
            f"Net P&amp;L: {sign}${total_pnl:.2f}",
            f"Total fees: ${total_fees:.2f}",
        ]
        if route_bits:
            lines.append("Routes: " + " | ".join(route_bits))
        if best:
            lines.append(f"Best: <code>{_escape_html(best['ticker'])}</code> +${best['pnl']}")
        if worst:
            lines.append(f"Worst: <code>{_escape_html(worst['ticker'])}</code> ${worst['pnl']}")
        return "\n".join(lines)

    return handler


def make_maker_command(db_path: str = "trades.db") -> CommandHandler:
    async def handler() -> str:
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            total_maker = conn.execute("SELECT COUNT(*) as c FROM trades WHERE route = 'maker'").fetchone()["c"]
            promoted = conn.execute(
                "SELECT COUNT(*) as c FROM trades WHERE route = 'taker_promoted'"
            ).fetchone()["c"]
            pure_taker = conn.execute(
                "SELECT COUNT(*) as c FROM trades WHERE route = 'taker'"
            ).fetchone()["c"]
            maker_fills = max(0, int(total_maker) - int(promoted))
            attempts = int(total_maker)
            fill_rate = (maker_fills / attempts * 100) if attempts else 0.0

            avg_fill_s = None
            row = conn.execute(
                """SELECT AVG((julianday(t2.timestamp) - julianday(t1.timestamp)) * 86400.0) as avg_s
                   FROM trades t1
                   JOIN trades t2 ON t2.ticker = t1.ticker AND t2.side = t1.side
                   WHERE t1.route = 'maker' AND t2.route = 'taker_promoted'"""
            ).fetchone()
            if row and row["avg_s"] is not None:
                avg_fill_s = float(row["avg_s"])
            conn.close()
        except Exception:
            return "Maker stats unavailable."

        lines = [
            "<b>Maker Routing</b>",
            f"Maker attempts: {attempts}",
            f"Maker fills (est): {maker_fills}",
            f"Maker timeouts/promoted: {promoted}",
            f"Pure taker entries: {pure_taker}",
            f"Fill rate: {fill_rate:.1f}%",
        ]
        if avg_fill_s is not None:
            lines.append(f"Avg time to promotion: {avg_fill_s:.1f}s")
        return "\n".join(lines)

    return handler


def make_signals_command(db_path: str = "trades.db") -> CommandHandler:
    async def handler() -> str:
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM signals ORDER BY id DESC LIMIT 10").fetchall()
            conn.close()
        except Exception:
            return "No signal data yet."

        if not rows:
            return "<b>Signals</b>\nNo signals evaluated yet."

        lines = ["<b>Recent Signals</b>"]
        for r in rows:
            ts = r["timestamp"][11:19] if r["timestamp"] else "?"
            edge_pct = f"{float(r['net_edge']) * 100:.1f}%"
            action = _escape_html(r["action"])
            ticker = _escape_html(r["ticker"])
            side = _escape_html(r["side"].upper())
            lines.append(
                f"  {ts} [{action}] {side} <code>{ticker[-12:]}</code> edge={edge_pct} {r['seconds_remaining']}s"
            )
        return "\n".join(lines)

    return handler


def make_config_command(settings: Any) -> CommandHandler:
    async def handler() -> str:
        paper_line = (
            f"\nPaper balance: ${settings.paper_balance:.2f}"
            if settings.trading_mode == "paper"
            else ""
        )
        return (
            "<b>Current Config</b>\n"
            f"Mode: {_escape_html(settings.trading_mode)} | Env: {_escape_html(settings.kalshi_env)}\n"
            f"Symbols: {_escape_html(settings.symbols)}\n"
            f"Edge threshold: {settings.edge_threshold:.2f}\n"
            f"Time window: {settings.momentum_min_time}-{settings.momentum_max_time}s\n"
            f"Price range: ${settings.min_trade_price:.2f}-${settings.max_trade_price:.2f}\n"
            f"Maker first: {'ON' if settings.maker_first else 'off'}\n"
            f"Maker fill horizon: {settings.maker_fill_horizon_s}s\n"
            f"Exit stop loss: ${settings.exit_stop_loss:.2f}/contract\n"
            f"Logistic k: {settings.logistic_k}\n"
            f"Daily loss limit: ${settings.daily_loss_limit:.2f}\n"
            f"Max per trade: ${settings.max_per_trade:.2f}"
            f"{paper_line}"
        )

    return handler


def make_set_command(settings: Any) -> ArgCommandHandler:
    from kalshi_bot.alerts.control import SettingError, mutate_setting, settable_keys

    async def handler(args: list[str]) -> str:
        if len(args) < 2:
            return (
                "Usage: /set &lt;key&gt; &lt;value&gt;\nSettable keys: "
                + ", ".join(settable_keys())
            )

        key, raw_value = args[0], args[1]
        try:
            alias, value = mutate_setting(settings, key, raw_value)
        except SettingError as exc:
            return str(exc)
        return f"Set {_escape_html(alias)} = {_escape_html(value)}"

    return handler


def make_cleardata_command(db_path: str = "trades.db") -> CommandHandler:
    async def handler() -> str:
        try:
            conn = sqlite3.connect(db_path)
            trades_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            signals_count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            conn.execute("DELETE FROM trades")
            conn.execute("DELETE FROM signals")
            conn.commit()
            conn.close()
            return f"<b>Data Cleared</b>\nDeleted {trades_count} trades, {signals_count} signals."
        except Exception as exc:
            return f"Failed to clear data: {exc}"

    return handler


def make_newsession_command(db_path: str = "trades.db") -> CommandHandler:
    async def handler() -> str:
        try:
            conn = sqlite3.connect(db_path)
            prev_start = conn.execute("SELECT started_at FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
            if prev_start:
                ts = prev_start[0]
                row = conn.execute(
                    "SELECT COUNT(*) as trades, COALESCE(SUM(CAST(pnl AS REAL)), 0) as pnl FROM trades WHERE timestamp >= ?",
                    (ts,),
                ).fetchone()
                prev_trades, prev_pnl = row[0], row[1]
            else:
                row = conn.execute("SELECT COUNT(*) as trades, COALESCE(SUM(CAST(pnl AS REAL)), 0) as pnl FROM trades").fetchone()
                prev_trades, prev_pnl = row[0], row[1]

            now = datetime.now(timezone.utc).isoformat()
            cur = conn.execute("INSERT INTO sessions (started_at, label) VALUES (?, ?)", (now, None))
            session_id = cur.lastrowid
            conn.commit()
            conn.close()
            return (
                "<b>New Session Started</b>\n"
                f"Session #{session_id}\n"
                f"Previous session: {prev_trades} trades, ${prev_pnl:+.2f} PnL\n"
                "Stats and PnL now track from this point.\nOld data preserved for backtesting."
            )
        except Exception as exc:
            return f"Failed to start session: {exc}"

    return handler


def make_ip_command() -> CommandHandler:
    async def handler() -> str:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get("https://api.ipify.org")
                ip = resp.text.strip()
            return f"<b>Server Info</b>\nIP: <code>{ip}</code>\nDashboard: http://{ip}"
        except Exception:
            return "Failed to fetch public IP."

    return handler


def make_kill_command() -> CommandHandler:
    async def handler() -> str:
        Path("KILL_SWITCH").touch()
        return "<b>Kill switch ACTIVATED</b>\nTrading halted. Send /resume to restart."

    return handler


def make_resume_command() -> CommandHandler:
    async def handler() -> str:
        ks = Path("KILL_SWITCH")
        if ks.exists():
            ks.unlink()
            return "<b>Kill switch removed</b>\nTrading resumed."
        return "Kill switch was not active."

    return handler


def make_reset_command(risk: Any) -> ArgCommandHandler:
    """Clear in-memory risk state (locked sides + cooldowns).

    Usage: /reset → clear locks only; /reset pnl → also reset daily P&L.
    """

    async def handler(args: list[str]) -> str:
        clear_pnl = any(a.lower() in ("pnl", "full", "all") for a in args)
        result = risk.reset_session(clear_pnl=clear_pnl)
        parts = [
            f"Cleared {result['cleared_locked_sides']} locked sides",
            f"{result['cleared_cooldowns']} cooldowns",
        ]
        if clear_pnl:
            parts.append("reset daily P&amp;L")
        parts.append(f"open positions: {result['open_positions']}")
        return "<b>Risk state reset</b>\n" + ", ".join(parts)

    return handler


def make_window_command(tracker: Any) -> CommandHandler:
    async def handler() -> str:
        symbols = ["BTC", "ETH", "SOL"]
        lines = ["<b>Active Windows</b>\n"]
        found = False
        for sym in symbols:
            win = tracker.get_window(sym)
            if win is None:
                continue
            found = True
            pct = win.price_change_pct
            secs = win.seconds_remaining
            mins = secs // 60
            remaining = f"{mins}m{secs % 60:02d}s"
            lines.append(
                f"<b>{sym}</b> {win.open_time.strftime('%H:%M')}–{win.close_time.strftime('%H:%M')} UTC\n"
                f"  Open: ${win.open_price:,.2f}\n"
                f"  Now:  ${win.current_price:,.2f} ({pct:+.4%})\n"
                f"  Remaining: {remaining}\n"
            )
        if not found:
            lines.append("No active windows.")
        return "\n".join(lines)

    return handler


def make_analysis_command(db_path: str = "trades.db") -> CommandHandler:
    async def handler() -> str:
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM window_analyses ORDER BY id DESC LIMIT 5").fetchall()
            conn.close()
        except sqlite3.OperationalError:
            return "No analyses logged yet."
        except Exception as exc:
            return f"DB Error: {exc}"

        if not rows:
            return "No analyses found."

        lines = ["<b>Last 5 Window Analyses</b>\n"]
        for r in rows:
            symbol = _escape_html(r["symbol"])
            open_time = r["window_open"][11:16]
            close_time = r["window_close"][11:16]
            change = r["price_change_pct"]
            res = r["result"].upper()
            ai = _escape_html(r["ai_commentary"]) if r["ai_commentary"] else "<i>None</i>"
            lines.append(f"<b>{symbol} {open_time}-{close_time}</b>: Δ {change:+.2%} {res}")
            lines.append(f"AI: {ai}\n")
        return "\n".join(lines)

    return handler


def make_data_command(db_path: str = "trades.db") -> CommandHandler:
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
            lines = ["<b>Data Collection</b>\n"]
            for table, label in tables.items():
                try:
                    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
                except sqlite3.OperationalError:
                    count = 0
                lines.append(f"  {label}: {count:,}")
            conn.close()
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    return handler


def make_symbols_command(tracker: Any, settings: Any) -> CommandHandler:
    async def handler() -> str:
        active = {s.strip() for s in settings.symbols.split(",")}
        all_symbols = ["BTC", "ETH", "SOL"]
        lines = ["<b>Symbols</b>\n"]
        for sym in all_symbols:
            enabled = "ON" if sym in active else "OFF"
            win = tracker.get_window(sym)
            if win is not None:
                status = f"${win.current_price:,.2f} ({win.price_change_pct:+.4%}) {win.seconds_remaining}s left"
            else:
                status = "no window"
            lines.append(f"  <b>{_escape_html(sym)}</b> [{enabled}] {status}")
        return "\n".join(lines)

    return handler


def make_calendar_command(db_path: str = "trades.db") -> ArgCommandHandler:
    """Render a text-based P&L calendar for Telegram.

    Usage: /calendar        → current month (or most recent with data)
           /calendar 2026 5 → May 2026
    """

    async def handler(args: list[str]) -> str:
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT date(timestamp) as day,
                          COALESCE(SUM(CAST(pnl AS REAL)), 0) as pnl,
                          COUNT(*) as trades,
                          SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins,
                          SUM(CASE WHEN CAST(pnl AS REAL) <= 0 THEN 1 ELSE 0 END) as losses
                   FROM trades
                   WHERE pnl IS NOT NULL
                   GROUP BY day
                   ORDER BY day""",
            ).fetchall()
            conn.close()
        except Exception:
            return "No trade data yet."

        by_day: dict[str, dict[str, Any]] = {}
        for r in rows:
            by_day[str(r["day"])] = {
                "pnl": float(r["pnl"]),
                "trades": int(r["trades"]),
                "wins": int(r["wins"]),
                "losses": int(r["losses"]),
            }

        # Determine which month to show
        year: int | None = None
        month: int | None = None
        if len(args) >= 2:
            try:
                year = int(args[0])
                month = int(args[1])
            except ValueError:
                return "Usage: /calendar [YYYY MM]"
        elif len(args) == 1:
            # Try month name or number for current year
            try:
                month = int(args[0])
                year = date.today().year
            except ValueError:
                return "Usage: /calendar [YYYY MM]"

        if year is None or month is None:
            if by_day:
                last_key = sorted(by_day.keys())[-1]
                d = date.fromisoformat(last_key)
                year, month = d.year, d.month
            else:
                today = date.today()
                year, month = today.year, today.month

        if month < 1 or month > 12:
            return "Invalid month. Use 1-12."

        month_name = _calendar.month_name[month]
        days_in_month = _calendar.monthrange(year, month)[1]
        # Python: Monday=0; shift to Sunday=0
        first_dow = (_calendar.monthrange(year, month)[0] + 1) % 7

        # Gather monthly stats
        month_pnl = 0.0
        month_trades = 0
        month_wins = 0
        green_days = 0
        red_days = 0
        flat_days = 0
        trading_days = 0

        day_data: list[dict[str, Any] | None] = []
        for d in range(1, days_in_month + 1):
            key = f"{year}-{month:02d}-{d:02d}"
            info = by_day.get(key)
            day_data.append(info)
            if info:
                trading_days += 1
                month_pnl += info["pnl"]
                month_trades += info["trades"]
                month_wins += info["wins"]
                if info["pnl"] > 0:
                    green_days += 1
                elif info["pnl"] < 0:
                    red_days += 1
                else:
                    flat_days += 1

        # Build the text calendar
        lines: list[str] = []
        lines.append(f"<b>📅 {month_name} {year}</b>")
        lines.append("")

        # Monospace grid header
        lines.append("<code>Sun Mon Tue Wed Thu Fri Sat</code>")

        # Build week rows
        cells: list[str] = []
        # Leading blanks
        for _ in range(first_dow):
            cells.append("   ")

        for d in range(1, days_in_month + 1):
            info = day_data[d - 1]
            if info is None:
                # No trades — just the day number, dimmed
                cells.append(f"{d:>3}")
            else:
                if info["pnl"] > 0:
                    marker = "🟩"
                elif info["pnl"] < 0:
                    marker = "🟥"
                else:
                    marker = "⬜"
                cells.append(f"{marker}{d:<2}")

        # Pad to complete the last week
        while len(cells) % 7 != 0:
            cells.append("   ")

        # Format into rows of 7
        for i in range(0, len(cells), 7):
            week = cells[i : i + 7]
            lines.append("<code>" + " ".join(week) + "</code>")

        lines.append("")

        # Detail lines for days with trades
        lines.append("<b>Daily Breakdown</b>")
        for d in range(1, days_in_month + 1):
            info = day_data[d - 1]
            if info is None:
                continue
            sign = "+" if info["pnl"] >= 0 else ""
            icon = "🟢" if info["pnl"] > 0 else "🔴" if info["pnl"] < 0 else "⚪"
            lines.append(
                f"  {icon} {d:>2} — {sign}${info['pnl']:.2f}"
                f"  ({info['trades']}t {info['wins']}w/{info['losses']}l)"
            )

        # Monthly summary
        lines.append("")
        sign = "+" if month_pnl >= 0 else ""
        wr = (month_wins / month_trades * 100) if month_trades > 0 else 0
        avg_day = month_pnl / trading_days if trading_days > 0 else 0
        avg_sign = "+" if avg_day >= 0 else ""
        day_wr = (green_days / (green_days + red_days) * 100) if (green_days + red_days) > 0 else 0

        lines.append(f"<b>Month Total: {sign}${month_pnl:.2f}</b>")
        lines.append(
            f"Days: {green_days}🟢 {red_days}🔴 {flat_days}⚪"
            f"  ({day_wr:.0f}% win days)"
        )
        lines.append(f"Trades: {month_trades}  WR: {wr:.0f}%")
        lines.append(f"Avg/day: {avg_sign}${avg_day:.2f}")

        return "\n".join(lines)

    return handler
