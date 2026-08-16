"""Slicer abstraction: Bambu Studio / OrcaSlicer CLI."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from print_engineer.core.types import (
    ModelValidation,
    ProfileInfo,
    ProfileKind,
    SliceJob,
    SliceResult,
    SlicerInfo,
    SlicerKind,
)


class Slicer(ABC):
    """Abstraction over a slicer CLI (Bambu Studio / OrcaSlicer)."""

    @property
    @abstractmethod
    def kind(self) -> SlicerKind:
        """The kind of slicer this adapter drives."""

    @abstractmethod
    def detect(self) -> SlicerInfo | None:
        """Detect the installed slicer and return rich info, if any."""

    @abstractmethod
    def list_profiles(self, profile_kind: ProfileKind) -> list[ProfileInfo]:
        """List available process/filament/printer profiles."""

    @abstractmethod
    def find_profile(self, profile_kind: ProfileKind, name: str) -> ProfileInfo | None:
        """Find a profile by name and materialize it for read-only inspection."""

    @abstractmethod
    def validate_input(self, model_path: Path) -> ModelValidation:
        """Validate *model_path* as a sliceable input for this slicer."""

    @abstractmethod
    def slice(self, job: SliceJob) -> SliceResult:
        """Slice *job* and return the resulting artifact."""
