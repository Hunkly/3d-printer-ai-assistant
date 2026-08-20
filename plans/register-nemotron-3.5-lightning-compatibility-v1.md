# Register nvidia/nemotron-3.5-lightning:free Compatibility v1

Status: APPROVED — implementation is authorized and limited to the exact registry-only change defined below.

## Objective

Add exactly one manually authorized compatibility entry for
`nvidia/nemotron-3.5-lightning:free` to the existing production compatibility
registry at `tools/openrouter-free-selector/config/codex-compatible-free-models-v1.json`.
No other file is authorized to change.

## Evidence Basis

The successful manually authorized compatibility probe returned:

```text
model_id=nvidia/nemotron-3.5-lightning:free
codex_sdk_version=0.147.0
provider_id=openrouter
wire_api=responses
tool_loop_success=true
final_completion_success=true
worktree_unchanged=true
```

The linked worktree remained clean.

```text
validated_at=2026-08-20T17:32:10.368Z
valid_until=2026-09-19T17:32:10.368Z
```

`valid_until` is exactly `validated_at + 2,592,000 seconds` (30 days), the
exact validity period required by production code. The registry currently
contains exactly:

```json
{"schema_version":1,"entries":[]}
```

## Authorized Scope

Modify exactly one file:

- `tools/openrouter-free-selector/config/codex-compatible-free-models-v1.json`

No source changes. No test changes unless validation unexpectedly proves
necessary (if it does, BUILD must stop and report the conflict rather than
weaken tests). No package changes. No lockfile changes. No dependency changes.
No selector redesign. No probe changes. No provider changes.

## Registration Contract

1. Preserve `schema_version=1`.
2. Add exactly one entry.
3. Exact model: `nvidia/nemotron-3.5-lightning:free`
4. Exact SDK: `0.147.0`
5. Exact provider: `openrouter`
6. Exact wire API: `responses`
7. Exact validation timestamp: `2026-08-20T17:32:10.368Z`
8. Exact valid-until timestamp: `2026-09-19T17:32:10.368Z`
9. Do not register Gemma, GLM, or any other historical model.
10. Do not infer compatibility from historical rollouts.
11. No second Lightning probe.
12. No network/inference during PLAN/BUILD/REVIEW.
13. Registry must remain valid under the existing production `compatibility.ts`
    parser and validity rules.
14. No duplicate model IDs.
15. Exactly `$0.00` external inference spend remains required.

## Exact Target Registry Content

```json
{
  "schema_version": 1,
  "entries": [
    {
      "model_id": "nvidia/nemotron-3.5-lightning:free",
      "codex_sdk_version": "0.147.0",
      "provider_id": "openrouter",
      "wire_api": "responses",
      "validated_at": "2026-08-20T17:32:10.368Z",
      "valid_until": "2026-09-19T17:32:10.368Z"
    }
  ]
}
```

The entry must be accepted by the production `validSchema`/`loadRegistry`/
`requireCompatibility` logic in `tools/openrouter-free-selector/src/compatibility.ts`:
exact six-key schema, `model_id` matching `/^[^/,]+\/[^/,]+:free$/` and not
`openrouter/free` or `openrouter/auto`, `codex_sdk_version === "0.147.0"`,
`provider_id === "openrouter"`, `wire_api === "responses"`, exact
`Z`-suffixed millisecond timestamps with finite parse and ISO round-trip
equality, and `valid_until - validated_at === 2,592,000,000` milliseconds.
A synthetic `now` satisfying `validated_at <= now < valid_until` must validate
the entry; `now == valid_until` is expired.

## Future Build Verification

Inference-free validation using existing production code only. From
`tools/openrouter-free-selector`:

1. Run `npm.cmd run build` (the existing package workflow `tsc -p tsconfig.json`
   requires it before tests).
2. Run the existing selector tests (`npm.cmd test`), which build first and run
   the four existing hermetic test modules including `compatibility.test.js`.
3. Load the registry through production `loadRegistry(...)` from the compiled
   `dist` output with the exact config path
   `config/codex-compatible-free-models-v1.json`; it must return exactly one
   entry.
4. Call `requireCompatibility(...)` for `nvidia/nemotron-3.5-lightning:free`
   at a synthetic time inside the validity window (for example
   `new Date("2026-08-25T12:00:00.000Z")`); it must return the entry without
   throwing.
5. Verify exactly one registry entry.
6. Verify the exact six entry fields match the Exact Target Registry Content.
7. Verify `valid_until - validated_at` is exactly 2,592,000 seconds.
8. Verify no other model was added.
9. From the repository root run `git diff --check`.
10. Run `git diff --name-only`.
11. Run `git status --short`.

No live compatibility probe. No external network. No inference. No registry
publication outside Git. No push before independent review.