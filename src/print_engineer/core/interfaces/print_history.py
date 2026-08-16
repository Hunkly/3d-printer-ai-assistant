"""Print history abstraction (past prints, ratings, recommendations)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from print_engineer.core.types import PrintRecord, Recommendation


class PrintHistory(ABC):
    """Records past prints and suggests settings (Phase 3+)."""

    @abstractmethod
    def record(self, record: PrintRecord) -> None:
        """Persist a finished print."""

    @abstractmethod
    def recent(self, limit: int = 20) -> list[PrintRecord]:
        """Return the most recent records, newest first."""

    @abstractmethod
    def recommend(self, model_path: Path) -> Recommendation | None:
        """Suggest settings for *model_path* based on history."""
