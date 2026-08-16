"""Robust subprocess runner for slicer CLIs.

Windows-specific notes:
- ``CREATE_NO_WINDOW`` prevents console flashes from the GUI slicers.
- Exit codes from crashed GUI apps are unreliable (a crash can still yield a
  ``0``/``0xc0000005``-style code), so callers must additionally inspect output
  artifacts and stdout/stderr rather than trusting ``returncode`` alone.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from print_engineer.errors import SlicerUnavailable

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass(frozen=True)
class ProcResult:
    """Captured process outcome."""

    stdout: str
    stderr: str
    return_code: int | None
    timed_out: bool
    killed: bool
    duration_seconds: float


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    """Terminate *proc* and any children (taskkill on Windows)."""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
            )
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    proc.kill()


def run_cli(
    command: Sequence[str | Path],
    *,
    timeout: float,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> ProcResult:
    """Run *command* with a hard timeout, killing the process tree if exceeded.

    Raises :class:`SlicerUnavailable` if the executable cannot be launched at
    all. A timed-out run is reported (not raised) so callers can inspect any
    partial artifacts before deciding on the error.
    """
    cmd = [str(part) for part in command]
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd) if cwd else None,
            env=dict(env) if env else None,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as exc:
        raise SlicerUnavailable(
            f"Could not launch slicer: {exc}",
            details={"command": cmd, "error": str(exc)},
        ) from exc

    timed_out = False
    killed = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_tree(proc)
        killed = True
        stdout, stderr = proc.communicate()

    return ProcResult(
        stdout=stdout or "",
        stderr=stderr or "",
        return_code=proc.returncode,
        timed_out=timed_out,
        killed=killed,
        duration_seconds=time.monotonic() - start,
    )
