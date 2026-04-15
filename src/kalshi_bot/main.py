"""Main async loop — Coinbase feed -> window tracker -> strategies -> risk -> execute."""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal as unix_signal
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog

from kalshi_bot.alerts import MultiAlerter
from kalshi_bot.alerts.discord import DiscordWebhookAlerter
from kalshi_bot.alerts.discord_bot import (
    DiscordBotAlerter,
    make_analysis_command as make_discord_analysis_command,
    make_balance_command as make_discord_balance_command,
    make_config_command as make_discord_config_command,
    make_data_command as make_discord_data_command,
    make_ip_command as make_discord_ip_command,
    make_kill_command as make_discord_kill_command,
    make_maker_command as make_discord_maker_command,
    make_newsession_command as make_discord_newsession_command,
    make_pnl_command as make_discord_pnl_command,
    make_positions_command as make_discord_positions_command,
    make_resume_command as make_discord_resume_command,
    make_set_command as make_discord_set_command,
    make_signals_command as make_discord_signals_command,
    make_stats_command as make_discord_stats_command,
    make_status_command as make_discord_status_command,
    make_symbols_command as make_discord_symbols_command,
    make_trades_command as make_discord_trades_command,
    make_window_command as make_discord_window_command,
)
from kalshi_bot.alerts.telegram import (
    TelegramAlerter,
    make_analysis_command,
    make_balance_command,
    make_cleardata_command,
    make_config_command,
    make_data_command,
    make_ip_command,
    make_kill_command,
    make_newsession_command,
    make_pnl_command,
    make_positions_command,
    make_resume_command,
    make_set_command,
    make_signals_command,
    make_stats_command,
    make_maker_command,
    make_status_command,
    make_symbols_command,
    make_trades_command,
    make_window_command,
)
from kalshi_bot.analysis.window_analyzer import analyze_window
from kalshi_bot.client.coinbase import CoinbaseFeed
from kalshi_bot.client.kalshi import KalshiClient
from kalshi_bot.client.kalshi_ws import KalshiOrderbookFeed
from kalshi_bot.client.openrouter import OpenRouterClient
from kalshi_bot.config import Settings
from kalshi_bot.data.recorder import DataRecorder
from kalshi_bot.data.window_tracker import WindowState, WindowTracker
from kalshi_bot.execution.executor import Executor
from kalshi_bot.logging_config import setup_logging
from kalshi_bot.models.price import PriceTick
from kalshi_bot.models.market import Market
from kalshi_bot.risk.manager import RiskManager, RiskVetoError
from kalshi_bot.strategy.momentum import evaluate_momentum
from kalshi_bot.strategy.probability import estimate_k_from_vol, estimate_up_probability

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

AlerterLike = (
    TelegramAlerter | DiscordWebhookAlerter | DiscordBotAlerter | MultiAlerter | None
)

SERIES_MAP: dict[str, str] = {
    "BTC": "KXBTC15M",
    "ETH": "KXETH15M",
    "SOL": "KXSOL15M",
}

ORDERBOOK_STALENESS_S = 15.0
MIN_EVAL_INTERVAL_S = 0.2
HOUSEKEEPING_INTERVAL_S = 5.0


def _bump_counter(counters: dict[str, int], key: str) -> None:
    """Increment named in-memory signal counter."""
    counters[key] = counters.get(key, 0) + 1


class CachedState:
    """TTL-based cache for slow-changing REST state."""

    def __init__(self) -> None:
        self.balance: Decimal = Decimal("0")
        self._balance_refreshed_mono = 0.0
        self._positions_refreshed_mono = 0.0
        self._markets: dict[str, Market] = {}
        self._markets_refreshed_mono = 0.0
        self.active_symbols: set[str] = set()

    async def refresh_balance(self, client: KalshiClient, ttl_s: float = 10.0) -> None:
        """Refresh cached balance if TTL expired."""
        now = time.monotonic()
        if now - self._balance_refreshed_mono < ttl_s:
            return
        with contextlib.suppress(Exception):
            self.balance = await client.get_balance()
            self._balance_refreshed_mono = now

    async def refresh_positions(
        self,
        client: KalshiClient,
        risk: RiskManager,
        ttl_s: float = 10.0,
    ) -> None:
        """Refresh open positions and sync risk manager."""
        now = time.monotonic()
        if now - self._positions_refreshed_mono < ttl_s:
            return
        with contextlib.suppress(Exception):
            positions = await client.get_positions()
            risk.sync_positions(positions)
            self._positions_refreshed_mono = now

    async def refresh_markets(
        self,
        client: KalshiClient,
        tracker: WindowTracker,
        settings: Settings,
        ttl_s: float = 30.0,
    ) -> None:
        """Refresh active market tickers and window mapping per symbol."""
        self.active_symbols = {
            s.strip() for s in settings.symbols.split(",") if s.strip()
        }
        now_mono = time.monotonic()
        now_utc = datetime.now(timezone.utc)
        if now_mono - self._markets_refreshed_mono < ttl_s and self._markets:
            if all(m.close_time > now_utc for m in self._markets.values()):
                return

        next_markets: dict[str, Market] = {}

        for symbol in self.active_symbols:
            series = SERIES_MAP.get(symbol)
            if series is None:
                continue
            with contextlib.suppress(Exception):
                markets = await client.get_open_markets(series)
                active = [m for m in markets if m.close_time > now_utc]
                if not active:
                    continue
                market = min(active, key=lambda m: m.close_time)
                tracker.set_window(
                    symbol,
                    ticker=market.ticker,
                    open_time=market.open_time,
                    close_time=market.close_time,
                )
                next_markets[symbol] = market

        self._markets = next_markets
        self._markets_refreshed_mono = now_mono

    def get_market(self, symbol: str) -> Market | None:
        """Return cached active market for a symbol, if present."""
        return self._markets.get(symbol)


def _write_live_state(live_state: dict[str, Any]) -> None:
    """Atomically write live state JSON for dashboard readers."""
    path = Path("live_state.json")
    tmp = Path("live_state.json.tmp")
    tmp.write_text(json.dumps(live_state, default=str), encoding="utf-8")
    tmp.replace(path)


def _make_alerter(
    settings: Settings,
) -> MultiAlerter:
    alerters: list[TelegramAlerter | DiscordWebhookAlerter | DiscordBotAlerter] = []
    if (
        settings.discord_enabled
        and settings.discord_bot_token
        and settings.discord_channel_id
    ):
        alerters.append(
            DiscordBotAlerter(settings.discord_bot_token, settings.discord_channel_id)
        )
    if (
        settings.telegram_enabled
        and settings.telegram_bot_token
        and settings.telegram_chat_id
    ):
        alerters.append(
            TelegramAlerter(
                settings.telegram_bot_token,
                settings.telegram_chat_id,
                discord_webhook_url="",
            )
        )
    if (
        settings.discord_enabled
        and settings.discord_webhook_url
        and not any(isinstance(a, DiscordBotAlerter) for a in alerters)
    ):
        alerters.append(DiscordWebhookAlerter(settings.discord_webhook_url))
    return MultiAlerter(alerters)


def _register_commands(
    alerter: TelegramAlerter,
    risk: RiskManager,
    executor: Executor,
    client: KalshiClient,
    settings: Settings,
    tracker: WindowTracker,
) -> None:
    alerter.register("status", make_status_command(risk, executor, client, settings))
    alerter.register("pnl", make_pnl_command(risk))
    alerter.register("balance", make_balance_command(client))
    alerter.register("positions", make_positions_command(client))
    alerter.register("trades", make_trades_command())
    alerter.register("kill", make_kill_command())
    alerter.register("resume", make_resume_command())
    alerter.register("stats", make_stats_command())
    alerter.register("maker", make_maker_command())
    alerter.register("signals", make_signals_command())
    alerter.register("analysis", make_analysis_command())
    alerter.register("window", make_window_command(tracker))
    alerter.register("config", make_config_command(settings))
    alerter.register("data", make_data_command())
    alerter.register("symbols", make_symbols_command(tracker, settings))
    alerter.register("newsession", make_newsession_command())
    alerter.register("cleardata", make_cleardata_command())
    alerter.register("ip", make_ip_command())
    alerter.register_with_args("set", make_set_command(settings))


def _register_discord_commands(
    alerter: DiscordBotAlerter,
    risk: RiskManager,
    executor: Executor,
    client: KalshiClient,
    settings: Settings,
    tracker: WindowTracker,
) -> None:
    alerter.register(
        "status", make_discord_status_command(risk, executor, client, settings)
    )
    alerter.register("pnl", make_discord_pnl_command(risk))
    alerter.register("balance", make_discord_balance_command(client))
    alerter.register("positions", make_discord_positions_command(client))
    alerter.register("trades", make_discord_trades_command())
    alerter.register("kill", make_discord_kill_command())
    alerter.register("resume", make_discord_resume_command())
    alerter.register("stats", make_discord_stats_command())
    alerter.register("maker", make_discord_maker_command())
    alerter.register("signals", make_discord_signals_command())
    alerter.register("analysis", make_discord_analysis_command())
    alerter.register("window", make_discord_window_command(tracker))
    alerter.register("config", make_discord_config_command(settings))
    alerter.register("data", make_discord_data_command())
    alerter.register("symbols", make_discord_symbols_command(tracker, settings))
    alerter.register("ip", make_discord_ip_command())
    alerter.register("newsession", make_discord_newsession_command())
    alerter.register_with_args("set", make_discord_set_command(settings))
    alerter.register_default_slash_commands()


async def _emit_feed_health_alerts(
    *,
    alerter: AlerterLike,
    coinbase_age: float | None,
    kalshi_age: float | None,
    threshold_s: float = 30.0,
    sent_state: dict[str, bool],
) -> None:
    if alerter is None:
        return

    async def _send_once(key: str, message: str, is_stale: bool) -> None:
        already_sent = sent_state.get(key, False)
        if is_stale and not already_sent:
            sent_state[key] = True
            # Reuse generic alert hook via exit-style text for consistency
            await alerter.trade_exited("SYSTEM", key, 0, message)
        if (not is_stale) and already_sent:
            sent_state[key] = False

    if coinbase_age is not None:
        await _send_once(
            "coinbase_feed",
            f"Coinbase feed stale: {coinbase_age:.1f}s since last tick",
            coinbase_age > threshold_s,
        )
    if kalshi_age is not None:
        await _send_once(
            "kalshi_ws",
            f"Kalshi WS stale: {kalshi_age:.1f}s since last update",
            kalshi_age > threshold_s,
        )


async def run_bot(settings: Settings) -> None:
    dry_run = settings.trading_mode == "paper"
    if dry_run:
        logger.info("paper_trading_mode")

    client = KalshiClient(settings)
    risk = RiskManager(settings)
    executor = Executor(client, risk, dry_run=dry_run)
    tracker = WindowTracker()
    recorder = DataRecorder()
    cached = CachedState()
    signal_counters: dict[str, int] = {}
    signal_counter_window_start: list[datetime] = [datetime.now(timezone.utc)]
    alerter = _make_alerter(settings)

    logger.info(
        "alerters_initialized",
        count=len(alerter.alerters),
        modes=[type(a).__name__ for a in alerter.alerters],
    )

    openrouter: OpenRouterClient | None = None
    if settings.openrouter_api_key:
        openrouter = OpenRouterClient(
            settings.openrouter_api_key, settings.openrouter_model
        )

    for a in alerter.alerters:
        if isinstance(a, TelegramAlerter):
            _register_commands(a, risk, executor, client, settings, tracker)
        elif isinstance(a, DiscordBotAlerter):
            _register_discord_commands(a, risk, executor, client, settings, tracker)

    price_queue: asyncio.Queue[PriceTick] = asyncio.Queue(maxsize=500)
    eval_trigger = asyncio.Event()
    feed = CoinbaseFeed(price_queue, eval_trigger=eval_trigger)
    ws_feed = KalshiOrderbookFeed(settings, eval_trigger=eval_trigger)

    await cached.refresh_balance(client, ttl_s=0.0)
    await cached.refresh_positions(client, risk, ttl_s=0.0)
    await cached.refresh_markets(client, tracker, settings, ttl_s=0.0)
    initial_tickers = {
        market.ticker
        for symbol in cached.active_symbols
        if (market := cached.get_market(symbol)) is not None
    }
    await ws_feed.set_tickers(initial_tickers)

    shutdown_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("shutdown_signal_received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (unix_signal.SIGINT, unix_signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    if alerter:
        await alerter.bot_started("paper" if dry_run else "live")

    feed_task = asyncio.create_task(feed.start())
    ws_feed_task = asyncio.create_task(ws_feed.start())

    alerter_tasks: list[asyncio.Task[None]] = []
    for a in alerter.alerters:
        if isinstance(a, TelegramAlerter):
            alerter_tasks.append(asyncio.create_task(a.poll_commands()))
        elif isinstance(a, DiscordBotAlerter):
            alerter_tasks.append(asyncio.create_task(a.start()))

    try:
        fast_eval_task = asyncio.create_task(
            _fast_eval_loop(
                tracker,
                risk,
                executor,
                settings,
                shutdown_event,
                eval_trigger,
                alerter,
                cached,
                ws_feed,
                signal_counters,
                signal_counter_window_start,
            )
        )
        housekeeping_task = asyncio.create_task(
            _slow_housekeeping_loop(
                client,
                tracker,
                risk,
                executor,
                settings,
                shutdown_event,
                alerter,
                openrouter,
                cached,
                recorder,
                feed=feed,
                ws_feed=ws_feed,
                signal_counters=signal_counters,
                signal_counter_window_start=signal_counter_window_start,
            )
        )
        drain_task = asyncio.create_task(
            _drain_prices(
                price_queue,
                tracker,
                shutdown_event,
                recorder,
                eval_trigger=eval_trigger,
            )
        )

        await shutdown_event.wait()
    finally:
        logger.info("shutting_down")
        await feed.stop()
        await ws_feed.stop()
        feed_task.cancel()
        ws_feed_task.cancel()
        shutdown_event.set()
        tasks = [feed_task, ws_feed_task, fast_eval_task, housekeeping_task, drain_task]
        for t in alerter_tasks:
            t.cancel()
            tasks.append(t)
        await asyncio.gather(*tasks, return_exceptions=True)
        if openrouter:
            await openrouter.close()
        recorder.close()
        await executor.close()
        await client.close()
        if alerter:
            await alerter.bot_stopped()
            await alerter.close()
        logger.info("shutdown_complete")


async def _drain_prices(
    queue: asyncio.Queue[PriceTick],
    tracker: WindowTracker,
    shutdown: asyncio.Event,
    recorder: DataRecorder | None = None,
    eval_trigger: asyncio.Event | None = None,
) -> None:
    while not shutdown.is_set():
        try:
            tick = await asyncio.wait_for(queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        tracker.update_price(tick)
        if eval_trigger is not None:
            eval_trigger.set()
        if recorder is not None:
            recorder.record_price_tick(tick.symbol, tick.price, tick.timestamp)


async def _fast_eval_loop(
    tracker: WindowTracker,
    risk: RiskManager,
    executor: Executor,
    settings: Settings,
    shutdown: asyncio.Event,
    eval_trigger: asyncio.Event,
    alerter: AlerterLike,
    cached: CachedState,
    ws_feed: KalshiOrderbookFeed,
    signal_counters: dict[str, int],
    signal_counter_window_start: list[datetime],
) -> None:
    """Event-driven strategy evaluation loop (no REST reads on hot path)."""
    last_eval_mono: dict[str, float] = {}
    last_risk_block: dict[str, str] = {}

    while not shutdown.is_set():
        now_utc = datetime.now(timezone.utc)
        if (now_utc - signal_counter_window_start[0]).total_seconds() >= 3600:
            signal_counters.clear()
            signal_counter_window_start[0] = now_utc

        try:
            await asyncio.wait_for(eval_trigger.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        eval_trigger.clear()

        symbols = cached.active_symbols or {
            s.strip() for s in settings.symbols.split(",") if s.strip()
        }
        now = time.monotonic()

        for symbol in symbols:
            last = last_eval_mono.get(symbol, 0.0)
            if now - last < MIN_EVAL_INTERVAL_S:
                _bump_counter(signal_counters, "throttled")
                continue

            window = tracker.get_window(symbol)
            if window is None:
                _bump_counter(signal_counters, "skip_no_window")
                continue

            market = cached.get_market(symbol)
            if market is None:
                _bump_counter(signal_counters, "skip_no_market")
                continue
            ticker = market.ticker

            ob_cached = ws_feed.get_orderbook(ticker)
            if ob_cached is None:
                _bump_counter(signal_counters, "skip_no_orderbook")
                continue
            orderbook, ob_ts = ob_cached
            age = (datetime.now(timezone.utc) - ob_ts).total_seconds()
            if age > ORDERBOOK_STALENESS_S:
                _bump_counter(signal_counters, "skip_stale_orderbook")
                continue

            signal = evaluate_momentum(
                window,
                ticker,
                orderbook,
                edge_threshold=settings.edge_threshold,
                k=settings.logistic_k,
                min_time=settings.momentum_min_time,
                max_time=settings.momentum_max_time,
                min_price=settings.min_trade_price,
                max_price=settings.max_trade_price,
                maker_first=settings.maker_first,
            )
            last_eval_mono[symbol] = now

            if signal is None:
                _bump_counter(signal_counters, "no_signal")
                continue

            try:
                risk.check(signal)
            except RiskVetoError as exc:
                executor.log_signal(signal, "skip_risk", str(exc))
                _bump_counter(signal_counters, "skip_risk")
                reason_str = str(exc)
                if last_risk_block.get(ticker) != reason_str:
                    logger.info("risk_blocked", ticker=ticker, reason=reason_str)
                    last_risk_block[ticker] = reason_str
                continue

            result = await executor.submit(signal, cached.balance)
            if result is not None:
                _bump_counter(signal_counters, "trade")
                last_risk_block.pop(ticker, None)
                executor.log_signal(signal, "trade", f"order_id={result.order_id}")
                if alerter is not None:
                    await alerter.trade_placed(
                        signal, result.contracts, result.order_id
                    )
            elif executor._dry_run:
                _bump_counter(signal_counters, "paper_trade")
                executor.log_signal(signal, "paper_trade", "")
            else:
                _bump_counter(signal_counters, "skip_sizing")
                executor.log_signal(
                    signal, "skip_sizing", "sizing returned 0 contracts"
                )


async def _slow_housekeeping_loop(
    client: KalshiClient,
    tracker: WindowTracker,
    risk: RiskManager,
    executor: Executor,
    settings: Settings,
    shutdown: asyncio.Event,
    alerter: AlerterLike,
    openrouter: OpenRouterClient | None,
    cached: CachedState,
    recorder: DataRecorder | None = None,
    feed: CoinbaseFeed | None = None,
    ws_feed: KalshiOrderbookFeed | None = None,
    signal_counters: dict[str, int] | None = None,
    signal_counter_window_start: list[datetime] | None = None,
) -> None:
    """Periodic housekeeping loop (REST I/O, settlements, exits, live state)."""
    last_analysis_time: dict[str, datetime] = {}
    stale_alert_state: dict[str, bool] = {}

    while not shutdown.is_set():
        try:
            await executor.check_pending_fills()
            await executor.promote_to_taker()
            await executor.cancel_stale()

            if executor._dry_run:
                await _settle_paper_positions(executor, tracker, alerter)
            await _check_settlements(client, executor, alerter)

            await cached.refresh_balance(client)
            await cached.refresh_positions(client, risk)
            await cached.refresh_markets(client, tracker, settings)

            active_tickers = {
                market.ticker
                for symbol in cached.active_symbols
                if (market := cached.get_market(symbol)) is not None
            }
            if ws_feed is not None:
                await ws_feed.set_tickers(active_tickers)

            live_state: dict[str, Any] = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "daily_pnl": float(risk.daily_pnl),
                "open_positions": risk.open_position_count,
                "symbols": {},
                "health": {},
            }

            util = client.api_utilization()
            coinbase_age = feed.last_tick_age_s if feed is not None else None
            kalshi_age = ws_feed.last_update_age_s if ws_feed is not None else None
            db_age = recorder.last_write_age_s if recorder is not None else None
            db_latency = (
                recorder.last_write_latency_ms if recorder is not None else None
            )

            live_state["health"] = {
                "coinbase_last_tick_age_s": coinbase_age,
                "kalshi_ws_last_update_age_s": kalshi_age,
                "coinbase_stale": bool(
                    coinbase_age is not None and coinbase_age > 30.0
                ),
                "kalshi_ws_stale": bool(kalshi_age is not None and kalshi_age > 30.0),
                "db_last_write_age_s": db_age,
                "db_last_write_latency_ms": db_latency,
                "api_read_per_sec": util["read_per_sec"],
                "api_write_per_sec": util["write_per_sec"],
                "api_read_utilization": util["read_utilization"],
                "api_write_utilization": util["write_utilization"],
            }

            if signal_counters is not None and signal_counter_window_start is not None:
                top = sorted(
                    signal_counters.items(),
                    key=lambda x: x[1],
                    reverse=True,
                )[:8]
                live_state["health"]["signal_counters_window_start"] = (
                    signal_counter_window_start[0].isoformat()
                )
                live_state["health"]["signal_counters_hour"] = {
                    key: value for key, value in top
                }

            await _emit_feed_health_alerts(
                alerter=alerter,
                coinbase_age=coinbase_age,
                kalshi_age=kalshi_age,
                sent_state=stale_alert_state,
            )

            for symbol in cached.active_symbols:
                market = cached.get_market(symbol)
                if market is None:
                    continue
                ticker = market.ticker

                window = tracker.get_window(symbol)
                if window is None:
                    continue

                orderbook = None
                if ws_feed is not None:
                    ob_cached = ws_feed.get_orderbook(ticker)
                    if ob_cached is not None:
                        ob_snapshot, ob_ts = ob_cached
                        age = (datetime.now(timezone.utc) - ob_ts).total_seconds()
                        if age <= ORDERBOOK_STALENESS_S:
                            orderbook = ob_snapshot

                if orderbook is None:
                    with contextlib.suppress(Exception):
                        orderbook = await client.get_orderbook(ticker)
                if orderbook is None:
                    continue

                kalshi_yes_price = orderbook.best_yes_ask
                if kalshi_yes_price is None:
                    continue

                yes_depth = sum(lv.quantity for lv in orderbook.yes_levels)
                no_depth = sum(lv.quantity for lv in orderbook.no_levels)
                best_bid = orderbook.best_yes_bid
                best_no_bid = orderbook.best_no_bid

                live_state["symbols"][symbol] = {
                    "ticker": ticker,
                    "open_price": window.open_price,
                    "current_price": window.current_price,
                    "price_change_pct": window.price_change_pct,
                    "seconds_remaining": window.seconds_remaining,
                    "kalshi_yes_ask": float(kalshi_yes_price),
                    "kalshi_yes_bid": float(best_bid) if best_bid is not None else None,
                    "position_contracts": 0,
                    "position_notional": 0.0,
                    "entry_price": None,
                    "unrealized_pnl": 0.0,
                    "fee_per_contract": None,
                }

                active_orders = [
                    o for o in executor.filled_orders if o.signal.ticker == ticker
                ]
                if active_orders:
                    total_contracts = sum(o.contracts for o in active_orders)
                    if total_contracts > 0:
                        total_notional = sum(
                            float(o.price) * o.contracts for o in active_orders
                        )
                        avg_entry = total_notional / total_contracts
                        mark_price = float(kalshi_yes_price)
                        unrealized = 0.0
                        for o in active_orders:
                            if o.signal.side.value == "yes":
                                unrealized += (
                                    mark_price - float(o.price)
                                ) * o.contracts
                            else:
                                unrealized += (
                                    (1.0 - mark_price) - float(o.price)
                                ) * o.contracts

                        live_state["symbols"][symbol]["position_contracts"] = (
                            total_contracts
                        )
                        live_state["symbols"][symbol]["position_notional"] = (
                            total_notional
                        )
                        live_state["symbols"][symbol]["entry_price"] = avg_entry
                        live_state["symbols"][symbol]["unrealized_pnl"] = unrealized
                        live_state["symbols"][symbol]["fee_per_contract"] = (
                            active_orders[0].fee_per_contract
                        )

                recent = tracker.get_recent_changes(symbol)
                dynamic_k = (
                    estimate_k_from_vol(recent)
                    if len(recent) >= 5
                    else settings.logistic_k
                )
                snap_prob_live = estimate_up_probability(
                    window.price_change_pct,
                    window.seconds_remaining,
                    k=dynamic_k,
                )
                live_state["symbols"][symbol]["model_prob"] = snap_prob_live
                live_state["symbols"][symbol]["dynamic_k"] = dynamic_k

                await _evaluate_exits(
                    executor,
                    ticker,
                    kalshi_yes_price,
                    settings,
                    alerter,
                    window,
                    k=dynamic_k,
                )

                signal_for_record = evaluate_momentum(
                    window,
                    ticker,
                    orderbook,
                    edge_threshold=settings.edge_threshold,
                    k=settings.logistic_k,
                    min_time=settings.momentum_min_time,
                    max_time=settings.momentum_max_time,
                    min_price=settings.min_trade_price,
                    max_price=settings.max_trade_price,
                    maker_first=settings.maker_first,
                )

                if recorder is not None:
                    spread = (
                        kalshi_yes_price - best_bid if best_bid is not None else None
                    )
                    recorder.record_orderbook_snapshot(
                        ticker=ticker,
                        symbol=symbol,
                        best_yes_ask=kalshi_yes_price,
                        best_yes_bid=best_bid,
                        best_no_ask=orderbook.best_no_ask,
                        best_no_bid=best_no_bid,
                        yes_depth=yes_depth,
                        no_depth=no_depth,
                        spread=spread,
                    )
                    recorder.record_window_snapshot(
                        ticker=ticker,
                        symbol=symbol,
                        seconds_remaining=window.seconds_remaining,
                        open_price=window.open_price,
                        current_price=window.current_price,
                        price_change_pct=window.price_change_pct,
                        kalshi_yes_ask=float(kalshi_yes_price),
                        kalshi_yes_bid=float(best_bid)
                        if best_bid is not None
                        else None,
                        kalshi_no_bid=float(best_no_bid)
                        if best_no_bid is not None
                        else None,
                        real_prob=snap_prob_live,
                        dynamic_k=dynamic_k,
                        yes_depth=yes_depth,
                        no_depth=no_depth,
                        momentum_60s=window.momentum_60s,
                    )

                    yes_edge = snap_prob_live - float(kalshi_yes_price)
                    if signal_for_record is not None:
                        recorder.record_strategy_eval(
                            ticker=ticker,
                            symbol=symbol,
                            strategy=signal_for_record.strategy.value,
                            seconds_remaining=window.seconds_remaining,
                            price_change_pct=window.price_change_pct,
                            kalshi_yes_price=float(kalshi_yes_price),
                            real_prob=snap_prob_live,
                            edge=float(signal_for_record.edge),
                            net_edge=float(signal_for_record.net_edge),
                            signal_side=signal_for_record.side.value,
                            action="signal",
                        )
                    else:
                        recorder.record_strategy_eval(
                            ticker=ticker,
                            symbol=symbol,
                            strategy="momentum",
                            seconds_remaining=window.seconds_remaining,
                            price_change_pct=window.price_change_pct,
                            kalshi_yes_price=float(kalshi_yes_price),
                            real_prob=snap_prob_live,
                            edge=abs(yes_edge),
                            net_edge=0.0,
                            signal_side="yes" if yes_edge > 0 else "no",
                            action="no_signal",
                        )

            with contextlib.suppress(Exception):
                _write_live_state(live_state)

            for symbol, closed_window in tracker.pop_closed_windows():
                prev = tracker.get_previous_result(symbol)
                if recorder is not None and prev is not None:
                    recorder.record_market_event(
                        ticker=closed_window.ticker,
                        symbol=symbol,
                        event_type="close",
                        open_time=closed_window.open_time.isoformat(),
                        close_time=closed_window.close_time.isoformat(),
                        open_price=prev.open_price,
                        close_price=prev.close_price,
                        result="up" if prev.went_up else "down",
                    )

                now_utc = datetime.now(timezone.utc)
                last_ran = last_analysis_time.get(symbol)
                if last_ran is None or (now_utc - last_ran).total_seconds() >= 3600:
                    last_analysis_time[symbol] = now_utc
                    await _run_window_analysis(
                        symbol,
                        closed_window,
                        executor,
                        tracker,
                        alerter,
                        openrouter,
                    )

        except Exception:
            logger.exception("housekeeping_cycle_error")

        await asyncio.sleep(HOUSEKEEPING_INTERVAL_S)

    await executor.cancel_stale()


async def _check_settlements(
    client: KalshiClient,
    executor: Executor,
    alerter: AlerterLike,
) -> None:
    tickers = executor.active_tickers
    for ticker in tickers:
        try:
            data = await client._request("GET", f"/markets/{ticker}")
            market = data["market"]
        except Exception:
            continue
        if market.get("status") not in ("determined", "settled"):
            continue
        result = market.get("result", "")
        executor.record_settlement(ticker, result)
        if alerter:
            total_pnl = Decimal("0")
            for order in executor.settled_orders:
                if order.signal.ticker == ticker and order.pnl is not None:
                    total_pnl += order.pnl
            won = total_pnl > 0
            await alerter.trade_settled(ticker, won, total_pnl)


async def _evaluate_exits(
    executor: Executor,
    ticker: str,
    kalshi_yes_price: Decimal,
    settings: Settings,
    alerter: AlerterLike,
    window: WindowState,
    k: float = 150.0,
) -> None:
    filled = [o for o in executor.filled_orders if o.signal.ticker == ticker]
    if not filled:
        return

    for order in filled:
        current_value = (
            kalshi_yes_price
            if order.signal.side.value == "yes"
            else Decimal("1") - kalshi_yes_price
        )

        unrealized_loss = order.price - current_value

        real_prob = estimate_up_probability(
            window.price_change_pct, window.seconds_remaining, k=k
        )
        if order.signal.side.value == "yes":
            current_edge = real_prob - float(kalshi_yes_price)
        else:
            current_edge = (1 - real_prob) - float(Decimal("1") - kalshi_yes_price)

        should_exit = False
        reason = ""

        if order.signal.seconds_remaining > 90 and window.seconds_remaining < 30:
            should_exit = True
            reason = f"time_exit: entered_at={order.signal.seconds_remaining}s now={window.seconds_remaining}s"
        elif unrealized_loss >= max(
            Decimal(str(settings.exit_stop_loss)), order.price * Decimal("0.40")
        ):
            should_exit = True
            reason = f"stop_loss: unrealized_loss={unrealized_loss}/contract"
        elif current_edge <= 0:
            order.negative_edge_count += 1
            if order.negative_edge_count >= 3:
                should_exit = True
                reason = f"edge_gone: current_edge={current_edge:.4f}"
        else:
            order.negative_edge_count = 0

        if should_exit:
            logger.info("exit_signal", ticker=ticker, reason=reason)
            exited = await executor.exit_position(order, current_value)
            if exited and alerter is not None:
                await alerter.trade_exited(
                    ticker, order.signal.side.value, order.contracts, reason
                )


async def _settle_paper_positions(
    executor: Executor,
    tracker: WindowTracker,
    alerter: AlerterLike,
) -> None:
    for order in list(executor.filled_orders):
        symbol = order.signal.symbol
        window = tracker.get_window(symbol)
        if window is not None:
            continue
        prev = tracker.get_previous_result(symbol)
        if prev is None:
            continue

        result = "yes" if prev.went_up else "no"
        executor.record_settlement(order.signal.ticker, result)
        if alerter:
            total_pnl = Decimal("0")
            for settled in executor.settled_orders:
                if (
                    settled.signal.ticker == order.signal.ticker
                    and settled.pnl is not None
                ):
                    total_pnl += settled.pnl
            won = total_pnl > 0
            await alerter.trade_settled(order.signal.ticker, won, total_pnl)


async def _run_window_analysis(
    symbol: str,
    old_window: WindowState,
    executor: Executor,
    tracker: WindowTracker,
    alerter: AlerterLike,
    openrouter: OpenRouterClient | None,
) -> None:
    ticker = old_window.ticker

    prev = tracker.get_previous_result(symbol)
    if prev is None:
        logger.info(
            "window_analysis_skipped", ticker=ticker, reason="no previous result"
        )
        return

    try:
        cur = executor._db.execute(
            "SELECT COUNT(*) FROM signals WHERE ticker = ?", (ticker,)
        )
        signals_in_window = cur.fetchone()[0]

        cur = executor._db.execute(
            "SELECT COUNT(*) FROM trades WHERE ticker = ?", (ticker,)
        )
        trades_in_window = cur.fetchone()[0]

        cur = executor._db.execute(
            "SELECT SUM(CAST(pnl AS REAL)) FROM trades "
            "WHERE ticker = ? AND order_id LIKE 'PAPER-%'",
            (ticker,),
        )
        paper_pnl = cur.fetchone()[0] or 0.0

        cur = executor._db.execute(
            "SELECT side, contracts, price, pnl, edge "
            "FROM trades WHERE ticker = ? ORDER BY timestamp",
            (ticker,),
        )
        trades_detail = [
            {
                "side": row[0],
                "qty": row[1],
                "price": row[2],
                "pnl": row[3],
                "edge": row[4],
                "seconds_remaining": None,
            }
            for row in cur.fetchall()
        ]

        cur = executor._db.execute(
            "SELECT seconds_remaining FROM strategy_evals "
            "WHERE ticker = ? AND action = 'signal' ORDER BY timestamp",
            (ticker,),
        )
        secs_rows = [r[0] for r in cur.fetchall()]
        for i, td in enumerate(trades_detail):
            if i < len(secs_rows):
                td["seconds_remaining"] = secs_rows[i]

        cur = executor._db.execute(
            "SELECT price_change_pct FROM window_snapshots WHERE ticker = ? ORDER BY timestamp",
            (ticker,),
        )
        snapshots = [row[0] for row in cur.fetchall()]
        price_path_summary = None
        if snapshots:
            positive_count = sum(1 for pct in snapshots if pct > 0)
            total_count = len(snapshots)
            price_path_summary = f"{positive_count}/{total_count} snapshots positive"

        cur = executor._db.execute(
            "SELECT best_yes_ask FROM orderbook_snapshots WHERE ticker = ?", (ticker,)
        )
        prices = [row[0] for row in cur.fetchall() if row[0] is not None]
        kalshi_price_range = (min(prices), max(prices)) if prices else None

        cur = executor._db.execute(
            "SELECT AVG(yes_depth), AVG(no_depth) FROM orderbook_snapshots WHERE ticker = ?",
            (ticker,),
        )
        row = cur.fetchone()
        avg_depth = None
        if row and row[0] is not None and row[1] is not None:
            avg_depth = (int(row[0]), int(row[1]))

        model_prob_at_entry = None
        cur = executor._db.execute(
            "SELECT real_prob FROM strategy_evals "
            "WHERE ticker = ? AND action = 'signal' ORDER BY timestamp LIMIT 1",
            (ticker,),
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            model_prob_at_entry = float(row[0])

        commentary = await analyze_window(
            openrouter,
            executor,
            symbol,
            old_window.open_time,
            old_window.close_time,
            prev.open_price,
            prev.close_price,
            signals_in_window,
            trades_in_window,
            paper_pnl,
            trades_detail=trades_detail,
            price_path_summary=price_path_summary,
            kalshi_price_range=kalshi_price_range,
            avg_depth=avg_depth,
            model_prob_at_entry=model_prob_at_entry,
        )

        if alerter:
            await alerter.window_analyzed(
                symbol,
                old_window.open_time,
                old_window.close_time,
                prev.open_price,
                prev.close_price,
                signals_in_window,
                trades_in_window,
                paper_pnl,
                commentary,
            )
    except Exception:
        logger.exception("window_analysis_failed", ticker=ticker)


def main() -> None:
    setup_logging()
    settings = Settings()
    asyncio.run(run_bot(settings))


if __name__ == "__main__":
    main()
