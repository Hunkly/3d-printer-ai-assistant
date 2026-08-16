"""Printer abstraction.

Implementations must evaluate every state-changing action against a
``SafetyPolicy`` before talking to the physical printer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from print_engineer.core.policy import PolicyDecision
from print_engineer.core.types import PrinterStatus, Snapshot, TemperatureSetpoint


class Printer(ABC):
    """Abstraction over a 3D printer (Bambu Lab A1 LAN MQTT in Phase 2+)."""

    @abstractmethod
    def connect(self) -> None:
        """Open a connection to the printer."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close the connection."""

    @abstractmethod
    def get_status(self) -> PrinterStatus:
        """Return the current printer status."""

    @abstractmethod
    def start_print(self, project: Path, *, confirm: bool = False) -> PolicyDecision:
        """Start printing *project*; requires ``confirm=True``."""

    @abstractmethod
    def stop_print(self, *, confirm: bool = False) -> PolicyDecision:
        """Stop the running print; requires ``confirm=True``."""

    @abstractmethod
    def pause_print(self, *, confirm: bool = False) -> PolicyDecision:
        """Pause the running print."""

    @abstractmethod
    def resume_print(self, *, confirm: bool = False) -> PolicyDecision:
        """Resume a paused print."""

    @abstractmethod
    def set_temperature(
        self, setpoint: TemperatureSetpoint, *, confirm: bool = False
    ) -> PolicyDecision:
        """Set a temperature; requires ``confirm=True``."""

    @abstractmethod
    def take_snapshot(self) -> Snapshot:
        """Capture a current photo of the print bed."""
