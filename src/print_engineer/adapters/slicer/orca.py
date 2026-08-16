"""OrcaSlicer adapter: the verified working slicer for Phase 1.

Verified CLI invocation (OrcaSlicer 2.3.2):

    orca-slicer.exe <model> --load-settings process.json;machine.json \\
        --load-filaments filament.json --slice 1 \\
        --export-3mf <bare-name>.gcode.3mf --outputdir <dir>

- ``--export-3mf`` must be a bare filename resolved against ``--outputdir``.
- ``--outputdir`` must already exist.
- Profiles are materialized into the job directory and passed by bare name
  with the subprocess working directory set to that directory.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from print_engineer.adapters.slicer.base import BaseSlicerAdapter
from print_engineer.adapters.slicer.gcode import parse_gcode, parse_gcode_3mf
from print_engineer.adapters.slicer.process import run_cli
from print_engineer.adapters.slicer.version import detect_orca_version
from print_engineer.core.types import SliceJob, SliceResult, SlicerKind
from print_engineer.errors import SlicerUnavailable, SliceTimeout


class OrcaSlicerAdapter(BaseSlicerAdapter):
    kind = SlicerKind.ORCA_SLICER

    install_paths = (Path(r"C:\Program Files\OrcaSlicer\orca-slicer.exe"),)
    binaries = ("orca-slicer.exe", "OrcaSlicer.exe")
    appdata_dirname = "OrcaSlicer"

    def _detect_version(self, exe: Path) -> tuple[str | None, str | None]:
        return detect_orca_version(exe.parent)

    def _build_slice_command(
        self,
        exe: Path,
        model_path: Path,
        files: dict[str, Path],
        export_name: str,
        job_dir: Path,
    ) -> list[str]:
        return [
            str(exe),
            str(model_path),
            "--load-settings",
            f"{files['process'].name};{files['printer'].name}",
            "--load-filaments",
            files["filament"].name,
            "--slice",
            "1",
            "--export-3mf",
            export_name,
            "--outputdir",
            str(job_dir),
        ]

    def _find_slice_artifacts(self, job_dir: Path) -> tuple[Path | None, Path | None]:
        gcode = next(iter(sorted(job_dir.glob("plate_*.gcode"))), None)
        three_mf = next(iter(sorted(job_dir.glob("*.gcode.3mf"))), None)
        return gcode, three_mf

    def slice(self, job: SliceJob) -> SliceResult:
        info = self._require_detected()
        if not info.slicing_supported:
            raise SlicerUnavailable(
                f"{self._adapter_name} {info.version or 'unknown'} cannot slice on this machine",
                details={
                    "slicer_kind": self.kind.value,
                    "version": info.version,
                    "reason": "unsupported_version",
                },
            )

        self._validate_model_locally(job.model_path)
        self._check_project_version(job.model_path, info.version)

        process, filament, machine = self._prepare_profiles(job)
        job_dir = self._job_dir()
        files = self._write_job_files(
            job_dir,
            {"process": process, "filament": filament, "printer": machine},
        )
        export_name = self._export_name(job)

        command = self._build_slice_command(
            info.executable, job.model_path, files, export_name, job_dir
        )
        timeout = self._timeout_for(job)
        result = run_cli(command, timeout=timeout, cwd=job_dir)

        if result.timed_out:
            raise SliceTimeout(
                f"{self._adapter_name} exceeded the {timeout:.0f}s slice timeout",
                details={
                    "slicer_kind": self.kind.value,
                    "timeout_seconds": timeout,
                    "output_dir": str(job_dir),
                    "command": command,
                },
            )

        gcode, three_mf = self._find_slice_artifacts(job_dir)
        if gcode is None and three_mf is None:
            error = self._error_from_stderr(result.stderr, result.stdout)
            error.details["return_code"] = result.return_code
            error.details["output_dir"] = str(job_dir)
            raise error

        stats = parse_gcode(gcode) if gcode else {}
        meta = parse_gcode_3mf(three_mf) if three_mf else {}

        notes: list[str] = [
            f"sliced by {self._adapter_name} {info.version or 'unknown'} "
            f"in {result.duration_seconds:.1f}s"
        ]

        estimated_time_minutes: float | None = stats.get("time_minutes")
        if estimated_time_minutes is None and meta.get("prediction_seconds"):
            estimated_time_minutes = float(meta["prediction_seconds"]) / 60.0
            notes.append("print time taken from slice_info prediction")

        filament_used_mm: float | None = stats.get("filament_used_mm")
        if filament_used_mm is None and meta.get("filament_used_m") is not None:
            filament_used_mm = float(meta["filament_used_m"]) * 1000.0
            notes.append("filament length taken from slice_info (meters)")

        filament_used_cm3: float | None = stats.get("filament_used_cm3")
        filament_density: float | None = stats.get("filament_density")

        weight_g: float | None = None
        if filament_density and filament_used_cm3:
            weight_g = filament_density * filament_used_cm3
        if weight_g is None:
            used_g = meta.get("filament_used_g")
            if used_g is not None and float(used_g) > 0:
                weight_g = float(used_g)
        if weight_g is None and meta.get("weight_g") is not None:
            weight_g = float(meta["weight_g"])
        if weight_g is None:
            notes.append("filament weight not reported (density unavailable)")

        return SliceResult(
            job=job,
            sliced_at=datetime.now(UTC),
            output_3mf=three_mf,
            gcode_path=gcode,
            estimated_time_minutes=estimated_time_minutes,
            layer_count=stats.get("layer_count"),
            max_z_height=stats.get("max_z_height"),
            filament_used_mm=filament_used_mm,
            filament_used_cm3=filament_used_cm3,
            filament_density=filament_density,
            filament_weight_g=weight_g,
            return_code=result.return_code,
            notes=notes,
        )
