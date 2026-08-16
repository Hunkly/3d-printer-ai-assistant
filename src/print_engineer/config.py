"""Settings loading: YAML base config + .env secrets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from print_engineer.errors import ConfigError
from print_engineer.utils.paths import project_root, resolve


class AppConfig(BaseModel):
    name: str = "print-engineer"
    version: str = "0.1.0"
    log_level: str = "INFO"


class MCPConfig(BaseModel):
    server_name: str = "print-engineer-mcp"
    host: str = "127.0.0.1"
    port: int = 8765


class StorageConfig(BaseModel):
    root: Path = Path("runtime/data")
    db_dir: Path = Path("runtime/data/db")
    workspace_dir: Path = Path("runtime/data/workspace")


class LoggingConfig(BaseModel):
    log_dir: Path = Path("runtime/logs")
    json_log_path: Path = Path("runtime/logs/audit.jsonl")
    max_bytes: int = 1_048_576
    backup_count: int = 5


class SlicerConfig(BaseModel):
    executable: str | None = None
    kind: str | None = None
    timeout_seconds: float = 600.0
    orca_install_path: str | None = None
    bambu_install_path: str | None = None


class PrinterConfig(BaseModel):
    host: str | None = None
    serial: str | None = None


class AnalysisConfig(BaseModel):
    default_overhang_threshold_degrees: float = 45.0


class LLMConfig(BaseModel):
    enabled: bool = True
    provider: str = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3-coder:30b"
    timeout_seconds: float = 120.0
    temperature: float = 0.2


class RecommendConfig(BaseModel):
    default_goal: str = "balanced"
    default_slicer: str = "orca_slicer"
    overhang_percent_threshold: float = 10.0
    thin_wall_min_ratio: float = 2.5
    allow_deterministic_fallback: bool = True
    slice_timeout_seconds: float = 600.0
    default_printer: str | None = None
    default_nozzle_diameter: float | None = Field(default=None, ge=0.05, le=2.0)
    default_build_plate: str | None = None


class BambuSecrets(BaseSettings):
    """Secrets loaded from ``.env`` (``BAMBU_*`` prefix). Never persisted to YAML."""

    model_config = SettingsConfigDict(
        env_prefix="BAMBU_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ip: str | None = None
    serial: str | None = None
    access_code: str | None = None


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Could not load config {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config {path} must contain a YAML mapping")
    return data


class Settings(BaseModel):
    """Combined application settings.

    Storage/logging paths are rebased against ``root`` on construction so the
    same YAML works from any working directory.
    """

    app: AppConfig = Field(default_factory=AppConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    slicer: SlicerConfig = Field(default_factory=SlicerConfig)
    printer: PrinterConfig = Field(default_factory=PrinterConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    recommend: RecommendConfig = Field(default_factory=RecommendConfig)

    root: Path = Field(default_factory=project_root)
    secrets: BambuSecrets = Field(default_factory=BambuSecrets)

    def model_post_init(self, __context: Any) -> None:
        self._rebase()

    def _rebase(self) -> None:
        self.storage.root = resolve(self.root, self.storage.root)
        self.storage.db_dir = resolve(self.root, self.storage.db_dir)
        self.storage.workspace_dir = resolve(self.root, self.storage.workspace_dir)
        self.logging.log_dir = resolve(self.root, self.logging.log_dir)
        self.logging.json_log_path = resolve(self.root, self.logging.json_log_path)

    @classmethod
    def load(
        cls, config_path: str | Path | None = None, root: str | Path | None = None
    ) -> Settings:
        """Build settings from an optional YAML file and optional root override."""
        data: dict[str, Any] = {}
        if config_path is not None:
            data = _load_yaml(Path(config_path))
        if root is not None:
            data["root"] = Path(root)
        return cls.model_validate(data)
