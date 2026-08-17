# Phase 2+ — Bambu A1 Read-Only Printer Integration Plan

## Status

APPROVED

## Understanding

Implement the FIRST increment of Phase 2+ (Bambu Lab A1 LAN/MQTT printer
integration): a read-only status channel that connects to the A1 over LAN
MQTT (TLS), retrieves the printer's current status report, and normalizes it
into the existing `PrinterStatus` model. The increment is strictly
READ-ONLY: it must never change printer state, never send commands, never
publish to the request topic, and never slice. The increment performs no
MQTT publish of any kind — not even a `pushall` request (decision 4). The
full lifecycle is connect → subscribe/read `device/{serial}/report` →
normalize status → disconnect (decision 6).

The `Printer` ABC (`src/print_engineer/core/interfaces/printer.py`) requires
9 abstract methods. Only the read-only subset (`connect`, `get_status`,
`disconnect`) is implemented for real in this increment; the camera method
(`take_snapshot`) and all state-changing methods are implemented as
structured "operation not supported in the read-only increment" errors so
the adapter remains a concrete class without ever touching the printer.

The increment also exposes one read-only MCP tool (`printer.status`), which
the existing MCP architecture explicitly provides for (`mcp/tools/__init__.py`
plans `printer.*`; README architecture section plans `printer.*` tools).

## Repository Evidence

- `src/print_engineer/core/interfaces/printer.py` — `Printer` ABC: `connect`,
  `disconnect`, `get_status`, `start_print`, `stop_print`, `pause_print`,
  `resume_print`, `set_temperature`, `take_snapshot`. Docstring: "Bambu Lab
  A1 LAN MQTT in Phase 2+"; implementations must evaluate every
  state-changing action against a `SafetyPolicy`. Read-only operations are
  not policy-gated.
- `src/print_engineer/core/types.py` — `PrinterState` (OFFLINE, IDLE,
  PRINTING, PAUSED, ERROR, UNKNOWN), `PrinterStatus` (state, is_connected,
  bed_temp, nozzle_temp, target_bed_temp, target_nozzle_temp, progress,
  ams), `AMSInfo` (is_connected, slots), `Snapshot`, `TemperatureSetpoint`.
- `src/print_engineer/core/policy.py` — `SafetyPolicy` ABC + `PermissivePolicy`
  Phase-0 stub. Relevant only to state-changing actions (future increment).
- `src/print_engineer/config.py` — `PrinterConfig` (host, serial from YAML);
  `BambuSecrets` (`BAMBU_IP`, `BAMBU_SERIAL`, `BAMBU_ACCESS_CODE` from
  `.env`, never persisted). `tests/unit/test_config.py` already pins
  `test_bambu_secrets_from_env`.
- `.env.example` — already documents `BAMBU_IP`, `BAMBU_SERIAL`,
  `BAMBU_ACCESS_CODE` with the comment "(Phase 2+)".
- `config/config.example.yaml` — `printer:` section with `host`, `serial`.
- `src/print_engineer/adapters/printer/__init__.py` — placeholder: "Placeholder
  until Phase 2+".
- `src/print_engineer/mcp/server.py` — tools are plain callables registered
  via `mcp.tool(name=..., description=...)(callable)`; per-group `build_tools`
  pattern (`slicer.build_tools`, `model.build_tools`,
  `recommend.build_tools`) and per-group description helpers.
- `src/print_engineer/mcp/tools/recommend.py` — established pattern: a bound
  tool-group class (`RecommendTools(settings)`), `build_tools(settings)`
  returning `{name: callable}`, `{"ok": True, ...}` /
  `{"ok": False, "error": {code, message, details}}` response contract.
- `src/print_engineer/mcp/tools/system.py` — minimal tool example.
- `src/print_engineer/errors.py` — typed error hierarchy; `SlicerError` carries
  `code` + `details` + `to_dict()`. No printer errors exist yet.
- `src/print_engineer/adapters/slicer/base.py` — adapter pattern: keyword-only
  constructor params, explicit defaults, structured errors with `details`
  (incl. `path_hint`-style hints).
- `pyproject.toml` — dependencies: fastmcp, httpx, numpy, pydantic,
  pydantic-settings, PyYAML, trimesh. **No MQTT library present.**
- `tests/unit/test_interfaces.py` — pins `PrinterStatus()` defaults
  (UNKNOWN, not connected, None temps/ams) and `AMSInfo(slots=[...])`.
- `tests/unit/test_setup_mcp.py` — hermetic MCP test pattern: `create_server`,
  `fastmcp.Client`, `_call_tool` helper, monkeypatched fakes.
- `tests/unit/test_mcp_server.py` — asserts tool registration with subset
  assertions (`{"system.info", "system.health"} <= names`); adding a tool
  will not break it.
- `tests/integration/test_slicer_cli_probe.py` — integration tests skip
  cleanly when hardware is absent.
- No MQTT, socket, TLS, certificate, or other networking abstractions exist
  anywhere in `src/` (verified by grep for `paho|mqtt|socket|ssl|TLS|certificate`).
- `README.md` — printer integration is **Phase 2+**, not done; MCP plan
  includes `printer.*`.

## External Protocol Evidence

Verified from authoritative community documentation of the reverse-engineered
Bambu LAN protocol (OpenBambuAPI `mqtt.md`, Bambu-Lab-Cloud-API `API_MQTT.md`,
Bambu Studio source, Home Assistant `ha-bambulab`, `bambu-local`, Bambu
forum threads on A1 MQTT):

- **Broker**: `mqtt://{PRINTER_IP}:8883`, MQTT over TLS. Username `bblp`,
  password = the LAN access code shown on the printer display (Settings →
  WLAN → LAN Only Mode). The access code is ~8 characters.
- **Certificate**: the printer presents a self-signed certificate; community
  clients and Bambu Studio connect with certificate verification disabled
  (`ssl.CERT_NONE`) or pinned against Bambu Studio's `printer.cer`. There is
  no trusted public CA.
- **Status topic**: `device/{serial}/report` — the printer publishes a JSON
  telemetry document (retained; full state on connect; updates roughly every
  0.5–2 s). No subscription to any other topic is required for read-only
  status.
- **Command topic**: `device/{serial}/request` — publish-only, NOT needed for
  this increment.
- **Report structure**: top-level object with a `print` object containing
  `gcode_state`, `mc_percent` (0–100), `mc_remaining_time` (minutes),
  `layer_num`, `total_layer_num`, `nozzle_temper`, `nozzle_target_temper`,
  `bed_temper`, `bed_target_temper`, `ams` (list of AMS units, each with a
  `tray` list), `hms`, `ipcam`, `spd_lvl`, etc. Field values are mixed
  int/float/string.
- **`gcode_state` values** (Bambu Studio `GCodeState` enum): `IDLE`,
  `PREPARE`, `RUNNING`, `PAUSE`, `FINISH`, `FAILED`, `UNKNOWN`.
- **Firmware change (post January 2025)**: MQTT *commands* must be signed
  with the extracted Bambu Connect X.509 certificate (RSA-SHA256); unsigned
  commands are rejected (error `84033543`). This affects only state-changing
  operations (future work). Read-only status subscription is unaffected.
- **Concurrency**: the printer accepts only a small number of simultaneous
  MQTT clients (community reports 2–3); Bambu Studio/Handy should be closed
  while a client is connected. Short-lived connections are therefore
  preferable.
- **A1 camera**: JPEG over TLS on port 6000 with a proprietary auth packet —
  a separate protocol from MQTT; NOT part of this increment.

## Requirements

Supported by the user's increment list and the repository evidence above:

1. LAN MQTT (TLS) connection to the Bambu Lab A1 using `bblp`/access-code
   authentication, from `BambuSecrets`/`PrinterConfig`.
2. `Printer` ABC implemented concretely: `connect`, `get_status`,
   `disconnect` work against a real A1; `take_snapshot` and the four
   state-changing methods raise a structured "not supported in this
   read-only increment" error and never touch the printer.
3. Raw Bambu report normalized into the existing `PrinterStatus` model
   (state, temps, targets, progress, AMS); unknown/missing values stay
   `None`/`UNKNOWN` — nothing is fabricated.
4. Hermetic unit tests with a mocked MQTT layer (no real printer, no real
   network).
5. Read-only MCP tool `printer.status` following the established
   `build_tools(settings)` + `{ok, error}` contract.
6. Structured errors with stable machine-readable codes, following the
   `SlicerError` pattern.
7. No state-changing operation, no command signing, no cloud connection, no
   printer discovery, no snapshot, no print history (Phase 3+), no Phase 3B.
8. No MQTT publish of any kind: strictly connect → subscribe/read
   `device/{serial}/report` → normalize → disconnect. No commands and no
   `pushall` requests (decisions 4 and 6).

## Existing Implementation

Reused as-is (no changes):

- `Printer` ABC — the adapter implements it.
- `PrinterStatus`, `PrinterState`, `AMSInfo` — the normalization target.
- `BambuSecrets`, `PrinterConfig`, `Settings` — connection parameters.
- `SlicerError` pattern — model for the new `PrinterError` hierarchy.
- MCP registration pattern in `print_engineer.mcp.server.create_server` and
  the `RecommendTools`/`build_tools` tool-group pattern.
- Test fixtures `tmp_root`/`base_settings` (`tests/conftest.py`) and the
  `test_setup_mcp.py` MCP test pattern.

## Required Changes

### 1. `pyproject.toml` — add dependency

- **File**: `pyproject.toml`
- **Change**: add `"paho-mqtt>=2,<3"` to `[project].dependencies`.
- **Reason**: the repo has no MQTT dependency and no networking abstraction;
  paho-mqtt is the de-facto standard Python MQTT client and is what every
  major Bambu community client (pybambu, ha-bambulab, bambu-local) uses.
  Implementing raw MQTT over TLS sockets would be unjustified new protocol
  code. paho 2.x API requires `mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
  client_id=...)` — the transport wraps this so the API difference is
  contained in one module.

### 2. `src/print_engineer/errors.py` — add printer error hierarchy

- **File**: `src/print_engineer/errors.py`
- **Change**: add, following the `SlicerError` pattern (code + `details` +
  `to_dict()`):
  - `class PrinterError(PrintEngineerError)` — code `printer_error`
  - `class PrinterNotConfigured(PrinterError)` — code `printer_not_configured`
  - `class PrinterUnreachable(PrinterError)` — code `printer_unreachable`
  - `class PrinterAuthFailed(PrinterError)` — code `printer_auth_failed`
  - `class PrinterTimeout(PrinterError)` — code `printer_timeout`
  - `class PrinterInvalidReport(PrinterError)` — code `printer_invalid_report`
  - `class PrinterOperationUnsupported(PrinterError)` — code
    `printer_operation_unsupported`
- **Reason**: the MCP error contract needs stable machine-readable codes for
  printer failures, exactly as slicer errors provide.

### 3. NEW `src/print_engineer/adapters/printer/transport.py` — MQTT transport abstraction

- **File**: NEW `src/print_engineer/adapters/printer/transport.py`
- **Classes/functions**:
  - `class MqttConnectionError(Exception)` with `reason: str` in
    `{"auth", "unreachable"}` — transport-level failure signal.
  - `class MqttClient(Protocol)` — minimal surface the adapter needs:
    `connect() -> None`, `fetch_report(topic: str, timeout_seconds: float)
    -> bytes | None`, `disconnect() -> None`.
  - `class PahoMqttClient` — real implementation wrapping
    `paho.mqtt.client.Client`:
    - `connect()`: TLS (`tls_set(cert_reqs=ssl.CERT_NONE,
      tls_version=ssl.PROTOCOL_TLS_CLIENT)`), `username_pw_set("bblp",
      access_code)`, `client.connect(host, 8883, keepalive)`,
      `loop_start()`; MQTT CONNACK return codes 4/5 → raise
      `MqttConnectionError("auth")`; socket/TLS/timeout failures → raise
      `MqttConnectionError("unreachable")`.
    - `fetch_report(topic, timeout)`: `subscribe(topic, qos=0)`; wait on a
      `threading.Event` set by `on_message` for the first payload; return
      raw bytes or `None` on timeout; `loop_stop()`.
    - `disconnect()`: `disconnect()` + `loop_stop()`.
  - `class MqttClientFactory(Protocol)` — `__call__(*, host, port, username,
    password, client_id) -> MqttClient`; `PahoMqttClientFactory` (stateless)
    returns `PahoMqttClient`. Factory instances are never used as default
    arguments — the adapter constructs the default inside its body
    (decision 7; see item 4).
- **Reason**: hermetic tests require a mockable MQTT layer (requirement 4);
  the paho API is fully contained behind the protocol so the fake is trivial
  and the real client cannot be instantiated in unit tests.

### 4. NEW `src/print_engineer/adapters/printer/bambu.py` — the adapter

- **File**: NEW `src/print_engineer/adapters/printer/bambu.py`
- **Functions/classes**:
  - `def _normalize_status(payload: dict[str, Any]) -> PrinterStatus` —
    pure function; maps a Bambu report (see Status Mapping below). Must not
    raise for missing fields (defaults to `UNKNOWN`/`None`); raises
    `PrinterInvalidReport` only if the payload is not a mapping or `print`
    is not a mapping.
  - `class BambuPrinterAdapter(Printer)`:
    - `__init__(self, *, host: str, serial: str, access_code: str,
      timeout_seconds: float = 10.0, client_factory: MqttClientFactory |
      None = None)` — keyword-only, mirrors `BaseSlicerAdapter` style; pure
      adapter (no `Settings` dependency). Safe dependency-injection default
      construction (decision 7): the default factory is created in the body
      (`self._client_factory = client_factory or PahoMqttClientFactory()`),
      never as a stateful default argument.
    - `connect()` — build client via factory (port 8883, username `bblp`,
      client_id `print-engineer-{serial}`), call `connect()`; translate
      `MqttConnectionError("auth")` → `PrinterAuthFailed` and
      `MqttConnectionError("unreachable")` → `PrinterUnreachable`.
    - `get_status()` — requires a connected client; subscribe topic
      `device/{serial}/report` via `fetch_report(topic,
      self._timeout_seconds)`; `None` → `PrinterTimeout`; parse JSON
      (`json.JSONDecodeError` or non-dict → `PrinterInvalidReport`); return
      `_normalize_status(...)`.
    - `disconnect()` — disconnect the client.
    - `take_snapshot()`, `start_print()`, `stop_print()`, `pause_print()`,
      `resume_print()`, `set_temperature()` — all raise
      `PrinterOperationUnsupported` (message states the operation is not
      supported in the read-only increment); never create/connect a client
      or touch the printer.
- **Reason**: minimal existing-interface → adapter → tests; satisfies the
  ABC concretely while keeping the increment strictly read-only.

### 5. `src/print_engineer/adapters/printer/__init__.py` — export the adapter

- **File**: `src/print_engineer/adapters/printer/__init__.py`
- **Change**: replace the placeholder docstring with exports:
  `from print_engineer.adapters.printer.bambu import BambuPrinterAdapter` and
  `from print_engineer.adapters.printer.transport import (MqttClient,
  MqttClientFactory, PahoMqttClientFactory)`; keep an updated docstring
  ("Bambu Lab LAN MQTT adapter, Phase 2+ (read-only increment)").
- **Reason**: the package is imported by `adapters/__init__.py` and must
  expose the adapter like the slicer/model/llm packages do.

### 6. NEW `src/print_engineer/mcp/tools/printer.py` — `printer.status` tool

- **File**: NEW `src/print_engineer/mcp/tools/printer.py`
- **Classes/functions**:
  - `def _connection_params(settings) -> tuple[str, str, str]` — resolve
    `host = secrets.ip or settings.printer.host`, `serial = secrets.serial
    or settings.printer.serial`, `access_code = secrets.access_code`; any
    missing → raise `PrinterNotConfigured` with `details` listing the
    missing keys.
  - `class PrinterTools`:
    - `__init__(self, settings)`.
    - `status(self) -> dict[str, Any]` — resolve params; build
      `BambuPrinterAdapter(...)`; `connect()` → `get_status()` →
      `disconnect()` in `try/finally`; return
      `{"ok": True, "status": dataclasses.asdict(status)}`; catch
      `PrinterError` → `{"ok": False, "error": exc.to_dict()}`.
  - `def build_tools(settings) -> dict[str, Callable]` — returns
    `{"printer.status": tools.status}`.
- **Reason**: the MCP architecture explicitly plans `printer.*` tools and
  this is the read-only status tool the increment requires.

### 7. `src/print_engineer/mcp/server.py` — register the tool

- **File**: `src/print_engineer/mcp/server.py`
- **Change**: import `printer` from `print_engineer.mcp.tools`; register
  `for name, tool in printer.build_tools(settings).items(): mcp.tool(name=
  name, description=_printer_tool_description(name))(tool)`; add
  `_printer_tool_description(name)` mapping `"printer.status"` to a
  read-only description ("Current Bambu Lab A1 status over LAN MQTT
  (temperatures, state, progress, AMS). Read-only: never sends commands,
  never changes printer state.").
- **Reason**: all tool groups are registered in `create_server`; this is the
  established registration point.

### 8. `src/print_engineer/mcp/tools/__init__.py` — docstring update (minor)

- **File**: `src/print_engineer/mcp/tools/__init__.py`
- **Change**: update the docstring so the "later phases" list reflects that
  `printer.*` (read-only status) is now implemented.
- **Reason**: keeps the module doc accurate; no code change.

### 9. NEW tests (see Test Strategy)

- `tests/unit/test_bambu_printer_adapter.py`
- `tests/unit/test_printer_mcp.py`
- `tests/integration/test_bambu_printer_lan.py`
- **Reason**: requirement 4 (hermetic tests) and the repo convention of
  skippable integration tests. No existing test files are modified
  (`test_mcp_server.py` uses subset assertions; `test_config.py` already
  covers `BambuSecrets`).

## New Files

- `src/print_engineer/adapters/printer/transport.py`
- `src/print_engineer/adapters/printer/bambu.py`
- `src/print_engineer/mcp/tools/printer.py`
- `tests/unit/test_bambu_printer_adapter.py`
- `tests/unit/test_printer_mcp.py`
- `tests/integration/test_bambu_printer_lan.py`

## MQTT Connection Design

- **Transport**: MQTT 3.1.1 over TLS, `{host}:8883`, via paho-mqtt wrapped
  in `PahoMqttClient` (the only module that imports paho).
- **Auth**: username `bblp`, password = LAN access code from
  `BAMBU_ACCESS_CODE` (`.env`).
- **TLS**: `tls_set(cert_reqs=ssl.CERT_NONE, tls_version=ssl.PROTOCOL_TLS_CLIENT)`
  because the printer's certificate is self-signed (externally verified).
  No CA file is shipped. Security note in Risks.
- **Client id**: `print-engineer-{serial}` — unique per adapter instance.
- **Lifecycle**: short-lived, per status call (`connect` → `fetch_report` →
  `disconnect`). This respects the printer's limited concurrent-client
  capacity and avoids holding MQTT sessions open.
- **Topic**: subscribe to `device/{serial}/report` (QoS 0). The report is
  retained, so the first delivered message is the current full state.
- **Publish**: none. The adapter performs no MQTT publish of any kind — no
  commands and no `pushall` requests on `device/{serial}/request`
  (decisions 4 and 6). Lifecycle is strictly connect → subscribe/read →
  normalize → disconnect.
- **Timeouts**: connect failure / TLS handshake → `PrinterUnreachable`;
  CONNACK rc 4/5 → `PrinterAuthFailed`; no report within
  `timeout_seconds` (default 10.0) → `PrinterTimeout`.

## Status Mapping

Raw report: `{"print": {..., "gcode_state": ..., "mc_percent": ...,
"nozzle_temper": ..., "nozzle_target_temper": ..., "bed_temper": ...,
"bed_target_temper": ..., "ams": {"ams": [{"id": "0", "tray": [...]}],
"ams_exist_bits": ...}, ...}}`.

| `PrinterStatus` field | Bambu source | Transformation |
| --- | --- | --- |
| `state` | `print.gcode_state` | `IDLE→IDLE`, `RUNNING→PRINTING`, `PREPARE→PRINTING`, `PAUSE→PAUSED`, `FINISH→IDLE`, `FAILED→ERROR`, `UNKNOWN→UNKNOWN`, unknown/missing→`UNKNOWN` |
| `is_connected` | — | `True` whenever a report was received |
| `bed_temp` | `print.bed_temper` | float, `None` if missing/unparseable |
| `nozzle_temp` | `print.nozzle_temper` | float, `None` if missing/unparseable |
| `target_bed_temp` | `print.bed_target_temper` | float, `None` if missing/unparseable |
| `target_nozzle_temp` | `print.nozzle_target_temper` | float, `None` if missing/unparseable |
| `progress` | `print.mc_percent` (0–100) | `round(mc_percent / 100.0, 4)` → 0.0–1.0 (matches the scale implied by `test_interfaces.py` `progress=0.5`); `None` if missing |
| `ams` | `print.ams` | `AMSInfo(is_connected=bool(ams), slots=[...])`; `slots` = `chr(65 + unit_index) + str(tray_index + 1)` for each loaded tray (tray dict has keys beyond `id`, and tray `id` != `"254"` external-spool sentinel); `None` if no AMS data |

Fixed contract for this increment (decisions 1 and 2): `mc_percent` (0–100)
is normalized to `progress` 0.0–1.0, and `gcode_state` maps exactly as the
table above. The mapping is pinned by unit tests; it changes only as a
deliberate contract change after hardware verification. Missing or
unparseable values are never guessed.

## Error Handling

- `PrinterNotConfigured` (`printer_not_configured`) — missing host/serial/
  access code at tool level; `details` lists the missing keys.
- `PrinterUnreachable` (`printer_unreachable`) — TCP/TLS connect failure,
  refused, DNS, handshake; `details` includes host/port and a short reason.
- `PrinterAuthFailed` (`printer_auth_failed`) — MQTT CONNACK rc 4/5 (wrong
  access code); `details` hints to check `BAMBU_ACCESS_CODE` / LAN mode.
- `PrinterTimeout` (`printer_timeout`) — no report within
  `timeout_seconds`; `details` includes the topic and timeout.
- `PrinterInvalidReport` (`printer_invalid_report`) — payload is not JSON,
  not a dict, or `print` is not a dict; `details` includes a truncated
  payload.
- `PrinterOperationUnsupported` (`printer_operation_unsupported`) — any
  state-changing/camera operation in this increment; never touches the
  printer. `take_snapshot` explicitly raises this error; camera integration
  is a separate future increment (decision 5).
- All carry `code`, `message`, `details`, and `to_dict()`, and surface
  through the MCP contract as `{"ok": false, "error": {...}}`, exactly like
  slicer errors.

## MCP Interface

- **Tool**: `printer.status` (no arguments).
- **Success**: `{"ok": true, "status": {state, is_connected, bed_temp,
  nozzle_temp, target_bed_temp, target_nozzle_temp, progress, ams}}`
  (from `dataclasses.asdict(PrinterStatus)`).
- **Failure**: `{"ok": false, "error": {code, message, details}}` with one
  of the `printer_*` codes above.
- **Registration**: `create_server` via `printer.build_tools(settings)`,
  mirroring `slicer`/`model`/`recommend`; description via
  `_printer_tool_description`.
- **Concurrency**: sync tool (like all existing tools); paho is
  synchronous. The 10 s default report timeout bounds the blocking window;
  fastmcp runs sync tools on an executor.

## Test Strategy

Hermetic unit tests (no network, no printer — the MQTT layer is a fake):

- `tests/unit/test_bambu_printer_adapter.py` — `FakeMqttClient` implements
  `MqttClient` (records `connect` args and subscribed topic; returns a
  canned payload from `fetch_report`; can be configured to raise
  `MqttConnectionError("auth")`/`("unreachable")` or return `None`):
  - table-driven `gcode_state` → `PrinterState` for all 7 documented values
    plus missing/unknown;
  - temperature/progress/AMS mapping, including missing fields → `None`,
    string-valued numerics, `vt_tray` (id `"254"`) excluded from slots;
  - non-JSON / non-dict payload → `PrinterInvalidReport`;
  - auth failure → `PrinterAuthFailed`; unreachable → `PrinterUnreachable`;
    `fetch_report` → `None` → `PrinterTimeout`;
  - subscribed topic is exactly `device/{serial}/report`; client_id is
    `print-engineer-{serial}`;
  - `take_snapshot` and the four state-changing methods raise
    `PrinterOperationUnsupported` and never create a client;
  - connect/disconnect lifecycle.
- `tests/unit/test_printer_mcp.py` — mirror `test_setup_mcp.py`: assert
  `printer.status` is in the registered tool list; monkeypatch the adapter
  factory in `print_engineer.mcp.tools.printer` with a fake adapter to
  assert the success payload; assert `printer_not_configured` when no
  `BAMBU_*` env vars and no YAML printer config are set; assert
  `printer_unreachable`/`printer_auth_failed` error payloads via the fake.
- `tests/integration/test_bambu_printer_lan.py` — skip cleanly (pytest.skip)
  unless `BAMBU_IP`, `BAMBU_SERIAL`, and `BAMBU_ACCESS_CODE` are set
  (mirrors `test_slicer_cli_probe.py`); then `connect` → `get_status` and
  assert a valid `PrinterState` and `is_connected` are returned.

Existing tests are unchanged. Note: the full suite has one pre-existing,
unrelated failure (`test_print_context.py::TestResolvePrinter::
test_ambiguous_prefix_match_raises`, reproduced on pristine HEAD `3294bb1`)
that must not be attributed to this work.

## Hardware Verification

Required after implementation, on a real Bambu Lab A1 (deferred, cannot be
done in this planning phase):

1. Enable LAN (LAN Only) mode on the printer; record IP, serial, access code.
2. Close Bambu Studio/Handy (concurrent-client limit).
3. Verify TLS connection with `CERT_NONE` works on the current firmware.
4. Verify `device/{serial}/report` delivers the full retained report on
   subscribe.
5. Verify field names/types (`gcode_state`, `mc_percent`, temperatures,
   `ams` structure) against the A1 firmware — especially whether A1 sends
   the full report or partial deltas (P1-series behavior documented in
   OpenBambuAPI). If the retained report is insufficient, record that as a
   follow-up task rather than expanding this implementation (decision 4).
6. Verify `gcode_state` values actually emitted by the A1 and that
   `mc_percent` is 0–100.
7. Verify AMS tray structure (loaded vs. empty trays, `vt_tray` id 254).
8. Verify timeout behavior when the printer is powered off/unreachable.
9. Confirm the access-code authentication failure path (wrong code → rc 4/5).

## Data Flow

```
MCP client (OpenCode)
  → printer.status tool (PrinterTools.status)
    → _connection_params(settings)   # BAMBU_* secrets + printer YAML; else PrinterNotConfigured
    → BambuPrinterAdapter(host, serial, access_code, timeout_seconds)
      → connect(): PahoMqttClientFactory → PahoMqttClient (TLS 8883, bblp/access_code)
      → get_status(): fetch_report("device/{serial}/report", timeout)
          → raw bytes → JSON dict
          → _normalize_status(dict) → PrinterStatus
      → disconnect()
  → {"ok": true, "status": asdict(PrinterStatus)}
    | → PrinterError → {"ok": false, "error": {code, message, details}}
```

## Implementation Order

1. `pyproject.toml`: add `paho-mqtt>=2,<3`; `uv sync`.
2. `errors.py`: add the `PrinterError` hierarchy.
3. `transport.py`: `MqttConnectionError`, `MqttClient` protocol,
   `PahoMqttClient`, `MqttClientFactory`/`PahoMqttClientFactory`.
4. `bambu.py`: `_normalize_status` + `BambuPrinterAdapter` (constructor
   takes `client_factory: MqttClientFactory | None = None` and constructs
   the default in the body — decision 7); update
   `adapters/printer/__init__.py` exports.
5. `tests/unit/test_bambu_printer_adapter.py`; run it — hermetic, no network.
6. `mcp/tools/printer.py` (`PrinterTools.status`, `build_tools`);
   register in `mcp/server.py` (+ `_printer_tool_description`);
   update `mcp/tools/__init__.py` docstring.
7. `tests/unit/test_printer_mcp.py`; run it.
8. `tests/integration/test_bambu_printer_lan.py` (skippable).
9. Run the full unit suite, `ruff check src tests`, `mypy src tests`.
10. Hardware verification against the real A1 (deferred; per section above).

## Risks

- **Self-signed TLS certificate**: connecting with `CERT_NONE` disables
  certificate verification. Acceptable on a LAN-only device, but the
  connection is not authenticated against a pinned cert; a rogue device on
  the LAN could impersonate the printer. Mitigation option (later): pin
  Bambu Studio's `printer.cer`. Flagged for the user.
- **Firmware-version variance**: post-Jan-2025 firmware requires signed
  commands — this does not affect read-only subscription, but future
  state-changing increments must implement X.509 signing (error `84033543`).
- **A1 report behavior**: if the A1 (P1-like) emits partial delta reports
  instead of a full report on subscribe, fields may be `None`. The
  normalization is defensive. `pushall` is explicitly NOT implemented
  (decision 4): if the retained report is insufficient on real hardware,
  the finding is recorded as a follow-up task.
- **Concurrent MQTT clients**: the printer supports only ~2–3 simultaneous
  MQTT clients. The connect-per-status-call design minimizes session
  duration; Bambu Studio/Handy must be closed during verification.
- **paho 2.x API**: `CallbackAPIVersion` is required; contained within
  `transport.py`.
- **Blocking MCP tool**: `printer.status` blocks up to `timeout_seconds`
  (10 s default) in the fastmcp executor; acceptable for a status poll.
- **Status-mapping contract**: `progress` normalization (0.0–1.0) and the
  `gcode_state` table are fixed for this increment (decisions 1–2) and
  pinned by tests; any post-hardware correction is a deliberate contract
  change.

## Out of Scope

- All state-changing operations: `start_print`, `stop_print`, `pause_print`,
  `resume_print`, `set_temperature` — raise `PrinterOperationUnsupported`;
  no command signing (X.509), no `device/{serial}/request` publishes.
- `take_snapshot` — camera is a separate protocol (port 6000, proprietary
  auth); raises `PrinterOperationUnsupported` in this increment; camera
  integration is a separate future increment (decision 5).
- Cloud MQTT (`us.mqtt.bambulab.com`) and Bambu account login.
- Printer discovery (mDNS/scanning); FTPS (port 990) file transfer;
  `pushing.pushall` requests — explicitly NOT implemented (decision 4);
  if hardware verification shows the retained report is insufficient,
  record a follow-up task instead of expanding this implementation.
- Print history / learning (Phase 3+); Phase 3B (undefined); any change to
  Phase 3A.1 code, tests, or existing plans.
- No change to `SafetyPolicy`/`PermissivePolicy` (no state-changing actions
  exist to gate); the ABC's policy note becomes relevant in the next
  increment.

## Open Questions

Only genuinely unresolved hardware questions remain. All behavior questions
are decided (decisions 1–8) and fixed for this increment:

1. **Retained report sufficiency**: on real A1 firmware, does subscribing to
   `device/{serial}/report` deliver a complete report immediately (retained
   message), or only partial deltas (P1-series behavior)? If insufficient,
   record a follow-up task — do not expand this implementation (decision 4).
2. **Field conformance**: do the real A1 report field names, value types,
   and `gcode_state` values match the externally documented set assumed by
   the fixed mapping contract (Status Mapping table)?
3. **TLS handshake**: does `CERT_NONE` connection succeed on the current A1
   firmware (self-signed certificate)?
4. **AMS structure**: does the real A1 AMS payload match the assumed
   `ams.ams[].tray[]` layout and the loaded-tray / `vt_tray` (id `"254"`)
   heuristics in the `ams` mapping?

## Final Verdict

IMPLEMENTATION CHANGES REQUIRED

PLAN ONLY — no source or test files were modified.