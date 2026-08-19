# Phase 2 — Printer Status Assessment v1

Status: APPROVED

## Objective

Extend successful `printer.status` responses with one small, deterministic,
machine-friendly assessment derived only from the same normalized
`PrinterStatus` already obtained by the existing status retrieval.

The assessment distinguishes informational states, explicit attention states,
explicit error/disconnection, and unknown state without claiming that the
printer is healthy, diagnosing a cause, or recommending an action.

## Baseline

The implementation baseline consists of:

- `cc1e210d6f68116776d85d5fde5ec015efbae3c7`,
  `feat(printer): checkpoint read-only status support`;
- `84bb09872ffe06385e7b4ebdaf73b97196686f47`,
  `feat(printer): add deterministic status summary`.

At this baseline, `printer.status` constructs one adapter, calls
`get_status()` once, and returns the normalized structured status plus a
deterministic summary. The normalized status contains state, connection,
temperatures, progress, AMS, layers, and remaining minutes. This increment does
not reopen any of those established contracts.

The following intentional untracked files are unrelated and must remain
untouched, unstaged, and outside Build scope:

- `plans/phase-2-bounded-mqtt-transport-lifecycle.md`;
- `plans/phase-2-printer-monitor-core.md`;
- `plans/phase-2-retained-status-session-v1.md`;
- `tools/codex-controller/package-lock.json`.

## Current Architecture

`PrinterStatus` is a frozen normalized data object in
`src/print_engineer/core/types.py`. Its actual `PrinterState` members are:

- `OFFLINE`;
- `IDLE`;
- `PRINTING`;
- `PAUSED`;
- `ERROR`;
- `UNKNOWN`.

`PrinterTools.status()` in `src/print_engineer/mcp/tools/printer.py` resolves
configuration, constructs one `BambuPrinterAdapter`, obtains one
`PrinterStatus`, and uses that same object for structured serialization and
summary formatting. Existing `PrinterError` responses return before successful
presentation is built.

The MCP printer module already contains private pure presentation helpers and
is the only consumer of the proposed assessment. There is no repository
presentation/service abstraction that warrants a new module, and the existing
response convention supports additive top-level success fields.

The Bambu mapping currently produces IDLE, PRINTING, PAUSED, ERROR, or UNKNOWN
from `gcode_state`; `OFFLINE` remains a valid public `PrinterState` and must
still receive an exhaustive, literal assessment. `is_connected` is a distinct
normalized flag and takes precedence over every state value.

## Scope

This increment includes only:

1. one private pure `PrinterStatus -> assessment mapping` helper in the
   existing MCP printer module;
2. one additive top-level `assessment` object on successful `printer.status`
   responses;
3. hermetic tests for the exact schema, exhaustive connection/state mapping,
   non-diagnostic boundary, response compatibility, determinism, and the
   existing single-retrieval invariant.

## Non-Goals

This increment does not add:

- a separate `printer.check` or `printer.assess` tool;
- diagnostics, troubleshooting, recommendations, alerts, or health scoring;
- a `healthy` flag or broad claim that no problem exists;
- temperature, AMS, progress, layer, or remaining-time inference;
- thresholds, cross-field validation, anomaly detection, or stalled-print
  detection;
- timestamps, freshness, history, persistence, monitoring, or retained state;
- another adapter, retrieval, refresh, connection, worker, cache, or retry;
- changes to the normalized status model or summary grammar;
- printer control, MQTT behavior, protocol parsing, or hardware testing;
- localization, configuration, dependencies, LLM calls, or user preferences.

## Assessment Architecture

Add a private pure helper beside `_serialize_status()` and
`_format_status_summary()` in `src/print_engineer/mcp/tools/printer.py`,
conceptually:

```python
def _assess_status(status: PrinterStatus) -> dict[str, str]:
    ...
```

The helper accepts only `PrinterStatus`, performs no I/O, and returns a new
JSON-compatible dictionary. Use private module-level constants or a private
mapping as appropriate to local style. Do not add a core enum, dataclass,
protocol, service, module, or public parsing/classification API for this single
MCP consumer.

The successful flow remains:

```text
construct one adapter
-> call get_status() once
-> bind one PrinterStatus
-> serialize that status
-> summarize that status
-> assess that status
-> return one success response
```

Assessment must consume `PrinterStatus` directly. It must never parse the
human-readable summary.

## MCP Contract

Keep the existing `printer.status` tool rather than adding a second tool. A
separate independently invoked tool would encourage another cold adapter and
status retrieval while retained sessions remain parked.

On success, preserve `ok`, the complete existing `status` mapping, and the
existing `summary` string byte-for-byte, then add one top-level sibling:

```json
{
  "ok": true,
  "status": {"...": "all existing normalized fields unchanged"},
  "summary": "existing deterministic summary unchanged",
  "assessment": {
    "level": "info",
    "code": "printer_printing",
    "message": "Printer is printing."
  }
}
```

`assessment` is top-level because it classifies the whole normalized
connection/state result; it is not telemetry and must not be inserted into
`status` or `PrinterStatus`.

On any existing configuration or `PrinterError` failure, preserve the current
error response exactly:

```json
{"ok": false, "error": {"code": "...", "message": "...", "details": {}}}
```

Error responses do not gain `assessment` or a synthetic summary. Retrieval
failures remain represented by their established structured error contract.

## Assessment Schema

The successful assessment object has exactly three string fields:

```json
{
  "level": "info | attention | error | unknown",
  "code": "stable_machine_code",
  "message": "Exact English sentence."
}
```

Field meanings:

- `level`: broad presentation-neutral severity class;
- `code`: stable machine-oriented classification;
- `message`: concise deterministic description of the explicit connection or
  state only.

Use only these four level strings:

- `info` for explicit IDLE and PRINTING states;
- `attention` for explicit OFFLINE and PAUSED states;
- `error` for disconnection precedence and explicit ERROR state;
- `unknown` for connected UNKNOWN state.

Do not add `requires_attention`. The level already expresses the useful
machine distinction, while a boolean would collapse `unknown` into an
untruthful yes/no. A tri-state flag would duplicate `level` without adding v1
meaning. Do not add `healthy`, confidence, diagnostics, actions, observations,
or nested metadata.

## Connection Precedence

When `status.is_connected is False`, return exactly:

```json
{
  "level": "error",
  "code": "printer_disconnected",
  "message": "Printer is disconnected."
}
```

This result wins for every `PrinterState`, including contradictory manually
constructed combinations such as disconnected + PRINTING or disconnected +
ERROR. Return one primary assessment only; do not append a second state
assessment.

Connection precedence describes only the explicit normalized connection flag.
It does not clear or mutate structured fields, infer why the printer is
disconnected, retry, reconnect, or initiate a refresh.

## State Assessment Rules

When `is_connected is True`, use this exhaustive exact mapping:

| `PrinterState` | `level` | `code` | `message` |
|---|---|---|---|
| `OFFLINE` | `attention` | `printer_offline` | `Printer reports an offline state.` |
| `IDLE` | `info` | `printer_idle` | `Printer is idle.` |
| `PRINTING` | `info` | `printer_printing` | `Printer is printing.` |
| `PAUSED` | `attention` | `printer_paused` | `Printer is paused.` |
| `ERROR` | `error` | `printer_error` | `Printer reports an error state.` |
| `UNKNOWN` | `unknown` | `printer_state_unknown` | `Printer state is unknown.` |

`OFFLINE` is kept distinct from `is_connected=False`: the former is an
explicit enum value rendered literally when the normalized connection flag is
true, while the latter invokes connection precedence. The assessment does not
invent why an OFFLINE state and a true connection flag coexist.

PRINTING and IDLE are informational descriptions, not health findings. PAUSED
is an explicit attention state without asserting a fault. ERROR preserves the
printer-reported error classification without diagnosing its cause. UNKNOWN
preserves uncertainty rather than becoming informational or disconnected.

## Explicit Non-Diagnostic Boundary

The assessment may read only:

- `status.is_connected`;
- `status.state`.

It must not read or classify from:

- nozzle or bed current/target temperatures;
- progress;
- current or total layers;
- remaining minutes;
- AMS presence, connection, slots, colors, or materials;
- the existing summary string;
- combinations of any excluded fields.

Do not claim `healthy`, `normal`, `ready`, `working`, `everything is fine`, or
`no problems detected`. Do not infer heating, cooling, stalled progress,
completion, layer inconsistency, stale remaining time, AMS faults, or required
operator action. The exact table messages are the complete v1 interpretation.

## Determinism

Assessment is a pure function of the immutable normalized status connection
flag and enum value. Given the same `PrinterStatus`, it must return an equal
three-field mapping with identical strings.

It must not access settings, adapters, transports, MQTT, raw reports, summary
text, environment variables, filesystem, current time, timers, history,
locale, randomness, mutable caches, LLMs, or recommendation logic.

## Single-Retrieval Invariant

One `printer.status` invocation must continue to create exactly one adapter and
call `get_status()` exactly once. The one returned `PrinterStatus` object is
the sole source for structured serialization, summary, and assessment.

Assessment adds:

- adapter instances: 0;
- `get_status()` calls: 0;
- refresh requests: 0;
- transport/MQTT operations: 0.

Extend the existing real registered-tool fake-adapter test to assert exactly:

```text
adapter instances == 1
get_status calls == 1
```

while validating the existing structured response, existing summary, and new
assessment from that same invocation. A direct helper test separately shows
that assessment requires no adapter or settings.

## Exact Build Scope

Repository inspection establishes that exactly two files are required.

Production:

- `src/print_engineer/mcp/tools/printer.py`
  - add the private pure connection/state assessment helper or mapping;
  - add the top-level `assessment` success field from the existing local
    `status` object.

Tests:

- `tests/unit/test_printer_mcp.py`
  - add exhaustive exact assessment cases;
  - extend the real MCP success, single-retrieval, structured-response, summary,
    and error assertions.

No changes are required to core types, Bambu normalization, transport, MCP
server registration, configuration, dependencies, or any other test module.

## Explicitly Unchanged

- `PrinterStatus`, `PrinterState`, and `AMSInfo` definitions and semantics;
- all structured `printer.status` keys and values;
- the complete approved summary grammar and formatter behavior;
- Bambu `_BambuStatusAccumulator` and telemetry parsing;
- layer normalization and accumulation;
- remaining-time normalization and accumulation;
- temperatures, progress, and AMS normalization;
- `src/print_engineer/core/types.py`;
- `src/print_engineer/adapters/printer/bambu.py`;
- `src/print_engineer/adapters/printer/transport.py`;
- MQTT topics, fixed `pushing.pushall` request, QoS, cooldown, and lifecycle;
- request-status-refresh behavior;
- printer interfaces and all printer-control capability;
- MCP server registration and lifecycle;
- recommendation engine and LLM behavior;
- retained-session, bounded-transport-lifecycle, and monitor-core work;
- configuration, dependencies, integrations, and hardware tests;
- all intentional untracked files listed in Baseline.

## Test Plan

All tests are hermetic and perform no network or hardware activity.

### Pure assessment tests

Import the private assessment helper into `tests/unit/test_printer_mcp.py` and
assert exact dictionary equality for:

1. connected OFFLINE -> attention / `printer_offline` / exact offline message;
2. connected IDLE -> info / `printer_idle` / exact idle message;
3. connected PRINTING -> info / `printer_printing` / exact printing message;
4. connected PAUSED -> attention / `printer_paused` / exact paused message;
5. connected ERROR -> error / `printer_error` / exact error-state message;
6. connected UNKNOWN -> unknown / `printer_state_unknown` / exact unknown
   message;
7. disconnected + PRINTING with progress, layers, remaining time,
   temperatures, and AMS populated -> exact disconnected assessment;
8. disconnected + ERROR -> the same disconnected assessment, proving
   connection precedence;
9. the same immutable `PrinterStatus` assessed twice -> equal mappings;
10. statuses with the same connection/state but different temperatures,
    progress, layers, remaining time, and AMS -> equal assessments, proving the
    explicit non-diagnostic boundary.

Exact table assertions make broad health wording impossible. No separate
banned-word test is required, though messages must contain none of `healthy`,
`normal`, `everything is fine`, or equivalent claims.

### MCP integration and regression tests

- Invoke the real registered `printer.status` success path with the existing
  fake adapter.
- Assert exactly one adapter instance and one `get_status()` call.
- Assert the complete existing `status` mapping is unchanged.
- Assert the existing summary string is unchanged.
- Assert the new top-level assessment equals the exact expected three-field
  mapping.
- In a disconnected populated-status case, assert disconnected assessment wins
  while structured fields remain unchanged and summary remains exactly
  `Printer disconnected`.
- Cover all six connected enum values either through the pure helper table or
  the registered path, with at least the normal success path using the real
  registered tool.
- For every existing tested `PrinterError`, assert the previous response shape
  remains unchanged and both `summary` and `assessment` are absent.
- Preserve all existing configuration-precedence, summary, serialization, and
  determinism tests without weakening their assertions.

Tests must fail if assessment retrieves again, reads excluded telemetry,
parses summary, changes summary/status, loses connection precedence, collapses
UNKNOWN into info, weakens ERROR/PAUSED, or appears on error responses.

## Verification Commands

Focused MCP and assessment tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_printer_mcp.py
```

Relevant printer regression suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_interfaces.py tests/unit/test_printer_transport.py tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
```

If the known Windows pytest default temp-root `PermissionError` occurs, retry
each affected command once with a unique `--basetemp` under system TEMP.

Ruff and Mypy over the exact Build scope:

```powershell
.\.venv\Scripts\ruff.exe check src/print_engineer/mcp/tools/printer.py tests/unit/test_printer_mcp.py
.\.venv\Scripts\python.exe -m mypy src/print_engineer/mcp/tools/printer.py tests/unit/test_printer_mcp.py
```

If focused verification passes, run the full unit suite in a child environment
with `BAMBU_IP`, `BAMBU_SERIAL`, `BAMBU_ACCESS_CODE`,
`RUN_BAMBU_LAN_HARDWARE_TEST`, and
`RUN_BAMBU_LAN_STATUS_REFRESH_TEST` removed:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/
```

One unique `--basetemp` retry is permitted for the known Windows temp-root
issue. Classify the known print-context and Windows subprocess termination
failures, or other unrelated failures, rather than fixing them. Do not run
integration or hardware tests.

## Hardware Verification Decision

NO HARDWARE VERIFICATION REQUIRED.

Assessment is a pure deterministic mapping from the existing normalized
`is_connected` and `PrinterState` values. It introduces no protocol field,
device assumption, status request, MQTT behavior, or physical-printer claim.

## Risks / Unverified Assumptions

- `OFFLINE` is a valid public enum member even though current Bambu
  `gcode_state` mapping does not produce it. The plan intentionally gives it a
  literal connected-state assessment for exhaustive public compatibility; it
  does not infer disconnection when `is_connected=True`.
- `error` for `is_connected=False` is an API assessment severity, not a claim
  about printer hardware failure or its cause. The exact message remains only
  `Printer is disconnected.`
- `attention` means the explicit state deserves caller awareness; it does not
  prescribe action or diagnose a fault.
- The assessment is intentionally incomplete as a health evaluation because it
  ignores all telemetry except connection and state. Its schema and wording
  make that boundary explicit.

No repository blocker was found. These are resolved v1 classification rules,
not implementation choices left to Build.

## Acceptance Criteria

DONE only if:

- successful `printer.status` adds exactly the specified top-level assessment;
- assessment has exactly `level`, `code`, and `message` string fields;
- assessment derives directly and only from the same normalized
  `PrinterStatus.is_connected` and `.state`;
- one invocation creates one adapter and calls `get_status()` once;
- disconnected precedence is exact for every underlying state;
- all six actual `PrinterState` members map exactly as specified;
- ERROR remains explicit error, PAUSED/OFFLINE remain attention, UNKNOWN
  remains unknown, and PRINTING/IDLE remain non-diagnostic info;
- no `healthy` or broader health guarantee is introduced;
- no temperature, progress, layer, remaining-time, or AMS inference exists;
- existing structured status and summary remain byte/value compatible;
- existing error responses remain unchanged and contain no assessment;
- no second tool, retrieval, adapter, refresh, MQTT operation, worker, cache,
  lifecycle behavior, or printer-control capability is added;
- implementation changes exactly the two scoped files;
- focused pytest, Ruff, and Mypy pass;
- no relevant introduced regression remains;
- no hardware test is required or executed.

## Definition of Done

The increment is complete when the two scoped files implement and prove the
exact additive three-field assessment contract, the real registered status
path retains its single retrieval and existing status/summary/error behavior,
all classification and non-diagnostic rules are hermetically tested, static
checks pass, the diff contains no other files, and independent Review confirms
zero added network, MQTT, lifecycle, or control behavior.

## Approval Questions

1. Should assessment be additive to `printer.status` or a separate tool?
   - **Proposed resolution:** additive to existing successful
     `printer.status`; no separate tool.
2. Where should the pure assessment function live?
   - **Proposed resolution:** a private helper in
     `src/print_engineer/mcp/tools/printer.py` beside serialization and summary.
3. What exact assessment structure is returned?
   - **Proposed resolution:** exactly `level`, `code`, and `message` strings in
     one top-level `assessment` object.
4. What exact level/code/message vocabulary is used?
   - **Proposed resolution:** the four levels and exhaustive table in
     Assessment Schema and State Assessment Rules.
5. Does connection state take precedence over `PrinterState`?
   - **Proposed resolution:** yes; false always returns the exact disconnected
     assessment.
6. What exact outcome corresponds to OFFLINE?
   - **Proposed resolution:** attention / `printer_offline` /
     `Printer reports an offline state.`
7. What exact outcome corresponds to IDLE?
   - **Proposed resolution:** info / `printer_idle` / `Printer is idle.`
8. What exact outcome corresponds to PRINTING?
   - **Proposed resolution:** info / `printer_printing` /
     `Printer is printing.`
9. What exact outcome corresponds to PAUSED?
   - **Proposed resolution:** attention / `printer_paused` /
     `Printer is paused.`
10. What exact outcome corresponds to ERROR?
    - **Proposed resolution:** error / `printer_error` /
      `Printer reports an error state.`
11. What exact outcome corresponds to UNKNOWN?
    - **Proposed resolution:** unknown / `printer_state_unknown` /
      `Printer state is unknown.`
12. Is a `requires_attention` field needed?
    - **Proposed resolution:** no; it would duplicate or collapse `level`.
13. If so, is it bool or tri-state?
    - **Proposed resolution:** not applicable because the field is omitted.
14. How is the existing MCP error path handled?
    - **Proposed resolution:** unchanged; no assessment or summary is added.
15. How is exactly-one-`get_status` proven?
    - **Proposed resolution:** extend the real registered-tool fake-adapter test
      to assert one adapter instance and exactly one call while checking all
      three success outputs.
16. What exact Build scope is required?
    - **Proposed resolution:** exactly `printer.py` and
      `test_printer_mcp.py`.
17. Is hardware verification unnecessary?
    - **Proposed resolution:** yes; this is pure presentation/classification of
      established normalized fields.

No classification, architecture, compatibility, safety, or test semantic is
left for Build to invent.

## Final Verdict

The checkpointed repository supports Printer Status Assessment v1 as a
two-file additive MCP presentation increment. The exhaustive schema,
connection precedence, state mapping, non-diagnostic boundary,
single-retrieval invariant, error compatibility, and safety constraints are
fully specified for independent plan review.

PROPOSED — READY FOR INDEPENDENT PLAN REVIEW
