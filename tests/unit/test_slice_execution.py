"""Contract tests for the realized Increment 3 execution boundary."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from print_engineer.adapters.slicer.execution import (
    CandidateSliceArtifact,
    ObservedSliceFacts,
    SliceExecutionFailure,
    SliceExecutionSuccess,
    SliceExecutor,
    _is_structurally_valid_gcode,
)
from print_engineer.adapters.slicer.orca import OrcaSlicerAdapter
from print_engineer.adapters.slicer.realization import (
    EffectiveSliceInputs,
    OverlayEntry,
    ProfileReference,
    RealizationResource,
    RealizationResult,
)
from print_engineer.core.preparation import (
    ActualInputIdentity,
    ModelIdentity,
    ProfileIdentity,
    SelectedSetup,
)
from print_engineer.core.types import (
    ProfileInfo,
    ProfileKind,
    RealizedConfigPaths,
    SliceJob,
    SlicerInfo,
    SlicerKind,
)
from print_engineer.errors import SlicerUnavailable, SliceTimeout


def test_realized_config_paths_are_exact_and_immutable(tmp_path: Path) -> None:
    paths = RealizedConfigPaths(
        tmp_path / "printer.json", tmp_path / "process.json", tmp_path / "filament.json"
    )
    assert paths.printer == tmp_path / "printer.json"
    with pytest.raises(AttributeError):
        paths.printer = tmp_path / "other.json"  # type: ignore[misc]


def test_candidate_and_observed_facts_contracts() -> None:
    candidate = CandidateSliceArtifact("run-1", Path("plate_1.gcode"), "gcode", "a" * 64, 3)
    facts = ObservedSliceFacts(1, 4, None, None, None, None, None)
    assert candidate.artifact_format == "gcode"
    assert candidate.byte_size > 0
    assert facts.plate_number == 1
    assert facts.layer_count == 4


def test_candidate_and_facts_reject_invalid_values() -> None:
    with pytest.raises(ValueError):
        CandidateSliceArtifact("run-1", Path("plate_1.gcode"), "gcode", "bad", 3)
    with pytest.raises(ValueError):
        ObservedSliceFacts(1, 0, None, None, None, None, None)


def test_success_shape_is_immutable_and_candidate_is_not_final_artifact() -> None:
    model = ModelIdentity(Path("cube.stl"), "a" * 64)
    actual = ActualInputIdentity(SlicerKind.ORCA_SLICER, None, None, None, None, None, None)
    candidate = CandidateSliceArtifact("run-1", Path("plate_1.gcode"), "gcode", "a" * 64, 3)
    success = SliceExecutionSuccess(
        "run-1",
        "realization",
        model,
        actual,
        "OrcaSlicer",
        "2.3.2",
        Path("workspace"),
        "a" * 64,
        "b" * 64,
        "c" * 64,
        candidate,
        ObservedSliceFacts(1, 1, None, None, None, None, None),
    )
    assert success.candidate_artifact is candidate
    assert success.candidate_artifact.__class__.__name__ == "CandidateSliceArtifact"
    with pytest.raises(AttributeError):
        success.slice_run_id = "other"  # type: ignore[misc]


def test_failure_contains_no_partial_success_evidence() -> None:
    failure = SliceExecutionFailure(None, "slice_output_missing", "plate_1.gcode is missing")
    assert not hasattr(failure, "candidate_artifact")
    assert not hasattr(failure, "observed_facts")


def _resource_digest(kind: str, inputs: EffectiveSliceInputs, base: Mapping[str, object]) -> str:
    overlay = (
        inputs.printer_overlay
        if kind == "printer"
        else inputs.process_overlay
        if kind == "process"
        else ()
    )
    payload = {
        "capability": inputs.capability,
        "kind": kind,
        "reference": {
            "identity": {
                "name": inputs.printer.identity.name
                if kind == "printer"
                else inputs.process.identity.name
                if kind == "process"
                else inputs.filament.identity.name,
                "kind": (
                    inputs.printer.identity.kind.value
                    if kind == "printer"
                    else inputs.process.identity.kind.value
                    if kind == "process"
                    else inputs.filament.identity.kind.value
                ),
                "setting_id": (
                    inputs.printer.identity.setting_id
                    if kind == "printer"
                    else inputs.process.identity.setting_id
                    if kind == "process"
                    else inputs.filament.identity.setting_id
                ),
            },
            "materialized_name": (
                inputs.printer.materialized_name
                if kind == "printer"
                else inputs.process.materialized_name
                if kind == "process"
                else inputs.filament.materialized_name
            ),
            "content_sha256": (
                inputs.printer.content_sha256
                if kind == "printer"
                else inputs.process.content_sha256
                if kind == "process"
                else inputs.filament.content_sha256
            ),
        },
        "content": base,
        "overlay": [(entry.key, entry.value, entry.layer, entry.units) for entry in overlay],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@pytest.fixture
def realization_fixture(tmp_path: Path) -> tuple[RealizationResult, ModelIdentity]:
    printer_id = ProfileIdentity("A1 printer", ProfileKind.PRINTER, "printer-1")
    process_id = ProfileIdentity("0.20 process", ProfileKind.PROCESS, "process-1")
    filament_id = ProfileIdentity("PLA", ProfileKind.FILAMENT, "filament-1")
    printer = {"name": "A1 printer", "nozzle_diameter": "0.4", "curr_bed_type": "cool_plate"}
    process = {"name": "0.20 process", "layer_height": "0.2", "wall_loops": 2}
    filament = {"name": "PLA", "filament_type": "PLA"}

    def ref(identity: ProfileIdentity, data: Mapping[str, object]) -> ProfileReference:
        content = json.dumps(data, indent=2)
        digest = hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return ProfileReference(identity, identity.name, digest, content)

    refs = (ref(printer_id, printer), ref(process_id, process), ref(filament_id, filament))
    overlays = (
        (
            OverlayEntry("curr_bed_type", "cool_plate", "printer", "none"),
            OverlayEntry("nozzle_diameter", "0.4", "printer", "mm"),
        ),
        (OverlayEntry("layer_height", "0.2", "process", "mm"),),
        (),
    )
    actual = ActualInputIdentity(
        SlicerKind.ORCA_SLICER, printer_id, 0.4, "cool_plate", "PLA", filament_id, process_id, ()
    )
    inputs = EffectiveSliceInputs(
        SlicerKind.ORCA_SLICER,
        "OrcaSlicer 2.3.2",
        refs[0],
        refs[1],
        refs[2],
        0.4,
        "0.4",
        "cool_plate",
        "cool_plate",
        "cool_plate",
        "PLA",
        overlays[0],
        overlays[1],
        actual,
        "realization-id",
    )
    resources = tuple(
        RealizationResource(
            kind,
            _resource_digest(kind, inputs, cast(Mapping[str, object], data)),
            refs[index].content_sha256,
            refs[index],
        )
        for index, (kind, data) in enumerate(
            zip(("printer", "process", "filament"), (printer, process, filament), strict=True)
        )
    )
    setup = SelectedSetup(
        SlicerKind.ORCA_SLICER,
        printer_id,
        0.4,
        "cool_plate",
        "PLA",
        filament_id,
        process_id,
    )
    model = tmp_path / "model.stl"
    model.write_bytes(b"solid model\n")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    return RealizationResult(setup, inputs, resources, True), ModelIdentity(model, digest)


class _FakeSlicer:
    def __init__(
        self,
        output: bytes = b"; total layer number: 2\nG1 X1 Y1\n",
        return_code: int = 0,
        write_output: bool = True,
    ) -> None:
        self.output = output
        self.return_code = return_code
        self.write_output = write_output
        self.calls = 0
        self.jobs: list[object] = []

    def slice(self, job: object) -> object:
        self.calls += 1
        self.jobs.append(job)
        output_dir = cast(Any, job).output_dir
        assert output_dir is not None
        if self.write_output:
            (output_dir / "plate_1.gcode").write_bytes(self.output)
        return SimpleNamespace(return_code=self.return_code)


class _RaisingSlicer:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def slice(self, job: object) -> object:
        raise self.error


@pytest.mark.parametrize(
    "line",
    [
        "G1 X10 Y20",
        "G1 X10.5 Y-2.3 E0.44",
        "M104 S220",
        "M9833.2",
        "M1002 gcode_claim_action : 2",
        "M1002 set_gcode_claim_speed_level : 2",
        "M1002 set_filament_type:PLA-AERO",
        "M1002 judge_flag build_plate_detect_flag",
        "M1002 judge_last_extrude_cali_success",
        "M1006 W",
        "G28 X",
        "G28 Z P0 T140",
        "G28 Z P0 T300",
        "M17",
        "M17 D",
        "M17 R S",
        "M18 X Y Z",
        "M211 R",
        "M620 M",
        "M620.1 E F523.843 T250",
        "M620 S0A",
        "M620 S255",
        "M621 S0A",
        "M621 S255",
        "M624 AQAAAAAAAAA=",
        "M900 C R S",
    ],
)
def test_structural_validator_accepts_evidenced_orca_syntax(line: str) -> None:
    assert _is_structurally_valid_gcode((line + "\n").encode())


@pytest.mark.parametrize(
    "line",
    [
        "G",
        "Gfoo",
        "M.2",
        "G1.",
        "G1.2.3",
        "G1 garbage",
        "G1 Xabc",
        "G1 X",
        "G28 Z",
        "G28 Z P0",
        "G28 Z T140",
        "G28 Z Pabc T140",
        "G28 Z P0 Tabc",
        "M1002",
        "M1002 gcode_claim_action : abc",
        "M1002 unknown_payload",
        "M104 garbage",
        "M620 S1",
        "M624 not-base64!",
    ],
)
def test_structural_validator_rejects_malformed_or_unsupported_orca_syntax(line: str) -> None:
    assert not _is_structurally_valid_gcode((line + "\n").encode())


def test_structural_validator_preserves_comments_and_requires_command() -> None:
    content = b"; HEADER_BLOCK_START\n\nM9833.2 ; inline metadata\n"
    assert _is_structurally_valid_gcode(content)
    assert not _is_structurally_valid_gcode(b"; HEADER_BLOCK_START\n\n")


def test_orca_like_multiline_candidate_reaches_success(
    tmp_path: Path, realization_fixture: tuple[RealizationResult, ModelIdentity]
) -> None:
    realization, model = realization_fixture
    output = (
        b"; total layer number: 4\n"
        b"M9833.2\nM1002 gcode_claim_action : 2\n"
        b"M1002 set_filament_type:PLA-AERO\nM1006 W\n"
        b"G28 X\nG28 Z P0 T140\nM17 D\nM620 S0A\nM624 AQAAAAAAAAA=\n"
        b"G1 X10.5 Y-2.3 E0.44 ; metadata\n"
    )
    result = SliceExecutor(tmp_path, cast(Any, _FakeSlicer(output))).execute(realization, model)
    assert isinstance(result, SliceExecutionSuccess)
    assert result.candidate_artifact.path == result.workspace_path / "plate_1.gcode"
    assert result.observed_facts.layer_count == 4


def test_realized_execution_success_and_source_preservation(
    tmp_path: Path, realization_fixture: tuple[RealizationResult, ModelIdentity]
) -> None:
    realization, model = realization_fixture
    source_before = model.path.read_bytes()
    slicer = _FakeSlicer(
        b"; model printing time: 2m; total estimated time: 2m\n"
        b"; total layer number: 2\n; max_z_height: 0.40\n"
        b"; filament_density: 1.24\n; filament used [mm] = 12.5\n"
        b"; filament used [cm3] = 0.1\nG1 X1 Y1\n"
    )
    result = SliceExecutor(tmp_path, cast(Any, slicer)).execute(realization, model)
    inputs = realization.effective_inputs
    assert inputs is not None
    assert isinstance(result, SliceExecutionSuccess)
    assert result.actual_input_identity is inputs.actual_inputs
    assert result.realization_identity == "realization-id"
    assert result.candidate_artifact.path == result.workspace_path / "plate_1.gcode"
    assert result.observed_facts == ObservedSliceFacts(1, 2, 2.0, 0.4, 12.5, 0.1, 1.24)
    assert model.path.read_bytes() == source_before
    assert result.workspace_path.is_dir()


def test_relative_source_resolves_once_and_hands_off_absolute_verified_path(
    tmp_path: Path,
    realization_fixture: tuple[RealizationResult, ModelIdentity],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    realization, model = realization_fixture
    caller = tmp_path / "caller"
    caller.mkdir()
    source = caller / "model.stl"
    source.write_bytes(b"caller source")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    original = Path.resolve
    resolve_calls = 0

    def resolve_once(path: Path, *, strict: bool = False) -> Path:
        nonlocal resolve_calls
        resolve_calls += 1
        return original(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_once)
    monkeypatch.chdir(caller)
    original_model = ModelIdentity(Path("model.stl"), digest)
    slicer = _FakeSlicer()
    result = SliceExecutor(tmp_path / "slice", cast(Any, slicer)).execute(
        realization, original_model
    )
    assert isinstance(result, SliceExecutionSuccess)
    assert resolve_calls == 1
    assert original_model.path == Path("model.stl")
    assert result.model_identity is original_model
    assert len(slicer.jobs) == 1
    job = cast(Any, slicer.jobs[0])
    assert job.model_path.is_absolute()
    assert job.model_path == source.resolve(strict=True)
    assert job.model_path.read_bytes() == source.read_bytes()
    assert job.model_path == source
    assert model.path != original_model.path


def test_absolute_source_remains_absolute_and_successful(
    tmp_path: Path, realization_fixture: tuple[RealizationResult, ModelIdentity]
) -> None:
    realization, model = realization_fixture
    slicer = _FakeSlicer()
    result = SliceExecutor(tmp_path / "slice", cast(Any, slicer)).execute(realization, model)
    assert isinstance(result, SliceExecutionSuccess)
    job = cast(Any, slicer.jobs[0])
    assert job.model_path.is_absolute()
    assert job.model_path == model.path.resolve(strict=True)


def test_missing_relative_source_does_not_search_or_invoke_slicer(
    tmp_path: Path,
    realization_fixture: tuple[RealizationResult, ModelIdentity],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    realization, model = realization_fixture
    monkeypatch.chdir(tmp_path)
    slicer = _FakeSlicer()
    result = SliceExecutor(tmp_path / "slice", cast(Any, slicer)).execute(
        realization, ModelIdentity(Path("missing.stl"), model.sha256)
    )
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "invalid_source_model"
    assert slicer.calls == 0


@pytest.mark.parametrize(
    "error", [FileNotFoundError("missing"), OSError("unreadable"), RuntimeError("loop")]
)
def test_source_resolution_exceptions_are_invalid_source(
    tmp_path: Path,
    realization_fixture: tuple[RealizationResult, ModelIdentity],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    realization, model = realization_fixture
    original_resolve = Path.resolve

    def fail_resolve(path: Path, *, strict: bool = False) -> Path:
        if path == model.path:
            raise error
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", fail_resolve)
    slicer = _FakeSlicer()
    result = SliceExecutor(tmp_path / "slice", cast(Any, slicer)).execute(realization, model)
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "invalid_source_model"
    assert slicer.calls == 0


def test_relative_sha_mismatch_blocks_slicer(
    tmp_path: Path,
    realization_fixture: tuple[RealizationResult, ModelIdentity],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    realization, model = realization_fixture
    caller = tmp_path / "caller"
    caller.mkdir()
    source = caller / "model.stl"
    source.write_bytes(b"verified source")
    monkeypatch.chdir(caller)
    slicer = _FakeSlicer()
    result = SliceExecutor(tmp_path / "slice", cast(Any, slicer)).execute(
        realization, ModelIdentity(Path("model.stl"), "0" * 64)
    )
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "source_model_identity_mismatch"
    assert slicer.calls == 0


def test_relative_basename_collision_never_substitutes_workspace_file(
    tmp_path: Path,
    realization_fixture: tuple[RealizationResult, ModelIdentity],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    realization, _ = realization_fixture
    caller = tmp_path / "caller"
    caller.mkdir()
    source = caller / "model.stl"
    source.write_bytes(b"caller bytes")
    monkeypatch.chdir(caller)
    slicer = _FakeSlicer()
    result = SliceExecutor(tmp_path / "slice", cast(Any, slicer)).execute(
        realization,
        ModelIdentity(Path("model.stl"), hashlib.sha256(source.read_bytes()).hexdigest()),
    )
    assert isinstance(result, SliceExecutionSuccess)
    job = cast(Any, slicer.jobs[0])
    collision = job.output_dir / "model.stl"
    collision.write_bytes(b"workspace bytes")
    assert job.model_path == source.resolve(strict=True)
    assert job.model_path.read_bytes() == b"caller bytes"
    assert collision.read_bytes() != job.model_path.read_bytes()


@pytest.mark.parametrize("missing_kind", ["missing", "digest"])
def test_source_model_gate_and_preservation(
    tmp_path: Path,
    realization_fixture: tuple[RealizationResult, ModelIdentity],
    missing_kind: str,
) -> None:
    realization, model = realization_fixture
    if missing_kind == "missing":
        invalid = ModelIdentity(tmp_path / "missing.stl", model.sha256)
        expected = "invalid_source_model"
    else:
        invalid = replace(model, sha256="0" * 64)
        expected = "source_model_identity_mismatch"
    result = SliceExecutor(tmp_path, cast(Any, _FakeSlicer())).execute(realization, invalid)
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == expected
    assert not list((tmp_path / "slicer" / "orca_slicer").glob("*"))
    assert model.path.exists()


@pytest.mark.parametrize(
    ("error", "stage"),
    [(SlicerUnavailable("not installed"), "slicer_unavailable"),
     (SliceTimeout("timed out"), "slicer_timeout")],
)
def test_unavailable_and_timeout_are_stage_owned(
    tmp_path: Path,
    realization_fixture: tuple[RealizationResult, ModelIdentity],
    error: Exception,
    stage: str,
) -> None:
    realization, model = realization_fixture
    result = SliceExecutor(tmp_path, cast(Any, _RaisingSlicer(error))).execute(realization, model)
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == stage
    assert not list((tmp_path / "slicer" / "orca_slicer").glob("*"))


def test_missing_output_is_stage_owned_and_workspace_is_cleaned(
    tmp_path: Path, realization_fixture: tuple[RealizationResult, ModelIdentity]
) -> None:
    realization, model = realization_fixture
    result = SliceExecutor(tmp_path, cast(Any, _FakeSlicer(write_output=False))).execute(
        realization, model
    )
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "slice_output_missing"
    assert not list((tmp_path / "slicer" / "orca_slicer").glob("*"))


def test_effective_config_identity_is_semantic_and_workspace_independent(
    tmp_path: Path, realization_fixture: tuple[RealizationResult, ModelIdentity]
) -> None:
    realization, model = realization_fixture
    first = SliceExecutor(tmp_path / "one", cast(Any, _FakeSlicer())).execute(realization, model)
    second = SliceExecutor(tmp_path / "two", cast(Any, _FakeSlicer())).execute(realization, model)
    assert isinstance(first, SliceExecutionSuccess)
    assert isinstance(second, SliceExecutionSuccess)
    assert (
        first.printer_config_identity,
        first.process_config_identity,
        first.filament_config_identity,
    ) == (
        second.printer_config_identity,
        second.process_config_identity,
        second.filament_config_identity,
    )
    for name in ("printer", "process", "filament"):
        path = first.workspace_path / f"{name}.realized.json"
        assert json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("index", [0, 1, 2])
def test_each_resource_identity_is_authoritative(
    tmp_path: Path, realization_fixture: tuple[RealizationResult, ModelIdentity], index: int
) -> None:
    realization, model = realization_fixture
    resources = list(realization.resources)
    resources[index] = replace(resources[index], identity="0" * 64)
    bad = replace(realization, resources=tuple(resources))
    result = SliceExecutor(tmp_path, cast(Any, _FakeSlicer())).execute(bad, model)
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "config_materialization_failed"


@pytest.mark.parametrize("index", [0, 1, 2])
def test_each_profile_reference_identity_and_digest_are_checked(
    tmp_path: Path, realization_fixture: tuple[RealizationResult, ModelIdentity], index: int
) -> None:
    realization, model = realization_fixture
    inputs = realization.effective_inputs
    assert inputs is not None
    ids = [inputs.printer.identity, inputs.process.identity, inputs.filament.identity]
    ids[index] = replace(ids[index], setting_id="different")
    refs = [inputs.printer, inputs.process, inputs.filament]
    refs[index] = replace(refs[index], identity=ids[index])
    bad_inputs = replace(inputs, printer=refs[0], process=refs[1], filament=refs[2])
    result = SliceExecutor(tmp_path, cast(Any, _FakeSlicer())).execute(
        replace(realization, effective_inputs=bad_inputs), model
    )
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "config_materialization_failed"

    refs[index] = replace(refs[index], content_sha256="0" * 64)
    bad_inputs = replace(bad_inputs, printer=refs[0], process=refs[1], filament=refs[2])
    result = SliceExecutor(tmp_path, cast(Any, _FakeSlicer())).execute(
        replace(realization, effective_inputs=bad_inputs), model
    )
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "config_materialization_failed"


@pytest.mark.parametrize(
    ("output", "stage"),
    [
        (b"", "slice_output_invalid"),
        (b"not gcode", "slice_output_invalid"),
        (b"G1 X1 Y1\n", "slice_facts_invalid"),
    ],
)
def test_output_validation_distinguishes_malformed_and_missing_facts(
    tmp_path: Path,
    realization_fixture: tuple[RealizationResult, ModelIdentity],
    output: bytes,
    stage: str,
) -> None:
    realization, model = realization_fixture
    result = SliceExecutor(tmp_path, cast(Any, _FakeSlicer(output))).execute(realization, model)
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == stage


@pytest.mark.parametrize("output", [b"G1 garbage\n", b"G1 Xabc\n", b"random words\n", b"G foo\n"])
def test_structural_gcode_rejects_malformed_commands(
    tmp_path: Path,
    realization_fixture: tuple[RealizationResult, ModelIdentity],
    output: bytes,
) -> None:
    realization, model = realization_fixture
    result = SliceExecutor(tmp_path, cast(Any, _FakeSlicer(output))).execute(realization, model)
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "slice_output_invalid"


@pytest.mark.parametrize("index", [0, 1, 2])
def test_same_content_different_profile_authority_is_rejected(
    tmp_path: Path, realization_fixture: tuple[RealizationResult, ModelIdentity], index: int
) -> None:
    realization, model = realization_fixture
    resources = list(realization.resources)
    ref = resources[index].reference
    assert ref is not None
    changed = replace(ref, identity=replace(ref.identity, setting_id="substitute"))
    resources[index] = replace(resources[index], reference=changed)
    result = SliceExecutor(tmp_path, cast(Any, _FakeSlicer())).execute(
        replace(realization, resources=tuple(resources)), model
    )
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "config_materialization_failed"


def test_nonzero_process_wins_over_valid_output(
    tmp_path: Path, realization_fixture: tuple[RealizationResult, ModelIdentity]
) -> None:
    realization, model = realization_fixture
    result = SliceExecutor(tmp_path, cast(Any, _FakeSlicer(return_code=1))).execute(
        realization, model
    )
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "slicer_process_failed"


def test_wrong_resource_kind_is_rejected(
    tmp_path: Path, realization_fixture: tuple[RealizationResult, ModelIdentity]
) -> None:
    realization, model = realization_fixture
    resources = list(realization.resources)
    resources[1] = replace(resources[1], kind="printer")
    result = SliceExecutor(tmp_path, cast(Any, _FakeSlicer())).execute(
        replace(realization, resources=tuple(resources)), model
    )
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "config_materialization_failed"


def test_candidate_identity_failure_is_reachable(
    tmp_path: Path,
    realization_fixture: tuple[RealizationResult, ModelIdentity],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    realization, model = realization_fixture
    from print_engineer.adapters.slicer import execution as execution_module

    original = execution_module._digest_bytes

    def fail_candidate(content: bytes) -> str:
        if content.startswith(b"G1"):
            raise OSError("hashing failed")
        return original(content)

    monkeypatch.setattr(execution_module, "_digest_bytes", fail_candidate)
    result = SliceExecutor(
        tmp_path,
        cast(Any, _FakeSlicer(b"G1 X1 Y1\n; total layer number: 2\n")),
    ).execute(realization, model)
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "candidate_artifact_identity_failed"


def test_realized_orca_command_uses_exact_paths_and_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from print_engineer.adapters.slicer import orca as orca_module

    model = tmp_path / "model.stl"
    model.write_bytes(b"solid model")
    paths = RealizedConfigPaths(
        tmp_path / "printer.realized.json",
        tmp_path / "process.realized.json",
        tmp_path / "filament.realized.json",
    )
    for path in (paths.printer, paths.process, paths.filament):
        path.write_text("{}", encoding="utf-8")
    job = SliceJob(
        model,
        ProfileInfo("process", ProfileKind.PROCESS),
        ProfileInfo("filament", ProfileKind.FILAMENT),
        ProfileInfo("printer", ProfileKind.PRINTER),
        tmp_path,
        SlicerKind.ORCA_SLICER,
        realized_configs=paths,
    )
    commands: list[list[str]] = []

    def run(command: list[str], **_: object) -> object:
        commands.append(command)
        (tmp_path / "plate_1.gcode").write_text(
            "; total layer number: 1\nG1 X1\n", encoding="utf-8"
        )
        return SimpleNamespace(return_code=0, timed_out=False)

    monkeypatch.setattr(orca_module, "run_cli", run)
    adapter = OrcaSlicerAdapter(executable=tmp_path / "orca.exe")
    info = SlicerInfo(SlicerKind.ORCA_SLICER, "OrcaSlicer", tmp_path / "orca.exe", "2.3.2")
    result = adapter._slice_realized(job, info)
    assert result.gcode_path == tmp_path / "plate_1.gcode"
    assert "--load-settings" in commands[0]
    assert commands[0][commands[0].index("--load-settings") + 1] == (
        "process.realized.json;printer.realized.json"
    )
    assert commands[0][commands[0].index("--load-filaments") + 1] == "filament.realized.json"
    assert commands[0][commands[0].index("--slice") + 1] == "1"


def test_readback_tamper_prevents_orca(
    tmp_path: Path,
    realization_fixture: tuple[RealizationResult, ModelIdentity],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    realization, model = realization_fixture
    slicer = _FakeSlicer()
    from print_engineer.adapters.slicer import execution as execution_module

    original = execution_module._verify_configs

    def tamper(
        paths: RealizedConfigPaths, expected: tuple[object, object, object]
    ) -> tuple[str, str, str]:
        paths.printer.write_text("{}", encoding="utf-8")
        return original(paths, expected)

    monkeypatch.setattr(execution_module, "_verify_configs", tamper)
    result = SliceExecutor(tmp_path, cast(Any, slicer)).execute(realization, model)
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "config_verification_failed"
    assert slicer.calls == 0


@pytest.mark.parametrize("index", [0, 1, 2])
def test_authority_failures_block_orca_including_missing_reference_and_content_mismatch(
    tmp_path: Path,
    realization_fixture: tuple[RealizationResult, ModelIdentity],
    index: int,
) -> None:
    realization, model = realization_fixture
    resources = list(realization.resources)
    resources[index] = replace(resources[index], reference=None)
    fake = _FakeSlicer()
    result = SliceExecutor(tmp_path, cast(Any, fake)).execute(
        replace(realization, resources=tuple(resources)), model
    )
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "config_materialization_failed"
    assert fake.calls == 0

    resources = list(realization.resources)
    resources[index] = replace(resources[index], content_sha256="0" * 64)
    fake = _FakeSlicer()
    result = SliceExecutor(tmp_path / "content", cast(Any, fake)).execute(
        replace(realization, resources=tuple(resources)), model
    )
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "config_materialization_failed"
    assert fake.calls == 0


def test_same_content_different_authority_blocks_orca(
    tmp_path: Path, realization_fixture: tuple[RealizationResult, ModelIdentity]
) -> None:
    realization, model = realization_fixture
    resources = list(realization.resources)
    reference = resources[0].reference
    assert reference is not None
    resources[0] = replace(
        resources[0],
        reference=replace(reference, identity=replace(reference.identity, setting_id="other")),
    )
    fake = _FakeSlicer()
    result = SliceExecutor(tmp_path, cast(Any, fake)).execute(
        replace(realization, resources=tuple(resources)), model
    )
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "config_materialization_failed"
    assert fake.calls == 0


def test_success_materialization_and_evidence_are_independently_exact(
    tmp_path: Path, realization_fixture: tuple[RealizationResult, ModelIdentity]
) -> None:
    realization, model = realization_fixture
    source_before = model.path.read_bytes()
    result = SliceExecutor(tmp_path, cast(Any, _FakeSlicer())).execute(realization, model)
    assert isinstance(result, SliceExecutionSuccess)
    inputs = realization.effective_inputs
    assert inputs is not None
    expected = {
        "printer": {"name": "A1 printer", "nozzle_diameter": "0.4", "curr_bed_type": "cool_plate"},
        "process": {"name": "0.20 process", "layer_height": "0.2", "wall_loops": 2},
        "filament": {"name": "PLA", "filament_type": "PLA"},
    }
    for name, identity in zip(
        ("printer", "process", "filament"),
        (
            result.printer_config_identity,
            result.process_config_identity,
            result.filament_config_identity,
        ),
        strict=True,
    ):
        parsed = json.loads(
            (result.workspace_path / f"{name}.realized.json").read_text(encoding="utf-8")
        )
        assert parsed == expected[name]
        assert hashlib.sha256(
            json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest() == identity
    assert result.realization_identity == inputs.identity
    assert result.model_identity is model
    assert result.actual_input_identity is inputs.actual_inputs
    assert (result.slicer_name, result.slicer_version) == ("OrcaSlicer", "2.3.2")
    candidate_bytes = result.candidate_artifact.path.read_bytes()
    assert result.candidate_artifact.path == result.workspace_path / "plate_1.gcode"
    assert result.candidate_artifact.artifact_format == "gcode"
    assert result.candidate_artifact.slice_run_id == result.slice_run_id
    assert result.candidate_artifact.byte_size == len(candidate_bytes) > 0
    assert result.candidate_artifact.sha256 == hashlib.sha256(candidate_bytes).hexdigest()
    assert model.path.read_bytes() == source_before


@pytest.mark.parametrize("output", [b"\n ; comment only\n", b"   \n; another comment\n"])
def test_blank_whitespace_and_comment_only_output_is_invalid(
    tmp_path: Path, realization_fixture: tuple[RealizationResult, ModelIdentity], output: bytes
) -> None:
    realization, model = realization_fixture
    result = SliceExecutor(tmp_path, cast(Any, _FakeSlicer(output))).execute(realization, model)
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "slice_output_invalid"


def test_valid_candidate_with_optional_facts_absent_is_success(
    tmp_path: Path, realization_fixture: tuple[RealizationResult, ModelIdentity]
) -> None:
    realization, model = realization_fixture
    result = SliceExecutor(
        tmp_path, cast(Any, _FakeSlicer(b"; total layer number: 3\nG1 X1 Y1\n"))
    ).execute(realization, model)
    assert isinstance(result, SliceExecutionSuccess)
    assert result.observed_facts == ObservedSliceFacts(1, 3, None, None, None, None, None)


@pytest.mark.parametrize("operation", ["read_bytes", "stat"])
def test_candidate_identity_read_and_stat_failures_are_stage_owned_and_cleaned(
    tmp_path: Path,
    realization_fixture: tuple[RealizationResult, ModelIdentity],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    realization, model = realization_fixture
    original = getattr(Path, operation)
    calls = 0

    def fail_on_candidate(self: Path, *args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        if self.name == "plate_1.gcode":
            calls += 1
            if calls == (1 if operation == "read_bytes" else 2):
                raise OSError(f"{operation} failed")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, operation, fail_on_candidate)
    result = SliceExecutor(tmp_path, cast(Any, _FakeSlicer())).execute(realization, model)
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "candidate_artifact_identity_failed"
    assert not list((tmp_path / "slicer" / "orca_slicer").glob("*"))


def test_process_failure_cleans_workspace_and_preserves_source(
    tmp_path: Path, realization_fixture: tuple[RealizationResult, ModelIdentity]
) -> None:
    realization, model = realization_fixture
    before = model.path.read_bytes()
    result = SliceExecutor(tmp_path, cast(Any, _FakeSlicer(return_code=7))).execute(
        realization, model
    )
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "slicer_process_failed"
    assert not list((tmp_path / "slicer" / "orca_slicer").glob("*"))
    assert model.path.read_bytes() == before


@pytest.mark.parametrize(
    ("archive", "candidate", "stage"),
    [(None, True, None), ("foo.gcode.3mf", True, None), ("model.gcode.3mf", True, None),
     ("model.gcode.3mf", False, "slice_output_missing")],
)
def test_auxiliary_archives_never_change_exact_candidate_authority(
    tmp_path: Path,
    realization_fixture: tuple[RealizationResult, ModelIdentity],
    archive: str | None,
    candidate: bool,
    stage: str | None,
) -> None:
    realization, model = realization_fixture

    class ArchiveSlicer(_FakeSlicer):
        def slice(self, job: object) -> object:
            result = super().slice(job)
            output_dir = cast(Any, job).output_dir
            assert output_dir is not None
            if archive is not None:
                (output_dir / archive).write_bytes(b"not candidate")
            if not candidate:
                (output_dir / "plate_1.gcode").unlink(missing_ok=True)
            return result

    slicer = ArchiveSlicer(write_output=candidate)
    result = SliceExecutor(tmp_path, cast(Any, slicer)).execute(realization, model)
    if stage is None:
        assert isinstance(result, SliceExecutionSuccess)
        assert result.candidate_artifact.path.name == "plate_1.gcode"
    else:
        assert isinstance(result, SliceExecutionFailure)
        assert result.stage == stage


@pytest.mark.parametrize("mode", ["plate_2", "external"])
def test_only_non_authoritative_output_is_missing(
    tmp_path: Path,
    realization_fixture: tuple[RealizationResult, ModelIdentity],
    mode: str,
) -> None:
    realization, model = realization_fixture

    class OtherPathSlicer(_FakeSlicer):
        def slice(self, job: object) -> object:
            output_dir = cast(Any, job).output_dir
            assert output_dir is not None
            target = (
                output_dir / "plate_2.gcode"
                if mode == "plate_2"
                else tmp_path / "plate_1.gcode"
            )
            target.write_bytes(b"; total layer number: 1\nG1 X1\n")
            return SimpleNamespace(return_code=0)

    result = SliceExecutor(tmp_path, cast(Any, OtherPathSlicer())).execute(realization, model)
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "slice_output_missing"


def test_cleanup_failure_does_not_replace_underlying_process_failure(
    tmp_path: Path,
    realization_fixture: tuple[RealizationResult, ModelIdentity],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    realization, model = realization_fixture
    from print_engineer.adapters.slicer import execution as execution_module

    monkeypatch.setattr(
        execution_module.shutil,
        "rmtree",
        lambda _: (_ for _ in ()).throw(OSError("cleanup")),
    )
    result = SliceExecutor(tmp_path, cast(Any, _FakeSlicer(return_code=2))).execute(
        realization, model
    )
    assert isinstance(result, SliceExecutionFailure)
    assert result.stage == "slicer_process_failed"


def test_realized_orca_allows_preexisting_auxiliary_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from print_engineer.adapters.slicer import orca as orca_module

    model = tmp_path / "model.stl"
    model.write_bytes(b"solid model")
    paths = RealizedConfigPaths(*(tmp_path / name for name in (
        "printer.realized.json", "process.realized.json", "filament.realized.json"
    )))
    for path in (paths.printer, paths.process, paths.filament):
        path.write_text("{}", encoding="utf-8")
    (tmp_path / "model.gcode.3mf").write_bytes(b"auxiliary")
    job = SliceJob(
        model, ProfileInfo("p", ProfileKind.PROCESS), ProfileInfo("f", ProfileKind.FILAMENT),
        ProfileInfo("m", ProfileKind.PRINTER), tmp_path, realized_configs=paths
    )
    calls = 0

    def run(*_: Any, **__: Any) -> object:
        nonlocal calls
        calls += 1
        return SimpleNamespace(return_code=0, timed_out=False)

    monkeypatch.setattr(orca_module, "run_cli", run)
    adapter = OrcaSlicerAdapter(executable=tmp_path / "orca.exe")
    result = adapter._slice_realized(
        job, SlicerInfo(SlicerKind.ORCA_SLICER, "OrcaSlicer", tmp_path / "orca.exe", "2.3.2")
    )
    assert calls == 1
    assert result.gcode_path == tmp_path / "plate_1.gcode"
