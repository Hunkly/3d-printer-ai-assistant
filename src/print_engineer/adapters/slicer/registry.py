"""Slicer adapter registry and factory."""

from __future__ import annotations

from typing import Any

from print_engineer.adapters.slicer.bambu import BambuStudioAdapter
from print_engineer.adapters.slicer.orca import OrcaSlicerAdapter
from print_engineer.core.interfaces.slicer import Slicer
from print_engineer.core.types import SlicerInfo, SlicerKind
from print_engineer.errors import SlicerNotInstalled

_ADAPTER_FACTORIES: dict[SlicerKind, type[Slicer]] = {
    SlicerKind.ORCA_SLICER: OrcaSlicerAdapter,
    SlicerKind.BAMBU_STUDIO: BambuStudioAdapter,
}


class SlicerRegistry:
    """Builds slicer adapters from settings and reports what is installed."""

    def __init__(
        self,
        *,
        settings: Any | None = None,
        workdir: Any | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._settings = settings
        self._workdir = workdir
        self._timeout_seconds = timeout_seconds

    def _kwargs(self, kind: SlicerKind) -> dict[str, object]:
        kwargs: dict[str, object] = {}
        if self._workdir is not None:
            kwargs["workdir"] = self._workdir
        if self._timeout_seconds is not None:
            kwargs["timeout_seconds"] = self._timeout_seconds
        if self._settings is not None:
            if kind == SlicerKind.ORCA_SLICER and self._settings.slicer.orca_install_path:
                kwargs["executable"] = self._settings.slicer.orca_install_path
            if kind == SlicerKind.BAMBU_STUDIO and self._settings.slicer.bambu_install_path:
                kwargs["executable"] = self._settings.slicer.bambu_install_path
        return kwargs

    def adapters(self) -> list[Slicer]:
        return [
            factory(**self._kwargs(kind))
            for kind, factory in _ADAPTER_FACTORIES.items()
        ]

    def detect_all(self) -> dict[SlicerKind, SlicerInfo]:
        detected: dict[SlicerKind, SlicerInfo] = {}
        for adapter in self.adapters():
            info = adapter.detect()
            if info is not None:
                detected[adapter.kind] = info
        return detected

    def get(self, kind: SlicerKind) -> Slicer:
        factory = _ADAPTER_FACTORIES.get(kind)
        if factory is None:
            raise SlicerNotInstalled(
                f"Unknown slicer kind: {kind.value}",
                details={"slicer_kind": kind.value},
            )
        adapter = factory(**self._kwargs(kind))
        if adapter.detect() is None:
            raise SlicerNotInstalled(
                f"{kind.value} executable not found",
                details={"slicer_kind": kind.value},
            )
        return adapter

    def get_info(self, kind: SlicerKind) -> SlicerInfo | None:
        factory = _ADAPTER_FACTORIES.get(kind)
        if factory is None:
            return None
        return factory(**self._kwargs(kind)).detect()
