# Phase 2 — Printer Status Summary v1

Status: APPROVED

## Objective

Extend the successful existing `printer.status` MCP response with one additive
human-readable field:

```json
"summary": "Printing · 73% complete · Layer 184 / 252 · About 32 min remaining · Nozzle 220 / 220 °C · Bed 55 / 55 °C · AMS connected"
```

The summary is a deterministic, concise presentation of the same normalized
`PrinterStatus` already returned by the one existing status retrieval. It is
not another status model, does not change any structured field, and makes no
diagnostic, health, freshness, or accuracy claim.

## Baseline

The implementation baseline is commit
`cc1e210d6f68116776d85d5fde5ec015efbae3c7`,
`feat(printer): checkpoint read-only status support`.

That checkpoint already provides the approved status-refresh capability,
normalized layer and remaining-time fields, and flat `printer.status`
serialization. This plan does not reopen those contracts.

The following intentional untracked files are unrelated and must remain
untouched, unstaged, and outside Build scope:

- `plans/phase-2-bounded-mqtt-transport-lifecycle.md`;
- `plans/phase-2-printer-monitor-core.md`;
- `plans/phase-2-retained-status-session-v1.md`;
- `tools/codex-controller/package-lock.json`.

## Current Architecture

`PrinterStatus` is a frozen structured data object in
`src/print_engineer/core/types.py`. It exposes state, connection, temperatures,
progress, AMS, layers, and remaining minutes. It intentionally contains no
presentation text.

`PrinterTools.status()` in `src/print_engineer/mcp/tools/printer.py` currently:

1. resolves connection configuration;
2. constructs one `BambuPrinterAdapter`;
3. calls `adapter.get_status()` exactly once;
4. serializes the returned `PrinterStatus` through `_serialize_status()`;
5. returns the existing structured error unchanged when a `PrinterError` is
   raised.

There is no existing printer presentation/service layer. Other MCP tools do
not establish a reusable status-summary abstraction. A private pure formatter
beside the existing MCP serializer is therefore the smallest appropriate
boundary. A new module or domain-model field would add architecture without a
second consumer.

Current Bambu progress normalization divides `mc_percent` by 100 and rounds the
stored fraction to four decimal places, but does not enforce a range. Summary
range handling must consequently be presentation-only and must not mutate the
normalized value.

`AMSInfo` exposes only `is_connected` plus opaque normalized slot labels. Slot
labels do not establish installed capacity, readiness, material, or health, so
v1 uses only `is_connected` in summary text.

## Scope

This increment includes only:

1. one private pure `PrinterStatus -> str` formatter in the existing MCP
   printer tool module;
2. one additive top-level `summary` key on successful `printer.status`
   responses;
3. hermetic exact-string tests for the formatting grammar, partial values,
   freshness-sensitive omission, error behavior, and the single-retrieval
   invariant.

## Non-Goals

This increment does not add or change:

- normalized `PrinterStatus` fields or semantics;
- MQTT telemetry, parsing, topics, payloads, QoS, refreshes, or cooldowns;
- adapters, connections, caches, workers, polling, retries, or lifecycle;
- ETA timestamps, clocks, stale/fresh tracking, remaining-time correction, or
  local countdowns;
- health scoring, anomaly detection, temperature tolerances, AMS diagnostics,
  alerts, troubleshooting, or recommendations;
- LLM calls, localization, Markdown, ANSI formatting, or configuration;
- a second MCP tool or any printer-control capability.

## Summary Architecture

Add a small private pure helper in
`src/print_engineer/mcp/tools/printer.py`, conceptually:

```python
def _format_status_summary(status: PrinterStatus) -> str:
    ...
```

It accepts only an already-normalized `PrinterStatus` and returns a string. It
must not accept settings, adapters, transports, MQTT fields, raw mappings, a
clock, or callbacks. It performs no I/O and maintains no state.

`PrinterTools.status()` retains its single `adapter.get_status()` call, then
uses that same local `status` object for both `_serialize_status(status)` and
`_format_status_summary(status)`. The formatter must not be called through
another MCP tool.

Do not place the summary in `PrinterStatus`, the Printer ABC, the Bambu
accumulator, or MQTT transport. It is derived MCP presentation, not normalized
domain state.

## MCP Contract

On success, preserve the existing response and add one top-level sibling of
`status`:

```json
{
  "ok": true,
  "status": {
    "state": "printing",
    "is_connected": true,
    "bed_temp": 55.0,
    "nozzle_temp": 220.0,
    "target_bed_temp": 55.0,
    "target_nozzle_temp": 220.0,
    "progress": 0.73,
    "ams": {"is_connected": true, "slots": ["A1"]},
    "current_layer": 184,
    "total_layers": 252,
    "remaining_time_minutes": 32
  },
  "summary": "Printing · 73% complete · Layer 184 / 252 · About 32 min remaining · Nozzle 220 / 220 °C · Bed 55 / 55 °C · AMS connected"
}
```

`summary` is top-level because it is a presentation of the whole structured
status rather than another normalized telemetry field. Every existing status
key, value, and type remains unchanged.

Error responses remain exactly:

```json
{"ok": false, "error": {"code": "...", "message": "...", "details": {}}}
```

They do not gain `summary`. Presentation must not hide, replace, or paraphrase
structured errors.

## Summary Grammar

The output is one English plain-text line. Build an ordered list of non-empty
fragments and join them using the exact separator:

```text
 · 
```

There is no leading/trailing separator, newline, Markdown, or ANSI. Fragment
order is fixed:

1. connection/state;
2. progress;
3. layers;
4. remaining time;
5. nozzle temperature;
6. bed temperature;
7. AMS.

Given an identical `PrinterStatus`, the returned Unicode string is
byte-for-byte identical.

## State / Connection Wording

Connection takes precedence.

When `status.is_connected is False`, return exactly:

```text
Printer disconnected
```

Do not append any other normalized values in this case. The current production
meaning of false is that no valid report was applied in that accumulator
lifecycle; including independently constructed or residual-looking values
would imply freshness that the connection flag does not establish. This is
presentation filtering only and does not clear or alter structured fields.

When connected, use exactly this exhaustive `PrinterState` mapping:

| State | Lead fragment |
|---|---|
| `IDLE` | `Idle` |
| `PRINTING` | `Printing` |
| `PAUSED` | `Paused` |
| `ERROR` | `Printer error` |
| `UNKNOWN` | `Status unknown` |
| `OFFLINE` | `Offline` |

`UNKNOWN` is not described as disconnected when `is_connected` is true.
`ERROR` receives no diagnosis. An inconsistent connected `OFFLINE` value is
rendered literally rather than rewritten.

## Progress Formatting

When connected and `progress` is a finite number, append:

```text
<percent>% complete
```

For display only, clamp the fraction to `[0.0, 1.0]`, multiply by 100, and
round to the nearest whole percent with halves rounded upward. Examples:

- `0.0 -> 0% complete`;
- `0.725 -> 73% complete`;
- `1.0 -> 100% complete`;
- `-0.1 -> 0% complete`;
- `1.2 -> 100% complete`.

Use an explicit deterministic calculation rather than relying on binary
floating output or Python's tie-to-even `round()` behavior. A non-finite
value is omitted rather than emitting `nan%` or `inf%`. This is presentation
sanitization only; the structured `progress` remains untouched.

Progress is not used to infer state, completion, remaining time, or health.

## Layer Formatting

Layer fields are formatted independently without validation or inference:

| Available values | Fragment |
|---|---|
| current and total | `Layer <current> / <total>` |
| current only | `Layer <current>` |
| total only | `Total layers <total>` |
| neither | omit |

Do not clamp, swap, compare, or infer completion when current equals or exceeds
total.

## Remaining-Time Formatting

Display `remaining_time_minutes` only when the connected state is `PRINTING`
or `PAUSED` and the field is not `None`:

```text
About <minutes> min remaining
```

Use the normalized whole-minute integer verbatim. Keep `min` for both singular
and plural and do not convert to hours. Examples:

- `1 -> About 1 min remaining`;
- `32 -> About 32 min remaining`;
- `120 -> About 120 min remaining`;
- `0 -> About 0 min remaining` while PRINTING or PAUSED.

For `IDLE`, `ERROR`, `UNKNOWN`, and `OFFLINE`, omit remaining time even when a
positive or zero normalized value exists. This is an explicit presentation
freshness safeguard for the approved latest-valid accumulator contract: for
example, `IDLE` may coexist with a prior positive remaining value after a
sparse state delta. The normalized structured value remains visible and
unchanged; the formatter neither synthesizes zero nor mutates it.

No timestamp, ETA, countdown, accuracy guarantee, monotonicity claim, or
progress/layer derivation is introduced.

## Temperature Formatting

Format nozzle before bed. Values are presentation-safe only when finite.

For each component:

| Available values | Fragment form |
|---|---|
| current and target | `<Name> <current> / <target> °C` |
| current only | `<Name> <current> °C` |
| target only | `<Name> target <target> °C` |
| neither | omit |

Use `Nozzle` and `Bed` as the exact names. Do not compare values or infer
heating, cooling, target attainment, correctness, or abnormality.

Each finite numeric temperature uses one deterministic rule:

1. format the value using Python fixed-point formatting with exactly one
   decimal place, equivalent to `format(value, ".1f")`;
2. if the resulting string ends with `.0`, remove that final `.0`;
3. otherwise preserve the single decimal digit.

Examples:

- `54.9375 -> "54.9"`;
- `220.03125 -> "220"`;
- `55.0 -> "55"`;
- `54.95 -> "55"`;
- `20.04 -> "20"`;
- `20.06 -> "20.1"`.

Do not use `Decimal`, custom half-up rounding, or a second temperature
rounding rule. The contract is Python `.1f` formatting followed only by the
specified trailing-zero removal.

The Build must use one private numeric-format helper shared by current and
target formatting so precision is consistent. Non-finite individual values —
`NaN`, positive infinity, and negative infinity — are treated as unavailable
and omitted from the relevant temperature fragment. The summary must never
emit `nan`, `inf`, or `-inf`. Structured `PrinterStatus` values are not changed.

## AMS Formatting

When `ams is None`, omit AMS text. `None` means unavailable telemetry and must
not be described as absent or disconnected.

When present, use only `AMSInfo.is_connected`:

- true: `AMS connected`;
- false: `AMS not connected`.

Do not include slot count or slot labels. The current `slots` list does not
establish total capacity, loaded material, readiness, or health.

## Partial Status / Fallback Behavior

The state/connection fragment guarantees a non-empty summary:

- `PrinterStatus()` -> `Printer disconnected`;
- connected `UNKNOWN` with no optional telemetry -> `Status unknown`;
- connected `PRINTING` with only progress `0.73` ->
  `Printing · 73% complete`;
- connected `IDLE` with stale `remaining_time_minutes=139` -> `Idle`;
- connected `ERROR` with stale remaining time -> `Printer error`.

Missing optional fragments are simply omitted. Joining must never produce
empty fragments, placeholders, duplicate separators, or dangling punctuation.

## Determinism

The formatter depends only on its `PrinterStatus` argument and fixed constants.
It must not read time, locale, environment variables, configuration, network,
filesystem, randomness, global mutable state, or previous statuses.

English is fixed for v1. No localization infrastructure is introduced.

## Single-Retrieval Invariant

Each `printer.status` invocation must retain exactly the existing retrieval
flow:

```text
construct one adapter
-> call get_status() once
-> bind one PrinterStatus object
-> serialize that object
-> format that same object
-> return one success response
```

Required network/status fetches per invocation: exactly the existing one.

The summary path must create zero adapters, make zero `get_status()` calls,
make zero refresh requests, and perform zero transport/MQTT operations. It must
not call `printer.status` or another MCP tool internally.

The MCP integration test must add a `get_status` call counter to the existing
fake adapter, invoke the real registered `printer.status` tool once, and assert
the count is exactly one while validating both structured fields and summary.
A direct pure-formatter test additionally proves the formatter can run without
settings, adapter, or transport construction.

## Exact Build Scope

Repository inspection establishes that exactly two files are required:

Production:

- `src/print_engineer/mcp/tools/printer.py`
  - add private pure numeric/status formatting helpers;
  - add top-level `summary` on the existing success response using the same
    local `status` object.

Tests:

- `tests/unit/test_printer_mcp.py`
  - add exact formatter cases and real MCP single-retrieval/response tests;
  - preserve existing configuration, structured status, and error tests.

No new helper module is justified for one local MCP presentation consumer. No
other production or test file is necessary.

## Explicitly Unchanged

- `PrinterStatus`, `PrinterState`, and `AMSInfo` model semantics;
- `src/print_engineer/core/types.py`;
- Bambu telemetry decoding and `_BambuStatusAccumulator`;
- `mc_remaining_time` and layer parsing/accumulation;
- `src/print_engineer/adapters/printer/bambu.py`;
- `src/print_engineer/adapters/printer/transport.py`;
- MQTT topic, fixed pushall payload, QoS, cooldown, subscription, and lifecycle;
- printer interfaces and printer-control capability;
- MCP registration/server lifecycle and existing error responses;
- retained-session, bounded-transport, and monitor-core work;
- recommendation engine and all LLM behavior;
- configuration, dependencies, hardware tests, and integration tests;
- the four intentional untracked files listed in Baseline.

## Test Plan

All tests are hermetic and require no network or physical printer.

### Pure formatter exact-string tests

Import the private formatter into `tests/unit/test_printer_mcp.py` and cover:

1. Full connected printing status:
   `PRINTING`, `0.73`, layers `184/252`, remaining `32`, nozzle `220/220`,
   bed `55/55`, connected AMS -> the exact Objective summary.
2. Printing with remaining time `None`: duration fragment absent with clean
   separators.
3. Printing without layers: no layer placeholder or malformed separator.
4. Layers: both, current-only, total-only, neither, and inconsistent values
   preserved literally.
5. Partial temperatures: current-only and target-only for both components.
6. Temperature precision: pin at least `54.9375 -> "54.9"`,
   `220.03125 -> "220"`, `54.95 -> "55"`, `55.0 -> "55"`, and
   `20.06 -> "20.1"`; also prove `NaN`, positive infinity, and negative
   infinity are omitted rather than rendered.
7. Paused with remaining time: exact `Paused` and duration wording.
8. Idle with normalized stale `139`: summary omits duration while the
   separately serialized structured field remains `139`.
9. Error with a prior remaining value: exact `Printer error`, no diagnostic or
   duration claim.
10. Connected unknown with no optionals: exact `Status unknown`.
11. Disconnected with no values: exact `Printer disconnected`.
12. Disconnected with populated structured fields: still exactly
    `Printer disconnected`, proving no freshness claim.
13. Connected offline: exact `Offline`.
14. Progress: zero, normal fraction, half-up boundary, below/above range, and
    non-finite omission.
15. AMS: connected, explicitly not connected, and `None` omission; slot labels
    do not affect text.
16. Determinism: repeated calls with one immutable status produce identical
    strings.

### MCP integration and regression tests

- Invoke the real registered `printer.status` path with the fake adapter.
- Assert every existing structured key and value remains unchanged.
- Assert exact top-level `summary` text.
- Count `get_status()` and assert exactly one call for one tool invocation.
- Assert one adapter instance is created.
- Assert the formatter itself causes no adapter/transport interaction.
- For an IDLE status with structured remaining `139`, assert the status mapping
  retains `139` while summary omits it.
- For every existing `PrinterError` path, assert the response remains
  `{"ok": False, "error": ...}` and has no `summary` key.
- Keep all existing configuration-precedence and serialization tests intact.

These tests must fail if Build retrieves twice, places summary inside the
structured status mapping, mutates normalized fields, exposes stale IDLE
remaining time, emits float artifacts, or changes errors.

## Verification Commands

Focused MCP/formatter tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_printer_mcp.py
```

Relevant printer regression suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_interfaces.py tests/unit/test_printer_transport.py tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
```

If the known Windows pytest temp-root `PermissionError` occurs, retry each
affected command once with a unique `--basetemp` under system TEMP.

Ruff and Mypy over the exact Build scope:

```powershell
.\.venv\Scripts\ruff.exe check src/print_engineer/mcp/tools/printer.py tests/unit/test_printer_mcp.py
.\.venv\Scripts\python.exe -m mypy src/print_engineer/mcp/tools/printer.py tests/unit/test_printer_mcp.py
```

If focused verification succeeds, run the full unit suite with all Bambu
hardware variables removed from the child environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/
```

Classify known unrelated or Windows runtime failures rather than fixing them.
Do not run integration or hardware tests and do not enable hardware gates.

## Hardware Verification Decision

NO HARDWARE VERIFICATION REQUIRED.

The summary is a pure deterministic transformation of the normalized
`PrinterStatus` already fetched and reviewed. It introduces no telemetry,
protocol assumption, request, control behavior, or physical-device claim.

## Risks / Unverified Assumptions

- Unicode middle-dot and degree symbols are already valid UTF-8 JSON string
  content, but exact encoding must be pinned by the real MCP JSON test.
- Current progress normalization does not enforce `[0, 1]`; display clamping
  is intentionally presentation-only and could differ from the raw structured
  value. Tests must make that distinction explicit.
- Remaining time can be latest-valid but stale across sparse state changes.
  State-based omission outside PRINTING/PAUSED avoids presenting it as current
  without altering the structured field.
- `is_connected=False` with populated values is possible through manual model
  construction even though production accumulation normally couples received
  telemetry with true. Returning only `Printer disconnected` is the deliberate
  no-freshness-claim rule.
- Float fixed-point rounding follows Python formatting behavior. Boundary
  examples must be pinned on the supported project runtime; Build must not add
  Decimal or another dependency solely for temperature display.

No repository blocker was found. These are explicit presentation contracts,
not unresolved implementation questions.

## Acceptance Criteria

DONE only if:

- `printer.status` performs exactly one `get_status()` call per invocation;
- summary derives only from that returned `PrinterStatus` object;
- successful responses add one deterministic top-level `summary` string;
- every existing structured status field remains unchanged;
- errors remain unchanged and contain no summary;
- no second adapter, retrieval, tool invocation, cache, worker, or I/O exists;
- no MQTT, refresh, cooldown, lifecycle, parser, or printer-control code changes;
- state/connection wording and fragment order exactly match this plan;
- progress presentation uses finite checking, display clamping, and deterministic
  half-up whole-percent rounding;
- layer cases format independently without inference;
- remaining time appears only for connected PRINTING/PAUSED states and uses
  whole minutes without conversion;
- stale IDLE/error/unknown/offline remaining values remain structured but are
  omitted from summary;
- temperatures use finite checking and the exact one-decimal/strip-zero policy;
- AMS wording uses only the explicit connection flag and omits unavailable AMS;
- partial statuses always produce a non-empty clean string;
- no diagnosis, recommendation, localization, clock, randomness, or hidden
  state inference is introduced;
- focused pytest, Ruff, and Mypy pass;
- no hardware verification is required or executed.

## Definition of Done

The increment is complete when the two scoped files implement and prove the
exact additive summary contract, the real MCP path performs one retrieval and
preserves all structured/error behavior, relevant regressions are green, the
diff contains no other file, and Review independently confirms zero added
network or MQTT interaction.

## Approval Questions

1. Should summary be additive on existing `printer.status`?
   - **Proposed resolution: yes.** One top-level success field preserves one
     retrieval and avoids a second tool/status call.
2. Where should the pure formatter live?
   - **Proposed resolution:** as a private helper in
     `src/print_engineer/mcp/tools/printer.py`, beside the existing serializer.
3. What exact state wording is used?
   - **Proposed resolution:** Idle, Printing, Paused, Printer error, Status
     unknown, and Offline, with disconnected connection precedence.
4. What exact separator/order is used?
   - **Proposed resolution:** ` · `; state, progress, layers, remaining time,
     nozzle, bed, AMS.
5. How is progress rounded?
   - **Proposed resolution:** finite fraction, display-clamped to `[0,1]`, then
     nearest whole percent with halves upward.
6. How are partial layer fields rendered?
   - **Proposed resolution:** both `Layer C / T`, current `Layer C`, total
     `Total layers T`, neither omitted.
7. In which states is remaining time displayed?
   - **Proposed resolution:** only connected PRINTING and PAUSED.
8. How are minutes formatted?
   - **Proposed resolution:** `About N min remaining`, verbatim whole minutes,
     no singular variation or hour conversion.
9. What temperature precision is used?
   - **Proposed resolution:** one fixed decimal, strip trailing `.0`.
10. How are partial current/target temperatures rendered?
    - **Proposed resolution:** `Name current °C`, `Name target target °C`, or
      `Name current / target °C`.
11. What exact AMS wording is supported?
    - **Proposed resolution:** `AMS connected`, `AMS not connected`, or omit
      when unavailable; slots are not summarized.
12. How is disconnected status rendered?
    - **Proposed resolution:** exactly `Printer disconnected`, with all other
      summary fragments suppressed and structured data untouched.
13. Can summary generation be proven to cause zero additional retrievals?
    - **Proposed resolution: yes.** A pure helper plus a real MCP fake-adapter
      call counter proves one existing retrieval and zero formatter retrievals.
14. What exact Build scope is required?
    - **Proposed resolution:** exactly `printer.py` and `test_printer_mcp.py`.
15. Is hardware verification unnecessary?
    - **Proposed resolution: yes.** This is presentation-only over established
      normalized data.

No presentation or software-semantic question is intentionally left open.

## Final Verdict

The checkpointed repository supports this as a two-file MCP-presentation
increment. The grammar, partial-data rules, freshness-sensitive remaining-time
omission, numeric formatting, single-retrieval invariant, error contract, and
safety boundary are specified sufficiently for independent review.

PROPOSED — READY FOR INDEPENDENT PLAN REVIEW
