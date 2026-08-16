"""Tests for core ABC interfaces and shared data types."""

from __future__ import annotations

from abc import ABC
from datetime import UTC, datetime
from pathlib import Path

from print_engineer.core.interfaces import ModelAnalyzer, Printer, PrintHistory, Slicer
from print_engineer.core.types import (
    AMSInfo,
    PrinterState,
    PrinterStatus,
    ProfileInfo,
    ProfileKind,
    SliceJob,
    SliceResult,
    SlicerKind,
)


def test_interfaces_are_abstract() -> None:
    for cls in (Printer, Slicer, ModelAnalyzer, PrintHistory):
        assert issubclass(cls, ABC)
        assert cls.__abstractmethods__, f"{cls.__name__} has no abstract members"


def test_printer_status_defaults() -> None:
    status = PrinterStatus()
    assert status.state == PrinterState.UNKNOWN
    assert not status.is_connected
    assert status.bed_temp is None
    assert status.ams is None


def test_printer_status_with_ams() -> None:
    ams = AMSInfo(slots=["A1", "B1"])
    status = PrinterStatus(state=PrinterState.PRINTING, progress=0.5, ams=ams)
    assert status.state == PrinterState.PRINTING
    assert status.ams is not None
    assert status.ams.slots == ["A1", "B1"]


def test_slice_result_roundtrip(tmp_path: Path) -> None:
    profile = ProfileInfo(name="0.16mm", kind=ProfileKind.PROCESS)
    filament = ProfileInfo(name="PLA Basic", kind=ProfileKind.FILAMENT)
    job = SliceJob(
        model_path=tmp_path / "m.stl",
        profile=profile,
        filament=filament,
        output_dir=tmp_path,
        kind=SlicerKind.ORCA_SLICER,
    )
    result = SliceResult(job=job, output_3mf=tmp_path / "m.gcode.3mf", sliced_at=datetime.now(UTC))
    assert result.job.kind == SlicerKind.ORCA_SLICER
    assert result.job.profile.name == "0.16mm"
    assert result.estimated_time_minutes is None
    assert result.notes == []
