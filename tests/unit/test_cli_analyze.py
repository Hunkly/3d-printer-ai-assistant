"""End-to-end tests for the ``print-engineer analyze`` CLI command."""

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


def test_analyze_cube(tmp_path: Path) -> None:
    path = write_ascii_stl(tmp_path / "cube.stl", box_mesh(20, 20, 20))
    completed = _run_cli("analyze", str(path))

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["analysis"]["volume_mm3"] == pytest.approx(8000.0)
    assert payload["analysis"]["dimensions_mm"] == pytest.approx([20, 20, 20])


def test_analyze_cube_with_threshold_flag(tmp_path: Path) -> None:
    path = write_ascii_stl(tmp_path / "cube.stl", box_mesh(20, 20, 20))
    completed = _run_cli("analyze", str(path), "--overhang-threshold", "0")

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["analysis"]["overhang"]["area_mm2"] == pytest.approx(400.0)


def test_analyze_missing_file(tmp_path: Path) -> None:
    completed = _run_cli("analyze", str(tmp_path / "missing.stl"))

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_model"
    assert payload["error"]["details"]["reason"] == "not_found"


def test_analyze_unsupported_suffix(tmp_path: Path) -> None:
    path = tmp_path / "model.obj"
    path.write_text("o object", encoding="utf-8")
    completed = _run_cli("analyze", str(path))

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["error"]["details"]["reason"] == "unsupported_suffix"
