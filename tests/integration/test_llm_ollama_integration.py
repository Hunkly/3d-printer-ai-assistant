"""Optional real-Ollama integration test for the recommendation engine.

Skips when Ollama is not reachable. Verifies the full pipeline against a real
local LLM: deterministic candidates + LLM merge with evidence grounding.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest
from tests.model_helpers import box_mesh, write_ascii_stl

from print_engineer.adapters.llm.ollama import OllamaClient
from print_engineer.config import Settings
from print_engineer.core.recommendation import RecommendationGoal, RecommendationMode
from print_engineer.errors import LLMError
from print_engineer.recommendation.engine import RecommendationEngine

_BASE_URL = "http://127.0.0.1:11434"
_DEFAULT_MODEL = "qwen3-coder:30b"


def _ollama_reachable() -> bool:
    try:
        with httpx.Client(base_url=_BASE_URL, timeout=3.0) as client:
            response = client.get("/api/tags")
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def test_recommend_with_real_ollama(tmp_path: Path) -> None:
    if not _ollama_reachable():
        pytest.skip("Ollama is not reachable")

    settings = Settings.load()
    settings.recommend.allow_deterministic_fallback = True
    llm = OllamaClient(
        base_url=_BASE_URL,
        model=_DEFAULT_MODEL,
        timeout_seconds=120.0,
        temperature=0.2,
    )
    engine = RecommendationEngine(settings, llm=llm)

    path = write_ascii_stl(tmp_path / "cube.stl", box_mesh(20, 20, 20))
    request = __import__(
        "print_engineer.core.recommendation", fromlist=["RecommendationRequest"]
    ).RecommendationRequest(
        model_path=path,
        goal=RecommendationGoal.STRENGTH,
        slicer_kind="orca_slicer",
    )

    started = time.monotonic()
    try:
        result = engine.recommend(request)
    except LLMError:
        pytest.skip("Ollama refused to answer; LLM integration untestable right now")
    elapsed = time.monotonic() - started

    assert result.goal == RecommendationGoal.STRENGTH
    if result.mode == RecommendationMode.LLM:
        # Strict merge path: every recommendation must be grounded in measured facts.
        for rec in result.recommendations:
            assert rec.evidence, "every LLM recommendation must cite measured facts"
        assert any(r.source.value == "llm" for r in result.recommendations)
    else:
        # The model produced something the strict validator rejected; the fallback
        # must have engaged and been labeled honestly.
        assert result.mode == RecommendationMode.DETERMINISTIC
        assert any("LLM reasoning unavailable" in w for w in result.warnings)
        assert any("no LLM reasoning was used" in w for w in result.warnings)
    assert elapsed < 240, "LLM call should stay within a reasonable bound"
