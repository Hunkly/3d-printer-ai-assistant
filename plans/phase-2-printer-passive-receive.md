# Phase 2 — Passive Multi-Report MQTT Receive

## Status

APPROVED

## Understanding

Make repeated passive reads reliable on one Bambu LAN MQTT connection while preserving the existing minimal `MqttClient` protocol:

`connect() → fetch_report(topic, timeout) → fetch_report(...) → disconnect()`

This increment corrects transport receive semantics only. It must not merge reports into a status snapshot, expand `PrinterStatus`, add telemetry fields, or introduce any publish or printer-control path.

## Real Hardware Evidence

Real A1 verification established that TLS, LAN access-code authentication, subscription to `device/{serial}/report`, normalization, cleanup, and the strict no-publish path work. Twenty independent reconnect/status runs succeeded.

Reports are observationally incremental: one report contained state, temperatures, targets, progress, and AMS, while later reports contained only a smaller set such as current temperatures, fan data, Wi-Fi signal, and metadata. Status completeness therefore currently depends on which single passive report arrives first.

A persistent-connection experiment received one report and then timed out on subsequent reads. Code inspection proves that this result is explained by the transport stopping its own network loop after the first fetch; it does not establish that the printer stopped emitting reports.

## Root Cause

`PahoMqttClient.connect()` starts Paho's network loop. Each current `fetch_report()` clears a single payload/event pair, subscribes, waits, then calls `loop_stop()` on both success and timeout. A later fetch clears state and subscribes again but never restarts the loop, so callbacks cannot process new network traffic.

The single `_payload` slot also overwrites an earlier unread report if several callbacks arrive before the caller fetches again. Current adapter tests use a canned fake client and do not exercise Paho loop, subscription, buffering, or repeated-read behavior.

## Existing Contract Conflict

`BambuPrinterAdapter.connect()` retains one client, and subsequent `get_status()` calls reuse it until `disconnect()`. The transport protocol likewise exposes a reusable `fetch_report()`. Stopping the loop inside the first fetch violates that connected-client lifecycle.

Standalone `adapter.get_status()` must retain its existing ownership:

`construct client → connect → fetch one report → normalize → disconnect in finally`

No adapter normalization or error contract change is required to correct the transport.

## Desired Transport Semantics

- `connect()` resets connection-scoped receive state, establishes MQTT, starts the network loop, and waits for CONNACK as today.
- A successful active `connect()` is the only operation that starts the loop.
- Calling `connect()` while this wrapper already owns an active connection is an idempotent no-op; it must not create a second loop or clear queued reports.
- Failed connection attempts stop the loop, leave the wrapper disconnected, and preserve the existing `MqttConnectionError("auth" | "unreachable")` behavior.
- `fetch_report(topic, timeout_seconds)` ensures the report topic is subscribed, then returns the oldest queued payload for that topic.
- A fetch waits for a new queued payload when none is available and returns `None` at timeout.
- Timeout does not stop the loop, remove the subscription, or make a later fetch unusable.
- `fetch_report()` never returns a payload already returned by an earlier fetch.
- `fetch_report()` does not start or stop the network loop.
- `disconnect()` owns final MQTT/loop cleanup, is idempotent, and clears subscriptions and buffered receive state even if underlying disconnect cleanup raises.
- Preserve the current safe underlying cleanup order: request MQTT disconnect, then guarantee `loop_stop()` in `finally`; afterward mark disconnected and clear connection-scoped state.
- Reconnecting the same wrapper begins with no payloads or subscription markers from the prior connection.

The public `MqttClient` interface remains unchanged. A new `collect_reports()` API is unnecessary because repeated ordered `fetch_report()` calls can express bounded passive collection without expanding public surface area.

## Message Buffering Design

Replace the single `_payload` and `_report_received` pair with standard-library, thread-safe, per-topic FIFO queues. The callback routes `message.payload` using `message.topic`; `fetch_report(topic, timeout)` consumes only that topic's queue.

Create a topic queue before calling `subscribe()` so a fast callback cannot race queue creation. Preserve FIFO order and remove each returned payload exactly once. Define the private constant `_REPORT_QUEUE_CAPACITY = 32`; every per-topic FIFO queue has capacity 32. This is a modest fixed bound intended to absorb short passive telemetry bursts while preventing unbounded memory growth.

When a topic queue is full, deterministically drop the oldest queued report, retain the newest incoming report, and increment the private dropped-report counter. Never block Paho's network callback thread and never log payload contents. Unit tests must reference and pin `_REPORT_QUEUE_CAPACITY` when proving capacity and overflow behavior. Normal bursts within capacity must not overwrite or lose reports. Overflow observability beyond this internal diagnostic is out of scope because adding a new public error or result type would expand the transport contract; any evidence that the chosen capacity is inadequate requires a separate plan.

All queues, counters, and topic-subscription state are connection-scoped and cleared on a fresh connection and on disconnect. No payload is persisted or logged.

## Subscription Lifecycle

Track subscribed topics in a private set for the active connection.

- First fetch for a topic creates its queue and calls `subscribe(topic, qos=0)` once.
- Later fetches for the same topic reuse the subscription and queue without another subscribe call.
- Different topics may be tracked independently without returning one topic's payload to another caller, although the production adapter continues to use only `device/{serial}/report`.
- A timeout retains the active subscription.
- Disconnect clears the subscription set locally; explicit MQTT unsubscribe is unnecessary because the MQTT connection is closing.
- Reconnect starts with an empty subscription set, so the first post-reconnect fetch subscribes again.

Keep the existing subscription result handling unless a focused test demonstrates an existing contradiction; subscription acknowledgement/error redesign is not required for this receive-loop correction.

## Required Production Changes

Modify only:

- `src/print_engineer/adapters/printer/transport.py`

Changes are limited to connection-state tracking, loop ownership, per-topic bounded FIFO buffering, subscribe-once state, idempotent cleanup, and state reset. Preserve TLS, credentials, client ID, callback API version, factory behavior, exception contract, and the three-method `MqttClient` protocol.

No production change is planned for:

- `src/print_engineer/adapters/printer/bambu.py`
- MCP tools or registration
- models, errors, normalization, or configuration

If implementation proves an adapter production change is necessary, stop and return the plan for revision rather than expanding scope.

## Required Test Changes

Create a focused hermetic transport test module, preferably:

- `tests/unit/test_printer_transport.py`

Use a fake Paho client at the existing `mqtt.Client` boundary. It must simulate callbacks and record `connect`, `subscribe`, `loop_start`, `loop_stop`, and `disconnect` calls without a broker, LAN, internet, or physical printer.

Prove:

- connect starts the Paho loop exactly once
- repeated connect while active is safe and does not start another loop
- first fetch returns the first new payload
- successful fetch does not stop the loop
- a second fetch on the same connection returns a second new payload
- multiple reports arriving before subsequent fetches are retained and returned in order
- different topic queues do not cross-deliver payloads
- timeout returns `None`
- timeout does not stop the loop, remove the subscription, or prevent a later successful fetch
- a returned payload is not returned again
- one topic is subscribed only once per connection
- disconnect requests MQTT disconnect and stops the loop
- repeated disconnect is safe and does not repeat underlying cleanup
- reconnect clears stale payloads, dropped-count state, and subscriptions, then subscribes afresh
- bounded overflow follows the documented drop-oldest policy without blocking or logging payload data
- connection failures retain current classification and clean up the loop
- neither `MqttClient`, the fake Paho boundary, nor production transport exposes `publish()`
- no `/request` or `pushall` path exists

Do not weaken the existing adapter tests or make them depend on timing or real threads.

## Adapter Regression Coverage

Update `tests/unit/test_bambu_printer_adapter.py` only to add retained-client regression coverage. Extend its fake with an ordered payload sequence as needed, without modeling or bypassing production normalization.

Prove:

- `adapter.connect()` creates and connects one client
- two or more subsequent `adapter.get_status()` calls use that same client and normalize distinct queued reports in order
- neither retained read disconnects the client
- final `adapter.disconnect()` disconnects once
- standalone `adapter.get_status()` still constructs, connects, fetches, normalizes, and disconnects on success and existing failure paths

No status merging or normalization changes belong in these tests.

## Read-Only Safety Verification

The implementation must retain **ZERO MQTT PUBLISH PATHS**.

Review the complete relevant diff and search production plus fakes for `publish`, `/request`, `pushall`, and state-changing commands. Text in safety assertions or documentation is not itself a violation, but any reachable write path blocks approval.

The only intended outbound MQTT application operation is `subscribe`. Do not add unsubscribe unless implementation evidence makes it necessary; connection teardown is sufficient for this increment.

## Post-Implementation Hardware Experiment

After Build and independent Review are complete, prepare a separate explicitly opted-in integration check in `tests/integration/test_bambu_printer_lan.py`. It must remain skipped by default and require a dedicated exact opt-in such as `RUN_BAMBU_LAN_PASSIVE_RECEIVE_TEST=1` plus the existing three `BAMBU_*` variables. The gate must run before credentials are read or a transport is constructed.

The check must use the real production `PahoMqttClientFactory`/`PahoMqttClient`, establish one connection, subscribe passively to `device/{serial}/report`, and call `fetch_report()` repeatedly for a total bounded window of approximately 10–15 seconds. It must always disconnect in `finally`.

Parse received JSON only in memory. Display only:

- sanitized report ordinal and relative arrival timing
- sorted field names under the `print` object for each report
- the sorted union of observed `print` field names
- report count and timeout count

Never display or store values, raw payloads, IP, serial, access code, identifiers, filenames, or complete exceptions. The test must not normalize or merge reports because its purpose is to observe passive transport delivery and field presence. Malformed input should fail with a sanitized classification.

The experiment answers whether the A1 emits multiple reports over one subscription, their approximate cadence, whether they are sparse, whether their field-name union is materially more complete, and whether later passive aggregation appears viable. Its result must not trigger implementation changes automatically. Status merging, if justified, requires a separate `PLAN → APPROVE → BUILD → REVIEW` increment.

Do not run this hardware experiment during Build or ordinary automated verification.

## Test Commands

Use the project virtual environment. During Build, with all hardware opt-ins absent, run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_printer_transport.py tests/unit/test_bambu_printer_adapter.py
.\.venv\Scripts\python.exe -m pytest -q -rs tests/integration/test_bambu_printer_lan.py
.\.venv\Scripts\ruff.exe check src/print_engineer/adapters/printer/transport.py tests/unit/test_printer_transport.py tests/unit/test_bambu_printer_adapter.py tests/integration/test_bambu_printer_lan.py
.\.venv\Scripts\python.exe -m mypy src/print_engineer/adapters/printer/transport.py tests/unit/test_printer_transport.py tests/unit/test_bambu_printer_adapter.py tests/integration/test_bambu_printer_lan.py
```

Run the existing printer MCP tests as a regression when the focused transport/adapter suite is green:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_printer_mcp.py
```

The later hardware command must be defined and authorized during review of the gated test. It must use `.venv`, the dedicated exact opt-in, one connection, and the bounded 10–15 second window. Build must not execute it.

## Risks

- Callback and fetch threads can race during queue creation, consumption, overflow, disconnect, or reconnect; tests must exercise deterministic boundaries rather than rely on sleeps.
- A bounded queue necessarily needs an overflow policy. Drop-oldest favors current telemetry but means a sufficiently slow consumer cannot reconstruct every report; the private counter must make this testable without exposing payloads.
- Keeping the network loop active until disconnect makes correct caller cleanup more important; existing adapter `finally` behavior and idempotent transport disconnect must remain intact.
- Passive message cadence is controlled by printer firmware. Correct transport behavior cannot guarantee multiple reports in a particular interval.
- Sparse reports remain sparse. This increment enables observation but does not make a single `PrinterStatus` complete.
- Paho reconnect behavior can retain internal state independently; wrapper queues and subscription markers must nevertheless reset deterministically.
- The hardware diagnostic must avoid accidental identifier/value disclosure when parsing real reports.

## Out of Scope

- `PrinterStatus` expansion
- new normalized telemetry fields
- multi-report status merging or cached status snapshots
- changes to normalization or MCP response fields
- `publish()` or any MQTT write API
- `device/{serial}/request`
- `pushall`
- printer start, stop, pause, resume, temperatures, settings, or other control
- file upload, FTPS, camera, cloud MQTT, account authentication, command signing, slicing, or automatic printing
- automatic implementation changes based on the later hardware experiment

## Implementation Order

1. Review and approve this plan.
2. Record the expected changed-file set and inspect current working-tree state.
3. Add hermetic Paho-boundary tests that reproduce the stopped-loop failure and define queue/subscription semantics.
4. Update `transport.py` to move loop shutdown to disconnect/failure cleanup and implement bounded per-topic FIFO queues.
5. Add adapter retained-client regression coverage without changing adapter production code.
6. Add the separately gated, sanitized passive hardware experiment without running it.
7. Run focused unit tests, the disabled integration skip, Ruff, and Mypy.
8. Audit the diff and explicit no-publish/request/pushall searches.
9. Independently review the software and gated hardware-test safety.
10. Only after explicit authorization, run the bounded real-hardware experiment once and record sanitized findings.
11. Create a separate proposed plan if report merging or another behavior change is justified.

## Acceptance Criteria

- Existing `MqttClient` public methods and signatures remain unchanged.
- Connect starts one network loop; fetch never stops it; disconnect owns loop and MQTT cleanup.
- Repeated fetches on one connection return distinct passive reports in FIFO order.
- Fast arrivals within capacity are retained rather than overwritten.
- Buffer memory is bounded with the documented, tested drop-oldest policy.
- Fetch timeout returns `None` without disabling later reads.
- Subscription occurs once per topic per connection and resets on reconnect.
- Disconnect and repeated disconnect are safe; reconnect has clean receive state.
- Retained `BambuPrinterAdapter` reads work repeatedly on one client.
- Standalone adapter lifecycle and normalization remain unchanged.
- All focused hermetic tests, Ruff, and Mypy pass.
- The integration experiment skips unless separately and explicitly enabled.
- Production and test transports expose no publish method or reachable write path.
- No request topic, `pushall`, printer control, raw telemetry persistence, credential exposure, or hardware connection occurs during Build.

## Final Verdict

TRANSPORT CHANGES REQUIRED

PLAN ONLY — no source or test files were modified and no hardware connection was performed.

## Hardware Verification Result

**RESULT: PASS**

- Persistent passive receive works on a real Bambu Lab A1: six reports arrived during the approved approximately 12-second experiment, and a later manual observation received eight reports over approximately 19 seconds.
- While actively printing, reports arrived approximately every two seconds. Repeated `fetch_report()` calls worked on the same persistent connection, and connection cleanup completed normally.
- Reports contained demonstrably sparse and incremental field subsets rather than a complete snapshot every time. Observed safe field names included `bed_temper`, `nozzle_temper`, `nozzle_target_temper`, `layer_num`, `mc_percent`, `mc_remaining_time`, `wifi_signal`, `cooling_fan_speed`, `big_fan1_speed`, `big_fan2_speed`, `fan_gear`, `mc_print_sub_stage`, and `stg_cur`.
- One observed print showed 24% progress, layer advancement from 59 to 60 to 61, bed temperature around 65 C, nozzle temperature around 220 C, and Wi-Fi signal around -46 to -48 dBm. The unit of `mc_remaining_time` remains unverified.
- The transport FIFO and persistent-connection lifecycle worked against real hardware without MQTT publishing, `device/{serial}/request`, `pushall`, or any printer-control command.
- No printer IP, serial, access code, raw MQTT payload, filename, or identifier was recorded.
- This result does not implement status aggregation. Completeness of a cold-start status snapshot remains unresolved, and a passive state accumulator is justified as a separate future increment.
