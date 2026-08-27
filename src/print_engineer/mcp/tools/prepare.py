"""Public print.prepare MCP tool."""
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any

from print_engineer.config import Settings
from print_engineer.core.preparation import NotReadyPreparationResult, ReadyPreparationResult
from print_engineer.core.preparation_service import PreparationService
from print_engineer.core.recommendation import RecommendationGoal


def _value(value: object) -> object:
    return value.value if isinstance(value, (StrEnum,)) else value


def _ready(result: ReadyPreparationResult) -> dict[str, Any]:
    setup = result.selected_setup
    facts = result.slice_result
    artifact = result.artifact
    return {
        "ok": True,
        "preparation": {
            "status": "READY",
            "goal": _value(result.identity.goal),
            "setup": {
                "slicer": _value(setup.slicer),
                "printer": {"name": setup.printer.name, "setting_id": setup.printer.setting_id},
                "process": {
                    "name": setup.process_profile.name,
                    "setting_id": setup.process_profile.setting_id,
                },
                "filament": {
                    "name": setup.filament_profile.name,
                    "setting_id": setup.filament_profile.setting_id,
                },
                "material": setup.material,
                "nozzle_diameter_mm": setup.nozzle_diameter_mm,
                "build_plate": setup.build_plate,
                "overrides": [
                    {"setting": item.setting, "value": item.value} for item in setup.overrides
                ],
            },
            "artifact": {
                "path": str(artifact.path),
                "slice_run_id": artifact.slice_run_id,
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
            },
            "slice": {
                "layer_count": facts.layer_count,
                "estimated_time_minutes": facts.estimated_time_minutes,
                "filament_used_mm": facts.filament_used_mm,
                "filament_used_cm3": facts.filament_used_cm3,
                "filament_weight_g": facts.filament_weight_g,
            },
            "slicer": {"kind": "orca_slicer", "name": "OrcaSlicer", "version": "2.3.2"},
            "verification": {"status": _value(result.verification.status)},
        },
    }


def _failure(result: NotReadyPreparationResult) -> dict[str, Any]:
    allowed = {"field", "profile_kind", "supported_values", "timeout_seconds"}
    details: dict[str, Any] = {"status": "NOT_READY", "stage": result.failure.stage.value}
    for item in result.failure.details:
        if item.key in allowed:
            details[item.key] = item.value
    return {
        "ok": False,
        "error": {
            "code": result.failure.code,
            "message": result.failure.message,
            "details": details,
        },
    }


class PrepareTools:
    def __init__(self, settings: Settings) -> None:
        self._service = PreparationService.from_settings(settings)

    def prepare(
        self,
        model: str,
        goal: RecommendationGoal,
        material: str | None = None,
        printer: str | None = None,
        build_plate: str | None = None,
        nozzle_diameter_mm: float | None = None,
    ) -> dict[str, Any]:
        result = self._service.prepare(
            model,
            goal,
            material=material,
            printer=printer,
            build_plate=build_plate,
            nozzle_diameter_mm=nozzle_diameter_mm,
        )
        return _ready(result) if isinstance(result, ReadyPreparationResult) else _failure(result)


def build_tools(settings: Settings) -> dict[str, Callable[..., dict[str, Any]]]:
    tools = PrepareTools(settings)
    return {"print.prepare": tools.prepare}
