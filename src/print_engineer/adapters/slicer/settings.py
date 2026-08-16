"""Typed settings digest reader for materialized slicer profiles.

OrcaSlicer/Bambu Studio profiles store nearly every value as a string
(``"0.2"``, ``"15%"``, ``"0"``, ``"tree(auto)"``) and speeds as arrays. This
module converts a *materialized* profile (fully resolved inheritance chain)
into a typed :class:`SlicerSettingsDigest` with explicit units.

Parsing is strict: a value that cannot be parsed becomes ``None`` and the field
name is recorded in ``digest.unavailable``. The reader never guesses.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from print_engineer.core.recommendation import (
    FilamentSettings,
    PrinterSettings,
    ProcessSettings,
    SlicerSettingsDigest,
)
from print_engineer.core.types import ProfileInfo

_MATERIAL_KEYWORDS = ("PLA", "PETG", "ABS", "ASA", "TPU", "PA", "PC", "PVA", "Nylon")


def _first(value: object) -> object:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _parse_float(value: object) -> float | None:
    value = _first(value)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _parse_int(value: object) -> int | None:
    parsed = _parse_float(value)
    if parsed is None or not parsed.is_integer():
        return None
    return int(parsed)


def _parse_bool(value: object) -> bool | None:
    value = _first(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0.0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
    return None


def _parse_percent(value: object) -> float | None:
    """Parse ``"15%"`` -> 15.0; a bare number is treated as percent as-is."""
    value = _first(value)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            text = text[:-1].strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


_FLOAT_FIELDS = {
    "layer_height_mm": "layer_height",
    "initial_layer_height_mm": "initial_layer_print_height",
    "line_width_mm": "line_width",
    "top_shell_thickness_mm": "top_shell_thickness",
    "bottom_shell_thickness_mm": "bottom_shell_thickness",
    "support_threshold_angle_deg": "support_threshold_angle",
    "outer_wall_speed_mms": "outer_wall_speed",
    "inner_wall_speed_mms": "inner_wall_speed",
    "top_surface_speed_mms": "top_surface_speed",
    "sparse_infill_speed_mms": "sparse_infill_speed",
    "initial_layer_speed_mms": "initial_layer_speed",
    "brim_width_mm": "brim_width",
}

_INT_FIELDS = {
    "wall_loops": "wall_loops",
    "top_shell_layers": "top_shell_layers",
    "bottom_shell_layers": "bottom_shell_layers",
}

_BOOL_FIELDS = {
    "enable_support": "enable_support",
    "support_on_build_plate_only": "support_on_build_plate_only",
    "detect_thin_wall": "detect_thin_wall",
    "spiral_mode": "spiral_mode",
    "adaptive_layer_height": "adaptive_layer_height",
}

_STR_FIELDS = {
    "wall_generator": "wall_generator",
    "sparse_infill_pattern": "sparse_infill_pattern",
    "support_type": "support_type",
}

_PERCENT_FIELDS = {"sparse_infill_percent": "sparse_infill_density"}


def _read_json(profile: ProfileInfo) -> dict[str, Any] | None:
    if profile is None or profile.content is None:
        return None
    try:
        data = json.loads(profile.content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_material_type(name: str, data: dict[str, Any]) -> str | None:
    value = data.get("filament_type")
    if isinstance(value, str) and value.strip():
        return value.strip()
    upper = name.upper()
    for keyword in _MATERIAL_KEYWORDS:
        if keyword in upper:
            return keyword
    return None


def _build_process(
    data: dict[str, Any] | None, name: str
) -> tuple[ProcessSettings | None, list[str]]:
    if data is None:
        return None, []
    unavailable: list[str] = []

    def get(field: str, key: str, parser: Callable[[object], object]) -> object:
        value = parser(data.get(key))
        if value is None:
            unavailable.append(field)
        return value

    fields: dict[str, object] = {"name": name}
    for field, key in _FLOAT_FIELDS.items():
        fields[field] = get(field, key, _parse_float)
    for field, key in _INT_FIELDS.items():
        fields[field] = get(field, key, _parse_int)
    for field, key in _BOOL_FIELDS.items():
        fields[field] = get(field, key, _parse_bool)
    for field, key in _STR_FIELDS.items():
        raw = data.get(key)
        text = _first(raw)
        if isinstance(text, str) and text.strip():
            fields[field] = text.strip()
        else:
            fields[field] = None
            unavailable.append(field)
    for field, key in _PERCENT_FIELDS.items():
        fields[field] = get(field, key, _parse_percent)

    return ProcessSettings.model_validate(fields), unavailable


def _build_filament(
    data: dict[str, Any] | None, name: str
) -> tuple[FilamentSettings | None, list[str]]:
    if data is None:
        return None, []
    unavailable: list[str] = []
    material = _read_material_type(name, data)
    if material is None:
        unavailable.append("material_type")

    density = _parse_float(data.get("filament_density"))
    max_volumetric = _parse_float(data.get("filament_max_volumetric_speed"))
    flow_ratio = _parse_float(data.get("filament_flow_ratio"))
    vendor = _first(data.get("filament_vendor"))

    for field, value in (
        ("density_g_cm3", density),
        ("max_volumetric_speed", max_volumetric),
        ("flow_ratio", flow_ratio),
    ):
        if value is None:
            unavailable.append(field)

    return (
        FilamentSettings(
            name=name,
            material_type=material,
            density_g_cm3=density,
            max_volumetric_speed=max_volumetric,
            flow_ratio=flow_ratio,
            vendor=vendor if isinstance(vendor, str) and vendor.strip() else None,
        ),
        unavailable,
    )


def _build_printer(
    data: dict[str, Any] | None, name: str
) -> tuple[PrinterSettings | None, list[str]]:
    if data is None:
        return None, []
    unavailable: list[str] = []
    nozzle = _parse_float(data.get("nozzle_diameter"))
    printable_height = _parse_float(data.get("printable_height"))
    max_accel = _parse_float(data.get("machine_max_acceleration_extruding"))
    for field, value in (
        ("nozzle_diameter_mm", nozzle),
        ("printable_height_mm", printable_height),
        ("max_acceleration_mm_s2", max_accel),
    ):
        if value is None:
            unavailable.append(field)

    return (
        PrinterSettings(
            name=name,
            nozzle_diameter_mm=nozzle,
            printable_height_mm=printable_height,
            printer_model=(
                data.get("printer_model")
                if isinstance(data.get("printer_model"), str)
                else None
            ),
            printer_variant=(
                data.get("printer_variant")
                if isinstance(data.get("printer_variant"), str)
                else None
            ),
            max_acceleration_mm_s2=max_accel,
        ),
        unavailable,
    )


def build_digest(
    *,
    slicer_kind: str,
    process: ProfileInfo | None = None,
    filament: ProfileInfo | None = None,
    printer: ProfileInfo | None = None,
) -> SlicerSettingsDigest:
    """Build a typed digest from materialized profiles (all optional)."""
    unavailable: list[str] = []
    notes: list[str] = []

    process_settings: ProcessSettings | None = None
    if process is not None:
        data = _read_json(process)
        process_settings, process_unavailable = _build_process(data, process.name)
        if data is None:
            notes.append(f"process profile {process.name!r} could not be parsed")
        unavailable.extend(process_unavailable)

    filament_settings: FilamentSettings | None = None
    if filament is not None:
        data = _read_json(filament)
        filament_settings, filament_unavailable = _build_filament(data, filament.name)
        if data is None:
            notes.append(f"filament profile {filament.name!r} could not be parsed")
        unavailable.extend(filament_unavailable)

    printer_settings: PrinterSettings | None = None
    if printer is not None:
        data = _read_json(printer)
        printer_settings, printer_unavailable = _build_printer(data, printer.name)
        if data is None:
            notes.append(f"printer profile {printer.name!r} could not be parsed")
        unavailable.extend(printer_unavailable)

    if process_settings is None and filament_settings is None and printer_settings is None:
        notes.append("no slicer profile information available")

    return SlicerSettingsDigest(
        slicer_kind=slicer_kind,
        process=process_settings,
        filament=filament_settings,
        printer=printer_settings,
        unavailable=sorted(set(unavailable)),
        notes=notes,
    )


def is_materialized(profile: ProfileInfo) -> bool:
    """Whether *profile* is materialized (resolved, CLI-consumable)."""
    return profile.materialized and profile.content is not None
