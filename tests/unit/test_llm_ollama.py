"""Ollama LLM client tests (Phase 3A)."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import httpx
import pytest

from print_engineer.adapters.llm.ollama import OllamaClient, build_llm_client
from print_engineer.config import LLMConfig
from print_engineer.errors import LLMInvalidResponse, LLMTimeout, LLMUnavailable

_Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: _Handler) -> OllamaClient:
    transport = httpx.MockTransport(handler)
    return OllamaClient(
        base_url="http://127.0.0.1:11434",
        model="test-model",
        timeout_seconds=30.0,
        temperature=0.2,
        client=httpx.Client(base_url="http://127.0.0.1:11434", transport=transport),
    )


def _json_handler(payload: Mapping[str, object]) -> _Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        return httpx.Response(200, json=payload)

    return handler


class TestCompleteJson:
    def test_returns_dict_content_directly(self) -> None:
        payload = {"message": {"content": {"goal": "balanced"}}}
        client = _client(_json_handler(payload))
        assert client.complete_json("prompt") == {"goal": "balanced"}

    def test_parses_json_string_content(self) -> None:
        payload = {"message": {"content": '{"goal": "balanced"}'}}
        client = _client(_json_handler(payload))
        assert client.complete_json("prompt") == {"goal": "balanced"}

    def test_sends_expected_payload(self) -> None:
        captured: dict[str, bytes] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["json"] = request.content
            return httpx.Response(200, json={"message": {"content": {"ok": True}}})

        client = _client(handler)
        client.complete_json("hello")
        import json as _json

        sent = _json.loads(captured["json"].decode())
        assert sent["model"] == "test-model"
        assert sent["stream"] is False
        assert sent["format"] == "json"
        assert sent["options"] == {"temperature": 0.2}
        assert sent["messages"][0]["content"] == "hello"

    def test_http_500_raises_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        client = _client(handler)
        with pytest.raises(LLMUnavailable) as excinfo:
            client.complete_json("prompt")
        assert excinfo.value.code == "llm_unavailable"

    def test_connect_error_raises_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        client = _client(handler)
        with pytest.raises(LLMUnavailable):
            client.complete_json("prompt")

    def test_timeout_raises_llm_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("slow")

        client = _client(handler)
        with pytest.raises(LLMTimeout) as excinfo:
            client.complete_json("prompt")
        assert excinfo.value.code == "llm_timeout"

    def test_non_json_content_raises_invalid_response(self) -> None:
        payload = {"message": {"content": "definitely not json"}}
        client = _client(_json_handler(payload))
        with pytest.raises(LLMInvalidResponse) as excinfo:
            client.complete_json("prompt")
        assert excinfo.value.code == "llm_invalid_response"

    def test_missing_message_raises_invalid_response(self) -> None:
        payload = {"nonsense": True}
        client = _client(_json_handler(payload))
        with pytest.raises(LLMInvalidResponse):
            client.complete_json("prompt")

    def test_scalar_content_raises_invalid_response(self) -> None:
        payload = {"message": {"content": 42}}
        client = _client(_json_handler(payload))
        with pytest.raises(LLMInvalidResponse):
            client.complete_json("prompt")


class TestBuildLlMClient:
    def test_disabled_returns_none(self) -> None:
        config = LLMConfig(enabled=False)
        assert build_llm_client(config) is None

    def test_unsupported_provider_raises_unavailable(self) -> None:
        config = LLMConfig(enabled=True, provider="openai")
        with pytest.raises(LLMUnavailable) as excinfo:
            build_llm_client(config)
        assert excinfo.value.code == "llm_unavailable"

    def test_ollama_returns_client(self) -> None:
        config = LLMConfig(enabled=True, provider="ollama")
        client = build_llm_client(config)
        assert isinstance(client, OllamaClient)
