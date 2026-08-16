"""Model analysis abstraction (STL / 3MF geometry)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from print_engineer.core.types import ModelAnalysis


class ModelAnalyzer(ABC):
    """Analyzes 3D model geometry (STL / 3MF) deterministically.

    The analyzer never modifies, repairs, or rotates the model and never calls
    a slicer or printer. Results are consumed by higher layers (AI reasoning)
    in later phases.
    """

    @abstractmethod
    def analyze(
        self, path: Path, overhang_threshold_degrees: float = 45.0
    ) -> ModelAnalysis:
        """Compute geometry metrics for the model at *path*.

        *overhang_threshold_degrees* is the overhang angle (from vertical,
        default 45 degrees) above which a downward-facing surface is flagged.
        """
