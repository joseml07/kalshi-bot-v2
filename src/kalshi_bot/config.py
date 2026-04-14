"""Temporary config stub for phase 1 imports."""

from __future__ import annotations

from pathlib import Path


class Settings:
    """Minimal settings used by phase 1 infrastructure modules."""

    kalshi_api_key: str
    kalshi_private_key_path: Path

    def __init__(
        self,
        kalshi_api_key: str = "",
        kalshi_private_key_path: Path = Path("./kalshi_key.pem"),
        kalshi_env: str = "demo",
    ) -> None:
        self.kalshi_api_key = kalshi_api_key
        self.kalshi_private_key_path = kalshi_private_key_path
        self.kalshi_env = kalshi_env

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
