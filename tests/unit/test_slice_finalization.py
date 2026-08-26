"""Hermetic tests for Increment 4 post-slice finalization."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from print_engineer.adapters.slicer import finalization
from print_engineer.adapters.slicer.execution import (
    CandidateSliceArtifact,
    ObservedSliceFacts,
    SliceExecutionSuccess,
)
from print_engineer.adapters.slicer.finalization import finalize_slice
from print_engineer.core.preparation import (
    ActualInputIdentity,
    ModelIdentity,
    NotReadyPreparationResult,
    PreparationAuthority,
    PreparationIdentity,
    ProfileIdentity,
    ReadyPreparationResult,
    SelectedSetup,
)
from print_engineer.core.recommendation import RecommendationGoal
from print_engineer.core.types import ProfileKind, SlicerKind


def _digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _success(
    tmp_path: Path,
    *,
    name: str = "OrcaSlicer",
    version: str = "2.3.2",
    goal: RecommendationGoal = RecommendationGoal.BALANCED,
    workspace_name: str = "workspace",
) -> SliceExecutionSuccess:
    workspace = tmp_path / workspace_name
    workspace.mkdir()
    configs = {
        "printer": {"name": "Bambu Lab A1", "nozzle_diameter": "0.4"},
        "process": {"name": "0.20mm Standard @BBL A1", "setting_id": "GP079"},
        "filament": {"name": "Bambu PLA Tough+", "setting_id": "base"},
    }
    for key, value in configs.items():
        (workspace / f"{key}.realized.json").write_bytes(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        )
    candidate = b"; total layer number: 4\nG1 X1 Y1\n"
    (workspace / "plate_1.gcode").write_bytes(candidate)
    printer = ProfileIdentity("Bambu Lab A1", ProfileKind.PRINTER, "GM030")
    process = ProfileIdentity("0.20mm Standard @BBL A1", ProfileKind.PROCESS, "GP079")
    filament = ProfileIdentity("Bambu PLA Tough+", ProfileKind.FILAMENT, "base")
    setup = SelectedSetup(
        SlicerKind.ORCA_SLICER, printer, 0.4, "cool_plate", "PLA", filament, process
    )
    actual = ActualInputIdentity(
        SlicerKind.ORCA_SLICER, printer, 0.4, "cool_plate", "PLA", filament, process
    )
    authority = PreparationAuthority(
        PreparationIdentity(ModelIdentity(tmp_path / "source.stl", "a" * 64), goal), setup
    )
    return SliceExecutionSuccess(
        "run-1", "realization-1", authority, actual, name, version, workspace,
        _digest(configs["printer"]), _digest(configs["process"]), _digest(configs["filament"]),
        CandidateSliceArtifact(
            "run-1", workspace / "plate_1.gcode", "gcode",
            hashlib.sha256(candidate).hexdigest(), len(candidate)
        ),
        ObservedSliceFacts(1, 4, 12.0, 0.8, 100.0, 1.2, 1.24),
    )


def _not_ready(
    success: SliceExecutionSuccess, code: str, *, workspace_retained: bool = True
) -> NotReadyPreparationResult:
    result = finalize_slice(success)
    assert isinstance(result, NotReadyPreparationResult)
    assert result.failure.code == code
    assert result.identity is success.preparation_authority.identity
    assert success.workspace_path.exists() is workspace_retained
    return result


def _make_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"host cannot create symlink: {exc}")


def test_valid_ready_preserves_authority_complete_facts_and_fresh_artifact(tmp_path: Path) -> None:
    success = _success(tmp_path)
    retained = {
        path: path.read_bytes()
        for path in (
            success.workspace_path / "plate_1.gcode",
            success.workspace_path / "printer.realized.json",
            success.workspace_path / "process.realized.json",
            success.workspace_path / "filament.realized.json",
        )
    }
    result = finalize_slice(success)
    assert isinstance(result, ReadyPreparationResult)
    assert success.workspace_path.is_dir()
    assert all(path.is_file() for path in retained)
    assert {path: path.read_bytes() for path in retained} == retained
    assert result.identity is success.preparation_authority.identity
    assert result.selected_setup is success.preparation_authority.selected_setup
    assert result.slice_result.actual_inputs is success.actual_input_identity
    facts = success.observed_facts
    assert facts == ObservedSliceFacts(1, 4, 12.0, 0.8, 100.0, 1.2, 1.24)
    # The published READY contract intentionally projects only these four facts.
    assert result.slice_result.layer_count == facts.layer_count
    assert result.slice_result.estimated_time_minutes == facts.time_minutes
    assert result.slice_result.filament_used_mm == facts.filament_used_mm
    assert result.slice_result.filament_used_cm3 == facts.filament_used_cm3
    assert facts.plate_number == 1
    assert facts.max_z_height == 0.8
    assert facts.filament_density == 1.24
    assert success.observed_facts == facts
    assert any(item.code == "facts_reused" for item in result.evidence)
    assert result.artifact.sha256 == hashlib.sha256(
        success.candidate_artifact.path.read_bytes()
    ).hexdigest()
    assert result.artifact.size_bytes == success.candidate_artifact.path.stat().st_size


def test_portable_stat_without_file_attributes_continues_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    success = _success(tmp_path)
    real_lstat = os.lstat

    class PortableStat:
        def __init__(self, mode: int) -> None:
            self.st_mode = mode

    def portable_lstat(path: str | os.PathLike[str]) -> PortableStat:
        return PortableStat(real_lstat(path).st_mode)

    monkeypatch.setattr(finalization.os, "lstat", portable_lstat)
    assert isinstance(finalize_slice(success), ReadyPreparationResult)


def test_candidate_artifact_rejects_non_gcode() -> None:
    with pytest.raises(ValueError):
        CandidateSliceArtifact("run", Path("plate"), "3mf", "a" * 64, 1)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [("missing", "candidate_missing"), ("non_file", "candidate_not_file"),
     ("empty", "candidate_empty"), ("size", "candidate_size_mismatch"),
     ("hash", "candidate_hash_mismatch")],
)
def test_candidate_integrity_matrix(tmp_path: Path, mutation: str, expected: str) -> None:
    success = _success(tmp_path)
    path = success.workspace_path / "plate_1.gcode"
    if mutation == "missing":
        path.unlink()
    elif mutation == "non_file":
        path.unlink()
        path.mkdir()
    elif mutation == "empty":
        path.write_bytes(b"")
    elif mutation == "size":
        path.write_bytes(path.read_bytes() + b"x")
    else:
        path.write_bytes(b"; total layer number: 4\nG1 X2 Y2\n")
    _not_ready(success, expected)


@pytest.mark.parametrize(
    ("kind", "expected"), [("path", "candidate_path_mismatch"), ("run", "candidate_run_mismatch")]
)
def test_candidate_correlation_matrix(tmp_path: Path, kind: str, expected: str) -> None:
    success = _success(tmp_path)
    artifact = (
        replace(success.candidate_artifact, path=success.workspace_path / "else.gcode")
        if kind == "path"
        else replace(success.candidate_artifact, slice_run_id="other-run")
    )
    _not_ready(replace(success, candidate_artifact=artifact), expected)


def test_candidate_outside_workspace_is_rejected(tmp_path: Path) -> None:
    success = _success(tmp_path)
    outside = tmp_path / "outside.gcode"
    outside.write_bytes(success.candidate_artifact.path.read_bytes())
    _not_ready(
        replace(success, candidate_artifact=replace(success.candidate_artifact, path=outside)),
        "candidate_path_mismatch",
    )


def test_candidate_symlink_is_rejected(tmp_path: Path) -> None:
    success = _success(tmp_path)
    path = success.workspace_path / "plate_1.gcode"
    target = tmp_path / "outside.gcode"
    target.write_bytes(path.read_bytes())
    path.unlink()
    _make_symlink(path, target)
    _not_ready(success, "candidate_reparse_or_unsafe")


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [("missing", "workspace_missing"), ("file", "workspace_not_directory")],
)
def test_workspace_matrix(tmp_path: Path, mutation: str, expected: str) -> None:
    success = _success(tmp_path)
    workspace = success.workspace_path
    if mutation == "missing":
        # I4 performs no deletion; a deliberately deleted workspace cannot be retained.
        for child in workspace.iterdir():
            child.unlink()
        workspace.rmdir()
    else:
        workspace.rename(tmp_path / "workspace-old")
        workspace.write_bytes(b"not a directory")
    _not_ready(success, expected, workspace_retained=mutation != "missing")


def test_workspace_symlink_is_rejected(tmp_path: Path) -> None:
    success = _success(tmp_path)
    old = tmp_path / "workspace-real"
    success.workspace_path.rename(old)
    _make_symlink(success.workspace_path, old)
    _not_ready(success, "workspace_reparse_or_unsafe")


def test_workspace_replacement_with_incomplete_evidence_is_not_ready(tmp_path: Path) -> None:
    success = _success(tmp_path)
    old = tmp_path / "workspace-old"
    success.workspace_path.rename(old)
    success.workspace_path.mkdir()
    before = tuple(success.workspace_path.iterdir())
    _not_ready(success, "candidate_missing")
    assert success.workspace_path.is_dir()
    assert tuple(success.workspace_path.iterdir()) == before


@pytest.mark.parametrize(
    ("role", "mutation", "expected"),
    [(role, mutation, f"{role}_config_{suffix}")
     for role in ("printer", "process", "filament")
     for mutation, suffix in (("missing", "missing"), ("non_file", "not_file"),
                              ("malformed", "invalid"), ("wrong_root", "invalid"),
                              ("identity", "identity_mismatch"))],
)
def test_config_matrix(tmp_path: Path, role: str, mutation: str, expected: str) -> None:
    success = _success(tmp_path)
    path = success.workspace_path / f"{role}.realized.json"
    if mutation == "missing":
        path.unlink()
    elif mutation == "non_file":
        path.unlink()
        path.mkdir()
    elif mutation == "malformed":
        path.write_text("{", encoding="utf-8")
    elif mutation == "wrong_root":
        path.write_text("[]", encoding="utf-8")
    else:
        path.write_text("{}", encoding="utf-8")
    _not_ready(success, expected)


@pytest.mark.parametrize("role", ["printer", "process", "filament"])
def test_config_symlink_escape_is_rejected(tmp_path: Path, role: str) -> None:
    success = _success(tmp_path)
    path = success.workspace_path / f"{role}.realized.json"
    target = tmp_path / f"outside-{role}.json"
    target.write_bytes(path.read_bytes())
    path.unlink()
    _make_symlink(path, target)
    _not_ready(success, f"{role}_config_reparse_or_unsafe")


@pytest.mark.parametrize("role", ["printer", "process", "filament"])
def test_config_external_decoy_is_not_discovered(
    tmp_path: Path, role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Config paths are fixed workspace children, not caller-supplied fields.

    An arbitrary external config path cannot be represented through the supported
    I4 API. This equivalent public-boundary proof removes the exact workspace
    child and places a valid same-name decoy outside it, proving no external
    lookup or fallback occurs.
    """
    success = _success(tmp_path)
    expected = success.workspace_path / f"{role}.realized.json"
    decoy = tmp_path / f"{role}.realized.json"
    decoy.write_bytes(expected.read_bytes())
    decoy_before = decoy.read_bytes()
    expected.unlink()
    monkeypatch.chdir(tmp_path)

    _not_ready(success, f"{role}_config_missing")

    assert decoy.read_bytes() == decoy_before


@pytest.mark.parametrize(
    ("name", "version"),
    [("orca", "2.3.2"), ("OrcaSlicer", "2.3.3"), ("OrcaSlicer", ""), ("OrcaSlicer", "9.9")],
)
def test_unsupported_slicer_identity(tmp_path: Path, name: str, version: str) -> None:
    _not_ready(_success(tmp_path, name=name, version=version), "unsupported_slicer_version")


def test_source_mutation_does_not_affect_finalization(tmp_path: Path) -> None:
    success = _success(tmp_path)
    success.preparation_authority.identity.model.path.write_bytes(b"changed")
    assert isinstance(finalize_slice(success), ReadyPreparationResult)


def test_different_goals_have_distinct_preparation_authority_not_artifact_identity(
    tmp_path: Path,
) -> None:
    balanced_success = _success(tmp_path, goal=RecommendationGoal.BALANCED)
    quality_success = replace(
        balanced_success,
        preparation_authority=PreparationAuthority(
            PreparationIdentity(
                balanced_success.preparation_authority.identity.model,
                RecommendationGoal.SURFACE_QUALITY,
            ),
            balanced_success.preparation_authority.selected_setup,
        ),
    )
    balanced = finalize_slice(balanced_success)
    quality = finalize_slice(quality_success)
    assert isinstance(balanced, ReadyPreparationResult)
    assert isinstance(quality, ReadyPreparationResult)
    assert balanced_success.workspace_path == quality_success.workspace_path
    assert balanced_success.candidate_artifact.path == quality_success.candidate_artifact.path
    assert balanced_success.slice_run_id == quality_success.slice_run_id
    assert (
        balanced_success.preparation_authority.identity
        != quality_success.preparation_authority.identity
    )
    assert balanced_success.preparation_authority != quality_success.preparation_authority
    assert balanced.identity != quality.identity
    assert balanced.selected_setup == quality.selected_setup
    assert balanced.artifact == quality.artifact
    assert balanced.identity.goal is RecommendationGoal.BALANCED
    assert quality.identity.goal is RecommendationGoal.SURFACE_QUALITY


def test_not_ready_retains_mutated_candidate_and_all_configs(tmp_path: Path) -> None:
    success = _success(tmp_path)
    candidate = success.workspace_path / "plate_1.gcode"
    mutated = b"mutated candidate bytes"
    candidate.write_bytes(mutated)
    config_bytes = {
        role: (success.workspace_path / f"{role}.realized.json").read_bytes()
        for role in ("printer", "process", "filament")
    }

    result = _not_ready(success, "candidate_size_mismatch")

    assert isinstance(result, NotReadyPreparationResult)
    assert success.workspace_path.is_dir()
    assert candidate.read_bytes() == mutated
    assert all(
        (success.workspace_path / f"{role}.realized.json").read_bytes() == content
        for role, content in config_bytes.items()
    )


def test_not_ready_retains_mutated_config_and_other_evidence(tmp_path: Path) -> None:
    success = _success(tmp_path)
    mutated_config = success.workspace_path / "printer.realized.json"
    mutated_config.write_text("{}", encoding="utf-8")
    candidate_before = (success.workspace_path / "plate_1.gcode").read_bytes()
    other_configs = {
        role: (success.workspace_path / f"{role}.realized.json").read_bytes()
        for role in ("process", "filament")
    }

    _not_ready(success, "printer_config_identity_mismatch")

    assert success.workspace_path.is_dir()
    assert mutated_config.read_text(encoding="utf-8") == "{}"
    assert (success.workspace_path / "plate_1.gcode").read_bytes() == candidate_before
    assert all(
        (success.workspace_path / f"{role}.realized.json").read_bytes() == content
        for role, content in other_configs.items()
    )


def test_repeated_ready_and_not_ready_are_stateless(tmp_path: Path) -> None:
    success = _success(tmp_path)
    first, second = finalize_slice(success), finalize_slice(success)
    assert first == second and isinstance(first, ReadyPreparationResult)
    (success.workspace_path / "plate_1.gcode").write_bytes(b"changed")
    failed = finalize_slice(success)
    assert failed == finalize_slice(success)
    assert isinstance(failed, NotReadyPreparationResult)
    assert failed.failure.code == "candidate_size_mismatch"
    assert success.workspace_path.exists()


def test_deep_immutability_and_no_extra_authority_arguments(tmp_path: Path) -> None:
    result = finalize_slice(_success(tmp_path))
    assert isinstance(result, ReadyPreparationResult)
    with pytest.raises(FrozenInstanceError):
        result.evidence = ()  # type: ignore[misc]
    assert isinstance(result.artifact.path, Path)
