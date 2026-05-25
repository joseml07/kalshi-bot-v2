"""Trading signal data model."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel


from kalshi_bot.strategy.asset_config import SignalStrength


class Side(str, Enum):
    """Order side."""

    YES = "yes"
    NO = "no"


class StrategyName(str, Enum):
    """Which strategy produced the signal."""

    PRICE_LAG = "price_lag"
    CONSENSUS = "consensus"
    ORDERBOOK_IMBALANCE = "orderbook_imbalance"
    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"
    LWM = "lwm"


class Signal(BaseModel):
    """A trading signal produced by a strategy."""

    timestamp: datetime
    strategy: StrategyName
    ticker: str
    symbol: str
    side: Side
    edge: Decimal
    net_edge: Decimal
    kalshi_price: Decimal
    real_prob: float
    seconds_remaining: int
    contracts: int = 1
    route: str = "taker"
    taker_price: Decimal | None = None
    reason: str = ""
    signal_strength: SignalStrength = SignalStrength.MODERATE
