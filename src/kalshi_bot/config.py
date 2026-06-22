"""Application configuration via environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Bot configuration loaded from .env file or environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Kalshi API
    kalshi_api_key: str = Field(description="Kalshi API key")
    kalshi_private_key_path: Path = Field(description="Path to RSA private key PEM file")
    kalshi_env: str = Field(default="demo", pattern=r"^(demo|prod)$")

    # Trading mode
    trading_mode: str = Field(default="paper", pattern=r"^(paper|live)$")
    paper_balance: float = Field(default=25.0, ge=0.0)
    bankroll_override: float = Field(
        default=0.0, ge=0.0,
        description="If > 0, use this as bankroll for Kelly sizing even in live mode. "
        "The bot still places real orders but sizes as if this were the balance.",
    )

    # Risk limits
    daily_loss_limit: float = Field(default=25.0)
    max_per_trade: float = Field(default=25.0)
    max_concurrent_positions: int = Field(default=3)
    max_contracts_per_trade: int = Field(default=10, ge=1, le=1000)
    max_trade_cost_dollars: float = Field(default=10.0, ge=1.0)
    kelly_fraction: float = Field(default=0.25, ge=0.01, le=1.0)

    # Strategy selection: "momentum" (default, V2), "lwm" (legacy), "settlement_edge" (V3)
    strategy_name: str = Field(default="settlement_edge", pattern=r"^(lwm|momentum|settlement_edge)$")

    # Settlement edge strategy (V3 — sell expensive YES, hold to expiry)
    settlement_edge_enabled: bool = Field(default=True)
    settlement_edge_sell_threshold: float = Field(default=0.85, ge=0.50, le=0.99)
    settlement_edge_min_time: int = Field(default=10)
    settlement_edge_max_time: int = Field(default=900)
    settlement_edge_allowed_hours: str = Field(
        default="",
        description="Comma-separated UTC hours, e.g. '4,13,18,22'. Empty = all hours.",
    )
    settlement_edge_require_crypto_down: bool = Field(default=False)
    settlement_edge_crypto_down_threshold: float = Field(default=-0.001)
    settlement_edge_require_prev_down: bool = Field(default=True)
    settlement_edge_grow_balance: bool = Field(default=True)
    settlement_edge_kelly_win_rate: float = Field(
        default=0.358,
        ge=0.0, le=1.0,
        description="Empirical win rate for Kelly sizing. 0 = use model estimate. Default 0.358 = 35.8%% prev-DOWN WR.",
    )
    settlement_edge_min_depth: int = Field(default=100)
    settlement_edge_max_spread: float = Field(default=0.03)
    settlement_edge_higher_edge_threshold: float = Field(default=0.02)
    
    # Shared strategy gates
    edge_threshold: float = Field(default=0.06)
    momentum_min_time: int = Field(default=30)
    momentum_max_time: int = Field(default=480)
    min_trade_price: float = Field(default=0.35)
    max_trade_price: float = Field(default=0.80)
    logistic_k: float = Field(default=150.0)
    symbols: str = Field(default="BTC")

    # LWM strategy
    lwm_decision_min_s: int = Field(default=30)
    lwm_decision_max_s: int = Field(default=540)
    lwm_yes_decision_max_s: int = Field(default=120)
    lwm_min_price_change: float = Field(default=0.0003)
    lwm_min_book_sum: float = Field(default=0.90)
    lwm_max_book_sum: float = Field(default=1.50)
    lwm_min_price: float = Field(default=0.05)
    lwm_max_price: float = Field(default=0.95)
    lwm_yes_only: bool = Field(default=False)
    lwm_no_side_edge_bonus: float = Field(default=0.04)

    # Side gating — disable YES side execution (still logs what-if signals)
    yes_side_disabled: bool = Field(default=False)

    # Time-of-day gating — block live trading during losing hours
    offpeak_start_utc: int = Field(default=20, ge=0, le=23)
    offpeak_end_utc: int = Field(default=23, ge=0, le=23)

    # Maker-first execution
    maker_first: bool = Field(default=True)
    maker_fill_horizon_s: int = Field(default=90)
    taker_fill_horizon_s: int = Field(default=120)

    # Exit management
    # Absolute floor: exit when unrealized loss >= this (per contract)
    exit_stop_loss: float = Field(default=0.10)
    # Drawdown fraction: exit when unrealized loss >= entry_price * this.
    # The effective threshold is max(exit_stop_loss, entry_price * exit_stop_drawdown).
    # Sweep (2026-05-23) showed 0.60 as optimal — lets trades breathe but caps
    # catastrophic losses on late entries that won't get a time_exit.
    exit_stop_drawdown: float = Field(default=0.60, ge=0.0, le=1.0)

    # Per-side risk gates (OFF by default; enable before flipping to live)
    # Maximum daily loss for a single side (yes or no) before that side is
    # paused for the rest of the day. None / 0 = disabled.
    per_side_daily_loss_limit: float = Field(default=0.0, ge=0.0)
    # Rolling window for side-degradation alerts. When the last N trades on a
    # side have a win rate below `side_wr_alert_threshold`, an alert fires.
    side_wr_alert_window: int = Field(default=30, ge=5)
    side_wr_alert_threshold: float = Field(default=0.30, ge=0.0, le=1.0)
    side_wr_alerts_enabled: bool = Field(default=False)

    # Telegram alerts (optional)
    telegram_enabled: bool = Field(default=False)
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")

    # Discord bot/webhook
    discord_enabled: bool = Field(default=False)
    discord_bot_token: str = Field(default="")
    discord_channel_id: str = Field(default="")

    # OpenRouter AI Analysis
    openrouter_api_key: str = Field(default="")
    openrouter_model: str = Field(default="deepseek/deepseek-chat")

    # Dashboard
    dashboard_port: int = Field(default=8080)

    # Discord webhook (optional)
    discord_webhook_url: str = Field(default="")

    # Dashboard admin key (gates write endpoints: /api/reset, /api/kill, /api/settings)
    dashboard_admin_key: str = Field(default="")

    @property
    def rest_base_url(self) -> str:
        if self.kalshi_env == "demo":
            return "https://demo-api.kalshi.co/trade-api/v2"
        return "https://api.elections.kalshi.com/trade-api/v2"

    @property
    def ws_base_url(self) -> str:
        if self.kalshi_env == "demo":
            return "wss://demo-api.kalshi.co/trade-api/ws/v2"
        return "wss://api.elections.kalshi.com/trade-api/ws/v2"
