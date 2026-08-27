"""Tests for the filament candidate matrix and setup engine (Phase 3A.1)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

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

_BASE = {
    "nozzle_diameter": "0.4;0.2;0.6;0.8",
    "default_print_profile": "0.20mm Standard @BBL A1",
}
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
    "cool_plate_temp": ["35"],
    "textured_plate_temp": ["45"],
    "hot_plate_temp": ["55"],
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


def _plate_adapter(
    data: Mapping[str, object],
    *,
    name: str = "Bambu PLA Basic @BBL A1",
    material: str | None = None,
) -> FakeProfileAdapter:
    filament_data = dict(data)
    if material is not None:
        filament_data["filament_type"] = material
    base = make_profile(ProfileKind.PRINTER, "Bambu Lab A1", _BASE, materialized=False)
    variant = make_profile(
        ProfileKind.PRINTER, "Bambu Lab A1 0.4 nozzle", _VARIANT_04, materialized=True
    )
    process = make_profile(ProfileKind.PROCESS, "0.20mm Standard @BBL A1", _PROCESS)
    filament = make_profile(ProfileKind.FILAMENT, name, filament_data)
    return FakeProfileAdapter(
        profiles={
            ProfileKind.PRINTER: [base, variant],
            ProfileKind.PROCESS: [process],
            ProfileKind.FILAMENT: [filament],
        },
        materialized={
            "printer:Bambu Lab A1 0.4 nozzle": variant,
            "process:0.20mm Standard @BBL A1": process,
            f"filament:{name}": filament,
        },
    )


class _CountingProfileAdapter(FakeProfileAdapter):
    def __init__(self, source: FakeProfileAdapter) -> None:
        super().__init__(source.profiles, source.materialized)
        self.filament_lookups: dict[str, int] = {}

    def find_profile(self, profile_kind: ProfileKind, name: str):  # type: ignore[no-untyped-def]
        if profile_kind == ProfileKind.FILAMENT:
            self.filament_lookups[name] = self.filament_lookups.get(name, 0) + 1
        return super().find_profile(profile_kind, name)


class _MissingMaterializedProfileAdapter(FakeProfileAdapter):
    def find_profile(self, profile_kind: ProfileKind, name: str):  # type: ignore[no-untyped-def]
        if profile_kind == ProfileKind.FILAMENT:
            return None
        return super().find_profile(profile_kind, name)


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
            PrintContextIntent(printer="Bambu Lab A1", build_plate="cool_plate")
        )
        matrix = FilamentMatrixBuilder(_settings(), adapter).build(
            resolved, goal=RecommendationGoal.BALANCED
        )
        by_name = {candidate.profile_name: candidate for candidate in matrix.candidates}
        assert "Bambu PLA Basic @BBL A1" in by_name
        assert any(
            r.reason_code == "incompatible_build_plate" for r in matrix.rejected
        )

    @pytest.mark.parametrize(
        ("plate", "field", "other_field"),
        [
            ("cool_plate", "cool_plate_temp", "textured_plate_temp"),
            ("textured_pei_plate", "textured_plate_temp", "cool_plate_temp"),
            ("high_temp_plate", "hot_plate_temp", "cool_plate_temp"),
        ],
    )
    def test_canonical_plate_uses_only_selected_materialized_field(
        self, plate: str, field: str, other_field: str
    ) -> None:
        data = dict(_BAMBU_PLA)
        data[field] = ["35"]
        data[other_field] = ["0"]
        adapter = _plate_adapter(data)
        resolved = PrintContextResolver(_settings(), adapter=adapter).resolve(
            PrintContextIntent(printer="Bambu Lab A1", build_plate=plate)
        )

        matrix = FilamentMatrixBuilder(_settings(), adapter).build(resolved)

        assert len(matrix.candidates) == 1

    @pytest.mark.parametrize(
        "plate",
        [
            "Cool Plate",
            "Textured PEI Plate",
            "High Temp Plate",
            "cool plate",
            "COOL_PLATE",
            "hot",
            "high temp",
            "engineering",
            "1",
        ],
    )
    def test_unapproved_plate_vocabulary_fails_closed(self, plate: str) -> None:
        adapter = _plate_adapter(_BAMBU_PLA)
        resolved = PrintContextResolver(_settings(), adapter=adapter).resolve(
            PrintContextIntent(printer="Bambu Lab A1", build_plate=plate)
        )

        matrix = FilamentMatrixBuilder(_settings(), adapter).build(resolved)

        assert matrix.candidates == []
        assert matrix.rejected[0].reason_code == "incompatible_build_plate"

    @pytest.mark.parametrize(
        "value",
        [
            None,
            [],
            ["35", "40"],
            "35",
            35,
            [35],
            [""],
            ["abc"],
            ["-1"],
            ["35.0"],
            ["0.0"],
            [" 35 "],
            ["+35"],
            ["1e2"],
            ["NaN"],
            ["nan"],
            ["inf"],
            ["Infinity"],
        ],
    )
    def test_plate_value_grammar_fails_closed(self, value: object) -> None:
        data: dict[str, object] = dict(_BAMBU_PLA)
        data["cool_plate_temp"] = value
        adapter = _plate_adapter(data)
        resolved = PrintContextResolver(_settings(), adapter=adapter).resolve(
            PrintContextIntent(printer="Bambu Lab A1", build_plate="cool_plate")
        )

        matrix = FilamentMatrixBuilder(_settings(), adapter).build(resolved)

        assert matrix.candidates == []
        assert matrix.rejected[0].reason_code == "incompatible_build_plate"

    def test_missing_selected_plate_field_is_incompatible(self) -> None:
        data = dict(_BAMBU_PLA)
        del data["cool_plate_temp"]
        adapter = _plate_adapter(data)
        resolved = PrintContextResolver(_settings(), adapter=adapter).resolve(
            PrintContextIntent(printer="Bambu Lab A1", build_plate="cool_plate")
        )

        matrix = FilamentMatrixBuilder(_settings(), adapter).build(resolved)

        assert matrix.candidates == []
        assert matrix.rejected[0].reason_code == "incompatible_build_plate"

    def test_explicit_zero_decimal_text_is_incompatible(self) -> None:
        data = dict(_BAMBU_PLA)
        data["cool_plate_temp"] = ["0.0"]
        adapter = _plate_adapter(data)
        resolved = PrintContextResolver(_settings(), adapter=adapter).resolve(
            PrintContextIntent(printer="Bambu Lab A1", build_plate="cool_plate")
        )

        matrix = FilamentMatrixBuilder(_settings(), adapter).build(resolved)

        assert matrix.candidates == []
        assert matrix.rejected[0].reason_code == "incompatible_build_plate"

    def test_profile_name_does_not_infer_plate_compatibility(self) -> None:
        data = dict(_BAMBU_PLA)
        data["cool_plate_temp"] = ["0"]
        adapter = _plate_adapter(data, name="Bambu PLA Tough+ @base", material="PLA")
        resolved = PrintContextResolver(_settings(), adapter=adapter).resolve(
            PrintContextIntent(printer="Bambu Lab A1", build_plate="cool_plate")
        )

        matrix = FilamentMatrixBuilder(_settings(), adapter).build(resolved)

        assert matrix.candidates == []
        assert matrix.rejected[0].reason_code == "incompatible_build_plate"

    def test_extremely_long_valid_positive_plate_value_is_accepted(self) -> None:
        data = dict(_BAMBU_PLA)
        data["cool_plate_temp"] = ["1" + "0" * 5000]
        adapter = _plate_adapter(data)
        resolved = PrintContextResolver(_settings(), adapter=adapter).resolve(
            PrintContextIntent(printer="Bambu Lab A1", build_plate="cool_plate")
        )

        matrix = FilamentMatrixBuilder(_settings(), adapter).build(resolved)

        assert len(matrix.candidates) == 1

    def test_selected_build_plate_remains_context_authority(self) -> None:
        incompatible = dict(_BAMBU_PLA)
        incompatible["cool_plate_temp"] = ["0"]
        compatible_elsewhere = dict(_BAMBU_PLA)
        compatible_elsewhere["cool_plate_temp"] = ["0"]
        compatible_elsewhere["textured_plate_temp"] = ["45"]
        compatible_cool = dict(_BAMBU_PLA)
        adapter = _plate_adapter(
            incompatible, name="Bambu PLA Tough+ @base", material="PLA"
        )
        adapter.profiles[ProfileKind.FILAMENT].append(
            make_profile(ProfileKind.FILAMENT, "Neutral Material @base", compatible_elsewhere)
        )
        adapter.materialized["filament:Neutral Material @base"] = adapter.profiles[
            ProfileKind.FILAMENT
        ][-1]
        adapter.profiles[ProfileKind.FILAMENT].append(
            make_profile(ProfileKind.FILAMENT, "Cool Compatible @base", compatible_cool)
        )
        adapter.materialized["filament:Cool Compatible @base"] = adapter.profiles[
            ProfileKind.FILAMENT
        ][-1]

        result = _engine(adapter).recommend(
            SetupRequest(printer="Bambu Lab A1", build_plate="cool_plate")
        )

        assert result.context.build_plate == "cool_plate"
        assert result.matrix.build_plate == "cool_plate"
        assert result.filament is not None

    @pytest.mark.parametrize("value", [["35"], ["90"]])
    def test_positive_integer_plate_values_are_accepted(self, value: list[str]) -> None:
        data = dict(_BAMBU_PLA)
        data["cool_plate_temp"] = value
        adapter = _plate_adapter(data)
        resolved = PrintContextResolver(_settings(), adapter=adapter).resolve(
            PrintContextIntent(printer="Bambu Lab A1", build_plate="cool_plate")
        )

        matrix = FilamentMatrixBuilder(_settings(), adapter).build(resolved)

        assert len(matrix.candidates) == 1

    def test_materialization_failure_is_incompatible_build_plate(self) -> None:
        source = _plate_adapter(_BAMBU_PLA)
        adapter = _MissingMaterializedProfileAdapter(source.profiles, source.materialized)
        resolved = PrintContextResolver(_settings(), adapter=adapter).resolve(
            PrintContextIntent(printer="Bambu Lab A1", build_plate="cool_plate")
        )

        matrix = FilamentMatrixBuilder(_settings(), adapter).build(resolved)

        assert matrix.candidates == []
        assert matrix.rejected[0].reason_code == "incompatible_build_plate"

    def test_unparseable_materialized_document_is_incompatible_build_plate(self) -> None:
        source = _plate_adapter(_BAMBU_PLA)
        source.materialized["filament:Bambu PLA Basic @BBL A1"] = make_profile(
            ProfileKind.FILAMENT,
            "Bambu PLA Basic @BBL A1",
            "not json",
        )
        resolved = PrintContextResolver(_settings(), adapter=source).resolve(
            PrintContextIntent(printer="Bambu Lab A1", build_plate="cool_plate")
        )

        matrix = FilamentMatrixBuilder(_settings(), source).build(resolved)

        assert matrix.candidates == []
        assert matrix.rejected[0].reason_code == "incompatible_build_plate"

    def test_zero_is_rejected_before_ranking_and_positive_is_accepted(self) -> None:
        zero = dict(_BAMBU_PLA)
        zero["cool_plate_temp"] = ["0"]
        positive = dict(_BAMBU_PLA)
        positive["cool_plate_temp"] = ["35"]
        adapter = _plate_adapter(zero, name="Bambu ABS @base", material="ABS")
        adapter.profiles[ProfileKind.FILAMENT].append(
            make_profile(ProfileKind.FILAMENT, "Bambu PLA Tough+ @base", positive)
        )
        adapter.materialized["filament:Bambu PLA Tough+ @base"] = adapter.profiles[
            ProfileKind.FILAMENT
        ][-1]
        resolved = PrintContextResolver(_settings(), adapter=adapter).resolve(
            PrintContextIntent(printer="Bambu Lab A1", build_plate="cool_plate")
        )

        matrix = FilamentMatrixBuilder(_settings(), adapter).build(resolved)

        assert [candidate.profile_name for candidate in matrix.candidates] == [
            "Bambu PLA Tough+ @base"
        ]
        assert any(
            rejection.profile_name == "Bambu ABS @base"
            and rejection.reason_code == "incompatible_build_plate"
            for rejection in matrix.rejected
        )

    def test_explicit_abs_does_not_substitute_pla(self) -> None:
        abs_data = dict(_BAMBU_PLA)
        abs_data["filament_type"] = "ABS"
        abs_data["cool_plate_temp"] = ["0"]
        pla_data = dict(_BAMBU_PLA)
        adapter = _plate_adapter(abs_data, name="Bambu ABS @base", material="ABS")
        adapter.profiles[ProfileKind.FILAMENT].append(
            make_profile(ProfileKind.FILAMENT, "Bambu PLA Tough+ @base", pla_data)
        )
        adapter.materialized["filament:Bambu PLA Tough+ @base"] = adapter.profiles[
            ProfileKind.FILAMENT
        ][-1]
        resolved = PrintContextResolver(_settings(), adapter=adapter).resolve(
            PrintContextIntent(printer="Bambu Lab A1", build_plate="cool_plate")
        )

        matrix = FilamentMatrixBuilder(_settings(), adapter).build(resolved, material="ABS")

        assert matrix.candidates == []
        assert any(r.reason_code == "incompatible_build_plate" for r in matrix.rejected)
        assert not any(r.profile_name == "Bambu PLA Tough+ @base" for r in matrix.candidates)

    def test_explicit_abs_empty_selection_has_no_selectable_filament(self) -> None:
        abs_data = dict(_BAMBU_PLA)
        abs_data["filament_type"] = "ABS"
        abs_data["cool_plate_temp"] = ["0"]
        adapter = _plate_adapter(abs_data, name="Bambu ABS @base", material="ABS")
        result = _engine(adapter).recommend(
            SetupRequest(
                printer="Bambu Lab A1",
                build_plate="cool_plate",
                material="ABS",
            )
        )

        assert result.matrix.candidates == []
        assert result.material is None
        assert result.filament is None
        assert result.matrix.rejected
        assert all(
            rejection.reason_code == "incompatible_build_plate"
            for rejection in result.matrix.rejected
        )

    def test_explicit_pla_positive_plate_value_is_selectable(self) -> None:
        adapter = _plate_adapter(_BAMBU_PLA, material="PLA")
        result = _engine(adapter).recommend(
            SetupRequest(
                printer="Bambu Lab A1",
                build_plate="cool_plate",
                material="PLA",
            )
        )

        assert result.matrix.candidates
        assert result.material is not None
        assert result.filament is not None

    def test_compatibility_uses_materialized_document_once(self) -> None:
        adapter = _CountingProfileAdapter(_plate_adapter(_BAMBU_PLA))
        resolved = PrintContextResolver(_settings(), adapter=adapter).resolve(
            PrintContextIntent(printer="Bambu Lab A1", build_plate="cool_plate")
        )

        matrix = FilamentMatrixBuilder(_settings(), adapter).build(resolved)

        assert matrix.candidates
        assert adapter.filament_lookups == {"Bambu PLA Basic @BBL A1": 1}

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
    def test_authoritative_recommendation_resolves_once(self) -> None:
        adapter = _adapter()
        engine = _engine(adapter)
        resolver = engine._resolver
        calls = 0
        original = resolver.resolve_with_authority

        def counted(request: SetupRequest) -> Any:
            nonlocal calls
            calls += 1
            return original(request)

        cast(Any, resolver).resolve_with_authority = counted
        selected = engine.recommend_authoritative(SetupRequest(printer="Bambu Lab A1 0.4 nozzle"))
        assert calls == 1
        assert selected.recommendation.context is selected.context_authority.context

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
def test_authoritative_selection_type_is_internal() -> None:
    from dataclasses import is_dataclass

    from print_engineer.recommendation.setup import AuthoritativeSetupSelection

    assert is_dataclass(AuthoritativeSetupSelection)
