"""File-based control channel between the dashboard process and the bot process.

Dashboard runs as a separate FastAPI process and cannot call into the bot's
in-memory state directly. Admin actions (reset risk state, change a setting)
are queued to a JSON file; the main bot loop drains the queue on each tick
and applies the operations.

The queue file contains a list of request objects; each has a `type` and
`payload`. Writes are atomic via rename().
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONTROL_QUEUE_PATH = Path("control_queue.json")
KILL_SWITCH_PATH = Path("KILL_SWITCH")


def _read_queue() -> list[dict[str, Any]]:
    if not CONTROL_QUEUE_PATH.exists():
        return []
    try:
        data = json.loads(CONTROL_QUEUE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
    except (json.JSONDecodeError, OSError):
        return []
    return []


def _write_queue(queue: list[dict[str, Any]]) -> None:
    tmp = CONTROL_QUEUE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(queue), encoding="utf-8")
    os.replace(tmp, CONTROL_QUEUE_PATH)


def enqueue(request_type: str, payload: dict[str, Any]) -> str:
    """Append a request to the queue. Returns the request id."""
    req_id = uuid.uuid4().hex
    request = {
        "id": req_id,
        "type": request_type,
        "payload": payload,
        "ts": time.time(),
    }
    queue = _read_queue()
    queue.append(request)
    _write_queue(queue)
    return req_id


def drain() -> list[dict[str, Any]]:
    """Read and clear all pending requests. Safe to call when file missing."""
    queue = _read_queue()
    if not queue:
        return []
    try:
        CONTROL_QUEUE_PATH.unlink()
    except FileNotFoundError:
        pass
    return queue


def kill_switch_active() -> bool:
    return KILL_SWITCH_PATH.exists()


def activate_kill_switch() -> None:
    KILL_SWITCH_PATH.touch()


def deactivate_kill_switch() -> bool:
    """Remove KILL_SWITCH. Returns True if it existed."""
    try:
        KILL_SWITCH_PATH.unlink()
        return True
    except FileNotFoundError:
        return False
