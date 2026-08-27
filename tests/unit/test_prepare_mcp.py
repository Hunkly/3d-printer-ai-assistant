import ast
import inspect
import json
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from print_engineer.adapters.slicer.execution import SliceExecutionFailure
from print_engineer.config import Settings
from print_engineer.core.preparation import (
    DeterministicEvidence,
    EvidenceAuthority,
    EvidenceDetail,
    FailureStage,
    PreparationFailure,
    ProfileIdentity,
)
from print_engineer.core.preparation_service import PreparationService
from print_engineer.core.recommendation import RecommendationGoal
from print_engineer.core.types import ProfileKind
from print_engineer.mcp.tools.prepare import PrepareTools, _ready


def test_prepare_failure_is_public_and_sanitized(tmp_path: Path) -> None:
    tool = PrepareTools(Settings(root=tmp_path))
    response = tool.prepare(str(tmp_path / "missing.stl"), RecommendationGoal.BALANCED)
    assert response["ok"] is False
    assert response["error"]["code"] == "model_missing"
    assert response["error"]["details"] == {"status": "NOT_READY", "stage": "model_input"}
    assert str(tmp_path) not in str(response)


def test_prepare_public_signature_excludes_internal_controls() -> None:
    names = PrepareTools.prepare.__annotations__
    assert "timeout" not in names
    assert "workspace" not in names
    assert "use_defaults" not in names


def test_ready_serializer_emits_nullable_filament_setting_id() -> None:
    setup = SimpleNamespace(
        slicer="orca_slicer",
        printer=ProfileIdentity("Bambu Lab A1 0.4 nozzle", ProfileKind.PRINTER, "GM030"),
        process_profile=ProfileIdentity("0.20mm Standard @BBL A1", ProfileKind.PROCESS, "GP079"),
        filament_profile=ProfileIdentity("Bambu PLA Tough+ @base", ProfileKind.FILAMENT, None),
        material="PLA",
        nozzle_diameter_mm=0.4,
        build_plate="cool_plate",
        overrides=(),
    )
    result = SimpleNamespace(
        identity=SimpleNamespace(goal=RecommendationGoal.BALANCED),
        selected_setup=setup,
        slice_result=SimpleNamespace(
            layer_count=10,
            estimated_time_minutes=5,
            filament_used_mm=100.0,
            filament_used_cm3=1.0,
            filament_weight_g=None,
        ),
        artifact=SimpleNamespace(
            path=Path("artifact.3mf"), slice_run_id="run-1", sha256="a" * 64, size_bytes=1
        ),
        verification=SimpleNamespace(status="pass"),
    )
    payload = json.loads(json.dumps(_ready(cast(Any, result))))
    assert payload["preparation"]["setup"]["printer"]["setting_id"] == "GM030"
    assert payload["preparation"]["setup"]["process"]["setting_id"] == "GP079"
    assert payload["preparation"]["setup"]["filament"]["setting_id"] is None
    assert "setting_id" in payload["preparation"]["setup"]["filament"]


REALIZATION_CASES = {
    "unsupported_slicer_version": (
        "unsupported_slicer_version",
        "The requested slicer version is unsupported.",
        set(),
    ),
    "printer_profile_missing": (
        "printer_profile_missing",
        "The printer profile is unavailable.",
        {"profile_kind"},
    ),
    "process_profile_missing": (
        "process_profile_missing",
        "The process profile is unavailable.",
        {"profile_kind"},
    ),
    "filament_profile_missing": (
        "filament_profile_missing",
        "The filament profile is unavailable.",
        {"profile_kind"},
    ),
    "ambiguous_profile_resolution": (
        "ambiguous_profile_resolution",
        "Profile resolution was ambiguous.",
        set(),
    ),
    "wrong_profile_kind": (
        "wrong_profile_kind",
        "A selected profile has the wrong kind.",
        {"profile_kind"},
    ),
    "profile_materialization_failed": (
        "profile_materialization_failed",
        "A selected profile could not be materialized.",
        {"profile_kind"},
    ),
    "profile_content_invalid": (
        "profile_content_invalid",
        "A selected profile is invalid.",
        {"profile_kind"},
    ),
    "incompatible_profiles": (
        "incompatible_profiles",
        "The selected profiles are incompatible.",
        set(),
    ),
    "unsupported_nozzle": (
        "unsupported_nozzle",
        "The selected nozzle is unsupported by the printer profile.",
        {"supported_values"},
    ),
    "build_plate_not_representable": (
        "build_plate_not_representable",
        "The selected build plate cannot be represented.",
        set(),
    ),
    "material_not_provable": (
        "material_not_provable",
        "The filament material cannot be verified.",
        set(),
    ),
    "material_profile_mismatch": (
        "material_profile_mismatch",
        "The selected material conflicts with the filament profile.",
        set(),
    ),
    "unsupported_override": (
        "unsupported_override",
        "A requested setup override is unsupported.",
        {"field"},
    ),
    "invalid_effective_value": (
        "invalid_effective_value",
        "The effective setup contains an invalid value.",
        {"field"},
    ),
    "effective_settings_mismatch": (
        "effective_settings_mismatch",
        "The effective setup does not match the selected setup.",
        {"field"},
    ),
}
SLICE_CASES = {
    k: (k, msg, {"timeout_seconds"} if k == "slicer_timeout" else set())
    for k, msg in {
        "invalid_source_model": "The source model is invalid.",
        "source_model_identity_mismatch": "The source model identity could not be verified.",
        "workspace_creation_failed": "The slice workspace could not be created.",
        "config_materialization_failed": "The realized slicer configuration could not be prepared.",
        "config_verification_failed": "The realized slicer configuration could not be verified.",
        "slicer_unavailable": "The slicer is unavailable.",
        "slicer_timeout": "The slicer timed out.",
        "slicer_process_failed": "The slicer process failed.",
        "slice_output_missing": "The slice output is missing.",
        "slice_output_invalid": "The slice output is invalid.",
        "slice_facts_invalid": "The slice facts are invalid.",
        "candidate_artifact_identity_failed": "The slice artifact identity could not be verified.",
    }.items()
}
FINALIZATION_CASES = {
    k: (k, msg)
    for group, msg, _allowed in [
        (
            {"workspace_missing", "workspace_reparse_or_unsafe", "workspace_not_directory"},
            "The slice workspace failed final verification.",
            frozenset[str](),
        ),
        (
            {
                "candidate_run_mismatch",
                "candidate_path_mismatch",
                "candidate_missing",
                "candidate_not_file",
                "candidate_empty",
                "candidate_size_mismatch",
                "candidate_hash_mismatch",
            },
            "The slice artifact failed final verification.",
            frozenset[str](),
        ),
        (
            {
                "printer_config_path_mismatch",
                "printer_config_missing",
                "printer_config_not_file",
                "printer_config_invalid",
                "printer_config_identity_mismatch",
            },
            "The printer realized configuration failed final verification.",
            frozenset[str](),
        ),
        (
            {
                "process_config_path_mismatch",
                "process_config_missing",
                "process_config_not_file",
                "process_config_invalid",
                "process_config_identity_mismatch",
            },
            "The process realized configuration failed final verification.",
            frozenset[str](),
        ),
        (
            {
                "filament_config_path_mismatch",
                "filament_config_missing",
                "filament_config_not_file",
                "filament_config_invalid",
                "filament_config_identity_mismatch",
            },
            "The filament realized configuration failed final verification.",
            frozenset[str](),
        ),
        (
            {"unsupported_slicer_version"},
            "The slicer identity failed final verification.",
            frozenset[str](),
        ),
    ]
    for k in group
}


def _dict_keys(function: Any) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys.update(
                k.value
                for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            )
    return keys


@pytest.mark.parametrize("category, expected", REALIZATION_CASES.items())
def test_realization_failure_taxonomy_is_exact(
    category: str, expected: tuple[str, str, set[str]]
) -> None:
    code, message, allowed = expected
    details = tuple(EvidenceDetail(key, "trusted") for key in allowed)
    failure = PreparationFailure(FailureStage.REALIZATION, category, "SECRET_RAW_MESSAGE", details)
    mapped = PreparationService._mapped_realization(failure)
    assert (mapped.code, mapped.stage.value, mapped.message) == (code, "realization", message)
    assert {item.key for item in mapped.details} == allowed


@pytest.mark.parametrize("stage, expected", SLICE_CASES.items())
def test_slice_failure_taxonomy_is_exact(stage: str, expected: tuple[str, str, set[str]]) -> None:
    code, message, allowed = expected
    mapped = PreparationService._mapped_slice(
        SliceExecutionFailure("run", stage, "SECRET_DIAGNOSTIC")
    )
    assert (mapped.code, mapped.stage.value, mapped.message) == (code, "slicing", message)
    assert {item.key for item in mapped.details} == set()


@pytest.mark.parametrize("category, expected", FINALIZATION_CASES.items())
def test_finalization_failure_taxonomy_is_exact(category: str, expected: tuple[str, str]) -> None:
    code, message = expected
    mapped = PreparationService._mapped_finalization(
        PreparationFailure(FailureStage.FINAL_VERIFICATION, category, "SECRET_RAW_MESSAGE")
    )
    assert (mapped.code, mapped.stage.value, mapped.message) == (
        code,
        "final_verification",
        message,
    )
    assert mapped.details == ()


def test_failure_taxonomy_sets_match_production_tables() -> None:
    realization_source = inspect.getsource(PreparationService._mapped_realization)
    slice_source = inspect.getsource(PreparationService._mapped_slice)
    assert set(REALIZATION_CASES) == _dict_keys(PreparationService._mapped_realization)
    assert set(SLICE_CASES) == _dict_keys(PreparationService._mapped_slice)
    final_source = textwrap.dedent(inspect.getsource(PreparationService._mapped_finalization))
    final_literals = {
        node.value
        for node in ast.walk(ast.parse(final_source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert set(FINALIZATION_CASES) == {
        value for value in final_literals if value in FINALIZATION_CASES
    }
    assert realization_source and slice_source and final_source


@pytest.mark.parametrize(
    "unsafe",
    [
        "command",
        "argv",
        "stdout",
        "stderr",
        "workspace",
        "workspace_path",
        "output_dir",
        "source",
        "source_path",
        "model_path",
        "config_path",
        "output_path",
        "profile_path",
        "profile_json",
        "environment",
        "traceback",
        "diagnostic",
        "unknown_detail",
    ],
)
def test_public_failure_serializer_drops_each_unsafe_sentinel(unsafe: str) -> None:
    sentinel = "SECRET_" + unsafe.upper() + "_7341"
    failure = PreparationFailure(
        FailureStage.REALIZATION,
        "unsupported_slicer_version",
        "safe",
        (EvidenceDetail(unsafe, sentinel),),
    )
    result = SimpleNamespace(failure=failure)
    from print_engineer.mcp.tools.prepare import _failure

    response = str(_failure(cast(Any, result)))
    assert sentinel not in response


@pytest.mark.parametrize(
    "category", ["unsupported_slicer_version", "__future_realization_failure__"]
)
def test_raw_realization_message_is_never_public(category: str) -> None:
    from print_engineer.mcp.tools.prepare import _failure

    payload = _failure(
        cast(
            Any,
            SimpleNamespace(
                failure=PreparationService._mapped_realization(
                    PreparationFailure(FailureStage.REALIZATION, category, "SECRET_TRACEBACK_992B")
                )
            ),
        )
    )
    assert "SECRET_TRACEBACK_992B" not in json.dumps(payload)


@pytest.mark.parametrize("kind", ["realization", "slice", "finalization"])
def test_raw_internal_message_is_never_public_for_each_pipeline_mapper(kind: str) -> None:
    from print_engineer.mcp.tools.prepare import _failure

    if kind == "realization":
        failure = PreparationService._mapped_realization(
            PreparationFailure(
                FailureStage.REALIZATION, "unsupported_slicer_version", "SECRET_RAW_MESSAGE"
            )
        )
    elif kind == "slice":
        failure = PreparationService._mapped_slice(
            SliceExecutionFailure("run", "slicer_process_failed", "SECRET_RAW_MESSAGE")
        )
    else:
        failure = PreparationService._mapped_finalization(
            PreparationFailure(
                FailureStage.FINAL_VERIFICATION, "workspace_missing", "SECRET_RAW_MESSAGE"
            )
        )
    assert "SECRET_RAW_MESSAGE" not in json.dumps(
        _failure(cast(Any, SimpleNamespace(failure=failure)))
    )


@pytest.mark.parametrize("stage", ["__future_slice_failure__"])
def test_unknown_slice_failure_is_safe(stage: str) -> None:
    from print_engineer.mcp.tools.prepare import _failure

    mapped = PreparationService._mapped_slice(
        SliceExecutionFailure("run", stage, "SECRET_DIAGNOSTIC")
    )
    payload = _failure(cast(Any, SimpleNamespace(failure=mapped)))
    assert payload["error"] == {
        "code": "slice_failed",
        "message": "The slicing operation failed.",
        "details": {"status": "NOT_READY", "stage": "slicing"},
    }


def test_unknown_finalization_failure_is_safe() -> None:
    from print_engineer.mcp.tools.prepare import _failure

    mapped = PreparationService._mapped_finalization(
        PreparationFailure(
            FailureStage.FINAL_VERIFICATION, "__future_finalization_failure__", "SECRET_RAW_MESSAGE"
        )
    )
    payload = _failure(cast(Any, SimpleNamespace(failure=mapped)))
    assert payload["error"] == {
        "code": "finalization_failed",
        "message": "The prepared result failed final verification.",
        "details": {"status": "NOT_READY", "stage": "final_verification"},
    }


def test_public_failure_allowlist_positive() -> None:
    from print_engineer.mcp.tools.prepare import _failure

    details = tuple(
        EvidenceDetail(k, v)
        for k, v in [
            ("field", "nozzle"),
            ("profile_kind", "printer"),
            ("supported_values", "0.4,0.6"),
            ("timeout_seconds", "600"),
        ]
    )
    payload = _failure(
        cast(
            Any,
            SimpleNamespace(
                failure=PreparationFailure(FailureStage.SLICING, "slicer_timeout", "raw", details)
            ),
        )
    )
    assert payload["error"]["details"]["field"] == "nozzle"
    assert payload["error"]["details"]["profile_kind"] == "printer"
    assert payload["error"]["details"]["supported_values"] == "0.4,0.6"
    assert payload["error"]["details"]["timeout_seconds"] == "600"
    assert set(payload["error"]["details"]) == {
        "status",
        "stage",
        "field",
        "profile_kind",
        "supported_values",
        "timeout_seconds",
    }


def test_ready_serializer_drops_internal_evidence_but_retains_artifact() -> None:
    from print_engineer.mcp.tools.prepare import _ready

    setup = SimpleNamespace(
        slicer="orca_slicer",
        printer=ProfileIdentity("P", ProfileKind.PRINTER, None),
        process_profile=ProfileIdentity("Q", ProfileKind.PROCESS, None),
        filament_profile=ProfileIdentity("F", ProfileKind.FILAMENT, None),
        material="PLA",
        nozzle_diameter_mm=0.4,
        build_plate="cool_plate",
        overrides=(),
    )
    result = SimpleNamespace(
        identity=SimpleNamespace(goal=RecommendationGoal.BALANCED),
        selected_setup=setup,
        evidence=(
            DeterministicEvidence(
                EvidenceAuthority.VERIFICATION,
                "SECRET_READY_EVIDENCE",
                "SECRET_READY_WORKSPACE",
                (
                    EvidenceDetail("config_path", "SECRET_READY_CONFIG"),
                    EvidenceDetail("profile_root", "SECRET_READY_PROFILE_ROOT"),
                ),
            ),
        ),
        slice_result=SimpleNamespace(
            layer_count=1,
            estimated_time_minutes=1,
            filament_used_mm=1,
            filament_used_cm3=1,
            filament_weight_g=None,
        ),
        artifact=SimpleNamespace(
            path=Path("FINAL_ARTIFACT.3mf"), slice_run_id="run", sha256="a" * 64, size_bytes=1
        ),
        verification=SimpleNamespace(status="pass"),
    )
    payload = _ready(cast(Any, result))
    serialized = json.dumps(payload)
    for sentinel in (
        "SECRET_READY_WORKSPACE",
        "SECRET_READY_CONFIG",
        "SECRET_READY_PROFILE_ROOT",
        "SECRET_READY_EVIDENCE",
    ):
        assert sentinel not in serialized
    assert payload["preparation"]["artifact"]["path"] == "FINAL_ARTIFACT.3mf"
