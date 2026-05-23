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
    make_reset_command as make_discord_reset_command,
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
    make_calendar_command,
    make_cleardata_command,
    make_config_command,
    make_data_command,
    make_ip_command,
    make_kill_command,
    make_newsession_command,
    make_pnl_command,
    make_positions_command,
    make_reset_command,
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
from kalshi_bot.alerts.control import SettingError, mutate_setting
from kalshi_bot.analysis.window_analyzer import analyze_window
from kalshi_bot.client.coinbase import CoinbaseFeed
from kalshi_bot.client.kalshi import KalshiClient
from kalshi_bot.client.kalshi_ws import KalshiOrderbookFeed
from kalshi_bot.client.openrouter import OpenRouterClient
from kalshi_bot.config import Settings
from kalshi_bot.control_channel import drain as drain_control_queue
from kalshi_bot.data.recorder import DataRecorder
from kalshi_bot.data.window_tracker import WindowState, WindowTracker
from kalshi_bot.execution.executor import Executor, OrderState
from kalshi_bot.logging_config import setup_logging
from kalshi_bot.models.price import PriceTick
from kalshi_bot.models.market import Market
from kalshi_bot.risk.manager import RiskManager, RiskVetoError
from kalshi_bot.strategy.fees import maker_fee, taker_fee
from kalshi_bot.strategy.lwm import evaluate_lwm
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
EVAL_STALE_THRESHOLD_S = 180.0


def _bump_counter(counters: dict[str, int], key: str) -> None:
    """Increment named in-memory signal counter."""
    counters[key] = counters.get(key, 0) + 1


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _infer_no_signal_block(
    *,
    window: WindowState,
    orderbook: Any,
    settings: Settings,
) -> str:
    """Best-effort likely block reason when the strategy returns no signal."""
    seconds_remaining = window.seconds_remaining

    if settings.strategy_name == "lwm":
        if not (
            settings.lwm_decision_min_s <= seconds_remaining <= settings.lwm_decision_max_s
        ):
            return "lwm_outside_time_window"
        if abs(window.price_change_pct) < settings.lwm_min_price_change:
            return "lwm_weak_price_move"
        yes_bid = orderbook.best_yes_bid
        no_bid = orderbook.best_no_bid
        if yes_bid is None or no_bid is None:
            return "lwm_book_missing"
        book_sum = float(yes_bid + no_bid)
        if not (settings.lwm_min_book_sum <= book_sum <= settings.lwm_max_book_sum):
            return "lwm_bad_book_sum"
        if window.price_change_pct < 0 and settings.lwm_yes_only:
            return "lwm_no_side_disabled"
        return "lwm_edge_below_threshold"

    if not (
        settings.momentum_min_time <= seconds_remaining <= settings.momentum_max_time
    ):
        return "outside_time_window"

    momentum = window.momentum_60s
    if momentum is None or momentum == 0.0:
        return "momentum_none"

    imbalance = orderbook.orderbook_imbalance
    mom_sign = _sign(momentum)
    imb_sign = _sign(imbalance)
    if mom_sign == 0 or imb_sign == 0 or mom_sign != imb_sign:
        return "momentum_obi_sign_mismatch"

    up_prob = estimate_up_probability(
        window.price_change_pct,
        seconds_remaining,
        k=settings.logistic_k,
    )

    if mom_sign > 0:
        est_prob = up_prob
        maker_price = orderbook.best_yes_bid
        taker_price = orderbook.best_yes_ask
    else:
        est_prob = 1 - up_prob
        maker_price = orderbook.best_no_bid
        taker_price = orderbook.best_no_ask

    maker_ok = False
    if (
        maker_price is not None
        and settings.min_trade_price <= float(maker_price) <= settings.max_trade_price
    ):
        contracts = 1
        maker_fee_total = maker_fee(contracts, float(maker_price))
        maker_net_edge = (
            est_prob - float(maker_price) - float(maker_fee_total / contracts)
        )
        maker_ok = maker_net_edge >= settings.edge_threshold

    taker_ok = False
    if (
        taker_price is not None
        and settings.min_trade_price <= float(taker_price) <= settings.max_trade_price
    ):
        contracts = 1
        taker_fee_total = taker_fee(contracts, float(taker_price))
        taker_net_edge = (
            est_prob - float(taker_price) - float(taker_fee_total / contracts)
        )
        taker_ok = taker_net_edge >= settings.edge_threshold

    if not maker_ok and not taker_ok:
        maker_in_bounds = (
            maker_price is not None
            and settings.min_trade_price
            <= float(maker_price)
            <= settings.max_trade_price
        )
        taker_in_bounds = (
            taker_price is not None
            and settings.min_trade_price
            <= float(taker_price)
            <= settings.max_trade_price
        )
        if not maker_in_bounds and not taker_in_bounds:
            return "price_out_of_bounds"
        return "edge_below_threshold"

    return "edge_below_threshold"


class CachedState:
    """TTL-based cache for slow-changing REST state."""

    def __init__(self) -> None:
        self.balance: Decimal = Decimal("0")
        self._balance_refreshed_mono = 0.0
        self._positions_refreshed_mono = 0.0
        self._markets: dict[str, Market] = {}
        self._markets_refreshed_mono = 0.0
        self.active_symbols: set[str] = set()

    async def refresh_balance(
        self,
        client: KalshiClient,
        ttl_s: float = 10.0,
        simulated_balance: Decimal | None = None,
    ) -> None:
        """Refresh cached balance if TTL expired."""
        now = time.monotonic()
        if simulated_balance is not None:
            self.balance = simulated_balance
            self._balance_refreshed_mono = now
            return
        if now - self._balance_refreshed_mono < ttl_s:
            return
        try:
            self.balance = await client.get_balance()
            self._balance_refreshed_mono = now
        except Exception:
            logger.exception("balance_refresh_failed")

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
                strike = market.settlement_strike
                tracker.set_window(
                    symbol,
                    ticker=market.ticker,
                    open_time=market.open_time,
                    close_time=market.close_time,
                    strike=float(strike) if strike is not None else None,
                    strike_type=market.strike_type,
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


def _drain_control_requests(settings: Settings, risk: RiskManager) -> None:
    """Apply queued control requests from the dashboard process."""
    for req in drain_control_queue():
        req_type = req.get("type")
        payload = req.get("payload", {}) or {}
        if req_type == "set":
            key = str(payload.get("key", ""))
            value = payload.get("value")
            try:
                alias, coerced = mutate_setting(settings, key, value)
                logger.info("control_set", key=alias, value=coerced)
            except SettingError as exc:
                logger.warning("control_set_rejected", key=key, error=str(exc))
        elif req_type == "reset":
            clear_pnl = bool(payload.get("clear_pnl", False))
            result = risk.reset_session(clear_pnl=clear_pnl)
            logger.info("control_reset", **result)
        else:
            logger.warning("control_unknown_request", type=req_type)


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
    alerter.register("balance", make_balance_command(client, settings))
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
    alerter.register_with_args("reset", make_reset_command(risk))
    alerter.register_with_args("calendar", make_calendar_command())


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
    alerter.register("balance", make_discord_balance_command(client, settings))
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
    alerter.register_with_args("reset", make_discord_reset_command(risk))
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
    executor = Executor(client, risk, dry_run=dry_run, settings=settings)
    tracker = WindowTracker()
    recorder = DataRecorder()
    cached = CachedState()
    signal_counters: dict[str, int] = {}
    signal_counter_window_start: list[datetime] = [datetime.now(timezone.utc)]
    last_eval_reason: dict[str, dict[str, Any]] = {}
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
    executor.attach_orderbook_source(ws_feed.get_orderbook)

    _bankroll_sim: Decimal | None = None
    if dry_run:
        _bankroll_sim = Decimal(str(settings.paper_balance))
    elif settings.bankroll_override > 0:
        _bankroll_sim = Decimal(str(settings.bankroll_override))
    await cached.refresh_balance(
        client,
        ttl_s=0.0,
        simulated_balance=_bankroll_sim,
    )
    if not dry_run and _bankroll_sim is None and cached.balance <= 0:
        logger.critical(
            "live_mode_zero_balance — refusing to start. "
            "Check API credentials and account funding.",
            balance=str(cached.balance),
        )
        raise SystemExit(1)
    logger.info("startup_balance", balance=str(cached.balance), mode="paper" if dry_run else "live")
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
                last_eval_reason,
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
                last_eval_reason=last_eval_reason,
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
            try:
                tick = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            tracker.update_price(tick)
            if eval_trigger is not None:
                eval_trigger.set()
            if recorder is not None:
                recorder.record_price_tick(tick.symbol, tick.price, tick.timestamp)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("drain_prices_error")
            await asyncio.sleep(0.25)


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
    last_eval_reason: dict[str, dict[str, Any]],
) -> None:
    """Event-driven strategy evaluation loop (no REST reads on hot path)."""
    last_eval_mono: dict[str, float] = {}
    last_risk_block: dict[str, str] = {}

    def _record_reason(
        symbol: str,
        ticker: str | None,
        result: str,
        *,
        likely_block: str | None = None,
        log: bool = False,
        **fields: Any,
    ) -> None:
        prev_state = last_eval_reason.get(symbol, {})
        prev = prev_state.get("result")
        prev_block = prev_state.get("likely_block")
        last_eval_reason[symbol] = {
            "result": result,
            "at": datetime.now(timezone.utc).isoformat(),
            "ticker": ticker,
            "likely_block": likely_block,
        }
        if log and (prev != result or (likely_block is not None and prev_block != likely_block)):
            logger.info(result, symbol=symbol, ticker=ticker, **fields)

    while not shutdown.is_set():
        try:
            now_utc = datetime.now(timezone.utc)
            if (now_utc - signal_counter_window_start[0]).total_seconds() >= 3600:
                signal_counters.clear()
                signal_counter_window_start[0] = now_utc

            try:
                await asyncio.wait_for(eval_trigger.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            eval_trigger.clear()

            _drain_control_requests(settings, risk)

            symbols = cached.active_symbols or {
                s.strip() for s in settings.symbols.split(",") if s.strip()
            }
            now = time.monotonic()

            for symbol in symbols:
                try:
                    last = last_eval_mono.get(symbol, 0.0)
                    if now - last < MIN_EVAL_INTERVAL_S:
                        _bump_counter(signal_counters, "throttled")
                        continue

                    window = tracker.get_window(symbol)
                    if window is None:
                        _bump_counter(signal_counters, "skip_no_window")
                        _record_reason(symbol, None, "skip_no_window")
                        continue

                    market = cached.get_market(symbol)
                    if market is None:
                        _bump_counter(signal_counters, "skip_no_market")
                        _record_reason(symbol, None, "skip_no_market")
                        continue
                    ticker = market.ticker

                    ob_cached = ws_feed.get_orderbook(ticker)
                    if ob_cached is None:
                        _bump_counter(signal_counters, "skip_no_orderbook")
                        _record_reason(
                            symbol,
                            ticker,
                            "skip_no_orderbook",
                            likely_block="no_orderbook",
                            log=True,
                        )
                        continue
                    orderbook, ob_ts = ob_cached
                    age = (datetime.now(timezone.utc) - ob_ts).total_seconds()
                    if age > ORDERBOOK_STALENESS_S:
                        _bump_counter(signal_counters, "skip_stale_orderbook")
                        _record_reason(
                            symbol,
                            ticker,
                            "skip_stale_orderbook",
                            likely_block="stale_orderbook",
                            log=True,
                            age_s=age,
                        )
                        continue

                    if settings.strategy_name == "lwm":
                        signal = evaluate_lwm(
                            window,
                            ticker,
                            orderbook,
                            edge_threshold=settings.edge_threshold,
                            decision_min_s=settings.lwm_decision_min_s,
                            decision_max_s=settings.lwm_decision_max_s,
                            yes_decision_max_s=settings.lwm_yes_decision_max_s,
                            min_price_change=settings.lwm_min_price_change,
                            min_book_sum=settings.lwm_min_book_sum,
                            max_book_sum=settings.lwm_max_book_sum,
                            min_price=settings.lwm_min_price,
                            max_price=settings.lwm_max_price,
                            yes_only=settings.lwm_yes_only,
                            no_side_edge_bonus=settings.lwm_no_side_edge_bonus,
                            maker_first=settings.maker_first,
                        )
                    else:
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
                        likely_block = _infer_no_signal_block(
                            window=window,
                            orderbook=orderbook,
                            settings=settings,
                        )
                        _bump_counter(signal_counters, "no_signal")
                        _record_reason(
                            symbol,
                            ticker,
                            "no_signal",
                            likely_block=likely_block,
                            log=True,
                            seconds_remaining=window.seconds_remaining,
                            price_change_pct=window.price_change_pct,
                            momentum_60s=window.momentum_60s,
                            orderbook_imbalance=orderbook.orderbook_imbalance,
                            best_yes_bid=float(orderbook.best_yes_bid)
                            if orderbook.best_yes_bid is not None
                            else None,
                            best_yes_ask=float(orderbook.best_yes_ask)
                            if orderbook.best_yes_ask is not None
                            else None,
                            best_no_bid=float(orderbook.best_no_bid)
                            if orderbook.best_no_bid is not None
                            else None,
                            best_no_ask=float(orderbook.best_no_ask)
                            if orderbook.best_no_ask is not None
                            else None,
                            min_trade_price=settings.min_trade_price,
                            max_trade_price=settings.max_trade_price,
                            edge_threshold=settings.edge_threshold,
                        )
                        continue

                    try:
                        risk.check(signal, orderbook)
                    except RiskVetoError as exc:
                        executor.log_signal(signal, "skip_risk", str(exc))
                        _bump_counter(signal_counters, "skip_risk")
                        reason_str = str(exc)
                        if last_risk_block.get(ticker) != reason_str:
                            logger.info(
                                "risk_blocked", ticker=ticker, reason=reason_str
                            )
                            last_risk_block[ticker] = reason_str
                        _record_reason(symbol, ticker, "skip_risk")
                        continue

                    # YES side gating: log what-if but don't execute
                    if (
                        settings.yes_side_disabled
                        and signal.side.value == "yes"
                    ):
                        executor.log_signal(
                            signal, "whatif_yes_disabled",
                            f"edge={signal.net_edge} price={signal.kalshi_price}",
                        )
                        _bump_counter(signal_counters, "whatif_yes")
                        _record_reason(symbol, ticker, "whatif_yes_disabled")
                        logger.info(
                            "whatif_yes_disabled",
                            ticker=ticker,
                            side="yes",
                            edge=str(signal.net_edge),
                            price=str(signal.kalshi_price),
                            secs=signal.seconds_remaining,
                        )
                        continue

                    submit_result = await executor.submit(signal, cached.balance)
                    if submit_result.order is not None:
                        _bump_counter(signal_counters, "trade")
                        _record_reason(symbol, ticker, "trade")
                        last_risk_block.pop(ticker, None)
                        executor.log_signal(
                            signal, "trade", f"order_id={submit_result.order.order_id}"
                        )
                        if alerter is not None:
                            await alerter.trade_placed(
                                signal,
                                submit_result.order.contracts,
                                submit_result.order.order_id,
                            )
                    else:
                        skip_reason = submit_result.skip_reason or "unknown"
                        if skip_reason == "edge_gone":
                            _bump_counter(signal_counters, "skip_edge")
                            _record_reason(symbol, ticker, "skip_edge")
                            executor.log_signal(
                                signal,
                                "skip_edge",
                                "edge gone at fresh price",
                            )
                        elif skip_reason == "sizing_zero":
                            _bump_counter(signal_counters, "skip_sizing")
                            _record_reason(symbol, ticker, "skip_sizing")
                            executor.log_signal(
                                signal, "skip_sizing", "sizing returned 0 contracts"
                            )
                        else:
                            _bump_counter(signal_counters, "skip_submit")
                            _record_reason(symbol, ticker, "skip_submit")
                            executor.log_signal(
                                signal, "skip_submit", f"submit skipped: {skip_reason}"
                            )

                    # --- Exit evaluation (fast path) ---
                    # Check exits on every eval tick (~200ms) instead of
                    # waiting for the 5s housekeeping cycle. The time_exit
                    # fires at <30s remaining; 5s jitter was 17% timing error
                    # on the bot's most profitable operation.
                    best_yes_bid = orderbook.best_yes_bid
                    best_no_bid = orderbook.best_no_bid
                    await _evaluate_exits(
                        executor, ticker,
                        best_yes_bid, best_no_bid,
                        alerter, window,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Never let one symbol's failure kill the eval loop. Log
                    # with traceback and move on. The 2026-04-18/-04-20 wedges
                    # both presented as this loop silently dying with no log
                    # line — the bare `except Exception` is what stops that.
                    logger.exception(
                        "fast_eval_iter_error",
                        extra={"symbol": symbol},
                    )
                    continue
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("fast_eval_loop_error")
            # Avoid hot-looping if something pathological is raising every
            # iteration (e.g. cached-state corruption).
            await asyncio.sleep(1.0)


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
    last_eval_reason: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Periodic housekeeping loop (REST I/O, settlements, exits, live state)."""
    # Dedupe only: key = (symbol, window_close_time.isoformat()). Prevents
    # double-analysis if the same closed window gets popped twice; does NOT
    # gate cadence — every newly-closed window gets analyzed.
    analyzed_windows: set[tuple[str, str]] = set()
    ANALYSIS_INTERVAL_S = 3600.0  # AI analysis at most once per hour
    last_analysis_mono: float = 0.0
    stale_alert_state: dict[str, bool] = {}
    last_heartbeat_mono: float = 0.0
    last_orphan_check_mono: float = 0.0
    ORPHAN_CHECK_INTERVAL_S = 1800.0  # 30 minutes

    while not shutdown.is_set():
        try:
            await executor.check_pending_fills()
            now_mono = time.monotonic()
            if now_mono - last_orphan_check_mono > ORPHAN_CHECK_INTERVAL_S:
                executor._reconcile_orphans()
                last_orphan_check_mono = now_mono
            promotion_failures = await executor.promote_to_taker()
            stale_cancels = await executor.cancel_stale()
            if alerter is not None:
                for order in stale_cancels:
                    await alerter.trade_failed(
                        order.signal.ticker,
                        order.signal.side.value,
                        order.contracts,
                        "order not filled within timeout — cancelled",
                    )
                for order in promotion_failures:
                    await alerter.trade_failed(
                        order.signal.ticker,
                        order.signal.side.value,
                        order.contracts,
                        "maker timed out and taker promotion failed",
                    )

            if executor._dry_run:
                await _settle_paper_positions(executor, tracker, alerter)
            await _check_settlements(client, executor, alerter)

            _br_sim: Decimal | None = None
            if settings.trading_mode == "paper":
                _br_sim = Decimal(str(settings.paper_balance))
            elif settings.bankroll_override > 0:
                _br_sim = Decimal(str(settings.bankroll_override))
            await cached.refresh_balance(
                client,
                simulated_balance=_br_sim,
            )
            await cached.refresh_positions(client, risk)
            await cached.refresh_markets(client, tracker, settings)

            active_tickers = {
                market.ticker
                for symbol in cached.active_symbols
                if (market := cached.get_market(symbol)) is not None
            }
            if ws_feed is not None:
                await ws_feed.set_tickers(active_tickers)

            balance_age_s: float | None = None
            if cached._balance_refreshed_mono > 0:
                balance_age_s = max(
                    0.0, time.monotonic() - cached._balance_refreshed_mono
                )

            live_state: dict[str, Any] = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "daily_pnl": float(risk.daily_pnl),
                "open_positions": risk.open_position_count,
                "balance": float(cached.balance),
                "balance_age_s": balance_age_s,
                "trading_mode": settings.trading_mode,
                "kelly_fraction": settings.kelly_fraction,
                "symbols": {},
                "health": {},
            }

            util = client.api_utilization()
            coinbase_age = feed.last_tick_age_s if feed is not None else None
            kalshi_age = ws_feed.last_update_age_s if ws_feed is not None else None
            ws_diag = ws_feed.diagnostics() if ws_feed is not None else None
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
            if ws_diag is not None:
                live_state["health"]["kalshi_ws"] = ws_diag

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

            now_mono = time.monotonic()
            if now_mono - last_heartbeat_mono >= 60.0:
                last_heartbeat_mono = now_mono
                trades_h = (
                    signal_counters.get("trade", 0)
                    if signal_counters is not None
                    else 0
                )
                signals_h = (
                    sum(signal_counters.values()) if signal_counters is not None else 0
                )

                eval_stale: list[dict[str, Any]] = []
                eval_max_stale_age_s = 0.0
                if last_eval_reason is not None:
                    now_iso = datetime.now(timezone.utc)
                    for symbol, rec in last_eval_reason.items():
                        at_raw = rec.get("at")
                        if not isinstance(at_raw, str):
                            continue
                        try:
                            age_s = (now_iso - datetime.fromisoformat(at_raw)).total_seconds()
                        except ValueError:
                            continue
                        if age_s > EVAL_STALE_THRESHOLD_S:
                            eval_stale.append(
                                {"symbol": symbol, "age_s": round(age_s, 1)}
                            )
                            eval_max_stale_age_s = max(eval_max_stale_age_s, age_s)

                eval_stale_count = len(eval_stale)
                eval_max_stale_age_s = round(eval_max_stale_age_s, 1)
                if eval_stale:
                    logger.warning("eval_loop_stalled", stale=eval_stale)

                live_state["health"]["eval_stale_symbols"] = eval_stale_count
                live_state["health"]["eval_max_stale_age_s"] = eval_max_stale_age_s

                logger.info(
                    "HEALTH",
                    mode=settings.trading_mode,
                    balance=float(cached.balance),
                    daily_pnl=float(risk.daily_pnl),
                    open_positions=risk.open_position_count,
                    trades_h=trades_h,
                    signals_h=signals_h,
                    resyncs_ticker=(ws_diag.get("resync_ticker", 0) if ws_diag else 0),
                    resyncs_full=(ws_diag.get("resync_full", 0) if ws_diag else 0),
                    negative_qty=(ws_diag.get("negative_qty", 0) if ws_diag else 0),
                    coinbase_age_s=coinbase_age,
                    kalshi_ws_age_s=kalshi_age,
                    coinbase_stale=bool(
                        coinbase_age is not None and coinbase_age > 30.0
                    ),
                    kalshi_ws_stale=bool(kalshi_age is not None and kalshi_age > 30.0),
                    eval_stale_symbols=eval_stale_count,
                    eval_max_stale_age_s=eval_max_stale_age_s,
                )

            await _emit_feed_health_alerts(
                alerter=alerter,
                coinbase_age=coinbase_age,
                kalshi_age=kalshi_age,
                sent_state=stale_alert_state,
            )

            if ws_diag is not None:
                ws_warn = (
                    ws_diag.get("negative_qty", 0) > 100
                    or ws_diag.get("delta_missing_fields", 0) > 20
                    or ws_diag.get("resync_ticker", 0) > 0
                    or ws_diag.get("resync_full", 0) > 0
                )
                if ws_warn:
                    logger.warning("kalshi_ws_health_alert", **ws_diag)

            for symbol in cached.active_symbols:
                market = cached.get_market(symbol)
                if market is None:
                    continue
                ticker = market.ticker

                window = tracker.get_window(symbol)
                if window is None:
                    continue

                orderbook = None
                orderbook_age_s: float | None = None
                if ws_feed is not None:
                    ob_cached = ws_feed.get_orderbook(ticker)
                    if ob_cached is not None:
                        ob_snapshot, ob_ts = ob_cached
                        age = (datetime.now(timezone.utc) - ob_ts).total_seconds()
                        if age <= ORDERBOOK_STALENESS_S:
                            orderbook = ob_snapshot
                            orderbook_age_s = age

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
                    "momentum_60s": window.momentum_60s,
                    "kalshi_yes_ask": float(kalshi_yes_price),
                    "kalshi_yes_bid": float(best_bid) if best_bid is not None else None,
                    "kalshi_no_bid": float(best_no_bid)
                    if best_no_bid is not None
                    else None,
                    "orderbook_imbalance": orderbook.orderbook_imbalance,
                    "orderbook_age_s": orderbook_age_s,
                    "position_contracts": 0,
                    "position_notional": 0.0,
                    "entry_price": None,
                    "unrealized_pnl": 0.0,
                    "fee_per_contract": None,
                }

                if last_eval_reason is not None:
                    reason = last_eval_reason.get(symbol)
                    if reason is not None:
                        live_state["symbols"][symbol]["last_eval"] = {
                            "result": reason.get("result"),
                            "at": reason.get("at"),
                        }
                        live_state["symbols"][symbol]["likely_block"] = reason.get(
                            "likely_block"
                        )

                active_orders = [
                    o for o in executor._orders.values()
                    if o.signal.ticker == ticker
                    and o.state in (OrderState.FILLED, OrderState.EXITING)
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

                if settings.strategy_name == "lwm":
                    signal_for_record = evaluate_lwm(
                        window,
                        ticker,
                        orderbook,
                        edge_threshold=settings.edge_threshold,
                        decision_min_s=settings.lwm_decision_min_s,
                        decision_max_s=settings.lwm_decision_max_s,
                        yes_decision_max_s=settings.lwm_yes_decision_max_s,
                        min_price_change=settings.lwm_min_price_change,
                        min_book_sum=settings.lwm_min_book_sum,
                        max_book_sum=settings.lwm_max_book_sum,
                        min_price=settings.lwm_min_price,
                        max_price=settings.lwm_max_price,
                        yes_only=settings.lwm_yes_only,
                        no_side_edge_bonus=settings.lwm_no_side_edge_bonus,
                        maker_first=settings.maker_first,
                    )
                else:
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

                dedupe_key = (symbol, closed_window.close_time.isoformat())
                now_analysis = time.monotonic()
                if (
                    dedupe_key not in analyzed_windows
                    and now_analysis - last_analysis_mono >= ANALYSIS_INTERVAL_S
                ):
                    analyzed_windows.add(dedupe_key)
                    last_analysis_mono = now_analysis
                    try:
                        await asyncio.wait_for(
                            _run_window_analysis(
                                symbol,
                                closed_window,
                                executor,
                                tracker,
                                alerter,
                                openrouter,
                            ),
                            timeout=30.0,
                        )
                    except asyncio.TimeoutError:
                        logger.warning(
                            "window_analysis_timeout symbol=%s window=%s",
                            symbol,
                            closed_window.close_time.isoformat(),
                        )

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("housekeeping_cycle_error")

        await asyncio.sleep(HOUSEKEEPING_INTERVAL_S)

    await executor.cancel_stale()


async def _emit_risk_events(
    alerter: AlerterLike,
    events: list[dict[str, Any]],
) -> None:
    """Fan out per-side risk events (pause / WR degradation) to the alerter.

    Uses the existing `trade_exited("SYSTEM", ...)` channel so the alerter
    interface doesn't need a new method.
    """
    if not alerter or not events:
        return
    for ev in events:
        for kind, payload in ev.items():
            if kind == "side_paused":
                side = payload.get("side", "?")
                pnl = payload.get("daily_pnl", "?")
                limit = payload.get("limit", "?")
                msg = (
                    f"{side.upper()} side paused for the day: "
                    f"daily_pnl={pnl} limit=-{limit}"
                )
                await alerter.trade_exited("SYSTEM", "side_paused", 0, msg)
            elif kind == "side_wr_alert":
                side = payload.get("side", "?")
                wr = payload.get("win_rate", 0.0) * 100
                window = payload.get("window", "?")
                threshold = payload.get("threshold", 0.0) * 100
                msg = (
                    f"{side.upper()} win-rate degraded: "
                    f"{wr:.0f}% over last {window} trades "
                    f"(threshold {threshold:.0f}%)"
                )
                await alerter.trade_exited("SYSTEM", "wr_degraded", 0, msg)


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
            logger.warning("settlement_check_failed ticker=%s", ticker, exc_info=True)
            continue
        if market.get("status") not in ("determined", "settled"):
            continue
        result = market.get("result", "")
        risk_events = executor.record_settlement(ticker, result)
        if alerter:
            total_pnl = Decimal("0")
            for order in executor.settled_orders:
                if order.signal.ticker == ticker and order.pnl is not None:
                    total_pnl += order.pnl
            won = total_pnl > 0
            await alerter.trade_settled(ticker, won, total_pnl)
            await _emit_risk_events(alerter, risk_events)


async def _evaluate_exits(
    executor: Executor,
    ticker: str,
    best_yes_bid: Decimal | None,
    best_no_bid: Decimal | None,
    alerter: AlerterLike,
    window: WindowState,
) -> None:
    filled = [o for o in executor.filled_orders if o.signal.ticker == ticker]
    if not filled:
        return

    for order in filled:
        if order.signal.side.value == "yes":
            if best_yes_bid is None:
                continue
            current_value = best_yes_bid
        else:
            if best_no_bid is None:
                continue
            current_value = best_no_bid

        # Binary contracts cap loss at entry_price by construction. Backtest of 306
        # settled trades showed stop_loss + edge_gone exits converted 112 winning
        # entries into recorded losses (-$95.92 vs hold-to-settlement). Only the
        # time_exit rule remains: lock in mark before final-30s settlement risk.
        should_exit = False
        reason = ""

        # Take-profit was disabled 2026-05-23 after backtest showed it cost
        # ~14% PnL by locking in 60c partial wins on contracts that settle at
        # $1.00.  Original motivation (preventing 1730-style unrealized swings)
        # is already addressed by MAX_CONTRACTS=10 sizing cap.

        if not should_exit and order.signal.seconds_remaining > 90 and window.seconds_remaining < 30:
            should_exit = True
            reason = (
                f"time_exit: entered_at={order.signal.seconds_remaining}s "
                f"now={window.seconds_remaining}s"
            )

        if should_exit:
            logger.info("exit_signal", ticker=ticker, reason=reason)
            exited, risk_events = await executor.exit_position(order, current_value, exit_reason=reason.split(":", 1)[0])
            if exited and alerter is not None:
                await alerter.trade_exited(
                    ticker, order.signal.side.value, order.contracts, reason
                )
                await _emit_risk_events(alerter, risk_events)


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
        risk_events = executor.record_settlement(order.signal.ticker, result)
        if alerter:
            await _emit_risk_events(alerter, risk_events)
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
