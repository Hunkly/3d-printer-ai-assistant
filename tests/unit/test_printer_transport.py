"""Hermetic tests for the read-only Paho MQTT transport boundary."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import pytest

import print_engineer.adapters.printer.transport as transport_module
from print_engineer.adapters.printer.transport import (
    _REPORT_QUEUE_CAPACITY,
    MqttClient,
    MqttConnectionError,
    PahoMqttClient,
)

TOPIC = "device/test/report"
OTHER_TOPIC = "device/other/report"


class _ReasonCode:
    def __init__(self, *, is_failure: bool = False, value: int = 0) -> None:
        self.is_failure = is_failure
        self.value = value


class _FakePahoClient:
    """Deterministic callback-driven stand-in for paho.Client."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.on_connect: Any = None
        self.on_connect_fail: Any = None
        self.on_message: Any = None
        self.connect_count = 0
        self.disconnect_count = 0
        self.loop_start_count = 0
        self.loop_stop_count = 0
        self.subscriptions: list[tuple[str, int]] = []
        self.subscribe_payloads: dict[str, list[bytes]] = {}
        self.connect_error: Exception | None = None
        self.reason_code = _ReasonCode()

    def tls_set(self, **_kwargs: Any) -> None:
        pass

    def username_pw_set(self, _username: str, _password: str) -> None:
        pass

    def connect(self, _host: str, _port: int, *, keepalive: int) -> None:
        self.connect_count += 1
        if self.connect_error is not None:
            raise self.connect_error

    def loop_start(self) -> None:
        self.loop_start_count += 1
        self.on_connect(self, None, None, self.reason_code, None)

    def loop_stop(self) -> None:
        self.loop_stop_count += 1

    def subscribe(self, topic: str, qos: int) -> tuple[int, int]:
        self.subscriptions.append((topic, qos))
        for payload in self.subscribe_payloads.pop(topic, []):
            self.emit(topic, payload)
        return (0, len(self.subscriptions))

    def disconnect(self) -> None:
        self.disconnect_count += 1

    def emit(self, topic: str, payload: bytes) -> None:
        message = SimpleNamespace(topic=topic, payload=payload)
        self.on_message(self, None, message)


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[PahoMqttClient, _FakePahoClient]:
    fake = _FakePahoClient()
    monkeypatch.setattr(transport_module.mqtt, "Client", lambda *_a, **_kw: fake)
    wrapper = PahoMqttClient(
        host="printer.local",
        port=8883,
        username="bblp",
        password="secret",
        client_id="test-client",
    )
    return wrapper, fake


def test_connect_starts_loop_once_and_active_connect_is_idempotent(
    client: tuple[PahoMqttClient, _FakePahoClient],
) -> None:
    wrapper, fake = client
    wrapper.connect()
    wrapper.connect()
    assert fake.connect_count == 1
    assert fake.loop_start_count == 1


def test_repeated_fetches_return_distinct_reports_without_stopping_loop(
    client: tuple[PahoMqttClient, _FakePahoClient],
) -> None:
    wrapper, fake = client
    fake.subscribe_payloads[TOPIC] = [b"first", b"second"]
    wrapper.connect()
    assert wrapper.fetch_report(TOPIC, 0) == b"first"
    assert wrapper.fetch_report(TOPIC, 0) == b"second"
    assert wrapper.fetch_report(TOPIC, 0) is None
    assert fake.loop_stop_count == 0
    assert fake.subscriptions == [(TOPIC, 0)]


def test_queued_reports_preserve_fifo_order(
    client: tuple[PahoMqttClient, _FakePahoClient],
) -> None:
    wrapper, fake = client
    fake.subscribe_payloads[TOPIC] = [b"one", b"two", b"three"]
    wrapper.connect()
    assert [wrapper.fetch_report(TOPIC, 0) for _ in range(3)] == [
        b"one",
        b"two",
        b"three",
    ]


def test_topic_queues_do_not_cross_deliver(
    client: tuple[PahoMqttClient, _FakePahoClient],
) -> None:
    wrapper, fake = client
    fake.subscribe_payloads[TOPIC] = [b"one"]
    fake.subscribe_payloads[OTHER_TOPIC] = [b"other"]
    wrapper.connect()
    assert wrapper.fetch_report(TOPIC, 0) == b"one"
    assert wrapper.fetch_report(OTHER_TOPIC, 0) == b"other"
    assert fake.subscriptions == [(TOPIC, 0), (OTHER_TOPIC, 0)]


def test_timeout_preserves_subscription_loop_and_later_usability(
    client: tuple[PahoMqttClient, _FakePahoClient],
) -> None:
    wrapper, fake = client
    wrapper.connect()
    assert wrapper.fetch_report(TOPIC, 0) is None
    assert fake.loop_stop_count == 0
    fake.emit(TOPIC, b"later")
    assert wrapper.fetch_report(TOPIC, 0) == b"later"
    assert fake.subscriptions == [(TOPIC, 0)]


def test_disconnect_is_idempotent_and_owns_loop_shutdown(
    client: tuple[PahoMqttClient, _FakePahoClient],
) -> None:
    wrapper, fake = client
    wrapper.connect()
    wrapper.disconnect()
    wrapper.disconnect()
    assert fake.disconnect_count == 1
    assert fake.loop_stop_count == 1


def test_reconnect_clears_payloads_subscriptions_and_drop_count(
    client: tuple[PahoMqttClient, _FakePahoClient],
) -> None:
    wrapper, fake = client
    fake.subscribe_payloads[TOPIC] = [b"stale"] * (_REPORT_QUEUE_CAPACITY + 1)
    wrapper.connect()
    assert wrapper.fetch_report(TOPIC, 0) == b"stale"
    assert wrapper._dropped_report_count == 1
    wrapper.disconnect()
    assert wrapper._dropped_report_count == 0

    wrapper.connect()
    assert wrapper.fetch_report(TOPIC, 0) is None
    assert fake.subscriptions == [(TOPIC, 0), (TOPIC, 0)]
    assert wrapper._dropped_report_count == 0


def test_queue_capacity_drops_oldest_and_retains_newest_without_logging(
    client: tuple[PahoMqttClient, _FakePahoClient],
    caplog: pytest.LogCaptureFixture,
) -> None:
    wrapper, fake = client
    payloads = [str(index).encode() for index in range(_REPORT_QUEUE_CAPACITY + 2)]
    fake.subscribe_payloads[TOPIC] = payloads
    wrapper.connect()

    received = [
        wrapper.fetch_report(TOPIC, 0) for _ in range(_REPORT_QUEUE_CAPACITY)
    ]
    assert _REPORT_QUEUE_CAPACITY == 32
    assert received == payloads[2:]
    assert wrapper._dropped_report_count == 2
    assert caplog.records == []


@pytest.mark.parametrize(
    ("reason_code", "expected_reason"),
    [(_ReasonCode(is_failure=True, value=134), "auth"), (_ReasonCode(), None)],
)
def test_connection_classification_and_cleanup(
    client: tuple[PahoMqttClient, _FakePahoClient],
    reason_code: _ReasonCode,
    expected_reason: str | None,
) -> None:
    wrapper, fake = client
    if expected_reason is None:
        fake.connect_error = OSError("unreachable")
    else:
        fake.reason_code = reason_code
    with pytest.raises(MqttConnectionError) as exc_info:
        wrapper.connect()
    assert exc_info.value.reason == (expected_reason or "unreachable")
    assert fake.loop_stop_count == (1 if expected_reason else 0)
    assert wrapper._report_queues == {}
    assert wrapper._subscribed_topics == set()
    assert wrapper._dropped_report_count == 0


def test_read_only_transport_has_no_write_path() -> None:
    source = inspect.getsource(transport_module)
    assert not hasattr(MqttClient, "publish")
    assert not hasattr(_FakePahoClient, "publish")
    assert "publish(" not in source
    assert "/request" not in source
    assert "pushall" not in source
