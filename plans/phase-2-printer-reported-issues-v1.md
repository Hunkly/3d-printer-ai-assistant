# Phase 2 — Printer-Reported Issues v1

Status: APPROVED

## Objective

Add the first evidence-backed printer diagnostic data to the existing read-only
`printer.status` result. The feature will expose explicit issue identifiers
reported by the printer through Bambu `hms` and `print_error` telemetry. It will
not infer faults from temperatures, progress, layers, timing, AMS state, or any
other generic telemetry.

The increment is deliberately machine-readable. Summary v2 and Assessment v1
remain unchanged, and issue identifiers are not decoded into human-facing
messages without a sufficiently authoritative, stable mapping.

## Baseline

Completed printer-status work includes normalized read-only telemetry,
deterministic summary formatting, explicit state-based assessment, and
state-aware Summary v2. The latest completed checkpoint is:

`e386c74ebbe9f760d5c007c197b370e5be84344a feat(printer): make status summary state-aware`

The current success path creates one adapter, invokes `get_status()` once, and
uses the returned `PrinterStatus` for structured serialization, summary, and
assessment. The Bambu adapter already accumulates sparse `push_status` deltas
within a connection and requests no issue-specific report.

## Known Physical Evidence

A prior read-only physical A1 observation established that a full
`push_status` snapshot contains both `hms` and `print_error`. That observation
did not contain or preserve a non-empty HMS example or a non-zero print-error
example in this repository, and therefore does not establish code meanings,
cross-model coverage, or a human-readable lookup contract.

No new hardware access occurred during planning. A healthy observation with
`hms=[]` and `print_error=0` would add no evidence about non-empty record shape
or code meaning, and deliberately causing a printer fault is outside scope.

## Repository Research

- `src/print_engineer/adapters/printer/bambu.py` currently normalizes state,
  temperatures, progress, AMS, layers, and remaining time. It does not parse
  `hms` or `print_error`.
- `_BambuStatusAccumulator.apply()` updates modeled fields only when their keys
  are present. Missing sparse-delta fields retain the last accepted value;
  malformed modeled values generally leave the last valid value intact.
- The accumulator is connection-scoped. Disconnect/reset creates a new
  accumulator, so retained issue state must not cross connections.
- `src/print_engineer/core/types.py` defines a frozen `PrinterStatus`; it has no
  issue field or issue type today.
- `src/print_engineer/mcp/tools/printer.py` serializes that same status object,
  then formats summary and assessment. It performs one `get_status()` call.
- Existing adapter and MCP unit tests contain no non-empty `hms` or non-zero
  `print_error` fixtures. The LAN integration test does not establish issue
  semantics and must not become an automated hardware dependency.
- Repository searches for `hms` and `print_error` found no existing production
  parser, decoder, or lookup table that can be reused.

Repository evidence establishes where normalization belongs and how the local
sparse accumulator works. It does not independently establish Bambu field
schema or meanings.

## External Evidence

Primary/direct-source evidence comes from Bambu Lab's public Bambu Studio
source:

- `DevHMS.h` declares `ParseHMSItems`, stores an ordered vector of HMS items,
  and models each raw item from unsigned `attr` and `code` values:
  https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/DeviceCore/DevHMS.h
- `DevHMS.cpp` accepts an array, reads `attr` and `code`, clears the prior HMS
  list before parsing, and constructs a hexadecimal long error identifier from
  those values:
  https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/DeviceCore/DevHMS.cpp
- `DeviceManager.hpp` stores `MachineObject::print_error` as signed C++ `int`.
  `DeviceManager.cpp` updates HMS only when `hms` is present, assigns
  `print_error` with `get<int>()` only when that field is present and numeric,
  initializes it to zero, rejects negative values in `get_error_code_str()`,
  and otherwise formats it as uppercase hexadecimal:
  https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/DeviceManager.cpp
  https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/DeviceManager.hpp

This direct client implementation is sufficient evidence for raw field shape,
field-presence delta behavior, explicit clearing, integer identifier treatment,
and the fact that HMS and print error are maintained separately. Bambu Studio's
signed storage type is a source fact; it does not prove that the MQTT/wire
protocol is signed, and it does not prove that the wire protocol is unsigned.
The exact accepted domain below is this project's conservative normalization
policy. The source does not provide a complete, versioned, locally available
mapping from every identifier to stable human-readable text.

Community protocol notes and integrations were consulted only as corroborating
leads. They are not an API-semantic authority and will not be used to assign
messages, severity, actions, categories, or equivalence between sources.

## HMS Semantics

For v1, `hms` is an ordered array of explicit printer-reported records. A valid
record is an object containing both:

- `attr`: an integer in the unsigned 32-bit range
- `code`: an integer in the unsigned 32-bit range

Python booleans are not accepted as integers. Each valid record becomes one
issue with source `hms`. Its lossless canonical code is the raw pair rendered
as 16 uppercase hexadecimal characters:

`{attr:08X}{code:08X}`

Exact synthetic example:

```text
attr = 0x03001234
code = 0x00020056
project code = 0300123400020056
```

This 16-character value is the project's lossless concatenation of the raw
`attr`/`code` pair. It is not semantically decoded and is not claimed to equal
every possible Bambu Studio `get_long_error_code()` representation for every
accepted raw code. It exposes no inferred module, part, severity, category,
message, or recommendation.

An explicitly empty array means that the printer reports no HMS records in
that update and clears the accumulated HMS collection. A missing `hms` field
does not mean clear. An unrecognized but structurally valid pair is preserved
exactly; it is not dropped merely because no message is known.

Human-readable HMS decoding is not part of v1. Bambu Studio demonstrates bit
parsing and identifier construction, but repository/direct-source research did
not establish a complete, stable, versioned message table suitable for an
offline core contract. No message will be fabricated.

## print_error Semantics

**Source fact:** Bambu Studio stores `MachineObject::print_error` as signed C++
`int`, assigns it with `get<int>()` when the field is present and numeric, and
returns no formatted code for a negative value. This does not establish the
signedness or full domain of the MQTT/wire field itself.

**Project normalization policy:** v1 conservatively accepts `print_error` only
when `type(value) is int` and `0 <= value <= 0x7FFFFFFF`. Python booleans are
rejected even though `bool` subclasses `int`.

- `0` explicitly clears the current print-error issue;
- `1..0x7FFFFFFF` creates one issue with source `print_error` and code
  `f"{value:08X}"`;
- a missing field preserves the previous accumulated print-error value;
- every other value is malformed and preserves the previous valid
  print-error source state.

Malformed values include booleans, negative integers, integers greater than
`0x7FFFFFFF`, floats, numeric strings, `None`, lists, tuples, dictionaries, and
other collections or objects. There is no coercion. Values
`0x80000000..0xFFFFFFFF` are not accepted in v1. This upper bound is a
conservative project policy aligned with non-negative values representable by
Bambu Studio's signed `int` storage, not a claim that future firmware cannot
emit a wider value.

Formatting is exactly eight uppercase hexadecimal digits with no `0x` prefix,
hyphen, lowercase, or variable width:

```text
0x00000001 -> 00000001
0x0012ABCD -> 0012ABCD
0x7FFFFFFF -> 7FFFFFFF
0x80000000 -> malformed; preserve prior valid print-error state
```

v1 does not claim a meaning for any non-zero code. The two telemetry sources
are complementary in the sense that they are separate explicit report
channels, but their event-level relationship is not established. They must not
be deduplicated or translated into one another.

## Evidence Confidence

High confidence, supported by Bambu Studio source:

- HMS is an array of `attr`/`code` integer records.
- HMS order is represented as an ordered vector.
- an explicit HMS array replaces the prior list, including `[]` clearing it.
- missing HMS preserves the prior list.
- Bambu Studio stores `print_error` separately as signed C++ `int`, assigns it
  only when present and numeric, and suppresses formatting for negatives.
- explicit `print_error=0` clears that source; missing preserves it.
- raw values can be represented deterministically as hexadecimal identifiers.

Moderate confidence:

- the MQTT/wire domain of `print_error` is not formally specified. The project
  intentionally accepts only `0..0x7FFFFFFF`, matching the non-negative range
  of Bambu Studio's signed storage without claiming a protocol-wide limit.

Not established and intentionally excluded:

- complete human-readable meanings;
- severity, category, recoverability, or recommended action;
- cross-source equivalence/deduplication;
- guarantees that every firmware/model emits both fields in every full report.

## Scope

- Add a minimal immutable normalized issue record and an `issues` tuple to
  `PrinterStatus`.
- Parse valid `hms` and `print_error` values in the Bambu status accumulator.
- Preserve independent source state across sparse deltas and combine it into a
  deterministic normalized issue tuple.
- Add an additive `issues` array to successful `printer.status` serialization.
- Preserve all existing status fields, summary, assessment, retrieval, and
  error behavior.

## Non-Goals

- no human-readable issue decoding or lookup table;
- no severity, category, health score, recoverability, or recommendations;
- no temperature, progress, layer, timing, AMS, Wi-Fi, fan, or state heuristics;
- no summary or assessment changes;
- no staleness timestamps, history, previous-session retention, caches, or
  monitoring;
- no issue acknowledgment, clearing command, printer control, MQTT publish,
  transport change, pushall change, cooldown change, or reconnect change;
- no new MCP tool and no additional status retrieval;
- no intentionally induced fault and no hardware verification.

## Normalized Issue Model

Add these protocol-independent core concepts in `core/types.py`:

```python
class PrinterIssueSource(str, Enum):
    HMS = "hms"
    PRINT_ERROR = "print_error"


@dataclass(frozen=True)
class PrinterIssue:
    source: PrinterIssueSource
    code: str
```

Extend the frozen `PrinterStatus` with:

```python
issues: tuple[PrinterIssue, ...] = ()
```

No `message`, `severity`, `category`, action, or vendor-specific raw-object
field is added. `code` is an opaque normalized identifier, not a decoded
diagnosis. The source enum prevents free-form source spelling and preserves the
distinction needed to avoid unsafe cross-source deduplication.

An empty tuple means "no valid explicit issue records are currently available
in this accumulated status." It must not be described as proof that the printer
is healthy. On a new connection before either source is observed, the same
empty representation is used; v1 intentionally exposes reported records, not a
separate completeness/freshness model.

Unknown/unrecognized but structurally valid identifiers are represented like
known identifiers. Since v1 has no decoder, all codes are opaque by contract.

## Sparse Delta / Clearing Semantics

The accumulator tracks HMS records and print error independently:

| Incoming field | Valid value | Accumulator result |
|---|---|---|
| `hms` missing | — | preserve prior HMS issues |
| `hms` present | valid array, including `[]` | atomically replace prior HMS issues in payload order |
| `hms` present | malformed array or any malformed entry | reject the entire HMS update; preserve prior HMS issues |
| `print_error` missing | — | preserve prior print-error issue |
| `print_error` present | integer `0` | clear prior print-error issue |
| `print_error` present | exact integer `1..0x7FFFFFFF` | replace prior print-error issue |
| `print_error` present | malformed/out of range/bool | preserve prior print-error issue |

Atomic HMS validation means every entry in one present non-empty array must
validate before replacement. If the previous HMS state is `[A]` and an incoming
array contains valid `B`, a malformed item, and valid `C`, the result remains
`[A]`, not `[B]`, `[B, C]`, or empty. A fresh accumulator begins with no
available issue records. Disconnect/reset discards all accumulated issues
together with the rest of the connection-scoped status.

Field-presence behavior and explicit empty/zero clearing are supported by Bambu
Studio. In contrast, malformed-present HMS preservation is a
**project-specific defensive normalization policy**: Bambu Studio clears its
HMS vector before parsing a present payload, while this project rejects the
entire malformed source update so malformed telemetry cannot silently erase a
previously valid explicit printer-reported issue state. This is not asserted as
protocol truth. Valid `hms=[]` and `print_error=0` remain explicit independent
clears.

HMS and print error are independent source accumulators. Validation and apply
are performed independently per source within the same report: a malformed
update from one source must never block, roll back, or suppress a valid clear or
replacement from the other. There is no whole-report transaction for issues.

## Adapter Mapping

Extend `_BambuStatusAccumulator` with separate private state for the ordered HMS
issues and optional print-error issue. Parsing remains in the Bambu adapter;
MCP must never inspect raw Bambu keys.

For each valid HMS pair, generate:

```python
PrinterIssue(
    source=PrinterIssueSource.HMS,
    code=f"{attr:08X}{code:08X}",
)
```

For a valid non-zero print error, generate:

```python
PrinterIssue(
    source=PrinterIssueSource.PRINT_ERROR,
    code=f"{print_error:08X}",
)
```

The required synthetic print-error example is
`0x0012ABCD -> "0012ABCD"`; valid boundaries are
`0x00000001 -> "00000001"` and `0x7FFFFFFF -> "7FFFFFFF"`.

The snapshot order is all HMS records in printer payload order, including
duplicates, followed by the one print-error record when non-zero. No
deduplication is performed because equivalence within HMS or across sources is
not evidenced. This ordering is deterministic and preserves the only observed
source ordering rather than sorting opaque identifiers.

## MCP Contract

Extend the successful `printer.status` response with an always-present additive
field:

```json
"issues": [
  {"source": "hms", "code": "0300000000020001"},
  {"source": "print_error", "code": "0300400C"}
]
```

The shown values illustrate serialization shape only and must not be assigned
human meanings in implementation or tests. With no currently available valid
records, serialize exactly:

```json
"issues": []
```

Each issue object has exactly `source` and `code`. There is no `message: null`
field because v1 does not offer message decoding. Existing structured fields
remain unchanged. The existing `PrinterError` failure response remains
unchanged and does not acquire an `issues` field.

## Summary Compatibility

`_format_status_summary()` is out of scope and must not change. No issue code or
text is appended to Summary v2. All state-aware fragment eligibility,
formatting, ordering, and disconnected behavior remain byte-for-byte unchanged.

## Assessment Compatibility

`_assess_status()` is out of scope and must not change. Assessment continues to
depend only on `is_connected` and `state`, with every existing exact
level/code/message dictionary preserved. A successful `PrinterStatus` with
issues does not alter assessment in v1.

## Single-Retrieval Invariant

The registered success path remains exactly:

1. create one adapter;
2. call `get_status()` once;
3. use that same `PrinterStatus` for structured serialization, unchanged
   summary, unchanged assessment, and the additive issue array.

Summary/issues add zero adapters, retrievals, refreshes, subscriptions,
connections, or MQTT operations. Existing exact-count tests remain and gain no
alternate issue-fetching path.

## Determinism

Issue output is a pure function of the accumulated report state:

- uppercase, zero-padded hexadecimal formatting is fixed;
- HMS payload order is preserved;
- duplicate source records are preserved;
- print error follows all HMS records;
- no set iteration, lookup service, time, environment, history, network call,
  random value, or LLM affects output.

## Exact Build Scope

Production:

- `src/print_engineer/core/types.py`
- `src/print_engineer/adapters/printer/bambu.py`
- `src/print_engineer/mcp/tools/printer.py`

Tests:

- `tests/unit/test_bambu_printer_adapter.py`
- `tests/unit/test_printer_mcp.py`

No other file is required. In particular, do not modify transport, MCP server,
recommendation code, dependencies, retained-session work, lifecycle work,
monitor work, or printer-control interfaces. If implementation reveals a need
for another production file, stop for plan re-review rather than expanding
scope.

`tests/unit/test_interfaces.py` is regression verification only and does not
require modification. No core export/index module change is required because
the existing code imports domain types directly from `core.types`.

## Test Plan

Adapter tests must cover:

1. a fresh/full report with `hms=[]` and `print_error=0` yields `issues == ()`;
2. HMS `attr=0x03001234`, `code=0x00020056` produces exactly source `hms`
   and code `0300123400020056`;
3. multiple HMS items preserve payload order and duplicates;
4. an unknown valid HMS pair is preserved without decoding;
5. malformed HMS input is covered by a parameterized semantic matrix:
   - container `None`, dictionary, string, integer, and another non-list value;
   - non-dictionary list entry;
   - missing `attr` and missing `code`;
   - for `attr`: `True`, `False`, `-1`, `0x100000000`, `1.0`, `"1"`,
     `None`, `[]`, and `{}`;
   - for `code`: `True`, `False`, `-1`, `0x100000000`, `1.0`, `"1"`,
     `None`, `[]`, and `{}`;
   every such present HMS source update preserves the previous valid HMS state;
6. missing `hms` in a later sparse delta preserves the prior HMS collection;
7. explicit `hms=[]` clears the prior HMS collection;
8. atomic HMS sequencing: previous `[A]`, then `[valid B, malformed, valid C]`
   leaves exactly `[A]`;
9. `print_error=0x0012ABCD` produces exactly source `print_error` and code
   `0012ABCD`; boundaries `1` and `0x7FFFFFFF` produce `00000001` and
   `7FFFFFFF`;
10. missing `print_error` preserves it and explicit zero clears it;
11. malformed print-error values `True`, `False`, `-1`, `0x80000000`, `1.0`,
    `"1"`, `None`, `[]`, `{}`, and a representative tuple/other collection
    preserve the last valid print-error state with no coercion;
12. same-report source independence is pinned by these exact sequences:
    - previous HMS `[A]` and print error `X`; malformed HMS plus
      `print_error=0` results in aggregate `[A]`;
    - previous HMS `[A]` and print error `X`; `hms=[]` plus malformed
      print error results in aggregate `[X]`;
    - previous HMS `[A]` and print error `X`; valid `hms=[B, C]` plus valid
      print error `Y` results in `[B, C, Y]`;
    - previous HMS `[A]` and print error `X`; valid `hms=[B]` with missing
      print error results in `[B, X]`;
    - previous HMS `[A]` and print error `X`; missing HMS with valid print
      error `Y` results in `[A, Y]`;
    each sequence uses exact synthetic codes and asserts the full normalized
    tuple, proving there is no whole-report rollback;
13. HMS records precede print error, with no cross-source deduplication;
14. disconnect/new connection resets accumulated issues;
15. all pre-existing normalization and zero-publish transport assertions pass.

MCP tests must cover:

1. exact `issues: []` serialization;
2. exact serialization of one and multiple source-qualified records;
3. unknown raw codes are preserved and no message/severity fields appear;
4. all existing structured fields remain identical;
5. exact Summary v2 strings remain unchanged with issues present;
6. exact Assessment v1 dictionaries remain unchanged with issues present;
7. disconnected successful status retains existing summary/assessment while
   serializing its normalized issue tuple consistently;
8. one registered invocation creates exactly one adapter and calls
   `get_status()` exactly once;
9. the existing `PrinterError` response remains exact and issue-free;
10. no additional transport/MQTT method is invoked.

Existing canonical printing, paused, physical-A1 idle, non-active-state,
temperature, AMS, assessment, structured-preservation, retrieval-count, and
error-path regressions remain required.

## Verification Commands

Run focused checks using the project virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_bambu_printer_adapter.py
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_printer_mcp.py
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_interfaces.py tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
.\.venv\Scripts\ruff.exe check src/print_engineer/core/types.py src/print_engineer/adapters/printer/bambu.py src/print_engineer/mcp/tools/printer.py tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
.\.venv\Scripts\python.exe -m mypy src/print_engineer/core/types.py src/print_engineer/adapters/printer/bambu.py src/print_engineer/mcp/tools/printer.py tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
```

After focused success, run the full unit suite with Bambu hardware environment
variables removed from the child environment. A known Windows pytest temp-root
`PermissionError` may be retried once with a unique `--basetemp` under the
system temporary directory. Classify unrelated failures rather than modifying
unrelated code. Do not run integration hardware tests.

## MQTT / Safety Impact

Required and expected deltas:

- additional `get_status()` calls: 0
- new MQTT operations: 0
- new refresh requests: 0
- new adapters/connections: 0
- new workers/caches: 0
- new reconnect behavior: 0
- printer-control capability: 0

The adapter only reads two fields already present in accepted `push_status`
telemetry. No transport, request topic, publish path, pushall behavior,
cooldown, or lifecycle behavior changes.

## Hardware Verification Decision

NO NEW HARDWARE VERIFICATION REQUIRED for v1.

The plan relies on direct Bambu Studio source for non-empty schema and clear
semantics and deliberately avoids human decoding. The previous A1 observation
already established field presence in this project's real read-only chain.
Another healthy snapshot would not strengthen non-empty issue evidence, while
triggering a fault would be unsafe and out of scope. Unit tests using synthetic
payloads can fully verify the deterministic parsing and serialization contract.

## Risks / Unverified Assumptions

- Bambu Lab does not publish a formal, versioned LAN MQTT schema here. HMS
  unsigned widths follow the current public client's `unsigned` fields;
  print-error v1 deliberately uses the narrower non-negative signed-`int`
  project policy. Strict parsing and unknown-code preservation defend both.
- Firmware/model variants might omit one or both fields. Missing values are
  therefore preservation events, not clears.
- Empty `issues` means no valid currently accumulated explicit records, not a
  health guarantee and not proof that both sources were observed.
- The semantic relationship between an HMS record and a print-error identifier
  is unknown. Preserving separate records can expose two records for one
  real-world event, but is more truthful than unsupported deduplication.
- Source payload order may not convey priority. It is preserved only as the
  deterministic order supplied by the printer.
- Human-readable mappings may change or be incomplete. Adding a mapping is a
  separate evidence and API-design increment.

None of these risks blocks the raw, source-qualified v1 contract.

## Acceptance Criteria

- `PrinterStatus` contains an immutable tuple of minimal `PrinterIssue` values.
- Only structurally valid explicit `hms` and non-zero `print_error` telemetry
  creates issue records.
- Raw identifiers use the exact uppercase hexadecimal formats defined above.
- unknown valid identifiers are preserved without invented meanings;
- missing sparse fields preserve their source state;
- valid empty HMS and zero print error clear their respective source state;
- malformed updates cannot silently clear the last valid source state;
- output ordering is HMS payload order followed by print error, with duplicates
  retained and no cross-source deduplication;
- successful MCP output always includes the exact additive `issues` array;
- no-available-record output is exactly `"issues": []`;
- existing structured telemetry, Summary v2, Assessment v1, disconnected
  behavior, and `PrinterError` response remain unchanged;
- one adapter and one `get_status()` call remain exact;
- focused tests, Ruff, Mypy, and relevant unit regressions pass;
- no transport, MQTT, lifecycle, refresh, hardware, or control behavior changes.

## Definition of Done

Implementation is complete only when the exact five-file scope implements the
raw issue contract, tests prove schema/validation/sparse clearing/order/MCP
compatibility and safety invariants, static checks pass, the final diff contains
no unrelated work, and no hardware operation has been performed. Build stops
after this increment and does not add decoding or issue-driven reasoning.

## Approval Questions

1. **RESOLVED** — `hms` is an ordered array of unsigned `attr`/`code` records.
2. **RESOLVED** — Bambu Studio stores `print_error` as signed C++ `int`; this
   project accepts only exact Python integers `0..0x7FFFFFFF`, with zero as the
   explicit clear value, without claiming MQTT wire signedness.
3. **RESOLVED** — both are needed to preserve the two explicit report channels;
   neither is translated into or deduplicated against the other.
4. **RESOLVED** — the normalized model is exactly immutable `source` plus opaque
   uppercase hexadecimal `code`.
5. **RESOLVED** — issues belong in `PrinterStatus`; protocol parsing remains in
   the adapter and MCP only serializes normalized records.
6. **RESOLVED** — successful `printer.status` always adds
   `issues: [{"source": ..., "code": ...}]` in deterministic order.
7. **RESOLVED** — no currently available valid records is exactly `issues: []`;
   this is not documented as a printer-health guarantee.
8. **RESOLVED** — structurally valid unknown codes are preserved identically and
   receive no message.
9. **RESOLVED** — human-readable decoding is not justified in v1.
10. **RESOLVED** — no lookup table is used; a future decoder requires a separate
    authoritative, versioning, completeness, and fallback review.
11. **RESOLVED** — missing source fields preserve their previous source state;
    malformed fields preserve the last valid source state.
12. **RESOLVED** — `hms=[]` clears HMS and `print_error=0` clears print error.
    HMS validation is atomic, while HMS and print-error validation/apply remain
    independent within the same report.
13. **RESOLVED** — no deduplication is performed without equivalence evidence.
14. **RESOLVED** — preserve HMS payload order and append print error last.
15. **RESOLVED** — Assessment v1 remains byte-for-byte unchanged.
16. **RESOLVED** — Summary v2 remains byte-for-byte unchanged.
17. **RESOLVED** — exactly one adapter and one `get_status()` remain.
18. **RESOLVED** — exact build scope is the three production and two unit-test
    files listed above.
19. **RESOLVED** — transport changes are unnecessary and prohibited.
20. **RESOLVED** — hardware verification is unnecessary for the raw v1 contract;
    no fault will be induced.

## Final Verdict

PROPOSED — READY FOR INDEPENDENT PLAN REVIEW
