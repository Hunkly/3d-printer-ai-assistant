"""Logging: human console output on stderr + JSON audit log file.

MCP servers communicate with the host over stdio, so all log output must go to
stderr. Tool calls are additionally recorded as JSON lines in a rotating audit
file for offline review and (later) print history mining.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import UTC, datetime
from typing import Any

from print_engineer.config import LoggingConfig
from print_engineer.utils.paths import ensure_dirs


class JsonFormatter(logging.Formatter):
    """One JSON object per log line, including structured tool-call fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("tool", "arguments", "result"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, default=str)


def setup_logging(config: LoggingConfig, log_level: str = "INFO") -> logging.Logger:
    """Configure the ``print_engineer`` logger. Reconfigures on every call."""
    ensure_dirs([config.log_dir])

    logger = logging.getLogger("print_engineer")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(log_level.upper())
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(console)

    audit = logging.handlers.RotatingFileHandler(
        config.json_log_path,
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
        encoding="utf-8",
    )
    audit.setLevel(logging.DEBUG)
    audit.setFormatter(JsonFormatter())
    logger.addHandler(audit)

    return logger


def audit_tool_call(
    logger: logging.Logger, tool: str, arguments: dict[str, Any], result: Any
) -> None:
    """Record a tool invocation in the JSON audit log."""
    logger.info(
        "tool %s -> %s",
        tool,
        result,
        extra={"tool": tool, "arguments": arguments, "result": result},
    )
