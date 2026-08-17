"""Filament candidate matrix (Phase 3A.1).

Enumerates the locally-installed filament profiles for a slicer, materializes
inheritance chains, attaches vendor/consistency metadata, applies the resolved
print context (printer / nozzle / build plate / vendor / material) as filters,
and ranks the survivors deterministically for the requested goal.

Design rules:
- A profile is never trusted to be a generic "Default"; inheritance is resolved.
- ``vendor_verified`` is False unless the profile's own document declares
  ``filament_vendor``; an inherited vendor plus a name that implies another
  brand produces a ``data_warning``.
- Internally inconsistent values (e.g. nozzle temperature outside the declared
  range) become ``data_warnings``, never silent corrections.
- Ranking uses only numbers present in the profiles. Goals with no local
  numeric evidence (strength, surface quality) do not differentiate scores and
  mark ``requires_external_evidence``.
- No slicing is ever performed here.
"""

from __future__ import annotations

import json
from typing import Any

from print_engineer.core.recommendation import (
    FilamentCandidate,
    FilamentCandidateMatrix,
    RecommendationGoal,
    RejectedFilamentCandidate,
    ResolvedPrintContext,
)
from print_engineer.core.types import ProfileInfo, ProfileKind
from print_engineer.recommendation.context import ProfileReader

_MATERIAL_KEYWORDS = ("PLA", "PETG", "ABS", "ASA", "TPU", "PA", "PC", "PVA", "Nylon")

_NEUTRAL_BRAND_TOKENS = frozenset(
    {
        "generic",
        "standard",
        "fdm",
        "pla",
        "petg",
        "abs",
        "asa",
        "tpu",
        "pa",
        "pc",
        "pva",
        "nylon",
        "support",
        "soluble",
        "carbon",
        "wood",
        "glow",
        "silk",
        "matte",
        "transparent",
        "multicolor",
        "0.4",
        "0.2",
        "0.6",
        "0.8",
    }
)

_PLATE_FIELDS: dict[str, str] = {
    "cool": "cool_plate_temperature_c",
    "textured": "textured_plate_temperature_c",
    "hot": "hot_plate_temperature_c",
    "engineering": "hot_plate_temperature_c",
    "high temp": "hot_plate_temperature_c",
}

_GOAL_WEIGHTS: dict[RecommendationGoal, dict[str, float]] = {
    RecommendationGoal.PRINT_TIME: {"print_time": 1.0},
    RecommendationGoal.FILAMENT_USAGE: {"filament_usage": 1.0},
    RecommendationGoal.BALANCED: {"print_time": 0.5, "filament_usage": 0.5},
    RecommendationGoal.SURFACE_QUALITY: {},
    RecommendationGoal.STRENGTH: {},
}


def _first(value: object) -> object:
    if isinstance(value, (list, tuple)):
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
        if text.endswith("%"):
            text = text[:-1].strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


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


def _first_str(value: object) -> str | None:
    value = _first(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _str_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(";") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _material_from_name(name: str) -> str | None:
    upper = name.upper()
    for keyword in _MATERIAL_KEYWORDS:
        if keyword in upper:
            return keyword
    return None


def _read_json(profile: ProfileInfo) -> dict[str, Any]:
    if profile is None or profile.content is None:
        return {}
    try:
        data = json.loads(profile.content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _brand_token(name: str) -> str | None:
    token = name.split()[0].strip("()[]{}<>@") if name.split() else ""
    if token and token.upper() not in _NEUTRAL_BRAND_TOKENS:
        return token
    return None


def _inconsistent(a: str, b: str) -> bool:
    return a not in b and b not in a


def _plate_field(build_plate: str) -> str | None:
    lower = build_plate.lower()
    for key, field in _PLATE_FIELDS.items():
        if key in lower:
            return field
    return None


def _verify_vendor(
    candidate: FilamentCandidate,
    own_data: dict[str, Any],
    data: dict[str, Any],
    name: str,
) -> None:
    """Set vendor metadata from the profile's own document where possible."""
    own_vendor = _first_str(own_data.get("filament_vendor"))
    vendor = _first_str(data.get("filament_vendor")) or own_vendor
    candidate.vendor = vendor
    if own_vendor is not None:
        candidate.vendor_verified = True
    else:
        candidate.notes.append("vendor inherited or undeclared in the profile; not verified")
    token = _brand_token(name)
    if token and vendor and token.lower() not in vendor.lower():
        candidate.data_warnings.append(
            f"profile name suggests vendor {token!r} but declared/inherited vendor is {vendor!r}"
        )
    if vendor is None:
        candidate.data_warnings.append("no vendor declared anywhere in the profile")


def _check_consistency(candidate: FilamentCandidate, data: dict[str, Any]) -> None:
    temp = candidate.nozzle_temperature_c
    low = candidate.nozzle_temperature_range_low_c
    high = candidate.nozzle_temperature_range_high_c
    if temp is not None and low is not None and high is not None and not (
        low - 1e-9 <= temp <= high + 1e-9
    ):
        candidate.data_warnings.append(
            f"nozzle temperature {temp:g} C is outside the declared range {low:g}-{high:g} C"
        )
    initial = candidate.nozzle_temperature_initial_layer_c
    low_initial = _parse_float(data.get("nozzle_temperature_initial_layer_range_low"))
    high_initial = _parse_float(data.get("nozzle_temperature_initial_layer_range_high"))
    if (
        initial is not None
        and low_initial is not None
        and high_initial is not None
        and not (low_initial - 1e-9 <= initial <= high_initial + 1e-9)
    ):
        candidate.data_warnings.append(
            f"initial-layer nozzle temperature {initial:g} C is outside its declared range "
            f"{low_initial:g}-{high_initial:g} C"
        )

    field_material = _first_str(data.get("filament_type"))
    name_material = _material_from_name(candidate.profile_name)
    if (
        field_material
        and name_material
        and _inconsistent(field_material.upper(), name_material.upper())
    ):
        candidate.data_warnings.append(
            f"profile name implies material {name_material!r} but filament_type is "
            f"{field_material!r}"
        )


def _to_candidate(name: str, data: dict[str, Any], own_data: dict[str, Any]) -> FilamentCandidate:
    material = _first_str(data.get("filament_type")) or _material_from_name(name)
    candidate = FilamentCandidate(
        profile_name=name,
        setting_id=_first_str(data.get("setting_id")),
        material_type=material,
        density_g_cm3=_parse_float(data.get("filament_density")),
        max_volumetric_speed=_parse_float(data.get("filament_max_volumetric_speed")),
        flow_ratio=_parse_float(data.get("filament_flow_ratio")),
        cost_per_kg=_parse_float(data.get("filament_cost")),
        required_nozzle_hrc=_first_str(data.get("required_nozzle_HRC")),
        diameter_mm=_parse_float(data.get("filament_diameter")),
        shrinkage=_parse_float(data.get("filament_shrinkage")),
        soluble=_parse_bool(data.get("filament_soluble")),
        nozzle_temperature_c=_parse_float(data.get("nozzle_temperature")),
        nozzle_temperature_range_low_c=_parse_float(data.get("nozzle_temperature_range_low")),
        nozzle_temperature_range_high_c=_parse_float(data.get("nozzle_temperature_range_high")),
        nozzle_temperature_initial_layer_c=_parse_float(
            data.get("nozzle_temperature_initial_layer")
        ),
        hot_plate_temperature_c=_parse_float(data.get("hot_plate_temp")),
        textured_plate_temperature_c=_parse_float(data.get("textured_plate_temp")),
        cool_plate_temperature_c=_parse_float(data.get("cool_plate_temp")),
        fan_max_speed=_parse_float(data.get("fan_max_speed")),
        fan_min_speed=_parse_float(data.get("fan_min_speed")),
        fan_cooling_layer_time=_parse_float(data.get("fan_cooling_layer_time")),
        close_fan_the_first_x_layers=_parse_float(data.get("close_fan_the_first_x_layers")),
        overhang_fan_speed=_parse_float(data.get("overhang_fan_speed")),
        compatible_printers=_str_list(data.get("compatible_printers")),
    )
    _verify_vendor(candidate, own_data, data, name)
    _check_consistency(candidate, data)
    return candidate


def _score_list(values: list[float | None], *, higher_is_better: bool) -> list[float]:
    present = [(index, value) for index, value in enumerate(values) if value is not None]
    if not present:
        return [0.0] * len(values)
    lows = [value for _, value in present]
    lo, hi = min(lows), max(lows)
    span = hi - lo
    result = [0.0] * len(values)
    if span == 0:
        for index, _ in present:
            result[index] = 0.5
        return result
    for index, value in present:
        normalized = (value - lo) / span
        result[index] = normalized if higher_is_better else 1.0 - normalized
    return result


def _rank(candidates: list[FilamentCandidate], goal: RecommendationGoal) -> None:
    if not candidates:
        return
    weights = _GOAL_WEIGHTS.get(goal, {})
    speed_scores = _score_list(
        [candidate.max_volumetric_speed for candidate in candidates], higher_is_better=True
    )
    density_scores = _score_list(
        [candidate.density_g_cm3 for candidate in candidates], higher_is_better=False
    )
    cost_scores = _score_list(
        [candidate.cost_per_kg for candidate in candidates], higher_is_better=False
    )

    for index, candidate in enumerate(candidates):
        metrics: dict[str, float] = {}
        if "print_time" in weights:
            metrics["print_time"] = speed_scores[index]
        if "filament_usage" in weights:
            metrics["filament_usage"] = 0.5 * density_scores[index] + 0.5 * cost_scores[index]
        base = sum(weights.get(metric, 0.0) * value for metric, value in metrics.items())
        data_quality = 40.0 if candidate.vendor_verified else 0.0
        data_quality += max(0.0, 30.0 - 15.0 * len(candidate.data_warnings))
        candidate.goal_scores = {metric: round(value, 3) for metric, value in metrics.items()}
        candidate.score = max(0.0, round(100.0 * base + data_quality, 1))
        if goal in (RecommendationGoal.STRENGTH, RecommendationGoal.SURFACE_QUALITY):
            candidate.requires_external_evidence = True

    candidates.sort(key=lambda candidate: (-candidate.score, candidate.profile_name))


class FilamentMatrixBuilder:
    """Builds a :class:`FilamentCandidateMatrix` from a resolved context."""

    def __init__(self, settings: Any, adapter: ProfileReader) -> None:
        self._settings = settings
        self._adapter = adapter

    def build(
        self,
        resolved: ResolvedPrintContext,
        *,
        goal: RecommendationGoal = RecommendationGoal.BALANCED,
        vendor: str | None = None,
        material: str | None = None,
    ) -> FilamentCandidateMatrix:
        warnings: list[str] = []
        candidates: list[FilamentCandidate] = []
        rejected: list[RejectedFilamentCandidate] = []

        printer = resolved.printer
        if printer is None:
            warnings.append("printer not specified; printer compatibility filters were skipped")
        if resolved.nozzle_diameter_mm is None:
            warnings.append(
                "nozzle not specified; nozzle-dependent filtering is not possible "
                "(no filament profile restricts nozzle diameter)"
            )

        plate_field: str | None = None
        if resolved.build_plate:
            plate_field = _plate_field(resolved.build_plate)
            if plate_field is None:
                warnings.append(
                    f"build plate {resolved.build_plate!r} could not be mapped to a profile "
                    "plate temperature field; plate filter skipped"
                )

        profiles = self._adapter.list_profiles(ProfileKind.FILAMENT)
        for raw in profiles:
            candidate, error = self._build_one(
                raw,
                resolved=resolved,
                vendor=vendor,
                material=material,
                plate_field=plate_field,
            )
            if candidate is not None:
                candidates.append(candidate)
            elif error is not None:
                rejected.append(error)

        _rank(candidates, goal)
        return FilamentCandidateMatrix(
            slicer_kind=resolved.slicer_kind,
            printer=printer,
            goal=goal,
            nozzle_diameter_mm=resolved.nozzle_diameter_mm,
            build_plate=resolved.build_plate,
            candidates=candidates,
            rejected=rejected,
            warnings=warnings,
        )

    def _build_one(
        self,
        raw: ProfileInfo,
        *,
        resolved: ResolvedPrintContext,
        vendor: str | None,
        material: str | None,
        plate_field: str | None,
    ) -> tuple[FilamentCandidate | None, RejectedFilamentCandidate | None]:
        name = raw.name
        materialized = self._adapter.find_profile(ProfileKind.FILAMENT, name)
        if materialized is None:
            return None, RejectedFilamentCandidate(
                profile_name=name,
                reason_code="materialization_failed",
                reason="the profile could not be resolved against the slicer store",
            )

        data = _read_json(materialized)
        own_data = _read_json(raw)
        if not data:
            return None, RejectedFilamentCandidate(
                profile_name=name,
                reason_code="unparseable",
                reason="profile content could not be parsed as JSON",
            )

        compatible = _str_list(data.get("compatible_printers"))
        if resolved.printer is not None and compatible:
            if resolved.printer.name not in compatible:
                return None, RejectedFilamentCandidate(
                    profile_name=name,
                    vendor=_first_str(data.get("filament_vendor")),
                    material_type=_first_str(data.get("filament_type")),
                    reason_code="incompatible_printer",
                    reason=f"not compatible with printer {resolved.printer.name!r}",
                )

        candidate = _to_candidate(name, data, own_data)

        if plate_field is not None and getattr(candidate, plate_field) is None:
            return None, RejectedFilamentCandidate(
                profile_name=name,
                vendor=candidate.vendor,
                material_type=candidate.material_type,
                reason_code="incompatible_build_plate",
                reason=(
                    f"no {resolved.build_plate} temperature defined for this filament "
                    f"({plate_field} is unset)"
                ),
            )

        if vendor is not None:
            if not candidate.vendor or vendor.lower() not in candidate.vendor.lower():
                return None, RejectedFilamentCandidate(
                    profile_name=name,
                    vendor=candidate.vendor,
                    material_type=candidate.material_type,
                    reason_code="vendor_filter",
                    reason=f"vendor does not match filter {vendor!r}",
                )

        if material is not None:
            if not candidate.material_type or candidate.material_type.lower() != material.lower():
                return None, RejectedFilamentCandidate(
                    profile_name=name,
                    vendor=candidate.vendor,
                    material_type=candidate.material_type,
                    reason_code="material_filter",
                    reason=f"material type does not match filter {material!r}",
                )

        return candidate, None
