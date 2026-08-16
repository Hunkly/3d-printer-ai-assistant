"""Tests for print-context resolution (Phase 3A.1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.slicer_helpers import FakeProfileAdapter, make_profile

from print_engineer.config import Settings
from print_engineer.core.recommendation import PrintContextIntent
from print_engineer.core.types import ProfileKind
from print_engineer.errors import (
    AmbiguousPrintContext,
    SlicerError,
    UnresolvedPrintContext,
)
from print_engineer.recommendation.context import PrintContextResolver, parse_nozzle_values

_BASE_MACHINE = {"nozzle_diameter": "0.4;0.2;0.6;0.8"}
_VARIANT_04 = {
    "printer_model": "Bambu Lab A1",
    "printer_variant": "0.4",
    "nozzle_diameter": ["0.4"],
    "printable_height": "256",
    "default_print_profile": "0.20mm Standard @BBL A1",
    "default_filament_profile": ["Bambu PLA Basic @BBL A1"],
}
_PROCESS = {
    "layer_height": "0.2",
    "wall_loops": "2",
    "sparse_infill_density": "15%",
}
_FILAMENT = {
    "filament_type": "PLA",
    "filament_vendor": ["Bambu Lab"],
    "filament_density": "1.24",
}


def _adapter() -> FakeProfileAdapter:
    base = make_profile(
        ProfileKind.PRINTER, "Bambu Lab A1", _BASE_MACHINE, materialized=False
    )
    variant = make_profile(
        ProfileKind.PRINTER,
        "Bambu Lab A1 0.4 nozzle",
        _VARIANT_04,
        materialized=True,
    )
    process = make_profile(ProfileKind.PROCESS, "0.20mm Standard @BBL A1", _PROCESS)
    filament = make_profile(ProfileKind.FILAMENT, "Bambu PLA Basic @BBL A1", _FILAMENT)
    return FakeProfileAdapter(
        profiles={
            ProfileKind.PRINTER: [base, variant],
            ProfileKind.PROCESS: [process],
            ProfileKind.FILAMENT: [filament],
        },
        materialized={
            "printer:Bambu Lab A1 0.4 nozzle": variant,
            "process:0.20mm Standard @BBL A1": process,
            "filament:Bambu PLA Basic @BBL A1": filament,
        },
    )


def _resolver(adapter: FakeProfileAdapter) -> PrintContextResolver:
    settings = Settings.load(root=Path("runtime/data").absolute())
    return PrintContextResolver(settings, adapter=adapter)


class TestParseNozzle:
    def test_semicolon_string(self) -> None:
        assert parse_nozzle_values("0.4;0.2;0.6;0.8") == [0.2, 0.4, 0.6, 0.8]

    def test_single_value_and_list(self) -> None:
        assert parse_nozzle_values("0.4") == [0.4]
        assert parse_nozzle_values(["0.4"]) == [0.4]
        assert parse_nozzle_values(0.6) == [0.6]

    def test_junk_ignored(self) -> None:
        assert parse_nozzle_values("0.4;banana;0.6") == [0.4, 0.6]


class TestResolvePrinter:
    def test_base_machine_semicolon_nozzles(self) -> None:
        resolved = _resolver(_adapter()).resolve(
            PrintContextIntent(printer="Bambu Lab A1")
        )
        assert resolved.printer is not None
        assert resolved.printer.name == "Bambu Lab A1"
        assert resolved.printer.supported_nozzle_mm == [0.2, 0.4, 0.6, 0.8]
        assert resolved.printer.nozzle_diameter_mm is None
        assert resolved.nozzle_diameter_mm is None

    def test_exact_variant_profile(self) -> None:
        resolved = _resolver(_adapter()).resolve(
            PrintContextIntent(printer="Bambu Lab A1 0.4 nozzle")
        )
        assert resolved.printer is not None
        assert resolved.printer.nozzle_diameter_mm == 0.4
        assert resolved.printer.supported_nozzle_mm == [0.4]
        assert resolved.printer.default_print_profile == "0.20mm Standard @BBL A1"
        assert resolved.nozzle_diameter_mm == 0.4

    def test_unknown_printer_raises_unresolved(self) -> None:
        with pytest.raises(UnresolvedPrintContext) as excinfo:
            _resolver(_adapter()).resolve(PrintContextIntent(printer="Bogus Printer"))
        assert excinfo.value.code == "unresolved_print_context"
        assert excinfo.value.details["matches"] == []

    def test_ambiguous_prefix_match_raises(self) -> None:
        mini = make_profile(
            ProfileKind.PRINTER,
            "Bambu Lab A1 mini 0.4 nozzle",
            {"printer_model": "Bambu Lab A1 mini", "nozzle_diameter": ["0.4"]},
            materialized=True,
        )
        adapter = _adapter()
        del adapter.profiles[ProfileKind.PRINTER][0]
        adapter.profiles[ProfileKind.PRINTER].append(mini)
        adapter.materialized["printer:Bambu Lab A1 mini 0.4 nozzle"] = mini
        with pytest.raises(AmbiguousPrintContext) as excinfo:
            _resolver(adapter).resolve(PrintContextIntent(printer="Bambu Lab"))
        assert excinfo.value.code == "ambiguous_print_context"
        assert "Bambu Lab A1 mini" in excinfo.value.details["matches"]
        assert "Bambu Lab A1" in excinfo.value.details["matches"]

    def test_model_match_union_of_nozzles(self) -> None:
        adapter = _adapter()
        del adapter.profiles[ProfileKind.PRINTER][0]
        resolved = _resolver(adapter).resolve(PrintContextIntent(printer="Bambu Lab A1"))
        assert resolved.printer is not None
        assert resolved.printer.supported_nozzle_mm == [0.4]
        assert resolved.printer.nozzle_diameter_mm == 0.4

    def test_unknown_slicer_kind_raises_slicer_error(self) -> None:
        with pytest.raises(SlicerError) as excinfo:
            _resolver(_adapter()).resolve(PrintContextIntent(slicer_kind="not_a_slicer"))
        assert excinfo.value.code == "slicer_error"


class TestResolveNozzle:
    def test_user_nozzle_within_supported(self) -> None:
        resolved = _resolver(_adapter()).resolve(
            PrintContextIntent(printer="Bambu Lab A1", nozzle_diameter_mm=0.6)
        )
        assert resolved.nozzle_diameter_mm == 0.6

    def test_user_nozzle_outside_supported_raises(self) -> None:
        with pytest.raises(UnresolvedPrintContext) as excinfo:
            _resolver(_adapter()).resolve(
                PrintContextIntent(printer="Bambu Lab A1", nozzle_diameter_mm=0.3)
            )
        assert excinfo.value.code == "unresolved_print_context"
        assert excinfo.value.details["supported_nozzle_mm"] == [0.2, 0.4, 0.6, 0.8]


class TestDefaults:
    def test_use_defaults_applies_configured_printer(self, base_settings: Settings) -> None:
        base_settings.recommend.default_printer = "Bambu Lab A1 0.4 nozzle"
        resolver = PrintContextResolver(base_settings, adapter=_adapter())
        resolved = resolver.resolve(PrintContextIntent(use_defaults=True))
        assert resolved.printer is not None
        assert resolved.printer.nozzle_diameter_mm == 0.4
        assert any("default printer" in warning for warning in resolved.warnings)

    def test_use_defaults_without_default_raises(self, base_settings: Settings) -> None:
        base_settings.recommend.default_printer = None
        resolver = PrintContextResolver(base_settings, adapter=_adapter())
        with pytest.raises(UnresolvedPrintContext):
            resolver.resolve(PrintContextIntent(use_defaults=True))

    def test_no_printer_and_no_defaults_leaves_printer_none(self) -> None:
        resolved = _resolver(_adapter()).resolve(PrintContextIntent())
        assert resolved.printer is None
        assert resolved.nozzle_diameter_mm is None


class TestResolveProfiles:
    def test_process_profile_resolved(self) -> None:
        resolved = _resolver(_adapter()).resolve(
            PrintContextIntent(process_profile="0.20mm Standard @BBL A1")
        )
        assert resolved.process is not None
        assert resolved.process.layer_height_mm == 0.2

    def test_unknown_process_profile_raises(self) -> None:
        with pytest.raises(UnresolvedPrintContext):
            _resolver(_adapter()).resolve(PrintContextIntent(process_profile="nope"))

    def test_printer_default_process_resolved(self) -> None:
        intent = PrintContextIntent(printer="Bambu Lab A1 0.4 nozzle")
        resolved = _resolver(_adapter()).resolve(intent)
        assert resolved.process is not None
        assert resolved.process.name == "0.20mm Standard @BBL A1"

    def test_filament_profile_resolved(self) -> None:
        resolved = _resolver(_adapter()).resolve(
            PrintContextIntent(filament_profile="Bambu PLA Basic @BBL A1")
        )
        assert resolved.filament is not None
        assert resolved.filament.material_type == "PLA"
