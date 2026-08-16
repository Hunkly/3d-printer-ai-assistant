"""Real OrcaSlicer integration test: slice a generated cube end to end.

Skips when OrcaSlicer is not installed. All slicing artifacts land under the
adapter's workdir (pytest tmp_path).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from tests.slicer_helpers import find_compatible_triple, write_cube_stl

from print_engineer.adapters.slicer.orca import OrcaSlicerAdapter
from print_engineer.core.types import SliceJob

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("win"), reason="OrcaSlicer probing is Windows-only"
)

ORCA_PATH = Path(r"C:\Program Files\OrcaSlicer\orca-slicer.exe")


def _adapter(tmp_path: Path) -> OrcaSlicerAdapter:
    return OrcaSlicerAdapter(workdir=tmp_path / "workspace", timeout_seconds=600.0)


def test_orca_slices_generated_cube(tmp_path: Path) -> None:
    if not ORCA_PATH.is_file():
        pytest.skip("OrcaSlicer is not installed")

    adapter = _adapter(tmp_path)
    info = adapter.detect()
    assert info is not None, "OrcaSlicer must be detectable before slicing"

    cube = write_cube_stl(tmp_path / "cube.stl")
    process, filament, machine = find_compatible_triple(adapter)

    job = SliceJob(
        model_path=cube,
        profile=process,
        filament=filament,
        printer=machine,
        timeout_seconds=600.0,
    )
    result = adapter.slice(job)

    assert result.gcode_path is not None and result.gcode_path.is_file()
    assert result.output_3mf is not None and result.output_3mf.is_file()
    assert result.gcode_path.name == "plate_1.gcode"
    assert result.output_3mf.suffix == ".3mf"
    assert result.return_code == 0
    assert result.estimated_time_minutes is not None and result.estimated_time_minutes > 0
    assert result.layer_count is not None and result.layer_count > 0
    assert result.max_z_height is not None and result.max_z_height > 0
    assert result.filament_used_cm3 is not None and result.filament_used_cm3 > 0
    assert result.gcode_path.read_text(encoding="utf-8")[:9] == "; HEADER_"
