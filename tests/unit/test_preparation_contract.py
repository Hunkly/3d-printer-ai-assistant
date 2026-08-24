"""Focused tests for the immutable preparation contract."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from print_engineer.core.preparation import (
    ActualInputIdentity,
    AppliedOverride,
    DeterministicEvidence,
    EvidenceAuthority,
    EvidenceDetail,
    FailureStage,
    FinalArtifactIdentity,
    ModelIdentity,
    NotReadyPreparationResult,
    PreparationFailure,
    PreparationIdentity,
    ProfileIdentity,
    ReadyPreparationResult,
    SelectedSetup,
    SliceRepresentation,
    SliceRunIdentity,
    VerificationRepresentation,
    VerificationStatus,
)
from print_engineer.core.recommendation import RecommendationGoal
from print_engineer.core.types import ProfileKind, SlicerKind


def _setup() -> SelectedSetup:
    return SelectedSetup(
        SlicerKind.ORCA_SLICER,
        ProfileIdentity("A1", ProfileKind.PRINTER, "a1"),
        0.4,
        "textured_pei",
        "PLA",
        ProfileIdentity("PLA", ProfileKind.FILAMENT, "pla"),
        ProfileIdentity("0.20 Standard", ProfileKind.PROCESS, "process"),
        (AppliedOverride("layer_height_mm", 0.16), AppliedOverride("wall_loops", 3)),
    )


def _identity() -> PreparationIdentity:
    return PreparationIdentity(
        ModelIdentity(Path("model.stl"), "a" * 64), RecommendationGoal.BALANCED
    )


def _actual(nozzle: float = 0.4) -> ActualInputIdentity:
    setup = _setup()
    return ActualInputIdentity(
        setup.slicer,
        setup.printer,
        nozzle,
        setup.build_plate,
        setup.material,
        setup.filament_profile,
        setup.process_profile,
        setup.overrides,
    )


def _slice(
    succeeded: bool = True, run_id: str = "run-1", nozzle: float = 0.4
) -> SliceRepresentation:
    return SliceRepresentation(
        SliceRunIdentity(run_id, SlicerKind.ORCA_SLICER),
        succeeded,
        _actual(nozzle),
        output_reference="out.3mf" if succeeded else None,
        layer_count=10,
        errors=() if succeeded else ("slice_failed",),
    )


def _verification(
    status: VerificationStatus = VerificationStatus.PASS, nozzle: float = 0.4
) -> VerificationRepresentation:
    if status is VerificationStatus.PASS:
        return VerificationRepresentation(status, _setup(), _actual(nozzle), True, True)
    return VerificationRepresentation(
        status,
        _setup(),
        _actual(nozzle),
        False,
        False,
        (
            DeterministicEvidence(
                EvidenceAuthority.VERIFICATION, "input_diverged", "nozzle_diameter_mm"
            ),
        ),
    )


def test_ready_requires_all_authoritative_success_components() -> None:
    result = ReadyPreparationResult(
        _identity(),
        _setup(),
        (
            DeterministicEvidence(
                EvidenceAuthority.SELECTION,
                "selected",
                "setup-1",
                (EvidenceDetail("source", "deterministic"),),
            ),
        ),
        _slice(),
        FinalArtifactIdentity(Path("out.3mf"), "run-1", "b" * 64, 12),
        _verification(),
    )
    assert result.identity.goal is RecommendationGoal.BALANCED
    assert result.artifact.slice_run_id == result.slice_result.run.run_id
    assert result.verification.status is VerificationStatus.PASS
    with pytest.raises(ValueError):
        ReadyPreparationResult(
            _identity(),
            _setup(),
            (),
            _slice(),
            FinalArtifactIdentity(Path("x"), "other"),
            _verification(),
        )


@pytest.mark.parametrize("component", ("setup", "slice", "artifact", "verification"))
def test_ready_rejects_each_missing_or_invalid_required_component(component: str) -> None:
    values: dict[str, object] = {
        "identity": _identity(),
        "selected_setup": _setup(),
        "evidence": (
            DeterministicEvidence(EvidenceAuthority.SELECTION, "selected", "setup-1"),
        ),
        "slice_result": _slice(),
        "artifact": FinalArtifactIdentity("out.3mf", "run-1"),
        "verification": _verification(),
    }
    if component == "setup":
        values["selected_setup"] = None
    elif component == "slice":
        values["slice_result"] = _slice(succeeded=False)
    elif component == "artifact":
        values["artifact"] = FinalArtifactIdentity("out.3mf", "other-run")
    else:
        values["verification"] = _verification(VerificationStatus.BLOCKING_MISMATCH)
    with pytest.raises((TypeError, ValueError)):
        ReadyPreparationResult(**values)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="deterministic evidence"):
        ReadyPreparationResult(
            _identity(),
            _setup(),
            (),
            _slice(),
            FinalArtifactIdentity(Path("out.3mf"), "run-1"),
            _verification(),
        )


@pytest.mark.parametrize("nozzle", [0, -0.1, float("nan"), float("inf"), -float("inf"), True])
def test_actual_input_rejects_invalid_nozzle_diameter(nozzle: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        _actual(nozzle)  # type: ignore[arg-type]


def test_actual_input_validates_nested_runtime_types() -> None:
    setup = _setup()
    with pytest.raises(TypeError):
        ActualInputIdentity(
            setup.slicer,
            object(),  # type: ignore[arg-type]
            0.4,
            setup.build_plate,
            setup.material,
            setup.filament_profile,
            setup.process_profile,
        )
    with pytest.raises(TypeError):
        ActualInputIdentity(
            setup.slicer,
            setup.printer,
            0.4,
            setup.build_plate,
            setup.material,
            setup.filament_profile,
            setup.process_profile,
            (object(),),  # type: ignore[arg-type]
        )


def test_not_ready_supports_failure_before_setup_and_preserves_no_downstream_authority() -> None:
    result = NotReadyPreparationResult(
        _identity(),
        PreparationFailure(FailureStage.MODEL_INPUT, "missing_model", "model unavailable"),
    )
    assert (
        result.selected_setup is None
        and result.slice_result is None
        and result.verification is None
    )
    with pytest.raises(ValueError):
        NotReadyPreparationResult(
            _identity(),
            PreparationFailure(FailureStage.SLICING, "failed", "no"),
            slice_result=_slice(),
        )
    retained = NotReadyPreparationResult(
        _identity(),
        PreparationFailure(FailureStage.ARTIFACT, "artifact_missing", "output missing"),
        selected_setup=_setup(),
        slice_result=_slice(),
        verification=_verification(VerificationStatus.BLOCKING_MISMATCH),
    )
    assert retained.slice_result is not None and retained.slice_result.succeeded
    with pytest.raises(ValueError):
        NotReadyPreparationResult(
            _identity(),
            PreparationFailure(FailureStage.FINAL_VERIFICATION, "bad", "no"),
            verification=_verification(),
        )


@pytest.mark.parametrize(
    "setting,value",
    [
        ("layer_height_mm", 0.01),
        ("wall_loops", 1),
        ("sparse_infill_percent", 100.0),
        ("sparse_infill_pattern", "gyroid"),
        ("support_enablement", True),
        ("support_type", "normal"),
        ("support_threshold_angle_deg", 90.0),
        ("outer_wall_speed_mms", 1000.0),
    ],
)
def test_every_allowlisted_override_is_typed_and_canonical(setting: str, value: object) -> None:
    override = AppliedOverride(setting, value)  # type: ignore[arg-type]
    assert override.canonical_value
    assert override.value == value


def test_override_validation_rejects_unsupported_types_ranges_and_duplicates() -> None:
    with pytest.raises(ValueError):
        AppliedOverride("nozzle_temperature", 200)
    with pytest.raises(TypeError):
        AppliedOverride("wall_loops", 2.5)
    with pytest.raises(ValueError):
        AppliedOverride("sparse_infill_percent", 101)
    with pytest.raises(TypeError):
        AppliedOverride("support_enablement", "true")
    setup = _setup()
    with pytest.raises(ValueError, match="duplicate"):
        SelectedSetup(
            setup.slicer,
            setup.printer,
            0.4,
            "plate",
            "PLA",
            setup.filament_profile,
            setup.process_profile,
            (AppliedOverride("wall_loops", 2), AppliedOverride("wall_loops", 3)),
        )


def test_selected_setup_is_the_only_authority_and_actual_inputs_can_diverge() -> None:
    actual = _actual(0.6)
    verification = _verification(VerificationStatus.BLOCKING_MISMATCH, 0.6)
    assert actual.nozzle_diameter_mm != _setup().nozzle_diameter_mm
    assert verification.status is VerificationStatus.BLOCKING_MISMATCH


def test_deep_immutability_and_source_isolation() -> None:
    details = [EvidenceDetail("k", "v")]
    evidence = DeterministicEvidence(EvidenceAuthority.WARNING, "warn", "yes", details)
    details.append(EvidenceDetail("changed", "bad"))
    assert len(evidence.details) == 1
    with pytest.raises(FrozenInstanceError):
        evidence.value = "changed"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        evidence.details.append(EvidenceDetail("x", "y"))  # type: ignore[attr-defined]


def test_nested_collections_are_validated_and_isolated() -> None:
    detail_source = [EvidenceDetail("k", "v")]
    evidence_source = [
        DeterministicEvidence(EvidenceAuthority.MODEL_FACTS, "model", "facts", detail_source)
    ]
    result = ReadyPreparationResult(
        _identity(),
        _setup(),
        evidence_source,
        _slice(),
        FinalArtifactIdentity("out.3mf", "run-1"),
        _verification(),
    )
    detail_source.append(EvidenceDetail("later", "changed"))
    evidence_source.append(DeterministicEvidence(EvidenceAuthority.WARNING, "w", "warning"))
    assert len(result.evidence) == 1
    assert len(result.evidence[0].details) == 1
    with pytest.raises(TypeError):
        DeterministicEvidence(EvidenceAuthority.WARNING, "bad", "value", ["not a detail"])  # type: ignore[list-item]
    with pytest.raises(TypeError):
        SliceRepresentation(
            SliceRunIdentity("run-1", SlicerKind.ORCA_SLICER),
            True,
            _actual(),
            warnings=[object()],  # type: ignore[list-item]
        )
    with pytest.raises(TypeError):
        VerificationRepresentation(
            VerificationStatus.PASS,
            _setup(),
            _actual(),
            True,
            True,
            divergence=[object()],  # type: ignore[list-item]
        )


def test_slice_numeric_and_boolean_runtime_constraints() -> None:
    with pytest.raises(ValueError):
        SliceRepresentation(
            SliceRunIdentity("run-1", SlicerKind.ORCA_SLICER), True, _actual(), layer_count=True
        )
    for value in (float("nan"), float("inf"), -float("inf"), -1):
        with pytest.raises((TypeError, ValueError)):
            SliceRepresentation(
                SliceRunIdentity("run-1", SlicerKind.ORCA_SLICER),
                True,
                _actual(),
                estimated_time_minutes=value,
            )


def test_profile_kinds_goal_and_explicit_verification_status_are_authoritative() -> None:
    assert _setup().printer.kind is ProfileKind.PRINTER
    assert tuple(goal.value for goal in RecommendationGoal) == (
        "surface_quality",
        "strength",
        "print_time",
        "filament_usage",
        "balanced",
    )
    with pytest.raises(ValueError):
        VerificationRepresentation(VerificationStatus.PASS, _setup(), _actual(), False, True)


def test_all_evidence_categories_are_explicit_and_immutable() -> None:
    categories = tuple(EvidenceAuthority)
    evidence = tuple(
        DeterministicEvidence(category, category.value, "known") for category in categories
    )
    assert tuple(item.authority for item in evidence) == categories
    assert all(type(item) is DeterministicEvidence for item in evidence)


@pytest.mark.parametrize(
    "stage",
    tuple(FailureStage),
)
def test_every_failure_stage_is_constructible_without_fake_downstream_data(
    stage: FailureStage,
) -> None:
    result = NotReadyPreparationResult(
        _identity(), PreparationFailure(stage, "failure", "truthful failure")
    )
    assert result.slice_result is None
    assert result.verification is None


def test_invalid_result_combinations_are_rejected() -> None:
    with pytest.raises(ValueError):
        NotReadyPreparationResult(
            _identity(),
            PreparationFailure(FailureStage.FINAL_VERIFICATION, "bad", "bad"),
            verification=_verification(),
        )
    with pytest.raises(ValueError):
        NotReadyPreparationResult(
            _identity(),
            PreparationFailure(FailureStage.SLICING, "bad", "bad"),
            selected_setup=_setup(),
            slice_result=_slice(),
        )


@pytest.mark.parametrize("identity_type", (ModelIdentity, FinalArtifactIdentity))
@pytest.mark.parametrize("path", ("", "   ", "\t\n"))
def test_blank_path_identities_are_rejected(identity_type: type, path: str) -> None:
    with pytest.raises(ValueError, match="non-blank"):
        if identity_type is ModelIdentity:
            ModelIdentity(path)
        else:
            FinalArtifactIdentity(path, "run-1")


def test_explicit_non_blank_path_identity_semantics_are_preserved() -> None:
    model = ModelIdentity(" ./model.stl ")
    artifact = FinalArtifactIdentity(Path("."), "run-1")
    assert model.path == Path(" ./model.stl ")
    assert artifact.path == Path(".")


def test_authoritative_nested_values_are_immutable_on_repeated_access() -> None:
    result = ReadyPreparationResult(
        _identity(),
        _setup(),
        (DeterministicEvidence(EvidenceAuthority.SELECTION, "selected", "setup-1"),),
        _slice(),
        FinalArtifactIdentity("out.3mf", "run-1"),
        _verification(),
    )
    first = result.evidence
    second = result.evidence
    assert first == second
    assert first is second
    with pytest.raises(TypeError):
        first[0] = DeterministicEvidence(EvidenceAuthority.WARNING, "x", "y")  # type: ignore[index]
    with pytest.raises(AttributeError):
        result.slice_result.warnings.append("changed")  # type: ignore[attr-defined]
    assert result.evidence[0].value == "setup-1"
    assert result.slice_result.warnings == ()


@pytest.mark.parametrize(
    "factory",
    (
        lambda: EvidenceDetail("key", object()),
        lambda: DeterministicEvidence(EvidenceAuthority.MODEL_FACTS, "code", "value", [object()]),
        lambda: SliceRepresentation(
            SliceRunIdentity("run-1", SlicerKind.ORCA_SLICER), True, _actual(), warnings=[object()]
        ),
        lambda: SliceRepresentation(
            SliceRunIdentity("run-1", SlicerKind.ORCA_SLICER), True, _actual(), errors=[object()]
        ),
        lambda: ActualInputIdentity(
            SlicerKind.ORCA_SLICER, object(), 0.4, "plate", "PLA", None, None
        ),
        lambda: ActualInputIdentity(
            SlicerKind.ORCA_SLICER, None, 0.4, "plate", "PLA", None, None, [object()]
        ),
        lambda: SelectedSetup(
            SlicerKind.ORCA_SLICER,
            _setup().printer,
            0.4,
            "plate",
            "PLA",
            _setup().filament_profile,
            _setup().process_profile,
            [object()],
        ),
        lambda: VerificationRepresentation(
            VerificationStatus.BLOCKING_MISMATCH,
            _setup(),
            _actual(),
            divergence=[object()],
        ),
    ),
)
def test_malformed_nested_collection_and_identity_members_are_rejected(factory: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()  # type: ignore[operator]


@pytest.mark.parametrize(
    "artifact_run, slice_run",
    (("other-run", "run-1"), ("run-1", "other-run")),
)
def test_artifact_and_slice_run_identity_relationship_is_enforced(
    artifact_run: str, slice_run: str
) -> None:
    with pytest.raises(ValueError):
        ReadyPreparationResult(
            _identity(),
            _setup(),
            (DeterministicEvidence(EvidenceAuthority.SELECTION, "selected", "setup-1"),),
            _slice(run_id=slice_run),
            FinalArtifactIdentity("out.3mf", artifact_run),
            _verification(),
        )
