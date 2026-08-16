"""Tests for the typed slicer settings digest reader (Phase 3A)."""

from __future__ import annotations

import json
from pathlib import Path

from print_engineer.adapters.slicer.settings import build_digest, is_materialized
from print_engineer.core.types import ProfileInfo, ProfileKind


def _profile(
    kind: ProfileKind, name: str, content: str | None, materialized: bool = True
) -> ProfileInfo:
    return ProfileInfo(
        name=name,
        kind=kind,
        path=Path(f"{name}.json"),
        content=content,
        materialized=materialized,
    )


class TestProcessParsing:
    def test_parses_strings_arrays_and_percent(self) -> None:
        content = json.dumps(
            {
                "layer_height": "0.2",
                "wall_loops": "3",
                "sparse_infill_density": "15%",
                "sparse_infill_pattern": "gyroid",
                "enable_support": "1",
                "support_type": "tree(auto)",
                "outer_wall_speed": ["120", "120"],
                "support_threshold_angle": "50",
            }
        )
        process = _profile(ProfileKind.PROCESS, "P1", content)
        digest = build_digest(slicer_kind="orca_slicer", process=process)

        assert digest.process is not None
        assert digest.process.name == "P1"
        assert digest.process.layer_height_mm == 0.2
        assert digest.process.wall_loops == 3
        assert digest.process.sparse_infill_percent == 15.0
        assert digest.process.sparse_infill_pattern == "gyroid"
        assert digest.process.enable_support is True
        assert digest.process.support_type == "tree(auto)"
        assert digest.process.outer_wall_speed_mms == 120.0
        assert digest.process.support_threshold_angle_deg == 50.0

    def test_unparseable_values_become_none_and_recorded_unavailable(self) -> None:
        content = json.dumps(
            {
                "layer_height": "twenty",
                "wall_loops": "2.5",
                "sparse_infill_density": "a lot",
            }
        )
        process = _profile(ProfileKind.PROCESS, "P2", content)
        digest = build_digest(slicer_kind="orca_slicer", process=process)

        assert digest.process is not None
        assert digest.process.layer_height_mm is None
        assert digest.process.wall_loops is None
        assert digest.process.sparse_infill_percent is None
        assert "layer_height_mm" in digest.unavailable
        assert "wall_loops" in digest.unavailable
        assert "sparse_infill_percent" in digest.unavailable

    def test_missing_fields_recorded_unavailable(self) -> None:
        content = json.dumps({"layer_height": "0.2"})
        process = _profile(ProfileKind.PROCESS, "P3", content)
        digest = build_digest(slicer_kind="orca_slicer", process=process)
        assert digest.process is not None
        assert digest.process.layer_height_mm == 0.2
        assert "layer_height_mm" not in digest.unavailable
        assert "wall_loops" in digest.unavailable

    def test_boolean_parse_variants(self) -> None:
        content = json.dumps(
            {
                "enable_support": "true",
                "support_on_build_plate_only": "0",
                "detect_thin_wall": "off",
                "spiral_mode": "no",
                "adaptive_layer_height": "yes",
            }
        )
        process = _profile(ProfileKind.PROCESS, "P4", content)
        digest = build_digest(slicer_kind="orca_slicer", process=process)
        assert digest.process is not None
        assert digest.process.enable_support is True
        assert digest.process.support_on_build_plate_only is False
        assert digest.process.detect_thin_wall is False
        assert digest.process.spiral_mode is False
        assert digest.process.adaptive_layer_height is True


class TestFilamentParsing:
    def test_material_type_inferred_from_name(self) -> None:
        content = json.dumps(
            {
                "filament_density": "1.24",
                "filament_max_volumetric_speed": ["15"],
                "filament_vendor": ["Bambu Lab"],
            }
        )
        filament = _profile(ProfileKind.FILAMENT, "Bambu PLA Basic @BBL A1", content)
        digest = build_digest(slicer_kind="orca_slicer", filament=filament)
        assert digest.filament is not None
        assert digest.filament.material_type == "PLA"
        assert digest.filament.density_g_cm3 == 1.24
        assert digest.filament.max_volumetric_speed == 15.0
        assert digest.filament.vendor == "Bambu Lab"

    def test_material_type_prefers_filament_type_field(self) -> None:
        content = json.dumps({"filament_type": "PETG"})
        filament = _profile(ProfileKind.FILAMENT, "Mystery Stuff", content)
        digest = build_digest(slicer_kind="orca_slicer", filament=filament)
        assert digest.filament is not None
        assert digest.filament.material_type == "PETG"

    def test_missing_density_recorded(self) -> None:
        content = json.dumps({"filament_max_volumetric_speed": "10"})
        filament = _profile(ProfileKind.FILAMENT, "PLA", content)
        digest = build_digest(slicer_kind="orca_slicer", filament=filament)
        assert digest.filament is not None
        assert "density_g_cm3" in digest.unavailable


class TestPrinterParsing:
    def test_parses_printer(self) -> None:
        content = json.dumps(
            {
                "nozzle_diameter": ["0.4"],
                "printable_height": "256",
                "printer_model": "Bambu Lab A1",
                "printer_variant": "0.4",
            }
        )
        printer = _profile(ProfileKind.PRINTER, "Bambu Lab A1 0.4 nozzle", content)
        digest = build_digest(slicer_kind="orca_slicer", printer=printer)
        assert digest.printer is not None
        assert digest.printer.nozzle_diameter_mm == 0.4
        assert digest.printer.printable_height_mm == 256.0
        assert digest.printer.printer_model == "Bambu Lab A1"
        assert digest.printer.printer_variant == "0.4"


class TestDigestAssembly:
    def test_all_none_profiles_adds_note(self) -> None:
        digest = build_digest(slicer_kind="orca_slicer")
        assert digest.process is None
        assert digest.filament is None
        assert digest.printer is None
        assert any("no slicer profile information" in n for n in digest.notes)

    def test_invalid_json_flagged(self) -> None:
        process = _profile(ProfileKind.PROCESS, "broken", "{not json")
        digest = build_digest(slicer_kind="orca_slicer", process=process)
        assert digest.process is None
        assert any("could not be parsed" in n for n in digest.notes)

    def test_slicer_kind_passed_through(self) -> None:
        digest = build_digest(slicer_kind="bambu_studio")
        assert digest.slicer_kind == "bambu_studio"


class TestIsMaterialized:
    def test_materialized_profile(self) -> None:
        profile = _profile(ProfileKind.PROCESS, "p", "{}", materialized=True)
        assert is_materialized(profile) is True

    def test_non_materialized_profile(self) -> None:
        profile = _profile(ProfileKind.PROCESS, "p", "{}", materialized=False)
        assert is_materialized(profile) is False
