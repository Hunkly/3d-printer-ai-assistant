"""``slicer.*`` MCP tools: detection, validation, and slicing.

Phase 1 exposes ``slicer.list``, ``slicer.info``, ``slicer.validate`` and
``slicer.slice``. Printer / model-analysis / history / AI tools stay out of
scope until later phases.

All tools return ``{"ok": true, ...}`` on success and
``{"ok": false, "error": {code, message, details}}`` on structured failure.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from print_engineer.adapters.slicer.registry import SlicerRegistry
from print_engineer.core.types import (
    ModelValidation,
    ProfileInfo,
    ProfileKind,
    SliceJob,
    SliceResult,
    SlicerInfo,
    SlicerKind,
)
from print_engineer.errors import SlicerError

_MODEL_SUFFIX_HINT = "Model must be a path like C:/models/cube.stl or cube.3mf"


def _slicer_info_dict(info: SlicerInfo) -> dict[str, Any]:
    return {
        "kind": info.kind.value,
        "name": info.name,
        "executable": str(info.executable),
        "version": info.version,
        "version_source": info.version_source,
        "slicing_supported": info.slicing_supported,
        "notes": list(info.notes),
    }


def _profile_dict(profile: ProfileInfo) -> dict[str, Any]:
    return {
        "name": profile.name,
        "kind": profile.kind.value,
        "source": profile.source.value,
        "setting_id": profile.setting_id,
        "printer_model": profile.printer_model,
        "printer_variant": profile.printer_variant,
        "compatible_printers": list(profile.compatible_printers),
        "inherits": profile.inherits,
        "materialized": profile.materialized,
    }


def _validation_dict(validation: ModelValidation) -> dict[str, Any]:
    return {
        "path": str(validation.path),
        "is_valid": validation.is_valid,
        "message": validation.message,
        "size": list(validation.size) if validation.size else None,
        "volume_mm3": validation.volume_mm3,
        "facets": validation.facets,
        "is_manifold": validation.is_manifold,
        "parts": validation.parts,
    }


def _result_dict(result: SliceResult) -> dict[str, Any]:
    return {
        "output_3mf": str(result.output_3mf) if result.output_3mf else None,
        "gcode_path": str(result.gcode_path) if result.gcode_path else None,
        "estimated_time_minutes": result.estimated_time_minutes,
        "layer_count": result.layer_count,
        "max_z_height": result.max_z_height,
        "filament_used_mm": result.filament_used_mm,
        "filament_used_cm3": result.filament_used_cm3,
        "filament_density": result.filament_density,
        "filament_weight_g": result.filament_weight_g,
        "return_code": result.return_code,
        "notes": result.notes,
    }


def _parse_kind(value: str) -> SlicerKind:
    for kind in SlicerKind:
        if kind.value == value:
            return kind
    raise SlicerError(
        f"Unknown slicer {value!r} (expected one of {[k.value for k in SlicerKind]})",
        details={"slicer_kind": value},
    )


def _find_profile(kind: ProfileKind, name: str, profiles: list[ProfileInfo]) -> ProfileInfo:
    for profile in profiles:
        if profile.name == name:
            return profile
    raise SlicerError(
        f"Unknown {kind.value} profile {name!r}",
        details={"profile_kind": kind.value, "profile_name": name},
    )


class SlicerTools:
    """Bound MCP tool implementations for one settings object."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    def _registry(self) -> SlicerRegistry:
        return SlicerRegistry(
            settings=self._settings,
            workdir=self._settings.storage.workspace_dir,
            timeout_seconds=self._settings.slicer.timeout_seconds,
        )

    def list_slicers(self) -> dict[str, Any]:
        try:
            detected = self._registry().detect_all()
        except SlicerError as exc:
            return {"ok": False, "error": exc.to_dict()}
        return {
            "ok": True,
            "slicers": [_slicer_info_dict(info) for info in detected.values()],
        }

    def slicer_info(self, slicer: str) -> dict[str, Any]:
        try:
            kind = _parse_kind(slicer)
            adapter = self._registry().get(kind)
            info = adapter.detect()
            if info is None:
                raise SlicerError(
                    f"Slicer {slicer!r} is not installed",
                    details={"slicer_kind": slicer},
                )
            counts = {
                kind.value: len(adapter.list_profiles(kind))
                for kind in ProfileKind
            }
        except SlicerError as exc:
            return {"ok": False, "error": exc.to_dict()}
        return {"ok": True, "slicer": _slicer_info_dict(info), "profile_counts": counts}

    def validate_model(self, slicer: str, model: str) -> dict[str, Any]:
        try:
            adapter = self._registry().get(_parse_kind(slicer))
            validation = adapter.validate_input(Path(model))
        except SlicerError as exc:
            return {"ok": False, "error": exc.to_dict()}
        return {"ok": validation.is_valid, "validation": _validation_dict(validation)}

    def slice_model(
        self,
        slicer: str,
        model: str,
        process: str,
        filament: str,
        printer: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        try:
            kind = _parse_kind(slicer)
            adapter = self._registry().get(kind)
            process_profile = _find_profile(
                ProfileKind.PROCESS, process, adapter.list_profiles(ProfileKind.PROCESS)
            )
            filament_profile = _find_profile(
                ProfileKind.FILAMENT, filament, adapter.list_profiles(ProfileKind.FILAMENT)
            )
            printer_profile = None
            if printer is not None:
                printer_profile = _find_profile(
                    ProfileKind.PRINTER, printer, adapter.list_profiles(ProfileKind.PRINTER)
                )
            job = SliceJob(
                model_path=Path(model),
                profile=process_profile,
                filament=filament_profile,
                printer=printer_profile,
                kind=kind,
                timeout_seconds=timeout_seconds,
            )
            result = adapter.slice(job)
        except SlicerError as exc:
            return {"ok": False, "error": exc.to_dict()}
        return {"ok": True, "result": _result_dict(result)}


def build_tools(settings: Any) -> dict[str, Callable[..., dict[str, Any]]]:
    """Return the ``slicer.*`` tool callables bound to *settings*."""
    tools = SlicerTools(settings)
    return {
        "slicer.list": tools.list_slicers,
        "slicer.info": tools.slicer_info,
        "slicer.validate": tools.validate_model,
        "slicer.slice": tools.slice_model,
    }
