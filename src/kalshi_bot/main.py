"""Main async loop — Coinbase feed -> window tracker -> strategies -> risk -> execute."""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal as unix_signal
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog

from kalshi_bot.alerts.discord import DiscordWebhookAlerter
from kalshi_bot.alerts.discord_bot import (
    DiscordBotAlerter,
    make_analysis_command as make_discord_analysis_command,
    make_balance_command as make_discord_balance_command,
    make_config_command as make_discord_config_command,
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
from kalshi_bot.risk.manager import RiskManager, RiskVetoError
from kalshi_bot.strategy.momentum import evaluate_momentum
from kalshi_bot.strategy.probability import estimate_k_from_vol, estimate_up_probability

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

SERIES_MAP: dict[str, str] = {
    "BTC": "KXBTC15M",
    "ETH": "KXETH15M",
    "SOL": "KXSOL15M",
}

ORDERBOOK_STALENESS_S = 15.0


def _write_live_state(live_state: dict[str, Any]) -> None:
    """Atomically write live state JSON for dashboard readers."""
    path = Path("live_state.json")
    tmp = Path("live_state.json.tmp")
    tmp.write_text(json.dumps(live_state, default=str), encoding="utf-8")
    tmp.replace(path)


def _make_alerter(
    settings: Settings,
) -> TelegramAlerter | DiscordWebhookAlerter | DiscordBotAlerter | None:
    if (
        settings.discord_enabled
        and settings.discord_bot_token
        and settings.discord_channel_id
    ):
        return DiscordBotAlerter(
            settings.discord_bot_token, settings.discord_channel_id
        )
    if (
        settings.telegram_enabled
        and settings.telegram_bot_token
        and settings.telegram_chat_id
    ):
        return TelegramAlerter(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            discord_webhook_url=settings.discord_webhook_url,
        )
    if settings.discord_enabled and settings.discord_webhook_url:
        return DiscordWebhookAlerter(settings.discord_webhook_url)
    return None


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
    alerter.register("newsession", make_discord_newsession_command())
    alerter.register_with_args("set", make_discord_set_command(settings))
    alerter.register_default_slash_commands()


async def _emit_feed_health_alerts(
    *,
    alerter: TelegramAlerter | DiscordWebhookAlerter | DiscordBotAlerter | None,
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
    alerter = _make_alerter(settings)

    logger.info(
        "alerter_mode_selected",
        mode=(
            "discord_bot"
            if isinstance(alerter, DiscordBotAlerter)
            else "telegram"
            if isinstance(alerter, TelegramAlerter)
            else "discord_webhook"
            if isinstance(alerter, DiscordWebhookAlerter)
            else "none"
        ),
    )

    openrouter: OpenRouterClient | None = None
    if settings.openrouter_api_key:
        openrouter = OpenRouterClient(
            settings.openrouter_api_key, settings.openrouter_model
        )

    if isinstance(alerter, TelegramAlerter):
        _register_commands(alerter, risk, executor, client, settings, tracker)
    elif isinstance(alerter, DiscordBotAlerter):
        _register_discord_commands(alerter, risk, executor, client, settings, tracker)

    price_queue: asyncio.Queue[PriceTick] = asyncio.Queue(maxsize=500)
    feed = CoinbaseFeed(price_queue)
    ws_feed = KalshiOrderbookFeed(settings)

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

    tg_task: asyncio.Task[None] | None = None
    if isinstance(alerter, TelegramAlerter):
        tg_task = asyncio.create_task(alerter.poll_commands())

    discord_task: asyncio.Task[None] | None = None
    if isinstance(alerter, DiscordBotAlerter):
        discord_task = asyncio.create_task(alerter.start())

    try:
        poll_task = asyncio.create_task(
            _poll_and_trade(
                client,
                tracker,
                risk,
                executor,
                settings,
                shutdown_event,
                alerter,
                openrouter,
                recorder,
                feed=feed,
                ws_feed=ws_feed,
            )
        )
        drain_task = asyncio.create_task(
            _drain_prices(price_queue, tracker, shutdown_event, recorder)
        )

        await shutdown_event.wait()
    finally:
        logger.info("shutting_down")
        await feed.stop()
        await ws_feed.stop()
        feed_task.cancel()
        ws_feed_task.cancel()
        shutdown_event.set()
        tasks = [feed_task, ws_feed_task, poll_task, drain_task]
        if discord_task:
            discord_task.cancel()
            tasks.append(discord_task)
        if tg_task:
            tg_task.cancel()
            tasks.append(tg_task)
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
) -> None:
    while not shutdown.is_set():
        try:
            tick = await asyncio.wait_for(queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        tracker.update_price(tick)
        if recorder is not None:
            recorder.record_price_tick(tick.symbol, tick.price, tick.timestamp)


async def _poll_and_trade(
    client: KalshiClient,
    tracker: WindowTracker,
    risk: RiskManager,
    executor: Executor,
    settings: Settings,
    shutdown: asyncio.Event,
    alerter: TelegramAlerter | DiscordWebhookAlerter | DiscordBotAlerter | None,
    openrouter: OpenRouterClient | None,
    recorder: DataRecorder | None = None,
    feed: CoinbaseFeed | None = None,
    ws_feed: KalshiOrderbookFeed | None = None,
) -> None:
    last_risk_block: dict[str, str] = {}
    last_analysis_time: dict[str, datetime] = {}
    stale_alert_state: dict[str, bool] = {}
    while not shutdown.is_set():
        try:
            await _trade_cycle(
                client,
                tracker,
                risk,
                executor,
                settings,
                alerter,
                openrouter,
                last_risk_block,
                recorder,
                feed=feed,
                ws_feed=ws_feed,
                last_analysis_time=last_analysis_time,
                stale_alert_state=stale_alert_state,
            )
        except RiskVetoError as exc:
            logger.info("risk_veto", reason=str(exc))
        except Exception:
            logger.exception("trade_cycle_error")

        min_remaining = float("inf")
        active_symbols = {s.strip() for s in settings.symbols.split(",")}
        for symbol in active_symbols:
            window = tracker.get_window(symbol)
            if window:
                min_remaining = min(min_remaining, window.seconds_remaining)

        interval = 2.0 if min_remaining <= settings.momentum_max_time else 10.0
        await asyncio.sleep(interval)

    await executor.cancel_stale()


async def _check_settlements(
    client: KalshiClient,
    executor: Executor,
    alerter: TelegramAlerter | DiscordWebhookAlerter | DiscordBotAlerter | None,
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
    alerter: TelegramAlerter | DiscordWebhookAlerter | DiscordBotAlerter | None,
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
    alerter: TelegramAlerter | DiscordWebhookAlerter | DiscordBotAlerter | None,
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


async def _trade_cycle(
    client: KalshiClient,
    tracker: WindowTracker,
    risk: RiskManager,
    executor: Executor,
    settings: Settings,
    alerter: TelegramAlerter | DiscordWebhookAlerter | DiscordBotAlerter | None,
    openrouter: OpenRouterClient | None = None,
    last_risk_block: dict[str, str] | None = None,
    recorder: DataRecorder | None = None,
    feed: CoinbaseFeed | None = None,
    ws_feed: KalshiOrderbookFeed | None = None,
    last_analysis_time: dict[str, datetime] | None = None,
    stale_alert_state: dict[str, bool] | None = None,
) -> None:
    live_state: dict[str, Any] = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "daily_pnl": float(risk.daily_pnl),
        "open_positions": risk.open_position_count,
        "symbols": {},
        "health": {},
    }

    await executor.check_pending_fills()
    await executor.promote_to_taker()
    await executor.cancel_stale()

    if executor._dry_run:
        await _settle_paper_positions(executor, tracker, alerter)

    await _check_settlements(client, executor, alerter)

    try:
        bankroll = await client.get_balance()
    except Exception:
        logger.warning("balance_fetch_failed")
        bankroll = Decimal("0")

    try:
        positions = await client.get_positions()
        risk.sync_positions(positions)
    except Exception:
        logger.warning("positions_sync_failed")

    util = client.api_utilization()
    coinbase_age = feed.last_tick_age_s if feed is not None else None
    kalshi_age = ws_feed.last_update_age_s if ws_feed is not None else None
    db_age = recorder.last_write_age_s if recorder is not None else None
    db_latency = recorder.last_write_latency_ms if recorder is not None else None

    live_state["health"] = {
        "coinbase_last_tick_age_s": coinbase_age,
        "kalshi_ws_last_update_age_s": kalshi_age,
        "coinbase_stale": bool(coinbase_age is not None and coinbase_age > 30.0),
        "kalshi_ws_stale": bool(kalshi_age is not None and kalshi_age > 30.0),
        "db_last_write_age_s": db_age,
        "db_last_write_latency_ms": db_latency,
        "api_read_per_sec": util["read_per_sec"],
        "api_write_per_sec": util["write_per_sec"],
        "api_read_utilization": util["read_utilization"],
        "api_write_utilization": util["write_utilization"],
    }

    if stale_alert_state is not None:
        await _emit_feed_health_alerts(
            alerter=alerter,
            coinbase_age=coinbase_age,
            kalshi_age=kalshi_age,
            sent_state=stale_alert_state,
        )

    active_symbols = {s.strip() for s in settings.symbols.split(",")}
    active_tickers: set[str] = set()

    for symbol, series in SERIES_MAP.items():
        if symbol not in active_symbols:
            continue
        try:
            markets = await client.get_open_markets(series)
        except Exception:
            logger.warning("market_fetch_failed", series=series)
            continue

        if not markets:
            continue

        now = datetime.now(timezone.utc)
        active_markets = [m for m in markets if m.close_time > now]
        if not active_markets:
            continue
        market = min(active_markets, key=lambda m: m.close_time)
        ticker = market.ticker
        active_tickers.add(ticker)

        tracker.set_window(
            symbol,
            ticker=ticker,
            open_time=market.open_time,
            close_time=market.close_time,
        )
        window = tracker.get_window(symbol)
        if window is None:
            continue

        if ws_feed is not None:
            await ws_feed.set_tickers(active_tickers)

        orderbook = None
        if ws_feed is not None:
            cached = ws_feed.get_orderbook(ticker)
            if cached is not None:
                ob_snapshot, ob_ts = cached
                age = (datetime.now(timezone.utc) - ob_ts).total_seconds()
                if age <= ORDERBOOK_STALENESS_S:
                    orderbook = ob_snapshot

        if orderbook is None:
            try:
                orderbook = await client.get_orderbook(ticker)
            except Exception:
                logger.warning("orderbook_fetch_failed", ticker=ticker)
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

        active_orders = [o for o in executor.filled_orders if o.signal.ticker == ticker]
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
                        unrealized += (mark_price - float(o.price)) * o.contracts
                    else:
                        unrealized += (
                            (1.0 - mark_price) - float(o.price)
                        ) * o.contracts

                fee_pc = active_orders[0].fee_per_contract if active_orders else None
                live_state["symbols"][symbol]["position_contracts"] = total_contracts
                live_state["symbols"][symbol]["position_notional"] = total_notional
                live_state["symbols"][symbol]["entry_price"] = avg_entry
                live_state["symbols"][symbol]["unrealized_pnl"] = unrealized
                live_state["symbols"][symbol]["fee_per_contract"] = fee_pc

        if recorder is not None:
            spread = (kalshi_yes_price - best_bid) if best_bid is not None else None
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

        recent = tracker.get_recent_changes(symbol)
        dynamic_k = (
            estimate_k_from_vol(recent) if len(recent) >= 5 else settings.logistic_k
        )

        snap_prob_live = estimate_up_probability(
            window.price_change_pct, window.seconds_remaining, k=dynamic_k
        )
        if symbol in live_state["symbols"]:
            live_state["symbols"][symbol]["model_prob"] = snap_prob_live
            live_state["symbols"][symbol]["dynamic_k"] = dynamic_k

        if recorder is not None:
            recorder.record_window_snapshot(
                ticker=ticker,
                symbol=symbol,
                seconds_remaining=window.seconds_remaining,
                open_price=window.open_price,
                current_price=window.current_price,
                price_change_pct=window.price_change_pct,
                kalshi_yes_ask=float(kalshi_yes_price),
                kalshi_yes_bid=float(best_bid) if best_bid is not None else None,
                kalshi_no_bid=float(best_no_bid) if best_no_bid is not None else None,
                real_prob=snap_prob_live,
                dynamic_k=dynamic_k,
                yes_depth=yes_depth,
                no_depth=no_depth,
                momentum_60s=window.momentum_60s,
            )

        await _evaluate_exits(
            executor, ticker, kalshi_yes_price, settings, alerter, window, k=dynamic_k
        )

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

        if recorder is not None:
            yes_edge = snap_prob_live - float(kalshi_yes_price)
            if signal is not None:
                recorder.record_strategy_eval(
                    ticker=ticker,
                    symbol=symbol,
                    strategy=signal.strategy.value,
                    seconds_remaining=window.seconds_remaining,
                    price_change_pct=window.price_change_pct,
                    kalshi_yes_price=float(kalshi_yes_price),
                    real_prob=snap_prob_live,
                    edge=float(signal.edge),
                    net_edge=float(signal.net_edge),
                    signal_side=signal.side.value,
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

        if signal is None:
            continue

        try:
            risk.check(signal)
        except RiskVetoError as exc:
            executor.log_signal(signal, "skip_risk", str(exc))
            reason_str = str(exc)
            if last_risk_block is None or last_risk_block.get(ticker) != reason_str:
                logger.info("risk_blocked", ticker=ticker, reason=reason_str)
                if last_risk_block is not None:
                    last_risk_block[ticker] = reason_str
            continue

        result = await executor.submit(signal, bankroll)
        if result is not None:
            if last_risk_block is not None:
                last_risk_block.pop(ticker, None)
            executor.log_signal(signal, "trade", f"order_id={result.order_id}")
            if alerter is not None:
                await alerter.trade_placed(signal, result.contracts, result.order_id)
        elif executor._dry_run:
            executor.log_signal(signal, "paper_trade", "")
        else:
            executor.log_signal(signal, "skip_sizing", "sizing returned 0 contracts")

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
        last_ran = (
            last_analysis_time.get(symbol) if last_analysis_time is not None else None
        )
        if last_ran is None or (now_utc - last_ran).total_seconds() >= 3600:
            if last_analysis_time is not None:
                last_analysis_time[symbol] = now_utc
            await _run_window_analysis(
                symbol,
                closed_window,
                executor,
                tracker,
                alerter,
                openrouter,
            )


async def _run_window_analysis(
    symbol: str,
    old_window: WindowState,
    executor: Executor,
    tracker: WindowTracker,
    alerter: TelegramAlerter | DiscordWebhookAlerter | DiscordBotAlerter | None,
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
