"""Tests for KalshiOrderbookFeed parsing and resiliency."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from kalshi_bot.client.kalshi_ws import KalshiOrderbookFeed


TICKER = "KXBTC15M-TEST"


def _make_feed() -> KalshiOrderbookFeed:
    settings = MagicMock()
    settings.kalshi_private_key_path = MagicMock()
    settings.kalshi_api_key = "test-key"
    settings.ws_base_url = "wss://example.com/ws"
    with patch(
        "kalshi_bot.client.kalshi_ws.load_private_key", return_value=MagicMock()
    ):
        return KalshiOrderbookFeed(settings)


def test_apply_snapshot_supports_fp_keys() -> None:
    feed = _make_feed()
    feed._apply_snapshot(
        {
            "market_ticker": TICKER,
            "yes_dollars_fp": [["0.45", "100.00"]],
            "no_dollars_fp": [["0.55", "200.00"]],
        }
    )

    result = feed.get_orderbook(TICKER)
    assert result is not None
    orderbook, _ = result
    assert orderbook.best_yes_bid == Decimal("0.45")
    assert orderbook.best_no_bid == Decimal("0.55")


def test_apply_delta_supports_price_dollars_and_delta_fp() -> None:
    feed = _make_feed()
    feed._apply_snapshot({"market_ticker": TICKER, "yes": [["45", "100"]], "no": []})

    feed._apply_delta(
        {
            "market_ticker": TICKER,
            "side": "yes",
            "price_dollars": "0.45",
            "delta_fp": "-10.00",
        }
    )

    orderbook, _ = feed.get_orderbook(TICKER)  # type: ignore[misc]
    assert orderbook.yes_levels[0].quantity == 90


def test_apply_delta_invalid_side_does_not_mutate_book() -> None:
    feed = _make_feed()
    feed._apply_snapshot({"market_ticker": TICKER, "yes": [["45", "100"]], "no": []})

    feed._apply_delta(
        {
            "market_ticker": TICKER,
            "side": "maybe",
            "price_dollars": "0.45",
            "delta_fp": "-10.00",
        }
    )

    orderbook, _ = feed.get_orderbook(TICKER)  # type: ignore[misc]
    assert orderbook.yes_levels[0].quantity == 100


def test_negative_qty_is_clamped_silently() -> None:
    """Negative qty should clamp to zero without triggering a ticker resync.

    Kalshi occasionally over-subtracts — it's a transient feed artifact, not
    structural corruption, and treating it as grounds for resubscription
    produces feedback loops (production logged 51k+ negative_qty events and
    3,650 resyncs in 13 hours).
    """
    feed = _make_feed()
    feed._apply_snapshot({"market_ticker": TICKER, "yes": [["45", "100"]], "no": []})
    feed._resubscribe_event.clear()

    for _ in range(30):
        feed._apply_delta(
            {
                "market_ticker": TICKER,
                "side": "yes",
                "price_dollars": "0.45",
                "delta_fp": "-1000.00",
            }
        )

    diag = feed.diagnostics()
    # Level is dropped from the book but the ticker is still tracked.
    assert diag["negative_qty"] >= 1
    assert diag["resync_ticker"] == 0
    assert feed.get_orderbook(TICKER) is not None
    assert not feed._resubscribe_event.is_set()


def test_anomaly_threshold_triggers_ticker_resync() -> None:
    """Structural anomalies (bad side) still trigger a ticker resync."""
    feed = _make_feed()
    feed._apply_snapshot({"market_ticker": TICKER, "yes": [["45", "100"]], "no": []})
    feed._resubscribe_event.clear()

    # 25 bad_side deltas cross the per-ticker threshold.
    for _ in range(25):
        feed._apply_delta(
            {
                "market_ticker": TICKER,
                "side": "bogus",
                "price_dollars": "0.45",
                "delta_fp": "-1.00",
            }
        )

    assert feed.get_orderbook(TICKER) is None
    assert feed._resubscribe_event.is_set()
    assert TICKER in feed._awaiting_snapshot


def test_full_resync_on_anomaly_burst_across_tickers() -> None:
    """Cumulative per-ticker structural anomalies should escalate to a full
    resync even if no single ticker crosses its own threshold.
    """
    feed = _make_feed()
    tickers = [f"KX{sym}15M-TEST" for sym in ("BTC", "ETH", "SOL", "XRP", "DOGE")]
    feed._tickers = set(tickers)
    for t in tickers:
        feed._apply_snapshot({"market_ticker": t, "yes": [["45", "1000"]], "no": []})

    # 10 bad-side deltas each on 5 tickers → sum of per-ticker counts = 50,
    # crossing the burst threshold (50). No single ticker hits threshold (25).
    for t in tickers:
        for _ in range(10):
            feed._apply_delta(
                {
                    "market_ticker": t,
                    "side": "bogus",
                    "price_dollars": "0.45",
                    "delta_fp": "-1.00",
                }
            )

    diag = feed.diagnostics()
    assert diag["resync_full"] >= 1
    assert diag["resync_ticker"] == 0


@pytest.mark.asyncio
async def test_sequence_gap_triggers_full_resync() -> None:
    feed = _make_feed()
    feed._apply_snapshot({"market_ticker": TICKER, "yes": [["45", "100"]], "no": []})

    feed._last_seq_by_sid[1] = 5
    await feed._handle_message(
        {
            "type": "orderbook_delta",
            "sid": 1,
            "seq": 8,
            "msg": {
                "market_ticker": TICKER,
                "side": "yes",
                "price_dollars": "0.45",
                "delta_fp": "1.00",
            },
        }
    )

    assert feed.get_orderbook(TICKER) is None
    assert feed._resubscribe_event.is_set()


def test_diagnostics_exposes_ws_health_counters() -> None:
    feed = _make_feed()
    feed._apply_snapshot({"market_ticker": TICKER, "yes": [["45", "100"]], "no": []})

    # Structural corruption (bad side) still triggers a ticker resync.
    for _ in range(25):
        feed._apply_delta(
            {
                "market_ticker": TICKER,
                "side": "bogus",
                "price_dollars": "0.45",
                "delta_fp": "-1.00",
            }
        )

    diag = feed.diagnostics()
    assert diag["messages_snapshot"] == 0
    assert diag["messages_delta"] == 0
    assert diag["delta_bad_side"] >= 1
    assert diag["resync_ticker"] >= 1
    assert diag["last_resync_ticker"] == TICKER


def test_resync_blocks_stale_deltas_until_fresh_snapshot() -> None:
    """After a ticker resync, deltas from the pre-resync stream must be
    dropped — not applied against a partially-rebuilt book — until a fresh
    snapshot lands and the barrier clears.
    """
    feed = _make_feed()
    feed._tickers = {TICKER}
    feed._apply_snapshot({"market_ticker": TICKER, "yes": [["45", "100"]], "no": []})
    feed._resubscribe_event.clear()

    # Simulate the resync directly (bypass the anomaly counter path).
    feed._schedule_ticker_resync(TICKER, "test")
    assert TICKER in feed._awaiting_snapshot
    assert feed.get_orderbook(TICKER) is None

    # A stale in-flight delta arrives after the book was cleared but before
    # the new snapshot lands. It must be dropped against the barrier.
    feed._apply_delta(
        {
            "market_ticker": TICKER,
            "side": "yes",
            "price_dollars": "0.45",
            "delta_fp": "-50.00",
        }
    )
    diag = feed.diagnostics()
    assert diag["delta_dropped_awaiting_snapshot"] == 1
    assert diag["negative_qty"] == 0
    assert diag["delta_before_snapshot"] == 0

    # Fresh snapshot for the resubscribed ticker lifts the barrier.
    feed._apply_snapshot({"market_ticker": TICKER, "yes": [["45", "80"]], "no": []})
    assert TICKER not in feed._awaiting_snapshot

    # Subsequent deltas apply normally against the fresh book.
    feed._apply_delta(
        {
            "market_ticker": TICKER,
            "side": "yes",
            "price_dollars": "0.45",
            "delta_fp": "-10.00",
        }
    )
    result = feed.get_orderbook(TICKER)
    assert result is not None
    orderbook, _ = result
    assert orderbook.yes_levels[0].quantity == 70
    assert feed.diagnostics()["negative_qty"] == 0
