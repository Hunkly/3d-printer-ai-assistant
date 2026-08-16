"""Local-LLM provider abstraction (recommendation reasoning).

Kept deliberately small: a provider only needs to return a JSON object for a
prompt. No chat framework, no tool-calling, no Qwen-specific behavior in the
recommendation engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMClient(ABC):
    """Abstraction over a local LLM chat endpoint."""

    @abstractmethod
    def complete_json(
        self, prompt: str, *, timeout_seconds: float | None = None
    ) -> dict[str, Any]:
        """Ask the model for a JSON object.

        Raises provider-specific exceptions (:class:`print_engineer.errors.LLMError`
        subclasses) when the provider is unreachable, times out, or returns
        output that cannot be parsed as a JSON object.
        """
