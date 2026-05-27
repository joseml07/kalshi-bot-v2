"""Structured JSON logging with daily file rotation."""

from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

# Standard LogRecord attributes — anything else is from extra={} and should be merged.
_STDLIB_RECORD_ATTRS: frozenset[str] = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "taskName", "thread", "threadName",
})


def _merge_stdlib_extra(
    logger: Any, method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Pull extra={} fields from stdlib LogRecords into the structlog event dict."""
    record = event_dict.get("_record")
    if record is not None:
        for key, value in record.__dict__.items():
            if key not in _STDLIB_RECORD_ATTRS and not key.startswith("_"):
                event_dict.setdefault(key, value)
    return event_dict


def setup_logging(log_dir: str = "logs", level: int = logging.INFO) -> None:
    """Configure structlog with JSON output and daily rotation.

    Console gets human-readable output. File gets JSON lines with daily rotation.
    """
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)

    # Shared structlog processors
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        _merge_stdlib_extra,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # Configure structlog
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Console handler — human-readable
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(),
            ],
        )
    )

    # File handler — JSON lines, daily rotation, keep 30 days
    file_handler = TimedRotatingFileHandler(
        filename=str(log_path / "bot.log"),
        when="midnight",
        interval=1,
        backupCount=30,
        utc=True,
    )
    file_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )

    # Root logger
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)
    root.setLevel(level)

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
