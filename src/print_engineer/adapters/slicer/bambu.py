"""Bambu Studio adapter.

Phase 1 policy: Bambu Studio is **detected, versioned, and used for profile
discovery and model validation only**. ``--slice`` reproducibly crashes on
this machine (access violation in ``BambuStudio.dll`` during
``update_values_to_printer_extruders_for_multiple_filaments`` on version
2.6.0.51), so ``slice()`` always reports a clear ``SlicerUnavailableError``
instead of attempting a broken operation. Do not retry and do not work around
the crash; an upgrade to 2.7.1.62 (matching the user's own projects) is the
intended path before slicing is enabled.
"""

from __future__ import annotations

from pathlib import Path

from print_engineer.adapters.slicer.base import BaseSlicerAdapter
from print_engineer.adapters.slicer.process import run_cli
from print_engineer.adapters.slicer.version import parse_bambu_banner, parse_version, version_gte
from print_engineer.core.types import SliceJob, SliceResult, SlicerKind
from print_engineer.errors import SlicerUnavailable

_KNOWN_BROKEN_VERSION = "2.6.0.51"
_TARGET_VERSION = "2.7.1.62"

_CRASH_REASON = (
    f"Bambu Studio CLI slicing crashes on this machine (access violation in "
    f"BambuStudio.dll during printer extruder setup; confirmed on {_KNOWN_BROKEN_VERSION}). "
    f"Slicing is disabled until Bambu Studio is upgraded to {_TARGET_VERSION} "
    f"(the version your own projects were created with) and verified."
)


class BambuStudioAdapter(BaseSlicerAdapter):
    kind = SlicerKind.BAMBU_STUDIO

    install_paths = (
        Path(r"C:\Program Files\Bambu Studio\bambu-studio.exe"),
        Path(r"C:\Program Files\BambuStudio\BambuStudio.exe"),
    )
    binaries = ("bambu-studio.exe", "BambuStudio.exe")
    appdata_dirname = "BambuStudio"

    def __init__(
        self,
        *,
        executable: str | Path | None = None,
        appdata: str | Path | None = None,
        workdir: str | Path | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(
            executable=executable,
            appdata=appdata,
            workdir=workdir,
            timeout_seconds=timeout_seconds,
        )
        self._bambu_version: tuple[str | None, str | None] | None = None

    def _detect_version(self, exe: Path) -> tuple[str | None, str | None]:
        if self._bambu_version is not None:
            return self._bambu_version
        try:
            result = run_cli([str(exe), "--help"], timeout=30.0)
        except SlicerUnavailable:
            self._bambu_version = (None, None)
            return self._bambu_version
        version = parse_bambu_banner(result.stdout)
        self._bambu_version = (version, "banner")
        return self._bambu_version

    def _slicing_supported(self, version: str | None) -> bool:
        if version is None:
            return False
        return version_gte(version, _TARGET_VERSION)

    def slice(self, job: SliceJob) -> SliceResult:
        info = self._require_detected()
        detected = parse_version(info.version) if info.version else None
        reason = (
            "cli_slice_crash"
            if detected is None or detected == parse_version(_KNOWN_BROKEN_VERSION)
            else "slice_disabled_pending_verification"
        )
        details = {
            "slicer_kind": self.kind.value,
            "executable": str(info.executable),
            "version": info.version,
            "reason": reason,
            "hint": f"Upgrade Bambu Studio to {_TARGET_VERSION} and verify slicing.",
        }
        raise SlicerUnavailable(_CRASH_REASON, details=details)
