# Phase 2 — Retained Status Fast Refresh

## Status

APPROVED

## Understanding

Correct retained-session refresh latency without changing cold-start behavior or any telemetry semantics. A retained adapter call must choose its behavior from accumulator state at call entry:

- an empty current-session accumulator uses the existing bounded cold-start collection algorithm;
- an accumulator that already contains any structurally valid current-session report waits for exactly one new structurally valid report, applies it, and returns immediately.

The increment remains passive and read-only. It changes neither transport delivery nor `PrinterStatus`, accumulator merge rules, standalone lifecycle, or MCP lifetime.

## Real Hardware Evidence

The committed accumulator increment (`ded74cf`) was verified against a real Bambu Lab A1 while printing. Three retained snapshots arrived at approximately `+10.016s`, `+20.032s`, and `+30.047s`. State remained `UNKNOWN`, while progress and current temperatures were valid, retained, and updated.

This proved the accumulator correct but also showed that every retained `get_status()` call repeated the full cold readiness wait when no recognized `gcode_state` appeared. Passive traffic continued during the session, so the repeated delay is a usability issue in retained-call selection, not a transport failure or accumulator regression.

The observed approximately two-second passive cadence is evidence only. No implementation or test may encode it as a guarantee.

## Root Cause

`BambuPrinterAdapter._fetch_status()` currently uses the same state-readiness loop for every call. `_BambuStatusAccumulator.ready` remains false until a recognized `gcode_state` is observed, even after valid reports have made the session useful and `_BambuStatusAccumulator.has_report` is true.

Consequently a retained warm call keeps fetching until state readiness or the total deadline. The already-existing private `has_report` property precisely represents the missing decision: at least one structurally valid `print` mapping has been successfully applied in the current connection/session.

## Cold vs Warm Definition

Define the mode once, at the beginning of each adapter status call, before consuming a new report:

- **COLD**: `accumulator.has_report is False` at call entry.
- **WARM**: `accumulator.has_report is True` at call entry.

Do not recompute the mode after the first report inside a cold call. A cold call must remain in the existing cold algorithm until state readiness, partial-deadline return, structural error, or no-report timeout.

Warmth is connection/session-scoped and is independent of public field values. `UNKNOWN` state, absent progress, absent temperatures, or absent AMS do not make an accumulator cold after a structurally valid report has been applied. A structurally valid empty or wholly unmodeled `print` mapping also makes the current session warm.

Reuse `_BambuStatusAccumulator.has_report`; do not add a public API or infer warmth from `PrinterStatus` contents, elapsed time, or call count.

## Warm Read Semantics

For a retained call that is warm at entry:

1. Call `fetch_report()` exactly once for the existing report topic with the existing `timeout_seconds`.
2. If it returns a payload, run the same UTF-8, JSON, root, and `print` structural validation used by cold reads.
3. Apply that one structurally valid `print` delta through the existing accumulator.
4. Return `accumulator.snapshot()` immediately.

Do not inspect `accumulator.ready`, require `gcode_state`, require a modeled-field update, fetch a fixed report count, or perform a second fetch.

A valid delta containing only unmodeled fields counts as the next valid report and may return an unchanged `PrinterStatus`. A valid delta containing malformed individual modeled fields also counts: existing accumulator semantics retain last-known-good values, and the warm call returns without another readiness wait.

Avoid duplicate decode/validate/apply logic. Refactor only private adapter helpers as needed so cold and warm paths share processing of one payload. The cold loop and warm single-fetch path must remain visibly distinct.

## Timeout / Error Semantics

Warm timeout is exact:

- one `fetch_report(topic, timeout_seconds)` returning `None` raises the existing `PrinterTimeout`;
- it does not clear the accumulator;
- it does not disconnect the caller-owned retained client;
- a later retained call remains warm and can merge a later valid report.

Do not return cached status as a successful fresh read when no new report arrived.

A warm payload with invalid UTF-8, invalid JSON, a non-object root, missing `print`, or non-object `print` raises the existing `PrinterInvalidReport` immediately. Validation completes before accumulator mutation, so the prior cache remains intact and the next retained call remains warm.

Malformed individual modeled fields inside a structurally valid `print` mapping remain field-level invalid values, not structural failures. They retain last-known-good values and the warm call returns after that one report.

Cold timeout and error semantics remain exactly as committed: one total monotonic deadline, partial status after valid telemetry without recognized state, `PrinterTimeout` when no report arrives, and immediate `PrinterInvalidReport` for structural corruption.

## Session Lifecycle

Preserve current session boundaries:

- construction starts cold;
- a new connection attempt resets the accumulator before contacting the client;
- authentication or unreachable failure leaves the accumulator cold and no retained client;
- successful first retained status starts cold;
- after any structurally valid applied report, later retained calls are warm;
- warm timeout or structural-invalid payload preserves warm cached state;
- `disconnect()`, including a disconnect exception, resets the accumulator;
- reconnect starts cold;
- repeated `connect()` while already active remains an idempotent no-op and must preserve warm state.

No TTL, timestamp, freshness field, lock, background task, or cross-session cache is introduced.

## Standalone Behavior

Standalone `get_status()` always creates a fresh temporary accumulator and therefore always uses the existing cold algorithm:

`construct client → connect → bounded cold accumulation → return full/partial status or raise → disconnect in finally → discard accumulator`

Do not accelerate standalone calls, reduce their timeout, or carry temporary state to another call. Their one total monotonic deadline and readiness predicate remain unchanged.

## MCP Implications

Do not modify MCP. `printer.status` still creates a fresh adapter and invokes standalone `get_status()`, so each MCP call remains cold and can consume the existing timeout or return a partial snapshot.

This increment does not make MCP status an instant cached read. A persistent printer monitor/service is a separate future architecture decision requiring its own plan.

## Required Production Changes

Modify only:

- `src/print_engineer/adapters/printer/bambu.py`

Use the existing private `_BambuStatusAccumulator.has_report` as the call-entry predicate. Split or parameterize private status-fetch helpers only enough to preserve the existing cold loop and add the warm one-fetch path while sharing payload decode/validation/application.

Do not change `_BambuStatusAccumulator.apply()`, state mapping, progress cleanup, temperature semantics, AMS semantics, session reset rules, public interfaces, dependencies, or unsupported operations.

No production changes are required in transport, core types/interfaces, MCP, errors, configuration, exports, or recommendation code.

## Required Test Changes

Modify only:

- `tests/unit/test_bambu_printer_adapter.py`

No new unit-test file is required. Keep `tests/unit/test_bambu_state_accumulator.py`, `tests/unit/test_printer_transport.py`, and `tests/unit/test_printer_mcp.py` unchanged as regression suites. Reuse the existing fake `MqttClient` boundary; it must deliver distinct raw reports and must not merge payloads itself.

Add deterministic hermetic coverage proving:

- **Cold unchanged**: a fresh retained or standalone accumulator with telemetry but no recognized state continues until the existing deadline and returns partial; recognized state returns early; no report raises `PrinterTimeout`; decreasing remaining timeout values still prove one total deadline.
- **Warm predicate is report-based**: after a valid partial cold result with state still `UNKNOWN`, the retained session is warm.
- **Fast modeled refresh**: starting from cached `PRINTING`, progress `0.51`, bed `65`, and nozzle `220`, a next raw report containing only `nozzle_temper=219.8` causes exactly one fetch, returns immediately, retains state/progress/bed, and updates nozzle.
- **Warm partial refresh**: cached `UNKNOWN` state with progress and bed temperature remains warm; one next nozzle-only report returns after exactly one fetch without waiting for state.
- **Valid unmodeled delta**: a `print` mapping containing only a safe fake unmodeled key counts as the single report, performs exactly one fetch, and may return an unchanged snapshot.
- **Malformed modeled field**: one structurally valid report with a malformed modeled field retains last-known-good state and returns after exactly one fetch.
- **Warm timeout**: no next report raises `PrinterTimeout`, does not disconnect or reset cache, and a later one-report refresh updates the prior cache.
- **Warm structural error**: invalid UTF-8, JSON, root, missing/invalid `print` raises `PrinterInvalidReport`, preserves cache, and a later valid one-report refresh continues from it.
- **Session reset**: warm session A followed by disconnect/reconnect makes session B cold and prevents state/progress/AMS leakage.
- **Repeated active connect**: calling `connect()` again in a warm session creates no client, performs no reset, and the next call still uses one-fetch warm behavior.
- **Standalone regression**: independent standalone calls remain cold, bounded, and disconnected in `finally`.

Tests must compare fetch-call counts before and after each warm call and inspect supplied timeout values. Use no real sleeps, network, broker, internet, or physical printer.

Run these regressions after focused tests:

- `tests/unit/test_bambu_state_accumulator.py`
- `tests/unit/test_printer_transport.py`
- `tests/unit/test_printer_mcp.py`
- default-disabled `tests/integration/test_bambu_printer_lan.py`

## Hardware Verification Plan

After Build and independent Review, reuse the existing exact opt-in `RUN_BAMBU_LAN_STATE_ACCUMULATOR_TEST=1`; do not add another hardware flag or another MQTT implementation. Keep all three `BAMBU_*` credentials behind the existing opt-in gate.

Run the existing real-adapter, one-retained-connection sequence:

`connect → snapshot 1 → snapshot 2 → snapshot 3 → disconnect in finally`

The first snapshot remains cold and may consume the existing timeout. Snapshots two and three must each return after the next structurally valid passive report rather than waiting for state readiness. Record qualitative improvement using relative timing only; do not assert an exact cadence or hardcode the observed two-second interval.

Output remains limited to snapshot ordinal, relative timing, normalized state, progress, current/target temperatures, and AMS-present boolean. Do not print raw payloads, field names outside the model, IP, serial, access code, filenames, identifiers, or complete exception details. Do not run this hardware check during Build.

## Read-Only Safety

Maintain **ZERO MQTT PUBLISH PATHS**.

The increment may only connect, subscribe/read ordered passive telemetry, validate, update in-memory last-known state, return status, and disconnect. Search the relevant implementation and tests for `publish`, `/request`, `pushall`, and printer-control paths during Build and Review.

Do not add MQTT publishing, `device/{serial}/request`, `pushall`, start/stop/pause/resume commands, temperature commands, settings changes, uploads, FTPS, camera, cloud MQTT, slicing, or automatic printing.

## Risks

- A warm call can return an unchanged snapshot after a structurally valid delta containing no modeled fields. This is intentional: freshness means a new report was consumed, not that a public field changed.
- A warm call still waits up to the existing timeout when the printer emits no new report. It must surface `PrinterTimeout` rather than label stale cache as fresh.
- Standalone and MCP calls remain subject to cold readiness latency and partial status because their adapters do not retain sessions.
- Cached values remain last-known within one session and may age; freshness metadata and TTL remain out of scope.
- Hardware cadence varies by firmware and printer activity, so the real latency improvement is qualitative rather than a fixed-duration contract.

## Out of Scope

- changing cold-start readiness or its total deadline
- reducing standalone timeout
- persistent MCP connection or long-lived printer monitor/service
- background MQTT worker, polling loop, or cadence assumption
- transport changes or a new transport API
- accumulator merge, state, progress, temperature, AMS, or TTL changes
- new `PrinterStatus` fields or freshness metadata
- layer, remaining-time, Wi-Fi, fan, filename, firmware, or model exposure
- MQTT `publish()`
- `device/{serial}/request`
- `pushall`
- printer control, temperature commands, settings changes, upload, FTPS, camera, cloud MQTT, slicing, or automatic printing

## Implementation Order

1. Review and approve this plan.
2. Record `git status --short` and the exact expected changed-file set.
3. Add focused retained fast-refresh tests in `test_bambu_printer_adapter.py`.
4. Refactor private adapter fetch helpers and implement call-entry cold/warm selection in `bambu.py`.
5. Run focused adapter and accumulator tests.
6. Run transport and MCP regressions and the integration suite with every hardware opt-in removed.
7. Run focused Ruff and Mypy on the changed and directly relevant files.
8. Audit the complete diff and explicit publish/request/pushall/control searches.
9. Independently review semantics, test quality, scope, and hardware-harness safety.
10. Only after explicit authorization, rerun the existing accumulator hardware experiment and record sanitized timing evidence.
11. Create a separate plan for any persistent MCP service or further cold-readiness change.

## Acceptance Criteria

- Warm is exactly `accumulator.has_report is True` at retained-call entry, regardless of state or other public values.
- Cold mode is fixed at call entry and preserves the committed one-deadline readiness algorithm exactly.
- A warm retained call performs exactly one `fetch_report(topic, timeout_seconds)`.
- One structurally valid report is decoded, applied, and returned immediately without state or modeled-change readiness.
- Valid empty, unmodeled, or individually malformed deltas count as warm refresh reports under existing accumulator semantics.
- Warm no-report raises `PrinterTimeout` without clearing cache or disconnecting the retained client.
- Warm structural invalidity raises `PrinterInvalidReport` without mutating or resetting cache.
- Later warm calls remain usable after timeout or structural error.
- Repeated active connect preserves warm state; failed connection, disconnect, disconnect exception, and reconnect reset it.
- Standalone and MCP behavior remain cold and unchanged.
- Transport, accumulator merge behavior, public types/interfaces, errors, configuration, and dependencies remain unchanged.
- Focused hermetic tests, transport/MCP regressions, default hardware skips, Ruff, and Mypy pass.
- The existing hardware experiment is reused and is not executed during Build.
- Zero MQTT publish, request-topic, `pushall`, and printer-control paths remain intact.

## Final Verdict

ADAPTER CHANGES REQUIRED

PLAN ONLY — no source or test files were modified and no hardware connection was performed.

## Hardware Verification Result

RESULT: PASS

The real Bambu Lab A1 accumulator hardware harness produced these sanitized
snapshots:

- snapshot 1 at `+10.016s`: `state=unknown`, `progress=0.69`,
  `bed=64.96875`, `nozzle=220.0625`, `target_bed=None`,
  `target_nozzle=None`, `ams_present=False`;
- snapshot 2 at `+11.109s`: `state=unknown`, `progress=0.69`,
  `bed=64.96875`, `nozzle=220.0625`, `target_bed=None`,
  `target_nozzle=None`, `ams_present=False`;
- snapshot 3 at `+15.125s`: `state=unknown`, `progress=0.69`,
  `bed=64.9375`, `nozzle=219.96875`, `target_bed=None`,
  `target_nozzle=None`, `ams_present=False`.

The first call remained cold and took approximately `10.016` seconds. The
first warm retained refresh returned approximately `1.093` seconds later, and
the second warm retained refresh returned approximately `4.016` seconds after
that. Warm calls therefore no longer repeatedly wait for the full cold-start
readiness timeout; their latency follows arrival of the next passive MQTT
report. These observations do not establish an exact MQTT cadence guarantee.

Snapshot 2 returned even though the currently modeled values were unchanged,
proving that one structurally valid sparse or unmodeled report correctly
completes a warm refresh. Snapshot 3 subsequently updated current temperature
values while preserving accumulated state. State, target temperatures, and AMS
were not observed during this session and were correctly not fabricated.

The run used zero MQTT publishing, no request topic, no `pushall`, and no
printer-control operation. No credentials, identifiers, filenames, or raw MQTT
payloads were recorded.
