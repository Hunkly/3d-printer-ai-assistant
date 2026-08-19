"""Hermetic tests for the read-only Paho MQTT transport boundary."""

from __future__ import annotations

import inspect
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
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
SERIAL = "test"


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
        self.publishes: list[tuple[str, str, int]] = []
        self.events: list[str] = []
        self.subscribe_payloads: dict[str, list[bytes]] = {}
        self.subscribe_error: Exception | None = None
        self.subscribe_result: Any = transport_module.mqtt.MQTT_ERR_SUCCESS
        self.publish_error: Exception | None = None
        self.publish_result: Any = transport_module.mqtt.MQTT_ERR_SUCCESS
        self.connect_error: Exception | None = None
        self.reason_code = _ReasonCode()
        self.subscribe_barrier: Barrier | None = None
        self.subscribe_barrier_reached = False

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
        self.events.append("subscribe")
        if self.subscribe_error is not None:
            raise self.subscribe_error
        self.subscriptions.append((topic, qos))
        for payload in self.subscribe_payloads.pop(topic, []):
            self.emit(topic, payload)
        if self.subscribe_barrier is not None:
            self.subscribe_barrier_reached = True
            self.subscribe_barrier.wait(timeout=2)
        return (self.subscribe_result, len(self.subscriptions))

    def publish(self, topic: str, payload: str, qos: int) -> Any:
        self.events.append("publish")
        if self.publish_error is not None:
            raise self.publish_error
        self.publishes.append((topic, payload, qos))
        return SimpleNamespace(rc=self.publish_result)

    def disconnect(self) -> None:
        self.disconnect_count += 1

    def emit(self, topic: str, payload: bytes) -> None:
        message = SimpleNamespace(topic=topic, payload=payload)
        self.on_message(self, None, message)


@pytest.fixture(autouse=True)
def reset_refresh_cooldown() -> Iterator[None]:
    with transport_module._refresh_eligibility_lock:
        transport_module._refresh_next_eligible.clear()
    try:
        yield
    finally:
        with transport_module._refresh_eligibility_lock:
            transport_module._refresh_next_eligible.clear()


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
        serial=SERIAL,
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
    assert not hasattr(MqttClient, "request")
    assert not hasattr(MqttClient, "send_command")
    assert not hasattr(MqttClient, "send_json")
    assert source.count("self._client.publish(") == 1
    assert source.count('f"device/{self._serial}/request"') == 1
    assert source.count('"command": "pushall"') == 1
    for forbidden in (
        '"command": "start"',
        '"command": "stop"',
        '"command": "pause"',
        '"command": "resume"',
        '"gcode_line"',
        '"project_file"',
    ):
        assert forbidden not in source


def test_status_refresh_is_fixed_and_subscribes_before_publish(
    client: tuple[PahoMqttClient, _FakePahoClient],
) -> None:
    wrapper, fake = client
    wrapper.connect()

    assert wrapper.request_status_refresh() is True

    assert fake.events == ["subscribe", "publish"]
    assert fake.subscriptions == [(TOPIC, 0)]
    assert len(fake.publishes) == 1
    topic, payload, qos = fake.publishes[0]
    assert topic == "device/test/request"
    assert qos == 0
    assert json.loads(payload) == {
        "pushing": {
            "sequence_id": json.loads(payload)["pushing"]["sequence_id"],
            "command": "pushall",
            "version": 1,
            "push_target": 1,
        }
    }
    sequence_id = json.loads(payload)["pushing"]["sequence_id"]
    assert isinstance(sequence_id, str)
    assert sequence_id.isdecimal()
    assert int(sequence_id) >= 0
    assert wrapper.request_status_refresh() is False
    assert len(fake.publishes) == 1


@pytest.mark.parametrize("failure_kind", ["exception", "result"])
def test_failed_subscription_publishes_nothing_and_does_not_consume_cooldown(
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    first_fake = _FakePahoClient()
    if failure_kind == "exception":
        first_fake.subscribe_error = RuntimeError("subscribe failed")
    else:
        first_fake.subscribe_result = transport_module.mqtt.MQTT_ERR_NO_CONN
    second_fake = _FakePahoClient()
    fakes = iter([first_fake, second_fake])
    monkeypatch.setattr(transport_module.mqtt, "Client", lambda *_a, **_kw: next(fakes))
    first = PahoMqttClient(
        host="printer.local", port=8883, username="bblp", password="secret",
        client_id="first", serial=SERIAL,
    )
    second = PahoMqttClient(
        host="printer.local", port=8883, username="bblp", password="secret",
        client_id="second", serial=SERIAL,
    )
    first.connect()
    with pytest.raises(MqttConnectionError):
        first.request_status_refresh()
    assert first_fake.publishes == []

    second.connect()
    assert second.request_status_refresh() is True
    assert len(second_fake.publishes) == 1


@pytest.mark.parametrize("failure_kind", ["exception", "result"])
def test_publish_failure_is_not_retried_and_consumes_cooldown(
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    first_fake = _FakePahoClient()
    if failure_kind == "exception":
        first_fake.publish_error = RuntimeError("publish failed")
    else:
        first_fake.publish_result = transport_module.mqtt.MQTT_ERR_NO_CONN
    second_fake = _FakePahoClient()
    fakes = iter([first_fake, second_fake])
    monkeypatch.setattr(transport_module.mqtt, "Client", lambda *_a, **_kw: next(fakes))
    first = PahoMqttClient(
        host="printer.local", port=8883, username="bblp", password="secret",
        client_id="first", serial=SERIAL,
    )
    second = PahoMqttClient(
        host="printer.local", port=8883, username="bblp", password="secret",
        client_id="second", serial=SERIAL,
    )
    first.connect()
    with pytest.raises(MqttConnectionError):
        first.request_status_refresh()
    assert first_fake.events.count("publish") == 1

    second.connect()
    assert second.request_status_refresh() is False
    assert second_fake.publishes == []


def test_refresh_cooldown_is_per_serial_and_expires_without_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0
    monkeypatch.setattr(transport_module, "monotonic", lambda: now)
    fakes = [_FakePahoClient() for _ in range(4)]
    fake_iter = iter(fakes)
    monkeypatch.setattr(transport_module.mqtt, "Client", lambda *_a, **_kw: next(fake_iter))

    def build(serial: str, ordinal: int) -> PahoMqttClient:
        wrapper = PahoMqttClient(
            host="printer.local", port=8883, username="bblp", password="secret",
            client_id=f"client-{ordinal}", serial=serial,
        )
        wrapper.connect()
        return wrapper

    assert build("same", 1).request_status_refresh() is True
    assert build("same", 2).request_status_refresh() is False
    assert build("other", 3).request_status_refresh() is True
    now += 300.0
    assert build("same", 4).request_status_refresh() is True


def test_successful_publish_then_report_timeout_keeps_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fakes = [_FakePahoClient(), _FakePahoClient()]
    fake_iter = iter(fakes)
    monkeypatch.setattr(
        transport_module.mqtt, "Client", lambda *_a, **_kw: next(fake_iter)
    )
    wrappers = [
        PahoMqttClient(
            host="printer.local",
            port=8883,
            username="bblp",
            password="secret",
            client_id=f"client-{index}",
            serial=SERIAL,
        )
        for index in range(2)
    ]
    for wrapper in wrappers:
        wrapper.connect()

    assert wrappers[0].request_status_refresh() is True
    assert wrappers[0].fetch_report(TOPIC, 0) is None
    assert wrappers[1].request_status_refresh() is False
    assert sum(len(fake.publishes) for fake in fakes) == 1


def test_concurrent_same_serial_clients_publish_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fakes = [_FakePahoClient(), _FakePahoClient()]
    subscribe_barrier = Barrier(2)
    for fake in fakes:
        fake.subscribe_barrier = subscribe_barrier
    fake_iter = iter(fakes)
    monkeypatch.setattr(transport_module.mqtt, "Client", lambda *_a, **_kw: next(fake_iter))
    wrappers = [
        PahoMqttClient(
            host="printer.local", port=8883, username="bblp", password="secret",
            client_id=f"client-{index}", serial=SERIAL,
        )
        for index in range(2)
    ]
    for wrapper in wrappers:
        wrapper.connect()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda wrapper: wrapper.request_status_refresh(), wrappers))

    assert all(fake.subscribe_barrier_reached for fake in fakes)
    assert all(fake.subscriptions == [(TOPIC, 0)] for fake in fakes)
    assert sorted(results) == [False, True]
    assert sum(len(fake.publishes) for fake in fakes) == 1
