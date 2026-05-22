"""Executor tests — focus on live-mode re-entry safety."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from kalshi_bot.config import Settings
from kalshi_bot.execution.executor import Executor, OrderState
from kalshi_bot.models.market import OrderBook, OrderBookLevel
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


def _book(yes_bid: str, no_bid: str) -> OrderBook:
    return OrderBook(
        ticker="KXBTC15M-TEST",
        yes_levels=[OrderBookLevel(price=Decimal(yes_bid), quantity=100)],
        no_levels=[OrderBookLevel(price=Decimal(no_bid), quantity=100)],
    )


class _StubClient:
    """Records every place_order call and returns a fake order_id."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self._next_id = 0
        self.fail_next: bool = False
        self.order_status: dict[str, dict[str, Any]] = {}

    async def place_order(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("simulated place_order failure")
        self._next_id += 1
        return {"order_id": f"oid-{self._next_id}"}

    async def get_order(self, order_id: str) -> dict[str, Any]:
        return self.order_status.get(order_id, {"status": "pending"})

    async def cancel_order(self, order_id: str) -> None:
        self.cancelled.append(order_id)


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
    submit_result = await executor.submit(sig, Decimal("100"))
    order = submit_result.order
    assert order is not None
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


@pytest.mark.asyncio
async def test_promote_to_taker_aborts_when_edge_gone(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    settings = _settings()
    risk = RiskManager(settings)
    client = _StubClient()
    executor = Executor(
        client,  # type: ignore[arg-type]
        risk,
        dry_run=False,
        db_path=str(tmp_path / "trades.db"),
        settings=settings,
    )

    sig = _signal(side=Side.YES).model_copy(
        update={
            "real_prob": 0.70,
            "route": "maker",
            "taker_price": Decimal("0.62"),
            "kalshi_price": Decimal("0.60"),
        }
    )
    submit_result = await executor.submit(sig, Decimal("100"))
    order = submit_result.order
    assert order is not None
    order.placed_at = time.monotonic() - (order.maker_timeout + 1)

    executor.attach_orderbook_source(
        lambda _ticker: (_book("0.55", "0.20"), datetime.now(timezone.utc))
    )

    await executor.promote_to_taker()

    assert len(client.calls) == 1
    assert order.state.value == "cancelled"
    assert any("skip_taker_promote_edge_gone" in rec.message for rec in caplog.records)
    await executor.close()


@pytest.mark.asyncio
async def test_promote_to_taker_uses_fresh_price(tmp_path: Path) -> None:
    settings = _settings()
    risk = RiskManager(settings)
    client = _StubClient()
    executor = Executor(
        client,  # type: ignore[arg-type]
        risk,
        dry_run=False,
        db_path=str(tmp_path / "trades.db"),
        settings=settings,
    )

    sig = _signal(side=Side.YES).model_copy(
        update={
            "real_prob": 0.72,
            "route": "maker",
            "taker_price": Decimal("0.62"),
            "kalshi_price": Decimal("0.60"),
        }
    )
    submit_result = await executor.submit(sig, Decimal("100"))
    order = submit_result.order
    assert order is not None
    order.placed_at = time.monotonic() - (order.maker_timeout + 1)

    fresh_price = Decimal("0.64")
    executor.attach_orderbook_source(
        lambda _ticker: (
            _book("0.40", str(Decimal("1") - fresh_price)),
            datetime.now(timezone.utc),
        )
    )

    await executor.promote_to_taker()

    assert len(client.calls) == 2
    # _fresh_taker_price now adds 3c slippage buffer
    expected_price = fresh_price + Decimal("0.03")
    assert Decimal(str(client.calls[-1]["price_dollars"])) == expected_price
    promoted_orders = [o for o in executor.pending_orders if o.route == "taker_promoted"]
    assert len(promoted_orders) == 1
    assert promoted_orders[0].price == expected_price
    await executor.close()


@pytest.mark.asyncio
async def test_promote_to_taker_skips_when_no_orderbook(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    settings = _settings()
    risk = RiskManager(settings)
    client = _StubClient()
    executor = Executor(
        client,  # type: ignore[arg-type]
        risk,
        dry_run=False,
        db_path=str(tmp_path / "trades.db"),
        settings=settings,
    )

    sig = _signal(side=Side.YES).model_copy(
        update={
            "real_prob": 0.70,
            "route": "maker",
            "taker_price": Decimal("0.62"),
            "kalshi_price": Decimal("0.60"),
        }
    )
    submit_result = await executor.submit(sig, Decimal("100"))
    order = submit_result.order
    assert order is not None
    order.placed_at = time.monotonic() - (order.maker_timeout + 1)

    executor.attach_orderbook_source(lambda _ticker: None)

    await executor.promote_to_taker()

    assert len(client.calls) == 1
    assert order.state.value == "cancelled"
    assert any("skip_taker_promote_no_orderbook" in rec.message for rec in caplog.records)
    await executor.close()


# ---------------------------------------------------------------------------
# Bug 1: EXITING state and exit sell tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_position_live_transitions_to_exiting(tmp_path: Path) -> None:
    """Live exit_position must set EXITING, not CANCELLED, and NOT record settlement."""
    settings = _settings()
    risk = RiskManager(settings)
    client = _StubClient()
    executor = Executor(
        client,  # type: ignore[arg-type]
        risk,
        dry_run=False,
        db_path=str(tmp_path / "trades.db"),
        settings=settings,
    )

    sig = _signal()
    submit_result = await executor.submit(sig, Decimal("100"))
    order = submit_result.order
    assert order is not None
    order.state = OrderState.FILLED
    order.fill_time = time.monotonic()

    exited, events = await executor.exit_position(order, Decimal("0.60"))
    assert exited is True
    assert order.state == OrderState.EXITING
    assert order.exit_order_id is not None
    assert order.exit_price == Decimal("0.59")  # 0.60 - 0.01 EXIT_SELL_BUFFER
    # PnL should NOT be finalized yet
    assert order.pnl is None
    # Ticker should still be in active_tickers (EXITING is active)
    assert sig.ticker in executor.active_tickers
    await executor.close()


@pytest.mark.asyncio
async def test_exit_position_paper_finalizes_immediately(tmp_path: Path) -> None:
    """Paper (dry_run) exit must still finalize immediately — no EXITING state."""
    settings = _settings()
    risk = RiskManager(settings)
    client = _StubClient()
    executor = Executor(
        client,  # type: ignore[arg-type]
        risk,
        dry_run=True,
        db_path=str(tmp_path / "trades.db"),
        settings=settings,
    )

    sig = _signal()
    submit_result = await executor.submit(sig, Decimal("100"))
    order = submit_result.order
    assert order is not None
    assert order.state == OrderState.FILLED  # paper fills immediately

    exited, _events = await executor.exit_position(order, Decimal("0.60"))
    assert exited is True
    assert order.state == OrderState.CANCELLED  # finalized, not EXITING
    assert order.pnl is not None
    await executor.close()


@pytest.mark.asyncio
async def test_check_pending_fills_confirms_exit_sell(tmp_path: Path) -> None:
    """When the exit sell fills, check_pending_fills must finalize PnL."""
    settings = _settings()
    risk = RiskManager(settings)
    client = _StubClient()
    executor = Executor(
        client,  # type: ignore[arg-type]
        risk,
        dry_run=False,
        db_path=str(tmp_path / "trades.db"),
        settings=settings,
    )

    sig = _signal()
    submit_result = await executor.submit(sig, Decimal("100"))
    order = submit_result.order
    assert order is not None
    order.state = OrderState.FILLED
    order.fill_time = time.monotonic()

    # Place exit sell → transitions to EXITING
    exited, _ = await executor.exit_position(order, Decimal("0.60"))
    assert exited is True
    assert order.state == OrderState.EXITING
    sell_oid = order.exit_order_id
    assert sell_oid is not None

    # Stub the sell order as filled
    client.order_status[sell_oid] = {"status": "filled", "price": 0.60}

    await executor.check_pending_fills()

    assert order.state == OrderState.CANCELLED
    assert order.pnl is not None
    await executor.close()


@pytest.mark.asyncio
async def test_settlement_races_exiting_order(tmp_path: Path) -> None:
    """If the market settles while a sell is in-flight, settlement PnL wins."""
    settings = _settings()
    risk = RiskManager(settings)
    client = _StubClient()
    executor = Executor(
        client,  # type: ignore[arg-type]
        risk,
        dry_run=False,
        db_path=str(tmp_path / "trades.db"),
        settings=settings,
    )

    sig = _signal(side=Side.NO)
    submit_result = await executor.submit(sig, Decimal("100"))
    order = submit_result.order
    assert order is not None
    order.state = OrderState.FILLED
    order.fill_time = time.monotonic()

    # Place exit sell → EXITING
    exited, _ = await executor.exit_position(order, Decimal("0.60"))
    assert exited is True
    assert order.state == OrderState.EXITING

    # Market settles as "no" (we win) before the sell fills
    executor.record_settlement(sig.ticker, "no")
    assert order.state == OrderState.SETTLED
    assert order.pnl is not None
    assert order.pnl > 0  # won the bet
    await executor.close()


@pytest.mark.asyncio
async def test_exiting_excluded_from_filled_orders(tmp_path: Path) -> None:
    """EXITING orders must not appear in filled_orders (prevents double-exit)."""
    settings = _settings()
    risk = RiskManager(settings)
    client = _StubClient()
    executor = Executor(
        client,  # type: ignore[arg-type]
        risk,
        dry_run=False,
        db_path=str(tmp_path / "trades.db"),
        settings=settings,
    )

    sig = _signal()
    submit_result = await executor.submit(sig, Decimal("100"))
    order = submit_result.order
    assert order is not None
    order.state = OrderState.FILLED
    order.fill_time = time.monotonic()
    assert order in executor.filled_orders

    await executor.exit_position(order, Decimal("0.60"))
    assert order.state == OrderState.EXITING
    assert order not in executor.filled_orders
    await executor.close()


@pytest.mark.asyncio
async def test_exiting_in_active_tickers(tmp_path: Path) -> None:
    """EXITING orders must keep their ticker in active_tickers."""
    settings = _settings()
    risk = RiskManager(settings)
    client = _StubClient()
    executor = Executor(
        client,  # type: ignore[arg-type]
        risk,
        dry_run=False,
        db_path=str(tmp_path / "trades.db"),
        settings=settings,
    )

    sig = _signal()
    submit_result = await executor.submit(sig, Decimal("100"))
    order = submit_result.order
    assert order is not None
    order.state = OrderState.FILLED
    order.fill_time = time.monotonic()

    await executor.exit_position(order, Decimal("0.60"))
    assert sig.ticker in executor.active_tickers
    await executor.close()


# ---------------------------------------------------------------------------
# Bug 3: Orphan reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_orphans_cleans_old_trades(tmp_path: Path) -> None:
    """Trades with NULL pnl older than 30 min are marked as orphans on startup."""
    import sqlite3

    db_path = str(tmp_path / "trades.db")
    # Pre-create the DB and insert an old orphaned trade
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL, order_id TEXT NOT NULL,
            ticker TEXT NOT NULL, symbol TEXT NOT NULL,
            strategy TEXT NOT NULL, side TEXT NOT NULL,
            contracts INTEGER NOT NULL, price TEXT NOT NULL,
            edge TEXT NOT NULL, net_edge TEXT NOT NULL,
            pnl TEXT, fees TEXT, route TEXT DEFAULT 'taker',
            exit_reason TEXT)"""
    )
    conn.execute(
        """INSERT INTO trades (timestamp, order_id, ticker, symbol, strategy,
           side, contracts, price, edge, net_edge, pnl, fees)
           VALUES (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-2 hours'), 'old-orphan', 'KXBTC-OLD',
                   'BTC', 'momentum', 'no', 5, '0.50', '0.1', '0.08', NULL, NULL)"""
    )
    conn.commit()
    conn.close()

    settings = _settings()
    risk = RiskManager(settings)
    client = _StubClient()
    executor = Executor(
        client,  # type: ignore[arg-type]
        risk,
        dry_run=True,
        db_path=db_path,
        settings=settings,
    )

    row = executor._db.execute(
        "SELECT pnl, exit_reason FROM trades WHERE order_id = 'old-orphan'"
    ).fetchone()
    assert row is not None
    assert row[0] == "0"
    assert row[1] == "orphan_reconciled"
    await executor.close()


@pytest.mark.asyncio
async def test_reconcile_orphans_preserves_recent(tmp_path: Path) -> None:
    """Trades with NULL pnl newer than 30 min are NOT touched."""
    import sqlite3

    db_path = str(tmp_path / "trades.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL, order_id TEXT NOT NULL,
            ticker TEXT NOT NULL, symbol TEXT NOT NULL,
            strategy TEXT NOT NULL, side TEXT NOT NULL,
            contracts INTEGER NOT NULL, price TEXT NOT NULL,
            edge TEXT NOT NULL, net_edge TEXT NOT NULL,
            pnl TEXT, fees TEXT, route TEXT DEFAULT 'taker',
            exit_reason TEXT)"""
    )
    conn.execute(
        """INSERT INTO trades (timestamp, order_id, ticker, symbol, strategy,
           side, contracts, price, edge, net_edge, pnl, fees)
           VALUES (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-5 minutes'), 'recent-trade', 'KXBTC-NEW',
                   'BTC', 'momentum', 'no', 5, '0.50', '0.1', '0.08', NULL, NULL)"""
    )
    conn.commit()
    conn.close()

    settings = _settings()
    risk = RiskManager(settings)
    client = _StubClient()
    executor = Executor(
        client,  # type: ignore[arg-type]
        risk,
        dry_run=True,
        db_path=db_path,
        settings=settings,
    )

    row = executor._db.execute(
        "SELECT pnl FROM trades WHERE order_id = 'recent-trade'"
    ).fetchone()
    assert row is not None
    assert row[0] is None  # still NULL — not touched
    await executor.close()
