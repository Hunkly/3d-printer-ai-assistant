# Phase 2 — Printer Issue Info MCP API v1

Status: APPROVED

## Current Verified State

- Commit `08eb66b` completed the local issue metadata resolver increment.
- `PrinterIssue` is an immutable source-qualified value with `hms` and
  `print_error` sources.
- `load_issue_metadata()` reads only explicitly configured absolute local JSON
  resources and returns an immutable accepted set or a structured load failure.
- `resolve_issue_metadata()` is pure and accepts an existing issue, configured
  serial, canonical locale, fallback permission, and accepted resources. It
  performs exact family/source/locale/key resolution and returns either exact
  matched metadata or an unresolved `no_match` result.
- `Settings` already provides `printer.issue_metadata_paths`; serial precedence
  is `settings.secrets.serial` followed by `settings.printer.serial`.
- `printer.status` currently constructs one adapter and calls `get_status()`;
  its raw issue array, summary, assessment, and remaining-time behavior are
  covered by existing MCP tests and must remain unchanged.
- The worktree contains unrelated user changes and untracked plans. They are
  preserved and are not part of this increment.

## Problem

The completed resolver is not publicly reachable. Clients can receive raw
issue identity from `printer.status`, but cannot explicitly resolve one known
issue against configured local metadata without either duplicating resolver
logic or incorrectly calling `printer.status` again.

Add one narrow read-only MCP tool, `printer.issue_info`, that resolves the
caller-supplied raw issue directly from local configuration. The tool is not a
status convenience wrapper and must never obtain an issue from the printer.

## Exact Public Request Contract

Tool name: `printer.issue_info`.

The request is a JSON object with exactly these fields:

```json
{
  "source": "hms",
  "code": "0300123400020056",
  "locale": "en",
  "allow_english_fallback": false
}
```

- `source`: required string, case-sensitive and exactly `hms` or `print_error`.
- `code`: required string, preserved exactly in the echoed issue. For `hms`,
  it must match `[0-9A-F]{16}` exactly; lowercase, whitespace, `0x` prefixes,
  wrong length, and non-hex characters are invalid. For `print_error`, it must
  match `[0-9A-F]{8}` exactly and parse to `1..0x7FFFFFFF`; lowercase,
  whitespace, `0x` prefixes, wrong length, non-hex characters, zero, and
  values above `0x7FFFFFFF` are invalid. The existing resolver then performs
  the approved HMS lookup-key normalization and source-specific lookup.
- `locale`: required canonical locale string matching the resolver contract,
  `^[a-z]{2}(?:-[A-Z]{2})?$`. No implicit locale or environment locale is
  selected.
- `allow_english_fallback`: required boolean. English fallback is attempted
  only when this is `true`, and only for the same derived device family.

The tool constructs `PrinterIssue(PrinterIssueSource(source), code)` and
derives the device family from the configured serial only. Serial precedence is
`settings.secrets.serial`, then `settings.printer.serial`. The request does
not accept a serial, device family, metadata path, host, access code, or
printer identifier; those are configuration-owned inputs. A host and access
code are not required for this tool.

FastMCP validates the callable schema before the tool body executes. Missing
required arguments, wrong primitive argument types, unexpected arguments, and
other schema rejections use normal FastMCP/MCP validation behavior; they do
not use a project-owned envelope. Do not add a wrapper, catch-all interceptor,
`**kwargs` request object, or parallel validation framework.

After FastMCP accepts the argument shape and primitive types, semantic value
validation is owned by `printer.issue_info`. Unsupported source, invalid
source-specific code, or invalid locale returns exactly:

```json
{
  "ok": false,
  "error": {
    "code": "issue_info_invalid_request",
    "message": "Invalid printer issue lookup request.",
    "details": {
      "field": "<field>",
      "reason": "<stable_reason>"
    }
  }
}
```

The only allowed field/reason pairs are `source`/`unsupported_source`,
`code`/`invalid_hms_code`, `code`/`invalid_print_error_code`, and
`locale`/`invalid_locale`. Do not invoke the resolver with fabricated values.
Validate in that order—source first, then source-specific code, then locale—so
the selected error is deterministic when multiple values are invalid. A
syntactically valid but unknown code remains a normal unresolved lookup.

## Exact Public Response Contract

For every valid request whose configured resources load successfully, return a
normal read-only result with this shape:

```json
{
  "ok": true,
  "issue": {
    "source": "hms",
    "code": "0300123400020056"
  },
  "resolved": true,
  "metadata": {
    "message": "Vendor-provided explanatory text",
    "locale": "en",
    "vendor": "bambu_lab",
    "vendor_dataset_version": "202608141853",
    "resource_schema_version": 1,
    "provenance_origin": "user_supplied",
    "content_sha256": "<64 lowercase hex characters>"
  }
}
```

The unresolved shape is identical except for `resolved: false`,
`metadata: null`, and `reason: "no_match"`:

```json
{
  "ok": true,
  "issue": {"source": "hms", "code": "0300123400020056"},
  "resolved": false,
  "metadata": null,
  "reason": "no_match"
}
```

The public response may expose only fields present in
`ResolvedIssueMetadata`: exact message, actual selected locale, vendor,
vendor dataset version, schema version, origin, and content hash. It must not
expose `IssueMetadataResource.source_reference`, absolute paths, exception
text containing paths, serials, hostnames, access codes, or derived lookup
keys not returned by the completed resolver contract. The raw source/code
identity is always echoed unchanged.

Every successful resolved response has exactly `ok`, `issue`, `resolved`, and
`metadata`; `metadata` is non-null and contains exactly the seven fields shown
above. Every successful unresolved response has exactly `ok`, `issue`,
`resolved`, `metadata`, and `reason`; `metadata` is `null` and `reason` is
exactly `"no_match"`.

## Unresolved Reason Contract

Normal lookup absence is deliberately one public reason, `no_match`, matching
the completed resolver's private deterministic reason. This avoids inventing a
second classification layer in MCP. It covers:

- metadata not configured;
- missing device serial or unknown device family;
- unsupported/unavailable requested locale;
- English fallback disabled or unavailable;
- unknown source-specific code or absent metadata entry; and
- source isolation where the code exists only under the other source.

All such cases return `ok: true`, preserve the supplied issue, and set
`metadata` to `null`. They are not printer failures and do not call
`printer.status`.

Malformed semantic request values are not unresolved lookups: they use the
exact `issue_info_invalid_request` error described above.

## Configuration / Resource-Error Behavior

The tool calls `load_issue_metadata(settings.printer.issue_metadata_paths)` once
per invocation, with no cache, watcher, refresh, mutation, or background work.

- An empty configured path tuple is a successful empty accepted set and yields
  the normal unresolved `no_match` response.
- A configured missing, non-regular, unreadable, malformed, oversized,
  invalid-UTF-8, schema-invalid, duplicate-member, duplicate-entry,
  duplicate-resource, depth-invalid, or otherwise loader-rejected resource is
  a configuration/resource failure, not a normal no-match. Return exactly:

  ```json
  {
    "ok": false,
    "error": {
      "code": "issue_info_metadata_invalid",
      "message": "Configured printer issue metadata is invalid.",
      "details": {}
    }
  }
  ```

  Use this identical envelope for every `IssueMetadataLoadResult` failure.
  Never serialize `IssueMetadataLoadFailure.path`, filenames, raw failure
  messages, exception types, resource contents, serials, credentials, or any
  other private detail. No partial resource set is used.

No metadata resource failure changes `printer.status` behavior or becomes a
printer transport/authentication/timeout error.

## Exact Production Scope

Modify only:

- `src/print_engineer/mcp/tools/printer.py`
  - add a `PrinterTools.issue_info(...)` implementation;
  - add private request validation and response serialization;
  - resolve serial without `_connection_params()`;
  - load configured resources and call the existing loader/resolver;
  - add `printer.issue_info` to the existing `build_tools()` map.
- `src/print_engineer/mcp/server.py`
  - add only the tool description entry if registration requires no other
    change (registration already iterates `build_tools()`).

No resolver, configuration, core type, adapter, transport, error hierarchy,
dependency, or package metadata changes are expected. If the existing MCP
error envelope cannot safely represent the two planned request/resource error
codes, stop and report the contract conflict before implementation rather than
redesigning shared errors.

## Exact Test Scope

Prefer extending `tests/unit/test_printer_mcp.py`; add a separate focused MCP
test module only if its existing fixture structure cannot remain clear. Use
temporary synthetic schema-v1 JSON resources only. Do not use hardware, live
MQTT, network, downloads, or a bundled vendor dataset.

Tests must prove:

- the server registers `printer.issue_info` with exactly four required schema
  fields. Using the actual registered MCP tool where practical, missing
  required arguments, wrong primitive argument types, and unexpected extra
  arguments are rejected by the normal FastMCP/MCP validation behavior; these
  tests must not expect `issue_info_invalid_request`;
- a resolved HMS issue returns the original identity and all approved metadata
  fields, without a private path;
- a resolved `print_error` issue uses direct source-specific lookup;
- unknown code, source isolation, missing family/serial, unavailable locale,
  disabled fallback, and absent metadata all return `ok: true`,
  `resolved: false`, `metadata: null`, `reason: "no_match"`;
- exact locale wins and approved English fallback returns the actual `en`
  locale only when enabled;
- unsupported source returns exactly the `issue_info_invalid_request` envelope
  with `details={"field": "source", "reason": "unsupported_source"}`;
- lowercase, wrong-length, and non-hex HMS codes return exactly the
  `issue_info_invalid_request` envelope with
  `details={"field": "code", "reason": "invalid_hms_code"}`;
- lowercase, wrong-length, zero, and greater-than-`0x7FFFFFFF` print-error
  codes return exactly the `issue_info_invalid_request` envelope with
  `details={"field": "code", "reason": "invalid_print_error_code"}`;
- an 8-character uppercase print-error code containing a non-hex character
  (for example, `12AB34G6`) returns exactly the same
  `issue_info_invalid_request` envelope with
  `details={"field": "code", "reason": "invalid_print_error_code"}`;
- malformed locale returns exactly the `issue_info_invalid_request` envelope
  with `details={"field": "locale", "reason": "invalid_locale"}`;
- missing configured files, non-regular resources, and malformed resources
  return the identical exact `issue_info_metadata_invalid` envelope with
  `details={}`, and the serialized response contains no path, filename,
  internal message, exception type, or resource content;
- unconfigured metadata returns the normal `ok: true`, unresolved
  `no_match` response rather than an error;
- missing/unmapped serial and unsupported/unmatched family follow the same
  normal `no_match` contract;
- serial precedence and exact case-preserved family handling are honored;
- the tool never constructs `BambuPrinterAdapter`, calls `get_status()`, calls
  status refresh, publishes/subscribes MQTT, opens a network connection, or
  accesses hardware;
- `printer.status` remains unchanged, including adapter/get-status call counts,
  raw issue serialization, summaries, assessment, and remaining time;
- returned payloads survive JSON round-trip and are deterministic.

Use monkeypatch spies/failing sentinels at the MCP tool module boundary to
prove the negative printer/network guarantees. The test setup must not require
host, access code, or a reachable printer for `issue_info`.

## Safety Constraints

`printer.issue_info` is local-only, read-only, synchronous, and stateless.
It must perform:

- no printer connection or adapter construction;
- no `get_status()`, status refresh, MQTT, cloud, network, or hardware access;
- no metadata download, write, mutation, cache, watcher, or background task;
- no call to `printer.status`, directly or indirectly;
- no exposure of private paths, credentials, serials, or transport details.

The existing `printer.status`, raw `PrinterIssue` reporting, summaries,
assessment, remaining-time behavior, transport lifecycle, retained sessions,
and all printer-control boundaries remain unchanged.

## Verification Commands

After implementation, run only focused checks:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning tests/unit/test_printer_mcp.py tests/unit/test_mcp_server.py
.\.venv\Scripts\ruff.exe check src/print_engineer/mcp/tools/printer.py src/print_engineer/mcp/server.py tests/unit/test_printer_mcp.py tests/unit/test_mcp_server.py
.\.venv\Scripts\python.exe -m mypy src/print_engineer/mcp/tools/printer.py src/print_engineer/mcp/server.py tests/unit/test_printer_mcp.py tests/unit/test_mcp_server.py
git status --short
git diff --stat
git diff -- src/print_engineer/mcp/tools/printer.py src/print_engineer/mcp/server.py tests/unit/test_printer_mcp.py tests/unit/test_mcp_server.py
```

Do not run hardware-gated integration tests or perform hardware/MQTT/network
operations.

## Out of Scope

- enriching `printer.status` or changing raw issue serialization;
- obtaining issues from status, retained sessions, monitoring, or caches;
- any printer control, MQTT publish API, refresh behavior, camera, FTPS,
  cloud login, discovery, or hardware verification;
- bundled, downloaded, refreshed, persisted, or automatically updated metadata;
- metadata severity, remediation, summaries, assessment, recommendations,
  categories, health scoring, actions, images, URLs, or LLM explanations;
- new configuration fields or a second metadata path;
- granular public unresolved reason taxonomy beyond `no_match`;
- changes to the completed resolver or resource schema.

## Follow-up Work

- Independently review the implementation against this plan, including the
  no-adapter/no-network invariant and private-path redaction.
- Consider a separate plan for a richer unresolved-reason taxonomy only if the
  resolver contract is deliberately extended rather than classified in MCP.
- Keep metadata acquisition/refresh and retained MQTT lifecycle as separate
  Phase 2 decisions.

## Open Questions

None blocking for this proposed increment. The plan intentionally resolves the
serial question by using existing configuration only, and resolves the public
reason question by preserving the resolver's single `no_match` outcome.

## Operations

- source modified: NO
- tests modified: NO
- hardware/MQTT/network: NO
- stage: NO
- commit: NO
- push: NO

## Final Verdict

PROPOSED — ready for independent plan review.
