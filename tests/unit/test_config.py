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


def test_issue_metadata_paths_default_empty() -> None:
    assert Settings().printer.issue_metadata_paths == ()


def test_issue_metadata_empty_printer_mapping_loads_without_resource(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("printer: {}\n", encoding="utf-8")
    settings = Settings.load(config_path=config, root=tmp_path / "project-root")
    assert settings.printer.issue_metadata_paths == ()


def test_issue_metadata_paths_rebase_against_root(tmp_path: Path) -> None:
    settings = Settings.model_validate(
        {"root": tmp_path, "printer": {"issue_metadata_paths": ["metadata.json"]}}
    )
    assert settings.printer.issue_metadata_paths == (tmp_path / "metadata.json",)


def test_issue_metadata_absolute_path_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "metadata.json"
    settings = Settings.model_validate(
        {"root": tmp_path / "root", "printer": {"issue_metadata_paths": [path]}}
    )
    assert settings.printer.issue_metadata_paths == (path,)


def test_issue_metadata_yaml_relative_path_uses_explicit_root(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "printer:\n  issue_metadata_paths:\n    - metadata.json\n",
        encoding="utf-8",
    )
    root = tmp_path / "project-root"
    settings = Settings.load(config_path=config, root=root)
    assert settings.printer.issue_metadata_paths == (root / "metadata.json",)
def test_orca_appdata_path_rebase_and_legacy_default(tmp_path: Path) -> None:
    assert Settings(root=tmp_path).slicer.orca_appdata_path is None
    relative = Settings.model_validate(
        {"root": tmp_path, "slicer": {"orca_appdata_path": "profiles"}}
    )
    assert relative.slicer.orca_appdata_path == tmp_path / "profiles"
    absolute_path = tmp_path / "absolute-profiles"
    absolute = Settings.model_validate(
        {"root": tmp_path, "slicer": {"orca_appdata_path": absolute_path}}
    )
    assert absolute.slicer.orca_appdata_path == absolute_path
    legacy = Settings.model_validate(
        {"root": tmp_path, "slicer": {"orca_install_path": "orca.exe"}}
    )
    assert legacy.slicer.orca_install_path == "orca.exe"
