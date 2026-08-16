"""Hermetic unit tests for BambuStudioAdapter (no real slicer invoked)."""

from __future__ import annotations

from pathlib import Path

import pytest

from print_engineer.adapters.slicer.bambu import BambuStudioAdapter
from print_engineer.adapters.slicer.process import ProcResult
from print_engineer.core.types import ProfileInfo, ProfileKind, ProfileSource, SliceJob
from print_engineer.errors import SlicerNotInstalled, SlicerUnavailable

BANNER = (
    "BambuStudio-02.06.00.51:\n"
    "Usage: bambu-studio [ OPTIONS ] [ file.3mf/file.stl ... ]\n"
)


def _adapter(tmp_path: Path) -> BambuStudioAdapter:
    exe = tmp_path / "bambu-studio.exe"
    exe.write_bytes(b"MZ fake exe")
    return BambuStudioAdapter(
        executable=exe,
        appdata=tmp_path / "appdata",
        workdir=tmp_path / "workspace",
        timeout_seconds=30.0,
    )


def _job(tmp_path: Path) -> SliceJob:
    model = tmp_path / "cube.stl"
    model.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    process = ProfileInfo(
        name="Standard 0.20",
        kind=ProfileKind.PROCESS,
        source=ProfileSource.GENERATED,
        materialized=True,
    )
    filament = ProfileInfo(
        name="Generic PLA",
        kind=ProfileKind.FILAMENT,
        source=ProfileSource.GENERATED,
        materialized=True,
    )
    printer = ProfileInfo(
        name="A1 0.4 nozzle",
        kind=ProfileKind.PRINTER,
        source=ProfileSource.GENERATED,
        materialized=True,
    )
    return SliceJob(
        model_path=model,
        profile=process,
        filament=filament,
        printer=printer,
        timeout_seconds=30.0,
    )


def test_detect_parses_banner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)

    def fake_run(cmd: list[str], *, timeout: float) -> ProcResult:
        assert "--help" in cmd
        return ProcResult(
            stdout=BANNER,
            stderr="",
            return_code=-1,
            timed_out=False,
            killed=False,
            duration_seconds=0.1,
        )

    monkeypatch.setattr("print_engineer.adapters.slicer.bambu.run_cli", fake_run)
    info = adapter.detect()
    assert info is not None
    assert info.version == "02.06.00.51"
    assert info.version_source == "banner"
    assert info.slicing_supported is False


def test_detect_unknown_version_unsupported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    adapter = _adapter(tmp_path)

    def fake_run(cmd: list[str], *, timeout: float) -> ProcResult:
        return ProcResult(
            stdout="Usage: bambu-studio [ OPTIONS ]\n",
            stderr="",
            return_code=0,
            timed_out=False,
            killed=False,
            duration_seconds=0.1,
        )

    monkeypatch.setattr("print_engineer.adapters.slicer.bambu.run_cli", fake_run)
    info = adapter.detect()
    assert info is not None
    assert info.version is None
    assert info.slicing_supported is False


def test_detect_missing_returns_none(tmp_path: Path) -> None:
    adapter = BambuStudioAdapter(
        executable=tmp_path / "missing.exe",
        appdata=tmp_path / "appdata",
        workdir=tmp_path / "workspace",
    )
    assert adapter.detect() is None


def test_slice_raises_unavailable_without_launching_slice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    adapter = _adapter(tmp_path)
    adapter._bambu_version = ("02.06.00.51", "banner")

    def forbid_run(cmd: list[str], **kwargs: object) -> ProcResult:
        raise AssertionError(f"slice must not launch the slicer process: {cmd}")

    monkeypatch.setattr("print_engineer.adapters.slicer.bambu.run_cli", forbid_run)
    with pytest.raises(SlicerUnavailable) as excinfo:
        adapter.slice(_job(tmp_path))
    payload = excinfo.value.to_dict()
    assert payload["code"] == "slicer_unavailable"
    assert payload["details"]["version"] == "02.06.00.51"
    assert payload["details"]["reason"] == "cli_slice_crash"
    assert "crash" in payload["message"].lower()


def test_slice_not_installed_raises(tmp_path: Path) -> None:
    adapter = BambuStudioAdapter(
        executable=tmp_path / "missing.exe",
        appdata=tmp_path / "appdata",
        workdir=tmp_path / "workspace",
    )
    with pytest.raises(SlicerNotInstalled):
        adapter.slice(_job(tmp_path))


def test_validate_input_uses_info_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    adapter = _adapter(tmp_path)
    adapter._bambu_version = ("02.06.00.51", "banner")
    model = tmp_path / "cube.stl"
    model.write_text("solid cube\nendsolid cube\n", encoding="utf-8")

    def fake_run(cmd: list[str], *, timeout: float) -> ProcResult:
        assert "--info" in cmd
        return ProcResult(
            stdout="[cube.stl]\nsize_x = 20.000000\nsize_y = 20.000000\n"
            "size_z = 20.000000\nnumber_of_facets = 12\nmanifold = yes\n",
            stderr="",
            return_code=0,
            timed_out=False,
            killed=False,
            duration_seconds=0.1,
        )

    monkeypatch.setattr("print_engineer.adapters.slicer.base.run_cli", fake_run)
    validation = adapter.validate_input(model)
    assert validation.is_valid
    assert validation.size == (20.0, 20.0, 20.0)


def test_list_profiles_discovers_user_and_system(tmp_path: Path) -> None:
    appdata = tmp_path / "appdata"
    system_dir = appdata / "system" / "BBL" / "filament"
    user_dir = appdata / "user" / "1234" / "filament" / "base"
    system_dir.mkdir(parents=True)
    user_dir.mkdir(parents=True)
    (system_dir / "Generic PLA.json").write_text(
        '{"type": "filament", "name": "Generic PLA", "from": "system"}', encoding="utf-8"
    )
    (user_dir / "PLA Preset.json").write_text(
        '{"name": "PLA Preset", "inherits": "Generic PLA", "from": "User"}', encoding="utf-8"
    )

    adapter = BambuStudioAdapter(
        executable=tmp_path / "missing.exe",
        appdata=appdata,
        workdir=tmp_path / "workspace",
    )
    profiles = adapter.list_profiles(ProfileKind.FILAMENT)
    assert [p.name for p in profiles] == ["Generic PLA", "PLA Preset"]
    assert profiles[1].source == ProfileSource.USER
    assert profiles[1].inherits == "Generic PLA"
