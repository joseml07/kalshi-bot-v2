"""Shared bot-control helpers used by Discord, Telegram, and the dashboard.

Exposes a single `mutate_setting()` entry point so every surface
(chat bots, HTTP) validates runtime config mutations identically.
"""

from __future__ import annotations

from typing import Any

# Keys the user may change at runtime: alias -> (Settings attribute, cast)
_SETTABLE: dict[str, tuple[str, type]] = {
    "edge_threshold": ("edge_threshold", float),
    "exit_stop_loss": ("exit_stop_loss", float),
    "min_time": ("momentum_min_time", int),
    "max_time": ("momentum_max_time", int),
    "logistic_k": ("logistic_k", float),
    "symbols": ("symbols", str),
    "min_price": ("min_trade_price", float),
    "max_price": ("max_trade_price", float),
    "maker_first": ("maker_first", bool),
    "maker_fill_horizon_s": ("maker_fill_horizon_s", int),
    "kelly_fraction": ("kelly_fraction", float),
    "daily_loss_limit": ("daily_loss_limit", float),
    "max_concurrent_positions": ("max_concurrent_positions", int),
    "paper_balance": ("paper_balance", float),
    "yes_side_disabled": ("yes_side_disabled", bool),
}


class SettingError(ValueError):
    """Raised when a setting mutation is rejected."""


def settable_keys() -> list[str]:
    """Return the list of runtime-mutable setting aliases."""
    return sorted(_SETTABLE.keys())


def current_settings(settings: Any) -> dict[str, Any]:
    """Snapshot of the current values for every mutable setting."""
    snapshot: dict[str, Any] = {}
    for alias, (attr, _) in _SETTABLE.items():
        snapshot[alias] = getattr(settings, attr, None)
    return snapshot


def _coerce(cast: type, raw_value: Any) -> Any:
    if cast is bool:
        if isinstance(raw_value, bool):
            return raw_value
        return str(raw_value).lower() in ("true", "1", "yes", "on")
    try:
        return cast(raw_value)
    except (TypeError, ValueError) as exc:
        raise SettingError(f"Invalid value '{raw_value}'") from exc


def _validate(alias: str, value: Any) -> None:
    if alias == "kelly_fraction":
        if not (0.01 <= float(value) <= 1.0):
            raise SettingError("kelly_fraction must be between 0.01 and 1.0")
    elif alias == "edge_threshold":
        if not (0.0 <= float(value) <= 0.5):
            raise SettingError("edge_threshold must be between 0.0 and 0.5")
    elif alias == "daily_loss_limit":
        if float(value) < 0:
            raise SettingError("daily_loss_limit must be non-negative")
    elif alias == "max_concurrent_positions":
        if int(value) < 0:
            raise SettingError("max_concurrent_positions must be non-negative")
    elif alias == "paper_balance":
        if float(value) < 0:
            raise SettingError("paper_balance must be non-negative")
    elif alias in ("min_price", "max_price"):
        if not (0.0 < float(value) < 1.0):
            raise SettingError(f"{alias} must be between 0.0 and 1.0")
    elif alias == "exit_stop_loss":
        if not (0.0 <= float(value) <= 1.0):
            raise SettingError("exit_stop_loss must be between 0.0 and 1.0")


def mutate_setting(settings: Any, key: str, raw_value: Any) -> tuple[str, Any]:
    """Validate + apply a runtime setting change.

    Returns `(alias, new_value)`. Raises `SettingError` on unknown keys or
    invalid values. Caller is responsible for persisting if they want the
    change to survive a restart — this only mutates the in-memory Settings.
    """
    alias = key.lower()
    entry = _SETTABLE.get(alias)
    if entry is None:
        raise SettingError(
            f"Unknown key '{key}'. Allowed: {', '.join(settable_keys())}"
        )
    attr, cast = entry
    value = _coerce(cast, raw_value)
    _validate(alias, value)
    object.__setattr__(settings, attr, value)
    return alias, value
