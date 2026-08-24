"""Pure, deterministic realization of an authoritative selected setup.

This module describes the in-memory inputs that a later slicing increment may
materialize.  It deliberately has no subprocess, network, printer, or
filesystem-write path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

from print_engineer.adapters.slicer.profile import ProfileMaterializer, ProfileRepository
from print_engineer.adapters.slicer.settings import build_digest
from print_engineer.core.preparation import (
    ActualInputIdentity,
    AppliedOverride,
    FailureStage,
    PreparationFailure,
    ProfileIdentity,
    SelectedSetup,
)
from print_engineer.core.types import ProfileInfo, SlicerKind
from print_engineer.errors import InvalidProfile


class _RealizationError(ValueError):
    """A deterministic, user-facing realization failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

ORCA_CAPABILITY: Final[str] = "OrcaSlicer 2.3.2"

_PLATES: Final[dict[str, tuple[str, str]]] = {
    "cool_plate": ("Cool Plate", "cool_plate"),
    "textured_pei_plate": ("Textured PEI Plate", "textured_plate"),
    "high_temp_plate": ("High Temp Plate", "hot_plate"),
}
_OVERRIDES: Final[dict[str, tuple[str, str]]] = {
    "layer_height_mm": ("layer_height", "mm"),
    "wall_loops": ("wall_loops", "unitless"),
    "sparse_infill_percent": ("sparse_infill_density", "percent"),
    "sparse_infill_pattern": ("sparse_infill_pattern", "none"),
    "support_enablement": ("enable_support", "boolean"),
    "support_type": ("support_type", "none"),
    "support_threshold_angle_deg": ("support_threshold_angle", "degrees"),
    "outer_wall_speed_mms": ("outer_wall_speed", "mm/s"),
}


def _decimal(value: object, field: str, *, allow_zero: bool = False) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} is not representable") from None
    if not number.is_finite() or (number < 0 if allow_zero else number <= 0):
        message = (
            f"{field} must be finite and non-negative"
            if allow_zero
            else f"{field} must be finite and positive"
        )
        raise ValueError(message)
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


class _ExactRepository:
    """Repository view that prevents the materializer's name lookup from shadowing the root."""

    def __init__(self, repository: ProfileRepository, root: ProfileInfo) -> None:
        self._repository = repository
        self._root = root

    def find(self, kind: object, name: str) -> ProfileInfo | None:
        if kind is self._root.kind and name == self._root.name:
            return self._root
        return self._repository.find(kind, name)  # type: ignore[arg-type]


def _materialize_exact(
    repository: ProfileRepository,
    profile: ProfileInfo,
    materializer: ProfileMaterializer | None = None,
) -> ProfileInfo:
    # ProfileMaterializer.materialize(ProfileInfo) is safe only when its
    # repository resolves the same root object.  Give it a narrow repository
    # view rather than allowing find(kind, name) to select a shadow.
    try:
        owner = materializer or ProfileMaterializer(
            _ExactRepository(repository, profile)  # type: ignore[arg-type]
        )
        return owner.materialize(profile)
    except InvalidProfile as exc:
        raise _RealizationError("profile_materialization_failed", str(exc)) from exc


def _effective_profiles(
    printer: ProfileInfo, process: ProfileInfo, printer_overlay: tuple[OverlayEntry, ...],
    process_overlay: tuple[OverlayEntry, ...],
) -> tuple[dict[str, object], dict[str, object]]:
    try:
        printer_data = json.loads(printer.content or "")
        process_data = json.loads(process.content or "")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _RealizationError("profile_content_invalid", str(exc)) from exc
    if not isinstance(printer_data, dict) or not isinstance(process_data, dict):
        raise _RealizationError(
            "profile_content_invalid", "materialized profile content must be JSON objects"
        )
    for entry in printer_overlay:
        printer_data[entry.key] = entry.value
    for entry in process_overlay:
        process_data[entry.key] = entry.value
    return printer_data, process_data


def _validate_effective(
    printer: ProfileInfo, process: ProfileInfo, printer_overlay: tuple[OverlayEntry, ...],
    process_overlay: tuple[OverlayEntry, ...], selected_nozzle: str, native_plate: str,
) -> None:
    printer_data, process_data = _effective_profiles(
        printer, process, printer_overlay, process_overlay
    )
    if printer_data.get("curr_bed_type") != native_plate:
        raise _RealizationError("effective_settings_mismatch", "effective build plate mismatch")
    if str(printer_data.get("nozzle_diameter")) != selected_nozzle:
        raise _RealizationError("effective_settings_mismatch", "effective nozzle mismatch")
    effective = ProfileInfo(name=process.name, kind=process.kind, content=json.dumps(process_data))
    digest = build_digest(slicer_kind= SlicerKind.ORCA_SLICER.value, process=effective).process
    if digest is None:
        raise _RealizationError("invalid_effective_value", "effective process settings unavailable")
    expected = {
        "layer_height": "layer_height_mm", "wall_loops": "wall_loops",
        "sparse_infill_density": "sparse_infill_percent",
        "sparse_infill_pattern": "sparse_infill_pattern",
        "enable_support": "enable_support", "support_type": "support_type",
        "support_threshold_angle": "support_threshold_angle_deg",
        "outer_wall_speed": "outer_wall_speed_mms",
    }
    for entry in process_overlay:
        field = expected[entry.key]
        actual = getattr(digest, field, None)
        if actual is None:
            raise _RealizationError(
                "invalid_effective_value", f"effective setting {entry.key} is unavailable"
            )
        if field == "sparse_infill_percent":
            wanted: object = float(entry.value.rstrip("%"))
        elif field == "enable_support":
            wanted = entry.value == "1"
        elif field == "wall_loops":
            wanted = int(entry.value)
        elif field in {"sparse_infill_pattern", "support_type"}:
            wanted = entry.value
        else:
            wanted = float(entry.value)
        if actual != wanted:
            raise _RealizationError(
                "effective_settings_mismatch", f"effective setting mismatch for {entry.key}"
            )


@dataclass(frozen=True, slots=True)
class OverlayEntry:
    key: str
    value: str
    layer: str
    units: str


@dataclass(frozen=True, slots=True)
class ProfileReference:
    identity: ProfileIdentity
    materialized_name: str
    content_sha256: str
    content: str


@dataclass(frozen=True, slots=True)
class RealizationResource:
    kind: str
    identity: str
    content_sha256: str
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class EffectiveSliceInputs:
    slicer: SlicerKind
    capability: str
    printer: ProfileReference
    process: ProfileReference
    filament: ProfileReference
    nozzle_diameter_mm: float
    nozzle_diameter: str
    build_plate: str
    native_build_plate: str
    observed_build_plate: str
    material: str
    printer_overlay: tuple[OverlayEntry, ...]
    process_overlay: tuple[OverlayEntry, ...]
    actual_inputs: ActualInputIdentity
    identity: str


@dataclass(frozen=True, slots=True)
class RealizationResult:
    selected_setup: SelectedSetup
    effective_inputs: EffectiveSliceInputs | None
    resources: tuple[RealizationResource, ...]
    succeeded: bool
    failure: PreparationFailure | None = None


def _resolve(
    repository: ProfileRepository, identity: ProfileIdentity
) -> ProfileInfo:
    candidates = [
        profile
        for profile in repository.list_profiles(identity.kind)
        if profile.name == identity.name and profile.setting_id == identity.setting_id
    ]
    if not candidates:
        raise LookupError(f"{identity.kind.value} profile {identity.name!r} is missing")
    if len(candidates) != 1:
        raise RuntimeError(f"{identity.kind.value} profile {identity.name!r} is ambiguous")
    if candidates[0].kind is not identity.kind:
        raise _RealizationError(
            "wrong_profile_kind", f"{identity.kind.value} profile has the wrong kind"
        )
    return candidates[0]


def _overlay_value(item: AppliedOverride) -> tuple[str, str, str]:
    key, units = _OVERRIDES[item.setting]
    if item.setting == "support_enablement":
        value = "1" if item.value is True else "0"
    elif item.setting == "sparse_infill_percent":
        value = f"{item.canonical_value}%"
    elif item.setting in {"sparse_infill_pattern", "support_type"}:
        value = item.canonical_value
    elif item.setting == "wall_loops":
        value = item.canonical_value
    elif item.setting == "support_threshold_angle_deg":
        value = _decimal(item.canonical_value, item.setting, allow_zero=True)
    else:
        value = _decimal(item.canonical_value, item.setting)
    if not value:
        raise _RealizationError("unsupported_override", f"{item.setting} is unrepresentable")
    return key, value, units


def realize_setup(
    selected_setup: SelectedSetup,
    repository: ProfileRepository,
    materializer: ProfileMaterializer | None = None,
    capability: str = ORCA_CAPABILITY,
) -> RealizationResult:
    """Realize *selected_setup* without touching the filesystem or slicer."""
    if selected_setup.slicer is not SlicerKind.ORCA_SLICER or capability != ORCA_CAPABILITY:
        return RealizationResult(
            selected_setup, None, (), False,
            PreparationFailure(FailureStage.REALIZATION, "unsupported_slicer_version",
                               "Only OrcaSlicer 2.3.2 realization is supported."),
        )
    try:
        source_profiles = tuple(
            _resolve(repository, identity)
            for identity in (
                selected_setup.printer,
                selected_setup.process_profile,
                selected_setup.filament_profile,
            )
        )
        resolved = tuple(
            _materialize_exact(repository, profile, materializer)
            for profile in source_profiles
        )
        printer, process, filament = resolved
        printer_content, process_content, filament_content = (
            printer.content,
            process.content,
            filament.content,
        )
        if not all(
            isinstance(content, str)
            for content in (printer_content, process_content, filament_content)
        ):
            raise InvalidProfile("materialized profile has no content")
        assert isinstance(printer_content, str)
        assert isinstance(process_content, str)
        assert isinstance(filament_content, str)
        try:
            printer_data = json.loads(printer_content)
            process_data = json.loads(process_content)
            filament_data = json.loads(filament_content)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise _RealizationError("profile_content_invalid", str(exc)) from exc
        if not all(isinstance(data, dict) for data in (printer_data, process_data, filament_data)):
            raise _RealizationError(
                "profile_content_invalid", "materialized profile content must be JSON objects"
            )
        compatible = process.compatible_printers
        if compatible and printer.name not in compatible:
            return _result_failure(
                selected_setup,
                "incompatible_profiles",
                "process profile is not compatible with the selected printer",
            )
        supported_raw = printer_data.get("nozzle_diameter")
        if isinstance(supported_raw, list):
            supported_values = supported_raw
        elif isinstance(supported_raw, str) and ";" in supported_raw:
            supported_values = supported_raw.split(";")
        elif isinstance(supported_raw, str):
            supported_values = [supported_raw]
        else:
            raise _RealizationError(
                "profile_content_invalid", "printer nozzle declaration is malformed"
            )
        selected_nozzle = _decimal(selected_setup.nozzle_diameter_mm, "nozzle_diameter_mm")
        try:
            supported = {_decimal(value, "printer nozzle") for value in supported_values}
        except ValueError as exc:
            raise _RealizationError("profile_content_invalid", str(exc)) from exc
        if selected_nozzle not in supported:
            raise _RealizationError(
                "unsupported_nozzle", "selected nozzle is not declared by printer"
            )
        plate_key = selected_setup.build_plate
        if plate_key not in _PLATES:
            raise _RealizationError(
                "build_plate_not_representable", "unsupported build plate"
            )
        native_plate, observed_plate = _PLATES[plate_key]
        material_value = filament_data.get("filament_type")
        if (
            not isinstance(material_value, str)
            or not material_value
            or material_value.isspace()
        ):
            raise _RealizationError("material_not_provable", "filament material is unknown")
        if selected_setup.material != material_value:
            raise _RealizationError(
                "material_profile_mismatch",
                "selected material conflicts with filament profile",
            )
        printer_overlay = (
            OverlayEntry("curr_bed_type", native_plate, "printer", "none"),
            OverlayEntry("nozzle_diameter", selected_nozzle, "printer", "mm"),
        )
        process_overlay = tuple(
            OverlayEntry(key, value, "process", units)
            for key, value, units in sorted(
                (_overlay_value(item) for item in selected_setup.overrides),
                key=lambda x: x[0],
            )
        )
        _validate_effective(
            printer, process, printer_overlay, process_overlay, selected_nozzle, native_plate
        )
        actual = ActualInputIdentity(
            slicer=selected_setup.slicer, printer=selected_setup.printer,
            nozzle_diameter_mm=selected_setup.nozzle_diameter_mm, build_plate=plate_key,
            material=material_value, filament_profile=selected_setup.filament_profile,
            process_profile=selected_setup.process_profile,
            overrides=selected_setup.overrides,
        )
        refs_list: list[ProfileReference] = []
        for identity_ref, profile in zip(
            (
                selected_setup.printer,
                selected_setup.process_profile,
                selected_setup.filament_profile,
            ),
            resolved,
            strict=True,
        ):
            content = profile.content
            assert isinstance(content, str)
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("profile content must be a JSON object")
            digest = hashlib.sha256(_canonical_json(parsed)).hexdigest()
            refs_list.append(ProfileReference(identity_ref, profile.name, digest, content))
        refs = tuple(refs_list)
        semantic = {
            "capability": capability, "slicer": selected_setup.slicer.value,
            "profiles": [
                {
                    "identity": {
                        "name": ref.identity.name,
                        "kind": ref.identity.kind.value,
                        "setting_id": ref.identity.setting_id,
                    },
                    "name": ref.materialized_name,
                    "sha256": ref.content_sha256,
                }
                for ref in refs
            ],
            "printer_overlay": [(e.key, e.value, e.layer, e.units) for e in printer_overlay],
            "process_overlay": [(e.key, e.value, e.layer, e.units) for e in process_overlay],
            "actual": {
                "nozzle": selected_nozzle,
                "plate": plate_key,
                "material": material_value,
                "overrides": [
                    (o.setting, o.canonical_value)
                    for o in sorted(actual.overrides, key=lambda item: item.setting)
                ],
            },
        }
        identity = hashlib.sha256(_canonical_json(semantic)).hexdigest()
        effective = EffectiveSliceInputs(
            selected_setup.slicer, capability, refs[0], refs[1], refs[2],
            selected_setup.nozzle_diameter_mm, selected_nozzle, plate_key, native_plate,
            observed_plate,
            material_value,
            printer_overlay,
            process_overlay,
            actual,
            identity,
        )
        resources = tuple(
            RealizationResource(
                kind,
                hashlib.sha256(
                    _canonical_json(
                        {
                            "capability": capability,
                            "kind": kind,
                            "content": json.loads(ref.content),
                            "overlay": [
                                (entry.key, entry.value, entry.layer, entry.units)
                                for entry in (
                                    printer_overlay if kind == "printer"
                                    else process_overlay if kind == "process" else ()
                                )
                            ],
                        }
                    )
                ).hexdigest(),
                ref.content_sha256,
            )
            for kind, ref in zip(("printer", "process", "filament"), refs, strict=True)
        )
        return RealizationResult(selected_setup, effective, resources, True, None)
    except LookupError as exc:
        code = "missing_profile"
        text = str(exc)
        if "printer" in text:
            code = "printer_profile_missing"
        elif "process" in text:
            code = "process_profile_missing"
        elif "filament" in text:
            code = "filament_profile_missing"
        return _result_failure(selected_setup, code, text)
    except RuntimeError as exc:
        return _result_failure(selected_setup, "ambiguous_profile_resolution", str(exc))
    except _RealizationError as exc:
        return _result_failure(selected_setup, exc.code, str(exc))
    except (InvalidProfile, TypeError, ValueError, KeyError) as exc:
        return _result_failure(selected_setup, "profile_content_invalid", str(exc))


def _result_failure(setup: SelectedSetup, code: str, message: str) -> RealizationResult:
    return RealizationResult(setup, None, (), False, PreparationFailure(
        FailureStage.REALIZATION, code, message,
    ))


class SetupRealizer:
    """Dependency-injected façade for the pure realization function."""

    def __init__(
        self,
        repository: ProfileRepository,
        materializer: ProfileMaterializer | None = None,
    ) -> None:
        self._repository = repository
        self._materializer = materializer

    def realize(self, selected_setup: SelectedSetup) -> RealizationResult:
        return realize_setup(selected_setup, self._repository, self._materializer)
