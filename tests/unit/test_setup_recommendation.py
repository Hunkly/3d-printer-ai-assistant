"""Tests for the filament candidate matrix and setup engine (Phase 3A.1)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest
from tests.slicer_helpers import FakeProfileAdapter, make_profile

from print_engineer.config import Settings
from print_engineer.core.recommendation import (
    FilamentCandidateMatrix,
    PrintContextIntent,
    RecommendationGoal,
    RecommendationMode,
    ResolvedPrintContext,
    SetupRequest,
)
from print_engineer.core.types import ProfileKind
from print_engineer.errors import LLMUnavailable, UnresolvedPrintContext
from print_engineer.recommendation.context import PrintContextResolver
from print_engineer.recommendation.filament import FilamentMatrixBuilder
from print_engineer.recommendation.setup import SetupEngine

_BASE = {"nozzle_diameter": "0.4;0.2;0.6;0.8"}
_VARIANT_04 = {
    "printer_model": "Bambu Lab A1",
    "printer_variant": "0.4",
    "nozzle_diameter": ["0.4"],
    "printable_height": "256",
    "default_print_profile": "0.20mm Standard @BBL A1",
}
_PROCESS = {
    "layer_height": "0.2",
    "wall_loops": "2",
    "sparse_infill_density": "15%",
    "sparse_infill_pattern": "grid",
}

_BAMBU_PLA = {
    "filament_type": "PLA",
    "filament_vendor": ["Bambu Lab"],
    "filament_density": "1.24",
    "filament_max_volumetric_speed": "21",
    "filament_cost": "19.99",
    "nozzle_temperature": "220",
    "nozzle_temperature_range_low": "190",
    "nozzle_temperature_range_high": "230",
    "nozzle_temperature_initial_layer": "220",
    "cool_plate_temp": "35",
    "textured_plate_temp": "45",
    "compatible_printers": ["Bambu Lab A1"],
}

_SUNLU_PLA = {
    "filament_type": "PLA",
    "filament_vendor": ["Bambu Lab"],
    "filament_density": "1.24",
    "filament_max_volumetric_speed": "45",
    "filament_cost": "10.79",
    "nozzle_temperature": "225",
    "nozzle_temperature_range_low": "195",
    "nozzle_temperature_range_high": "220",
    "compatible_printers": ["Bambu Lab A1"],
}

_BAMBU_PETG = {
    "filament_type": "PETG",
    "filament_vendor": ["Bambu Lab"],
    "filament_density": "1.27",
    "filament_max_volumetric_speed": "12",
    "filament_cost": "22.99",
    "nozzle_temperature": "240",
    "compatible_printers": ["Bambu Lab A1"],
}

_X1_ONLY = {
    "filament_type": "PLA",
    "filament_vendor": ["Bambu Lab"],
    "compatible_printers": ["Bambu Lab X1"],
}


def _adapter() -> FakeProfileAdapter:
    base = make_profile(
        ProfileKind.PRINTER, "Bambu Lab A1", _BASE, materialized=False
    )
    variant = make_profile(
        ProfileKind.PRINTER, "Bambu Lab A1 0.4 nozzle", _VARIANT_04, materialized=True
    )
    process = make_profile(ProfileKind.PROCESS, "0.20mm Standard @BBL A1", _PROCESS)

    bambu_pla = make_profile(ProfileKind.FILAMENT, "Bambu PLA Basic @BBL A1", _BAMBU_PLA)
    sunlu_pla_raw = make_profile(
        ProfileKind.FILAMENT,
        "SUNLU PLA Basic @BBL A1",
        {
            "filament_type": "PLA",
            "filament_max_volumetric_speed": "45",
            "filament_cost": "10.79",
            "nozzle_temperature": "225",
            "nozzle_temperature_range_low": "195",
            "nozzle_temperature_range_high": "220",
            "compatible_printers": ["Bambu Lab A1"],
        },
    )
    sunlu_pla = make_profile(
        ProfileKind.FILAMENT, "SUNLU PLA Basic @BBL A1", _SUNLU_PLA
    )
    bambu_petg = make_profile(ProfileKind.FILAMENT, "Bambu PETG Basic @BBL A1", _BAMBU_PETG)
    x1_only = make_profile(ProfileKind.FILAMENT, "Bambu PLA X1 Only @BBL X1", _X1_ONLY)

    return FakeProfileAdapter(
        profiles={
            ProfileKind.PRINTER: [base, variant],
            ProfileKind.PROCESS: [process],
            ProfileKind.FILAMENT: [
                bambu_pla,
                sunlu_pla_raw,
                bambu_petg,
                x1_only,
            ],
        },
        materialized={
            "printer:Bambu Lab A1 0.4 nozzle": variant,
            "process:0.20mm Standard @BBL A1": process,
            "filament:Bambu PLA Basic @BBL A1": bambu_pla,
            "filament:SUNLU PLA Basic @BBL A1": sunlu_pla,
            "filament:Bambu PETG Basic @BBL A1": bambu_petg,
            "filament:Bambu PLA X1 Only @BBL X1": x1_only,
        },
    )


def _settings() -> Settings:
    return Settings.load(root=Path("runtime/data").absolute())


def _resolved(adapter: FakeProfileAdapter) -> ResolvedPrintContext:
    return PrintContextResolver(_settings(), adapter=adapter).resolve(
        PrintContextIntent(printer="Bambu Lab A1")
    )


def _matrix(
    adapter: FakeProfileAdapter,
    goal: RecommendationGoal,
    *,
    vendor: str | None = None,
    material: str | None = None,
) -> FilamentCandidateMatrix:
    resolved = _resolved(adapter)
    return FilamentMatrixBuilder(_settings(), adapter).build(
        resolved, goal=goal, vendor=vendor, material=material
    )


class TestVendorVerification:
    def test_sunlu_inherited_vendor_trap(self) -> None:
        matrix = _matrix(_adapter(), RecommendationGoal.BALANCED)
        by_name = {candidate.profile_name: candidate for candidate in matrix.candidates}
        sunlu = by_name["SUNLU PLA Basic @BBL A1"]
        bambu = by_name["Bambu PLA Basic @BBL A1"]

        assert bambu.vendor_verified is True
        assert bambu.vendor == "Bambu Lab"
        assert bambu.data_warnings == []

        assert sunlu.vendor == "Bambu Lab"
        assert sunlu.vendor_verified is False
        assert any("SUNLU" in warning and "Bambu Lab" in warning for warning in sunlu.data_warnings)

    def test_temperature_outside_declared_range_flagged(self) -> None:
        matrix = _matrix(_adapter(), RecommendationGoal.BALANCED)
        sunlu = next(
            c for c in matrix.candidates if c.profile_name == "SUNLU PLA Basic @BBL A1"
        )
        assert sunlu.nozzle_temperature_c == 225.0
        assert sunlu.nozzle_temperature_range_high_c == 220.0
        assert any("outside the declared range" in warning for warning in sunlu.data_warnings)

    def test_rank_penalizes_unverified_and_inconsistent(self) -> None:
        matrix = _matrix(_adapter(), RecommendationGoal.BALANCED)
        by_name = {candidate.profile_name: candidate for candidate in matrix.candidates}
        assert by_name["Bambu PLA Basic @BBL A1"].score > by_name[
            "SUNLU PLA Basic @BBL A1"
        ].score


class TestCompatibilityFilters:
    def test_incompatible_printer_rejected(self) -> None:
        matrix = _matrix(_adapter(), RecommendationGoal.BALANCED)
        names = {candidate.profile_name for candidate in matrix.candidates}
        assert "Bambu PLA X1 Only @BBL X1" not in names
        rejected = next(
            r for r in matrix.rejected if r.reason_code == "incompatible_printer"
        )
        assert rejected.profile_name == "Bambu PLA X1 Only @BBL X1"

    def test_build_plate_filter(self) -> None:
        adapter = _adapter()
        resolved = PrintContextResolver(_settings(), adapter=adapter).resolve(
            PrintContextIntent(printer="Bambu Lab A1", build_plate="cool plate")
        )
        matrix = FilamentMatrixBuilder(_settings(), adapter).build(
            resolved, goal=RecommendationGoal.BALANCED
        )
        by_name = {candidate.profile_name: candidate for candidate in matrix.candidates}
        assert "Bambu PLA Basic @BBL A1" in by_name
        assert any(
            r.reason_code == "incompatible_build_plate" for r in matrix.rejected
        )

    def test_material_filter(self) -> None:
        matrix = _matrix(_adapter(), RecommendationGoal.BALANCED, material="PLA")
        names = {candidate.profile_name for candidate in matrix.candidates}
        assert all("PETG" not in name for name in names)
        assert any(r.reason_code == "material_filter" for r in matrix.rejected)

    def test_vendor_filter(self) -> None:
        matrix = _matrix(_adapter(), RecommendationGoal.BALANCED, vendor="Bambu")
        names = {candidate.profile_name for candidate in matrix.candidates}
        assert "SUNLU PLA Basic @BBL A1" in names
        assert matrix.candidates


class TestRanking:
    def test_print_time_prefers_max_volumetric_speed(self) -> None:
        matrix = _matrix(_adapter(), RecommendationGoal.PRINT_TIME)
        top = matrix.candidates[0]
        speeds = [
            c.max_volumetric_speed
            for c in matrix.candidates
            if c.max_volumetric_speed is not None
        ]
        assert speeds
        assert top.max_volumetric_speed == max(speeds)
        sunlu = next(c for c in matrix.candidates if "SUNLU" in c.profile_name)
        assert sunlu in matrix.candidates[:2]

    def test_filament_usage_prefers_lighter_and_cheaper(self) -> None:
        matrix = _matrix(_adapter(), RecommendationGoal.FILAMENT_USAGE)
        top = matrix.candidates[0]
        assert top.material_type is not None
        assert "PLA" in top.material_type

    def test_strength_goal_marks_external_evidence(self) -> None:
        matrix = _matrix(_adapter(), RecommendationGoal.STRENGTH)
        assert matrix.candidates
        assert all(candidate.requires_external_evidence for candidate in matrix.candidates)


class TestNoSlice:
    def test_matrix_build_never_slices(self) -> None:
        adapter = _adapter()
        matrix = _matrix(adapter, RecommendationGoal.BALANCED)
        assert adapter.slicer_calls == []
        assert matrix.candidates


def _engine(
    adapter: FakeProfileAdapter,
    *,
    llm: object | None = None,
    settings: Settings | None = None,
) -> SetupEngine:
    settings = settings or _settings()
    return SetupEngine(
        settings, llm=llm, resolver=PrintContextResolver(settings, adapter=adapter)
    )


class TestSetupEngine:
    def test_full_four_layer_setup(self) -> None:
        engine = _engine(_adapter())
        result = engine.recommend(SetupRequest(printer="Bambu Lab A1"))

        assert result.mode == RecommendationMode.DETERMINISTIC
        assert result.material is not None
        assert result.filament is not None
        assert result.nozzle is not None
        assert result.nozzle.nozzle_diameter_mm == 0.4
        assert result.process is not None
        assert result.process.source == "printer_default"
        assert result.process.process_profile == "0.20mm Standard @BBL A1"
        assert result.process.goal_hint is not None
        assert result.matrix.candidates

    def test_printer_required(self) -> None:
        engine = _engine(_adapter())
        with pytest.raises(UnresolvedPrintContext):
            engine.recommend(SetupRequest(printer=None))

    def test_unknown_printer_raises(self) -> None:
        engine = _engine(_adapter())
        with pytest.raises(UnresolvedPrintContext):
            engine.recommend(SetupRequest(printer="Bogus Printer"))

    def test_strength_goal_sets_external_evidence(self) -> None:
        engine = _engine(_adapter())
        result = engine.recommend(
            SetupRequest(printer="Bambu Lab A1", goal=RecommendationGoal.STRENGTH)
        )
        assert result.material is not None
        assert result.material.requires_external_evidence is True
        assert result.material.alternatives


class _FakeLLM:
    def __init__(self, payload: Mapping[str, object] | Exception) -> None:
        self._payload = payload
        self.prompts: list[str] = []

    def complete_json(
        self, prompt: str, *, timeout_seconds: float | None = None
    ) -> dict[str, object]:
        self.prompts.append(prompt)
        if isinstance(self._payload, Exception):
            raise self._payload
        return dict(self._payload)


class TestLLMNarrative:
    def test_grounded_narrative_used(self) -> None:
        engine = _engine(
            _adapter(),
            llm=_FakeLLM(
                {
                    "summary": (
                        "For the balanced goal the top profile is Bambu PLA Basic @BBL A1. "
                        "Nozzle temperature range = 190-230 C."
                    ),
                    "rationale": "Density = 1.24 g/cm3 and Max volumetric speed = 21.0 mm3/s.",
                    "warnings": [],
                }
            ),
        )
        result = engine.recommend(SetupRequest(printer="Bambu Lab A1"))
        assert result.mode == RecommendationMode.LLM
        assert "Bambu PLA Basic" in result.summary
        assert not any("LLM reasoning unavailable" in w for w in result.warnings)

    def test_ungrounded_narrative_dropped(self) -> None:
        engine = _engine(
            _adapter(),
            llm=_FakeLLM(
                {
                    "summary": "This filament prints beautifully at 9.99 km/h.",
                    "rationale": "It is the strongest material on the market.",
                    "warnings": [],
                }
            ),
        )
        result = engine.recommend(SetupRequest(printer="Bambu Lab A1"))
        assert result.mode == RecommendationMode.DETERMINISTIC
        assert any("LLM reasoning unavailable" in w for w in result.warnings)

    def test_llm_unavailable_falls_back(self) -> None:
        settings = _settings()
        settings.recommend.allow_deterministic_fallback = True
        engine = _engine(
            _adapter(), llm=_FakeLLM(LLMUnavailable("offline")), settings=settings
        )
        result = engine.recommend(SetupRequest(printer="Bambu Lab A1"))
        assert result.mode == RecommendationMode.DETERMINISTIC
        assert any("LLM reasoning unavailable" in w for w in result.warnings)

    def test_llm_unavailable_raises_when_no_fallback(self) -> None:
        settings = _settings()
        settings.recommend.allow_deterministic_fallback = False
        engine = _engine(
            _adapter(), llm=_FakeLLM(LLMUnavailable("offline")), settings=settings
        )
        with pytest.raises(LLMUnavailable):
            engine.recommend(SetupRequest(printer="Bambu Lab A1"))
