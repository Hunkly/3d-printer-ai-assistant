# Phase 2 — Passive Printer State Accumulator

## Status

APPROVED

## Understanding

Add a Bambu-specific, connection-scoped accumulator that converts ordered passive MQTT delta reports into the best available last-known `PrinterStatus` without fabricating fields that have never been observed.

The data path remains strictly:

`subscribe/read raw delta → validate → update present valid fields → retain absent/invalid fields conservatively → return PrinterStatus`

The transport remains transport-only and continues delivering ordered raw reports. This increment does not add fields to `PrinterStatus`, redesign MCP lifetime, or send any MQTT command.

## Real Hardware Evidence

Commit `f642f8a` established reliable repeated passive reads on one A1 LAN MQTT connection. Real hardware produced approximately one report every two seconds while printing, but individual reports contained different subsets. A richer report included progress, remaining-time metadata, layer, temperatures, target temperature, Wi-Fi, and fan fields; other reports contained only `nozzle_temper`, or only `layer_num` and `bed_temper`. State, target temperatures, and AMS may be seen once and then be absent for 12–20 seconds.

Therefore one report is not a complete snapshot, and a fixed collection interval cannot prove completeness after a cold connection. The implementation must preserve last-known values across deltas while retaining `UNKNOWN`/`None` for values never observed.

## Existing Architecture

- `PahoMqttClient` owns connection, subscription, ordered per-topic FIFO delivery, and disconnect. It must remain unaware of printer-domain fields.
- `BambuPrinterAdapter` currently owns JSON validation, Bambu field interpretation, normalization, connection error mapping, and standalone versus retained-client lifecycle.
- `PrinterStatus` currently contains only `state`, `is_connected`, current and target bed/nozzle temperatures, `progress`, and `ams`.
- `printer.status` creates a new adapter and performs one standalone `get_status()` call; it does not retain a printer service between MCP calls.

Accumulation is therefore an adapter-domain responsibility. Moving it into transport would couple MQTT delivery to Bambu schema, while moving it into MCP would duplicate adapter behavior and exclude non-MCP callers.

## Accumulator Ownership

Add a private `_BambuStatusAccumulator` used only by `BambuPrinterAdapter`, initially in `src/print_engineer/adapters/printer/bambu.py`. Do not create a public class, protocol, interface, dependency, or transport API.

The helper owns Bambu delta merge state and produces immutable `PrinterStatus` snapshots. Both retained and standalone adapter paths must use the same helper and parsing functions. Keep JSON decoding/structural validation in the adapter layer and preserve the existing public `Printer` and `MqttClient` contracts.

## Cache Model

The private accumulator stores connection-scoped last-known values for exactly:

- normalized state plus a private `state_observed` flag
- bed temperature
- nozzle temperature
- target bed temperature
- target nozzle temperature
- progress
- AMS information
- whether at least one structurally valid report has been applied

Use explicit presence checks against the `print` mapping; never use truthiness to detect telemetry presence. `0`, `0.0`, and numeric string zero are valid values.

An empty accumulator snapshots as:

```text
state=PrinterState.UNKNOWN
is_connected=False
all numeric fields=None
ams=None
```

After any structurally valid `print` delta is applied, snapshots use `is_connected=True`, even when all recognized fields in that delta are absent or individually malformed. Unknown public values remain `UNKNOWN`/`None`; the accumulator does not invent defaults.

## Delta Merge Semantics

For every supported field, distinguish three cases:

1. **ABSENT** — key is not present in the `print` mapping; retain the cached value and observed state unchanged.
2. **PRESENT VALID** — parse using the existing normalization rule; update the cached value, including explicit zero.
3. **PRESENT INVALID** — key exists but has an unsupported type or cannot be parsed; retain the cached value. If never observed, it remains `UNKNOWN`/`None`.

Malformed individual fields do not invalidate an otherwise structurally valid delta. This deliberately preserves prior good telemetry rather than letting one bad delta erase it. A fresh accumulator still produces the same public unknown values expected by current defensive normalization tests.

Apply terminal-state progress cleanup before applying other valid fields from the same delta, so an explicit valid progress value included with a terminal state wins over the cleared cached progress. Target temperatures and every other modeled field continue to follow the normal absent/valid/invalid rules during terminal transitions.

## State Semantics

Preserve the approved mapping:

- `IDLE` → `IDLE`
- `RUNNING` and `PREPARE` → `PRINTING`
- `PAUSE` → `PAUSED`
- `FINISH` → `IDLE`
- `FAILED` → `ERROR`
- literal `UNKNOWN` → `UNKNOWN`

Rules:

- Missing `gcode_state` retains the prior state and does not mark state as observed.
- A recognized string, including literal `UNKNOWN`, updates state and sets `state_observed=True`.
- A non-string or unrecognized string is PRESENT INVALID: retain the prior state. On an empty accumulator the snapshot remains `UNKNOWN`, but `state_observed` remains false.
- `PAUSE` followed by `RUNNING`/`PREPARE` updates state to `PRINTING` without clearing other cached telemetry.

This distinguishes an absent delta field from an explicit printer-reported `UNKNOWN`.

## Progress / Print Lifecycle Semantics

- Valid `mc_percent` updates progress using the existing rule: `round(float(value) / 100.0, 4)`.
- Missing `mc_percent` retains progress.
- Malformed present `mc_percent` retains progress; on an empty cache it remains `None`.
- Explicit numeric zero updates progress to `0.0`.

Raw terminal states `IDLE`, `FINISH`, and `FAILED` clear only cached progress before other fields in that same delta are applied. Clearing progress is an accumulator-level print-lifecycle rule: retaining a completed or failed print percentage beside `IDLE` or `ERROR` would be misleading. If the terminal delta explicitly contains valid progress, that same-delta value is then applied.

Target temperatures, current temperatures, and AMS are never cleared merely because of a terminal state. In particular, absent target fields retain their previous last-known valid values, valid target fields update them including explicit zero, and malformed-present target fields retain their previous values. `RUNNING`, `PREPARE`, `PAUSE`, literal `UNKNOWN`, missing state, and invalid state do not trigger progress cleanup.

## Temperature Semantics

Preserve the current mappings and float parsing:

- `bed_temper` → `bed_temp`
- `nozzle_temper` → `nozzle_temp`
- `bed_target_temper` → `target_bed_temp`
- `nozzle_target_temper` → `target_nozzle_temp`

For all four fields, valid integers, floats, and numeric strings update the cache. Absence or malformed-present values retain prior values. Explicit zero is valid and must update the cache to `0.0`; it is never treated as missing.

These rules apply unchanged during `IDLE`, `FINISH`, and `FAILED` transitions. A terminal state alone is not evidence that a target changed or cleared.

## AMS Semantics

- Missing `ams` retains cached AMS information.
- A present payload that satisfies the current approved `ams.ams[].tray[]` structure updates the cache through the existing tray/slot normalization rules.
- A present malformed AMS value retains cached AMS; on a fresh cache it remains `None`.
- The external-spool sentinel and metadata-only tray behavior remain unchanged.

No verified explicit AMS-disconnected payload shape exists. Do not infer disconnection from a missing key, `None`, an empty/malformed mapping, or absence of loaded trays. Explicit disconnect clearing is deferred until sanitized hardware evidence establishes a representation. Session reset remains the only approved way to clear cached AMS in this increment.

## Cold-Start Behavior

Use the adapter's existing `timeout_seconds` as one total monotonic deadline; add no sleep and no second arbitrary timeout.

The exact readiness predicate is:

```text
at least one structurally valid delta has been applied
AND a recognized gcode_state (including literal UNKNOWN) has been observed
```

For a cold accumulator:

1. Fetch and apply ordered reports using only the remaining deadline.
2. Return immediately when the readiness predicate becomes true.
3. If the deadline expires after at least one structurally valid delta but before state readiness, return the partial accumulated snapshot with `state=UNKNOWN` and unknown fields as `None`.
4. If no report is received before the deadline, raise the existing `PrinterTimeout`.
5. A structurally invalid report raises `PrinterInvalidReport` immediately rather than being skipped.

Readiness is only an early-return condition; it is not a claim of snapshot completeness. Current temperatures alone establish connected telemetry and permit a partial result at the deadline, but they do not satisfy state readiness.

## Freshness / Staleness

Do not add timestamps or a TTL in this increment.

Hardware evidence proves some legitimate fields may be absent for at least 12–20 seconds, but does not establish a safe expiration interval. An arbitrary TTL could erase valid slow-changing state and recreate the sparse-report defect. Cache lifetime is bounded by the MQTT connection/session and explicit terminal-state cleanup:

- reset before each new connection attempt
- retain during one successful connection
- reset on disconnect, including cleanup exceptions
- reset after failed connect/authentication/unreachable outcomes

Long-lived-session freshness beyond these rules remains a documented limitation requiring separate evidence and a future plan.

## Connection / Session Lifecycle

- Adapter construction starts with an empty accumulator and no client.
- `connect()` while an adapter client is already retained is an idempotent no-op; it must not create a second client or reset accumulated state.
- A new connection attempt resets the accumulator before contacting the client.
- A successful connection retains that fresh accumulator for subsequent `get_status()` calls.
- Authentication or unreachable connection failure leaves no retained client and an empty accumulator.
- Each retained `get_status()` must receive at least one new structurally valid delta before returning; an already-ready cache does not permit returning without a new report.
- If no report arrives during a retained call, raise `PrinterTimeout` and preserve the same-session cache for a later call.
- If one or more valid deltas arrive but readiness is still unmet at the deadline, return the partial snapshot.
- A structurally invalid report raises `PrinterInvalidReport` without applying that report or corrupting prior cached state.
- `disconnect()` disconnects the retained client in `finally` and always resets the accumulator, including when transport disconnect raises.
- Reconnect after disconnect starts with an empty accumulator; values never cross connection boundaries.

## Standalone get_status() Behavior

Standalone `get_status()` continues to own one temporary connection and guaranteed disconnect, but uses a fresh temporary accumulator and bounded passive accumulation within the existing timeout budget.

It follows the same rules as a retained call: fetch until state readiness or deadline, return a partial snapshot if at least one valid sparse delta was received, and raise `PrinterTimeout` only if no report arrived. It never sleeps beyond waiting in `fetch_report()` and never carries cache state into a later standalone call.

This improves the chance of a useful one-shot snapshot without claiming completeness. Fields that do not appear before readiness or deadline remain unknown. The temporary accumulator is discarded when the standalone connection closes.

## MCP Implications

`printer.status` remains unchanged: it constructs an adapter and invokes standalone `get_status()`. It benefits from bounded passive accumulation within the existing timeout, but every MCP call still starts with an empty cache.

Do not introduce a persistent MCP printer service or change MCP registration/configuration in this increment. A one-shot MCP result may remain partial because passive telemetry offers no completeness guarantee. Persistent cross-call status service lifetime is a separate architecture decision.

## Invalid Report Handling

Preserve current structured errors for invalid UTF-8, invalid JSON, non-object roots, missing `print`, and non-object `print`. Such reports raise `PrinterInvalidReport` immediately and are never applied.

Valid sparse `print` mappings, including empty mappings or mappings containing no currently modeled fields, are valid deltas and set `is_connected=True`. Malformed individual modeled fields retain prior cached values as defined above and do not raise.

If valid deltas were already applied before a later structurally invalid report in the same call, the valid updates remain in the same-session accumulator, while the invalid report contributes nothing. The call raises, making the failure visible.

## Thread-Safety Model

No accumulator lock is required. Paho callbacks enqueue raw payloads inside the transport; accumulator parsing and mutation occur synchronously only after `fetch_report()` returns to the adapter caller thread.

The adapter is not made safe for simultaneous `get_status()` calls in this increment. Tests must be deterministic and single-caller. Do not add background accumulator threads, callbacks, locks, timers, or polling services.

## Required Production Changes

Modify only:

- `src/print_engineer/adapters/printer/bambu.py`

Add the private accumulator, tri-state field parsing/merge behavior, bounded multi-report read helper, accumulator session reset, and retained-connect idempotency. Preserve the transport, `PrinterStatus`, interfaces, errors, MCP code, configuration, exports, dependencies, and unsupported-operation behavior.

Do not modify `src/print_engineer/adapters/printer/transport.py`; it already provides the ordered passive receive contract required here.

## Required Test Changes

Create:

- `tests/unit/test_bambu_state_accumulator.py`

Modify:

- `tests/unit/test_bambu_printer_adapter.py`
- `tests/integration/test_bambu_printer_lan.py` only for the separately gated post-review hardware harness

Accumulator tests may import the private helper to prove merge semantics directly, while adapter tests must exercise the real `BambuPrinterAdapter` lifecycle and report-reading loop. Use fake `MqttClient` sequences; no broker, LAN, internet, sleeps, or physical printer.

Required hermetic coverage:

- sparse report A with state/current temperatures followed by report B with progress retains A and updates progress
- every missing modeled field retains its prior value
- explicit numeric zero updates current temperatures, targets, and progress
- malformed-present state, progress, temperatures, targets, and AMS retain prior values and remain unknown when never validly observed
- literal `UNKNOWN` updates state and satisfies state readiness; unrecognized/non-string state does not
- `PRINTING → sparse delta` retains printing state
- `PRINTING → PAUSE → RUNNING` updates state without erasing other telemetry
- starting from `PRINTING` with `progress=0.73`, `target_bed_temp=65`, and `target_nozzle_temp=220`, a later raw `FINISH` or `IDLE` delta without progress or target fields produces `state=IDLE`, `progress=None`, `target_bed_temp=65`, and `target_nozzle_temp=220`
- raw `IDLE`, `FINISH`, and `FAILED` clear only progress, with an explicit valid same-delta progress value applied afterward
- a later delta with `bed_target_temper=0` and `nozzle_target_temper=0` updates the cached targets to `0.0`
- malformed-present target values retain the previous last-known valid targets, including across terminal transitions
- valid AMS is cached; later missing or malformed AMS retains it; no unsupported disconnect shape is invented
- empty cache snapshots unknown values; first structurally valid delta sets `is_connected=True`
- new connection, failed connection, disconnect, disconnect exception, and reconnect reset cache as specified
- retained repeated reads use one client and merge distinct reports
- retained timeout before any new report raises while preserving same-session cache
- structurally invalid report raises without corrupting cached good state
- standalone cold start returns immediately on state readiness
- standalone cold start combines sparse deltas until readiness
- standalone deadline returns partial after valid telemetry without state
- standalone no-report timeout raises and always disconnects
- existing normalization, lifecycle, error, unsupported-operation, transport, MCP, and default hardware-skip tests remain green

Use an injected monotonic clock only if needed to make the deadline deterministic; do not use real sleeps.

## Hardware Verification Plan

After Build and independent Review, add/use a separately gated test requiring exact `RUN_BAMBU_LAN_STATE_ACCUMULATOR_TEST=1` plus the existing three `BAMBU_*` variables. The exact gate must run before credential reads or adapter construction. Do not run it during Build.

The test must use the real production adapter and one retained connection while the printer is actively printing:

`connect → get_status snapshot 1 → get_status snapshot 2 → get_status snapshot 3 → disconnect in finally`

Display only sanitized normalized values already present in `PrinterStatus`: state, progress, current temperatures, target temperatures, AMS-present boolean, snapshot ordinal, and relative timing. Do not print raw payloads, field names outside the model, IP, serial, access code, filenames, identifiers, or complete exception details.

Verify observationally that a modeled value present in an earlier snapshot is retained when absent from a later sparse delta, except for the approved terminal-state cleanup. Record whether cold-start readiness was reached or a partial deadline result was returned. Hardware findings must not trigger automatic code changes.

## Read-Only Safety

Maintain **ZERO MQTT PUBLISH PATHS**.

The increment may only connect, subscribe/read ordered telemetry, validate, merge modeled last-known values in memory, return status, and disconnect. Search the complete relevant diff for `publish`, `/request`, `pushall`, and printer-control paths before approval.

Do not add MQTT publish, `device/{serial}/request`, `pushall`, start/stop/pause/resume commands, temperature commands, settings changes, upload, FTPS, camera, cloud MQTT, slicing, or automatic printing.

## Risks

- A partial cold-start result can still lack state or slow-changing fields; `PrinterStatus` has no completeness marker.
- No TTL means a long-lived session can retain old values until a valid update, explicit terminal transition, or disconnect. This is deliberate because no evidence-based TTL exists yet.
- Terminal cleanup is intentionally limited to progress. Target temperatures can therefore remain last-known and potentially old during an idle/error period until an explicit valid target update or session reset; clearing them from state alone would violate sparse-delta semantics.
- Malformed-present retention favors last-known good data over surfacing field-level corruption. Structurally invalid reports still fail visibly.
- Literal `UNKNOWN` intentionally replaces a known state, while unrecognized values do not; tests must pin this distinction.
- Standalone accumulation can consume the full existing timeout when state is not observed, increasing one-shot latency without exceeding the current timeout contract.
- No verified AMS-disconnect shape exists, so same-session cached AMS may remain until disconnect.

## Out of Scope

- new `PrinterStatus` fields
- layer number, remaining time, Wi-Fi, fan-speed, firmware, model, or filename exposure
- per-field timestamps, freshness metadata, or TTL expiration
- cross-session or persisted cache
- multi-printer cache service
- MCP service-lifetime redesign
- transport changes or a new receive API
- explicit AMS disconnect parsing without hardware evidence
- MQTT `publish()`
- `device/{serial}/request`
- `pushall`
- printer control, temperature commands, settings changes, uploads, FTPS, camera, cloud MQTT, slicing, or automatic printing

## Implementation Order

1. Review and approve this plan.
2. Record the expected changed-file set and working-tree state.
3. Add focused private-accumulator tests for tri-state merging, terminal cleanup, AMS, and snapshots.
4. Implement `_BambuStatusAccumulator` and reuse existing normalization helpers in `bambu.py`.
5. Add deterministic adapter tests for cold-start bounded collection, retained sessions, failures, and reset behavior.
6. Add the separately gated hardware harness without enabling it.
7. Run focused accumulator/adapter tests, existing transport and MCP regressions, and the disabled integration suite.
8. Run focused Ruff and Mypy.
9. Audit the complete diff and explicit zero-publish/request/pushall searches.
10. Independently review software behavior and hardware-harness safety.
11. Only after explicit authorization, execute the state-accumulator hardware test and record sanitized findings.
12. Create a separate proposed plan for any model expansion, TTL, persistent MCP service, or compatibility correction.

## Acceptance Criteria

- Accumulation is private to the Bambu adapter layer; transport and public interfaces remain unchanged.
- Only existing `PrinterStatus` fields are cached and returned.
- Absent fields retain prior values; valid present fields update; malformed present fields retain prior values.
- Explicit zero is preserved.
- State absence differs from literal `UNKNOWN` and invalid state values.
- Raw `IDLE`, `FINISH`, and `FAILED` clear only progress before same-delta updates; they never clear target temperatures or other modeled fields by state inference.
- Target temperatures retain their last valid values when absent, update on valid values including explicit zero, and retain their last valid values when malformed, including during terminal transitions.
- Missing/malformed AMS retains valid cached AMS; no disconnect shape is invented.
- Cold start uses the existing total timeout, exact state-observed readiness, partial-result rule, and no sleeps.
- Every retained call consumes at least one new valid delta; timeout does not return stale cache as fresh status.
- Cache resets across connection attempts, failures, disconnect, and reconnect.
- Standalone and retained paths share merge logic while keeping their existing lifecycle ownership.
- Structurally invalid reports remain structured failures and do not corrupt cached state.
- No accumulator locking/background work is introduced.
- MCP architecture and response schema remain unchanged; one-shot incompleteness stays explicit.
- Hermetic tests, existing regressions, Ruff, and Mypy pass.
- The hardware harness is separately gated and skipped by default.
- Zero MQTT publish, request-topic, `pushall`, and printer-control paths are maintained.

## Open Questions

None block Build. The explicit AMS-disconnected representation, evidence-based field TTL, and persistent MCP service lifetime require separate hardware/architecture evidence and are deliberately deferred by the conservative rules above.

## Final Verdict

ADAPTER + INTERNAL CACHE CHANGES REQUIRED

PLAN ONLY — no source or test files were modified and no hardware connection was performed.

## Hardware Verification Result

RESULT: PASS WITH FOLLOW-UP FINDING

The separately gated accumulator experiment completed successfully against a real Bambu Lab A1 while it was printing. Three sanitized snapshots were observed:

- Snapshot 1 at approximately `+10.016s`: state `unknown`, progress `0.51`, bed temperature approximately `65.03`, nozzle temperature approximately `219.91`, target temperatures `None`, and AMS not yet observed.
- Snapshot 2 at approximately `+20.032s`: state `unknown`, progress `0.51`, bed temperature approximately `65.06`, nozzle temperature approximately `220.03`, target temperatures `None`, and AMS not yet observed.
- Snapshot 3 at approximately `+30.047s`: state `unknown`, progress `0.52`, bed temperature approximately `65.03`, nozzle temperature approximately `220.06`, target temperatures `None`, and AMS not yet observed.

This proves that passive state accumulation works against real hardware: sparse reports preserved previously observed values, progress remained available and advanced from `0.51` to `0.52`, and current temperatures remained accumulated while updating. No MQTT publish, `device/{serial}/request`, `pushall`, or printer-control operation occurred. Connection cleanup completed normally.

Missing state, target temperatures, and AMS do not establish that those printer properties were absent; they were not observed during this MQTT session, and the accumulator correctly did not fabricate them.

Each `get_status()` call took approximately the full 10-second timeout because no recognized `gcode_state` was observed during the session. The approved readiness predicate therefore waited until its deadline on every retained call. This matches the approved implementation and is not a Build defect.

Follow-up requirement: a separate future plan should distinguish an empty cold session from a warm retained session. A cold session should keep the existing bounded cold-start accumulation/readiness behavior. Once a retained session already has valid accumulated telemetry, `get_status()` should wait for the next valid passive delta, merge it, and return the updated snapshot immediately instead of repeating cold-start readiness waiting. This result does not implement that behavior and does not redesign MCP.
