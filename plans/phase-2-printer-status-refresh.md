# Phase 2 — Narrow Read-Only Printer Status Refresh

## Status

APPROVED

## Problem Statement

The current Bambu Lab A1 integration is strictly passive and has **ZERO MQTT
PUBLISH** paths. That boundary has been independently reviewed and verified on
real hardware. The physical A1 successfully established LAN MQTT over TLS on
port 8883, authenticated as `bblp`, delivered passive
`device/{serial}/report` telemetry, normalized through the production
`PrinterStatus` path, and disconnected cleanly without any publish, request
topic, or printer command.

Real passive observations also established a material limitation. During short
reads, a 12-second multi-report window, and multiple retained 60-second active
print sessions, the A1 repeatedly emitted sparse deltas such as current bed and
nozzle temperatures, fan fields, Wi-Fi signal, `command`, and `msg`.
`mc_remaining_time` and `layer_num` appeared occasionally. The sessions did
not produce `gcode_state`, `mc_percent`, target temperatures, or AMS telemetry.
The existing adapter already receives multiple reports under one total cold
deadline and merges known fields into a connection-scoped accumulator.
Waiting longer inside the existing bounded cold read is therefore not a
supported solution for a useful one-shot `printer.status` result.

Protocol research identifies one narrowly scoped informational operation,
`pushing.pushall`, conventionally used by Bambu clients to ask the printer to
emit a full `print.push_status` snapshot. It is an MQTT publish to the same
request topic that also carries dangerous control commands. This increment
must therefore authorize exactly one fixed information request without making
general MQTT writes or printer control representable.

## Goal

For the adapter's **standalone** `get_status()` path only, subscribe to the
configured report topic, send at most one fixed informational `pushall`, merge
the resulting telemetry through the existing accumulator, return a truthful
`PrinterStatus`, and disconnect in `finally`.

The increment must preserve the current public MCP contract and all existing
passive retained-session behavior.

## Protocol Evidence and Uncertainty

The protocol is not an officially supported Bambu public API. Available
upstream-client logs and established open-source protocol implementations
agree on these conventions:

- printer telemetry and responses arrive on `device/{serial}/report`;
- client requests are published to `device/{serial}/request`;
- a `pushing.pushall` request asks for current/full status telemetry;
- Bambu/Orca networking uses a payload containing `command="pushall"`,
  `version=1`, `push_target=1`, and a decimal-string `sequence_id`, at QoS 0;
- a candidate full snapshot is reported under `print` with
  `command="push_status"` and `msg == 0`, while autonomous deltas commonly use
  `msg == 1`;
- A1-family observations exist, but exact support on this repository's
  physical A1 and its current firmware has not yet been verified.

These are sufficient to define a hermetic implementation and separately gated
hardware experiment, but not to claim compatibility before that experiment.
The implementation must treat missing/rejected refresh responses truthfully
and must not enable Developer Mode or add a workaround.

## Safety-Boundary Change

Current invariant:

```text
ZERO MQTT PUBLISH
```

Proposed primary invariant for this increment:

```text
AT MOST ONE FIXED INFORMATIONAL PUSHALL PER ELIGIBLE STANDALONE get_status()
```

Additional cross-call invariant:

```text
AT MOST ONE INFORMATIONAL PUSHALL PER CONFIGURED PRINTER PER 5-MINUTE
PROCESS-LOCAL COOLDOWN
```

This is an explicit, narrow relaxation. It does not authorize a general MQTT
write capability. The five-minute cooldown is a conservative safety/rate
boundary derived from documented `pushall` guidance for resource-constrained
Bambu hardware; it is not claimed to be an A1 protocol requirement. A caller
cannot override or bypass it. There is no periodic timer, background refresh,
or delayed publish.

The following remain forbidden:

- arbitrary topics, payloads, JSON, commands, QoS, or control parameters;
- `publish(topic, payload)` or an equivalent general request API;
- `pushing.start` or `pushing.stop`;
- `print.start`, `stop`, `pause`, `resume`, `project_file`, `gcode_line`, or
  any other printer command;
- temperature, settings, calibration, motion, or AMS commands;
- uploads, FTPS, camera, cloud MQTT, command signing, or Developer Mode
  changes;
- retries, periodic refresh, background refresh, polling, or monitor behavior.

## Exact Allowed Outbound Capability

Exactly one outbound topic is permitted:

```text
device/{configured_serial}/request
```

Exactly one operation is permitted:

```text
pushing.pushall
```

The exact JSON schema is:

```json
{
  "pushing": {
    "sequence_id": "<internally generated decimal string>",
    "command": "pushall",
    "version": 1,
    "push_target": 1
  }
}
```

QoS is fixed to `0`.

The caller supplies no argument to the outbound operation. Topic, payload,
command, version, push target, QoS, and sequence format are constructed inside
the concrete Paho transport from the serial captured at client construction.
No raw payload or request topic crosses the adapter/transport boundary.

## Sequence-ID Policy

`PahoMqttClient` owns the status-refresh sequence ID. It is never supplied by
MCP, the adapter, configuration, or another caller.

Generate the value internally as a non-negative base-10 decimal string.
`"0"` is an acceptable initial/default value and appears in protocol
implementations, but literal `"0"` is not a protocol requirement. The value is
correlation metadata rather than part of the safety boundary. Tests must
verify decimal format and caller-inaccessibility, not one universal starting
value.

A small private counter/state or deterministic injection may be used to make
hermetic tests stable. Do not add persistent, process-global sequence
infrastructure. Sequence allocation remains private to the concrete transport.
A second refresh attempt on the same client must never cause a second Paho
publish.

## Process-Local Refresh Cooldown

The concrete transport module owns a module-private process-local eligibility
mapping keyed by configured printer serial, plus a module-private lock and a
monotonic clock/helper boundary. The mapping stores the next-eligible time or
last issued refresh time. It is shared by separately constructed standalone
clients in the same process; different serials are independent. Do not expose
a public rate-limiter API, persist this state, or add background cleanup.

The short check/reservation step is atomic per serial:

1. connect;
2. create the report queue and successfully submit the report subscription;
3. under the module-private lock, check and reserve refresh eligibility;
4. release the lock before the network publish;
5. if eligible, issue the one fixed informational publish;
6. if ineligible, issue no publish and continue with passive report receipt.

The lock must never cover connection, subscription I/O, publish I/O, report
receipt, normalization, or disconnect. Separate adapters for the same serial
cannot both win one cooldown window. Separate printer serials do not block one
another.

The reservation records the monotonic timestamp for the outbound attempt when
the refresh is issued. Once the publish is attempted, the five-minute cooldown
remains consumed even if the response times out, is malformed, lacks a full
marker, or disconnect later fails. A connection or subscription failure before
any publish does not consume the cooldown. There is no automatic retry and no
force-refresh bypass.

During cooldown, standalone `get_status()` falls back to the existing passive
cold behavior: connect, receive and accumulate passive telemetry under the
existing total deadline, return the truthful best partial status, and
disconnect. Suppression alone is not an error and does not return a fabricated
cached full snapshot.

## Architecture Decision

Extend the minimal transport protocol only with a zero-argument narrow
capability equivalent to:

```python
def request_status_refresh(self) -> bool: ...
```

`MqttClient` must not gain `publish()`, `request(topic, payload)`,
`send_command()`, or any caller-parameterized write method.

The concrete transport needs the configured serial so it can construct both
fixed topics internally. Extend `MqttClientFactory` / `PahoMqttClientFactory`
and `PahoMqttClient` construction with a keyword-only serial value. The
adapter already owns the validated configured serial and passes it to the
factory. Do not derive the serial by parsing `client_id`.

`request_status_refresh()` must:

1. ensure the fixed `device/{serial}/report` queue exists;
2. call `subscribe(report_topic, qos=0)`;
3. inspect its immediate return code and require `MQTT_ERR_SUCCESS`;
4. on a subscribe exception or non-success result, raise the existing
   transport failure without reserving cooldown eligibility and with zero
   publishes;
5. atomically check/reserve the process-local per-serial cooldown;
6. when eligible, construct the exact fixed request topic and payload
   internally and perform exactly one underlying Paho publish at QoS 0;
7. validate the immediate Paho publish result;
8. return whether the fixed publish was issued, so the adapter can retain
   truthful diagnostics without gaining any write parameters;
9. never retry the subscription or publish.

Waiting for SUBACK is not required by this increment unless implementation
evidence later proves it necessary. Immediate successful subscription
submission and same-client packet ordering are the planned boundary. A publish
must be unreachable after failed subscription submission.

`fetch_report()` remains the only receive operation. Its existing subscription
tracking must ensure the adapter's subsequent fetch does not duplicate the
subscription.

The concrete class necessarily calls Paho's general publish API internally,
but no general publish capability is exposed through the repository transport
protocol. This single call site is the only authorized request-topic path.

## Standalone and Retained Behavior

The distinction is based on adapter lifecycle, not on how often a caller asks
for status.

### Standalone path

When `BambuPrinterAdapter.get_status()` begins with no retained client
(`self._client is None`), it may send exactly one refresh only if the configured
serial wins the process-local cooldown reservation:

```text
build client
→ connect
→ subscribe device/{serial}/report
→ atomically reserve eligibility if outside the five-minute cooldown
→ send exactly one fixed informational pushall only when eligible
→ receive and accumulate valid report telemetry
→ recognize candidate full snapshot
→ normalize through the existing accumulator
→ return truthful status
→ disconnect in finally
```

If the reservation is unavailable, the same lifecycle continues without the
publish and uses the existing passive cold response behavior. Repeated MCP
`printer.status` calls therefore cannot issue more than one same-serial
informational refresh within five minutes in one process.

### Explicit retained path

When a caller has already invoked `adapter.connect()`, neither the first cold
retained `get_status()` nor any warm retained `get_status()` sends a refresh.
They keep the current passive behavior:

- cold retained read: accumulate passive reports under the existing total
  deadline;
- warm retained read: consume one next valid passive report;
- disconnect: clear the session and accumulator.

This prevents a monitor, retained reader, or repeated warm call from becoming
a periodic `pushall` source. Monitor-core work remains separate and is not
authorized by this plan.

## Response and Accumulation Semantics

Do not create a second full-snapshot parser or a second normalization model.

Refactor the existing internal fetch/apply helper only as necessary to expose
the decoded `print` mapping to the standalone return-condition logic after it
has been applied to `_BambuStatusAccumulator`.

A report is a candidate full snapshot only when:

```python
print_obj.get("command") == "push_status" and print_obj.get("msg") == 0
```

Do not infer completeness from:

- field count;
- temperatures;
- state or progress alone;
- arrival order;
- the response sequence ID;
- `command="push_status"` without `msg == 0`.

The standalone refresh path uses one total configured response deadline. It
accumulates every structurally valid `print` report received before returning.

- If a candidate full snapshot arrives, apply it through the existing
  accumulator and return immediately.
- If valid partial telemetry arrives but no full marker arrives before the
  deadline, return the best accumulated partial status.
- Fields never observed remain `PrinterState.UNKNOWN` or `None`.
- Optional fields are never required before returning.
- If no valid report arrives, raise the existing `PrinterTimeout` with the
  existing topic/timeout details.
- Malformed reports retain the existing `PrinterInvalidReport` behavior and
  must still disconnect in `finally`.

The refresh request is not retried after timeout, malformed telemetry,
publish failure, or any other error.

## Error Mapping

- TCP/TLS/CONNACK behavior remains unchanged:
  `MqttConnectionError("auth")` becomes `PrinterAuthFailed`; other connection
  failures become `PrinterUnreachable`.
- An exception from the one fixed Paho publish, or an immediate non-success
  Paho publish result, becomes `MqttConnectionError("unreachable")` at the
  transport boundary and therefore the existing structured
  `PrinterUnreachable` at the adapter boundary.
- A report-subscription exception or immediate non-success result before the
  publish follows the same existing transport failure mapping, performs zero
  publishes, and does not consume cooldown eligibility.
- No valid response before the total deadline remains `PrinterTimeout`.
- Invalid UTF-8, JSON, root, or `print` structure remains
  `PrinterInvalidReport`.
- A valid partial response is not an error and is never presented as complete.
- Unsupported printer operations remain `PrinterOperationUnsupported` and
  must instantiate, connect, refresh, and publish zero clients/messages.

Do not add a new public error type unless implementation evidence shows the
existing structured mapping cannot represent an immediate refresh transport
failure. Such evidence would require revising this plan before Build expands
file scope.

## MCP Contract

No MCP source change is required.

`printer.status` continues to:

```text
resolve configuration
→ construct BambuPrinterAdapter
→ adapter.get_status()
→ serialize PrinterStatus or PrinterError
```

MCP must not know the request topic, payload, sequence ID, full-snapshot
marker, or publish mechanics. The existing success and structured-error JSON
contracts remain unchanged.

## Exact Build Scope

Modify only:

- `src/print_engineer/adapters/printer/transport.py`
- `src/print_engineer/adapters/printer/bambu.py`
- `tests/unit/test_printer_transport.py`
- `tests/unit/test_bambu_printer_adapter.py`
- `tests/integration/test_bambu_printer_lan.py`

Do not modify MCP source, errors, configuration, core types, package exports,
recommendation code, dependencies, or `plans/phase-2-printer-monitor-core.md`.
The integration test file may change only to keep direct transport construction
compatible with the typed configured-serial requirement and/or the separately
authorized explicit hardware gate. Build must not broaden or execute its
hardware behavior.

If implementation requires another production file, stop and revise/review
this plan rather than expanding scope during Build.

## File-by-File Build Steps

### `src/print_engineer/adapters/printer/transport.py`

- Add zero-argument `request_status_refresh()` to `MqttClient`.
- Extend client/factory construction with the configured serial.
- Keep report/request topic construction private and fixed.
- Share the existing report-queue/subscription bookkeeping so subscription
  precedes refresh and later `fetch_report()` does not resubscribe.
- Check the immediate Paho subscribe return code and require
  `MQTT_ERR_SUCCESS`; exceptions/non-success results cause zero publishes and
  no automatic retry.
- Own the module-private, process-local, per-serial five-minute cooldown and
  its short atomic check/reservation lock using monotonic time.
- Provide only a private deterministic clock/helper seam needed by hermetic
  tests; expose no public cooldown or bypass API.
- Serialize the exact payload with internal decimal sequence ID.
- Call the underlying Paho publish exactly once at QoS 0.
- Reject/suppress any second refresh on the client without publishing.
- Convert immediate publish exceptions/non-success results into the existing
  transport connection-error signal.
- Add no other MQTT write method or command abstraction.

### `src/print_engineer/adapters/printer/bambu.py`

- Pass the configured serial to the client factory.
- In only the implicit standalone lifecycle, connect and call
  `request_status_refresh()` once before fetching reports.
- Preserve the explicit retained path as passive-only.
- Reuse `_BambuStatusAccumulator` for all report application.
- Recognize a candidate full response only from `command="push_status"` and
  integer `msg == 0` (boolean values must not accidentally satisfy the marker).
- Use the existing one-total-deadline cold algorithm.
- Return immediately on a candidate full snapshot; otherwise return the best
  valid partial snapshot at the deadline.
- Preserve timeout, invalid-report, connection-error, and `finally` cleanup
  behavior.

### `tests/unit/test_printer_transport.py`

- Extend the fake Paho boundary to record subscriptions and publishes.
- Prove report subscription occurs before the one publish.
- Prove subscribe exceptions and non-success return codes cause zero publishes,
  do not reserve cooldown, and cannot reach publish.
- Assert one exact request topic derived from configured serial.
- Decode/assert the exact payload envelope and fixed fields.
- Assert `sequence_id` is an internally generated non-negative decimal string.
- Do not require one universal sequence starting value; prove callers cannot
  supply or alter it and use deterministic private injection/state if needed.
- Assert QoS is exactly 0.
- Assert a second refresh call causes no second publish.
- Assert publish exceptions and non-success Paho results map to the transport
  error without retry.
- Exercise the real production cooldown around multiple real
  `PahoMqttClient` wrappers backed by fake Paho clients: same serial is
  suppressed inside five minutes, another serial is independent, and the same
  serial is eligible after fake monotonic time advances five minutes.
- Use deterministic synchronization around the real private reservation helper
  to prove two same-serial clients produce exactly one total underlying publish
  without timing-sensitive sleeps.
- Prove subscription failure leaves the serial eligible, while an issued
  publish followed by later response failure leaves it in cooldown.
- Assert `MqttClient` has no `publish`, general request, arbitrary topic, or
  arbitrary payload operation.
- Replace the old literal no-`publish`/no-`request`/no-`pushall` source check
  with stronger assertions that identify exactly one authorized Paho publish
  call and exactly one fixed request/pushall construction path, while rejecting
  every other write surface.

### `tests/unit/test_bambu_printer_adapter.py`

- Extend the fake `MqttClient` only with zero-argument
  `request_status_refresh()` and lifecycle event recording; do not give it a
  general publish method.
- Prove standalone ordering: connect/subscription preparation/refresh before
  report consumption, then disconnect.
- Prove exactly one refresh per standalone `get_status()`.
- Make the fake narrow refresh capability return issued/suppressed outcomes and
  prove a suppressed standalone call still performs the passive cold read,
  returns truthful partial status, and fabricates no rate-limit error or cached
  full status.
- Prove connection failure before the refresh method is reached reserves no
  cooldown and publishes nothing.
- Combine adapter lifecycle assertions with the real transport cooldown tests
  above; do not duplicate or mock the cooldown algorithm itself as proof of its
  atomicity or expiry behavior.
- Prove candidate full snapshots pass through the real existing accumulator.
- Prove multiple partial reports still merge.
- Prove best partial status returns when no full marker arrives before the
  total deadline.
- Prove no valid report still raises `PrinterTimeout`.
- Prove full-marker detection requires both exact command and integer `msg=0`.
- Prove refresh failure maps to structured `PrinterUnreachable` and cleanup.
- Prove disconnect after success, refresh failure, timeout, malformed report,
  and normalization/report failure.
- Prove explicit retained cold and warm calls issue zero refreshes.
- Prove all unsupported operations issue zero refreshes and instantiate no
  clients, reserve no cooldown, and publish nothing.

### `tests/integration/test_bambu_printer_lan.py`

- Pass the configured serial through the existing direct
  `PahoMqttClientFactory` construction so the typed factory/client contract
  remains valid.
- If an explicit pushall hardware gate is added here, keep it default-disabled
  and subject to the separate authorization described below.
- Do not broaden, combine, or automatically execute existing hardware behavior
  during Build.

## Required Test and Safety Coverage

Transport tests must prove:

- exactly one underlying Paho publish;
- immediate successful report subscription precedes publish;
- subscribe exceptions/non-success results make publish unreachable, produce
  zero publishes, and do not consume cooldown eligibility;
- exact configured request topic;
- exact payload shape;
- `command == "pushall"`;
- `push_target == 1`;
- `version == 1`;
- internal decimal-string sequence ID;
- QoS 0;
- no caller-controlled topic or payload;
- no public/general `publish()`;
- no automatic publish retry.

Adapter tests must prove:

- subscription preparation precedes refresh;
- one refresh only on standalone status;
- candidate full and partial reports use the real accumulator;
- best partial fallback and truthful unknowns;
- timeout when no valid report arrives;
- structured refresh failure;
- cleanup on every path;
- retained/warm paths and unsupported operations refresh zero times;
- process-local cooldown is per serial, atomic under concurrency, based on
  monotonic time, and has no caller bypass;
- same-serial calls inside five minutes use passive fallback with zero refresh;
- different serials remain independent and expiry is tested without real
  sleeping;
- failures before publish do not consume cooldown, while failures after an
  issued publish do consume it.

Safety regression tests must prove:

- exactly one reachable request-topic path;
- exactly one reachable `pushall` path;
- no `pushing.start` or `pushing.stop`;
- no pause, resume, stop, start, temperature, G-code, upload, FTPS, camera,
  cloud, command dispatcher, or caller-supplied JSON path;
- old zero-write assertions are replaced with stricter capability-boundary
  assertions, not deleted or weakened.

All unit tests remain hermetic. They must not use a broker, LAN, internet,
credentials, or physical printer.

## Verification Commands

Use the project virtual environment.

Focused tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_printer_transport.py tests/unit/test_bambu_printer_adapter.py
```

Focused Ruff:

```powershell
.\.venv\Scripts\ruff.exe check src/print_engineer/adapters/printer/transport.py src/print_engineer/adapters/printer/bambu.py tests/unit/test_printer_transport.py tests/unit/test_bambu_printer_adapter.py
```

Scoped integration-file Ruff (no hardware execution):

```powershell
.\.venv\Scripts\ruff.exe check tests/integration/test_bambu_printer_lan.py
```

Focused Mypy:

```powershell
.\.venv\Scripts\python.exe -m mypy src/print_engineer/adapters/printer/transport.py src/print_engineer/adapters/printer/bambu.py tests/unit/test_printer_transport.py tests/unit/test_bambu_printer_adapter.py
```

Scoped integration-file Mypy (no hardware execution):

```powershell
.\.venv\Scripts\python.exe -m mypy tests/integration/test_bambu_printer_lan.py
```

Relevant printer regression suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_printer_transport.py tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
```

Full unit suite after focused verification:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/
```

Do not fix unrelated failures. The known unrelated ambiguous print-context
failure must be classified independently if it remains present.

## Hardware Verification Gate

Build and unit tests must not contact hardware. Independent Review must pass
before any physical test.

Real-A1 verification requires separate explicit authorization and a new,
default-disabled exact gate. The hardware test may perform exactly:

```text
1 informational pushall publish
```

It must:

1. verify credentials are present without printing them;
2. review the exact test/diff for any additional publish or control path;
3. establish one connection and subscribe before refresh;
4. send one fixed `pushall` only;
5. make no retry if rejected or timed out;
6. record only sanitized report field names, marker fields, timing, and
   normalized `PrinterStatus`;
7. verify whether the exact A1 emits `command="push_status"` with `msg=0`;
8. verify whether state, progress, targets, and AMS become available;
9. verify no printer state, target temperature, print operation, setting, or
   other observable behavior changes;
10. disconnect cleanly and confirm repository state is unchanged.

The report must state exactly:

```text
MQTT publishes: exactly 1 informational pushall
Printer-control publishes: 0
Printer commands: 0
```

Do not enable Developer Mode automatically. If the current A1 firmware rejects
the informational request, stop and record the incompatibility. Do not add
signing, another payload, another request, Developer Mode changes, or a retry.

## Acceptance Criteria

- The only newly reachable MQTT write is one fixed `pushing.pushall` from the
  eligible implicit standalone `get_status()` lifecycle.
- At most one same-serial informational refresh is issued per five-minute
  process-local cooldown; different configured serials are independent.
- The cooldown uses monotonic time, has an atomic same-serial reservation, and
  has no caller-controlled override or force-refresh bypass.
- Cooldown tests use deterministic time and synchronization without real
  sleeping.
- The request topic is exactly the configured printer serial's request topic.
- Topic, payload, QoS, command, sequence format, and control parameters are not
  caller-controlled.
- `MqttClient` exposes no general publish or arbitrary request capability.
- The immediate report subscription result is `MQTT_ERR_SUCCESS` before the
  fixed publish; subscription exceptions/non-success results cause zero
  publishes and no retry.
- At most one underlying publish occurs per eligible standalone status call.
- Calls suppressed by cooldown perform the existing passive cold read and do
  not fabricate an error or cached full snapshot.
- Pre-publish connection/subscription failure does not consume cooldown;
  timeout, malformed/no-full response, or cleanup failure after an issued
  publish does consume it and cannot trigger a second refresh.
- No retry or periodic/background refresh exists.
- Explicit retained cold/warm status remains passive-only.
- Candidate full detection requires exact `push_status` plus integer `msg=0`.
- Existing accumulator/normalization remains the single source of status
  semantics.
- Full responses return promptly; partial valid telemetry returns truthfully at
  the existing total deadline; no telemetry raises `PrinterTimeout`.
- Unknown/missing fields remain `UNKNOWN`/`None` and are never inferred.
- Structured errors and disconnect cleanup are preserved on every path.
- MCP source and response contracts are unchanged.
- The direct integration factory call remains compatible with the required
  configured-serial construction contract without broadening or executing
  hardware behavior during Build.
- Safety tests prove no other request topic, payload, command, or MQTT write is
  reachable.
- Focused pytest, Ruff, and Mypy checks pass; relevant regressions pass.
- No hardware claim is made until the separately authorized exact-A1 test.

## Explicitly Out of Scope

- general MQTT publish;
- arbitrary request messages or command dispatcher;
- printer start, stop, pause, or resume;
- temperatures, settings, calibration, motion, or AMS control;
- G-code;
- uploads or FTPS;
- camera;
- cloud MQTT or Bambu account login;
- command signing;
- Developer Mode changes;
- `pushing.start`, `pushing.stop`, repeated or periodic `pushall`;
- caller-controlled cooldown, force-refresh bypass, persistent rate state, or
  background cooldown timers;
- background workers, polling, monitor-core, or MCP lifecycle changes;
- persistence or print history;
- automatic slicing or printing;
- Phase 3B;
- modification of `plans/phase-2-printer-monitor-core.md`.

## Unresolved Assumptions and Blockers

The following remain unverified until the separately authorized hardware gate:

1. The exact physical A1 and current firmware accept the four-field fixed
   `pushall` payload in the user's current LAN mode.
2. The response uses `print.command="push_status"` with integer `msg=0` as the
   full-snapshot marker.
3. The full response contains the modeled fields missing from passive deltas,
   especially state, progress, targets, and AMS.
4. The informational request has no observable printer-state or persistent
   side effect on this firmware.
5. The request does not require Developer Mode or command signing.

These uncertainties do not authorize implementation changes outside this
plan. They require truthful failure behavior and a separately authorized
single-request hardware experiment after Build and independent Review.

Before approval, reviewers must explicitly accept the safety-boundary change
from zero publishes to at most one fixed informational publish. If that change
is not accepted, the correct decision is to keep passive-only behavior rather
than broaden the implementation.

## Implementation Order

1. Review and explicitly approve this proposed safety-boundary change.
2. Build only the five scoped files.
3. Run focused transport/adapter tests.
4. Run focused Ruff and Mypy.
5. Run printer regressions and the full unit suite as justified.
6. Independently review exact capability confinement, lifecycle, response
   semantics, and all changed tests.
7. Only with separate authorization, execute the one-request hardware gate.
8. Treat monitor-core or MCP lifecycle work as a separate future decision.

## Final Verdict

PROPOSED — READY FOR INDEPENDENT PLAN REVIEW

This document does not approve implementation and does not authorize hardware
access or MQTT publishing.

PLAN ONLY — no production or test files were modified.
