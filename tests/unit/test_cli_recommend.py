"""End-to-end tests for the ``print-engineer recommend`` CLI command."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from tests.model_helpers import box_mesh, write_ascii_stl

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "print_engineer.cli", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_recommend_cube_deterministic(tmp_path: Path) -> None:
    path = write_ascii_stl(tmp_path / "cube.stl", box_mesh(20, 20, 20))
    completed = _run_cli("recommend", "--no-llm", str(path))

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    result = payload["recommendations"]
    assert result["goal"] == "balanced"
    assert result["mode"] == "deterministic"
    assert "No LLM reasoning was used" in result["summary"]


def test_recommend_cube_with_goal_flag(tmp_path: Path) -> None:
    path = write_ascii_stl(tmp_path / "cube.stl", box_mesh(20, 20, 20))
    completed = _run_cli("recommend", "--no-llm", "--goal", "strength", str(path))

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["recommendations"]["goal"] == "strength"


def test_recommend_missing_file(tmp_path: Path) -> None:
    completed = _run_cli("recommend", "--no-llm", str(tmp_path / "missing.stl"))

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_model"
    assert payload["error"]["details"]["reason"] == "not_found"


def test_recommend_invalid_goal(tmp_path: Path) -> None:
    path = write_ascii_stl(tmp_path / "cube.stl", box_mesh(20, 20, 20))
    completed = _run_cli("recommend", "--no-llm", "--goal", "cheap", str(path))

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "slicer_error"
    assert "invalid recommendation request" in payload["error"]["message"]


@pytest.mark.parametrize("flag", ["--no-llm", "--slice"])
def test_recommend_flags_accepted(tmp_path: Path, flag: str) -> None:
    path = write_ascii_stl(tmp_path / "cube.stl", box_mesh(20, 20, 20))
    completed = _run_cli("recommend", flag, str(path))

    assert completed.returncode in (0, 1)
    payload = json.loads(completed.stdout)
    assert "ok" in payload
