"""Tests for logging setup and the JSON audit formatter."""

from __future__ import annotations

import json
import logging
import logging.handlers
from pathlib import Path

from print_engineer.config import LoggingConfig
from print_engineer.logging_setup import setup_logging


def _fresh_config(tmp_path: Path) -> LoggingConfig:
    return LoggingConfig(
        log_dir=tmp_path / "logs",
        json_log_path=tmp_path / "logs" / "audit.jsonl",
    )


def test_setup_creates_dirs_and_file(tmp_path: Path) -> None:
    config = _fresh_config(tmp_path)
    logger = setup_logging(config)
    assert (tmp_path / "logs").is_dir()
    assert (tmp_path / "logs" / "audit.jsonl").is_file()
    assert any(
        isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers
    )
    assert any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.handlers.RotatingFileHandler)
        for h in logger.handlers
    )


def test_audit_line_is_valid_json(tmp_path: Path) -> None:
    config = _fresh_config(tmp_path)
    logger = setup_logging(config)
    logger.info(
        "tool %s -> %s",
        "system.health",
        "ok",
        extra={"tool": "system.health", "arguments": {}, "result": "ok"},
    )
    for handler in logger.handlers:
        handler.flush()
    line = (tmp_path / "logs" / "audit.jsonl").read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["msg"] == "tool system.health -> ok"
    assert payload["tool"] == "system.health"
    assert payload["arguments"] == {}
    assert payload["result"] == "ok"
    assert payload["level"] == "INFO"


def test_log_level_controls_console(tmp_path: Path) -> None:
    config = _fresh_config(tmp_path)
    logger = setup_logging(config, log_level="WARNING")
    console = next(
        h for h in logger.handlers if not isinstance(h, logging.handlers.RotatingFileHandler)
    )
    assert console.level == logging.WARNING
