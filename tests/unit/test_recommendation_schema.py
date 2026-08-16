"""Schema tests for the recommendation domain types (Phase 3A)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from print_engineer.core.recommendation import (
    RECOMMENDABLE_SETTINGS,
    ChangeDirection,
    LLMRecommendation,
    LLMRecommendationSet,
    RecommendationGoal,
    RecommendationMode,
    RecommendationRequest,
    RecommendationSet,
    category_for_setting,
)


class TestCategoryForSetting:
    def test_every_allowlisted_setting_maps_to_a_category(self) -> None:
        for setting in RECOMMENDABLE_SETTINGS:
            assert category_for_setting(setting) is not None

    def test_unknown_setting_returns_none(self) -> None:
        assert category_for_setting("nozzle_temperature") is None
        assert category_for_setting("flow_ratio") is None
        assert category_for_setting("bed_temperature") is None


class TestRecommendationRequest:
    def test_defaults(self) -> None:
        request = RecommendationRequest(model_path=Path("part.stl"))
        assert request.slicer_kind == "orca_slicer"
        assert request.goal == RecommendationGoal.BALANCED
        assert request.overhang_threshold_degrees == 45.0
        assert request.slice_on_demand is False
        assert request.process_profile is None

    def test_invalid_goal_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RecommendationRequest(
                model_path=Path("part.stl"),
                goal=cast(RecommendationGoal, "cheap"),
            )

    def test_negative_constraint_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RecommendationRequest(model_path=Path("part.stl"), max_time_minutes=-1.0)


class TestRecommendation:
    def test_confidence_bounds(self) -> None:
        def rec(confidence: float) -> LLMRecommendation:
            return LLMRecommendation(
                setting="layer_height_mm",
                change=ChangeDirection.DECREASE,
                reason="r",
                confidence=confidence,
            )

        with pytest.raises(ValidationError):
            rec(1.5)
        with pytest.raises(ValidationError):
            rec(-0.1)
        assert rec(0.0).confidence == 0.0

    def test_source_defaults_to_deterministic(self) -> None:
        set_rec = LLMRecommendationSet(
            goal=RecommendationGoal.BALANCED,
            summary="s",
            recommendations=[
                LLMRecommendation(
                    setting="wall_loops", change=ChangeDirection.INCREASE, reason="r"
                )
            ],
        )
        assert set_rec.recommendations[0].change == ChangeDirection.INCREASE


class TestLLMRecommendationSet:
    def test_change_must_be_valid_enum(self) -> None:
        payload: dict[str, object] = {
            "goal": "balanced",
            "summary": "s",
            "recommendations": [
                {"setting": "wall_loops", "change": "sideways", "reason": "r"}
            ],
        }
        with pytest.raises(ValidationError):
            LLMRecommendationSet.model_validate(payload)

    def test_invalid_goal_rejected(self) -> None:
        payload: dict[str, object] = {
            "goal": "faster",
            "summary": "s",
            "recommendations": [
                {"setting": "wall_loops", "change": "increase", "reason": "r"}
            ],
        }
        with pytest.raises(ValidationError):
            LLMRecommendationSet.model_validate(payload)

    def test_empty_recommendations_allowed(self) -> None:
        payload = {"goal": "balanced", "summary": "no suggestions", "recommendations": []}
        llm_set = LLMRecommendationSet.model_validate(payload)
        assert llm_set.recommendations == []
        assert llm_set.warnings == []


class TestRecommendationSet:
    def test_output_shape(self) -> None:
        result = RecommendationSet(
            goal=RecommendationGoal.BALANCED,
            recommendations=[],
            summary="nothing to suggest",
            warnings=["note"],
            mode=RecommendationMode.DETERMINISTIC,
        )
        data = result.model_dump(mode="json")
        assert data["goal"] == "balanced"
        assert data["mode"] == "deterministic"
        assert data["warnings"] == ["note"]
