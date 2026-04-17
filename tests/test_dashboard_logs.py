"""Tests for /api/logs/tail and /api/logs/stats endpoints."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kalshi_bot import dashboard


@pytest.fixture
def log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "bot.log"
    monkeypatch.setattr(dashboard, "_BOT_LOG_PATH", path)
    return path


def _write_lines(path: Path, entries: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _iso(year: int, month: int, day: int, hour: int, minute: int = 0) -> str:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).isoformat()


def test_tail_returns_most_recent_lines(log_file: Path) -> None:
    entries = [
        {"event": "A", "level": "info", "timestamp": _iso(2026, 4, 16, 10, i)}
        for i in range(10)
    ]
    _write_lines(log_file, entries)
    client = TestClient(dashboard.app)
    r = client.get("/api/logs/tail", params={"n": 3})
    assert r.status_code == 200
    data = r.json()
    assert data["returned"] == 3
    assert [e["timestamp"] for e in data["lines"]] == [
        _iso(2026, 4, 16, 10, 7),
        _iso(2026, 4, 16, 10, 8),
        _iso(2026, 4, 16, 10, 9),
    ]


def test_tail_filters_by_event_substring(log_file: Path) -> None:
    entries = [
        {
            "event": "HEALTH mode=live",
            "level": "info",
            "timestamp": _iso(2026, 4, 16, 10, 0),
        },
        {
            "event": "kalshi_ws_negative_qty ticker=X",
            "level": "warning",
            "timestamp": _iso(2026, 4, 16, 10, 1),
        },
        {
            "event": "HEALTH mode=live",
            "level": "info",
            "timestamp": _iso(2026, 4, 16, 10, 2),
        },
        {"event": "Placed", "level": "info", "timestamp": _iso(2026, 4, 16, 10, 3)},
    ]
    _write_lines(log_file, entries)
    client = TestClient(dashboard.app)
    r = client.get("/api/logs/tail", params={"event": "HEALTH"})
    assert r.status_code == 200
    lines = r.json()["lines"]
    assert len(lines) == 2
    assert all("HEALTH" in e["event"] for e in lines)


def test_tail_event_filter_supports_comma_list(log_file: Path) -> None:
    entries = [
        {"event": "A", "level": "info", "timestamp": _iso(2026, 4, 16, 10, 0)},
        {"event": "B", "level": "info", "timestamp": _iso(2026, 4, 16, 10, 1)},
        {"event": "C", "level": "info", "timestamp": _iso(2026, 4, 16, 10, 2)},
    ]
    _write_lines(log_file, entries)
    client = TestClient(dashboard.app)
    r = client.get("/api/logs/tail", params={"event": "A,C"})
    events = [e["event"] for e in r.json()["lines"]]
    assert events == ["A", "C"]


def test_tail_filters_by_level(log_file: Path) -> None:
    entries = [
        {"event": "info_event", "level": "info", "timestamp": _iso(2026, 4, 16, 10, 0)},
        {
            "event": "warn_event",
            "level": "warning",
            "timestamp": _iso(2026, 4, 16, 10, 1),
        },
        {"event": "err_event", "level": "error", "timestamp": _iso(2026, 4, 16, 10, 2)},
    ]
    _write_lines(log_file, entries)
    client = TestClient(dashboard.app)
    r = client.get("/api/logs/tail", params={"level": "warning"})
    events = [e["event"] for e in r.json()["lines"]]
    assert events == ["warn_event", "err_event"]


def test_tail_filters_by_since(log_file: Path) -> None:
    entries = [
        {"event": "A", "level": "info", "timestamp": _iso(2026, 4, 16, 10, i)}
        for i in range(5)
    ]
    _write_lines(log_file, entries)
    client = TestClient(dashboard.app)
    cutoff = _iso(2026, 4, 16, 10, 3)
    r = client.get("/api/logs/tail", params={"since": cutoff})
    timestamps = [e["timestamp"] for e in r.json()["lines"]]
    assert timestamps == [_iso(2026, 4, 16, 10, 3), _iso(2026, 4, 16, 10, 4)]


def test_tail_n_capped_at_500(log_file: Path) -> None:
    entries = [
        {"event": "E", "level": "info", "timestamp": _iso(2026, 4, 16, 10, 0)}
        for _ in range(10)
    ]
    _write_lines(log_file, entries)
    client = TestClient(dashboard.app)
    r = client.get("/api/logs/tail", params={"n": 10000})
    assert r.status_code == 200
    assert r.json()["returned"] == 10


def test_tail_missing_file_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dashboard, "_BOT_LOG_PATH", tmp_path / "does_not_exist.log")
    client = TestClient(dashboard.app)
    r = client.get("/api/logs/tail")
    assert r.status_code == 200
    assert r.json() == {
        "lines": [],
        "scanned_bytes": 0,
        "truncated": False,
        "returned": 0,
    }


def test_tail_skips_non_json_lines(log_file: Path) -> None:
    with log_file.open("w", encoding="utf-8") as f:
        f.write("not json\n")
        f.write(
            json.dumps({"event": "ok", "timestamp": _iso(2026, 4, 16, 10, 0)}) + "\n"
        )
        f.write("also not json\n")
    client = TestClient(dashboard.app)
    r = client.get("/api/logs/tail")
    lines = r.json()["lines"]
    assert len(lines) == 1
    assert lines[0]["event"] == "ok"


def test_stats_histogram_groups_by_event_prefix(log_file: Path) -> None:
    entries = [
        {
            "event": "kalshi_ws_negative_qty ticker=X",
            "level": "warning",
            "timestamp": _iso(2026, 4, 16, 10, 0),
        },
        {
            "event": "kalshi_ws_negative_qty ticker=Y",
            "level": "warning",
            "timestamp": _iso(2026, 4, 16, 10, 1),
        },
        {
            "event": "HEALTH mode=live",
            "level": "info",
            "timestamp": _iso(2026, 4, 16, 10, 2),
        },
        {"event": "Placed", "level": "info", "timestamp": _iso(2026, 4, 16, 10, 3)},
    ]
    _write_lines(log_file, entries)
    client = TestClient(dashboard.app)
    r = client.get("/api/logs/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["events"]["kalshi_ws_negative_qty"] == 2
    assert data["events"]["HEALTH"] == 1
    assert data["events"]["Placed"] == 1
    assert data["window_spans_seconds"] == pytest.approx(180.0)


def test_stats_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard, "_BOT_LOG_PATH", tmp_path / "missing.log")
    client = TestClient(dashboard.app)
    r = client.get("/api/logs/stats")
    assert r.status_code == 200
    assert r.json()["events"] == {}


def test_why_not_trading_returns_configured_symbols_when_no_live_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard, "_read_live_state", lambda: {})

    class DummySettings:
        symbols = "BTC,ETH,SOL"

    monkeypatch.setattr(dashboard, "_settings", lambda: DummySettings())

    client = TestClient(dashboard.app)
    r = client.get("/api/why_not_trading")
    assert r.status_code == 200
    payload = r.json()
    assert payload["updated_at"] is None
    rows = payload["rows"]
    assert [row["symbol"] for row in rows] == ["BTC", "ETH", "SOL"]
    for row in rows:
        assert row["ticker"] is None
        assert row["last_eval"] == {"result": None, "at": None}
        assert row["likely_block"] is None


def test_why_not_trading_returns_empty_rows_when_no_symbols_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard, "_read_live_state", lambda: {})

    class DummySettings:
        symbols = ""

    monkeypatch.setattr(dashboard, "_settings", lambda: DummySettings())

    client = TestClient(dashboard.app)
    r = client.get("/api/why_not_trading")
    assert r.status_code == 200
    payload = r.json()
    assert payload["updated_at"] is None
    assert payload["rows"] == []
