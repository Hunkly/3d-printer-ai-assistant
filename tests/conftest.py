"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from print_engineer.config import Settings


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """A fake project root inside the test tmp dir."""
    return tmp_path / "project"


@pytest.fixture
def base_settings(tmp_root: Path) -> Settings:
    """Settings rooted at tmp_root with all defaults."""
    return Settings.load(root=tmp_root)


@pytest.fixture
def configured_settings(tmp_path: Path, tmp_root: Path) -> Settings:
    """Settings loaded from a YAML file overriding a few values."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.yaml"
    config_file.write_text("app:\n  name: test-engine\n  log_level: DEBUG\n", encoding="utf-8")
    return Settings.load(config_path=config_file, root=tmp_root)
