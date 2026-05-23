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
    # Settlement-strike fields. Kalshi's KX{BTC,ETH,SOL}15M markets settle
    # on close-price >= floor_strike (when strike_type='greater_or_equal'),
    # NOT on close-vs-open. See explanation.md / Q4.
    floor_strike: Decimal | None = None
    cap_strike: Decimal | None = None
    strike_type: str | None = None
    expected_expiration_value: Decimal | None = None

    @property
    def settlement_strike(self) -> Decimal | None:
        """Return the price level Kalshi uses to determine settlement.

        For binary "above-target" markets (the 15-min crypto windows),
        Kalshi sets ``floor_strike`` to the threshold and ``strike_type``
        to ``"greater_or_equal"``. YES wins iff the close price >= strike.
        """
        if self.floor_strike is not None:
            return self.floor_strike
        if self.cap_strike is not None:
            return self.cap_strike
        return None


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
        """Normalized OBI: (yes_depth - no_depth) / total_depth. Range [-1, 1].

        Positive = more resting YES volume (bullish). Zero = balanced.
        """
        yes_vol = sum(lv.quantity for lv in self.yes_levels)
        no_vol = sum(lv.quantity for lv in self.no_levels)
        total = yes_vol + no_vol
        if total == 0:
            return 0.0
        return float(yes_vol - no_vol) / total

    @property
    def total_depth(self) -> int:
        """Total resting quantity across both sides."""
        return sum(lv.quantity for lv in self.yes_levels) + sum(
            lv.quantity for lv in self.no_levels
        )
