"""Price tick data model."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PriceTick(BaseModel):
    """A single price update from the Coinbase WebSocket feed."""

    symbol: str
    price: float
    timestamp: datetime
