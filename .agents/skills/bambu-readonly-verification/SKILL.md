---
name: bambu-readonly-verification
description: Verify or review the Bambu Lab A1 read-only LAN MQTT integration, including transport safety, configuration, lifecycle, normalization, MCP contracts, and hermetic tests. Use for read-only printer verification where zero MQTT publish paths is the core invariant.
---

# Verify Bambu Read-Only Integration

## Purpose

Provide a reusable procedure for reviewing or testing the Bambu Lab A1 read-only LAN MQTT integration. Do not implement printer control.

The core invariant is: **ZERO MQTT PUBLISH PATHS.**

## Scope

Apply this skill to:

- `src/print_engineer/adapters/printer/transport.py`
- `src/print_engineer/adapters/printer/bambu.py`
- `src/print_engineer/adapters/printer/__init__.py`
- `src/print_engineer/mcp/tools/printer.py`
- related printer errors
- printer MCP registration
- printer unit and integration tests

Use the approved plan as the authoritative behavioral contract when one exists.

## Safety boundary

Allow read-only functionality to establish a LAN MQTT connection, authenticate, subscribe to telemetry, receive reports, normalize status, expose read-only MCP status, and disconnect.

Do not allow read-only functionality to:

- call MQTT publish or expose `publish()` through the minimal transport protocol
- use `device/{serial}/request` or send `pushall`
- start, stop, pause, or resume a print
- change temperatures, printer settings, or other printer state
- upload print jobs or use FTPS
- use the camera protocol or cloud MQTT
- authenticate to a Bambu account
- automatically slice or print

Require unsupported printer operations to raise `PrinterOperationUnsupported` without contacting the printer.

## Transport contract

Require the minimal read-only client interface:

- `connect()`
- `fetch_report(topic, timeout_seconds)`
- `disconnect()`

Reject any publish operation in the read-only transport abstraction.

For the Paho implementation, verify against the approved plan:

- TLS port `8883`
- username `bblp`
- password equal to the LAN access code
- client ID `print-engineer-{serial}`
- status topic `device/{serial}/report`
- Paho Callback API `VERSION2`
- TLS behavior matching the approved implementation contract

Do not re-investigate approved Paho callback signatures unless the installed dependency version changed or the implementation no longer matches the approved contract.

## Configuration contract

Verify this precedence:

- host: `settings.secrets.ip`, then `settings.printer.host`
- serial: `settings.secrets.serial`, then `settings.printer.serial`
- access code: `settings.secrets.access_code`

Require missing parameters to produce `PrinterNotConfigured` with `details` containing the missing keys.

Require tests to exercise the real configuration-resolution code. A fake adapter configured to raise `PrinterNotConfigured` does not prove resolution.

## Connection lifecycle

For standalone status retrieval require:

`build client → connect → fetch report → disconnect`

Verify disconnect after:

- success
- authentication failure
- unreachable connection
- timeout
- invalid UTF-8
- invalid JSON
- invalid report structure
- normalization failure where applicable

Prove cleanup with a fake transport or factory. Do not require physical hardware.

## Connection errors

Verify these mappings:

- `MqttConnectionError("auth")` → `PrinterAuthFailed`
- other connection or unreachable failures → `PrinterUnreachable`
- no report before timeout → `PrinterTimeout`
- malformed report → `PrinterInvalidReport`
- missing configuration → `PrinterNotConfigured`
- unsupported operation → `PrinterOperationUnsupported`

Verify machine-readable `code`, `message`, and `details` remain intact through the MCP response.

## Status normalization

Verify this fixed mapping:

- `IDLE` → `PrinterState.IDLE`
- `RUNNING` → `PrinterState.PRINTING`
- `PREPARE` → `PrinterState.PRINTING`
- `PAUSE` → `PrinterState.PAUSED`
- `FINISH` → `PrinterState.IDLE`
- `FAILED` → `PrinterState.ERROR`
- `UNKNOWN` → `PrinterState.UNKNOWN`

Also require `UNKNOWN` for missing, non-string, or unrecognized `gcode_state`. Do not fabricate another state.

## Progress normalization

Use `print.mc_percent` and require:

```python
round(float(mc_percent) / 100.0, 4)
```

Examples: `50 → 0.5`, `"25.5" → 0.255`, and missing or unparseable values → `None`. Do not clamp or invent progress unless an approved plan changes the contract.

## Temperature normalization

Verify:

- `print.bed_temper` → `bed_temp`
- `print.nozzle_temper` → `nozzle_temp`
- `print.bed_target_temper` → `target_bed_temp`
- `print.nozzle_target_temper` → `target_nozzle_temp`

Allow numeric strings, integers, and floats to normalize to `float`. Require missing or unparseable values to become `None`. Do not fabricate temperatures.

## AMS normalization

Verify the approved conceptual output `AMSInfo(is_connected=..., slots=[...])`.

Require slot naming by unit and tray index: unit `0`, tray `0` → `A1`; unit `0`, tray `1` → `A2`; and so on.

Verify:

- loaded trays are included
- trays containing only `id` metadata are excluded
- tray ID `"254"`, the external-spool sentinel, is excluded
- missing or absent AMS data returns `None`

If hardware evidence later disproves the assumed AMS schema, record a follow-up requirement. Do not silently redesign parsing during read-only verification.

## Report validity

Require the payload to decode as UTF-8 and parse as JSON. Require the JSON root and `print` value to be mappings. Invalid values must produce `PrinterInvalidReport`.

Keep individual missing telemetry fields as `UNKNOWN` or `None` rather than invalidating an otherwise valid report, as required by the approved contract.

## MCP contract

Verify `printer.status` exists and follows the existing tool-group and `build_tools` architecture.

Require success to contain only:

```json
{
  "ok": true,
  "status": {
    "state": "<string>",
    "is_connected": "<bool>",
    "bed_temp": "<float|null>",
    "nozzle_temp": "<float|null>",
    "target_bed_temp": "<float|null>",
    "target_nozzle_temp": "<float|null>",
    "progress": "<float|null>",
    "ams": "<object|null>"
  }
}
```

When present, require AMS to contain `is_connected` as a boolean and `slots` as a list.

Require structured failures:

```json
{
  "ok": false,
  "error": {
    "code": "...",
    "message": "...",
    "details": "..."
  }
}
```

Do not add unrelated fields.

## Test quality requirements

Require hermetic unit tests using an injected fake `MqttClient`, fake `MqttClientFactory`, or equivalent transport. Do not connect to a printer, broker, internet, or LAN.

Require coverage for:

- configuration: missing host, serial, and access code; secret overrides; YAML host and serial fallback
- client construction: host, port `8883`, username `bblp`, access-code password, and client ID
- every fixed state plus missing, non-string, and unrecognized state
- numeric, numeric-string, missing, and invalid progress
- numeric, numeric-string, missing, and invalid temperatures
- loaded, metadata-only, ID `254`, and absent AMS trays
- auth, unreachable, timeout, invalid UTF-8, invalid JSON, non-object JSON, and missing or invalid `print`
- disconnect on success and all failure paths
- `start_print`, `stop_print`, `pause_print`, `resume_print`, `set_temperature`, and `take_snapshot` raising `PrinterOperationUnsupported`
- unsupported operations creating or connecting no MQTT client

## No-publish regression guard

Explicitly search relevant printer code for `publish`, `/request`, `pushall`, print, pause, resume, and temperature commands. Inspect each match; a textual match alone is not a violation.

Treat any reachable MQTT publish path as a blocking safety failure. Require the fake `MqttClient` protocol used in tests not to expose `publish()`.

## Hardware integration tests

Keep hardware tests separate from unit tests and skip them cleanly when required Bambu environment variables are absent. Permit only approved read-only operations.

Never infer hardware compatibility from unit tests. Record hardware-only uncertainties such as retained-report completeness, current firmware TLS behavior, exact field types, or AMS schema.

If status telemetry is insufficient on hardware, record a follow-up requirement. Do not introduce `pushall` or request-topic publishing.

## Verification workflow

1. Read `AGENTS.md`.
2. Read the relevant `APPROVED` plan.
3. Inspect Git status and diff.
4. Inspect the transport interface.
5. Search for accidental publish or request paths.
6. Inspect adapter normalization and lifecycle.
7. Inspect MCP configuration resolution.
8. Inspect unit tests for genuine coverage.
9. Run focused tests.
10. Run focused Ruff.
11. Run focused Mypy.
12. Report hardware behavior as unverified unless hardware testing occurred.

Use the project virtual environment. On Windows, adapt these commands only when repository layout requires it:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
.\.venv\Scripts\ruff.exe check src/print_engineer/adapters/printer/ src/print_engineer/mcp/tools/printer.py tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
.\.venv\Scripts\python.exe -m mypy src/print_engineer/adapters/printer/ src/print_engineer/mcp/tools/printer.py tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
```

## Review result

Classify each important requirement as `PASS`, `FAIL`, `PARTIAL`, or `UNKNOWN`. Safety violations are always `FAIL`.

Prevent approval for a blocking `UNKNOWN` involving publishing, lifecycle cleanup, authentication, configuration, or status correctness.

## Completion

Verify only the read-only increment. Do not treat this skill as authorization for printer control, command signing, upload or print workflow, camera, FTPS, cloud connectivity, history, or automatic printing. Those require separate approved plans.
