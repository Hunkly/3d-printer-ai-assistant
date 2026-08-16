"""Ollama LLM provider client.

Talks to a local Ollama instance over HTTP (``/api/chat``). Uses ``format:
"json"`` so Ollama is nudged toward valid JSON and reads the assistant message
content back as a dict.

Errors are mapped to :class:`print_engineer.errors.LLMError` subclasses so the
recommendation engine can react deterministically:
- unreachable / HTTP error       -> ``LLMUnavailable``
- request timeout                -> ``LLMTimeout``
- non-JSON or non-object content -> ``LLMInvalidResponse``
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from print_engineer.config import LLMConfig
from print_engineer.core.interfaces.llm import LLMClient
from print_engineer.errors import LLMInvalidResponse, LLMTimeout, LLMUnavailable

_CHAT_ENDPOINT = "/api/chat"


class OllamaClient(LLMClient):
    """HTTP client for Ollama's ``/api/chat`` endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 120.0,
        temperature: float = 0.2,
        client: httpx.Client | None = None,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout_seconds)

    def complete_json(
        self, prompt: str, *, timeout_seconds: float | None = None
    ) -> dict[str, Any]:
        timeout = timeout_seconds if timeout_seconds is not None else self._timeout_seconds
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "options": {"temperature": self._temperature},
        }
        try:
            response = self._client.post(_CHAT_ENDPOINT, json=payload, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise LLMTimeout(
                f"Ollama request exceeded {timeout:.0f}s",
                details={"model": self._model, "timeout_seconds": timeout},
            ) from exc
        except httpx.ConnectError as exc:
            raise LLMUnavailable(
                f"Could not reach Ollama at {self._client.base_url}",
                details={"model": self._model, "base_url": str(self._client.base_url)},
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable(
                f"Ollama request failed: {exc}",
                details={"model": self._model},
            ) from exc

        if response.status_code != 200:
            raise LLMUnavailable(
                f"Ollama returned HTTP {response.status_code}",
                details={
                    "model": self._model,
                    "status_code": response.status_code,
                    "body": response.text[:300],
                },
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMInvalidResponse(
                "Ollama response was not valid JSON",
                details={"model": self._model},
            ) from exc

        content = data.get("message", {}).get("content")
        if isinstance(content, dict):
            return content
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except (json.JSONDecodeError, TypeError) as exc:
                raise LLMInvalidResponse(
                    "Ollama message content was not valid JSON",
                    details={"model": self._model, "content": content[:300]},
                ) from exc
            if isinstance(parsed, dict):
                return parsed
        raise LLMInvalidResponse(
            "Ollama message content was not a JSON object",
            details={"model": self._model, "content_type": type(content).__name__},
        )


def build_llm_client(config: LLMConfig) -> LLMClient | None:
    """Build the configured LLM provider, or ``None`` when disabled."""
    if not config.enabled:
        return None
    if config.provider != "ollama":
        raise LLMUnavailable(
            f"Unsupported LLM provider {config.provider!r} (supported: ollama)",
            details={"provider": config.provider},
        )
    return OllamaClient(
        base_url=config.base_url,
        model=config.model,
        timeout_seconds=config.timeout_seconds,
        temperature=config.temperature,
    )
