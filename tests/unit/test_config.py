"""Tests for settings loading and path rebasing."""

from __future__ import annotations

from pathlib import Path

import pytest

from print_engineer.config import Settings
from print_engineer.errors import ConfigError


def test_defaults_are_sane() -> None:
    settings = Settings()
    assert settings.app.name == "print-engineer"
    assert settings.app.version == "0.1.0"
    assert settings.mcp.server_name == "print-engineer-mcp"
    assert settings.slicer.executable is None
    assert settings.printer.host is None


def test_root_rebases_storage(base_settings: Settings, tmp_root: Path) -> None:
    assert base_settings.storage.root == tmp_root / "runtime" / "data"
    assert base_settings.storage.db_dir == tmp_root / "runtime" / "data" / "db"
    assert base_settings.storage.workspace_dir == tmp_root / "runtime" / "data" / "workspace"
    assert base_settings.logging.log_dir == tmp_root / "runtime" / "logs"
    assert base_settings.logging.json_log_path == tmp_root / "runtime" / "logs" / "audit.jsonl"


def test_all_paths_absolute(base_settings: Settings) -> None:
    for path in (
        base_settings.storage.root,
        base_settings.storage.db_dir,
        base_settings.storage.workspace_dir,
        base_settings.logging.log_dir,
        base_settings.logging.json_log_path,
    ):
        assert path.is_absolute()


def test_load_with_config_file(configured_settings: Settings) -> None:
    assert configured_settings.app.name == "test-engine"
    assert configured_settings.app.log_level == "DEBUG"


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        Settings.load(config_path=tmp_path / "nope.yaml")


def test_load_invalid_yaml_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("key: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError):
        Settings.load(config_path=bad)


def test_load_non_mapping_raises(tmp_path: Path) -> None:
    bad = tmp_path / "scalar.yaml"
    bad.write_text("just a string", encoding="utf-8")
    with pytest.raises(ConfigError):
        Settings.load(config_path=bad)


def test_bambu_secrets_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAMBU_IP", "192.168.0.10")
    monkeypatch.setenv("BAMBU_ACCESS_CODE", "1234abcd")
    settings = Settings()
    assert settings.secrets.ip == "192.168.0.10"
    assert settings.secrets.access_code == "1234abcd"
    assert settings.secrets.serial is None
