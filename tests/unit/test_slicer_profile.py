"""Tests for slicer profile discovery and materialization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from print_engineer.adapters.slicer.profile import ProfileMaterializer, ProfileRepository
from print_engineer.core.types import ProfileInfo, ProfileKind, ProfileSource
from print_engineer.errors import InvalidProfile


def _write(root: Path, relative: str, content: object | None = None) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_text(json.dumps(content, indent=2), encoding="utf-8")
    return path


def _build_store(root: Path) -> Path:
    system = root / "system" / "BBL"

    _write(
        system,
        "machine/fdm_common.json",
        {
            "type": "machine",
            "name": "fdm_common",
            "inherits": "",
            "from": "system",
            "setting_id": "GM000",
            "thick_layers": ["4"],
        },
    )
    _write(
        system,
        "machine/Base 0.4 nozzle.json",
        {
            "type": "machine",
            "name": "Base 0.4 nozzle",
            "inherits": "fdm_common",
            "from": "system",
            "setting_id": "GM001",
            "printer_model": "Base",
            "printer_variant": "0.4",
            "compatible_printers": [],
            "nozzle_diameter": ["0.4"],
        },
    )
    _write(
        system,
        "process/Standard 0.20.json",
        {
            "type": "process",
            "name": "Standard 0.20",
            "inherits": "",
            "from": "system",
            "setting_id": "GP001",
            "compatible_printers": ["Base 0.4 nozzle"],
            "layer_height": "0.2",
        },
    )
    _write(
        system,
        "filament/Generic PLA.json",
        {
            "type": "filament",
            "name": "Generic PLA",
            "inherits": "",
            "from": "system",
            "setting_id": "GF001",
            "compatible_printers": ["Base 0.4 nozzle"],
            "filament_density": "1.24",
        },
    )
    _write(system, "machine/empty-list.json", "[]")
    _write(system, "machine/bad-json.json", "{ not valid json !!")

    user = root / "user" / "12345"
    _write(
        user,
        "machine/Base 0.4 nozzle - Copy.json",
        {
            "name": "Base 0.4 nozzle - Copy",
            "inherits": "Base 0.4 nozzle",
            "from": "User",
            "bed_temp": ["55"],
        },
    )
    _write(
        user,
        "machine/bad-json-user.json",
        "{ nope",
    )
    _write(
        user,
        "process/Standard 0.20 - Copy.json",
        {
            "name": "Standard 0.20 - Copy",
            "inherits": "Standard 0.20",
            "from": "User",
            "infill": ["15%"],
        },
    )
    _write(
        user,
        "filament/base/PLA Preset.json",
        {
            "name": "PLA Preset",
            "inherits": "",
            "from": "User",
            "filament_density": "1.20",
        },
    )
    _write(
        user,
        "machine/Cyclic.json",
        {"name": "Cyclic", "inherits": "Cyclic", "from": "User"},
    )
    return root


def test_list_profiles_discovery(tmp_path: Path) -> None:
    repo = ProfileRepository(_build_store(tmp_path))

    machines = repo.list_profiles(ProfileKind.PRINTER)
    names = [p.name for p in machines]
    # system: Base 0.4 nozzle, fdm_common; user: Base 0.4 nozzle - Copy, Cyclic
    # (empty-list, 2 bad JSON files skipped)
    assert names == ["Base 0.4 nozzle", "fdm_common", "Base 0.4 nozzle - Copy", "Cyclic"]

    processes = repo.list_profiles(ProfileKind.PROCESS)
    assert [p.name for p in processes] == ["Standard 0.20", "Standard 0.20 - Copy"]

    filaments = repo.list_profiles(ProfileKind.FILAMENT)
    assert [p.name for p in filaments] == ["Generic PLA", "PLA Preset"]


def test_profile_fields_parsed(tmp_path: Path) -> None:
    repo = ProfileRepository(_build_store(tmp_path))
    machine = repo.find(ProfileKind.PRINTER, "Base 0.4 nozzle")
    assert machine is not None
    assert machine.source == ProfileSource.SYSTEM
    assert machine.setting_id == "GM001"
    assert machine.printer_model == "Base"
    assert machine.printer_variant == "0.4"
    assert machine.compatible_printers == ()

    process = repo.find(ProfileKind.PROCESS, "Standard 0.20")
    assert process is not None
    assert process.compatible_printers == ("Base 0.4 nozzle",)

    delta = repo.find(ProfileKind.PRINTER, "Base 0.4 nozzle - Copy")
    assert delta is not None
    assert delta.source == ProfileSource.USER
    assert delta.inherits == "Base 0.4 nozzle"
    assert delta.setting_id is None


def test_find_user_shadows_system(tmp_path: Path) -> None:
    repo = ProfileRepository(_build_store(tmp_path))
    copy = repo.find(ProfileKind.PRINTER, "Base 0.4 nozzle - Copy")
    assert copy is not None and copy.source == ProfileSource.USER
    base = repo.find(ProfileKind.PRINTER, "Base 0.4 nozzle")
    assert base is not None and base.source == ProfileSource.SYSTEM


def test_malformed_profiles_are_skipped_not_crashing(tmp_path: Path) -> None:
    repo = ProfileRepository(_build_store(tmp_path))
    machines = repo.list_profiles(ProfileKind.PRINTER)
    names = [p.name for p in machines]
    assert "bad-json" not in names
    assert all(p.name for p in machines)


def test_materialize_machine_uses_base_identity(tmp_path: Path) -> None:
    repo = ProfileRepository(_build_store(tmp_path))
    materializer = ProfileMaterializer(repo)
    delta = repo.find(ProfileKind.PRINTER, "Base 0.4 nozzle - Copy")
    assert delta is not None

    out = materializer.materialize(delta)
    assert out.materialized
    assert out.source == ProfileSource.GENERATED
    assert out.name == "Base 0.4 nozzle"

    data = json.loads(out.content or "{}")
    assert data["type"] == "machine"
    assert data["from"] == "system"
    assert data["name"] == "Base 0.4 nozzle"
    assert data["bed_temp"] == ["55"]
    assert data["nozzle_diameter"] == ["0.4"]
    assert data["printer_model"] == "Base"
    assert "inherits" not in data
    assert data["thick_layers"] == ["4"]


def test_materialize_process_keeps_name_and_inherits_compatibility(tmp_path: Path) -> None:
    repo = ProfileRepository(_build_store(tmp_path))
    materializer = ProfileMaterializer(repo)
    delta = repo.find(ProfileKind.PROCESS, "Standard 0.20 - Copy")
    assert delta is not None

    out = materializer.materialize(delta)
    assert out.name == "Standard 0.20 - Copy"
    data = json.loads(out.content or "{}")
    assert data["type"] == "process"
    assert data["from"] == "system"
    assert data["compatible_printers"] == ["Base 0.4 nozzle"]
    assert data["infill"] == ["15%"]
    assert data["layer_height"] == "0.2"


def test_materialize_user_full_filament(tmp_path: Path) -> None:
    repo = ProfileRepository(_build_store(tmp_path))
    materializer = ProfileMaterializer(repo)
    filament = repo.find(ProfileKind.FILAMENT, "PLA Preset")
    assert filament is not None

    out = materializer.materialize(filament)
    data = json.loads(out.content or "{}")
    assert data["type"] == "filament"
    assert data["from"] == "system"
    assert data["filament_density"] == "1.20"


def test_materialize_already_materialized_is_identity(tmp_path: Path) -> None:
    materializer = ProfileMaterializer(ProfileRepository(_build_store(tmp_path)))
    profile = ProfileInfo(
        name="X",
        kind=ProfileKind.PROCESS,
        content='{"type": "process", "name": "X", "from": "system"}',
        source=ProfileSource.GENERATED,
        materialized=True,
    )
    assert materializer.materialize(profile) is profile


def test_materialize_missing_profile_raises(tmp_path: Path) -> None:
    materializer = ProfileMaterializer(ProfileRepository(_build_store(tmp_path)))
    ghost = ProfileInfo(name="Ghost", kind=ProfileKind.PROCESS)
    with pytest.raises(InvalidProfile):
        materializer.materialize(ghost)


def test_materialize_cyclic_chain_raises(tmp_path: Path) -> None:
    repo = ProfileRepository(_build_store(tmp_path))
    materializer = ProfileMaterializer(repo)
    cyclic = repo.find(ProfileKind.PRINTER, "Cyclic")
    assert cyclic is not None
    with pytest.raises(InvalidProfile):
        materializer.materialize(cyclic)


def test_parse_profile_non_dict_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "system/BBL/machine/array.json", '["a", "b"]')
    _write(tmp_path, "system/BBL/machine/no-name.json", {"type": "machine"})
    repo = ProfileRepository(tmp_path)
    machines = repo.list_profiles(ProfileKind.PRINTER)
    assert machines == []
