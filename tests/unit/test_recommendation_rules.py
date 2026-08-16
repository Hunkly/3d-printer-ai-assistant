"""Deterministic rule tests (Phase 3A)."""

from __future__ import annotations

from print_engineer.core.recommendation import (
    ChangeDirection,
    ModelFacts,
    ProcessSettings,
    Recommendation,
    RecommendationGoal,
    RecommendationInput,
    RecommendationSource,
    SlicerSettingsDigest,
    SliceStatistics,
)
from print_engineer.recommendation.rules import _next_higher, _next_lower, evaluate


def _digest(process: ProcessSettings | None = None) -> SlicerSettingsDigest:
    return SlicerSettingsDigest(slicer_kind="orca_slicer", process=process)


def _process(
    *,
    layer_height_mm: float | None = 0.2,
    wall_loops: int | None = 2,
    sparse_infill_percent: float | None = 15.0,
    sparse_infill_pattern: str | None = "grid",
    enable_support: bool | None = False,
    outer_wall_speed_mms: float | None = 200.0,
) -> ProcessSettings:
    return ProcessSettings(
        name="test",
        layer_height_mm=layer_height_mm,
        wall_loops=wall_loops,
        sparse_infill_percent=sparse_infill_percent,
        sparse_infill_pattern=sparse_infill_pattern,
        enable_support=enable_support,
        outer_wall_speed_mms=outer_wall_speed_mms,
    )


def _input(**overrides: object) -> RecommendationInput:
    defaults: dict[str, object] = {
        "goal": RecommendationGoal.BALANCED,
        "model": ModelFacts(),
        "slicer": _digest(),
    }
    defaults.update(overrides)
    return RecommendationInput(**defaults)  # type: ignore[arg-type]


def _by_setting(recs: list[Recommendation]) -> dict[str, Recommendation]:
    return {r.setting: r for r in recs}


class TestRungs:
    def test_next_lower(self) -> None:
        assert _next_lower(0.20, (0.08, 0.12, 0.16, 0.20, 0.24, 0.28)) == 0.16
        assert _next_lower(0.08, (0.08, 0.12, 0.16, 0.20, 0.24, 0.28)) is None

    def test_next_higher(self) -> None:
        assert _next_higher(0.16, (0.08, 0.12, 0.16, 0.20, 0.24, 0.28)) == 0.20
        assert _next_higher(0.28, (0.08, 0.12, 0.16, 0.20, 0.24, 0.28)) is None


class TestOverhangRule:
    def test_overhang_enables_support_when_disabled(self) -> None:
        model = ModelFacts(overhang_area_percent=16.7, overhang_face_count=2)
        recs, warnings = evaluate(_input(model=model, slicer=_digest(_process())))
        support = _by_setting(recs)["support_enablement"]
        assert support.change == ChangeDirection.ENABLE
        assert support.current_value == "off"
        assert support.recommended_value == "on"
        assert support.source == RecommendationSource.DETERMINISTIC
        assert support.confidence is None
        assert warnings == []

    def test_no_support_recommendation_below_threshold(self) -> None:
        model = ModelFacts(overhang_area_percent=2.0)
        recs, _ = evaluate(_input(model=model, slicer=_digest(_process())))
        assert "support_enablement" not in _by_setting(recs)

    def test_no_support_when_already_enabled(self) -> None:
        model = ModelFacts(overhang_area_percent=40.0)
        process = _process(enable_support=True)
        recs, _ = evaluate(_input(model=model, slicer=_digest(process)))
        assert "support_enablement" not in _by_setting(recs)

    def test_skipped_without_process_settings(self) -> None:
        model = ModelFacts(overhang_area_percent=40.0)
        recs, warnings = evaluate(_input(model=model, slicer=None))
        assert "support_enablement" not in _by_setting(recs)
        assert any("no current process settings" in w for w in warnings)


class TestThinWallRule:
    def test_thin_wall_decreases_layer_height(self) -> None:
        model = ModelFacts(thin_wall_supported=True, thin_wall_min_mm=0.4)
        process = _process(layer_height_mm=0.2)
        recs, _ = evaluate(_input(model=model, slicer=_digest(process)))
        layer = _by_setting(recs)["layer_height_mm"]
        assert layer.change == ChangeDirection.DECREASE
        assert layer.current_value == 0.2
        assert layer.recommended_value == 0.16

    def test_thick_wall_no_recommendation(self) -> None:
        model = ModelFacts(thin_wall_supported=True, thin_wall_min_mm=1.5)
        process = _process(layer_height_mm=0.2)
        recs, _ = evaluate(_input(model=model, slicer=_digest(process)))
        assert "layer_height_mm" not in _by_setting(recs)


class TestGoalRules:
    def test_strength_increases_walls_and_infill(self) -> None:
        process = _process(wall_loops=2, sparse_infill_percent=15.0, sparse_infill_pattern="grid")
        recs, _ = evaluate(
            _input(goal=RecommendationGoal.STRENGTH, slicer=_digest(process))
        )
        by = _by_setting(recs)
        assert by["wall_loops"].change == ChangeDirection.INCREASE
        assert by["wall_loops"].recommended_value == 3
        assert by["sparse_infill_percent"].change == ChangeDirection.INCREASE
        assert by["sparse_infill_percent"].recommended_value == 25.0
        assert by["sparse_infill_pattern"].change == ChangeDirection.SET
        assert by["sparse_infill_pattern"].recommended_value == "gyroid"

    def test_surface_quality_decreases_layer_height_and_speed(self) -> None:
        process = _process(layer_height_mm=0.2, outer_wall_speed_mms=200.0)
        recs, _ = evaluate(
            _input(goal=RecommendationGoal.SURFACE_QUALITY, slicer=_digest(process))
        )
        by = _by_setting(recs)
        assert by["layer_height_mm"].change == ChangeDirection.DECREASE
        assert by["outer_wall_speed_mms"].recommended_value == 120.0

    def test_print_time_increases_layer_height(self) -> None:
        process = _process(layer_height_mm=0.2, sparse_infill_percent=40.0, wall_loops=4)
        recs, _ = evaluate(_input(goal=RecommendationGoal.PRINT_TIME, slicer=_digest(process)))
        by = _by_setting(recs)
        assert by["layer_height_mm"].change == ChangeDirection.INCREASE
        assert by["sparse_infill_percent"].recommended_value == 15.0
        assert by["wall_loops"].recommended_value == 2

    def test_filament_usage_lowers_infill(self) -> None:
        process = _process(sparse_infill_percent=30.0, wall_loops=3)
        recs, _ = evaluate(
            _input(goal=RecommendationGoal.FILAMENT_USAGE, slicer=_digest(process))
        )
        by = _by_setting(recs)
        assert by["sparse_infill_percent"].recommended_value == 10.0
        assert by["wall_loops"].recommended_value == 2


class TestConstraintRules:
    def test_max_time_increases_layer_height(self) -> None:
        process = _process(layer_height_mm=0.2, wall_loops=4, sparse_infill_percent=40.0)
        stats = SliceStatistics(
            available=True,
            estimated_time_minutes=300.0,
            filament_weight_g=50.0,
        )
        recs, _ = evaluate(
            _input(
                slicer=_digest(process),
                slice_stats=stats,
                max_time_minutes=200.0,
            )
        )
        by = _by_setting(recs)
        assert by["layer_height_mm"].change == ChangeDirection.INCREASE
        assert by["wall_loops"].change == ChangeDirection.DECREASE
        assert by["sparse_infill_percent"].recommended_value == 15.0

    def test_max_time_warns_without_measured_stats(self) -> None:
        recs, warnings = evaluate(_input(max_time_minutes=200.0, slicer=_digest(_process())))
        assert recs == []
        assert any("measured print time is unavailable" in w for w in warnings)

    def test_max_filament_lowers_infill(self) -> None:
        process = _process(sparse_infill_percent=30.0, wall_loops=3)
        stats = SliceStatistics(available=True, filament_weight_g=80.0)
        recs, _ = evaluate(
            _input(
                slicer=_digest(process),
                slice_stats=stats,
                max_filament_g=50.0,
            )
        )
        by = _by_setting(recs)
        assert by["sparse_infill_percent"].recommended_value == 10.0
        assert by["wall_loops"].recommended_value == 2


class TestPrecedence:
    def test_geometry_wins_over_goal_conflict(self) -> None:
        model = ModelFacts(overhang_area_percent=30.0)
        process = _process(enable_support=False)
        # PRINT_TIME goal wants wall_loops decrease; only overhang + goal rules fire here.
        recs, warnings = evaluate(
            _input(
                goal=RecommendationGoal.PRINT_TIME,
                model=model,
                slicer=_digest(process),
            )
        )
        by = _by_setting(recs)
        assert by["support_enablement"].change == ChangeDirection.ENABLE

    def test_geometry_warning_for_multiple_components(self) -> None:
        model = ModelFacts(component_count=3)
        _, warnings = evaluate(_input(model=model))
        assert any("3 separate components" in w for w in warnings)

    def test_geometry_rule_beats_constraint_on_same_setting(self) -> None:
        model = ModelFacts(thin_wall_supported=True, thin_wall_min_mm=0.4)
        process = _process(layer_height_mm=0.2)
        stats = SliceStatistics(available=True, estimated_time_minutes=300.0)
        recs, warnings = evaluate(
            _input(
                model=model,
                slicer=_digest(process),
                slice_stats=stats,
                max_time_minutes=200.0,
            )
        )
        by = _by_setting(recs)
        assert by["layer_height_mm"].change == ChangeDirection.DECREASE
        assert any("conflicting recommendations for layer_height_mm" in w for w in warnings)


class TestEvaluator:
    def test_balanced_goal_no_recs_on_clean_box(self) -> None:
        recs, warnings = evaluate(
            _input(
                model=ModelFacts(
                    watertight=True,
                    overhang_area_percent=0.0,
                    component_count=1,
                ),
                slicer=_digest(_process(outer_wall_speed_mms=120.0)),
            )
        )
        assert recs == []
        assert warnings == []

    def test_all_recs_are_deterministic_with_null_confidence(self) -> None:
        model = ModelFacts(overhang_area_percent=20.0)
        process = _process(wall_loops=2, sparse_infill_percent=15.0)
        recs, _ = evaluate(
            _input(goal=RecommendationGoal.STRENGTH, model=model, slicer=_digest(process))
        )
        assert recs
        for rec in recs:
            assert rec.source == RecommendationSource.DETERMINISTIC
            assert rec.confidence is None
