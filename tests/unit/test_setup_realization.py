"""Hermetic tests for the filesystem-free Phase 3 Increment 2 boundary."""

from __future__ import annotations

import builtins
import json
import math
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from print_engineer.adapters.slicer.profile import ProfileMaterializer, ProfileRepository
from print_engineer.adapters.slicer.realization import (
    ORCA_CAPABILITY,
    OverlayEntry,
    ProfileReference,
    RealizationResult,
    realize_setup,
)
from print_engineer.core.preparation import AppliedOverride, ProfileIdentity, SelectedSetup
from print_engineer.core.types import ProfileInfo, ProfileKind, SlicerKind


def _write(root: Path, kind: str, name: str, data: dict[str, object]) -> None:
    path = root / "system" / "BBL" / kind
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{name}.json").write_text(json.dumps({"name": name, **data}), encoding="utf-8")


@pytest.fixture
def store(tmp_path: Path) -> Path:
    _write(store := tmp_path, "machine", "A1", {
        "type": "machine", "nozzle_diameter": ["0.4", "0.6"], "printer_model": "A1",
    })
    _write(store, "process", "0.20 Standard", {
        "type": "process", "compatible_printers": ["A1"], "layer_height": "0.2",
    })
    _write(store, "filament", "Generic PLA", {"type": "filament", "filament_type": "PLA"})
    return store


def _setup(*, store_name: str = "A1", nozzle: float = 0.4, plate: str = "cool_plate",
           material: str = "PLA", overrides: tuple[AppliedOverride, ...] = ()) -> SelectedSetup:
    return SelectedSetup(
        slicer=SlicerKind.ORCA_SLICER,
        printer=ProfileIdentity(store_name, ProfileKind.PRINTER),
        nozzle_diameter_mm=nozzle,
        build_plate=plate,
        material=material,
        filament_profile=ProfileIdentity("Generic PLA", ProfileKind.FILAMENT),
        process_profile=ProfileIdentity("0.20 Standard", ProfileKind.PROCESS),
        overrides=overrides,
    )


def _realize(store: Path, setup: SelectedSetup) -> RealizationResult:
    return realize_setup(setup, ProfileRepository(store))


def _with_profile_authority(
    setup: SelectedSetup, field: str, identity: ProfileIdentity
) -> SelectedSetup:
    if field == "printer":
        return replace(setup, printer=identity)
    if field == "process_profile":
        return replace(setup, process_profile=identity)
    if field == "filament_profile":
        return replace(setup, filament_profile=identity)
    raise AssertionError(f"unsupported profile field: {field}")


@pytest.mark.parametrize(
    ("plate", "native", "observed"),
    [("cool_plate", "Cool Plate", "cool_plate"),
     ("textured_pei_plate", "Textured PEI Plate", "textured_plate"),
     ("high_temp_plate", "High Temp Plate", "hot_plate")],
)
def test_closed_build_plate_mapping(store: Path, plate: str, native: str, observed: str) -> None:
    result = _realize(store, _setup(plate=plate))
    assert result.succeeded
    assert result.effective_inputs is not None
    assert result.effective_inputs.native_build_plate == native
    assert result.effective_inputs.observed_build_plate == observed


def test_selected_setup_owns_build_plate_whitespace_canonicalization(store: Path) -> None:
    raw = _setup(plate=" cool_plate ")
    canonical = _setup(plate="cool_plate")
    assert raw.build_plate == "cool_plate"
    left = _realize(store, raw)
    right = _realize(store, canonical)
    assert left.succeeded and right.succeeded
    assert left.effective_inputs is not None and right.effective_inputs is not None
    assert left.effective_inputs.identity == right.effective_inputs.identity
    assert left.effective_inputs.native_build_plate == "Cool Plate"


@pytest.mark.parametrize(
    "plate", ["3", "Hot Plate", "Engineering Plate", "textured_pei", "COOL_PLATE"]
)
def test_unsupported_plate_fails_without_fallback(store: Path, plate: str) -> None:
    result = _realize(store, _setup(plate=plate))
    assert not result.succeeded
    assert result.failure is not None
    assert result.failure.code == "build_plate_not_representable"


@pytest.mark.parametrize("nozzle", [0.7, 0.0, -0.4])
def test_nozzle_requires_exact_printer_membership(store: Path, nozzle: float) -> None:
    if nozzle <= 0:
        with pytest.raises(ValueError):
            _setup(nozzle=nozzle)
        return
    result = _realize(store, _setup(nozzle=nozzle))
    assert not result.succeeded
    assert result.failure is not None
    assert result.failure.code == "unsupported_nozzle"


def test_supported_nozzles_are_canonical_and_effective(store: Path) -> None:
    for nozzle in (0.4, 0.6):
        result = _realize(store, _setup(nozzle=nozzle))
        assert result.succeeded
        assert result.effective_inputs is not None
        assert result.effective_inputs.nozzle_diameter == str(nozzle)
        assert result.effective_inputs.printer_overlay[-1].key == "nozzle_diameter"
        assert result.effective_inputs.printer_overlay[-1].value == str(nozzle)


def test_all_eight_overrides_have_closed_write_mapping(store: Path) -> None:
    overrides = (
        AppliedOverride("layer_height_mm", 0.16),
        AppliedOverride("wall_loops", 3),
        AppliedOverride("sparse_infill_percent", 35.0),
        AppliedOverride("sparse_infill_pattern", " gyroid "),
        AppliedOverride("support_enablement", True),
        AppliedOverride("support_type", " tree(auto) "),
        AppliedOverride("support_threshold_angle_deg", 45.0),
        AppliedOverride("outer_wall_speed_mms", 80.0),
    )
    result = _realize(store, _setup(overrides=overrides))
    assert result.succeeded
    assert result.effective_inputs is not None
    values = {
        entry.key: (entry.value, entry.layer)
        for entry in result.effective_inputs.process_overlay
    }
    assert values == {
        "enable_support": ("1", "process"), "layer_height": ("0.16", "process"),
        "outer_wall_speed": ("80", "process"), "sparse_infill_density": ("35%", "process"),
        "sparse_infill_pattern": ("gyroid", "process"),
        "support_threshold_angle": ("45", "process"),
        "support_type": ("tree(auto)", "process"), "wall_loops": ("3", "process"),
    }


def test_support_threshold_zero_is_canonical_and_effective(store: Path) -> None:
    result = _realize(
        store, _setup(overrides=(AppliedOverride("support_threshold_angle_deg", 0.0),))
    )
    assert result.succeeded
    assert result.effective_inputs is not None
    assert result.effective_inputs.process_overlay[0].value == "0"


def test_actual_identity_preserves_authoritative_override_order(store: Path) -> None:
    overrides = (AppliedOverride("wall_loops", 3), AppliedOverride("layer_height_mm", 0.16))
    setup = _setup(overrides=overrides)
    result = _realize(store, setup)
    assert result.effective_inputs is not None
    assert result.effective_inputs.actual_inputs.overrides == overrides
    assert result.effective_inputs.actual_inputs.matches(setup)


def test_all_profile_kinds_require_exact_identity(store: Path) -> None:
    cases = (
        ("printer", "Missing", "printer_profile_missing"),
        ("process", "Missing", "process_profile_missing"),
        ("filament", "Missing", "filament_profile_missing"),
    )
    for kind, missing, code in cases:
        setup = _setup()
        if kind == "printer":
            setup = SelectedSetup(
                slicer=setup.slicer,
                printer=ProfileIdentity(missing, ProfileKind.PRINTER),
                nozzle_diameter_mm=setup.nozzle_diameter_mm,
                build_plate=setup.build_plate,
                material=setup.material,
                filament_profile=setup.filament_profile,
                process_profile=setup.process_profile,
            )
        elif kind == "process":
            setup = SelectedSetup(
                slicer=setup.slicer, printer=setup.printer,
                nozzle_diameter_mm=setup.nozzle_diameter_mm, build_plate=setup.build_plate,
                material=setup.material, filament_profile=setup.filament_profile,
                process_profile=ProfileIdentity(missing, ProfileKind.PROCESS),
            )
        elif kind == "filament":
            setup = SelectedSetup(
                slicer=setup.slicer, printer=setup.printer,
                nozzle_diameter_mm=setup.nozzle_diameter_mm, build_plate=setup.build_plate,
                material=setup.material,
                filament_profile=ProfileIdentity(missing, ProfileKind.FILAMENT),
                process_profile=setup.process_profile,
            )
        result = _realize(store, setup)
        assert not result.succeeded
        assert result.failure is not None and result.failure.code == code


def test_same_name_shadow_cannot_change_exact_setting_id_materialization(store: Path) -> None:
    system = store / "system" / "BBL" / "machine" / "A1.json"
    data = json.loads(system.read_text(encoding="utf-8"))
    data["setting_id"] = "exact-a"
    system.write_text(json.dumps(data), encoding="utf-8")
    user = store / "user" / "shadow" / "machine"
    user.mkdir(parents=True)
    shadow = {**data, "setting_id": "different-b", "nozzle_diameter": ["0.7"]}
    (user / "A1.json").write_text(json.dumps(shadow), encoding="utf-8")
    setup = SelectedSetup(
        slicer=SlicerKind.ORCA_SLICER,
        printer=ProfileIdentity("A1", ProfileKind.PRINTER, "exact-a"),
        nozzle_diameter_mm=0.4, build_plate="cool_plate", material="PLA",
        filament_profile=ProfileIdentity("Generic PLA", ProfileKind.FILAMENT),
        process_profile=ProfileIdentity("0.20 Standard", ProfileKind.PROCESS),
    )
    result = _realize(store, setup)
    assert result.succeeded
    assert result.effective_inputs is not None
    assert result.effective_inputs.printer.identity.setting_id == "exact-a"
    assert result.effective_inputs.printer.content_sha256
    assert "0.7" not in result.effective_inputs.printer.content


def test_unsupported_capability_fails_closed(store: Path) -> None:
    result = realize_setup(_setup(), ProfileRepository(store), capability="OrcaSlicer 2.4.0")
    assert not result.succeeded
    assert result.failure is not None
    assert result.failure.code == "unsupported_slicer_version"


def test_exact_profile_root_cannot_be_shadow_substituted(store: Path) -> None:
    system = store / "system" / "BBL" / "machine" / "A1.json"
    data = json.loads(system.read_text(encoding="utf-8"))
    data["setting_id"] = "system-a"
    system.write_text(json.dumps(data), encoding="utf-8")
    user = store / "user" / "shadow" / "machine"
    user.mkdir(parents=True)
    shadow = dict(data)
    shadow["setting_id"] = "user-b"
    shadow["printer_model"] = "SHADOW"
    (user / "A1.json").write_text(json.dumps(shadow), encoding="utf-8")
    result = _realize(
        store,
        SelectedSetup(
            slicer=SlicerKind.ORCA_SLICER,
            printer=ProfileIdentity("A1", ProfileKind.PRINTER, "system-a"),
            nozzle_diameter_mm=0.4,
            build_plate="cool_plate",
            material="PLA",
            filament_profile=ProfileIdentity("Generic PLA", ProfileKind.FILAMENT),
            process_profile=ProfileIdentity("0.20 Standard", ProfileKind.PROCESS),
        ),
    )
    assert result.succeeded
    assert result.effective_inputs is not None
    assert result.effective_inputs.printer.content.find("SHADOW") == -1


def test_profile_semantic_json_identity_ignores_formatting(store: Path) -> None:
    first = _realize(store, _setup())
    printer = store / "system" / "BBL" / "machine" / "A1.json"
    data = json.loads(printer.read_text(encoding="utf-8"))
    printer.write_text(json.dumps({key: data[key] for key in reversed(data)}), encoding="utf-8")
    second = _realize(store, _setup())
    assert first.effective_inputs is not None and second.effective_inputs is not None
    assert first.effective_inputs.identity == second.effective_inputs.identity


def test_identity_is_canonical_and_order_independent(store: Path) -> None:
    first = (AppliedOverride("wall_loops", 3), AppliedOverride("layer_height_mm", 0.16))
    second = tuple(reversed(first))
    left = _realize(store, _setup(overrides=first))
    right = _realize(store, _setup(overrides=second))
    assert left.effective_inputs is not None and right.effective_inputs is not None
    assert left.effective_inputs.identity == right.effective_inputs.identity
    assert left.effective_inputs.capability == ORCA_CAPABILITY
    assert left.effective_inputs.process_overlay == right.effective_inputs.process_overlay


def test_profiles_and_material_are_authoritative(store: Path, tmp_path: Path) -> None:
    result = _realize(store, _setup(material="PETG"))
    assert not result.succeeded
    assert result.failure is not None
    assert result.failure.code == "material_profile_mismatch"

    missing = _realize(store, _setup(store_name="Missing"))
    assert not missing.succeeded
    assert missing.failure is not None
    assert missing.failure.code == "printer_profile_missing"

    before = _realize(store, _setup())
    (store / "system" / "BBL" / "process" / "0.20 Standard.json").write_text("{}", encoding="utf-8")
    assert before.effective_inputs is not None
    assert before.effective_inputs.process.content != "{}"
    assert not (tmp_path / "realization.json").exists()


@pytest.mark.parametrize("material", ["pla", "PLA ", " PLA "])
def test_material_case_and_upstream_whitespace_semantics(store: Path, material: str) -> None:
    result = _realize(store, _setup(material=material))
    if material.strip() == "PLA":
        assert result.succeeded
    else:
        assert not result.succeeded
        assert result.failure is not None
        assert result.failure.code == "material_profile_mismatch"


def test_result_is_immutable(store: Path) -> None:
    result = _realize(store, _setup())
    assert result.effective_inputs is not None
    with pytest.raises((AttributeError, TypeError)):
        result.effective_inputs.identity = "changed"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        result.effective_inputs.process_overlay.append(None)  # type: ignore[attr-defined]
    with pytest.raises((AttributeError, TypeError)):
        result.effective_inputs.printer_overlay[0].value = "changed"  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        result.resources[0].identity = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("kind", [ProfileKind.PRINTER, ProfileKind.PROCESS, ProfileKind.FILAMENT])
def test_each_profile_kind_has_an_explicit_exact_success(store: Path, kind: ProfileKind) -> None:
    result = _realize(store, _setup())
    assert result.succeeded, kind
    assert result.effective_inputs is not None
    assert getattr(result.effective_inputs, kind.value).identity.kind is kind


@pytest.mark.parametrize("kind", [ProfileKind.PRINTER, ProfileKind.PROCESS, ProfileKind.FILAMENT])
def test_each_profile_kind_rejects_ambiguous_exact_identity(store: Path, kind: ProfileKind) -> None:
    directory = store / "user" / "duplicate" / "machine" if kind is ProfileKind.PRINTER else (
        store / "user" / "duplicate" / kind.value
    )
    directory.mkdir(parents=True)
    filename = {ProfileKind.PRINTER: "A1", ProfileKind.PROCESS: "0.20 Standard",
                ProfileKind.FILAMENT: "Generic PLA"}[kind]
    directory_name = "machine" if kind is ProfileKind.PRINTER else kind.value
    source = store / "system" / "BBL" / directory_name / f"{filename}.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    data["setting_id"] = "duplicate-id"
    source.write_text(json.dumps(data), encoding="utf-8")
    (directory / f"{filename}.json").write_text(json.dumps(data), encoding="utf-8")
    setup = _setup()
    if kind is ProfileKind.PRINTER:
        setup = _setup_with(setup, printer=ProfileIdentity("A1", kind, "duplicate-id"))
    elif kind is ProfileKind.PROCESS:
        setup = _setup_with(
            setup, process_profile=ProfileIdentity("0.20 Standard", kind, "duplicate-id")
        )
    else:
        setup = _setup_with(
            setup, filament_profile=ProfileIdentity("Generic PLA", kind, "duplicate-id")
        )
    result = _realize(store, setup)
    assert not result.succeeded
    assert result.failure is not None and result.failure.code == "ambiguous_profile_resolution"


def _setup_with(setup: SelectedSetup, **changes: object) -> SelectedSetup:
    values = {
        "slicer": setup.slicer,
        "printer": setup.printer,
        "nozzle_diameter_mm": setup.nozzle_diameter_mm,
        "build_plate": setup.build_plate, "material": setup.material,
        "filament_profile": setup.filament_profile, "process_profile": setup.process_profile,
        "overrides": setup.overrides,
    }
    values.update(changes)
    return SelectedSetup(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", [ProfileKind.PRINTER, ProfileKind.PROCESS, ProfileKind.FILAMENT])
def test_each_profile_kind_rejects_wrong_kind_without_fallback(
    store: Path, kind: ProfileKind
) -> None:
    import print_engineer.adapters.slicer.realization as realization_module

    original = ProfileRepository.list_profiles

    def wrong_kind(self: ProfileRepository, requested: ProfileKind) -> list[ProfileInfo]:
        profiles = original(self, requested)
        if requested is not kind:
            return profiles
        if profiles:
            profile = profiles[0]
            wrong = (
                ProfileKind.PROCESS
                if kind is ProfileKind.FILAMENT
                else ProfileKind.FILAMENT
            )
            return [ProfileInfo(profile.name, wrong, content=profile.content)]
        return profiles

    # The fake deliberately violates the repository's normal kind partition to
    # prove realization still classifies the authoritative identity correctly.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ProfileRepository, "list_profiles", wrong_kind)
    try:
        result = realization_module.realize_setup(_setup(), ProfileRepository(store))
    finally:
        monkeypatch.undo()
    assert not result.succeeded
    assert result.failure is not None and result.failure.code == "wrong_profile_kind"


@pytest.mark.parametrize("kind", [ProfileKind.PRINTER, ProfileKind.PROCESS, ProfileKind.FILAMENT])
def test_same_name_shadow_protection_applies_to_every_profile_kind(
    store: Path, kind: ProfileKind
) -> None:
    names = {
        ProfileKind.PRINTER: ("machine", "A1"),
        ProfileKind.PROCESS: ("process", "0.20 Standard"),
        ProfileKind.FILAMENT: ("filament", "Generic PLA"),
    }
    directory_name, name = names[kind]
    source = store / "system" / "BBL" / directory_name / f"{name}.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    data["setting_id"] = "exact-authority"
    source.write_text(json.dumps(data), encoding="utf-8")
    shadow = dict(data)
    shadow["setting_id"] = "shadow-substitute"
    shadow["shadow_marker"] = True
    if kind is ProfileKind.FILAMENT:
        shadow["filament_type"] = "PETG"
    user = store / "user" / "shadow" / directory_name
    user.mkdir(parents=True)
    (user / f"{name}.json").write_text(json.dumps(shadow), encoding="utf-8")
    setup = _setup_with(
        _setup(),
        **{
            {
                ProfileKind.PRINTER: "printer",
                ProfileKind.PROCESS: "process_profile",
                ProfileKind.FILAMENT: "filament_profile",
            }[kind]: ProfileIdentity(name, kind, "exact-authority")
        },
    )
    result = _realize(store, setup)
    assert result.succeeded
    assert result.effective_inputs is not None
    reference = getattr(result.effective_inputs, kind.value)
    assert "shadow_marker" not in reference.content


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (lambda data: data.pop("curr_bed_type", None), "effective_settings_mismatch"),
        (
            lambda data: data.__setitem__("curr_bed_type", "Textured PEI Plate"),
            "effective_settings_mismatch",
        ),
    ],
)
def test_effective_printer_values_are_observed(
    store: Path,
    mutator: Callable[[dict[str, object]], object],
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import print_engineer.adapters.slicer.realization as realization_module

    original = realization_module._effective_profiles

    def altered(*args: object, **kwargs: object) -> tuple[dict[str, object], dict[str, object]]:
        printer, process = original(*args, **kwargs)  # type: ignore[arg-type]
        mutator(printer)
        return printer, process

    monkeypatch.setattr(realization_module, "_effective_profiles", altered)
    result = _realize(store, _setup())
    assert not result.succeeded and result.failure is not None
    assert result.failure.code == expected


@pytest.mark.parametrize(
    "change",
    [
        lambda data: data.pop("layer_height", None),
        lambda data: data.__setitem__("layer_height", "bad"),
    ],
)
def test_effective_process_values_are_observed(
    store: Path,
    change: Callable[[dict[str, object]], object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import print_engineer.adapters.slicer.realization as realization_module

    original = realization_module._effective_profiles

    def altered(*args: object, **kwargs: object) -> tuple[dict[str, object], dict[str, object]]:
        printer, process = original(*args, **kwargs)  # type: ignore[arg-type]
        change(process)
        return printer, process

    monkeypatch.setattr(realization_module, "_effective_profiles", altered)
    result = _realize(store, _setup(overrides=(AppliedOverride("layer_height_mm", 0.2),)))
    assert not result.succeeded and result.failure is not None
    assert result.failure.code in {"invalid_effective_value", "effective_settings_mismatch"}


def test_malformed_profile_content_has_a_content_failure(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import print_engineer.adapters.slicer.realization as realization_module

    original = realization_module._materialize_exact

    def malformed(repository: ProfileRepository, profile: ProfileInfo) -> ProfileInfo:
        value = original(repository, profile)
        return ProfileInfo(value.name, value.kind, content="[]", materialized=True)

    monkeypatch.setattr(realization_module, "_materialize_exact", malformed)
    result = _realize(store, _setup())
    assert not result.succeeded and result.failure is not None
    assert result.failure.code == "profile_content_invalid"


def test_resolved_profile_materialization_failure_is_atomic(
    store: Path,
) -> None:
    class FailingMaterializer(ProfileMaterializer):
        def materialize(self, profile: ProfileInfo) -> ProfileInfo:
            from print_engineer.errors import InvalidProfile

            raise InvalidProfile(f"cannot materialize {profile.name}")

    result = realize_setup(
        _setup(),
        ProfileRepository(store),
        materializer=FailingMaterializer(ProfileRepository(store)),
    )
    assert not result.succeeded
    assert result.failure is not None
    assert result.failure.code == "profile_materialization_failed"
    assert result.effective_inputs is None
    assert result.resources == ()


def test_valid_unexpected_process_effective_value_fails_semantic_comparison(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import print_engineer.adapters.slicer.realization as realization_module

    original = realization_module._effective_profiles

    def altered(*args: object, **kwargs: object) -> tuple[dict[str, object], dict[str, object]]:
        printer, process = original(*args, **kwargs)  # type: ignore[arg-type]
        process["wall_loops"] = "4"
        return printer, process

    monkeypatch.setattr(realization_module, "_effective_profiles", altered)
    result = _realize(store, _setup(overrides=(AppliedOverride("wall_loops", 3),)))
    assert not result.succeeded
    assert result.failure is not None
    assert result.failure.code == "effective_settings_mismatch"


def test_applied_override_rejects_unsupported_authority() -> None:
    with pytest.raises(ValueError):
        AppliedOverride("arbitrary_unsupported_setting", "value")


def test_operational_profile_context_is_excluded_from_identity(store: Path, tmp_path: Path) -> None:
    first = _realize(store, _setup())
    relocated = tmp_path / "different-working-context"
    relocated.mkdir()
    second_store = relocated / "profiles"
    second_store.mkdir()
    for source in (store / "system" / "BBL").rglob("*.json"):
        destination = second_store / source.relative_to(store)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    second = _realize(second_store, _setup())
    assert first.succeeded and second.succeeded
    assert first.effective_inputs is not None and second.effective_inputs is not None
    assert first.effective_inputs.identity == second.effective_inputs.identity
    assert first.resources == second.resources
    assert all(
        resource.reference == getattr(first.effective_inputs, kind)
        for resource, kind in zip(
            first.resources, ("printer", "process", "filament"), strict=True
        )
    )


def test_caller_owned_source_mutation_cannot_change_realization(store: Path) -> None:
    source = {
        "name": "0.20 Standard",
        "type": "process",
        "compatible_printers": ["A1"],
        "layer_height": "0.2",
        "nested": {"marker": "original"},
    }
    path = store / "system" / "BBL" / "process" / "0.20 Standard.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    result = _realize(store, _setup())
    assert result.succeeded and result.effective_inputs is not None
    snapshot = (
        result.effective_inputs.printer.identity,
        result.effective_inputs.printer.content,
        result.effective_inputs.process.content,
        result.effective_inputs.actual_inputs,
        result.effective_inputs.identity,
    )
    source["layer_height"] = "4"
    source["nested"] = {"marker": "mutated"}
    source["compatible_printers"] = ["A1", "Other"]
    assert snapshot == (
        result.effective_inputs.printer.identity,
        result.effective_inputs.printer.content,
        result.effective_inputs.process.content,
        result.effective_inputs.actual_inputs,
        result.effective_inputs.identity,
    )
    assert "mutated" not in result.effective_inputs.process.content


def test_nested_authority_projections_are_snapshot_isolated(store: Path) -> None:
    result = _realize(
        store, _setup(overrides=(AppliedOverride("wall_loops", 3),))
    )
    assert result.effective_inputs is not None
    content = json.loads(result.effective_inputs.process.content)
    content["nested"] = {"changed": True}
    overlay = list(result.effective_inputs.process_overlay)
    overlay[0] = OverlayEntry("tampered", "x", "process", "none")
    assert "nested" not in result.effective_inputs.process.content
    assert result.effective_inputs.process_overlay[0].key != "tampered"
    assert result.resources[1].identity


def test_repeated_authority_access_is_stable(store: Path) -> None:
    result = _realize(store, _setup())
    assert result.effective_inputs is not None
    observed = tuple(
        (
            result.effective_inputs.identity,
            result.effective_inputs.actual_inputs,
            result.effective_inputs.printer_overlay,
            result.effective_inputs.process_overlay,
            tuple(resource.identity for resource in result.resources),
        )
        for _ in range(3)
    )
    assert observed[0] == observed[1] == observed[2]
    copied = json.loads(result.effective_inputs.printer.content)
    copied["temporary"] = True
    assert all("temporary" not in result.effective_inputs.printer.content for _ in range(3))


def test_realization_never_uses_file_write_apis(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("realization attempted a file write")

    original_open = builtins.open

    def guarded_open(*args: Any, **kwargs: Any) -> Any:
        mode = kwargs.get("mode", args[1] if len(args) > 1 else "r")
        if isinstance(mode, str) and any(flag in mode for flag in ("w", "a", "x", "+")):
            fail(*args, **kwargs)
        return original_open(*args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(Path, "write_text", fail)
    monkeypatch.setattr(Path, "write_bytes", fail)
    result = _realize(store, _setup())
    assert result.succeeded


@pytest.mark.parametrize("nozzle", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_nozzle_is_rejected_at_selected_setup_boundary(
    store: Path, nozzle: float
) -> None:
    with pytest.raises((TypeError, ValueError)):
        _setup(nozzle=nozzle)


def test_bool_nozzle_is_rejected_at_selected_setup_boundary(store: Path) -> None:
    with pytest.raises((TypeError, ValueError)):
        _setup(nozzle=True)  # type: ignore[arg-type]


@pytest.mark.parametrize("setting, value", [
    ("layer_height_mm", 0.01), ("layer_height_mm", 100.0),
    ("wall_loops", 1), ("wall_loops", 100),
    ("sparse_infill_percent", 0.0), ("sparse_infill_percent", 100.0),
    ("support_threshold_angle_deg", 0.0), ("support_threshold_angle_deg", 90.0),
    ("outer_wall_speed_mms", 0.01), ("outer_wall_speed_mms", 1000.0),
])
def test_override_valid_boundaries_are_realized(store: Path, setting: str, value: float) -> None:
    result = _realize(store, _setup(overrides=(AppliedOverride(setting, value),)))
    assert result.succeeded


@pytest.mark.parametrize("setting, value", [
    ("layer_height_mm", 0.009), ("layer_height_mm", 100.01),
    ("wall_loops", 0), ("wall_loops", 101),
    ("sparse_infill_percent", -0.01), ("sparse_infill_percent", 100.01),
    ("support_threshold_angle_deg", -0.01), ("support_threshold_angle_deg", 90.01),
    ("outer_wall_speed_mms", 0.009), ("outer_wall_speed_mms", 1000.01),
])
def test_override_invalid_numeric_boundaries_are_rejected(setting: str, value: float) -> None:
    with pytest.raises((TypeError, ValueError)):
        AppliedOverride(setting, value)


def test_override_nonfinite_and_bool_confusion_are_rejected() -> None:
    with pytest.raises((TypeError, ValueError)):
        AppliedOverride("layer_height_mm", math.nan)
    with pytest.raises((TypeError, ValueError)):
        AppliedOverride("layer_height_mm", True)  # type: ignore[arg-type]
    with pytest.raises((TypeError, ValueError)):
        AppliedOverride("wall_loops", True)  # type: ignore[arg-type]


def test_unknown_filament_material_is_not_derived_from_name(store: Path) -> None:
    path = store / "system" / "BBL" / "filament" / "Generic PLA.json"
    path.write_text(json.dumps({"name": "Generic PLA", "type": "filament"}), encoding="utf-8")
    result = _realize(store, _setup())
    assert not result.succeeded and result.failure is not None
    assert result.failure.code == "material_not_provable"


def test_resource_identity_changes_with_semantic_content_not_formatting(store: Path) -> None:
    first = _realize(store, _setup())
    printer = store / "system" / "BBL" / "machine" / "A1.json"
    data = json.loads(printer.read_text(encoding="utf-8"))
    data["printer_model"] = "A1-revised"
    printer.write_text(json.dumps(data, indent=4), encoding="utf-8")
    second = _realize(store, _setup())
    assert first.effective_inputs is not None and second.effective_inputs is not None
    assert first.effective_inputs.identity != second.effective_inputs.identity
    assert first.resources[0].identity != second.resources[0].identity


@pytest.mark.parametrize(
    ("kind", "profile_kind", "profile_name", "setup_field", "directory"),
    [
        ("printer", ProfileKind.PRINTER, "A1", "printer", "machine"),
        ("process", ProfileKind.PROCESS, "0.20 Standard", "process_profile", "process"),
        ("filament", ProfileKind.FILAMENT, "Generic PLA", "filament_profile", "filament"),
    ],
)
def test_resources_preserve_exact_profile_references(
    store: Path,
    kind: str,
    profile_kind: ProfileKind,
    profile_name: str,
    setup_field: str,
    directory: str,
) -> None:
    path = store / "system" / "BBL" / directory / f"{profile_name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["setting_id"] = f"{kind}-authority"
    path.write_text(json.dumps(data), encoding="utf-8")
    setup = _with_profile_authority(
        _setup(), setup_field, ProfileIdentity(profile_name, profile_kind, f"{kind}-authority")
    )
    result = _realize(store, setup)
    assert result.succeeded and result.effective_inputs is not None
    expected = getattr(result.effective_inputs, kind)
    resource = result.resources[("printer", "process", "filament").index(kind)]
    assert isinstance(resource.reference, ProfileReference)
    assert resource.reference == expected
    assert resource.reference.identity == getattr(setup, setup_field)


@pytest.mark.parametrize(
    ("kind", "directory", "filename", "profile_kind", "field"),
    [
        ("printer", "machine", "A1", ProfileKind.PRINTER, "printer"),
        ("process", "process", "0.20 Standard", ProfileKind.PROCESS, "process_profile"),
        ("filament", "filament", "Generic PLA", ProfileKind.FILAMENT, "filament_profile"),
    ],
)
def test_same_content_different_profile_authority_changes_resource_identity(
    store: Path,
    kind: str,
    directory: str,
    filename: str,
    profile_kind: ProfileKind,
    field: str,
) -> None:
    original = store / "system" / "BBL" / directory / f"{filename}.json"
    data = json.loads(original.read_text(encoding="utf-8"))
    data["setting_id"] = "authority-a"
    original.write_text(json.dumps(data), encoding="utf-8")
    base = _setup()
    base = _with_profile_authority(
        base, field, ProfileIdentity(filename, profile_kind, "authority-a")
    )
    first = _realize(store, base)

    alternate_dir = store / "user" / "same-content" / directory
    alternate_dir.mkdir(parents=True)
    alternate = dict(data)
    alternate["setting_id"] = "authority-b"
    (alternate_dir / f"{filename}.json").write_text(json.dumps(alternate), encoding="utf-8")
    second_setup = _with_profile_authority(
        base, field, ProfileIdentity(filename, profile_kind, "authority-b")
    )
    second = _realize(store, second_setup)
    index = ("printer", "process", "filament").index(kind)
    assert first.succeeded and second.succeeded
    assert first.resources[index].identity != second.resources[index].identity
    assert first.effective_inputs is not None and second.effective_inputs is not None
    assert first.effective_inputs.identity != second.effective_inputs.identity


def test_source_isolation_and_repeated_access_are_semantically_stable(store: Path) -> None:
    result = _realize(store, _setup())
    assert result.effective_inputs is not None
    source = json.loads(result.effective_inputs.process.content)
    source["layer_height"] = "999"
    first = result.effective_inputs.process.content
    second = result.effective_inputs.process.content
    assert first == second and "999" not in first
    observed = tuple(result.effective_inputs.process_overlay)
    assert observed == tuple(result.effective_inputs.process_overlay)
    assert all(isinstance(item, OverlayEntry) for item in observed)


def test_realization_has_no_subprocess_or_filesystem_side_effects(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("Increment 2 attempted an external side effect")

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(tempfile, "mkdtemp", fail)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", fail)
    monkeypatch.setattr(Path, "mkdir", fail)
    result = _realize(store, _setup())
    assert result.succeeded
    assert result.effective_inputs is not None
    assert result.resources[0].reference == result.effective_inputs.printer
