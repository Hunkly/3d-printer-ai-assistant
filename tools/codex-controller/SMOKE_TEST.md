# Codex Controller Smoke Test

From `tools/codex-controller`, use these non-destructive checks. Invalid environment values are parsed before task validation, worktree creation, or SDK execution, so these rejection checks spend no Codex tokens.

```powershell
npm run build
npm test

Remove-Item Env:CODEX_PROVIDER_MODE -ErrorAction SilentlyContinue
npm start -- --provider-status
# Expected: provider_mode=auto and safe machine-readable status fields; no inference.

$env:CODEX_PROVIDER_MODE = "invalid"
npm start -- --provider-status
# Expected: INVALID_PROVIDER_MODE before execution.

$env:CODEX_PROVIDER_MODE = "openrouter-free"
Remove-Item Env:OPENROUTER_API_KEY -ErrorAction SilentlyContinue
npm start -- --provider-status
# Expected: safe status fields followed by OPENROUTER_AUTH_MISSING; no metadata or inference request.

$env:CODEX_PHASE = "invalid"
Remove-Item Env:CODEX_THREAD_MODE -ErrorAction SilentlyContinue
npm start
# Expected: CODEX_PHASE must be one of: general, plan, build, review.

$env:CODEX_PHASE = "general"
$env:CODEX_THREAD_MODE = "invalid"
npm start
# Expected: CODEX_THREAD_MODE must be one of: resume, fresh.
```

Documentation/default audit:

- `CODEX_PHASE=review` with no `CODEX_THREAD_MODE` selects `fresh` and does not replace the persisted task thread.
- `general`, `plan`, and `build` with no `CODEX_THREAD_MODE` select `resume`.
- Explicit `resume` and `fresh` override eligible non-review defaults; REVIEW/approval rejects `resume`.

Clear the smoke-test variables afterward:

```powershell
Remove-Item Env:CODEX_PHASE,Env:CODEX_THREAD_MODE -ErrorAction SilentlyContinue
```

## Fail-Stop Live Smoke Harness

The single active live-smoke procedure is the fail-stop harness
`tools/codex-controller/smoke/run-smoke.ps1`. It supersedes any
pasted interactive PowerShell sequences (including the historical
interactive `LIVE_SMOKE_RESULT=PASS` from
`plans/auto-provider-fallback-live-smoke-v1.md`, which is INVALID
evidence and does not change the consumed one-shot FAIL).

The harness is built from
`plans/fail-stop-live-smoke-harness-v1.md`. PLAN approval did NOT
authorize a smoke. BUILD completion did NOT authorize a smoke.
REVIEW PASS did NOT authorize a smoke. READINESS did NOT authorize
a smoke. Only a separate explicit one-shot authorization artifact
written by an authorized supervisor (see Section 6 of the plan)
authorizes a smoke.

### Hermetic verification

```powershell
cd tools/codex-controller
npm.cmd run build
npm.cmd test
```

This runs both the existing unit-test suites and the two new smoke
suites (`dist/smoke/smoke-control.test.js` and
`dist/smoke/smoke-harness.test.js`). The harness tests run the .ps1
in `-DryRun` mode against temp git repos + linked temp worktrees.
No real OpenRouter or Codex inference is performed. No live
authorization is consumed. The smoke is not executed.

### Future live smoke (not part of automated verification)

A future live smoke will be invoked exactly as:

```powershell
powershell -NoProfile -File tools\codex-controller\smoke\run-smoke.ps1
```

It requires an explicit one-shot authorization at
`%LOCALAPPDATA%\print-engineer-codex\smoke\authorization`, written
by an authorized supervisor, bound to the exact task key, worktree,
repository, purpose, and validity window. Until that artifact exists
the harness returns `SMOKE_RESULT=FAIL` with
`failure=SMOKE_NOT_AUTHORIZED` and never launches a production
child. The harness must never be invoked interactively without the
authorization artifact, and must never be invoked with a previously
consumed authorization.

### Historical context (preserved)

The earlier one-shot smoke is recorded exactly as:

```text
previous one-shot consumed: YES
previous result: FAIL
auto fallback gate: PASS
failure: CODEX_COMPATIBILITY_UNKNOWN
worktree unchanged: YES
readonly success provenance absent: YES
retry_count: 0
```

The later interactive PowerShell `LIVE_SMOKE_RESULT=PASS` is INVALID
EVIDENCE and does not change the consumed one-shot result.

### Official later one-shot smoke (PASS)

A LATER, separately explicitly-authorized live automatic-fallback smoke was
executed and returned PASS. It is a distinct later attempt and does NOT rewrite
or reinterpret the earlier consumed FAIL above.

```text
date:                        2026-08-21
completed_at:                2026-08-21T01:24:07.375Z
result:                      SMOKE_RESULT=PASS
process exit:                0
execution mode:              CODEX_PROVIDER_MODE=auto
normal controller path:      node dist/src/index.js
                             with no smoke-only selector flags
provider:                    openrouter-free
exact selected model:        nvidia/nemotron-3.5-lightning:free
production_launches:         1
retry_count_derived:         0
preflight_count_derived:     1
inference_count_derived:     1
inference occurred:          YES
readonly/provenance:         PASS
worktree:                    C:\Users\Viktor\Desktop\projects\.codex-worktrees\issue-1
task key:                    issue-1
worktree unchanged:          YES
HEAD unchanged:              YES
branch unchanged:            YES
authorization:               consumed exactly once, non-reusable
retry:                       NO
second smoke:                NO
select-only:                 NO
compatibility probe:         NO
manual model test:           NO
registry mutation:           NO
hardware/MQTT:               NO
```

Deterministic read-only provenance was present and validated. This PASS record
is factual evidence of the executed later one-shot smoke only.

### Active procedure (unchanged)

The official PASS above does not create reusable authorization. Smoke execution
still requires a separate explicit one-shot authorization for each attempt; a
completed PASS does not authorize another run; no automatic retries are
allowed; and this record is not an instruction to run another smoke.
