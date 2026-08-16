"""Path helpers shared across the project."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from print_engineer.errors import ConfigError


def project_root() -> Path:
    """Absolute path to the project root (parent of ``src/``)."""
    return Path(__file__).resolve().parents[3]


def resolve(root: Path, value: str | Path) -> Path:
    """Resolve *value* against *root* unless it is already absolute."""
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path


def ensure_dirs(paths: Iterable[str | Path]) -> None:
    """Create each directory, raising ConfigError on failure."""
    for path in paths:
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(f"Could not create directory {path}: {exc}") from exc
