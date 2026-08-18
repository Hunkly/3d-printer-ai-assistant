# Phase 2 — Remaining Time v1

Status: APPROVED

## Objective

Extend the normalized public `PrinterStatus` and the existing `printer.status`
MCP success response with one optional field:

```python
remaining_time_minutes: int | None = None
```

Map Bambu telemetry `print.mc_remaining_time` to that field as the latest valid
printer-reported estimate of remaining print duration, expressed in whole
minutes.

The value is an estimate. It does not promise exact countdown behavior,
monotonic decrease, minute-level accuracy, or a correct ETA. It is not derived
from slicer output, progress, layers, state, or any local prediction algorithm.

This is a status-data-only increment. It reuses the current
`_BambuStatusAccumulator`, `PrinterStatus`, and MCP serializer without changing
MQTT, lifecycle, refresh, or printer-control behavior.

## Established Evidence

Repository and physical A1 evidence already establishes that:

- a physical A1 full `print.push_status` snapshot with exact integer `msg == 0`
  contains `mc_remaining_time`;
- passive physical A1 telemetry also contains `mc_remaining_time`;
- positive values including `139`, `138`, and `159` were observed while the
  printer was actively printing;
- the prior physical diagnostic recorded safe field presence and values but did
  not preserve the exact JSON type;
- current production does not normalize or expose `mc_remaining_time`.

External source-level evidence establishes that:

- maintained Bambu integrations map raw `mc_remaining_time` to remaining print
  duration in minutes;
- `ha-bambulab` assigns `mc_remaining_time` directly to its `remaining_time`
  model field;
- its Home Assistant duration sensor declares minutes as the native unit;
- its end-time calculation uses `timedelta(minutes=remaining_time)`;
- independent consumers similarly interpret the raw value as minutes;
- captured reports contain positive JSON integers while printing and explicit
  integer zero while idle or finished.

Weaker community documentation describes the field as seconds. The approved
research conclusion resolves that conflict in favor of the maintained direct
consumers and captured telemetry: raw `mc_remaining_time` is best represented
publicly as a whole-minute estimate.

Evidence remains deliberately separated:

- hardware-proven in this repository: A1 field presence and positive observed
  values while printing;
- externally source-proven: whole-minute interpretation and captured integer
  positive/zero representations;
- to be software-proven by Build and Review: strict normalization,
  accumulation, public model behavior, and MCP serialization.

## Current Architecture

`PrinterStatus` is a frozen dataclass in
`src/print_engineer/core/types.py`. All fields are defaulted. The current layer
fields are the last positional fields, so the new field can be appended without
changing the positional meaning of any existing argument.

`_BambuStatusAccumulator` in
`src/print_engineer/adapters/printer/bambu.py` is the connection-scoped owner of
normalized last-known printer status. Every structurally valid `print` report,
including full snapshots and passive sparse deltas, passes through `apply()`.
Missing or malformed modeled values preserve previous valid accumulator state.
`snapshot()` is the sole production conversion to `PrinterStatus`.

The existing private layer normalizer accepts exact integers and ASCII decimal
strings. It cannot be reused unchanged for remaining time because the approved
remaining-time contract rejects every string representation.

`_serialize_status()` in
`src/print_engineer/mcp/tools/printer.py` receives an already-normalized
`PrinterStatus` and builds the flat successful `printer.status` mapping. It does
not inspect MQTT reports.

Current construction-site inspection found:

- `_BambuStatusAccumulator.snapshot()` as the only production
  `PrinterStatus(...)` constructor;
- the MCP fake-status helper and unavailable-status construction in
  `tests/unit/test_printer_mcp.py`;
- default and selected-keyword construction in
  `tests/unit/test_interfaces.py`.

No CLI serializer, alternate status model, or other production consumer
requires modification. The exact Build scope is therefore six files.

## Scope

This increment includes only:

1. one appended optional public `PrinterStatus` field;
2. one private, strict remaining-time normalization path in `bambu.py`;
3. connection-scoped sparse accumulation of the latest valid value;
4. flat serialization through the existing MCP status serializer;
5. hermetic unit and regression coverage for the public model, accumulator,
   existing status fields, and MCP response.

## Non-Goals

This increment does not add or change:

- estimated finish time, ETA timestamps, or time formatting;
- elapsed time, total duration, slicer-estimated duration, or historical
  prediction;
- print stage, job name, filename, task ID, project ID, or remaining seconds;
- accuracy guarantees, monotonic countdown rules, or local countdown timers;
- derivation from `gcode_state`, `mc_percent`, `layer_num`, or
  `total_layer_num`;
- retained-session design, bounded transport, monitor-core, notifications, or
  persistence;
- printer control, automatic slicing, or automatic printing;
- MQTT topics, payloads, publishing, request APIs, cooldowns, retries,
  subscriptions, queues, connection lifecycle, or refresh behavior.

## Public Status Contract

Append this field after every existing `PrinterStatus` field:

```python
remaining_time_minutes: int | None = None
```

The name is intentionally unit-bearing. `remaining_time` is rejected because
it would make the public unit ambiguous. A generic duration object is not
needed by the current flat status architecture.

The field means only the latest valid printer-reported estimate of remaining
print duration in whole minutes within the current accumulator lifecycle.
`None` means no valid value has been observed. The field is an estimate and
does not imply countdown precision, monotonicity, ETA accuracy, or a particular
printer state.

## Remaining-Time Semantics

The printer's telemetry is authoritative for this field.

- A positive value is the current whole-minute estimate reported by the
  printer.
- Zero is a valid explicit value meaning the printer reports no remaining
  print duration.
- Missing is not zero and does not change accumulated state.
- Malformed is not zero and does not change accumulated state.
- Downward updates such as `139` to `138` are retained truthfully.
- Upward revisions such as `139` to `145` are also retained truthfully. The
  estimate is not required to be monotonic.
- No state, progress, or layer value may synthesize, reject, clamp, or modify
  remaining time.

Representative state-independent behavior:

- `gcode_state="PAUSE"` with `mc_remaining_time=52` stores `52`;
- `gcode_state="IDLE"` with the field omitted preserves the previous valid
  value and does not synthesize zero;
- `gcode_state="FINISH"` with explicit `mc_remaining_time=0` stores `0`;
- progress `1.0` or equal current/total layers does not synthesize zero.

The public value is not a timestamp, elapsed time, total print duration, or
locally calculated ETA. This increment must not calculate an end timestamp.

## Source Normalization

Add one private remaining-time normalization helper in `bambu.py`. It must not
be public and must not broaden the layer parser.

Its exact contract is:

- accept an exact Python `int` only when non-negative;
- reject `bool` explicitly before integer acceptance, because `bool` subclasses
  `int`;
- preserve accepted values without unit conversion;
- return `None` for every unsupported value without raising.

Accepted examples:

```text
0   -> 0
1   -> 1
139 -> 139
```

Rejected representations include:

- `True` and `False`;
- negative integers;
- floats, including `139.0`;
- numeric strings, including `"139"`, `" 139 "`, and `"0"`;
- `None`;
- empty or malformed strings;
- lists, mappings, and other collections;
- arbitrary objects.

There is no rounding, truncation, float parsing, string stripping, decimal
parsing, exponent parsing, or unit conversion. Remaining time intentionally
differs from layer normalization: source evidence supports integer wire values
and does not justify string coercion.

## Accumulator Behavior

Extend `_BambuStatusAccumulator` with:

```python
self._remaining_time_minutes: int | None = None
```

In `apply()`:

- if `mc_remaining_time` is absent, leave the accumulator unchanged;
- if it is present and normalizes to a valid integer, replace the accumulator,
  including replacement by explicit zero;
- if it is present but malformed, preserve the prior valid value;
- do not consult state, progress, current layer, or total layers.

Required sequence:

```text
first valid 139       -> 139
field omitted         -> 139
valid 138             -> 138
malformed value       -> 138
explicit zero         -> 0
```

A separate upward-revision sequence must prove `139 -> 145` results in `145`.
First observation missing or malformed leaves the value `None`.

`snapshot()` supplies the accumulated value to `PrinterStatus`. No second
parser, separate cache, full-snapshot-only path, MCP parser, timer, or derived
calculation may be introduced.

## MCP Contract

Add one flat key to the successful `printer.status` status mapping:

```json
{
  "remaining_time_minutes": 139
}
```

The value is an integer or JSON `null`. `_serialize_status()` reads only
`status.remaining_time_minutes`. It must not inspect `mc_remaining_time`, raw
MQTT reports, printer state, progress, or layers.

The successful response retains every existing key and meaning. The structured
error response remains unchanged. Do not add a nested remaining-time object,
unit metadata, end timestamp, remaining-seconds field, or formatted string.

## Backward Compatibility

- Append the new field after `total_layers`; do not insert it before an existing
  field.
- Default it to `None`.
- Preserve frozen dataclass semantics.
- `PrinterStatus()` continues to succeed.
- Existing positional arguments retain their exact meanings.
- Existing selected-keyword and full-keyword constructors remain valid without
  supplying `remaining_time_minutes`.
- Fake/test printers that omit the field receive `None`.
- The abstract `Printer` interface remains unchanged because it already returns
  `PrinterStatus`.
- MCP consumers receive one additive nullable key; no existing key changes
  name, type, or meaning.

Build must search construction sites again before editing. If a new construction
site has appeared since this plan, update it only when necessary for correctness
and report the scope conflict for review rather than silently expanding the
approved scope.

## MQTT Safety Boundary

This is a status-data-only increment. Its required MQTT impact is exactly:

- new MQTT publish paths: 0;
- new MQTT request APIs: 0;
- new generic MQTT APIs: 0;
- new printer-control paths: 0;
- new background threads or workers: 0;
- new reconnect behavior: 0;
- new periodic refresh behavior: 0.

The existing fixed informational refresh remains unchanged. Build and Review
must verify that the increment does not modify `transport.py`,
`request_status_refresh()`, the `pushall` topic/payload/QoS, refresh cooldown,
subscription/report queues, connection lifecycle, retry behavior, or publish
count.

## Exact Build Scope

Repository inspection establishes that these six files are sufficient.

Production files:

- `src/print_engineer/core/types.py`
  - append `remaining_time_minutes` to `PrinterStatus`;
- `src/print_engineer/adapters/printer/bambu.py`
  - add strict private integer-only normalization, accumulator storage/update,
    and snapshot mapping;
- `src/print_engineer/mcp/tools/printer.py`
  - serialize the normalized field as one flat key.

Test files:

- `tests/unit/test_interfaces.py`
  - verify default/backward-compatible model construction, explicit field
    construction, field order compatibility where appropriate, and frozen
    behavior remains intact;
- `tests/unit/test_bambu_printer_adapter.py`
  - verify strict normalization, zero, missing/malformed preservation, estimate
    revisions, state independence, sparse accumulation, and existing status
    regression behavior through the real adapter/accumulator path;
- `tests/unit/test_printer_mcp.py`
  - verify recognized and unavailable values through the existing MCP
    serializer/tool path and preserve success/error response shape.

No other production or test file is required. In particular,
`tests/unit/test_printer_transport.py` remains a regression suite to execute,
not a Build-scope file to modify.

## Explicitly Unchanged

- `src/print_engineer/adapters/printer/transport.py`;
- `request_status_refresh()` and its topic, payload, QoS, and sequence handling;
- refresh eligibility, cooldown, concurrency reservation, and retry behavior;
- MQTT connection, subscription, queue, disconnect, and cleanup lifecycle;
- `src/print_engineer/mcp/server.py` and MCP server lifecycle;
- printer interfaces and all control methods;
- retained-session work and
  `plans/phase-2-retained-status-session-v1.md`;
- bounded-transport work and
  `plans/phase-2-bounded-mqtt-transport-lifecycle.md`;
- monitor-core and `plans/phase-2-printer-monitor-core.md`;
- recommendation code and all Phase 3 work;
- dependencies and configuration;
- integration hardware tests.

## Test Plan

### Public model and compatibility

- `PrinterStatus()` succeeds and `remaining_time_minutes is None`.
- Existing selected-keyword construction succeeds without the new argument.
- Existing positional field meanings remain unchanged after appending the new
  field.
- Explicit `PrinterStatus(remaining_time_minutes=139)` retains `139` under the
  frozen dataclass.

### Exact integer and zero normalization

Through the real adapter/accumulator path, prove:

- `mc_remaining_time=139` produces `remaining_time_minutes == 139`;
- `mc_remaining_time=0` produces integer `0`, not `None`.

### Rejected representations

Parameterize at least:

- `True`;
- `False`;
- `-1`;
- `139.0`;
- `"139"`;
- `" 139 "`;
- `"0"`;
- `None`;
- empty and malformed strings;
- list and mapping values.

For first observation, every rejected value leaves the public field `None`.
Assertions must explicitly prove that `False` does not become `0`, `True` does
not become `1`, and `"139"` is rejected. These tests intentionally distinguish
remaining-time normalization from layer normalization.

### Sparse accumulation and estimate revisions

On one actual retained accumulator lifecycle through the adapter:

1. apply `139` and assert `139`;
2. apply a structurally valid report omitting `mc_remaining_time` and assert
   `139`;
3. apply `138` and assert `138`;
4. apply a malformed value and assert `138`;
5. apply explicit `0` and assert `0`.

Use a separate sequence or assertion to prove `139 -> 145` results in `145`.
Tests must not encode monotonic decrease as a requirement.

Parameterize malformed-after-valid cases sufficiently to catch bool, float,
string, negative, `None`, and collection coercion or erasure.

### State independence

- `PAUSE` plus explicit `52` stores `52`.
- `IDLE` with the field omitted does not synthesize zero.
- `FINISH` plus explicit zero stores zero.
- progress completion and equal current/total layers do not derive or alter
  remaining time.

### Existing status regression

In a report containing remaining time, assert unchanged normalization for:

- state and connectivity;
- progress;
- current and total layers;
- bed and nozzle temperatures;
- target temperatures;
- AMS.

Existing adapter and accumulator tests remain unchanged except for necessary
additive assertions. Tests must remain hermetic and use fake transports only.

### MCP

- A normalized `PrinterStatus(remaining_time_minutes=139)` produces the flat
  key with integer `139` through the real MCP tool/serializer path.
- A default status serializes the key as `None`/JSON `null`.
- Existing success keys remain present and unchanged.
- Existing structured error behavior remains unchanged.
- MCP tests must supply a normalized `PrinterStatus`; they must not fabricate or
  parse raw `mc_remaining_time`.

### MQTT and lifecycle regression

Run the existing transport/adapter/MCP regression suite without changing the
transport test file. Confirm retained adapter calls, standalone refresh,
cooldown, lifecycle cleanup, and narrow publish behavior are unchanged.

## Verification Commands

Inspect before Build:

```powershell
git status --short
```

Focused model/adapter/MCP tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_interfaces.py tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
```

Printer transport regression:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_printer_transport.py tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
```

Ruff over the exact Build scope:

```powershell
.\.venv\Scripts\ruff.exe check src/print_engineer/core/types.py src/print_engineer/adapters/printer/bambu.py src/print_engineer/mcp/tools/printer.py tests/unit/test_interfaces.py tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
```

Mypy over the exact Build scope:

```powershell
.\.venv\Scripts\python.exe -m mypy src/print_engineer/core/types.py src/print_engineer/adapters/printer/bambu.py src/print_engineer/mcp/tools/printer.py tests/unit/test_interfaces.py tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
```

If focused verification is green, run the full unit suite in a child environment
with `BAMBU_IP`, `BAMBU_SERIAL`, `BAMBU_ACCESS_CODE`,
`RUN_BAMBU_LAN_HARDWARE_TEST`, and `RUN_BAMBU_LAN_STATUS_REFRESH_TEST` removed:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/
```

If the known Windows pytest temp-root permission problem occurs, retry each
affected command once with a unique `--basetemp` under the system TEMP
directory. Classify every non-green broader result as `INTRODUCED`,
`PRE-EXISTING / UNRELATED`, `ENVIRONMENT / RUNTIME`, or `UNKNOWN`; do not fix
unrelated failures.

Inspect after Build:

```powershell
git status --short
git diff --stat
git diff
```

Build and Review must not run integration or physical-printer tests.

## Hardware Verification Decision

No new physical hardware request is required.

The conservative software contract relies on already established field
presence, source-level whole-minute interpretation, and captured integer
positive/zero reports. Hermetic tests can fully prove the proposed parser,
accumulator, model, and serializer behavior.

After Build, claims must remain bounded:

- this project's A1 proved field presence and positive observations while
  printing;
- external/source evidence established whole-minute interpretation and integer
  captured representations;
- software tests proved strict integer normalization and public behavior;
- this project's A1 did not directly prove JSON type, UI agreement, idle zero,
  or pause behavior.

Any future desire to prove those A1-specific facts requires a separately
authorized sanitized diagnostic. It is not a prerequisite for this increment.

## Risks / Unverified Assumptions

- This project's physical capture did not preserve JSON type. Strict
  integer-only parsing will intentionally leave unsupported future
  representations unavailable rather than coerce them.
- Some weaker community documentation labels the raw unit as seconds. The
  established research resolution favors maintained direct mappings and
  captured behavior supporting minutes.
- Printer estimates may move upward or downward and may be inaccurate. The
  public name and documentation must preserve estimate semantics.
- A sparse terminal-state report may omit remaining time, so the prior valid
  estimate can remain until explicit telemetry updates it. Synthesizing zero
  from state would violate the approved telemetry-authoritative contract.
- Paused, preparation, and error-state countdown behavior is not guaranteed.
  Explicit valid values are retained without state-dependent interpretation.

These are bounded public-contract limitations, not blockers.

## Acceptance Criteria

- `PrinterStatus` exposes
  `remaining_time_minutes: int | None = None` appended after existing fields.
- Existing default, positional, selected-keyword, and full-keyword constructors
  remain compatible.
- `print.mc_remaining_time` maps through `_BambuStatusAccumulator` to the public
  field.
- Exact non-negative integers are accepted without conversion.
- `bool`, strings, floats, negatives, `None`, collections, and malformed values
  are rejected without raising.
- Zero is preserved and replaces a prior positive value.
- Missing values preserve prior valid accumulated state.
- Malformed values preserve prior valid accumulated state.
- First missing or malformed observation remains `None`.
- Both downward and upward firmware estimate revisions are preserved
  truthfully.
- No state, progress, or layer inference, clamping, clearing, or derivation is
  introduced.
- `_BambuStatusAccumulator` remains the sole production normalization and
  accumulation source.
- MCP exposes one flat nullable `remaining_time_minutes` key and does not inspect
  raw MQTT fields.
- Existing status fields and MCP error behavior remain unchanged.
- No extra time, ETA, job, or stage field is added.
- MQTT publish paths, request APIs, generic APIs, control paths, workers,
  reconnect, periodic refresh, cooldown, and lifecycle behavior remain
  unchanged.
- Changes are limited to the exact six Build-scope files.
- Focused pytest, transport regression, Ruff, and Mypy checks pass.
- Broader failures are independently classified and no unrelated failure is
  fixed.
- No hardware test is run or claimed.

## Definition of Done

The increment is done only when the single optional public field is appended,
strict integer-only telemetry normalization and sparse accumulation are proven,
zero/missing/malformed and estimate-revision semantics are covered, MCP adds
only the flat nullable key, existing status behavior remains intact, the exact
six-file diff passes required verification, and independent Review confirms
zero MQTT or lifecycle impact.

Completion does not include approval of this plan, hardware verification, an
ETA, elapsed/total duration, remaining seconds, or any parked Phase 2 work.

## Approval Questions

1. Is `remaining_time_minutes` the correct public name?
   - **Proposed resolution: yes.** It communicates both meaning and unit and
     avoids the ambiguity of `remaining_time`.
2. Is whole minutes sufficiently supported?
   - **Proposed resolution: yes.** Maintained direct integrations, their unit
     declarations and end-time calculations, independent consumers, and
     captured values outweigh weaker conflicting documentation.
3. Should source parsing accept integer only?
   - **Proposed resolution: yes.** Captured reports support integers; there is
     no reliable requirement for string or float coercion.
4. Is zero a valid explicit value?
   - **Proposed resolution: yes.** Captured idle/finished reports contain
     integer zero, and zero must replace a prior positive estimate.
5. Should missing preserve prior state?
   - **Proposed resolution: yes.** Reports are sparse, and omission is not an
     explicit zero update.
6. Should malformed preserve prior state?
   - **Proposed resolution: yes.** Unsupported input must not erase the latest
     valid printer observation or synthesize zero.
7. Should state transitions ever synthesize zero?
   - **Proposed resolution: no.** Only explicit printer-provided remaining-time
     telemetry updates this field.
8. Is the six-file scope sufficient?
   - **Proposed resolution: yes.** Current construction and serialization site
     inspection finds no additional production consumer or test file requiring
     modification.
9. Is additional hardware verification unnecessary?
   - **Proposed resolution: yes.** The conservative optional contract can be
     proven hermetically while keeping hardware/source/software evidence
     explicitly separated.

Independent plan review must confirm or reject these proposed resolutions
before Build. No software-semantic question is intentionally left open.

## Final Verdict

The repository architecture supports this as a narrow additive status-data
increment in exactly six files. The proposed contract is explicit about unit,
estimate semantics, strict source grammar, zero, sparse/malformed preservation,
state independence, and hardware evidence limits. No repository blocker was
found.

PROPOSED — READY FOR INDEPENDENT PLAN REVIEW
