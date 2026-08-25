"""Immutable, data-only contracts for bounded print preparation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from print_engineer.core.recommendation import RECOMMENDABLE_SETTINGS, RecommendationGoal
from print_engineer.core.types import ProfileKind, SlicerKind


class EvidenceAuthority(StrEnum):
    MODEL_FACTS = "model_facts"
    COMPATIBILITY = "compatibility"
    SELECTION = "selection"
    ASSUMPTION = "assumption"
    UNKNOWN = "unknown"
    WARNING = "warning"
    REJECTED_ALTERNATIVE = "rejected_alternative"
    SLICE = "slice"
    VERIFICATION = "verification"


class FailureStage(StrEnum):
    MODEL_INPUT = "model_input"
    SETUP_SELECTION = "setup_selection"
    REALIZATION = "realization"
    VALIDATION = "validation"
    SLICING = "slicing"
    ARTIFACT = "artifact"
    FINAL_VERIFICATION = "final_verification"


class VerificationStatus(StrEnum):
    PASS = "pass"
    BLOCKING_MISMATCH = "blocking_mismatch"
    NON_BLOCKING_WARNING = "non_blocking_warning"


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-blank")
    return value.strip()


def _finite(value: float, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise TypeError(f"{field} must be a finite number")
    return float(value)


def _typed_tuple[T](
    value: tuple[T, ...] | list[T], item_type: type[T], field: str
) -> tuple[T, ...]:
    if not isinstance(value, (tuple, list)):
        raise TypeError(f"{field} must be a tuple or list")
    if any(type(item) is not item_type for item in value):
        raise TypeError(f"{field} contains an invalid item")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    path: Path
    sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.path, (str, Path)):
            raise TypeError("model path must be a string or Path")
        if isinstance(self.path, str) and not self.path.strip():
            raise ValueError("model path must be non-blank")
        object.__setattr__(self, "path", Path(self.path))
        if not str(self.path).strip():
            raise ValueError("model path must be non-blank")
        if self.sha256 is not None:
            if not isinstance(self.sha256, str) or not re.fullmatch(
                r"[0-9a-fA-F]{64}", self.sha256
            ):
                raise ValueError("sha256 must be a 64-character hexadecimal digest")


@dataclass(frozen=True, slots=True)
class PreparationIdentity:
    model: ModelIdentity
    goal: RecommendationGoal

    def __post_init__(self) -> None:
        if type(self.model) is not ModelIdentity:
            raise TypeError("model must be a ModelIdentity")
        object.__setattr__(self, "goal", RecommendationGoal(self.goal))


@dataclass(frozen=True, slots=True)
class ProfileIdentity:
    name: str
    kind: ProfileKind
    setting_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "profile name"))
        object.__setattr__(self, "kind", ProfileKind(self.kind))
        if self.setting_id is not None:
            object.__setattr__(self, "setting_id", _text(self.setting_id, "setting_id"))


_OVERRIDE_RULES: Final[dict[str, tuple[type, float | None, float | None]]] = {
    "layer_height_mm": (float, 0.01, 100.0),
    "wall_loops": (int, 1, 100),
    "sparse_infill_percent": (float, 0.0, 100.0),
    "sparse_infill_pattern": (str, None, None),
    "support_enablement": (bool, None, None),
    "support_type": (str, None, None),
    "support_threshold_angle_deg": (float, 0.0, 90.0),
    "outer_wall_speed_mms": (float, 0.01, 1000.0),
}


@dataclass(frozen=True, slots=True)
class AppliedOverride:
    """One validated, comparison-ready authoritative override."""

    setting: str
    value: bool | int | float | str
    canonical_value: str = ""

    def __post_init__(self) -> None:
        setting = _text(self.setting, "override setting")
        if setting not in RECOMMENDABLE_SETTINGS or setting not in _OVERRIDE_RULES:
            raise ValueError(f"unsupported recommendable setting: {setting}")
        expected, low, high = _OVERRIDE_RULES[setting]
        if expected is bool and type(self.value) is not bool:
            raise TypeError(f"{setting} requires a boolean value")
        if expected is int and (type(self.value) is not int or isinstance(self.value, bool)):
            raise TypeError(f"{setting} requires an integer value")
        if expected is float and (
            not isinstance(self.value, (int, float)) or isinstance(self.value, bool)
        ):
            raise TypeError(f"{setting} requires a numeric value")
        if expected is str and (not isinstance(self.value, str) or not self.value.strip()):
            raise TypeError(f"{setting} requires a non-blank string value")
        if expected in (int, float):
            number = _finite(float(self.value), setting)
            if (low is not None and number < low) or (high is not None and number > high):
                raise ValueError(f"{setting} is outside its supported range")
            value: bool | int | float | str = int(self.value) if expected is int else number
            canonical = str(value) if expected is int else format(number, ".15g")
        elif expected is bool:
            value, canonical = self.value, "true" if self.value else "false"
        else:
            assert isinstance(self.value, str)
            value, canonical = self.value.strip(), self.value.strip()
        object.__setattr__(self, "setting", setting)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "canonical_value", canonical)


@dataclass(frozen=True, slots=True)
class SelectedSetup:
    slicer: SlicerKind
    printer: ProfileIdentity
    nozzle_diameter_mm: float
    build_plate: str
    material: str
    filament_profile: ProfileIdentity
    process_profile: ProfileIdentity
    overrides: tuple[AppliedOverride, ...] = ()

    def __post_init__(self) -> None:
        if type(self.printer) is not ProfileIdentity:
            raise TypeError("printer must be a ProfileIdentity")
        if type(self.filament_profile) is not ProfileIdentity:
            raise TypeError("filament_profile must be a ProfileIdentity")
        if type(self.process_profile) is not ProfileIdentity:
            raise TypeError("process_profile must be a ProfileIdentity")
        object.__setattr__(self, "slicer", SlicerKind(self.slicer))
        object.__setattr__(
            self, "nozzle_diameter_mm", _finite(self.nozzle_diameter_mm, "nozzle_diameter_mm")
        )
        if self.nozzle_diameter_mm <= 0:
            raise ValueError("nozzle_diameter_mm must be positive")
        if self.printer.kind is not ProfileKind.PRINTER:
            raise ValueError("printer must be a printer profile")
        if self.filament_profile.kind is not ProfileKind.FILAMENT:
            raise ValueError("filament_profile must be a filament profile")
        if self.process_profile.kind is not ProfileKind.PROCESS:
            raise ValueError("process_profile must be a process profile")
        object.__setattr__(self, "build_plate", _text(self.build_plate, "build_plate"))
        object.__setattr__(self, "material", _text(self.material, "material"))
        overrides = _typed_tuple(self.overrides, AppliedOverride, "overrides")
        object.__setattr__(self, "overrides", overrides)
        if len({item.setting for item in self.overrides}) != len(self.overrides):
            raise ValueError("duplicate override settings are not allowed")


@dataclass(frozen=True, slots=True)
class PreparationAuthority:
    identity: PreparationIdentity
    selected_setup: SelectedSetup

    def __post_init__(self) -> None:
        if type(self.identity) is not PreparationIdentity:
            raise TypeError("identity must be a PreparationIdentity")
        if type(self.selected_setup) is not SelectedSetup:
            raise TypeError("selected_setup must be a SelectedSetup")


@dataclass(frozen=True, slots=True)
class ActualInputIdentity:
    """Immutable actual/effective inputs; never a second selected setup."""

    slicer: SlicerKind
    printer: ProfileIdentity | None
    nozzle_diameter_mm: float | None
    build_plate: str | None
    material: str | None
    filament_profile: ProfileIdentity | None
    process_profile: ProfileIdentity | None
    overrides: tuple[AppliedOverride, ...] = ()

    def __post_init__(self) -> None:
        for field in ("printer", "filament_profile", "process_profile"):
            value = getattr(self, field)
            if value is not None and type(value) is not ProfileIdentity:
                raise TypeError(f"{field} must be a ProfileIdentity or None")
        if self.printer is not None and self.printer.kind is not ProfileKind.PRINTER:
            raise ValueError("printer must be a printer profile")
        if (
            self.filament_profile is not None
            and self.filament_profile.kind is not ProfileKind.FILAMENT
        ):
            raise ValueError("filament_profile must be a filament profile")
        if (
            self.process_profile is not None
            and self.process_profile.kind is not ProfileKind.PROCESS
        ):
            raise ValueError("process_profile must be a process profile")
        object.__setattr__(self, "slicer", SlicerKind(self.slicer))
        if self.nozzle_diameter_mm is not None:
            nozzle = _finite(self.nozzle_diameter_mm, "nozzle_diameter_mm")
            if nozzle <= 0:
                raise ValueError("nozzle_diameter_mm must be positive")
            object.__setattr__(self, "nozzle_diameter_mm", nozzle)
        if self.build_plate is not None:
            object.__setattr__(self, "build_plate", _text(self.build_plate, "build_plate"))
        if self.material is not None:
            object.__setattr__(self, "material", _text(self.material, "material"))
        object.__setattr__(
            self, "overrides", _typed_tuple(self.overrides, AppliedOverride, "overrides")
        )
        if len({item.setting for item in self.overrides}) != len(self.overrides):
            raise ValueError("duplicate override settings are not allowed")

    def matches(self, setup: SelectedSetup) -> bool:
        return (
            self.slicer is setup.slicer
            and self.printer == setup.printer
            and self.nozzle_diameter_mm == setup.nozzle_diameter_mm
            and self.build_plate == setup.build_plate
            and self.material == setup.material
            and self.filament_profile == setup.filament_profile
            and self.process_profile == setup.process_profile
            and self.overrides == setup.overrides
        )


@dataclass(frozen=True, slots=True)
class EvidenceDetail:
    key: str
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _text(self.key, "evidence detail key"))
        object.__setattr__(self, "value", _text(self.value, "evidence detail value"))


@dataclass(frozen=True, slots=True)
class DeterministicEvidence:
    authority: EvidenceAuthority
    code: str
    value: str
    details: tuple[EvidenceDetail, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "authority", EvidenceAuthority(self.authority))
        object.__setattr__(self, "code", _text(self.code, "evidence code"))
        object.__setattr__(self, "value", _text(self.value, "evidence value"))
        object.__setattr__(
            self, "details", _typed_tuple(self.details, EvidenceDetail, "evidence details")
        )


@dataclass(frozen=True, slots=True)
class SliceRunIdentity:
    run_id: str
    slicer: SlicerKind
    result_reference: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _text(self.run_id, "slice run id"))
        object.__setattr__(self, "slicer", SlicerKind(self.slicer))
        if self.result_reference is not None:
            object.__setattr__(
                self, "result_reference", _text(self.result_reference, "result_reference")
            )


@dataclass(frozen=True, slots=True)
class SliceRepresentation:
    run: SliceRunIdentity
    succeeded: bool
    actual_inputs: ActualInputIdentity
    output_reference: str | None = None
    estimated_time_minutes: float | None = None
    filament_used_mm: float | None = None
    filament_used_cm3: float | None = None
    filament_weight_g: float | None = None
    layer_count: int | None = None
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.run) is not SliceRunIdentity:
            raise TypeError("run must be a SliceRunIdentity")
        if type(self.actual_inputs) is not ActualInputIdentity:
            raise TypeError("actual_inputs must be an ActualInputIdentity")
        if type(self.succeeded) is not bool:
            raise TypeError("succeeded must be a boolean")
        if self.output_reference is not None:
            object.__setattr__(
                self, "output_reference", _text(self.output_reference, "output_reference")
            )
        if self.run.slicer is not self.actual_inputs.slicer:
            raise ValueError("slice run and actual input slicer must match")
        for field in (
            "estimated_time_minutes",
            "filament_used_mm",
            "filament_used_cm3",
            "filament_weight_g",
        ):
            value = getattr(self, field)
            if value is not None and _finite(value, field) < 0:
                raise ValueError(f"{field} cannot be negative")
        if self.layer_count is not None and (
            type(self.layer_count) is not int or self.layer_count < 0
        ):
            raise ValueError("layer_count must be a non-negative integer")
        if self.succeeded and self.errors:
            raise ValueError("successful slice cannot contain errors")
        object.__setattr__(self, "warnings", _typed_tuple(self.warnings, str, "warnings"))
        object.__setattr__(self, "errors", _typed_tuple(self.errors, str, "errors"))


@dataclass(frozen=True, slots=True)
class FinalArtifactIdentity:
    path: Path
    slice_run_id: str
    sha256: str | None = None
    size_bytes: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.path, (str, Path)):
            raise TypeError("artifact path must be a string or Path")
        if isinstance(self.path, str) and not self.path.strip():
            raise ValueError("artifact path must be non-blank")
        object.__setattr__(self, "path", Path(self.path))
        if not str(self.path).strip():
            raise ValueError("artifact path must be non-blank")
        object.__setattr__(self, "slice_run_id", _text(self.slice_run_id, "slice_run_id"))
        if self.size_bytes is not None and (
            type(self.size_bytes) is not int or self.size_bytes < 0
        ):
            raise ValueError("artifact size must be a non-negative integer")
        if self.sha256 is not None:
            if not isinstance(self.sha256, str) or not re.fullmatch(
                r"[0-9a-fA-F]{64}", self.sha256
            ):
                raise ValueError("sha256 must be a 64-character hexadecimal digest")


@dataclass(frozen=True, slots=True)
class VerificationRepresentation:
    status: VerificationStatus
    expected_setup: SelectedSetup
    actual_inputs: ActualInputIdentity
    artifact_exists: bool | None = None
    compatibility: bool | None = None
    divergence: tuple[DeterministicEvidence, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.expected_setup) is not SelectedSetup:
            raise TypeError("expected_setup must be a SelectedSetup")
        if type(self.actual_inputs) is not ActualInputIdentity:
            raise TypeError("actual_inputs must be an ActualInputIdentity")
        for field in ("artifact_exists", "compatibility"):
            value = getattr(self, field)
            if value is not None and type(value) is not bool:
                raise TypeError(f"{field} must be a boolean or None")
        object.__setattr__(self, "status", VerificationStatus(self.status))
        object.__setattr__(
            self,
            "divergence",
            _typed_tuple(self.divergence, DeterministicEvidence, "divergence"),
        )
        object.__setattr__(self, "warnings", _typed_tuple(self.warnings, str, "warnings"))
        object.__setattr__(self, "errors", _typed_tuple(self.errors, str, "errors"))
        if self.status is VerificationStatus.PASS:
            if (
                self.artifact_exists is not True
                or self.compatibility is not True
                or not self.actual_inputs.matches(self.expected_setup)
                or self.divergence
                or self.errors
            ):
                raise ValueError(
                    "PASS verification requires existing compatible artifact and "
                    "no divergence/errors"
                )
        if self.status is VerificationStatus.BLOCKING_MISMATCH and not (
            self.divergence or self.errors
        ):
            raise ValueError("blocking mismatch requires divergence or errors")


@dataclass(frozen=True, slots=True)
class PreparationFailure:
    stage: FailureStage
    code: str
    message: str
    details: tuple[EvidenceDetail, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.stage, (FailureStage, str)):
            raise TypeError("stage must be a FailureStage")
        object.__setattr__(self, "stage", FailureStage(self.stage))
        object.__setattr__(self, "code", _text(self.code, "failure code"))
        object.__setattr__(self, "message", _text(self.message, "failure message"))
        object.__setattr__(
            self, "details", _typed_tuple(self.details, EvidenceDetail, "failure details")
        )


@dataclass(frozen=True, slots=True)
class ReadyPreparationResult:
    identity: PreparationIdentity
    selected_setup: SelectedSetup
    evidence: tuple[DeterministicEvidence, ...]
    slice_result: SliceRepresentation
    artifact: FinalArtifactIdentity
    verification: VerificationRepresentation

    def __post_init__(self) -> None:
        for field, expected in (
            ("identity", PreparationIdentity),
            ("selected_setup", SelectedSetup),
            ("slice_result", SliceRepresentation),
            ("artifact", FinalArtifactIdentity),
            ("verification", VerificationRepresentation),
        ):
            if type(getattr(self, field)) is not expected:
                raise TypeError(f"{field} has an invalid type")
        evidence = _typed_tuple(self.evidence, DeterministicEvidence, "evidence")
        if not evidence:
            raise ValueError("READY requires deterministic evidence")
        object.__setattr__(self, "evidence", evidence)
        if not self.slice_result.succeeded:
            raise ValueError("READY requires a successful slice")
        if self.verification.status is not VerificationStatus.PASS:
            raise ValueError("READY requires PASS verification")
        if self.artifact.slice_run_id != self.slice_result.run.run_id:
            raise ValueError("artifact must belong to the slice run")
        if self.verification.expected_setup != self.selected_setup:
            raise ValueError("verification must use the selected setup as expected authority")
        if self.verification.actual_inputs != self.slice_result.actual_inputs:
            raise ValueError("verification must describe the slice actual inputs")


@dataclass(frozen=True, slots=True)
class NotReadyPreparationResult:
    identity: PreparationIdentity
    failure: PreparationFailure
    evidence: tuple[DeterministicEvidence, ...] = ()
    selected_setup: SelectedSetup | None = None
    slice_result: SliceRepresentation | None = None
    verification: VerificationRepresentation | None = None

    def __post_init__(self) -> None:
        if type(self.identity) is not PreparationIdentity:
            raise TypeError("identity must be a PreparationIdentity")
        if type(self.failure) is not PreparationFailure:
            raise TypeError("failure must be a PreparationFailure")
        for field, expected in (
            ("selected_setup", SelectedSetup),
            ("slice_result", SliceRepresentation),
            ("verification", VerificationRepresentation),
        ):
            value = getattr(self, field)
            if value is not None and type(value) is not expected:
                raise TypeError(f"{field} has an invalid type")
        object.__setattr__(
            self, "evidence", _typed_tuple(self.evidence, DeterministicEvidence, "evidence")
        )
        if (
            self.slice_result is not None
            and self.slice_result.succeeded
            and self.failure.stage
            not in (
                FailureStage.ARTIFACT,
                FailureStage.FINAL_VERIFICATION,
            )
        ):
            raise ValueError(
                "only artifact or final-verification failures may retain a successful slice"
            )
        if self.verification is not None and self.verification.status is VerificationStatus.PASS:
            raise ValueError("NOT_READY cannot contain PASS verification")
        if self.verification is not None and self.slice_result is not None:
            if self.verification.actual_inputs != self.slice_result.actual_inputs:
                raise ValueError("verification must describe the retained slice actual inputs")


PreparationResult = ReadyPreparationResult | NotReadyPreparationResult
