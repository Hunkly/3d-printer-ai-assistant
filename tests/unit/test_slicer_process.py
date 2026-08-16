"""Tests for the hard-timeout subprocess runner."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from print_engineer.adapters.slicer.process import ProcResult, run_cli
from print_engineer.errors import SlicerUnavailable


def test_run_cli_captures_stdout(tmp_path: Path) -> None:
    result = run_cli(
        [sys.executable, "-c", "print('hello slicer')"], timeout=30, cwd=tmp_path
    )
    assert result.return_code == 0
    assert result.stdout.strip() == "hello slicer"
    assert not result.timed_out
    assert not result.killed


def test_run_cli_captures_stderr(tmp_path: Path) -> None:
    result = run_cli(
        [sys.executable, "-c", "import sys; print('boom', file=sys.stderr); sys.exit(3)"],
        timeout=30,
        cwd=tmp_path,
    )
    assert result.return_code == 3
    assert "boom" in result.stderr


def test_run_cli_enforces_timeout_and_kills(tmp_path: Path) -> None:
    start = __import__("time").monotonic()
    result = run_cli(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        timeout=1.0,
        cwd=tmp_path,
    )
    elapsed = __import__("time").monotonic() - start
    assert result.timed_out is True
    assert result.killed is True
    assert elapsed < 30


def test_run_cli_missing_executable_raises(tmp_path: Path) -> None:
    with pytest.raises(SlicerUnavailable):
        run_cli([str(tmp_path / "does-not-exist.exe"), "--info"], timeout=5)


def test_proc_result_is_a_dataclass() -> None:
    result = ProcResult(
        stdout="a",
        stderr="b",
        return_code=0,
        timed_out=False,
        killed=False,
        duration_seconds=0.1,
    )
    assert result.stdout == "a"
    assert result.return_code == 0
