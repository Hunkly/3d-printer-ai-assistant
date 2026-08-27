import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from print_engineer.adapters.slicer.execution import SliceExecutionFailure
from print_engineer.config import Settings
from print_engineer.core.preparation import (
    FailureStage,
    ModelIdentity,
    NotReadyPreparationResult,
    PreparationAuthority,
    PreparationFailure,
    PreparationIdentity,
    ProfileIdentity,
    SelectedSetup,
)
from print_engineer.core.preparation_service import PreparationService, _DomainFailure
from print_engineer.core.recommendation import FilamentCandidate, RecommendationGoal
from print_engineer.core.types import ProfileInfo, ProfileKind, SlicerKind

# The loop intentionally installs per-iteration test doubles below.
# ruff: noqa: B023


def _authority_selection(
    printer_id: str | None = "GM030",
    process_id: str | None = "GP079",
    filament_id: str | None = None,
) -> Any:
    printer = ProfileInfo("Bambu Lab A1 0.4 nozzle", ProfileKind.PRINTER, setting_id=printer_id)
    process = ProfileInfo("0.20mm Standard @BBL A1", ProfileKind.PROCESS, setting_id=process_id)
    candidate = FilamentCandidate(
        profile_name="Bambu PLA Tough+ @base", setting_id=filament_id, material_type="PLA"
    )
    context = SimpleNamespace(
        build_plate="cool_plate",
        printer=SimpleNamespace(supported_nozzle_mm=(0.4,)),
    )
    recommendation = SimpleNamespace(
        goal=RecommendationGoal.BALANCED,
        nozzle=SimpleNamespace(nozzle_diameter_mm=0.4),
        material=None,
    )
    authority = SimpleNamespace(
        context=context, printer_profiles=(printer,), process_profile=process
    )
    return SimpleNamespace(
        recommendation=recommendation,
        context_authority=authority,
        filament_candidate=candidate,
    )


def _profiles_for(selection: SimpleNamespace) -> dict[ProfileKind, list[ProfileInfo]]:
    return {
        ProfileKind.PRINTER: [selection.context_authority.printer_profiles[0]],
        ProfileKind.PROCESS: [selection.context_authority.process_profile],
        ProfileKind.FILAMENT: [
            ProfileInfo("Bambu PLA Tough+ @base", ProfileKind.FILAMENT, setting_id=None)
        ],
    }


def _service_with_profiles(
    tmp_path: Path, selection: SimpleNamespace, profiles: dict[ProfileKind, list[ProfileInfo]]
) -> PreparationService:
    settings = Settings(root=tmp_path)
    settings.slicer.orca_appdata_path = tmp_path
    service = PreparationService.from_settings(settings)
    service.repository.list_profiles = lambda kind: profiles[kind]  # type: ignore[method-assign]
    return service


def test_none_setting_ids_are_valid_when_each_authority_tuple_is_unique(tmp_path: Path) -> None:
    selection = _authority_selection(None, None, None)
    service = _service_with_profiles(tmp_path, selection, _profiles_for(selection))
    setup = service._selected_setup(selection, SimpleNamespace())  # type: ignore[arg-type]
    assert setup.printer == ProfileIdentity("Bambu Lab A1 0.4 nozzle", ProfileKind.PRINTER, None)
    assert setup.process_profile == ProfileIdentity(
        "0.20mm Standard @BBL A1", ProfileKind.PROCESS, None
    )
    assert setup.filament_profile == ProfileIdentity(
        "Bambu PLA Tough+ @base", ProfileKind.FILAMENT, None
    )


@pytest.mark.parametrize(
    ("role", "kind"),
    [
        ("printer", ProfileKind.PRINTER),
        ("process", ProfileKind.PROCESS),
        ("filament", ProfileKind.FILAMENT),
    ],
)
@pytest.mark.parametrize(
    "failure_code", ["profile_authority_missing", "profile_authority_ambiguous"]
)
def test_authority_resolution_fails_closed_without_downstream(
    tmp_path: Path,
    role: str,
    kind: ProfileKind,
    failure_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = tmp_path / "cube.stl"
    model.write_bytes(b"solid cube\n")
    selection = _authority_selection()
    profiles = _profiles_for(selection)
    if failure_code == "profile_authority_missing":
        profiles[kind] = []
    else:
        profiles[kind] = profiles[kind] * 2
    service = _service_with_profiles(tmp_path, selection, profiles)
    monkeypatch.setattr(service.setup_engine, "recommend_authoritative", lambda request: selection)
    monkeypatch.setattr(
        service.realizer, "realize", lambda authority: pytest.fail("realizer called")
    )
    monkeypatch.setattr(
        service.executor, "execute", lambda *args, **kwargs: pytest.fail("executor called")
    )
    monkeypatch.setattr(
        service.finalizer, "finalize", lambda *args, **kwargs: pytest.fail("finalizer called")
    )
    result = service.prepare(str(model), "balanced")
    assert isinstance(result, NotReadyPreparationResult)
    assert result.failure.code == failure_code
    assert result.failure.stage.value == "setup_selection"
    assert result.failure.details[0].key == "profile_kind"
    assert result.failure.details[0].value == role


@pytest.mark.parametrize("setting_id", [None, "ABC"])
def test_same_name_profiles_match_only_the_exact_setting_id(
    tmp_path: Path, setting_id: str | None
) -> None:
    selection = _authority_selection(filament_id=setting_id)
    profiles = _profiles_for(selection)
    profiles[ProfileKind.FILAMENT] = [
        ProfileInfo("Bambu PLA Tough+ @base", ProfileKind.FILAMENT, setting_id=None),
        ProfileInfo("Bambu PLA Tough+ @base", ProfileKind.FILAMENT, setting_id="ABC"),
    ]
    service = _service_with_profiles(tmp_path, selection, profiles)
    setup = service._selected_setup(selection, SimpleNamespace())  # type: ignore[arg-type]
    assert setup.filament_profile.setting_id == setting_id


@pytest.mark.parametrize("setting_id", [None, "ABC"])
def test_duplicate_exact_authority_is_ambiguous(tmp_path: Path, setting_id: str | None) -> None:
    selection = _authority_selection(filament_id=setting_id)
    profiles = _profiles_for(selection)
    profiles[ProfileKind.FILAMENT] = [
        ProfileInfo("Bambu PLA Tough+ @base", ProfileKind.FILAMENT, setting_id=setting_id),
        ProfileInfo("Bambu PLA Tough+ @base", ProfileKind.FILAMENT, setting_id=setting_id),
    ]
    service = _service_with_profiles(tmp_path, selection, profiles)
    with pytest.raises(_DomainFailure) as raised:
        service._require_unique_profile_authority(
            ProfileIdentity("Bambu PLA Tough+ @base", ProfileKind.FILAMENT, setting_id),
            "filament",
        )
    assert raised.value.code == "profile_authority_ambiguous"


def test_non_null_real_authority_ids_are_preserved_exactly(tmp_path: Path) -> None:
    selection = _authority_selection()
    service = _service_with_profiles(tmp_path, selection, _profiles_for(selection))
    setup = service._selected_setup(selection, SimpleNamespace())  # type: ignore[arg-type]
    assert setup.printer.setting_id == "GM030"
    assert setup.process_profile.setting_id == "GP079"
    assert setup.filament_profile.setting_id is None


def test_missing_model_fails_before_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(root=tmp_path)
    service = PreparationService.from_settings(settings)
    called = {"setup": 0, "realizer": 0, "executor": 0, "finalizer": 0}

    def fail(*args: object, **kwargs: object) -> None:
        called["setup"] += 1
        raise AssertionError("setup must not run")

    monkeypatch.setattr(service.setup_engine, "recommend_authoritative", fail)
    monkeypatch.setattr(
        service.realizer, "realize", lambda *args, **kwargs: called.__setitem__("realizer", 1)
    )
    monkeypatch.setattr(
        service.executor, "execute", lambda *args, **kwargs: called.__setitem__("executor", 1)
    )
    monkeypatch.setattr(
        service.finalizer, "finalize", lambda *args, **kwargs: called.__setitem__("finalizer", 1)
    )
    result = service.prepare("missing.stl", "balanced")
    assert isinstance(result, NotReadyPreparationResult)
    assert result.failure.code == "model_missing"
    assert result.failure.stage.value == "model_input"
    assert called == {"setup": 0, "realizer": 0, "executor": 0, "finalizer": 0}


def test_relative_model_identity_is_absolute_and_hashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = tmp_path / "-cube.stl"
    model.write_bytes(b"solid cube\n")
    settings = Settings(root=tmp_path)
    service = PreparationService.from_settings(settings)
    monkeypatch.chdir(tmp_path)
    result = service._model("-cube.stl")
    identity, failure = result
    assert failure is None
    assert identity is not None
    assert identity.path.is_absolute()
    assert not str(identity.path).startswith("-")
    assert len(identity.sha256 or "") == 64


@pytest.mark.parametrize(
    "setting_id,marker", [("SYSTEM", "SYSTEM_CONTENT"), ("USER", "USER_CONTENT")]
)
def test_setup_realizer_materializes_the_selected_exact_root(
    tmp_path: Path, setting_id: str, marker: str
) -> None:
    def write(kind: str, name: str, source: str, data: dict[str, object]) -> None:
        directory = tmp_path / ("system/BBL" if source == "SYSTEM" else "user/account") / kind
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.json").write_text(
            json.dumps({"name": name, "setting_id": source, **data}), encoding="utf-8"
        )

    printer_name = "Same Printer"
    process_name = "Same Process"
    filament_name = "Same Filament"
    for source in ("SYSTEM", "USER"):
        write(
            "machine",
            printer_name,
            source,
            {
                "printer_model": printer_name,
                "nozzle_diameter": ["0.4"],
                "marker": marker if source == setting_id else f"{source}_CONTENT",
            },
        )
        write(
            "process",
            process_name,
            source,
            {
                "compatible_printers": [printer_name],
                "layer_height": "0.2",
                "marker": marker if source == setting_id else f"{source}_CONTENT",
            },
        )
        write(
            "filament",
            filament_name,
            source,
            {
                "filament_type": "PLA",
                "marker": marker if source == setting_id else f"{source}_CONTENT",
            },
        )

    settings = Settings(root=tmp_path)
    settings.slicer.orca_appdata_path = tmp_path
    service = PreparationService.from_settings(settings)
    setup = SelectedSetup(
        slicer=SlicerKind.ORCA_SLICER,
        printer=ProfileIdentity(printer_name, ProfileKind.PRINTER, setting_id),
        nozzle_diameter_mm=0.4,
        build_plate="cool_plate",
        material="PLA",
        filament_profile=ProfileIdentity(filament_name, ProfileKind.FILAMENT, setting_id),
        process_profile=ProfileIdentity(process_name, ProfileKind.PROCESS, setting_id),
        overrides=(),
    )
    result = service.realizer.realize(
        PreparationAuthority(
            PreparationIdentity(ModelIdentity(tmp_path / "cube.stl"), RecommendationGoal.BALANCED),
            setup,
        )
    )
    assert result.succeeded
    assert result.effective_inputs is not None
    assert result.effective_inputs.printer.identity.setting_id == setting_id
    assert result.effective_inputs.process.identity.setting_id == setting_id
    assert result.effective_inputs.filament.identity.setting_id == setting_id
    assert all(
        marker in reference.content
        for reference in (
            result.effective_inputs.printer,
            result.effective_inputs.process,
            result.effective_inputs.filament,
        )
    )


@pytest.mark.parametrize(
    ("supported", "default", "explicit", "expected"),
    [
        ((0.6,), 0.4, 0.6, 0.6),
        ((0.6,), 0.4, None, 0.6),
        ((0.4, 0.6), 0.6, None, 0.6),
        ((0.4, 0.6), 0.8, None, 0.4),
        ((0.6,), 0.8, None, 0.6),
        ((0.6, 0.8), 1.0, None, None),
        ((0.4, 0.6), None, None, 0.4),
        ((0.6,), None, None, 0.6),
        ((0.6, 0.8), None, None, None),
    ],
)
def test_authoritative_nozzle_precedence_matrix(
    tmp_path: Path,
    supported: tuple[float, ...],
    default: float | None,
    explicit: float | None,
    expected: float | None,
) -> None:
    settings = Settings(root=tmp_path)
    settings.recommend.default_nozzle_diameter = default
    service = PreparationService.from_settings(settings)
    printer = SimpleNamespace(name="P", supported_nozzle_mm=supported, nozzle_diameter_mm=None)
    intent = SimpleNamespace(nozzle_diameter_mm=explicit, use_defaults=True)
    warnings: list[str] = []
    assert service.reader is not None
    from print_engineer.recommendation.context import PrintContextResolver

    resolver = PrintContextResolver(settings, adapter=service.reader)
    selected_nozzle = resolver._resolve_nozzle(
        cast(Any, intent), cast(Any, printer), warnings, authoritative=True
    )
    assert selected_nozzle == expected
    selection = _authority_selection()
    selection.recommendation = SimpleNamespace(
        goal=RecommendationGoal.BALANCED,
        nozzle=SimpleNamespace(nozzle_diameter_mm=selected_nozzle),
        material=None,
    )
    selection.context_authority.context.printer = printer
    service.repository.list_profiles = lambda kind: _profiles_for(selection)[kind]  # type: ignore[method-assign]
    if expected is None:
        with pytest.raises(_DomainFailure, match="nozzle_not_authoritative"):
            service._selected_setup(selection, intent)  # type: ignore[arg-type]
    else:
        setup = service._selected_setup(selection, intent)  # type: ignore[arg-type]
        assert setup.nozzle_diameter_mm == expected


def test_nozzle_ambiguity_is_order_invariant(tmp_path: Path) -> None:
    settings = Settings(root=tmp_path)
    settings.recommend.default_nozzle_diameter = 1.0
    service = PreparationService.from_settings(settings)
    from print_engineer.recommendation.context import PrintContextResolver

    resolver = PrintContextResolver(settings, adapter=service.reader)
    values = []
    for supported in ((0.6, 0.8), (0.8, 0.6)):
        values.append(
            resolver._resolve_nozzle(
                cast(Any, SimpleNamespace(nozzle_diameter_mm=None, use_defaults=True)),
                cast(Any, SimpleNamespace(nozzle_diameter_mm=None, supported_nozzle_mm=supported)),
                [],
                authoritative=True,
            )
        )
    assert values == [None, None]


def _pipeline_service(tmp_path: Path) -> PreparationService:
    settings = Settings(root=tmp_path)
    service = PreparationService.from_settings(settings)
    selection = _authority_selection()
    service.setup_engine.recommend_authoritative = lambda request: selection  # type: ignore[method-assign]
    service.repository.list_profiles = lambda kind: _profiles_for(selection)[kind]  # type: ignore[method-assign]
    return service


def _bump(counts: dict[str, int], key: str) -> bool:
    counts[key] += 1
    return False


def test_prepare_short_circuits_every_lifecycle_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = tmp_path / "cube.stl"
    model.write_bytes(b"solid cube\n")
    for boundary in (
        "model",
        "setup",
        "authority_missing",
        "authority_ambiguous",
        "realization",
        "slice",
        "finalizer",
    ):
        service = _pipeline_service(tmp_path / boundary)
        counts = {"setup": 0, "realizer": 0, "executor": 0, "finalizer": 0}
        if boundary == "model":
            monkeypatch.setattr(
                service,
                "_model",
                lambda value: (
                    None,
                    PreparationFailure(FailureStage.MODEL_INPUT, "model_invalid", "invalid"),
                ),
            )
        elif boundary == "setup":
            monkeypatch.setattr(
                service.setup_engine,
                "recommend_authoritative",
                lambda request: (
                    _bump(counts, "setup"),
                    (_ for _ in ()).throw(
                        _DomainFailure("setup_selection_failed", "failed", {"field": "setup"})
                    ),
                )[1],
            )
        else:
            monkeypatch.setattr(
                service.setup_engine,
                "recommend_authoritative",
                lambda request: _bump(counts, "setup") or _authority_selection(),
            )
        monkeypatch.setattr(
            service.realizer,
            "realize",
            lambda authority: (
                _bump(counts, "realizer")
                or SimpleNamespace(
                    succeeded=False,
                    failure=PreparationFailure(FailureStage.REALIZATION, "realization_failed", "x"),
                )
            ),
        )
        monkeypatch.setattr(
            service.executor,
            "execute",
            lambda *args, **kwargs: (
                _bump(counts, "executor") or SliceExecutionFailure("run", "slicer_timeout", "x")
            ),
        )
        monkeypatch.setattr(
            service.finalizer,
            "finalize",
            lambda value: _bump(counts, "finalizer") or None,
        )
        if boundary == "authority_missing":
            service.repository.list_profiles = lambda kind: []  # type: ignore[method-assign]
        elif boundary == "authority_ambiguous":
            selection = _authority_selection()
            service.repository.list_profiles = lambda kind: _profiles_for(selection)[kind] * 2  # type: ignore[method-assign]
            monkeypatch.setattr(
                service.setup_engine, "recommend_authoritative", lambda request: selection
            )
        if boundary == "setup":
            monkeypatch.setattr(
                service,
                "_selected_setup",
                lambda selection, request: (_ for _ in ()).throw(
                    _DomainFailure(
                        "setup_selection_failed",
                        "failed",
                        {"profile_kind": "printer"},
                    )
                ),
            )
        if boundary == "realization":
            pass
        elif boundary == "slice":
            monkeypatch.setattr(
                service.realizer,
                "realize",
                lambda authority: _bump(counts, "realizer") or SimpleNamespace(succeeded=True),
            )
        elif boundary == "finalizer":
            monkeypatch.setattr(
                service.realizer,
                "realize",
                lambda authority: _bump(counts, "realizer") or SimpleNamespace(succeeded=True),
            )
            monkeypatch.setattr(
                service.executor,
                "execute",
                lambda *args, **kwargs: _bump(counts, "executor") or object(),
            )
            monkeypatch.setattr(
                service.finalizer,
                "finalize",
                lambda value: (
                    _bump(counts, "finalizer")
                    or NotReadyPreparationResult(
                        PreparationIdentity(ModelIdentity(model), RecommendationGoal.BALANCED),
                        PreparationFailure(
                            FailureStage.FINAL_VERIFICATION, "finalization_failed", "x"
                        ),
                    )
                ),
            )
        path = str(model) if boundary != "model" else "missing.stl"
        result = service.prepare(path, "balanced")
        assert isinstance(result, NotReadyPreparationResult)
        assert max(counts.values()) <= 1
        if boundary == "model":
            assert counts == {"setup": 0, "realizer": 0, "executor": 0, "finalizer": 0}
        elif boundary in {"setup", "authority_missing", "authority_ambiguous"}:
            assert counts["realizer"] == counts["executor"] == counts["finalizer"] == 0
        elif boundary == "realization":
            assert counts["realizer"] == 1 and counts["executor"] == counts["finalizer"] == 0
        elif boundary == "slice":
            assert counts["realizer"] == counts["executor"] == 1 and counts["finalizer"] == 0
        else:
            assert counts == {"setup": 1, "realizer": 1, "executor": 1, "finalizer": 1}


def test_prepare_is_request_isolated_with_distinct_runs_and_workspaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = tmp_path / "a.stl", tmp_path / "b.stl"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    service = _pipeline_service(tmp_path)
    local = threading.local()
    seen: list[tuple[str, str, str, str]] = []

    def model(value: str) -> tuple[ModelIdentity, None]:
        stem = Path(value).stem
        identity = ModelIdentity(Path(value).resolve(), (stem[0] * 64))
        local.name = stem
        return identity, None

    monkeypatch.setattr(service, "_model", model)

    def setup(request: object) -> Any:
        name = local.name
        return _authority_selection(f"{name}-printer", f"{name}-process", f"{name}-filament")

    monkeypatch.setattr(service.setup_engine, "recommend_authoritative", setup)
    monkeypatch.setattr(
        service,
        "_selected_setup",
        lambda selection, request: SelectedSetup(
            SlicerKind.ORCA_SLICER,
            ProfileIdentity(local.name + "-printer", ProfileKind.PRINTER, local.name + "-printer"),
            0.4,
            "cool_plate",
            "PLA",
            ProfileIdentity(local.name + "-filament", ProfileKind.FILAMENT, None),
            ProfileIdentity(local.name + "-process", ProfileKind.PROCESS, local.name + "-process"),
        ),
    )
    gate = threading.Barrier(2)

    def realize(authority: object) -> Any:
        gate.wait()
        return SimpleNamespace(
            succeeded=True,
            authority=authority,
            preparation_authority=authority,
            run_id=local.name + "-run",
            workspace_path=tmp_path / local.name,
        )

    def execute(result: Any, **kwargs: object) -> Any:
        authority = result.preparation_authority
        seen.append(
            (
                authority.identity.model.path.name,
                authority.selected_setup.printer.setting_id,
                result.run_id,
                str(result.workspace_path),
            )
        )
        return object()

    monkeypatch.setattr(service.realizer, "realize", realize)
    monkeypatch.setattr(service.executor, "execute", execute)
    monkeypatch.setattr(
        service.finalizer,
        "finalize",
        lambda result: NotReadyPreparationResult(
            PreparationIdentity(
                ModelIdentity(Path(local.name + ".stl")), RecommendationGoal.BALANCED
            ),
            PreparationFailure(FailureStage.FINAL_VERIFICATION, "finalization_failed", "x"),
        ),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(service.prepare, (str(a), str(b)), ("balanced", "balanced")))
    assert len(seen) == 2
    assert {item[0] for item in seen} == {"a.stl", "b.stl"}
    assert {item[1] for item in seen} == {"a-printer", "b-printer"}
    assert seen[0][2] != seen[1][2]
    assert seen[0][3] != seen[1][3]
    assert not any(
        hasattr(service, name)
        for name in (
            "current_model",
            "current_setup",
            "current_authority",
            "current_workspace",
            "current_run",
        )
    )
