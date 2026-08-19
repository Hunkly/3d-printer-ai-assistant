# Phase 2 — Bambu A1 Read-Only Hardware Verification

## Status

APPROVED

## Understanding

Perform the first physical-printer verification of the existing Bambu Lab A1 LAN MQTT status integration. The verification must use the real `BambuPrinterAdapter` and the existing Paho transport, and must remain strictly:

`connect → subscribe/read device/{serial}/report → normalize → disconnect`

The increment verifies hardware behavior; it does not authorize implementation changes during the run. If subscription-only telemetry is insufficient, record that outcome as a read-only limitation rather than sending a request.

## Existing Verified Software

The approved software increment in `plans/phase-2-printer-read-only.md` already provides and hermetically verifies:

- Paho MQTT 2.x transport with TLS, LAN access-code authentication, and Callback API `VERSION2`
- a minimal `MqttClient` interface containing only `connect`, `fetch_report`, and `disconnect`
- `BambuPrinterAdapter` standalone lifecycle and structured error translation
- normalization into `PrinterStatus`, `PrinterState`, and `AMSInfo`
- `printer.status` MCP configuration precedence and serialization
- lifecycle cleanup on success and failure paths
- unsupported control operations that raise without contacting a printer
- zero MQTT publish paths
- 80 focused unit tests plus focused Ruff and Mypy checks

This plan does not reopen those software contracts.

## Hardware Unknowns

The following can be established only against a real A1:

1. Whether current firmware accepts the configured TLS handshake.
2. Whether access-code authentication succeeds over LAN MQTT.
3. Whether subscribing to `device/{serial}/report` produces a usable report without a publish or request.
4. Actual report field presence and value types.
5. Actual `gcode_state` values.
6. Actual AMS payload structure, including empty trays and the external-spool sentinel.
7. Whether the existing normalization produces a useful `PrinterStatus` from a real report.
8. Whether the real connection closes cleanly after success and each surfaced failure.

## Safety Boundary

The verification may only:

- establish a LAN MQTT connection to the explicitly configured printer
- authenticate with the LAN access code
- subscribe to `device/{serial}/report`
- receive one status report
- normalize it through the existing adapter
- disconnect

The verification must never:

- call MQTT publish
- use `device/{serial}/request`
- send `pushall`
- start, stop, pause, or resume a print
- change temperatures or printer settings
- upload a file or use FTPS
- use the camera protocol
- use cloud MQTT or Bambu account authentication
- sign commands
- slice or print automatically

**ZERO MQTT PUBLISH PATHS remain mandatory.** Failure to obtain usable state by subscription alone is evidence, not permission to publish.

## Configuration / Secrets

Use process environment variables only for the hardware run:

- `BAMBU_IP`
- `BAMBU_SERIAL`
- `BAMBU_ACCESS_CODE`

Add an explicit opt-in gate:

- `RUN_BAMBU_LAN_HARDWARE_TEST=1`

The integration test must skip unless the opt-in value is exactly `1`. After opt-in, it must skip with a generic reason if any required `BAMBU_*` variable is absent or empty. This two-part gate prevents an existing local `.env` from contacting hardware during ordinary test runs.

Read values without printing or embedding them in assertion messages. Never include the IP, serial, or access code in test IDs, logs, captured output, plan artifacts, exception snapshots, or committed fixtures. In particular, do not print `PrinterUnreachable.details` during evidence collection because it includes the host.

## Required Changes

No production source, configuration model, transport, adapter, MCP, or unit-test changes are required.

Create only:

- `tests/integration/test_bambu_printer_lan.py`

The approved software plan already anticipated this hardware-gated integration-test path. Do not add dependencies or another MQTT implementation.

## Integration Test Design

Create one deliberately hardware-gated test using the real `BambuPrinterAdapter`:

1. Check `RUN_BAMBU_LAN_HARDWARE_TEST == "1"`; otherwise call `pytest.skip`.
2. Read `BAMBU_IP`, `BAMBU_SERIAL`, and `BAMBU_ACCESS_CODE` from the process environment.
3. If any required value is missing or empty, skip with a message listing only variable names, never values.
4. Construct `BambuPrinterAdapter(host=..., serial=..., access_code=..., timeout_seconds=10.0)`.
5. Call `adapter.get_status()` so the real standalone lifecycle builds the client, connects, fetches one report, normalizes it, and disconnects in its existing `finally` block.
6. Also call `adapter.disconnect()` in the test's outer `finally`; it is idempotent and protects cleanup if future adapter behavior changes without duplicating connection ownership.
7. Assert only stable normalized-model invariants:
   - result is a `PrinterStatus`
   - `is_connected is True`
   - `state` is a `PrinterState`
   - `progress is None` or `0.0 <= progress <= 1.0`
   - each temperature field is `None` or `float`
   - `ams is None` or is an `AMSInfo` whose `is_connected` is `bool` and whose slots are strings

Do not assert a particular state, temperature, progress value, AMS presence, number of slots, or printing condition. Do not print the raw report. Keep this test separate from unit tests so ordinary `tests/unit/` execution remains hermetic.

Before approval and execution, review the test diff for `publish`, `/request`, `pushall`, and state-changing adapter calls. Any reachable write path blocks the hardware run.

## Manual Hardware Verification Procedure

1. On the printer, enable LAN/Local Network access as required to obtain the current LAN access code. Do not change printing, temperature, or motion state.
2. Ensure the workstation and printer are on the intended trusted LAN. Close Bambu Studio, Bambu Handy sessions where applicable, and other MQTT clients to reduce the printer's limited concurrent-client pressure.
3. In a private PowerShell session, set `BAMBU_IP`, `BAMBU_SERIAL`, and `BAMBU_ACCESS_CODE` as process-scoped environment variables. Do not paste them into shell history, committed scripts, issue text, chat, screenshots, or terminal transcripts. Set `RUN_BAMBU_LAN_HARDWARE_TEST=1` only immediately before the run.
4. Run only:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/integration/test_bambu_printer_lan.py
   ```

5. Permit pytest to display only the test node, pass/skip/fail result, elapsed time, and sanitized classification. Do not add verbose connection logging and do not display exception detail mappings containing host information.
6. Success requires TLS/authentication, receipt of a subscription-only report, successful normalization, all stable invariants passing, and clean return from the adapter lifecycle.
7. Classify any failure using the categories below. Do not change code, retry with commands, or expand protocol behavior during this run.
8. After the run, confirm the process returned, no MQTT loop/thread remains active, and no second connection is held. Clear the four process environment variables or close the private shell.

Prefer this integration test over an ad-hoc Python or MQTT probe. Do not invoke `printer.status` as a second hardware call during the first verification.

## Success Criteria

The first hardware verification passes only when:

- the explicitly opted-in test reaches the intended LAN printer
- TLS and access-code authentication succeed
- subscription to the report topic yields a payload within the configured timeout
- the existing parser and normalizer return `PrinterStatus`
- normalized values satisfy the conservative model invariants
- disconnect/cleanup completes without a surfaced error
- review confirms no publish, request-topic, or command path was used
- no secret or unnecessary raw telemetry was emitted or stored

Passing proves compatibility only for the tested printer, firmware, LAN, and moment in time.

## Failure Classification

- **PASS** — TLS/authentication succeeds, a report arrives, normalization succeeds, invariants pass, and cleanup completes.
- **AUTH FAILURE** — `PrinterAuthFailed` / `printer_auth_failed`; the printer rejected authentication. Recheck LAN mode and the access code privately before a later approved retry.
- **UNREACHABLE** — `PrinterUnreachable` / `printer_unreachable`; TCP, TLS, DNS/routing, broker connection, or other connection establishment failed.
- **TIMEOUT** — `PrinterTimeout` / `printer_timeout`; connection completed but no report arrived within the timeout.
- **INVALID REPORT** — `PrinterInvalidReport` / `printer_invalid_report`; bytes arrived but were not valid UTF-8 JSON or did not contain the required mapping structure.
- **SCHEMA MISMATCH** — normalization succeeds only partially or the normalized result shows that real field names/types differ from approved assumptions; record which normalized fields were absent or unexpected without storing raw values.
- **AMS MISMATCH** — real AMS behavior cannot be represented by the approved `AMSInfo` assumptions or produces implausible sanitized slot metadata.
- **READ-ONLY LIMITATION** — subscription alone does not provide sufficient initial state. Do not introduce `pushall` or the request topic.
- **CLEANUP FAILURE** — the test returns status or another classification but disconnect/loop cleanup surfaces an error or leaves a client active.

These outcomes are findings only. None authorizes an implementation change during hardware verification.

## Evidence Capture

Record a small operator report containing:

- date/time and a non-identifying firmware label if known
- exact test command
- pytest result and elapsed time
- classification above
- structured error class and code, but only sanitized details
- normalized state value
- whether each temperature field was populated, not necessarily its exact value
- whether progress was populated and whether it was within range
- whether AMS was detected and the count of normalized slots
- whether cleanup completed

Never record the access code, IP, serial, complete environment, credentials, full exception details, or raw MQTT payload.

If schema diagnosis requires information beyond normalized output, stop. Before any storage, define a separate approved diagnostic procedure that processes the payload in memory into an allowlisted summary containing only field names, value type names, collection lengths, recognized `gcode_state`, and AMS nesting shape. It must remove identifiers, network data, HMS/device metadata, file/job names, and all unapproved values; the raw payload must not be written to disk or copied into reports. Do not add that diagnostic capture to this first increment.

## Cleanup / Connection Lifecycle

The real path under test is the adapter's standalone lifecycle:

`build client → connect → subscribe/fetch → parse/normalize → disconnect in finally`

The integration test wraps `get_status()` with an outer `try/finally` calling the adapter's idempotent `disconnect()` as a final safeguard. Cleanup is required for PASS, authentication failure, unreachable connection, timeout, invalid report, assertion failure, and interruption.

Do not hold a persistent connection, poll repeatedly, or run multiple hardware tests concurrently. One status retrieval is sufficient for this increment.

## Test Commands

Before hardware execution, run the hermetic regression and static checks:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
.\.venv\Scripts\ruff.exe check tests/integration/test_bambu_printer_lan.py src/print_engineer/adapters/printer/
.\.venv\Scripts\python.exe -m mypy tests/integration/test_bambu_printer_lan.py src/print_engineer/adapters/printer/
```

Verify the integration test skips safely without opt-in:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/integration/test_bambu_printer_lan.py
```

Only after the plan and test are reviewed and the operator explicitly prepares the printer/environment, run the same integration-test command with the four process environment variables set in the private shell. Do not run the broader integration suite because it probes unrelated local software.

## Risks

- The printer uses a self-signed TLS certificate under the approved transport contract; the LAN connection is encrypted but not authenticated through a public CA or pinned certificate.
- A1 firmware may differ from the reverse-engineered report assumptions.
- The retained/subscription report may be partial or delayed, yielding a timeout or sparse normalized status.
- The printer permits few simultaneous MQTT clients; other Bambu applications may cause connection failures.
- Error details can include the configured host; careless verbose output could disclose it.
- `get_status()` performs a real network connection whenever the explicit gate and credentials are present; the opt-in must remain mandatory.
- A single successful run does not prove compatibility across firmware versions or all printer states.

## Out of Scope

- changes to production transport, adapter, normalization, errors, MCP, configuration, or unit tests
- MQTT publish, `device/{serial}/request`, or `pushall`
- printer control, temperature changes, motion, or settings changes
- command signing
- upload, FTPS, camera, cloud MQTT, or Bambu account login
- slicing or printing
- repeated polling, performance/load testing, discovery, or multiple-printer testing
- capturing or persisting raw reports
- correcting any hardware mismatch during the verification run

## Follow-up Rules

Hardware findings may justify a **separate future proposed plan** after the evidence is sanitized and reviewed. This verification increment itself must not introduce MQTT publishing, request-topic usage, printer control, or speculative parser changes.

- For `AUTH FAILURE` or `UNREACHABLE`, resolve environment/LAN setup separately before proposing code changes.
- For `TIMEOUT` or `READ-ONLY LIMITATION`, record that subscription-only status was insufficient. Do not propose `pushall` within this increment.
- For `INVALID REPORT`, `SCHEMA MISMATCH`, or `AMS MISMATCH`, create a separate diagnostic or compatibility plan based on sanitized evidence.
- For `CLEANUP FAILURE`, stop further hardware attempts and plan a focused lifecycle correction.
- Repeat hardware verification only after the relevant follow-up plan is approved.

## Implementation Order

1. Review and approve this plan.
2. Create only `tests/integration/test_bambu_printer_lan.py` with the explicit opt-in and credential gates.
3. Review the test source for secrets exposure, publish/request paths, state-changing calls, and guaranteed cleanup.
4. Run the existing focused hermetic printer tests.
5. Run focused Ruff and Mypy including the new integration test.
6. Run the integration test without opt-in and verify a clean skip.
7. Obtain explicit operator approval for the one-time physical run.
8. Prepare the printer, private process environment, and concurrent-client conditions.
9. Run the single hardware-gated test once.
10. Clear credentials, confirm cleanup, and record only sanitized evidence.
11. Classify the outcome without modifying implementation.
12. If necessary, create a separate proposed follow-up plan; do not continue automatically.

## Final Verdict

IMPLEMENTATION CHANGES REQUIRED

The software integration is already implemented and verified. This increment requires only the new explicitly gated integration test and its one-time manual execution procedure; no production changes are required.

PLAN ONLY — no source or test files were modified.

## Hardware Verification Result

- **RESULT: PASS**
- Real Bambu Lab A1 hardware was used.
- Explicit `RUN_BAMBU_LAN_HARDWARE_TEST=1` opt-in was used.
- The LAN MQTT/TLS connection and LAN access-code authentication succeeded.
- Subscription to `device/{serial}/report` succeeded, and a telemetry report was received without MQTT publishing.
- No `device/{serial}/request` or `pushall` was used.
- The real report normalized successfully to `PrinterStatus`, and the conservative integration-test assertions passed.
- Connection cleanup and disconnect completed.
- No printer state-changing operation was performed.
- Credentials were removed from the shell environment afterward.
- No raw telemetry or credentials were recorded.
- Optional field and schema cases not present during this single run remain observationally unverified, including AMS-specific payload details if AMS telemetry was absent.
