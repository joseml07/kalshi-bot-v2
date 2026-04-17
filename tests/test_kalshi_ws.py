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


def test_anomaly_threshold_triggers_ticker_resync() -> None:
    feed = _make_feed()
    feed._apply_snapshot({"market_ticker": TICKER, "yes": [["45", "100"]], "no": []})

    for _ in range(5):
        feed._apply_delta(
            {
                "market_ticker": TICKER,
                "side": "yes",
                "price_dollars": "0.45",
                "delta_fp": "-1000.00",
            }
        )

    assert feed.get_orderbook(TICKER) is None
    assert feed._resubscribe_event.is_set()


def test_interleaved_bad_and_good_deltas_still_trigger_resync() -> None:
    """Regression: a good delta between bad ones must NOT erase the anomaly
    counter. In production the reset-on-good logic let thousands of
    negative_qty events accumulate without ever triggering a resync because
    valid deltas kept arriving concurrently and resetting the counter.
    """
    feed = _make_feed()
    feed._apply_snapshot(
        {"market_ticker": TICKER, "yes": [["45", "10000"]], "no": []}
    )
    feed._resubscribe_event.clear()

    # Alternate: bad, good, bad, good, ... for 10 bad and 10 good.
    for _ in range(10):
        feed._apply_delta(
            {
                "market_ticker": TICKER,
                "side": "yes",
                "price_dollars": "0.45",
                "delta_fp": "-999999.00",  # drives new_qty negative
            }
        )
        feed._apply_delta(
            {
                "market_ticker": TICKER,
                "side": "yes",
                "price_dollars": "0.46",
                "delta_fp": "1.00",  # valid, positive delta on a fresh level
            }
        )

    diag = feed.diagnostics()
    assert diag["resync_ticker"] >= 1, (
        "Interleaved good deltas must not keep resetting the anomaly counter"
    )


def test_full_resync_on_anomaly_burst_across_tickers() -> None:
    """If the whole feed is flaky, cumulative per-ticker anomalies should
    escalate to a full resync even if no single ticker crosses its threshold.
    """
    feed = _make_feed()
    tickers = [f"KX{sym}15M-TEST" for sym in ("BTC", "ETH", "SOL", "XRP", "DOGE")]
    for t in tickers:
        feed._apply_snapshot({"market_ticker": t, "yes": [["45", "1000"]], "no": []})

    # 2 bad deltas each on 5 tickers → sum of per-ticker counts = 10. No
    # single ticker hits threshold (3). The final delta (sum=10) crosses the
    # burst threshold and triggers a full resync of all books.
    for t in tickers:
        for _ in range(2):
            feed._apply_delta(
                {
                    "market_ticker": t,
                    "side": "yes",
                    "price_dollars": "0.45",
                    "delta_fp": "-999999.00",
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

    for _ in range(5):
        feed._apply_delta(
            {
                "market_ticker": TICKER,
                "side": "yes",
                "price_dollars": "0.45",
                "delta_fp": "-1000.00",
            }
        )

    diag = feed.diagnostics()
    assert diag["messages_snapshot"] == 0
    assert diag["messages_delta"] == 0
    assert diag["negative_qty"] >= 1
    assert diag["resync_ticker"] >= 1
    assert diag["last_resync_ticker"] == TICKER
