# Phase 2 — State-Aware Printer Summary v2

Status: APPROVED

## Objective

Make the existing human-readable `printer.status` summary state-aware so that
job-specific telemetry is presented only when the normalized printer state
clearly represents an active job. This is presentation filtering only.

The normalized `PrinterStatus`, its serialization, assessment, retrieval flow,
and all printer/MQTT behavior remain unchanged.

## Baseline

The completed Phase 2 baseline already provides:

- normalized `PrinterStatus`;
- informational status refresh;
- progress, current/total layers, and `remaining_time_minutes`;
- deterministic top-level `printer.status` summary;
- explicit top-level `printer.status` assessment.

The approved Summary v1 contract is implemented by the private pure
`_format_status_summary(status)` helper in
`src/print_engineer/mcp/tools/printer.py`. One successful tool invocation
constructs one adapter, calls `get_status()` once, and uses the same returned
`PrinterStatus` for structured serialization, summary, and assessment.

Summary v1 already filters remaining time to connected `PRINTING` and `PAUSED`
states, but it currently renders finite progress and available layers in every
connected state. That permits residual job telemetry to appear active after a
printer transitions to `IDLE`, and likewise in `ERROR`, `UNKNOWN`, or connected
`OFFLINE`.

Repository inspection found the expected unrelated untracked files:

- `plans/phase-2-bounded-mqtt-transport-lifecycle.md`;
- `plans/phase-2-printer-monitor-core.md`;
- `plans/phase-2-retained-status-session-v1.md`;
- `tools/codex-controller/package-lock.json`.

They are parked work and must remain untouched, unstaged, and outside Build
scope. Any additional unrelated user changes present at Build time must receive
the same treatment.

## Physical A1 Evidence

Recent live physical A1 verification succeeded through the complete read-only
path:

```text
physical printer
→ MQTT
→ PrinterStatus
→ printer.status
→ summary
→ assessment
```

The observed connected idle status retained terminal job values:

```text
state = IDLE
is_connected = true
progress = 1.0
current_layer = 80
total_layers = 80
remaining_time_minutes = 0
nozzle_temp ≈ 27.3
target_nozzle_temp = 0
bed_temp ≈ 27.8
target_bed_temp = 0
AMS connected
```

Summary v1 correctly followed its approved contract and rendered:

```text
Idle · 100% complete · Layer 80 / 80 · Nozzle 27.3 / 0 °C · Bed 27.8 / 0 °C · AMS connected
```

The observation does not prove that `progress == 1.0` means a completed print.
It proves only that job-specific normalized fields can remain populated while
the authoritative current state is `IDLE`. Summary v2 responds solely by
filtering presentation according to state.

## Current Summary Contract

Summary v1 establishes the following contracts, all retained except for the
new progress/layer state eligibility rule:

- connection false overrides every fragment with exactly
  `Printer disconnected`;
- each connected `PrinterState` has a fixed lead fragment;
- included fragments are joined by exact separator ` · ` in fixed order:
  state, progress, layers, remaining time, nozzle, bed, AMS;
- finite progress is display-clamped to `[0.0, 1.0]`, converted to a whole
  percentage using half-up rounding, and rendered as `<N>% complete`;
- layers use `Layer C / T`, `Layer C`, or `Total layers T` according to which
  fields are available;
- remaining time is already eligible only in `PRINTING` and `PAUSED` and uses
  `About N min remaining`;
- temperature availability, finite-value filtering, `.1f` formatting with
  trailing `.0` removal, fragment grammar, and order remain unchanged;
- AMS uses only `AMSInfo.is_connected`, rendering `AMS connected` or
  `AMS not connected`, and remains omitted when `ams is None`;
- structured errors contain neither summary nor assessment.

Summary v2 changes only whether progress and layer fragments are eligible for a
connected state. It does not reopen any numeric or wording rule.

## Scope

This increment includes only:

1. changing the private pure summary formatter so job-specific fragments are
   eligible only in active-job states;
2. preserving Summary v1 formatting for every eligible fragment;
3. adding hermetic exact-string and registered-tool regression coverage for
   every state, the physical A1 observation, structured-data preservation,
   assessment compatibility, error compatibility, and single retrieval.

## Non-Goals

Do not add or change:

- `PrinterStatus`, `PrinterState`, `AMSInfo`, serialization, normalization, or
  accumulation semantics;
- clearing, rewriting, validating, or reinterpreting progress, layers,
  remaining time, temperatures, or AMS;
- completed-print inference or a `last print` concept;
- health diagnosis, temperature diagnosis or thresholds, progress-stall
  detection, freshness, timestamps, stale flags, history, caches, retained
  sessions, background monitors, recommendations, or actions;
- adapters, connections, refresh requests, MQTT operations, retries, reconnect
  behavior, workers, dependencies, MCP registration, or printer control;
- `_assess_status()` or any assessment level, code, or message;
- slicer, recommendation, model-analysis, or future-phase behavior.

In particular, never infer `progress == 1.0 → completed print`. The normalized
state is the sole presentation-eligibility input.

## State-Aware Presentation Model

For summary purposes, the exact active-job state set is:

```python
{PrinterState.PRINTING, PrinterState.PAUSED}
```

`PAUSED` remains an active job because it represents a temporarily paused print
and the approved existing contract already presents its remaining time. It must
not be treated as an error.

Progress, layers, and remaining time are job-specific fragments. They are
eligible only when connected and the state is in that active-job set. `IDLE`,
`ERROR`, `UNKNOWN`, and connected `OFFLINE` do not clearly establish current
job activity, so all three fragments are suppressed even if their normalized
fields are populated.

This conservative suppression makes no claim that the fields are stale. It
only avoids implying that they describe a currently active job.

Nozzle temperature, bed temperature, and AMS remain informational fragments.
They retain Summary v1 eligibility and formatting in every connected state,
including `IDLE`, `ERROR`, `UNKNOWN`, and connected `OFFLINE`. Their presence
must not add diagnostic wording such as cooling, cold, ready, normal, at target,
or any AMS health inference.

When disconnected, connection precedence remains absolute: return exactly
`Printer disconnected` and suppress every other fragment, without mutating the
structured status.

## Fragment Eligibility Matrix

The matrix applies only when `is_connected is True`. “Eligible” means the
fragment may be included when its existing Summary v1 availability/finite-value
conditions are also satisfied. “Suppressed” means omit it regardless of a
populated structured field.

| `PrinterState` | Progress | Layers | Remaining time | Nozzle temperature | Bed temperature | AMS |
|---|---:|---:|---:|---:|---:|---:|
| `OFFLINE` | Suppressed | Suppressed | Suppressed | Eligible | Eligible | Eligible |
| `IDLE` | Suppressed | Suppressed | Suppressed | Eligible | Eligible | Eligible |
| `PRINTING` | Eligible | Eligible | Eligible | Eligible | Eligible | Eligible |
| `PAUSED` | Eligible | Eligible | Eligible | Eligible | Eligible | Eligible |
| `ERROR` | Suppressed | Suppressed | Suppressed | Eligible | Eligible | Eligible |
| `UNKNOWN` | Suppressed | Suppressed | Suppressed | Eligible | Eligible | Eligible |

Disconnected behavior is separate from the matrix:

| Connection | Exact summary behavior |
|---|---|
| `is_connected is False` | Return exactly `Printer disconnected`; all fragments suppressed. |

## Exact Summary Grammar

The fixed connected-state lead fragments remain:

| State | Lead fragment |
|---|---|
| `OFFLINE` | `Offline` |
| `IDLE` | `Idle` |
| `PRINTING` | `Printing` |
| `PAUSED` | `Paused` |
| `ERROR` | `Printer error` |
| `UNKNOWN` | `Status unknown` |

Build an ordered list of eligible, available fragments and join it with exact
separator ` · `. There is no leading/trailing separator, newline, placeholder,
Markdown, or ANSI formatting. Exact order remains:

1. state;
2. progress;
3. layers;
4. remaining time;
5. nozzle temperature;
6. bed temperature;
7. AMS.

When eligible, progress retains finite-only handling, display clamp, explicit
half-up percentage rounding, and exact `<N>% complete` grammar. Structured
progress remains unchanged even when non-finite, out of range, or suppressed.

When eligible, layers retain these exact forms without validation or inference:

| Available values | Fragment |
|---|---|
| current and total | `Layer C / T` |
| current only | `Layer C` |
| total only | `Total layers T` |
| neither | omit |

Remaining time retains exact `About N min remaining` grammar, with no hour
conversion or ETA. Its existing eligibility remains exactly `PRINTING` and
`PAUSED`.

Temperature and AMS grammar remain byte-for-byte compatible with Summary v1.

Exact representative outputs with all physical-observation informational
telemetry populated are:

```text
IDLE:    Idle · Nozzle 27.3 / 0 °C · Bed 27.8 / 0 °C · AMS connected
ERROR:   Printer error · Nozzle 27.3 / 0 °C · Bed 27.8 / 0 °C · AMS connected
UNKNOWN: Status unknown · Nozzle 27.3 / 0 °C · Bed 27.8 / 0 °C · AMS connected
OFFLINE: Offline · Nozzle 27.3 / 0 °C · Bed 27.8 / 0 °C · AMS connected
```

Those non-active states suppress populated progress, layers, and remaining time.
If temperatures or AMS are unavailable under existing Summary v1 rules, their
fragments are simply absent.

The representative active summaries are pinned as:

```text
Printing · 73% complete · Layer 184 / 252 · About 32 min remaining · Nozzle 220 / 220 °C · Bed 54.9 / 55 °C · AMS connected
Paused · 73% complete · Layer 184 / 252 · About 32 min remaining · Nozzle 220 / 220 °C · Bed 54.9 / 55 °C · AMS connected
```

## Structured Data Preservation

The formatter receives an already-normalized immutable `PrinterStatus` and
returns only a string. It must not mutate, clear, clamp, replace, synthesize, or
reinterpret any structured field.

For the physical A1 regression input:

```python
PrinterStatus(
    state=PrinterState.IDLE,
    is_connected=True,
    progress=1.0,
    current_layer=80,
    total_layers=80,
    remaining_time_minutes=0,
    nozzle_temp=27.3125,
    target_nozzle_temp=0.0,
    bed_temp=27.84375,
    target_bed_temp=0.0,
    ams=AMSInfo(is_connected=True),
)
```

the exact summary is:

```text
Idle · Nozzle 27.3 / 0 °C · Bed 27.8 / 0 °C · AMS connected
```

while the serialized mapping must still contain exactly:

```text
progress = 1.0
current_layer = 80
total_layers = 80
remaining_time_minutes = 0
```

along with all temperatures and AMS exactly as serialized by the existing
serializer. Suppression affects only summary text.

## Assessment Compatibility

Assessment is completely out of scope. `_assess_status()` must not be edited,
called differently, or made dependent on summary eligibility.

The physical A1 idle case must continue to return exactly:

```json
{
  "level": "info",
  "code": "printer_idle",
  "message": "Printer is idle."
}
```

Existing exact assessment dictionaries for connected `OFFLINE`, `IDLE`,
`PRINTING`, `PAUSED`, `ERROR`, and `UNKNOWN`, plus disconnected connection
precedence, must remain unchanged. Successful `ERROR` summary expectations in
tests may change only by removing job-specific fragments; its assessment must
remain the exact existing error dictionary.

## Determinism

Summary v2 remains a pure deterministic transformation of one `PrinterStatus`.
It depends only on the status argument, the fixed active-job state set, fixed
lead wording, and existing Summary v1 formatting rules.

It must not read time, locale, settings, environment, filesystem, network,
MQTT, randomness, prior statuses, caches, or mutable global state. Identical
input must produce byte-for-byte identical output.

## Single-Retrieval Invariant

Every `printer.status` invocation must remain exactly:

```text
construct one adapter
→ call get_status() once
→ bind one PrinterStatus
   → serialize that same object
   → summarize that same object
   → assess that same object
→ return one response
```

Summary v2 adds zero adapters, zero `get_status()` calls, zero refresh requests,
zero transport operations, and zero MQTT operations. It must not invoke another
MCP tool or retrieval path.

## Exact Build Scope

Repository inspection confirms the sufficient and required Build scope is
exactly two files:

- `src/print_engineer/mcp/tools/printer.py`
  - restrict progress and layer formatting to the same explicit active-job
    state set already used for remaining time, preferably through one local
    boolean or private constant with no new public API;
  - leave serialization, temperature/AMS helpers, `_assess_status()`, adapter
    construction, error handling, and response shape unchanged.
- `tests/unit/test_printer_mcp.py`
  - update state-dependent formatter expectations and add the exact regressions
    in this plan without weakening unrelated assertions.

No new module or abstraction is justified for this local presentation rule.
Do not modify `src/print_engineer/core/types.py`, the Bambu adapter, transport,
MCP server, recommendation code, dependencies, integration/hardware tests, or
any parked/unrelated file.

## Test Plan

All tests are hermetic and perform no hardware or network activity.

1. **Physical A1 idle regression:** invoke the registered `printer.status`
   path with the exact observed values: `IDLE`, connected, progress `1.0`,
   layers `80/80`, remaining `0`, nozzle `27.3125/0.0`, bed `27.84375/0.0`,
   and connected AMS. Assert the exact summary
   `Idle · Nozzle 27.3 / 0 °C · Bed 27.8 / 0 °C · AMS connected`; assert
   structured progress, both layers, and remaining time remain exactly
   `1.0`, `80`, `80`, and `0`; assert the exact existing idle assessment.
2. **PRINTING regression:** preserve the existing repository test values and
   exact full output:
   `Printing · 73% complete · Layer 184 / 252 · About 32 min remaining · Nozzle 220 / 220 °C · Bed 54.9 / 55 °C · AMS connected`.
3. **PAUSED regression:** use the same representative populated values and pin
   exact output beginning
   `Paused · 73% complete · Layer 184 / 252 · About 32 min remaining`, proving
   all three job fragments remain eligible and no error is inferred.
4. **IDLE eligibility:** with progress and layers populated, assert all three
   job fragments are absent while available temperatures and AMS remain.
5. **ERROR eligibility:** with progress, layers, and remaining time populated,
   assert all three are absent; assert available temperatures/AMS remain and
   the exact existing error assessment is unchanged. Update the existing
   registered error-state test that currently expects `50% complete`.
6. **UNKNOWN eligibility:** with populated job telemetry, assert all three are
   absent and informational fragments follow unchanged Summary v1 rules.
7. **Connected OFFLINE eligibility:** with populated job telemetry, assert all
   three are absent, the lead remains exactly `Offline`, informational
   fragments remain eligible, and the existing offline assessment is unchanged.
8. **Disconnected precedence:** with every field populated, assert summary is
   exactly `Printer disconnected`, structured fields remain unchanged, and the
   existing disconnected assessment wins.
9. **Temperature compatibility:** preserve existing finite filtering, partial
   value grammar, `.1f` behavior, trailing-zero removal, order, and exact
   physical values `27.3125 → 27.3`, `27.84375 → 27.8`, and `0.0 → 0`.
10. **AMS compatibility:** preserve exact `AMS connected`,
    `AMS not connected`, and `ams is None` omission behavior in active and
    non-active connected states; slots remain absent from summary semantics.
11. **Assessment compatibility:** retain exact dictionaries for all six
    connected states and disconnected precedence; prove populated telemetry and
    summary suppression do not alter assessment.
12. **Single retrieval:** for one real registered-tool invocation, assert
    `len(fake_adapter.instances) == 1` and
    `fake_adapter.instances[0].get_status_calls == 1` while validating status,
    summary, and assessment from the same response.
13. **Structured preservation:** test suppression for `IDLE`, `ERROR`,
    `UNKNOWN`, and connected `OFFLINE` without any change to serialized
    progress, layers, remaining time, temperatures, or AMS.
14. **Error path:** preserve every existing exact `PrinterError` response and
    absence of `summary` and `assessment`; summary filtering must not execute a
    second retrieval or alter exceptions.
15. **Existing formatting regressions:** keep progress finite-only behavior,
    display clamp, half-up rounding, layer partial-value grammar, remaining-time
    grammar, separator/order, and determinism tests. Move progress state-agnostic
    formatting cases to an active-job state so they test formatting separately
    from eligibility.

Tests must fail if Build mutates normalized data, treats a non-active state as
an active job, hides active PRINTING/PAUSED telemetry, changes temperature or
AMS grammar, changes assessment/errors, or retrieves more than once.

## Verification Commands

Before editing, Build must inspect:

```powershell
git status --short
```

Focused MCP tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_printer_mcp.py
```

Relevant printer regressions:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_interfaces.py tests/unit/test_printer_transport.py tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
```

Ruff and Mypy over the exact changed scope:

```powershell
.\.venv\Scripts\ruff.exe check src/print_engineer/mcp/tools/printer.py tests/unit/test_printer_mcp.py
.\.venv\Scripts\python.exe -m mypy src/print_engineer/mcp/tools/printer.py tests/unit/test_printer_mcp.py
```

After focused success, run the full unit suite with all Bambu hardware variables
and hardware-test gates removed from the child environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/
```

If a known Windows pytest temp-root `PermissionError` occurs, retry the affected
command once with a unique `--basetemp` beneath system TEMP. Classify known
Windows temp-root, slicer subprocess, or print-context failures as environment
or unrelated failures rather than changing this increment to fix them.

Do not run integration tests, enable hardware gates, or perform a hardware test.

After verification, Build must inspect:

```powershell
git status --short
git diff --stat
git diff
```

and confirm that the diff is limited to the exact two-file Build scope while all
parked/user changes remain untouched.

## MQTT / Safety Impact

This increment is a pure presentation change. Required deltas are exactly:

| Capability / operation | Added by Summary v2 |
|---|---:|
| Additional `get_status()` calls | 0 |
| MQTT operations | 0 |
| Refresh requests | 0 |
| Adapters or connections | 0 |
| Workers or caches | 0 |
| Reconnect behavior | 0 |
| Printer-control capability | 0 |

There must remain zero MQTT publish paths in this increment. Do not connect to
hardware, publish MQTT, use the Bambu request topic, or introduce any state
change.

## Hardware Verification Decision

NO NEW HARDWARE VERIFICATION REQUIRED.

The physical A1 observation already demonstrated the exact connected-IDLE
telemetry motivating the change through the complete existing read-only path.
The proposed Build changes only deterministic local formatting of that already
normalized object. It introduces no new protocol, transport, normalization,
refresh, lifecycle, or hardware assumption. Hermetic unit coverage is the
appropriate verification boundary.

## Risks / Unverified Assumptions

- `PrinterState` is currently exhaustive over the six states in the matrix. If
  the enum changes before Build, that is a repository/plan conflict requiring
  plan review rather than an inferred fallback policy.
- Connected `OFFLINE` is semantically unusual, but Summary v1 explicitly treats
  it as valid and renders the literal `Offline` lead. Summary v2 preserves that
  contract and suppresses only job-specific fragments.
- Temperatures or AMS can also be old in some hypothetical lifecycle, but the
  repository has no freshness signal and the requested contract strongly
  preserves their informational value. This plan intentionally does not invent
  freshness or suppress them by state.
- Existing tests conflate progress numeric formatting with `UNKNOWN` state.
  Build must separate those concerns by exercising numeric grammar in
  `PRINTING` or `PAUSED`, then independently pin `UNKNOWN` suppression.

No unresolved implementation semantic remains. If Build discovers a material
conflict with these repository contracts, it must stop and report the conflict
instead of redesigning the policy.

## Acceptance Criteria

1. Active-job states are exactly `PRINTING` and `PAUSED`.
2. Progress is eligible only in those states and retains Summary v1 numeric
   formatting.
3. Layers are eligible only in those states and retain Summary v1 grammar.
4. Remaining time remains eligible only in those states with unchanged grammar.
5. `IDLE`, `ERROR`, `UNKNOWN`, and connected `OFFLINE` suppress all three
   job-specific fragments even when structured fields are populated.
6. Temperatures retain Summary v1 eligibility and formatting in every connected
   state.
7. AMS retains Summary v1 eligibility and wording in every connected state.
8. The physical A1 input produces exactly
   `Idle · Nozzle 27.3 / 0 °C · Bed 27.8 / 0 °C · AMS connected`.
9. The representative full PRINTING summary remains byte-for-byte unchanged,
   and PAUSED retains progress, layers, and remaining time.
10. Disconnected remains exactly `Printer disconnected`.
11. Every structured field is serialized unchanged despite summary suppression.
12. `_assess_status()` and all assessment dictionaries remain unchanged.
13. Error responses remain unchanged and contain no summary or assessment.
14. Each tool invocation constructs one adapter and calls `get_status()` once.
15. No MQTT/network/lifecycle/control behavior changes.
16. Build changes exactly the two scoped files and preserves parked/user work.
17. Focused tests, relevant printer regressions, Ruff, and Mypy pass; broader
    unit failures, if any, are classified without unrelated fixes.
18. No hardware verification is run or required.

## Definition of Done

For a future Build, this increment is done only when:

- the exact state eligibility matrix is implemented without changing any
  Summary v1 fragment grammar;
- the physical A1, all-state, active-job, structured-preservation, assessment,
  disconnected, error, and single-retrieval tests pass;
- focused and relevant printer tests pass;
- Ruff and Mypy pass on the exact changed files;
- the full unit suite is run after focused success and any unrelated/environment
  failures are classified;
- final status and diff inspection proves only the two approved-at-Build-time
  scope files changed and unrelated work is untouched;
- no hardware/network/MQTT operation occurred and no hardware claim is made.

## Approval Questions

The proposal resolves the requested review questions as follows; an independent
reviewer may accept or reject these answers, but this document does not approve
them:

1. Active-job states: exactly `PRINTING` and `PAUSED`.
2. Progress only for `PRINTING`/`PAUSED`: yes.
3. Layers only for `PRINTING`/`PAUSED`: yes.
4. Remaining-time behavior unchanged: yes, only `PRINTING`/`PAUSED`.
5. Temperatures unchanged across connected states: yes.
6. AMS behavior unchanged: yes.
7. Real A1 IDLE summary: exactly
   `Idle · Nozzle 27.3 / 0 °C · Bed 27.8 / 0 °C · AMS connected`.
8. `ERROR`: suppress progress, layers, and remaining time; retain temperatures
   and AMS under existing availability rules.
9. `UNKNOWN`: suppress progress, layers, and remaining time; retain temperatures
   and AMS under existing availability rules.
10. Connected `OFFLINE`: literal `Offline` lead; suppress progress, layers, and
    remaining time; retain temperatures and AMS under existing rules.
11. Disconnected: exactly `Printer disconnected`.
12. Structured fields completely untouched: yes.
13. Assessment completely untouched: yes.
14. One-`get_status()` invariant retained: yes.
15. Two-file Build scope sufficient: yes, based on current repository evidence.
16. Hardware verification unnecessary: yes.

## Final Verdict

The repository supports a precise presentation-only increment with no domain,
assessment, retrieval, MQTT, or hardware changes. The conservative policy is
to expose job-specific summary fragments only for the two states that clearly
represent an active job, while preserving connected informational temperature
and AMS telemetry.

PROPOSED — READY FOR INDEPENDENT PLAN REVIEW
