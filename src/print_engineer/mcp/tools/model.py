"""``model.*`` MCP tools: deterministic geometry analysis.

Phase 2 exposes ``model.analyze``. It is pure geometry analysis - no slicing,
no printer interaction, and no LLM reasoning.

All tools return ``{"ok": true, ...}`` on success and
``{"ok": false, "error": {code, message, details}}`` on structured failure.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from print_engineer.adapters.model.analyzer import TrimeshModelAnalyzer, model_analysis_to_dict
from print_engineer.errors import SlicerError


class ModelTools:
    """Bound MCP tool implementations for one settings object."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    def analyze_model(
        self, model: str, overhang_threshold_degrees: float | None = None
    ) -> dict[str, Any]:
        try:
            threshold = (
                overhang_threshold_degrees
                if overhang_threshold_degrees is not None
                else self._settings.analysis.default_overhang_threshold_degrees
            )
            analysis = TrimeshModelAnalyzer().analyze(Path(model), threshold)
        except SlicerError as exc:
            return {"ok": False, "error": exc.to_dict()}
        return {"ok": True, "analysis": model_analysis_to_dict(analysis)}


def build_tools(settings: Any) -> dict[str, Callable[..., dict[str, Any]]]:
    """Return the ``model.*`` tool callables bound to *settings*."""
    tools = ModelTools(settings)
    return {
        "model.analyze": tools.analyze_model,
    }
