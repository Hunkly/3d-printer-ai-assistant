"""``print.recommend`` MCP tool: AI-assisted print recommendations.

Phase 3A. Read-only: never writes slicer profiles, never modifies the input
model, and never touches the printer. Recommendations are suggestions only.

Returns ``{"ok": true, ...}`` on success and
``{"ok": false, "error": {code, message, details}}`` on structured failure.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from print_engineer.adapters.llm.ollama import build_llm_client
from print_engineer.core.recommendation import (
    PrintContextIntent,
    RecommendationGoal,
    RecommendationRequest,
    SetupRequest,
)
from print_engineer.errors import LLMError, SlicerError
from print_engineer.recommendation.context import PrintContextResolver
from print_engineer.recommendation.engine import RecommendationEngine
from print_engineer.recommendation.filament import FilamentMatrixBuilder
from print_engineer.recommendation.setup import SetupEngine


class RecommendTools:
    """Bound MCP tool implementations for one settings object."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    def recommend(
        self,
        model: str,
        process_profile: str | None = None,
        filament_profile: str | None = None,
        printer_profile: str | None = None,
        goal: str | None = None,
        overhang_threshold_degrees: float | None = None,
        max_time_minutes: float | None = None,
        max_filament_g: float | None = None,
        slicer_kind: str | None = None,
        slice_on_demand: bool = False,
    ) -> dict[str, Any]:
        try:
            request = RecommendationRequest(
                model_path=Path(model),
                slicer_kind=slicer_kind or self._settings.recommend.default_slicer,
                process_profile=process_profile,
                filament_profile=filament_profile,
                printer_profile=printer_profile,
                goal=cast(RecommendationGoal, goal or self._settings.recommend.default_goal),
                overhang_threshold_degrees=(
                    overhang_threshold_degrees
                    if overhang_threshold_degrees is not None
                    else self._settings.analysis.default_overhang_threshold_degrees
                ),
                max_time_minutes=max_time_minutes,
                max_filament_g=max_filament_g,
                slice_on_demand=slice_on_demand,
            )
            llm = build_llm_client(self._settings.llm)
            engine = RecommendationEngine(self._settings, llm=llm)
            result = engine.recommend(request)
        except (SlicerError, LLMError, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                exc = SlicerError(
                    "invalid recommendation request",
                    details={"validation_errors": str(exc)[:500]},
                )
            return {"ok": False, "error": exc.to_dict()}
        return {"ok": True, "recommendations": result.model_dump(mode="json")}

    def filament_candidates(
        self,
        slicer_kind: str | None = None,
        printer: str | None = None,
        nozzle_diameter_mm: float | None = None,
        build_plate: str | None = None,
        goal: str | None = None,
        vendor: str | None = None,
        material: str | None = None,
        use_defaults: bool = False,
    ) -> dict[str, Any]:
        """Enumerate and rank local filament profiles (read-only, never slices)."""
        try:
            intent = PrintContextIntent(
                slicer_kind=slicer_kind or self._settings.recommend.default_slicer,
                printer=printer,
                nozzle_diameter_mm=nozzle_diameter_mm,
                build_plate=build_plate,
                use_defaults=use_defaults,
            )
            resolver = PrintContextResolver(self._settings)
            resolved = resolver.resolve(intent)
            adapter = resolver.adapter(intent.slicer_kind)
            matrix = FilamentMatrixBuilder(self._settings, adapter).build(
                resolved,
                goal=cast(RecommendationGoal, goal or self._settings.recommend.default_goal),
                vendor=vendor,
                material=material,
            )
        except (SlicerError, LLMError, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                exc = SlicerError(
                    "invalid filament_candidates request",
                    details={"validation_errors": str(exc)[:500]},
                )
            return {"ok": False, "error": exc.to_dict()}
        return {"ok": True, "matrix": matrix.model_dump(mode="json")}

    def setup(
        self,
        printer: str,
        slicer_kind: str | None = None,
        nozzle_diameter_mm: float | None = None,
        build_plate: str | None = None,
        process_profile: str | None = None,
        filament_profile: str | None = None,
        goal: str | None = None,
        vendor: str | None = None,
        material: str | None = None,
        use_defaults: bool = False,
        use_llm: bool = True,
    ) -> dict[str, Any]:
        """Four-layer setup recommendation (read-only, never slices)."""
        try:
            request = SetupRequest(
                slicer_kind=slicer_kind or self._settings.recommend.default_slicer,
                printer=printer,
                nozzle_diameter_mm=nozzle_diameter_mm,
                build_plate=build_plate,
                process_profile=process_profile,
                filament_profile=filament_profile,
                use_defaults=use_defaults,
                goal=cast(RecommendationGoal, goal or self._settings.recommend.default_goal),
                vendor=vendor,
                material=material,
                use_llm=use_llm,
            )
            llm = build_llm_client(self._settings.llm) if use_llm else None
            engine = SetupEngine(self._settings, llm=llm)
            result = engine.recommend(request)
        except (SlicerError, LLMError, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                exc = SlicerError(
                    "invalid setup request",
                    details={"validation_errors": str(exc)[:500]},
                )
            return {"ok": False, "error": exc.to_dict()}
        return {"ok": True, "setup": result.model_dump(mode="json")}

    def filament_candidates(
        self,
        slicer_kind: str | None = None,
        printer: str | None = None,
        nozzle_diameter_mm: float | None = None,
        build_plate: str | None = None,
        goal: str | None = None,
        vendor: str | None = None,
        material: str | None = None,
        use_defaults: bool = False,
    ) -> dict[str, Any]:
        """Enumerate and rank local filament profiles (read-only, never slices)."""
        try:
            intent = PrintContextIntent(
                slicer_kind=slicer_kind or self._settings.recommend.default_slicer,
                printer=printer,
                nozzle_diameter_mm=nozzle_diameter_mm,
                build_plate=build_plate,
                use_defaults=use_defaults,
            )
            resolver = PrintContextResolver(self._settings)
            resolved = resolver.resolve(intent)
            adapter = resolver.adapter(intent.slicer_kind)
            matrix = FilamentMatrixBuilder(self._settings, adapter).build(
                resolved,
                goal=cast(RecommendationGoal, goal or self._settings.recommend.default_goal),
                vendor=vendor,
                material=material,
            )
        except (SlicerError, LLMError, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                exc = SlicerError(
                    "invalid filament_candidates request",
                    details={"validation_errors": str(exc)[:500]},
                )
            return {"ok": False, "error": exc.to_dict()}
        return {"ok": True, "matrix": matrix.model_dump(mode="json")}

    def setup(
        self,
        printer: str,
        slicer_kind: str | None = None,
        nozzle_diameter_mm: float | None = None,
        build_plate: str | None = None,
        process_profile: str | None = None,
        filament_profile: str | None = None,
        goal: str | None = None,
        vendor: str | None = None,
        material: str | None = None,
        use_defaults: bool = False,
        use_llm: bool = True,
    ) -> dict[str, Any]:
        """Four-layer setup recommendation (read-only, never slices)."""
        try:
            request = SetupRequest(
                slicer_kind=slicer_kind or self._settings.recommend.default_slicer,
                printer=printer,
                nozzle_diameter_mm=nozzle_diameter_mm,
                build_plate=build_plate,
                process_profile=process_profile,
                filament_profile=filament_profile,
                use_defaults=use_defaults,
                goal=cast(RecommendationGoal, goal or self._settings.recommend.default_goal),
                vendor=vendor,
                material=material,
                use_llm=use_llm,
            )
            llm = build_llm_client(self._settings.llm) if use_llm else None
            engine = SetupEngine(self._settings, llm=llm)
            result = engine.recommend(request)
        except (SlicerError, LLMError, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                exc = SlicerError(
                    "invalid setup request",
                    details={"validation_errors": str(exc)[:500]},
                )
            return {"ok": False, "error": exc.to_dict()}
        return {"ok": True, "setup": result.model_dump(mode="json")}


def build_tools(settings: Any) -> dict[str, Callable[..., dict[str, Any]]]:
    """Return the ``print.*`` tool callables bound to *settings*."""
    tools = RecommendTools(settings)
    return {
        "print.recommend": tools.recommend,
        "print.filament_candidates": tools.filament_candidates,
        "print.setup": tools.setup,
    }
