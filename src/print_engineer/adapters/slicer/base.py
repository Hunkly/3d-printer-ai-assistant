"""Shared adapter logic for the Bambu Studio / OrcaSlicer CLIs."""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from print_engineer.adapters.slicer.process import run_cli
from print_engineer.adapters.slicer.profile import (
    ProfileMaterializer,
    ProfileRepository,
)
from print_engineer.adapters.slicer.version import parse_version, version_tuple
from print_engineer.core.interfaces.slicer import Slicer
from print_engineer.core.types import (
    ModelValidation,
    ProfileInfo,
    ProfileKind,
    SlicerInfo,
)
from print_engineer.errors import (
    InvalidModel,
    InvalidProfile,
    SliceFailed,
    SlicerError,
    SlicerNotInstalled,
    VersionMismatch,
)

SUPPORTED_INPUT_SUFFIXES = frozenset({".stl", ".3mf", ".obj", ".amf", ".step", ".stp", ".ply"})

_INFO_KEY_RE = re.compile(r"^\s*([a-z_0-9]+)\s*=\s*(.*?)\s*$")
_PROJECT_VERSION_RE = re.compile(r'"version"\s*:\s*"(\d+\.\d+(?:\.\d+)*)"')


def _read_project_version(model_path: Path) -> str | None:
    """Read the slicer version embedded in a ``.3mf`` project, if any."""
    if model_path.suffix.lower() != ".3mf":
        return None
    try:
        with zipfile.ZipFile(model_path) as archive:
            if "Metadata/project_settings.config" not in set(archive.namelist()):
                return None
            text = archive.read("Metadata/project_settings.config").decode(
                "utf-8", errors="replace"
            )
    except (zipfile.BadZipFile, OSError):
        return None
    match = _PROJECT_VERSION_RE.search(text)
    return match.group(1) if match else None


def _parse_info_output(output: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in output.splitlines():
        match = _INFO_KEY_RE.match(line)
        if match:
            parsed.setdefault(match.group(1), match.group(2).strip())
    return parsed


class BaseSlicerAdapter(Slicer):
    """Common implementation shared by the two slicer adapters."""

    install_paths: tuple[Path, ...] = ()
    binaries: tuple[str, ...] = ()
    appdata_dirname = ""
    info_timeout_seconds = 60.0

    def __init__(
        self,
        *,
        executable: str | Path | None = None,
        appdata: str | Path | None = None,
        workdir: str | Path | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._executable = Path(executable) if executable else None
        self._appdata = (
            Path(appdata) if appdata else Path.home() / "AppData" / "Roaming" / self.appdata_dirname
        )
        self._workdir = Path(workdir) if workdir else Path("runtime/data/workspace")
        self._timeout_seconds = timeout_seconds
        self._detection: SlicerInfo | None | bool = False

    @property
    def _adapter_name(self) -> str:
        return type(self).__name__.replace("Adapter", "")

    def _find_executable(self) -> Path | None:
        if self._executable is not None:
            return self._executable if self._executable.is_file() else None
        for candidate in self.install_paths:
            if candidate.is_file():
                return candidate
        for binary in self.binaries:
            found = shutil.which(binary)
            if found:
                return Path(found)
        return None

    def _require_executable(self) -> Path:
        exe = self._find_executable()
        if exe is None:
            raise SlicerNotInstalled(
                f"{self._adapter_name} executable not found",
                details={
                    "slicer_kind": self.kind.value,
                    "searched": [str(p) for p in self.install_paths],
                    "path_hint": "Install the slicer or set its install path in config.",
                },
            )
        return exe

    def _detect_version(self, exe: Path) -> tuple[str | None, str | None]:
        return None, None

    def _slicing_supported(self, version: str | None) -> bool:
        return True

    def detect(self) -> SlicerInfo | None:
        exe = self._find_executable()
        if exe is None:
            self._detection = None
            return None
        version, source = self._detect_version(exe)
        supported = self._slicing_supported(version)
        notes: list[str] = []
        if version is None:
            notes.append("version could not be determined")
        if not supported:
            notes.append("slicing is currently unavailable for this version")
        info = SlicerInfo(
            kind=self.kind,
            name=self._adapter_name,
            executable=exe,
            version=version,
            version_source=source,
            slicing_supported=supported,
            notes=tuple(notes),
        )
        self._detection = info
        return info

    def _require_detected(self) -> SlicerInfo:
        info = self.detect()
        if info is None:
            raise SlicerNotInstalled(
                f"{self._adapter_name} executable not found",
                details={
                    "slicer_kind": self.kind.value,
                    "searched": [str(p) for p in self.install_paths],
                },
            )
        return info

    def _repository(self) -> ProfileRepository:
        return ProfileRepository(self._appdata)

    def _materializer(self) -> ProfileMaterializer:
        return ProfileMaterializer(self._repository())

    def list_profiles(self, profile_kind: ProfileKind) -> list[ProfileInfo]:
        return self._repository().list_profiles(profile_kind)

    def find_profile(self, profile_kind: ProfileKind, name: str) -> ProfileInfo | None:
        """Find a profile by name and materialize it for read-only inspection.

        Materialization resolves inheritance chains and ``from: User`` deltas
        into a self-contained document, which is what the recommendation
        engine's typed settings reader consumes.
        """
        profile = self._repository().find(profile_kind, name)
        if profile is None:
            return None
        return self._materializer().materialize(profile)

    def _check_project_version(self, model_path: Path, slicer_version: str | None) -> None:
        """Raise :class:`VersionMismatch` when a 3mf needs a newer slicer."""
        if slicer_version is None:
            return
        project_version = _read_project_version(model_path)
        if project_version is None:
            return
        project_tuple = parse_version(project_version)
        slicer_tuple = version_tuple(slicer_version)
        if project_tuple is not None and project_tuple > slicer_tuple:
            raise VersionMismatch(
                f"Project {model_path.name} was created by {self._adapter_name} "
                f"{project_version}, but {slicer_version} is installed.",
                details={
                    "model_path": str(model_path),
                    "model_version": project_version,
                    "slicer_version": slicer_version,
                    "slicer_kind": self.kind.value,
                    "hint": "Upgrade the slicer or re-save the project with the installed version.",
                },
            )

    def _validate_model_locally(self, model_path: Path) -> None:
        if not model_path.exists():
            raise InvalidModel(
                f"Model file does not exist: {model_path}",
                details={"model_path": str(model_path), "reason": "not_found"},
            )
        if not model_path.is_file():
            raise InvalidModel(
                f"Model path is not a file: {model_path}",
                details={"model_path": str(model_path), "reason": "not_a_file"},
            )
        if model_path.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
            raise InvalidModel(
                f"Unsupported model type {model_path.suffix!r}",
                details={
                    "model_path": str(model_path),
                    "reason": "unsupported_suffix",
                    "supported": sorted(SUPPORTED_INPUT_SUFFIXES),
                },
            )

    def validate_input(self, model_path: Path) -> ModelValidation:
        self._validate_model_locally(model_path)
        info = self._require_detected()
        self._check_project_version(model_path, info.version)

        exe = info.executable
        result = run_cli(
            [str(exe), "--info", str(model_path)],
            timeout=self.info_timeout_seconds,
        )
        if result.timed_out:
            return ModelValidation(
                path=model_path,
                is_valid=False,
                message=(
                    f"{self._adapter_name} did not respond to the info probe within "
                    f"{self.info_timeout_seconds:.0f}s"
                ),
            )

        parsed = _parse_info_output(result.stdout)
        if "size_x" not in parsed:
            detail = (result.stderr or result.stdout).strip().splitlines()
            message = f"{self._adapter_name} rejected the model"
            if detail:
                message += f": {detail[-1][:200]}"
            return ModelValidation(path=model_path, is_valid=False, message=message)

        try:
            size = (
                float(parsed["size_x"]),
                float(parsed["size_y"]),
                float(parsed["size_z"]),
            )
        except (KeyError, ValueError):
            size = None

        def _float_or_none(key: str) -> float | None:
            value = parsed.get(key)
            if value is None:
                return None
            try:
                return float(value)
            except ValueError:
                return None

        def _int_or_none(key: str) -> int | None:
            value = parsed.get(key)
            if value is None:
                return None
            try:
                return int(value)
            except ValueError:
                return None

        return ModelValidation(
            path=model_path,
            is_valid=True,
            message=f"model accepted by {self._adapter_name}",
            size=size,
            volume_mm3=_float_or_none("volume"),
            facets=_int_or_none("number_of_facets"),
            is_manifold=parsed.get("manifold", "").lower() == "yes",
            parts=_int_or_none("number_of_parts"),
        )

    def _prepare_profiles(self, job: Any) -> tuple[ProfileInfo, ProfileInfo, ProfileInfo]:
        """Materialize the process/filament/printer profiles for a job."""
        materializer = self._materializer()
        printer = job.printer
        if printer is None:
            raise InvalidProfile(
                "A printer (machine) profile is required to slice",
                details={"slicer_kind": self.kind.value},
            )
        try:
            process = materializer.materialize(job.profile)
            filament = materializer.materialize(job.filament)
            machine = materializer.materialize(printer)
        except InvalidProfile:
            raise
        except SlicerError as exc:
            raise InvalidProfile(str(exc), details=exc.details) from exc

        machine_name = machine.name
        for label, profile in (("process", process), ("filament", filament)):
            compatible = profile.compatible_printers
            if compatible and machine_name not in compatible:
                raise InvalidProfile(
                    f"{label} profile {profile.name!r} is not compatible with printer "
                    f"{machine_name!r}",
                    details={
                        "profile_kind": label,
                        "profile_name": profile.name,
                        "printer": machine_name,
                        "compatible_printers": list(compatible),
                    },
                )
        return process, filament, machine

    def _write_job_files(
        self, job_dir: Path, profiles: dict[str, ProfileInfo]
    ) -> dict[str, Path]:
        files: dict[str, Path] = {}
        for key, profile in profiles.items():
            path = job_dir / f"{key}.json"
            path.write_text(profile.content or "{}", encoding="utf-8")
            files[key] = path
        return files

    def _read_result_json(self, job_dir: Path) -> dict[str, Any] | None:
        candidate = job_dir / "result.json"
        if not candidate.is_file():
            return None
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _job_dir(self) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        directory = self._workdir / "slicer" / self.kind.value / stamp
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _export_name(self, job: Any) -> str:
        if job.export_name:
            return job.export_name
        return f"{job.model_path.stem}.gcode.3mf"

    def _timeout_for(self, job: Any) -> float:
        if job.timeout_seconds is not None:
            return float(job.timeout_seconds)
        if self._timeout_seconds is not None:
            return self._timeout_seconds
        return 600.0

    def _error_from_stderr(self, stderr: str, stdout: str) -> SlicerError:
        combined = f"{stderr}\n{stdout}"
        if "Version Check" in combined:
            match = re.search(r"File Version ([\d.]+)", combined)
            version = match.group(1) if match else "unknown"
            return VersionMismatch(
                f"Slicer rejected the project: file version {version} is too new",
                details={"model_version": version, "slicer_kind": self.kind.value},
            )
        if "not compatible with printer" in combined:
            return InvalidProfile(
                "Slicer rejected the job: process/filament not compatible with printer",
                details={"slicer_kind": self.kind.value, "reason": "incompatible_profiles"},
            )
        if "does not support filament" in combined:
            return InvalidProfile(
                "Slicer rejected the job: the selected filament is incompatible with "
                "the build plate of the process profile",
                details={
                    "slicer_kind": self.kind.value,
                    "reason": "incompatible_plate_filament",
                    "message": combined.strip()[-200:],
                },
            )
        if "from" in combined and "unsupported" in combined:
            return InvalidProfile(
                "Slicer rejected a profile: unsupported source field",
                details={"slicer_kind": self.kind.value, "reason": "invalid_source"},
            )
        return SliceFailed(
            f"{self._adapter_name} failed with an unknown error",
            details={
                "slicer_kind": self.kind.value,
                "stderr": stderr.strip()[:500],
                "stdout": stdout.strip()[:500],
            },
        )
