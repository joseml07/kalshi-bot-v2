"""Kalshi market data models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class Market(BaseModel):
    """A Kalshi market (single contract)."""

    ticker: str
    series_ticker: str
    title: str
    status: str
    open_time: datetime
    close_time: datetime
    yes_ask: Decimal | None = None
    yes_bid: Decimal | None = None
    no_ask: Decimal | None = None
    no_bid: Decimal | None = None
    volume: int = 0


class OrderBookLevel(BaseModel):
    """A single price level in the order book."""

    price: Decimal
    quantity: int


class OrderBook(BaseModel):
    """Order book snapshot for a market.

    yes_levels: resting YES side orders (bids to buy YES).
    no_levels: resting NO side orders (bids to buy NO).
    """

    ticker: str
    yes_levels: list[OrderBookLevel]
    no_levels: list[OrderBookLevel]

    @property
    def best_yes_bid(self) -> Decimal | None:
        """Best (highest) YES bid price."""
        if not self.yes_levels:
            return None
        return max(lv.price for lv in self.yes_levels)

    @property
    def best_yes_ask(self) -> Decimal | None:
        """Best YES ask = 1 - best NO bid (binary contract)."""
        if not self.no_levels:
            return None
        best_no_bid = max(lv.price for lv in self.no_levels)
        return Decimal("1") - best_no_bid

    @property
    def best_no_bid(self) -> Decimal | None:
        """Best (highest) NO bid price."""
        if not self.no_levels:
            return None
        return max(lv.price for lv in self.no_levels)

    @property
    def best_no_ask(self) -> Decimal | None:
        """Best NO ask = 1 - best YES bid (binary contract)."""
        if not self.yes_levels:
            return None
        return Decimal("1") - max(lv.price for lv in self.yes_levels)

    @property
    def orderbook_imbalance(self) -> float:
        """Raw OBI: yes_depth - no_depth. Positive = bullish."""
        yes_vol = sum(lv.quantity for lv in self.yes_levels)
        no_vol = sum(lv.quantity for lv in self.no_levels)
        return float(yes_vol - no_vol)

    @property
    def total_depth(self) -> int:
        """Total resting quantity across both sides."""
        return sum(lv.quantity for lv in self.yes_levels) + sum(
            lv.quantity for lv in self.no_levels
        )
