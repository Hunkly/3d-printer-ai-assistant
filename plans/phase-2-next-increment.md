# Phase 2 — Local Printer Issue Metadata Resolver v1

Status: APPROVED

## Current Verified State

The committed Phase 2 checkpoints are present in the current implementation:

- `PrinterStatus.issues` is an immutable tuple of raw, source-qualified
  `PrinterIssue` records. `BambuPrinterAdapter` accumulates valid Bambu HMS and
  `print_error` deltas losslessly, preserves sparse state correctly, and clears
  only on valid explicit clear values.
- `printer.status` serializes those records as an additive raw `issues` array.
  It continues to construct one adapter and call `get_status()` exactly once.
  Status summary and assessment intentionally do not interpret issue codes.
- State-aware summary v2 and assessment v1 are implemented and covered. They
  preserve structured telemetry, use no additional retrieval, and perform no
  MQTT work beyond the adapter call already required for `printer.status`.
- The read-only adapter can make one cold status observation, and an explicitly
  connected adapter can accumulate passive reports. The existing fixed-purpose
  `request_status_refresh()` remains the only outbound MQTT operation and is
  subject to its existing reservation/cooldown behavior.
- A long-lived MCP retained-status session is not safe to implement yet. The
  installed Paho 2.1.0 public lifecycle lacks a supported bounded fallback that
  guarantees socket closure after graceful disconnect cannot finish; current
  `loop_stop()` is unbounded and default reconnect behavior also needs explicit
  lifecycle treatment.
- `Settings` has no issue-metadata configuration and no existing resolver or
  metadata resource loader. The current test suite has hermetic transport,
  adapter, accumulator, MCP, interface, and configuration coverage; it makes
  no hardware connection in unit tests.

The worktree contains unrelated user changes and untracked plans. They are not
part of this increment and must be preserved.

## Problem / Missing Capability

The monitoring path truthfully exposes raw Bambu issue codes, but a local user
cannot yet resolve an already-observed raw HMS or `print_error` code against a
trusted, user-supplied metadata snapshot. Consequently, any later read-only
issue-information API would either duplicate validation/lookup logic, need an
unsafe runtime download, or invent meanings for unknown codes.

The repository already has an approved raw-issue contract and a completed
resource-contract research plan. What is missing is only the offline core that
loads strict project-format JSON snapshots and resolves a supplied raw issue
deterministically. It must not alter status retrieval or presentation.

## Exact Proposed Increment

Implement **Local Printer Issue Metadata Resolver v1**: a Bambu-specific,
offline loader and pure resolver for explicitly configured, user-supplied
normalized metadata JSON files.

1. Add `issue_metadata_paths: tuple[Path, ...] = ()` to `PrinterConfig` and
   rebase each relative path against `Settings.root`, following the existing
   storage/logging path behavior. This is exactly the configured
   `Settings.root` (the default project root or an explicit `root` override).
   It does not derive a base from the YAML configuration file's parent or the
   current working directory; an intentionally relative `root` retains the
   existing `Settings` path behavior. Absolute paths remain absolute. The field
   is optional; an empty tuple means metadata lookup is unavailable, not a
   configuration error. YAML supplies a list of paths, which Pydantic's
   existing `tuple[Path, ...]` conversion accepts without a custom validator.
   No environment variable, secret, default resource path, or automatic
   discovery is added.
2. Add one new internal module,
   `src/print_engineer/adapters/printer/issue_metadata.py`, containing:
   - immutable internal accepted-resource and accepted-resource-set values;
   - strict all-or-nothing decoding/validation of the project-owned schema v1
     specified by `phase-2-printer-issue-metadata-resource-contract-v1.md`;
   - a one-shot, stateless loader that receives the configured paths
     explicitly, reads them once, and returns either an immutable accepted set
     or a structured local validation failure without writing, downloading,
     caching, watching, or reloading. An empty configured tuple is a successful
     immutable empty set; a configured missing/non-regular file is a validation
     failure with no usable candidate set. Atomicity means no partial resource
     or set is returned on a failure; this increment has no retained loader
     state to replace or preserve;
   - pure device-family derivation from a supplied serial, HMS lookup-key
     derivation, and resource selection; and
   - a pure resolver accepting an existing `PrinterIssue`, supplied serial,
     requested canonical locale, `allow_english_fallback`, and an accepted set.
     It returns a frozen typed resolved/unresolved result. A resolved result
     has immutable metadata containing only the exact matched message and the
     resource/provenance context required by the resource contract; an
     unresolved result has no metadata and a private deterministic no-match
     category. Every result preserves the original source-qualified
     `PrinterIssue` unchanged, and resolution never raises for an
     unknown/malformed raw issue.
3. Support one or more configured files. Each accepted file represents exactly
   one `(vendor=bambu_lab, device_family, locale)` selection key. Duplicate
   selection keys reject the whole candidate set. Input path order must not
   alter successful lookup results.
4. Apply the resource-contract rules exactly: regular UTF-8 JSON files only;
   no BOM, duplicate object members, trailing data, excessive nesting, or
   schema extras; 5 MiB maximum; exact field types/keys; immutable indexed
   entries; source-separated keys; and all resource validation before the set
   becomes usable. Loader-assigned provenance is always `user_supplied`, with
   a lowercase SHA-256 of exact accepted bytes and a private absolute source
   reference.
   In particular, `entries` is required to contain 1--10,000 entries: an empty
   dataset is invalid and rejects its candidate resource.
5. Resolve exact locale first. Attempt `en` only when the caller explicitly
   requests English fallback and a separately validated English resource for
   the same family exists. Preserve raw issue source/code and report no match
   rather than fabricate text for unavailable resource, device, locale, key,
   or invalid input.

The resolver is core-only. It does not yet create a public MCP tool or change
the `printer.status` response.

## Why This Should Be Next

This is the smallest unblocked dependency that improves the usefulness of
already monitored printer faults. It builds directly on the completed raw issue
checkpoint and follows the resource-contract plan's explicit next-step
recommendation.

It is safer and narrower than retained monitoring: it needs no Paho lifecycle
change, background worker, adapter construction, connection, refresh, MQTT
operation, retry, or hardware access. The retained-status-session plan remains
properly blocked until bounded transport cleanup is resolved. Public MCP issue
lookup should follow only after this core has its own deterministic tests and
API review.

## Exact Production Files Expected to Change

- `src/print_engineer/config.py`
  - add/rebase the optional explicit `PrinterConfig.issue_metadata_paths`
    setting only.
- `src/print_engineer/adapters/printer/issue_metadata.py` (new)
  - contain the private schema validation, accepted resource/set types, loader,
    Bambu key/device-family helpers, and pure resolver.

Do not add an export from `adapters.printer.__init__`; this is not a public
adapter API. Do not alter `core.types`, `errors.py`, `bambu.py`, `transport.py`,
`mcp/tools/printer.py`, `mcp/server.py`, dependencies, or package metadata.

## Exact Tests Expected to Change/Add

- `tests/unit/test_config.py`
  - verify configuration omission/default empty paths, absolute-path retention,
    and relative-path rebasing against `Settings.root` rather than the YAML
    configuration file's parent or the process CWD.
- `tests/unit/test_printer_issue_metadata.py` (new)
  - use temporary synthetic JSON files only; no vendor dataset is checked in.
  - cover a valid empty configured set and valid single/multiple resource
    loads, immutable accepted values and returned resolved metadata,
    loader-assigned provenance/hash, and input-order-independent resolution.
  - cover atomic rejection for configured missing/non-regular paths, malformed
    JSON/UTF-8/BOM/duplicate members, invalid top-level shape/extra fields,
    invalid entries/entry extra fields, explicitly empty `entries`, duplicate
    entry keys, duplicate resource selection keys, and size/depth limits.
  - pin exact case-sensitive family derivation, exact locale selection, opt-in
    English fallback, and unresolved results for missing resource/family/locale
    /entry.
  - exercise a known matching issue and an unknown valid issue explicitly;
    assert that the former returns immutable matched metadata and the latter
    returns deterministic unresolved/no-metadata without altering the raw
    source-qualified issue.
  - pin HMS identity derivation and the required out-of-range-level conversion
    `0300123400050056 -> 0300123400000056`; pin direct `print_error` lookup and
    source isolation.
  - prove the pure resolver creates no `BambuPrinterAdapter`, calls no
    `get_status()`, uses no MQTT/network/time/environment access, and mutates
    neither input issues nor accepted resources.

No existing adapter, transport, accumulator, MCP, or integration test file is
expected to change.

## Behavior That Must Remain Unchanged

- `printer.status` input, success/error response shape, raw `issues` array,
  summary v2, assessment v1, one-adapter/one-`get_status()` invariant, and
  configuration precedence for host/serial/access code.
- `PrinterIssue`, `PrinterStatus`, raw HMS/`print_error` normalization,
  accumulator sparse-update semantics, issue ordering, and unknown-code
  preservation.
- The narrow fixed `request_status_refresh()` topic, payload, QoS, sequence,
  cooldown/reservation behavior, subscription behavior, and all transport
  error mappings.
- Existing bounded one-shot read-only status behavior and all unsupported
  printer-control operations.
- No automatic reconnect, retained session, monitor, worker, cache, file
  watcher, timer, background reload, persistence, or history behavior is
  introduced.

## Safety Constraints

- The resolver is offline, deterministic, read-only, and local-file-only.
- It must not construct an adapter; connect to a printer; subscribe, publish,
  refresh, or otherwise use MQTT; access Bambu cloud; log secrets; or perform
  hardware activity.
- Never bundle, copy, download, or automatically refresh Bambu metadata.
  Candidate metadata is user supplied and remains explicitly marked
  `user_supplied`; SHA-256 is identity evidence, not a claim of authenticity.
- Invalid candidates must not partially replace an accepted set, and no local
  source path may become public output in this increment.
- No severity, action, recommendation, health inference, code
  acknowledgement/clear command, printer control, or interpretation beyond an
  exact matched user-supplied message is permitted.
- Unit tests must remain hermetic and use only temporary synthetic files.

## Verification Commands

Before editing, Build must inspect:

```powershell
git status --short
```

Run the focused resolver/configuration checks:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_printer_issue_metadata.py tests/unit/test_config.py
.\.venv\Scripts\ruff.exe check src/print_engineer/config.py src/print_engineer/adapters/printer/issue_metadata.py tests/unit/test_config.py tests/unit/test_printer_issue_metadata.py
.\.venv\Scripts\python.exe -m mypy src/print_engineer/config.py src/print_engineer/adapters/printer/issue_metadata.py tests/unit/test_config.py tests/unit/test_printer_issue_metadata.py
```

Run only the directly relevant existing printer regressions to prove the
core-only boundary:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_interfaces.py tests/unit/test_bambu_printer_adapter.py tests/unit/test_printer_mcp.py
```

After focused success, inspect only task-relevant changes:

```powershell
git status --short
git diff --stat
git diff -- src/print_engineer/config.py src/print_engineer/adapters/printer/issue_metadata.py tests/unit/test_config.py tests/unit/test_printer_issue_metadata.py
```

Do not run hardware-gated integration tests or perform hardware/MQTT work.

## Explicit Out-of-Scope Items

- A public `printer.issue_info` MCP tool, any change to `printer.status`, and
  any metadata in existing status responses.
- Bundled Bambu metadata, Bambu Studio resource copying, remote endpoint use,
  background/runtime download, cache persistence, updates, or file watching.
- Metadata-based severity, summaries, assessments, remediation, images, URLs,
  action lists, categories, health scoring, diagnostics, or support claims.
- Bounded MQTT transport lifecycle, automatic-reconnect changes, retained
  status session, passive worker, FastMCP lifespan change, monitor, retry, or
  reconnect.
- Printer control, camera, FTPS, cloud login, device discovery, slicing,
  automatic printing, Phase 3 work, dependency changes, and hardware testing.

## Follow-Up Work After This Increment

1. Independently review/build a narrow public read-only issue-lookup API plan
   (likely `printer.issue_info`) that consumes this resolver without calling
   `printer.status`, constructing an adapter, or contacting the printer. That
   plan must pin public request/response and non-fatal unresolved reason names.
2. Keep automated/vendor metadata refresh blocked pending explicit endpoint
   authority, license/terms review, supported update semantics, and a
   platform-appropriate application-data decision.
3. Separately resolve the bounded Paho transport lifecycle decision before
   returning the retained-status-session plan for approval. Do not couple that
   work to issue metadata.

## Existing Plans Used

- `plans/phase-2-printer-issue-metadata-resource-contract-v1.md` — authoritative
  resource, validation, provenance, key-derivation, locale, and explicit
  next-step contract.
- `plans/phase-2-printer-issue-metadata-research-v1.md` — establishes why
  bundled/remote metadata and unsupported interpretation remain excluded.
- `plans/phase-2-printer-reported-issues-v1.md` — completed raw issue API that
  must remain unchanged.
- `plans/phase-2-state-aware-printer-summary-v2.md`,
  `plans/phase-2-printer-status-assessment-v1.md`, and
  `plans/phase-2-remaining-time-v1.md` — completed presentation/data contracts
  that issue lookup must not disturb.
- `plans/phase-2-bounded-mqtt-transport-lifecycle.md` and
  `plans/phase-2-retained-status-session-v1.md` — verified blockers/dependencies
  that make retained monitoring unsuitable as the next increment.

## Risks / Open Questions

- The user-facing configuration YAML name is fixed here as
  `printer.issue_metadata_paths`; the repository's installed Pydantic accepts
  the documented YAML-list-to-`tuple[Path, ...]` conversion without a custom
  validator.
- Resource-contract validation is intentionally strict and substantial. Its
  implementation must avoid a parallel generic metadata framework or a new
  public error hierarchy.
- The resource contract defines detailed loader failure categories but no
  public result/reason vocabulary. This core-only increment may use internal
  typed outcomes; it must not expose them through MCP until the follow-up API
  plan decides public names.
- User-supplied metadata may be incomplete, stale, inaccurate, or malicious.
  Exact matching plus provenance prevents fabricated certainty but does not
  authenticate content.
- No issue metadata sample is currently in the repository; temporary synthetic
  test records are sufficient and must not imply a vendor dataset is shipped.

## Acceptance Criteria

1. An empty configuration leaves all existing printer behavior untouched.
2. Explicit relative/absolute resource paths resolve deterministically against
   `Settings.root`.
3. Valid configured resources form one immutable, order-independent accepted
   set; any invalid candidate or duplicate selection key rejects the candidate
   set atomically.
4. The loader and resolver follow the v1 resource contract exactly, including
   schema, source namespace, UTF-8/JSON strictness, provenance, hash,
   family/locale selection, fallback, and Bambu key derivation.
5. Unknown/malformed issue inputs and unavailable data yield typed unresolved
   outcomes, never fabricated metadata or a printer-status failure.
6. No production path creates an adapter, calls `get_status()`, uses network/
   MQTT/hardware, writes files, downloads data, or changes a printer state.
7. The exact focused tests, Ruff, and Mypy commands pass, and task-relevant
   diffs are limited to the four scoped files.

## Final Verdict

PROPOSED — READY FOR INDEPENDENT PLAN REVIEW
