"""Executor tests — focus on live-mode re-entry safety."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from kalshi_bot.config import Settings
from kalshi_bot.execution.executor import Executor
from kalshi_bot.risk.manager import RiskManager, RiskVetoError
from kalshi_bot.strategy.signals import Side, Signal, StrategyName


def _settings() -> Settings:
    return Settings(
        kalshi_api_key="k",
        kalshi_private_key_path="./kalshi_key.pem",
        daily_loss_limit=25.0,
        max_concurrent_positions=2,
    )


def _signal(ticker: str = "KXBTC15M-TEST", side: Side = Side.NO) -> Signal:
    return Signal(
        timestamp=datetime.now(timezone.utc),
        strategy=StrategyName.MOMENTUM,
        ticker=ticker,
        symbol="BTC",
        side=side,
        edge=Decimal("0.10"),
        net_edge=Decimal("0.08"),
        kalshi_price=Decimal("0.53"),
        real_prob=0.3,
        seconds_remaining=120,
    )


class _StubClient:
    """Records every place_order call and returns a fake order_id."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._next_id = 0
        self.fail_next: bool = False

    async def place_order(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("simulated place_order failure")
        self._next_id += 1
        return {"order_id": f"oid-{self._next_id}"}


@pytest.mark.asyncio
async def test_live_submit_locks_side_before_network(tmp_path: Path) -> None:
    """Second submit on same ticker must be blocked — no duplicate Kalshi order."""
    settings = _settings()
    risk = RiskManager(settings)
    client = _StubClient()
    db = tmp_path / "trades.db"
    executor = Executor(
        client,  # type: ignore[arg-type]
        risk,
        dry_run=False,
        db_path=str(db),
        settings=settings,
    )

    sig = _signal()
    result = await executor.submit(sig, Decimal("100"))
    assert result is not None
    assert len(client.calls) == 1

    # Risk must veto any further signal on this ticker, regardless of side.
    with pytest.raises(RiskVetoError):
        risk.check(_signal(side=Side.YES))
    with pytest.raises(RiskVetoError):
        risk.check(_signal(side=Side.NO))

    await executor.close()


@pytest.mark.asyncio
async def test_live_submit_failure_releases_reservation(tmp_path: Path) -> None:
    """If place_order raises, the ticker must not stay locked."""
    settings = _settings()
    risk = RiskManager(settings)
    client = _StubClient()
    client.fail_next = True
    db = tmp_path / "trades.db"
    executor = Executor(
        client,  # type: ignore[arg-type]
        risk,
        dry_run=False,
        db_path=str(db),
        settings=settings,
    )

    sig = _signal()
    with pytest.raises(RuntimeError):
        await executor.submit(sig, Decimal("100"))

    # Reservation rolled back — a retry must pass the risk gate again.
    risk.check(sig)

    await executor.close()
