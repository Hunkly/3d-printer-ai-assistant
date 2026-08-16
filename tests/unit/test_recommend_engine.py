"""Engine orchestration tests (Phase 3A)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from tests.model_helpers import box_mesh, write_ascii_stl

from print_engineer.adapters.slicer.registry import SlicerRegistry
from print_engineer.config import Settings
from print_engineer.core.interfaces.llm import LLMClient
from print_engineer.core.recommendation import (
    ChangeDirection,
    RecommendationGoal,
    RecommendationMode,
    RecommendationRequest,
    RecommendationSource,
    SliceStatistics,
)
from print_engineer.core.types import ProfileInfo, ProfileKind, SlicerKind
from print_engineer.errors import InvalidProfile, LLMInvalidResponse, LLMUnavailable, SlicerError
from print_engineer.recommendation.engine import RecommendationEngine, _ProfileTriple


class _FakeLLM(LLMClient):
    """Return a canned LLMRecommendationSet dict (or raise)."""

    def __init__(self, payload: dict[str, Any] | Exception) -> None:
        self._payload = payload
        self.prompts: list[str] = []

    def complete_json(
        self, prompt: str, *, timeout_seconds: float | None = None
    ) -> dict[str, Any]:
        self.prompts.append(prompt)
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _process_profile(**overrides: object) -> ProfileInfo:
    data: dict[str, object] = {
        "layer_height": "0.2",
        "wall_loops": "2",
        "sparse_infill_density": "15%",
        "sparse_infill_pattern": "grid",
        "enable_support": "0",
        "outer_wall_speed": "200",
    }
    data.update(overrides)
    return ProfileInfo(
        name="process-profile",
        kind=ProfileKind.PROCESS,
        path=Path("process.json"),
        content=json.dumps(data),
        materialized=True,
    )


class _StubbedEngine(RecommendationEngine):
    """Engine with a fixed slicer triple (avoids real slicer detection)."""

    def __init__(
        self,
        settings: Settings,
        *,
        llm: LLMClient | None = None,
        process: ProfileInfo | None = None,
        stats: SliceStatistics | None = None,
    ) -> None:
        super().__init__(settings, llm=llm)
        self._proc = process
        self._stats = stats

    def _resolve_slicer(self, request: RecommendationRequest) -> tuple[_ProfileTriple, list[str]]:
        try:
            kind = SlicerKind(request.slicer_kind)
        except ValueError as exc:
            raise SlicerError(
                f"Unknown slicer {request.slicer_kind!r}",
                details={"slicer_kind": request.slicer_kind},
            ) from exc
        return _ProfileTriple(self._proc, None, None, kind), []

    def _obtain_stats(
        self, request: RecommendationRequest, triple: _ProfileTriple
    ) -> SliceStatistics | None:
        return self._stats


class _NoProfilesEngine(RecommendationEngine):
    """Engine whose slicer resolution always returns no profiles."""

    def _resolve_slicer(self, request: RecommendationRequest) -> tuple[_ProfileTriple, list[str]]:
        return _ProfileTriple(None, None, None, SlicerKind.ORCA_SLICER), []


def _write_model(tmp_path: Path) -> Path:
    return write_ascii_stl(tmp_path / "cube.stl", box_mesh(20, 20, 20))


def _request(tmp_path: Path, **overrides: object) -> RecommendationRequest:
    params: dict[str, object] = {"model_path": _write_model(tmp_path)}
    params.update(overrides)
    return RecommendationRequest(**params)  # type: ignore[arg-type]


class TestDeterministicPath:
    def test_llm_disabled_returns_deterministic_set(
        self, tmp_path: Path, base_settings: Settings
    ) -> None:
        engine = _StubbedEngine(base_settings, llm=None, process=_process_profile())
        result = engine.recommend(_request(tmp_path))
        assert result.mode == RecommendationMode.DETERMINISTIC
        assert all(r.source == RecommendationSource.DETERMINISTIC for r in result.recommendations)
        assert "No LLM reasoning was used" in result.summary

    def test_deterministic_recs_fired(self, tmp_path: Path, base_settings: Settings) -> None:
        engine = _StubbedEngine(base_settings, llm=None, process=_process_profile())
        result = engine.recommend(_request(tmp_path, goal=RecommendationGoal.STRENGTH))
        settings_present = {r.setting for r in result.recommendations}
        assert "support_enablement" in settings_present
        assert "wall_loops" in settings_present
        support = next(r for r in result.recommendations if r.setting == "support_enablement")
        assert support.change == ChangeDirection.ENABLE
        assert support.confidence is None


class TestLLMMerge:
    def test_candidates_preserved_and_llm_additions_merged(
        self, tmp_path: Path, base_settings: Settings
    ) -> None:
        payload = {
            "goal": "strength",
            "summary": "prefer walls and finer layers",
            "warnings": [],
            "recommendations": [
                {
                    "setting": "layer_height_mm",
                    "change": "decrease",
                    "current_value": 0.2,
                    "recommended_value": 0.16,
                    "reason": "finer layers strengthen thin walls",
                    "expected_benefit": "stronger parts",
                    "tradeoff": "slower",
                    "confidence": 0.8,
                    "evidence": ["Current layer height = 0.20 mm"],
                }
            ],
        }
        fake = _FakeLLM(payload)
        engine = _StubbedEngine(base_settings, llm=fake, process=_process_profile())
        result = engine.recommend(_request(tmp_path, goal=RecommendationGoal.STRENGTH))

        assert result.mode == RecommendationMode.LLM
        by = {r.setting: r for r in result.recommendations}
        assert by["support_enablement"].source == RecommendationSource.DETERMINISTIC
        assert by["wall_loops"].source == RecommendationSource.DETERMINISTIC
        assert by["sparse_infill_percent"].source == RecommendationSource.DETERMINISTIC
        layer = by["layer_height_mm"]
        assert layer.source == RecommendationSource.LLM
        assert layer.confidence == 0.8
        assert layer.recommended_value == 0.16
        assert fake.prompts  # LLM was actually consulted

    def test_wrong_goal_rejected(self, tmp_path: Path, base_settings: Settings) -> None:
        base_settings.recommend.allow_deterministic_fallback = False
        payload = {
            "goal": "print_time",
            "summary": "s",
            "recommendations": [],
        }
        fake = _FakeLLM(payload)
        engine = _StubbedEngine(base_settings, llm=fake, process=_process_profile())
        with pytest.raises(LLMInvalidResponse):
            engine.recommend(_request(tmp_path, goal=RecommendationGoal.STRENGTH))

    def test_disallowed_setting_rejected(self, tmp_path: Path, base_settings: Settings) -> None:
        base_settings.recommend.allow_deterministic_fallback = False
        payload = {
            "goal": "balanced",
            "summary": "s",
            "recommendations": [
                {
                    "setting": "nozzle_temperature",
                    "change": "increase",
                    "current_value": 200,
                    "recommended_value": 210,
                    "reason": "hotter",
                    "evidence": ["Model volume = 8000.0 mm3"],
                }
            ],
        }
        fake = _FakeLLM(payload)
        engine = _StubbedEngine(base_settings, llm=fake, process=_process_profile())
        with pytest.raises(LLMInvalidResponse) as excinfo:
            engine.recommend(_request(tmp_path))
        assert "disallowed setting" in str(excinfo.value)

    def test_fabricated_value_rejected(self, tmp_path: Path, base_settings: Settings) -> None:
        base_settings.recommend.allow_deterministic_fallback = False
        payload = {
            "goal": "balanced",
            "summary": "s",
            "recommendations": [
                {
                    "setting": "layer_height_mm",
                    "change": "decrease",
                    "current_value": 0.2,
                    "recommended_value": 0.16,
                    "reason": "nicer",
                    "evidence": ["Model volume = 8000.0 mm3"],
                }
            ],
        }
        fake = _FakeLLM(payload)
        engine = _StubbedEngine(base_settings, llm=fake, process=None)
        with pytest.raises(LLMInvalidResponse) as excinfo:
            engine.recommend(_request(tmp_path))
        assert "fabricated a current value" in str(excinfo.value)

    def test_ungrounded_evidence_rejected(self, tmp_path: Path, base_settings: Settings) -> None:
        base_settings.recommend.allow_deterministic_fallback = False
        payload = {
            "goal": "balanced",
            "summary": "s",
            "recommendations": [
                {
                    "setting": "layer_height_mm",
                    "change": "decrease",
                    "current_value": 0.2,
                    "recommended_value": 0.16,
                    "reason": "nicer",
                    "evidence": ["Current layer height = 9.99 mm"],
                }
            ],
        }
        fake = _FakeLLM(payload)
        engine = _StubbedEngine(base_settings, llm=fake, process=_process_profile())
        with pytest.raises(LLMInvalidResponse) as excinfo:
            engine.recommend(_request(tmp_path))
        assert "evidence" in str(excinfo.value).lower()


class TestLLMFallback:
    def test_fallback_to_deterministic_with_warning(
        self, tmp_path: Path, base_settings: Settings
    ) -> None:
        base_settings.recommend.allow_deterministic_fallback = True
        fake = _FakeLLM(LLMUnavailable("offline"))
        engine = _StubbedEngine(base_settings, llm=fake, process=_process_profile())
        result = engine.recommend(_request(tmp_path, goal=RecommendationGoal.STRENGTH))

        assert result.mode == RecommendationMode.DETERMINISTIC
        assert any("LLM reasoning unavailable" in w for w in result.warnings)
        assert any("no LLM reasoning was used" in w for w in result.warnings)

    def test_no_fallback_raises(self, tmp_path: Path, base_settings: Settings) -> None:
        base_settings.recommend.allow_deterministic_fallback = False
        fake = _FakeLLM(LLMUnavailable("offline"))
        engine = _StubbedEngine(base_settings, llm=fake, process=_process_profile())
        with pytest.raises(LLMUnavailable):
            engine.recommend(_request(tmp_path))


class TestSliceOnDemand:
    def test_missing_profiles_raises_invalid_profile(
        self, tmp_path: Path, base_settings: Settings
    ) -> None:
        engine = _NoProfilesEngine(base_settings, llm=None)
        with pytest.raises(InvalidProfile):
            engine.recommend(_request(tmp_path, slice_on_demand=True))

    def test_stats_flow_into_constraints(
        self, tmp_path: Path, base_settings: Settings
    ) -> None:
        stats = SliceStatistics(
            available=True, estimated_time_minutes=300.0, filament_weight_g=80.0
        )
        engine = _StubbedEngine(
            base_settings,
            llm=None,
            process=_process_profile(sparse_infill_density="40%", wall_loops="4"),
            stats=stats,
        )
        result = engine.recommend(_request(tmp_path, max_time_minutes=200.0))
        by = {r.setting: r for r in result.recommendations}
        assert by["layer_height_mm"].change == ChangeDirection.INCREASE
        assert by["sparse_infill_percent"].recommended_value == 15.0


class TestProfileResolution:
    def test_unknown_printer_profile_raises(self, tmp_path: Path, base_settings: Settings) -> None:
        class _FakeAdapter:
            def find_profile(self, profile_kind: ProfileKind, name: str) -> ProfileInfo | None:
                return None

        class _FakeRegistry:
            def get(self, kind: SlicerKind) -> _FakeAdapter:
                return _FakeAdapter()

        engine = RecommendationEngine(
            base_settings, llm=None, registry=cast(SlicerRegistry, _FakeRegistry())
        )
        with pytest.raises(InvalidProfile) as excinfo:
            engine.recommend(_request(tmp_path, printer_profile="bogus-printer"))
        assert "Unknown printer profile" in str(excinfo.value)

    def test_unknown_slicer_kind_raises(self, tmp_path: Path, base_settings: Settings) -> None:
        engine = _StubbedEngine(base_settings, llm=None)
        with pytest.raises(SlicerError) as excinfo:
            engine.recommend(_request(tmp_path, slicer_kind="not_a_slicer"))
        assert excinfo.value.code == "slicer_error"
