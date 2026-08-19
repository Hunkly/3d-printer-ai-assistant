# Phase 2 — Rich Printer Status v1

Status: APPROVED

## Objective

Extend the public `PrinterStatus` and the existing `printer.status` MCP response
with two optional layer fields derived from Bambu A1 status telemetry:

- `print.layer_num` → `PrinterStatus.current_layer`
- `print.total_layer_num` → `PrinterStatus.total_layers`

This is a status-data-only increment. It reuses the current cold standalone
status lifecycle, fixed informational refresh, `_BambuStatusAccumulator`, and
MCP tool architecture without changing MQTT or printer behavior.

## Established Hardware Evidence

The physical Bambu Lab A1 has already verified that the existing narrowly
authorized `pushing.pushall` request produces a candidate full snapshot with:

- `print.command == "push_status"`
- `print.msg` whose exact type is `int` and whose value is `0`

The sanitized physical snapshot field-name capture included both `layer_num`
and `total_layer_num`, together with the already modeled state, progress,
temperature, and AMS fields. The production accumulator normalized that
snapshot successfully to printing state, progress, temperatures, and AMS
slots.

This evidence proves physical field presence, not the concrete runtime value
types or the actual layer values in that observation. This plan therefore does
not claim that a physical `current_layer` or `total_layers` value has already
been normalized.

## Current Architecture

`PrinterStatus` is a frozen dataclass in
`src/print_engineer/core/types.py`. Every existing field has a default, so
callers may construct it with no arguments or with only selected keyword
arguments.

`BambuPrinterAdapter` owns one `_BambuStatusAccumulator` for an explicit
retained connection and creates one connection-scoped accumulator for a
standalone call. Every structurally valid `print` report is applied through
that accumulator. The accumulator already implements sparse-delta semantics:
missing modeled fields preserve their prior values, valid newer values replace
prior values, and malformed modeled values do not erase a prior valid value.
Its `snapshot()` method is the sole conversion into `PrinterStatus`.

`src/print_engineer/mcp/tools/printer.py` receives an already normalized
`PrinterStatus` and serializes its fields into a flat JSON-compatible mapping.
It does not inspect MQTT payloads.

Repository construction-site inspection found only:

- `_BambuStatusAccumulator.snapshot()` in production;
- the MCP printer fake-status helper in tests;
- shared-interface tests that exercise default and selected-keyword
  construction.

There is no separate CLI printer-status serializer or other production
`PrinterStatus` constructor that requires modification.

## Scope

The increment includes only:

1. two optional, defaulted integer fields on `PrinterStatus`;
2. defensive layer-value normalization in `_BambuStatusAccumulator`;
3. sparse accumulation of the two layer values;
4. flat serialization of both values by `printer.status`;
5. hermetic unit and regression coverage for the public model, Bambu
   normalization/accumulation, and MCP response.

## Non-Goals

This increment does not add or change:

- retained MQTT sessions or the parked retained-session design;
- Paho lifecycle behavior;
- background workers, reconnect, monitoring, caching, or persistence;
- alerts or historical status;
- MQTT topics, request payloads, QoS, cooldowns, retries, or publish count;
- generic MQTT APIs or printer-control operations;
- periodic status refresh;
- `mc_remaining_time` or any remaining-time unit/semantic contract;
- job names, filenames, task/project identifiers, or raw MQTT payloads;
- layer-derived state, progress, or print-completion inference.

## Public Status Contract

Append the following defaulted fields to the end of the existing frozen
`PrinterStatus` dataclass:

```python
current_layer: int | None = None
total_layers: int | None = None
```

Appending rather than inserting preserves the positional meaning of every
existing field. Defaults preserve all existing zero-argument, partial-keyword,
and full-keyword construction sites.

The fields mean only the latest valid values actually observed in the current
accumulator lifecycle. `None` means no valid value has been observed. They do
not imply that the printer is printing or that a layer transition has occurred.

## Layer Normalization Semantics

Add one private integer normalization helper in `bambu.py`; do not add a
general public parser. Its exact contract is:

- accept an exact Python `int` when it is non-negative;
- reject `bool` explicitly even though `bool` is an `int` subclass;
- accept a string after surrounding whitespace is removed only when the
  remaining text is one or more ASCII decimal digits (`0`–`9`);
- convert an accepted decimal string with base 10;
- accept zero (`0` and `"0"`), because zero is a representable telemetry value
  and must not be confused with missing data;
- reject negative integers and signed strings;
- reject floats, including integral-looking floats such as `10.0`;
- reject empty strings, decimal/fractional strings, exponent notation,
  booleans, lists, mappings, `None`, and other malformed values;
- return `None` for every rejected value without raising.

This is intentionally stricter than temperature/progress parsing because a
layer number is a discrete count, while those existing fields are continuous
numeric measurements. Accepting exact integers and base-10 integer strings
covers the plausible JSON representations without rounding, truncation, or
fabrication. The physical diagnostic did not record which representation the
current A1 firmware used, so the plan must not assert one.

`current_layer` and `total_layers` are normalized independently. If both are
valid but `current_layer > total_layers`, preserve both observed values
truthfully. The adapter has insufficient protocol evidence to repair, discard,
or infer either field based on their relationship. It must not infer progress
from layers.

## Accumulator Behavior

Extend `_BambuStatusAccumulator` with two fields initialized to `None`:

- `_current_layer`
- `_total_layers`

In `apply()`:

- when `layer_num` is absent, leave `_current_layer` unchanged;
- when `layer_num` is present and normalizes successfully, replace
  `_current_layer`;
- when `layer_num` is present but malformed, preserve the prior valid value;
- apply identical rules from `total_layer_num` to `_total_layers`.

Consequently:

- a full snapshot containing both fields sets both;
- a later delta omitting both preserves both;
- a later valid `layer_num`-only delta updates `current_layer` while preserving
  `total_layers`;
- a later valid `total_layer_num`-only delta updates `total_layers` while
  preserving `current_layer`;
- fields never observed validly remain `None`;
- an explicit valid zero is retained and returned;
- malformed newer values never erase valid accumulated values.

`snapshot()` supplies the two accumulated values to `PrinterStatus`. No second
parser, normalization model, or full-snapshot-only path is introduced. Both
full snapshots and passive deltas continue through the same `apply()` method.

## MCP Contract

Add two flat keys to the successful `printer.status` status object:

```json
{
  "current_layer": 10,
  "total_layers": 100
}
```

Each value is an integer or JSON `null`. The complete response retains its
existing shape and fields; there is no nested layer object and no error-contract
change.

`_serialize_status()` reads only `status.current_layer` and
`status.total_layers`. It must not parse MQTT fields, infer layer values, or
derive progress.

## Backward Compatibility

- Both new dataclass fields are appended and default to `None`.
- Existing positional construction retains its current field mapping.
- Existing keyword and default construction remains valid.
- The abstract `Printer` interface does not change; it already returns
  `PrinterStatus`.
- Fake/test printers may omit the new keywords and receive `None` defaults.
- Consumers that deserialize the MCP mapping gain two additive nullable keys;
  no existing key changes name, type, or meaning.
- Existing state, connectivity, temperatures, progress, and AMS normalization
  remain untouched.

## MQTT Safety Boundary

This increment adds no MQTT behavior.

- new MQTT publish paths: 0
- new generic MQTT APIs: 0
- new printer-control paths: 0
- new reconnect behavior: 0
- new background threads: 0

The existing approved behavior remains unchanged:

- at most one fixed informational `pushing.pushall` for an eligible standalone
  `get_status()`;
- five-minute process-local cooldown per configured serial;
- no automatic publish retry;
- explicit retained adapter calls remain passive;
- no general publish or arbitrary request surface.

## Exact Build Scope

Production files:

- `src/print_engineer/core/types.py`
  - append the two optional public fields to `PrinterStatus`;
- `src/print_engineer/adapters/printer/bambu.py`
  - add private integer normalization and accumulator storage/update/snapshot
    mapping;
- `src/print_engineer/mcp/tools/printer.py`
  - serialize the two normalized fields.

Test files:

- `tests/unit/test_interfaces.py`
  - verify default/backward-compatible `PrinterStatus` construction and the
    optional layer fields;
- `tests/unit/test_bambu_printer_adapter.py`
  - verify full, missing, sparse-delta, partial-update, zero, malformed, and
    unchanged existing-field behavior through the real adapter/accumulator;
- `tests/unit/test_printer_mcp.py`
  - verify present and unavailable layer serialization through the real MCP
    serializer/tool path.

No integration-test change is required. Existing physical field presence is
already established, and this increment does not change the hardware gate or
MQTT behavior.

## Explicitly Unchanged

- `src/print_engineer/adapters/printer/transport.py`, including Paho lifecycle;
- `request_status_refresh()`, its fixed request topic/payload/QoS, and sequence
  handling;
- refresh eligibility, process-local cooldown, and concurrency reservation;
- MQTT subscription/report queue behavior and error mapping;
- `src/print_engineer/mcp/server.py` and MCP server lifecycle;
- `src/print_engineer/core/interfaces/printer.py` and printer-control methods;
- all printer start/stop/pause/resume/temperature/G-code/upload behavior;
- core recommendation and Phase 3A code;
- dependencies and configuration;
- `plans/phase-2-retained-status-session-v1.md`;
- `plans/phase-2-bounded-mqtt-transport-lifecycle.md`;
- `plans/phase-2-printer-monitor-core.md`.

## Test Plan

### Public model and backward compatibility

- `PrinterStatus()` still succeeds and both new fields are `None`.
- Existing selected-keyword construction remains valid without layer
  arguments.
- Explicit `current_layer` and `total_layers` values are retained by the frozen
  dataclass.

### Full normalization

Using the real `BambuPrinterAdapter` with the existing fake transport boundary,
apply a candidate full report containing `layer_num` and `total_layer_num` and
assert both public fields. Include existing state/progress/temperature/AMS
values in the same report and assert they remain unchanged.

### Missing fields

Apply valid reports that never contain either layer field and assert both
public fields remain `None`.

### Sparse delta preservation

On one real retained accumulator lifecycle:

1. apply `layer_num=10` and `total_layer_num=100`;
2. apply a later valid report omitting both;
3. assert `10` and `100` remain.

### Partial updates

Starting from `10 / 100`, apply a later `layer_num=11`-only report and assert
`11 / 100`. Add the symmetric `total_layer_num`-only case if needed to prove
independent storage.

### Explicit zero

Test numeric `0` and string `"0"` as valid. Assert they remain integers and
are not converted to `None`.

### Accepted and malformed values

Parameterize accepted values:

- non-negative exact integers;
- ASCII base-10 digit strings, including surrounding whitespace.

Parameterize rejected values:

- `True` and `False`;
- negative integers and signed strings;
- floats including `10.0`;
- empty/whitespace-only strings;
- fractional and exponent strings;
- `None`, list, and mapping values.

For rejected values, prove both the never-observed result (`None`) and the
important accumulator rule that a prior valid value is preserved.

Test `current_layer > total_layers` explicitly and assert independent truthful
preservation without correction or progress inference.

### Existing-field regression

Run the existing Bambu adapter tests and retain assertions for:

- state normalization;
- progress normalization;
- current/target temperatures;
- AMS normalization;
- full-snapshot marker behavior;
- passive sparse accumulation;
- refresh/cooldown and cleanup behavior.

### MCP serialization

- Extend the MCP fake status with concrete layer values and assert the exact
  flat keys and integer values.
- Add/adjust a case using a default `PrinterStatus` to assert both keys are
  present with `None`.
- Preserve the JSON round-trip assertion.
- Assert the MCP layer consumes `PrinterStatus`; no test should feed raw Bambu
  telemetry to the MCP serializer.

## Verification Commands

Focused model/adapter/MCP tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_interfaces.py tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
```

Printer transport regression, ensuring the existing informational-refresh
boundary is unchanged:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_printer_transport.py tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
```

Ruff on the exact Build scope:

```powershell
.\.venv\Scripts\ruff.exe check src/print_engineer/core/types.py src/print_engineer/adapters/printer/bambu.py src/print_engineer/mcp/tools/printer.py tests/unit/test_interfaces.py tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
```

Mypy on the exact Build scope:

```powershell
.\.venv\Scripts\python.exe -m mypy src/print_engineer/core/types.py src/print_engineer/adapters/printer/bambu.py src/print_engineer/mcp/tools/printer.py tests/unit/test_interfaces.py tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
```

If focused verification is green, run the unit suite without enabling any
hardware environment gate:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/
```

Build and Review must not execute integration hardware tests.

## Hardware Verification Decision

No new physical-printer verification is required for this increment.

Hardware already proves that a physical A1 full snapshot contains the two
source field names. The Build will prove the exact conversion and accumulation
semantics hermetically for both plausible integer representations. A new
physical request would be needed only to prove the current firmware's concrete
runtime value types or actual layer values; neither is required for the
defensive optional public contract.

After Build, claims must remain separated:

- hardware-proven: the physical full snapshot contains `layer_num` and
  `total_layer_num`;
- software-proven: supported values convert to optional `current_layer` and
  `total_layers` with the specified sparse-delta semantics;
- not hardware-proven by the prior sanitized capture: the exact physical
  values and their concrete JSON types.

## Risks / Unverified Assumptions

- The physical capture established field presence but not runtime types. The
  parser therefore supports exact non-negative integers and decimal integer
  strings while rejecting representations that require rounding or guessing.
- Firmware could emit a representation outside that contract. It would remain
  truthfully unavailable (`None`) rather than being fabricated; evidence would
  be required for a later parser change.
- The protocol's layer numbering origin is not asserted. Zero is preserved as
  observed telemetry, not interpreted as a specific print phase.
- No invariant between current and total layer values is protocol-proven, so
  the fields remain independently normalized.
- These are latest observed values within the existing connection-scoped
  accumulator, not persistent history or a retained cross-call cache.

None of these uncertainties blocks the additive optional status contract.

## Acceptance Criteria

The increment is complete only when:

- `PrinterStatus.current_layer` exists as `int | None` with default `None`;
- `PrinterStatus.total_layers` exists as `int | None` with default `None`;
- both fields are appended so existing constructors remain compatible;
- `_BambuStatusAccumulator` maps valid `layer_num` to `current_layer`;
- it maps valid `total_layer_num` to `total_layers`;
- exact integers and base-10 integer strings follow the documented contract;
- bool, negative, fractional, float, and malformed values are rejected without
  raising or erasing prior valid state;
- zero is preserved;
- missing deltas preserve prior valid layer values;
- valid partial updates replace only the field present;
- never-observed fields remain `None`;
- current and total values are normalized independently, with no progress or
  state inference;
- the existing accumulator remains the sole normalization path;
- `printer.status` emits flat `current_layer` and `total_layers` keys with
  integer-or-null values;
- state, progress, temperature, AMS, structured errors, and response shape are
  otherwise unchanged;
- new MQTT publish paths, generic MQTT APIs, printer-control paths, reconnects,
  and background threads are all zero;
- the exact-scope focused tests, Ruff, and Mypy pass;
- broader printer regressions pass or any unrelated failures are independently
  classified;
- no physical hardware test is run during Build or Review.

## Definition of Done

An approved Build changes exactly the six scoped production/test files,
implements only the two optional layer fields and their existing-path
serialization, passes the required hermetic verification, confirms no MQTT or
lifecycle diff, and stops without entering retained-session or monitoring work.

## Approval Questions

An independent reviewer must resolve all of the following before approval:

1. Are appended optional dataclass fields backward-compatible with every
   current `PrinterStatus` construction site?
2. Is accepting only non-negative exact integers and ASCII decimal integer
   strings sufficiently truthful given that hardware proved field presence but
   did not record types?
3. Is explicit bool rejection enforced despite Python's `bool`/`int`
   relationship?
4. Do malformed and missing values preserve prior valid accumulated layer
   state consistently with current accumulator conventions?
5. Is independent preservation of `current_layer > total_layers` preferable to
   an unsupported correction or rejection rule?
6. Does MCP remain a serializer of `PrinterStatus` rather than a second raw
   telemetry parser?
7. Is the six-file Build scope exact and sufficient?
8. Do tests prove layer behavior through the real accumulator while preserving
   all existing MQTT safety tests?
9. Is no additional physical MQTT request necessary for this optional,
   defensively normalized contract?

## Final Verdict

The repository has a single clear normalization path and an additive public
model that can accept two defaulted optional fields without architecture or
MQTT changes. The established physical field presence plus strict hermetic
conversion tests are sufficient for this increment.

PROPOSED — READY FOR INDEPENDENT PLAN REVIEW
