"""Parsers for OrcaSlicer/Bambu Studio slice outputs.

``plate_1.gcode`` header block exposes:

    ; model printing time: 28m 31s; total estimated time: 28m 33s
    ; total layer number: 100
    ; filament_density: 0
    ; max_z_height: 20.00

and the file footer:

    ; filament used [mm] = 1321.15
    ; filament used [cm3] = 3.18

The exported ``*.gcode.3mf`` is a zip archive whose ``Metadata`` entries carry
the same gcode plus ``slice_info.config`` (prediction seconds, filament
``used_m``/``used_g``) and ``plate_1.json`` (layer height, nozzle, bed type).
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any

_DURATION_RE = re.compile(r"(\d+)\s*(h|m|s)", re.IGNORECASE)

_HEADER_TIME_RE = re.compile(r";\s*model printing time:\s*([^;]+)")
_HEADER_LAYERS_RE = re.compile(r";\s*total layer number:\s*(\d+)")
_HEADER_MAX_Z_RE = re.compile(r";\s*max_z_height:\s*([\d.]+)")
_HEADER_DENSITY_RE = re.compile(r";\s*filament_density:\s*([\d.]+)")
_FOOTER_MM_RE = re.compile(r";\s*filament used \[mm\] = ([\d.]+)")
_FOOTER_CM3_RE = re.compile(r";\s*filament used \[cm3\] = ([\d.]+)")

_SLICE_PREDICTION_RE = re.compile(r'<metadata key="prediction" value="(\d+)"')
_SLICE_WEIGHT_RE = re.compile(r'<metadata key="weight" value="([^"]*)"')
_SLICE_FILAMENT_RE = re.compile(
    r'<filament\b[^>]*used_m="([^"]*)"[^>]*used_g="([^"]*)"'
)


def parse_duration(text: str) -> float:
    """Parse a duration like ``28m 31s`` / ``1h 2m 3s`` into minutes."""
    seconds = 0.0
    for value, unit in _DURATION_RE.findall(text):
        number = float(value)
        if unit.lower() == "h":
            seconds += number * 3600
        elif unit.lower() == "m":
            seconds += number * 60
        else:
            seconds += number
    return seconds / 60.0


def _first(regex: re.Pattern[str], text: str) -> str | None:
    match = regex.search(text)
    return match.group(1) if match else None


def parse_gcode(path: Path) -> dict[str, Any]:
    """Parse statistics from a sliced ``plate_1.gcode`` file.

    Returns a mapping with ``time_minutes``, ``layer_count``, ``max_z_height``,
    ``filament_used_mm``, ``filament_used_cm3`` and ``filament_density`` (all
    optional; ``None`` when a value is absent).
    """
    text = path.read_text(encoding="utf-8", errors="replace")

    time_value = _first(_HEADER_TIME_RE, text)
    layers_value = _first(_HEADER_LAYERS_RE, text)
    max_z_value = _first(_HEADER_MAX_Z_RE, text)
    density_value = _first(_HEADER_DENSITY_RE, text)

    filament_mm = sum(float(v) for v in _FOOTER_MM_RE.findall(text))
    filament_cm3 = sum(float(v) for v in _FOOTER_CM3_RE.findall(text))

    return {
        "time_minutes": parse_duration(time_value) if time_value else None,
        "layer_count": int(layers_value) if layers_value else None,
        "max_z_height": float(max_z_value) if max_z_value else None,
        "filament_used_mm": filament_mm if _FOOTER_MM_RE.search(text) else None,
        "filament_used_cm3": filament_cm3 if _FOOTER_CM3_RE.search(text) else None,
        "filament_density": float(density_value) if density_value else None,
    }


def parse_gcode_3mf(path: Path) -> dict[str, Any]:
    """Parse the Metadata entries of an exported ``*.gcode.3mf`` archive."""
    result: dict[str, Any] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "Metadata/slice_info.config" in names:
                xml = archive.read("Metadata/slice_info.config").decode(
                    "utf-8", errors="replace"
                )
                prediction = _SLICE_PREDICTION_RE.search(xml)
                if prediction:
                    result["prediction_seconds"] = int(prediction.group(1))
                weight = _SLICE_WEIGHT_RE.search(xml)
                if weight and weight.group(1):
                    result["weight_g"] = float(weight.group(1))
                filament = _SLICE_FILAMENT_RE.search(xml)
                if filament:
                    result["filament_used_m"] = (
                        float(filament.group(1)) if filament.group(1) else None
                    )
                    result["filament_used_g"] = (
                        float(filament.group(2)) if filament.group(2) else None
                    )
            if "Metadata/plate_1.json" in names:
                try:
                    plate = json.loads(
                        archive.read("Metadata/plate_1.json").decode("utf-8", errors="replace")
                    )
                except json.JSONDecodeError:
                    plate = {}
                if isinstance(plate, dict):
                    result["layer_height"] = plate.get("layer_height")
                    result["nozzle_diameter"] = plate.get("nozzle_diameter")
                    result["bed_type"] = plate.get("bed_type")
    except (zipfile.BadZipFile, OSError):
        return result
    return result
