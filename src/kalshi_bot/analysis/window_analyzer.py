"""Post-window AI analysis pipeline."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from kalshi_bot.client.openrouter import OpenRouterClient
from kalshi_bot.execution.executor import Executor

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a trading bot's hourly performance reviewer. This bot trades
15-minute crypto binary options on Kalshi (YES/NO contracts that settle at $0 or $1).
The bot buys NO when crypto dips (high win rate) and YES when crypto rises (experimental,
recently re-enabled with a 120-second entry restriction).

Rules:
- 3-4 sentences maximum. No filler, no restating the input numbers.
- ONLY flag things the operator should act on or investigate.
- Skip windows where nothing interesting happened (no trades, small moves).

Report ONLY if any of these apply:
- A trade lost money: why? Was it a bad entry, bad timing, or market reversal?
- YES side took a trade: how did it go? This is the experimental side we're monitoring.
- The model probability was far off from the actual outcome (miscalibration signal).
- An unusual pattern: multiple trades in one window, very large PnL, odd entry timing.
- If nothing noteworthy happened, just say "Routine window, no issues."

Do NOT waste words on: wins that went as expected, restating the price change,
generic commentary about volatility, or suggesting strategy changes."""


async def analyze_window(
    openrouter: OpenRouterClient | None,
    executor: Executor,
    symbol: str,
    open_time: datetime,
    close_time: datetime,
    open_price: float,
    close_price: float,
    signals_in_window: int,
    trades_in_window: int,
    paper_pnl: float,
    *,
    trades_detail: list[dict[str, Any]] | None = None,
    price_path_summary: str | None = None,
    kalshi_price_range: tuple[float, float] | None = None,
    avg_depth: tuple[int, int] | None = None,
    model_prob_at_entry: float | None = None,
) -> str:
    """Run AI analysis on a completed window and log it.

    Returns the AI commentary (or empty string if disabled/failed).
    """
    price_change_pct = (close_price - open_price) / open_price if open_price > 0 else 0.0
    result = "up" if close_price >= open_price else "down"

    commentary = ""
    model_name = ""

    if openrouter is not None:
        # Build enriched user prompt
        user_prompt = (
            f"Window: {symbol} {open_time.strftime('%H:%M')}-{close_time.strftime('%H:%M')} UTC\n"
            f"Open: ${open_price:,.2f} → Close: ${close_price:,.2f} (Δ: {price_change_pct:+.4%})\n"
            f"Result: {result}\n"
        )

        # Add price path summary if available
        if price_path_summary:
            user_prompt += f"\nPrice path: {price_path_summary}\n"

        # Add Kalshi price range if available
        if kalshi_price_range:
            min_price, max_price = kalshi_price_range
            user_prompt += f"Kalshi YES range: ${float(min_price):.2f}-${float(max_price):.2f}\n"

        # Add average depth if available
        if avg_depth:
            yes_depth, no_depth = avg_depth
            user_prompt += f"Avg orderbook depth: YES={yes_depth:,} NO={no_depth:,}\n"

        # Add model probability at entry if available
        if model_prob_at_entry is not None:
            user_prompt += f"\nModel prob(up) at trade time: {model_prob_at_entry:.3f}\n"

        # Add trade details if available
        if trades_detail:
            user_prompt += "\nTrades taken:\n"
            for i, trade in enumerate(trades_detail, 1):
                side = trade.get("side", "").upper()
                qty = trade.get("qty", 0)
                price = float(trade.get("price", 0))
                pnl = float(trade.get("pnl", 0))
                secs = trade.get("seconds_remaining", 0)
                edge = float(trade.get("edge", 0))
                outcome = "won" if pnl >= 0 else "lost"
                user_prompt += (
                    f"  {i}. {side} x{qty} @${price:.2f} (edge={edge:.1%}, {secs}s left) "
                    f"→ {outcome} ${pnl:+.2f}\n"
                )

        if trades_detail:
            first_s = trades_detail[0].get("seconds_remaining", "N/A")
            last_s = trades_detail[-1].get("seconds_remaining", "N/A")
            user_prompt += (
                f"\nSignals: {signals_in_window} fired "
                f"(first at {first_s}s, last at {last_s}s)\n"
            )
        else:
            user_prompt += f"\nSignals: {signals_in_window}\n"
        user_prompt += f"Paper P&L: ${paper_pnl:+.2f}\n"

        commentary = await openrouter.chat(SYSTEM_PROMPT, user_prompt)
        model_name = openrouter.last_model_used or openrouter._model
        if commentary:
            logger.info("AI analysis complete for %s (%s)", symbol, model_name)
        else:
            logger.warning("AI analysis returned empty for %s (all models failed)", symbol)

    executor.log_window_analysis(
        symbol=symbol,
        window_open=open_time.isoformat(),
        window_close=close_time.isoformat(),
        open_price=open_price,
        close_price=close_price,
        price_change_pct=price_change_pct,
        result=result,
        signals_count=signals_in_window,
        trades_count=trades_in_window,
        paper_pnl=paper_pnl,
        ai_commentary=commentary,
        ai_model=model_name,
    )

    return commentary
