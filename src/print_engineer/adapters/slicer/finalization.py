"""Deterministic, read-only final verification of a successful slice."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from print_engineer.adapters.slicer.execution import (
    SliceExecutionSuccess,
    _canonical,
    _digest_bytes,
)
from print_engineer.core.preparation import (
    DeterministicEvidence,
    EvidenceAuthority,
    EvidenceDetail,
    FailureStage,
    FinalArtifactIdentity,
    NotReadyPreparationResult,
    PreparationFailure,
    PreparationResult,
    ReadyPreparationResult,
    SliceRepresentation,
    SliceRunIdentity,
    VerificationRepresentation,
    VerificationStatus,
)
from print_engineer.core.types import SlicerKind

_SUPPORTED_SLICER = "OrcaSlicer"
_SUPPORTED_VERSION = "2.3.2"
_CANDIDATE_NAME = "plate_1.gcode"
_CONFIG_NAMES = ("printer", "process", "filament")


class _FinalizationFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.fspath(_lexical(left))) == os.path.normcase(
        os.fspath(_lexical(right))
    )


def _unsafe(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(
        attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _contained(path: Path, workspace: Path) -> bool:
    try:
        _lexical(path).relative_to(_lexical(workspace))
    except ValueError:
        return False
    return True


def _resolve_contained(path: Path, workspace: Path, code: str, missing_code: str) -> Path:
    if not _contained(path, workspace):
        raise _FinalizationFailure(code, f"{path} is outside the expected workspace")
    if _unsafe(path):
        raise _FinalizationFailure(
            code.replace("_path_mismatch", "_reparse_or_unsafe"), f"unsafe path: {path}"
        )
    if not path.exists():
        raise _FinalizationFailure(missing_code, f"missing path: {path}")
    try:
        resolved_workspace = workspace.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        raise _FinalizationFailure(code, str(exc)) from exc
    if not _contained(resolved, resolved_workspace):
        raise _FinalizationFailure(code, f"{path} resolves outside the workspace")
    return resolved


def _evidence(code: str, value: str, **details: str) -> DeterministicEvidence:
    return DeterministicEvidence(
        EvidenceAuthority.VERIFICATION,
        code,
        value,
        tuple(EvidenceDetail(key, item) for key, item in details.items()),
    )


class SliceFinalizer:
    """Finalize exactly one constructor-validated slice success record."""

    def finalize(self, success: SliceExecutionSuccess) -> PreparationResult:
        identity = success.preparation_authority.identity
        setup = success.preparation_authority.selected_setup
        try:
            workspace = _lexical(success.workspace_path)
            try:
                workspace_info = os.lstat(workspace)
            except FileNotFoundError as exc:
                raise _FinalizationFailure("workspace_missing", str(exc)) from exc
            workspace_attributes = getattr(workspace_info, "st_file_attributes", 0)
            if stat.S_ISLNK(workspace_info.st_mode) or bool(
                workspace_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise _FinalizationFailure("workspace_reparse_or_unsafe", "workspace is unsafe")
            if not stat.S_ISDIR(workspace_info.st_mode):
                raise _FinalizationFailure(
                    "workspace_not_directory", "workspace is not a directory"
                )
            workspace = workspace.resolve(strict=True)

            if success.candidate_artifact.slice_run_id != success.slice_run_id:
                raise _FinalizationFailure(
                    "candidate_run_mismatch", "candidate belongs to another slice run"
                )
            candidate = workspace / _CANDIDATE_NAME
            if not _same_path(success.candidate_artifact.path, candidate):
                raise _FinalizationFailure(
                    "candidate_path_mismatch", "candidate path is not plate_1.gcode"
                )
            candidate = _resolve_contained(
                candidate, workspace, "candidate_path_mismatch", "candidate_missing"
            )
            if not candidate.is_file():
                raise _FinalizationFailure("candidate_not_file", "candidate is not a regular file")
            try:
                content = candidate.read_bytes()
            except FileNotFoundError as exc:
                raise _FinalizationFailure("candidate_missing", str(exc)) from exc
            except OSError as exc:
                raise _FinalizationFailure("candidate_not_file", str(exc)) from exc
            size = len(content)
            if size <= 0:
                raise _FinalizationFailure("candidate_empty", "candidate is empty")
            if size != success.candidate_artifact.byte_size:
                raise _FinalizationFailure("candidate_size_mismatch", "candidate byte size differs")
            digest = _digest_bytes(content)
            if digest != success.candidate_artifact.sha256:
                raise _FinalizationFailure("candidate_hash_mismatch", "candidate SHA-256 differs")

            config_ids = (
                success.printer_config_identity,
                success.process_config_identity,
                success.filament_config_identity,
            )
            config_evidence: list[DeterministicEvidence] = []
            for name, expected_id in zip(_CONFIG_NAMES, config_ids, strict=True):
                path = _resolve_contained(
                    workspace / f"{name}.realized.json",
                    workspace,
                    f"{name}_config_path_mismatch",
                    f"{name}_config_missing",
                )
                if not path.is_file():
                    raise _FinalizationFailure(
                        f"{name}_config_not_file", f"{path.name} is not a regular file"
                    )
                try:
                    parsed = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise _FinalizationFailure(f"{name}_config_invalid", str(exc)) from exc
                if not isinstance(parsed, dict):
                    raise _FinalizationFailure(
                        f"{name}_config_invalid", f"{path.name} root is not an object"
                    )
                actual_id = _digest_bytes(_canonical(parsed))
                if actual_id != expected_id:
                    raise _FinalizationFailure(
                        f"{name}_config_identity_mismatch", f"{path.name} identity differs"
                    )
                config_evidence.append(_evidence(f"{name}_config_verified", actual_id))

            if (
                success.slicer_name != _SUPPORTED_SLICER
                or success.slicer_version != _SUPPORTED_VERSION
            ):
                raise _FinalizationFailure(
                    "unsupported_slicer_version", "unsupported slicer identity"
                )

            artifact = FinalArtifactIdentity(candidate, success.slice_run_id, digest, size)
            facts = success.observed_facts
            slice_result = SliceRepresentation(
                SliceRunIdentity(success.slice_run_id, SlicerKind.ORCA_SLICER, str(candidate)),
                True,
                success.actual_input_identity,
                str(candidate),
                facts.time_minutes,
                facts.filament_used_mm,
                facts.filament_used_cm3,
                None,
                facts.layer_count,
            )
            verification = VerificationRepresentation(
                VerificationStatus.PASS, setup, success.actual_input_identity, True, True
            )
            evidence = (
                _evidence("workspace_verified", str(workspace)),
                _evidence("candidate_verified", digest, size_bytes=str(size)),
                *config_evidence,
                _evidence("slicer_verified", f"{success.slicer_name} {success.slicer_version}"),
                _evidence("facts_reused", "candidate_sha256_correlated"),
            )
            return ReadyPreparationResult(
                identity, setup, evidence, slice_result, artifact, verification
            )
        except _FinalizationFailure as failure:
            return NotReadyPreparationResult(
                identity,
                PreparationFailure(FailureStage.FINAL_VERIFICATION, failure.code, failure.message),
                evidence=(_evidence("finalization_failed", failure.code),),
                selected_setup=setup,
            )


def finalize_slice(success: SliceExecutionSuccess) -> PreparationResult:
    return SliceFinalizer().finalize(success)
