"""Bounded execution of an already-realized Increment 2 slice."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from print_engineer.adapters.slicer.base import SUPPORTED_INPUT_SUFFIXES
from print_engineer.adapters.slicer.gcode import parse_gcode
from print_engineer.adapters.slicer.orca import OrcaSlicerAdapter
from print_engineer.adapters.slicer.realization import (
    EffectiveSliceInputs,
    ProfileReference,
    RealizationResource,
    RealizationResult,
)
from print_engineer.core.preparation import ActualInputIdentity, ModelIdentity, SelectedSetup
from print_engineer.core.types import (
    ProfileInfo,
    ProfileSource,
    RealizedConfigPaths,
    SliceJob,
    SlicerKind,
)
from print_engineer.errors import SlicerError, SlicerNotInstalled, SlicerUnavailable, SliceTimeout


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: object) -> str:
    return _digest_bytes(_canonical(value))


_GCODE_COMMAND_RE = re.compile(r"^[GMTgmt][0-9]+(?:\.[0-9]+)?$")
_GCODE_PARAMETER_RE = re.compile(
    r"^[A-Za-z][-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$"
)
_GCODE_BARE_FLAGS: dict[str, frozenset[str]] = {
    "g28": frozenset({"X"}),
    "m1006": frozenset({"W"}),
    "m17": frozenset({"D", "R", "S"}),
    "m18": frozenset({"X", "Y", "Z"}),
    "m211": frozenset({"R", "S"}),
    "m620": frozenset({"M"}),
    "m620.1": frozenset({"E"}),
    "m900": frozenset({"C", "R", "S"}),
}
_M1002_TEXT_RE = (
    r"(?:gcode_claim_action\s*:\s*[0-9]+|"
    r"set_gcode_claim_speed_level\s*:\s*[0-9]+|"
    r"set_filament_type:[A-Za-z0-9_-]+|"
    r"judge_flag [A-Za-z0-9_]+|"
    r"judge_last_extrude_cali_success)"
)
_GCODE_M1002_RE = re.compile(rf"^M1002\s+{_M1002_TEXT_RE}$", re.IGNORECASE)
_GCODE_M620_RE = re.compile(r"^M62[01]\s+S(?:0A|255)$", re.IGNORECASE)
_GCODE_M624_RE = re.compile(
    r"^M624\s+(?=[A-Za-z0-9+/=]+$)(?:[A-Za-z0-9+/]{4})*"
    r"(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$",
    re.IGNORECASE,
)


def _is_structurally_valid_gcode(content: bytes) -> bool:
    """Recognize the smallest useful Orca G-Code structure.

    ``parse_gcode`` intentionally parses optional comments and returns a
    mapping even for arbitrary text.  A real command line is therefore the
    execution-level boundary between malformed output and a structurally
    acceptable file whose required facts may still be absent.
    """
    text = content.decode("utf-8", errors="replace")
    command_found = False
    for line in text.splitlines():
        code = line.split(";", 1)[0].strip()
        if not code:
            continue
        tokens = code.split()
        if not tokens or _GCODE_COMMAND_RE.fullmatch(tokens[0]) is None:
            return False
        command_found = True
        command = tokens[0].lower()
        if command == "m1002":
            if _GCODE_M1002_RE.fullmatch(code) is None:
                return False
            continue
        if command in {"m620", "m621"}:
            if _GCODE_M620_RE.fullmatch(code):
                continue
            if "." not in command and not (
                command == "m620"
                and tokens[1:]
                and all(token.upper() in _GCODE_BARE_FLAGS[command] for token in tokens[1:])
            ):
                return False
        if command == "m624":
            if _GCODE_M624_RE.fullmatch(code):
                continue
            return False
        if command == "g28" and len(tokens) == 4 and tokens[1].upper() == "Z":
            if (
                tokens[2][:1].upper() == "P"
                and tokens[3][:1].upper() == "T"
                and _GCODE_PARAMETER_RE.fullmatch(tokens[2]) is not None
                and _GCODE_PARAMETER_RE.fullmatch(tokens[3]) is not None
            ):
                continue
            return False
        allowed_flags = _GCODE_BARE_FLAGS.get(command, frozenset())
        for token in tokens[1:]:
            if len(token) == 1 and token.upper() in allowed_flags:
                continue
            if _GCODE_PARAMETER_RE.fullmatch(token) is None:
                return False
    return command_found


def _resource_identity(
    kind: str,
    inputs: EffectiveSliceInputs,
    ref: ProfileReference,
    base: dict[str, object],
) -> str:
    overlay = (
        inputs.printer_overlay
        if kind == "printer"
        else inputs.process_overlay
        if kind == "process"
        else ()
    )
    return _digest(
        {
            "capability": inputs.capability,
            "kind": kind,
            "reference": {
                "identity": {
                    "name": ref.identity.name,
                    "kind": ref.identity.kind.value,
                    "setting_id": ref.identity.setting_id,
                },
                "materialized_name": ref.materialized_name,
                "content_sha256": ref.content_sha256,
            },
            "content": base,
            "overlay": [(entry.key, entry.value, entry.layer, entry.units) for entry in overlay],
        }
    )


def _verify_resource_authority(
    inputs: EffectiveSliceInputs,
    selected_setup: SelectedSetup,
    resources: tuple[RealizationResource, ...],
) -> None:
    """Verify the exact resources certified by Increment 2.

    This deliberately consumes only the realization object.  It does not
    resolve a profile by name or consult a repository.
    """
    if len(resources) != 3:
        raise ValueError("realization does not contain three resources")
    refs = (inputs.printer, inputs.process, inputs.filament)
    kinds = ("printer", "process", "filament")
    selected = (
        selected_setup.printer,
        selected_setup.process_profile,
        selected_setup.filament_profile,
    )
    actual = (
        inputs.actual_inputs.printer,
        inputs.actual_inputs.process_profile,
        inputs.actual_inputs.filament_profile,
    )
    for kind, ref, resource, selected_identity, actual_identity in zip(
        kinds, refs, resources, selected, actual, strict=True
    ):
        if resource.kind != kind:
            raise ValueError(f"{kind} resource kind does not match realization")
        if ref.identity != selected_identity or ref.identity != actual_identity:
            raise ValueError(f"{kind} profile identity does not match selected authority")
        if ref.materialized_name != selected_identity.name:
            raise ValueError(f"{kind} materialized name does not match selected authority")
        try:
            base = json.loads(ref.content)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"{kind} profile content is invalid: {exc}") from exc
        if not isinstance(base, dict):
            raise ValueError(f"{kind} profile content must be a JSON object")
        if ref.content_sha256 != _digest(base):
            raise ValueError(f"{kind} profile reference digest is inconsistent")
        if resource.content_sha256 != ref.content_sha256:
            raise ValueError(f"{kind} resource content digest does not match reference")
        if resource.reference is None or resource.reference != ref:
            raise ValueError(f"{kind} resource reference does not match profile authority")
        if resource.identity != _resource_identity(kind, inputs, ref, base):
            raise ValueError(f"{kind} resource identity does not match realization")


@dataclass(frozen=True, slots=True)
class CandidateSliceArtifact:
    slice_run_id: str
    path: Path
    artifact_format: Literal["gcode"]
    sha256: str
    byte_size: int

    def __post_init__(self) -> None:
        if not self.slice_run_id.strip():
            raise ValueError("slice_run_id must be non-empty")
        if self.artifact_format != "gcode":
            raise ValueError("artifact_format must be gcode")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError("sha256 must be a lowercase hexadecimal digest")
        if self.byte_size <= 0:
            raise ValueError("byte_size must be greater than zero")


@dataclass(frozen=True, slots=True)
class ObservedSliceFacts:
    plate_number: Literal[1]
    layer_count: int
    time_minutes: float | None
    max_z_height: float | None
    filament_used_mm: float | None
    filament_used_cm3: float | None
    filament_density: float | None

    def __post_init__(self) -> None:
        if self.plate_number != 1:
            raise ValueError("only plate 1 is supported")
        if self.layer_count <= 0:
            raise ValueError("layer_count must be greater than zero")


@dataclass(frozen=True, slots=True)
class SliceExecutionSuccess:
    slice_run_id: str
    realization_identity: str
    model_identity: ModelIdentity
    actual_input_identity: ActualInputIdentity
    slicer_name: str
    slicer_version: str
    workspace_path: Path
    printer_config_identity: str
    process_config_identity: str
    filament_config_identity: str
    candidate_artifact: CandidateSliceArtifact
    observed_facts: ObservedSliceFacts


@dataclass(frozen=True, slots=True)
class SliceExecutionFailure:
    slice_run_id: str | None
    stage: str
    diagnostic: str
    stdout: str = ""
    stderr: str = ""


def _failure(
    run_id: str | None, stage: str, diagnostic: str, *, stdout: str = "", stderr: str = ""
) -> SliceExecutionFailure:
    return SliceExecutionFailure(run_id, stage, diagnostic, stdout[-4000:], stderr[-4000:])


def _write_configs(
    inputs: EffectiveSliceInputs,
    resources: tuple[RealizationResource, ...],
    workspace: Path,
) -> tuple[RealizedConfigPaths, tuple[dict[str, object], dict[str, object], dict[str, object]]]:
    expected: list[dict[str, object]] = []
    names = ("printer", "process", "filament")
    overlays = (inputs.printer_overlay, inputs.process_overlay, ())
    refs = (inputs.printer, inputs.process, inputs.filament)
    for kind, ref, overlay in zip(names, refs, overlays, strict=True):
        try:
            base = json.loads(ref.content)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"{kind} profile content is invalid: {exc}") from exc
        if not isinstance(base, dict):
            raise ValueError(f"{kind} profile content must be a JSON object")
        resource = resources[names.index(kind)]
        if resource.kind != kind or resource.content_sha256 != ref.content_sha256:
            raise ValueError(f"{kind} base resource identity does not match realization")
        value = dict(base)
        for entry in overlay:
            value[entry.key] = entry.value
        expected.append(value)
    paths: list[Path] = []
    for kind, value in zip(names, expected, strict=True):
        path = workspace / f"{kind}.realized.json"
        path.write_bytes(_canonical(value))
        paths.append(path)
    return RealizedConfigPaths(paths[0], paths[1], paths[2]), (
        expected[0],
        expected[1],
        expected[2],
    )


def _verify_configs(
    paths: RealizedConfigPaths, expected: tuple[object, object, object]
) -> tuple[str, str, str]:
    identities: list[str] = []
    for path, wanted in zip((paths.printer, paths.process, paths.filament), expected, strict=True):
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict) or parsed != wanted:
            raise ValueError(f"config verification failed for {path.name}")
        identities.append(_digest(parsed))
    return identities[0], identities[1], identities[2]


def _workspace(root: Path, run_id: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    path = root / "slicer" / "orca_slicer" / f"{stamp}_{run_id}"
    path.mkdir(parents=True, exist_ok=False)
    return path


class SliceExecutor:
    """Execute one successful Increment 2 realization and retain its workspace."""

    def __init__(self, workspace_root: str | Path, slicer: OrcaSlicerAdapter | None = None) -> None:
        self.workspace_root = Path(workspace_root)
        self.slicer = slicer or OrcaSlicerAdapter(workdir=self.workspace_root)

    def execute(
        self,
        realization: RealizationResult,
        model_identity: ModelIdentity,
        *,
        timeout_seconds: float | None = None,
    ) -> SliceExecutionSuccess | SliceExecutionFailure:
        run_id = uuid.uuid4().hex
        workspace: Path | None = None
        retained = False
        try:
            if not realization.succeeded or realization.effective_inputs is None:
                return _failure(
                    run_id,
                    "config_materialization_failed",
                    "Increment 2 realization was not successful",
                )
            try:
                source = model_identity.path.resolve(strict=True)
            except (FileNotFoundError, OSError, RuntimeError) as exc:
                return _failure(run_id, "invalid_source_model", str(exc))
            if not source.is_file() or source.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
                return _failure(
                    run_id, "invalid_source_model", "authoritative source model is missing"
                )
            if model_identity.sha256 is None:
                return _failure(
                    run_id, "invalid_source_model", "authoritative source model digest is missing"
                )
            actual_digest = _digest_bytes(source.read_bytes())
            if actual_digest.lower() != model_identity.sha256.lower():
                return _failure(
                    run_id,
                    "source_model_identity_mismatch",
                    "source model SHA-256 does not match ModelIdentity",
                )
            workspace = _workspace(self.workspace_root, run_id)
            inputs = realization.effective_inputs
            resources = realization.resources
            try:
                _verify_resource_authority(inputs, realization.selected_setup, resources)
                configs, expected = _write_configs(inputs, resources, workspace)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                return _failure(run_id, "config_materialization_failed", str(exc))
            try:
                identities = _verify_configs(configs, expected)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                return _failure(run_id, "config_verification_failed", str(exc))
            profiles = (
                ProfileInfo(
                    inputs.process.materialized_name,
                    inputs.process.identity.kind,
                    content=inputs.process.content,
                    source=ProfileSource.GENERATED,
                    materialized=True,
                ),
                ProfileInfo(
                    inputs.filament.materialized_name,
                    inputs.filament.identity.kind,
                    content=inputs.filament.content,
                    source=ProfileSource.GENERATED,
                    materialized=True,
                ),
                ProfileInfo(
                    inputs.printer.materialized_name,
                    inputs.printer.identity.kind,
                    content=inputs.printer.content,
                    source=ProfileSource.GENERATED,
                    materialized=True,
                ),
            )
            job = SliceJob(
                source,
                profiles[0],
                profiles[1],
                profiles[2],
                workspace,
                SlicerKind.ORCA_SLICER,
                timeout_seconds,
                f"{source.stem}.gcode.3mf",
                configs,
            )
            result = self.slicer.slice(job)
            candidate = workspace / "plate_1.gcode"
            if result.return_code != 0:
                return _failure(
                    run_id, "slicer_process_failed", "slicer process returned a non-zero code"
                )
            if not candidate.is_file():
                return _failure(
                    run_id, "slice_output_missing", "exact plate_1.gcode output is missing"
                )
            try:
                validation_content = candidate.read_text(encoding="utf-8").encode("utf-8")
            except (OSError, UnicodeError) as exc:
                return _failure(run_id, "slice_output_invalid", str(exc))
            if not validation_content:
                return _failure(
                    run_id, "slice_output_invalid", "exact plate_1.gcode output is empty"
                )
            if not _is_structurally_valid_gcode(validation_content):
                return _failure(
                    run_id, "slice_output_invalid", "exact plate_1.gcode output is malformed"
                )
            try:
                stats = parse_gcode(candidate)
            except (OSError, UnicodeError, ValueError) as exc:
                return _failure(run_id, "slice_output_invalid", str(exc))
            layers = stats.get("layer_count")
            if not isinstance(layers, int) or layers <= 0:
                return _failure(
                    run_id, "slice_facts_invalid", "positive parser-derived layer_count is missing"
                )
            try:
                identity_content = candidate.read_bytes()
                byte_size = candidate.stat().st_size
                artifact = CandidateSliceArtifact(
                    run_id, candidate, "gcode", _digest_bytes(identity_content), byte_size
                )
            except Exception as exc:
                return _failure(run_id, "candidate_artifact_identity_failed", str(exc))
            facts = ObservedSliceFacts(
                1,
                layers,
                stats.get("time_minutes"),
                stats.get("max_z_height"),
                stats.get("filament_used_mm"),
                stats.get("filament_used_cm3"),
                stats.get("filament_density"),
            )
            retained = True
            return SliceExecutionSuccess(
                run_id,
                inputs.identity,
                model_identity,
                inputs.actual_inputs,
                "OrcaSlicer",
                "2.3.2",
                workspace,
                identities[0],
                identities[1],
                identities[2],
                artifact,
                facts,
            )
        except (OSError, json.JSONDecodeError) as exc:
            return _failure(
                run_id,
                (
                    "workspace_creation_failed"
                    if workspace is None
                    else "config_materialization_failed"
                ),
                str(exc),
            )
        except (SlicerNotInstalled, SlicerUnavailable) as exc:
            return _failure(run_id, "slicer_unavailable", str(exc))
        except SliceTimeout as exc:
            return _failure(run_id, "slicer_timeout", str(exc))
        except SlicerError as exc:
            return _failure(
                run_id, "slicer_process_failed", str(exc), stderr=str(exc.details.get("stderr", ""))
            )
        finally:
            if workspace is not None and not retained:
                try:
                    shutil.rmtree(workspace)
                except OSError:
                    pass


def execute_slice(
    realization: RealizationResult,
    model_identity: ModelIdentity,
    *,
    workspace_root: str | Path,
    slicer: OrcaSlicerAdapter | None = None,
    timeout_seconds: float | None = None,
) -> SliceExecutionSuccess | SliceExecutionFailure:
    return SliceExecutor(workspace_root, slicer).execute(
        realization, model_identity, timeout_seconds=timeout_seconds
    )
