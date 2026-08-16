"""Slicer CLI integration probe: real detection and graceful failure.

Runs against the slicers installed on this Windows machine. Every test skips
when its slicer is not present, so the suite stays green elsewhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from tests.slicer_helpers import write_cube_stl

from print_engineer.adapters.slicer.bambu import BambuStudioAdapter
from print_engineer.adapters.slicer.orca import OrcaSlicerAdapter
from print_engineer.core.types import ProfileKind
from print_engineer.errors import SlicerUnavailable

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("win"), reason="real slicer probing is Windows-only"
)

ORCA_PATH = Path(r"C:\Program Files\OrcaSlicer\orca-slicer.exe")
BAMBU_PATH = Path(r"C:\Program Files\Bambu Studio\bambu-studio.exe")


def _orca_adapter(tmp_path: Path) -> OrcaSlicerAdapter:
    return OrcaSlicerAdapter(workdir=tmp_path / "workspace", timeout_seconds=30.0)


def _bambu_adapter(tmp_path: Path) -> BambuStudioAdapter:
    return BambuStudioAdapter(workdir=tmp_path / "workspace", timeout_seconds=30.0)


def test_orca_slicer_detected(tmp_path: Path) -> None:
    if not ORCA_PATH.is_file():
        pytest.skip("OrcaSlicer is not installed")
    info = _orca_adapter(tmp_path).detect()
    assert info is not None
    assert info.kind.value == "orca_slicer"
    assert info.executable.is_file()


def test_orca_version_detected(tmp_path: Path) -> None:
    if not ORCA_PATH.is_file():
        pytest.skip("OrcaSlicer is not installed")
    info = _orca_adapter(tmp_path).detect()
    assert info is not None
    assert info.version is not None
    assert info.slicing_supported is True
    assert info.version_source in ("registry", "binary")


def test_orca_profile_discovery(tmp_path: Path) -> None:
    if not ORCA_PATH.is_file():
        pytest.skip("OrcaSlicer is not installed")
    adapter = _orca_adapter(tmp_path)
    for kind in ProfileKind:
        profiles = adapter.list_profiles(kind)
        assert profiles, f"expected at least one {kind.value} profile"


def test_bambu_slicer_detected(tmp_path: Path) -> None:
    if not BAMBU_PATH.is_file():
        pytest.skip("Bambu Studio is not installed")
    info = _bambu_adapter(tmp_path).detect()
    assert info is not None
    assert info.kind.value == "bambu_studio"


def test_bambu_slicing_currently_unsupported(tmp_path: Path) -> None:
    if not BAMBU_PATH.is_file():
        pytest.skip("Bambu Studio is not installed")
    info = _bambu_adapter(tmp_path).detect()
    assert info is not None
    assert info.slicing_supported is False
    assert "unavailable" in " ".join(info.notes)


def test_bambu_validate_input_works(tmp_path: Path) -> None:
    if not BAMBU_PATH.is_file():
        pytest.skip("Bambu Studio is not installed")
    cube = write_cube_stl(tmp_path / "cube.stl")
    validation = _bambu_adapter(tmp_path).validate_input(cube)
    assert validation.is_valid
    assert validation.size == (20.0, 20.0, 20.0)


def test_bambu_slice_fails_gracefully(tmp_path: Path) -> None:
    if not BAMBU_PATH.is_file():
        pytest.skip("Bambu Studio is not installed")
    from print_engineer.core.types import ProfileInfo, ProfileKind, ProfileSource, SliceJob

    cube = write_cube_stl(tmp_path / "cube.stl")
    job = SliceJob(
        model_path=cube,
        profile=ProfileInfo(
            name="x", kind=ProfileKind.PROCESS, source=ProfileSource.GENERATED
        ),
        filament=ProfileInfo(
            name="y", kind=ProfileKind.FILAMENT, source=ProfileSource.GENERATED
        ),
        printer=ProfileInfo(
            name="z", kind=ProfileKind.PRINTER, source=ProfileSource.GENERATED
        ),
        timeout_seconds=30.0,
    )
    with pytest.raises(SlicerUnavailable) as excinfo:
        _bambu_adapter(tmp_path).slice(job)
    payload = excinfo.value.to_dict()
    assert payload["code"] == "slicer_unavailable"
    assert "crash" in payload["message"].lower()
