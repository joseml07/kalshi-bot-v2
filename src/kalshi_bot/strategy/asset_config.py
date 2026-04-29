"""Per-asset configuration and signal strength utilities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SignalStrength(str, Enum):
    """Signal strength classification for adaptive execution."""

    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


@dataclass(frozen=True)
class AssetConfig:
    """Tuned parameters for a specific crypto asset.

    These override the global defaults in ``Settings`` when the asset
    is actively traded.  Unspecified fields fall back to the global
    default so the bot degrades gracefully when a new symbol is added.
    """

    symbol: str
    edge_threshold: float | None = None
    min_trade_price: float | None = None
    max_trade_price: float | None = None
    maker_horizon_base_s: int | None = None
    taker_edge_floor: float | None = None
    momentum_min_time: int | None = None
    momentum_max_time: int | None = None
    lwm_min_price_change: float | None = None
    lwm_yes_only: bool | None = None
    lwm_no_side_edge_bonus: float | None = None
    sizing_multiplier: float = 1.0


# Tuned configs derived from live CSV analysis (Apr 7–29 2026).
# ETH: 8× the profit of BTC, higher volatility → lower edge floor,
#      wider price bounds, longer maker horizon.
# BTC: Lower volatility, tighter bounds, more conservative.
# SOL: Minimal data (5 trades), very conservative.
DEFAULT_ASSET_CONFIGS: dict[str, AssetConfig] = {
    "ETH": AssetConfig(
        symbol="ETH",
        edge_threshold=0.05,
        min_trade_price=0.30,
        max_trade_price=0.85,
        maker_horizon_base_s=100,
        taker_edge_floor=0.03,
        momentum_min_time=25,
        momentum_max_time=480,
        lwm_min_price_change=0.00025,
        lwm_yes_only=False,
        lwm_no_side_edge_bonus=0.03,
        sizing_multiplier=1.25,
    ),
    "BTC": AssetConfig(
        symbol="BTC",
        edge_threshold=0.06,
        min_trade_price=0.35,
        max_trade_price=0.80,
        maker_horizon_base_s=80,
        taker_edge_floor=0.04,
        momentum_min_time=30,
        momentum_max_time=480,
        lwm_min_price_change=0.0003,
        lwm_yes_only=True,
        lwm_no_side_edge_bonus=0.04,
        sizing_multiplier=1.0,
    ),
    "SOL": AssetConfig(
        symbol="SOL",
        edge_threshold=0.08,
        min_trade_price=0.40,
        max_trade_price=0.75,
        maker_horizon_base_s=60,
        taker_edge_floor=0.05,
        momentum_min_time=35,
        momentum_max_time=450,
        lwm_min_price_change=0.0004,
        lwm_yes_only=True,
        lwm_no_side_edge_bonus=0.05,
        sizing_multiplier=0.75,
    ),
}


def get_asset_config(symbol: str) -> AssetConfig | None:
    """Return the tuned config for *symbol*, or None if not configured."""
    return DEFAULT_ASSET_CONFIGS.get(symbol.upper())


def resolve_param(
    symbol: str,
    global_value: float | int | bool,
    param_name: str,
) -> float | int | bool:
    """Resolve a parameter using per-asset override if available.

    Args:
        symbol: The crypto symbol (e.g. "ETH", "BTC").
        global_value: The default from ``Settings``.
        param_name: Attribute name on ``AssetConfig`` to check.

    Returns:
        The per-asset value if configured, otherwise *global_value*.
    """
    cfg = get_asset_config(symbol)
    if cfg is None:
        return global_value
    override = getattr(cfg, param_name, None)
    return override if override is not None else global_value


def compute_signal_strength(
    net_edge: float,
    obi: float,
    seconds_remaining: int,
    total_depth: int,
) -> SignalStrength:
    """Classify signal strength for adaptive execution.

    Scoring (0–100):
    - Edge score: net_edge / 0.10 * 40  (max 40)
    - OBI score: min(|obi| / 500, 1) * 25  (max 25)
    - Time score: min(seconds_remaining / 300, 1) * 20  (max 20)
    - Depth score: min(total_depth / 1000, 1) * 15  (max 15)

    Thresholds:
    - STRONG: >= 65
    - MODERATE: >= 40
    - WEAK: < 40
    """
    edge_score = min(net_edge / 0.10, 1.0) * 40
    obi_score = min(abs(obi) / 500.0, 1.0) * 25
    time_score = min(seconds_remaining / 300.0, 1.0) * 20
    depth_score = min(total_depth / 1000.0, 1.0) * 15

    total = edge_score + obi_score + time_score + depth_score

    if total >= 65:
        return SignalStrength.STRONG
    if total >= 40:
        return SignalStrength.MODERATE
    return SignalStrength.WEAK


def maker_timeout_for_strength(
    strength: SignalStrength,
    symbol: str,
    global_horizon: int = 90,
) -> int:
    """Return the maker fill timeout in seconds based on signal strength.

    Strong signals get more time because the edge is durable.
    Weak signals get less time to avoid tying up capital.
    """
    base = resolve_param(symbol, global_horizon, "maker_horizon_base_s")
    if isinstance(base, bool):
        base = global_horizon
    base = int(base)

    if strength == SignalStrength.STRONG:
        return int(base * 1.33)  # +33% time
    if strength == SignalStrength.WEAK:
        return int(base * 0.67)  # -33% time
    return base
