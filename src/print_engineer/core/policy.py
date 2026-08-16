"""Safety policy gating physical printer actions.

Every action that changes the physical printer state must be evaluated against a
``SafetyPolicy`` before execution, so the AI can never send commands to the
printer unchecked.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum


class PrinterAction(StrEnum):
    START_PRINT = "start_print"
    STOP_PRINT = "stop_print"
    PAUSE_PRINT = "pause_print"
    RESUME_PRINT = "resume_print"
    SET_TEMPERATURE = "set_temperature"


@dataclass(frozen=True)
class PolicyContext:
    action: PrinterAction
    actor: str = "mcp"
    description: str = ""
    confirm: bool = False


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""
    requires_confirmation: bool = False


class SafetyPolicy(ABC):
    """Decides whether a printer action may be executed."""

    @abstractmethod
    def evaluate(self, context: PolicyContext) -> PolicyDecision:
        """Return the decision for *context*."""


class PermissivePolicy(SafetyPolicy):
    """Phase-0 inert stub.

    Does not talk to any printer. Dangerous actions require explicit
    confirmation; everything else is allowed (but nothing is executed yet).
    """

    _REQUIRES_CONFIRMATION = frozenset(
        {
            PrinterAction.START_PRINT,
            PrinterAction.STOP_PRINT,
            PrinterAction.SET_TEMPERATURE,
        }
    )

    def evaluate(self, context: PolicyContext) -> PolicyDecision:
        if context.action in self._REQUIRES_CONFIRMATION and not context.confirm:
            return PolicyDecision(
                allowed=False,
                requires_confirmation=True,
                reason=f"{context.action.value} requires explicit confirmation",
            )
        return PolicyDecision(allowed=True, reason="permissive stub: allowed")
