"""MQTT transport abstraction for the Bambu Lab LAN protocol (Phase 2+).

The only module that imports paho-mqtt. Strictly read-only: the transport
subscribes to a single report topic and never publishes anything.
"""

from __future__ import annotations

import ssl
import threading
from typing import Any, Protocol

import paho.mqtt.client as mqtt
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

_CONNACK_WAIT_SECONDS = 10.0


class MqttConnectionError(Exception):
    """Transport-level failure signal.

    ``reason`` is one of ``"auth"`` or ``"unreachable"``.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class MqttClient(Protocol):
    """Minimal MQTT surface the printer adapter needs."""

    def connect(self) -> None: ...

    def fetch_report(self, topic: str, timeout_seconds: float) -> bytes | None: ...

    def disconnect(self) -> None: ...


class PahoMqttClient:
    """Real MQTT client wrapping ``paho.mqtt.client.Client``."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        client_id: str,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=client_id
        )
        self._connected = threading.Event()
        self._connack_failure: str | None = None
        self._report_received = threading.Event()
        self._payload: bytes | None = None
        self._client.on_connect = self._on_connect
        self._client.on_connect_fail = self._on_connect_fail
        self._client.on_message = self._on_message

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: ReasonCode,
        properties: Properties | None,
    ) -> None:
        if reason_code.is_failure:
            # v3.1.1 CONNACK rc 4/5 map to v5 reason codes 134/135.
            self._connack_failure = (
                "auth" if reason_code.value in (134, 135) else "unreachable"
            )
        self._connected.set()

    def _on_connect_fail(self, client: mqtt.Client, userdata: Any) -> None:
        self._connack_failure = "unreachable"
        self._connected.set()

    def _on_message(
        self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage
    ) -> None:
        self._payload = message.payload
        self._report_received.set()

    def connect(self) -> None:
        self._client.tls_set(
            cert_reqs=ssl.CERT_NONE, tls_version=ssl.PROTOCOL_TLS_CLIENT
        )
        self._client.username_pw_set(self._username, self._password)
        try:
            self._client.connect(self._host, self._port, keepalive=60)
        except (OSError, ValueError) as exc:
            raise MqttConnectionError("unreachable") from exc
        self._client.loop_start()
        if not self._connected.wait(timeout=_CONNACK_WAIT_SECONDS):
            self._client.loop_stop()
            raise MqttConnectionError("unreachable")
        if self._connack_failure is not None:
            self._client.loop_stop()
            raise MqttConnectionError(self._connack_failure)

    def fetch_report(self, topic: str, timeout_seconds: float) -> bytes | None:
        self._payload = None
        self._report_received.clear()
        self._client.subscribe(topic, qos=0)
        if not self._report_received.wait(timeout=timeout_seconds):
            self._client.loop_stop()
            return None
        self._client.loop_stop()
        return self._payload

    def disconnect(self) -> None:
        try:
            self._client.disconnect()
        finally:
            self._client.loop_stop()


class MqttClientFactory(Protocol):
    """Factory protocol for building MQTT clients."""

    def __call__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        client_id: str,
    ) -> MqttClient: ...


class PahoMqttClientFactory:
    """Stateless factory returning real paho-backed clients."""

    def __call__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        client_id: str,
    ) -> MqttClient:
        return PahoMqttClient(
            host=host,
            port=port,
            username=username,
            password=password,
            client_id=client_id,
        )