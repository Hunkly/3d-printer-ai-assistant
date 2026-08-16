"""Shared helpers for slicer integration tests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from print_engineer.adapters.slicer.base import BaseSlicerAdapter
from print_engineer.core.types import ProfileInfo, ProfileKind, ProfileSource


class FakeProfileAdapter:
    """In-memory slicer-adapter stand-in for profile-store reads.

    ``profiles`` maps ``ProfileKind`` to a list of raw (on-disk) ``ProfileInfo``
    docs. ``materialized`` maps ``"<kind>:<name>"`` to the materialized
    ``ProfileInfo`` whose content holds the resolved (inheritance-merged)
    document, mirroring ``BaseSlicerAdapter.find_profile``. Any ``slice`` call
    is a hard error: setup flows must never invoke the slicer.
    """

    def __init__(
        self,
        profiles: dict[ProfileKind, list[ProfileInfo]] | None = None,
        materialized: dict[str, ProfileInfo] | None = None,
        slicer_calls: list[str] | None = None,
    ) -> None:
        self.profiles = profiles or {}
        self.materialized = materialized or {}
        self.slicer_calls: list[str] = [] if slicer_calls is None else slicer_calls

    def list_profiles(self, profile_kind: ProfileKind) -> list[ProfileInfo]:
        return list(self.profiles.get(profile_kind, []))

    def find_profile(self, profile_kind: ProfileKind, name: str) -> ProfileInfo | None:
        key = f"{profile_kind.value}:{name}"
        if key in self.materialized:
            return self.materialized[key]
        for profile in self.profiles.get(profile_kind, []):
            if profile.name == name:
                return profile
        return None

    def slice(self, job: object) -> object:  # pragma: no cover - must never run
        self.slicer_calls.append("slice")
        raise AssertionError("slice must not be called for read-only setup flows")


def make_profile(
    kind: ProfileKind,
    name: str,
    data: Mapping[str, object] | str,
    *,
    materialized: bool = True,
    source: ProfileSource = ProfileSource.SYSTEM,
    printer_model: str | None = None,
    printer_variant: str | None = None,
    compatible_printers: tuple[str, ...] = (),
    setting_id: str | None = None,
) -> ProfileInfo:
    content = data if isinstance(data, str) else json.dumps(data)
    return ProfileInfo(
        name=name,
        kind=kind,
        path=Path(f"{name}.json"),
        content=content,
        source=source,
        materialized=materialized,
        printer_model=printer_model,
        printer_variant=printer_variant,
        compatible_printers=compatible_printers,
        setting_id=setting_id,
    )


def write_cube_stl(path: Path, size: float = 20.0) -> Path:
    """Write a simple 20 mm cube as an ASCII STL (12 triangles)."""
    vertices = [
        (0.0, 0.0, 0.0),
        (size, 0.0, 0.0),
        (size, size, 0.0),
        (0.0, size, 0.0),
        (0.0, 0.0, size),
        (size, 0.0, size),
        (size, size, size),
        (0.0, size, size),
    ]
    faces = [
        (0, 1, 3),
        (3, 1, 2),
        (4, 5, 7),
        (7, 5, 6),
        (0, 4, 1),
        (1, 4, 5),
        (1, 5, 2),
        (2, 5, 6),
        (3, 2, 7),
        (7, 2, 6),
        (0, 3, 4),
        (4, 3, 7),
    ]
    path.write_text("solid cube\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as fh:
        for a, b, c in faces:
            fh.write("facet normal 0 0 0\n  outer loop\n")
            for index in (a, b, c):
                x, y, z = vertices[index]
                fh.write(f"    vertex {x} {y} {z}\n")
            fh.write("  endloop\nendfacet\n")
        fh.write("endsolid cube\n")
    return path


def _compatible(profile: ProfileInfo, machine_name: str) -> bool:
    compatible = profile.compatible_printers
    return not compatible or machine_name in compatible


_AVOID_FILAMENTS = ("ABS", "ASA", "PC", "PETG", "PA", "Nylon", "TPU", "PVA", "Support")
_PREFER_PROCESS = ("Standard", "0.20", "0.2")


def _process_score(name: str) -> int:
    lowered = name.lower()
    if "standard" in lowered or "0.20" in lowered or "0.2 " in lowered:
        return 0
    if "extra fine" in lowered or "0.08" in lowered:
        return 2
    return 1


def _filament_score(name: str) -> int:
    lowered = name.lower()
    if "pla" in lowered:
        return 0
    if any(bad.lower() in lowered for bad in _AVOID_FILAMENTS):
        return 3
    return 1


def find_compatible_triple(
    adapter: BaseSlicerAdapter,
) -> tuple[ProfileInfo, ProfileInfo, ProfileInfo]:
    """Pick a system process/filament/printer triple that is compatible."""
    machines = adapter.list_profiles(ProfileKind.PRINTER)
    processes = adapter.list_profiles(ProfileKind.PROCESS)
    filaments = adapter.list_profiles(ProfileKind.FILAMENT)

    candidates: list[tuple[int, ProfileInfo, ProfileInfo, ProfileInfo]] = []
    for machine in machines:
        if machine.source != ProfileSource.SYSTEM or "0.4 nozzle" not in machine.name:
            continue
        for process in processes:
            if process.source != ProfileSource.SYSTEM:
                continue
            if not _compatible(process, machine.name):
                continue
            for filament in filaments:
                if filament.source != ProfileSource.SYSTEM:
                    continue
                if not _compatible(filament, machine.name):
                    continue
                score = _process_score(process.name) + _filament_score(filament.name)
                candidates.append((score, process, filament, machine))

    if not candidates:
        raise RuntimeError("no compatible system profile triple found for any machine")

    candidates.sort(key=lambda item: (item[0], item[3].name, item[1].name))
    _, process, filament, machine = candidates[0]
    return process, filament, machine
