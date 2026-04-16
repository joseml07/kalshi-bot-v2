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

    # Risk limits
    daily_loss_limit: float = Field(default=25.0)
    max_per_trade: float = Field(default=25.0)
    max_concurrent_positions: int = Field(default=3)
    kelly_fraction: float = Field(default=0.25, ge=0.01, le=1.0)

    # Momentum strategy
    edge_threshold: float = Field(default=0.06)
    momentum_min_time: int = Field(default=30)
    momentum_max_time: int = Field(default=480)
    min_trade_price: float = Field(default=0.35)
    max_trade_price: float = Field(default=0.80)
    logistic_k: float = Field(default=150.0)
    symbols: str = Field(default="BTC")

    # Maker-first execution
    maker_first: bool = Field(default=True)
    maker_fill_horizon_s: int = Field(default=90)

    # Exit management
    exit_stop_loss: float = Field(default=0.10)

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
