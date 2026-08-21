# Auto Provider Fallback Live Smoke v1

Status: BLOCKED — first authorized one-shot live smoke was consumed and failed
closed at CODEX_COMPATIBILITY_UNKNOWN before successful execution/provenance.
No retry is authorized pending compatibility-aware selection
PLAN -> APPROVE -> BUILD -> REVIEW -> explicit new smoke approval.

### Consumed Smoke Result (authoritative)

```text
LIVE_SMOKE_RESULT=FAIL
failure=CODEX_COMPATIBILITY_UNKNOWN
auto_decision=openrouter-free
auto_provider_gate=PASS
primary_auth_available=true
primary_plan_supported=true
primary_quota_available=false
preferred_model=gpt-5.6-sol
preferred_model_available=true
openrouter_config_available=true
worktree_unchanged=true
readonly_success_record_present=false
retry_count=0
```

### Bogus trailing PASS rejected

The trailing printed `LIVE_SMOKE_RESULT=PASS` block is NOT valid smoke
evidence. It was emitted because smoke commands were pasted into an interactive
PowerShell session, and later pasted statements continued executing after earlier
`throw` statements returned control to the interactive prompt. The bogus success
section had empty `actual_provider`, `model_id`, `phase`, `role`, `completion`
and no provenance record. It is explicitly rejected as evidence.

### Retry authorization

No retry is authorized under the previous one-shot authorization. A new
live attempt requires:

1. `compatibility-aware-free-selection-v1` plan APPROVED
2. BUILD PASS (implementation of compatibility-aware selection)
3. Independent REVIEW PASS
4. Explicit supervisor re-authorization of exactly ONE new smoke attempt

Do NOT automatically revive the previous one-shot authorization.

## Objective

Plan the first real end-to-end production-style smoke of `CODEX_PROVIDER_MODE=auto`
using the existing provider architecture: auto mode begins, the structured primary
availability/quota check proves primary genuinely unavailable, the fallback selector
runs, the compatibility registry gate accepts the registered model, the exact
zero-cost preflight succeeds, the selector chooses exactly
`nvidia/nemotron-3.5-lightning:free`, Codex executes one small READ-ONLY repository
task through OpenRouter, the task completes, provenance records the actual
provider/model/path, and the linked worktree remains byte-for-byte Git-status
unchanged. External inference spend must remain exactly `$0.00`.

This is a PLANNING artifact only. It is NOT another compatibility probe and
performs no inference, no network, no source/test/registry change.

## Verdict

**BLOCKED.** The one-shot live smoke was consumed and failed at
`CODEX_COMPATIBILITY_UNKNOWN`. The architecture defect (compatibility checked
post-selection rather than during eligibility) is now recorded as the root
cause. The fix is planned in `compatibility-aware-free-selection-v1.md`. No
further live attempt is authorized until that plan is approved, built, reviewed,
and a new smoke explicitly re-authorized.

## Relationship to Existing Approved Plans

Precedence (unchanged): `Codex OpenRouter Free Provider v1` (provider modes,
machine-readable primary status, auto decision, fallback selection/registry/
preflight, provenance, thread identity, `$0.00`), `OpenRouter Free Model Selector
v1` (catalog ordering, zero-cost rules, preflight, producer provenance,
worktree-state hashing, locks), `Multi-Model Execution Fallback v1` (worktree,
task/plan validation, prompt, secret, Git/publication, safety contracts), and
`Register nvidia/nemotron-3.5-lightning:free Compatibility v1` (the one current
registry entry). This plan changes none of them.

## Repository Baseline Evidence (2026-08-20, working tree)

- Working tree is dirty with pre-existing user work (`M AGENTS.md`,
  `M CODEX_OPTIMIZATION.md`, untracked `.opencode/agents/fallback-*`,
  `plans/multi-model-execution-fallback-v1.md`,
  `plans/openrouter-free-model-selector-v1.md`, printer-phase plans,
  `tmp-success-recovery-plan-prompt.txt`, `tools/model-runner/`). These are
  untouched by this plan.
- `tools/codex-controller` and `tools/openrouter-free-selector` are committed
  (HEAD `844cdfc` "Register Nemotron Lightning Codex compatibility").
- `git worktree list`: only the main checkout and
  `C:/Users/Viktor/Desktop/projects/.codex-worktrees/issue-1`,
  `.../issue-3` (previous GitHub-issue-mode worktrees). There is no dedicated
  controller/live-test worktree.
- Registry `tools/openrouter-free-selector/config/codex-compatible-free-models-v1.json`
  contains exactly one entry (see section 5.4) and currently parses as valid:
  `validated_at=2026-08-20T17:32:10.368Z <= now(2026-08-20) < valid_until=2026-09-19T17:32:10.368Z`.

## Identified Exact Elements (from existing source)

The following are established by source inspection and are exact.

### 5.1 Provider mode and environment variables

- `parseProviderMode` (`tools/codex-controller/src/core.ts:6`): `auto` is the
  default when `CODEX_PROVIDER_MODE` is missing/empty; `primary` and
  `openrouter-free` are manual overrides; any other value throws
  `INVALID_PROVIDER_MODE` before worktree/selector/Codex work.
- Environment variables consumed by the fallback path (from
  `tools/codex-controller/src/index.ts` `runManualMode`/`fallbackSelection`/
  `fallbackTask`/`runCodexTask` and `core.ts` `prepareFallbackExecution`):
  - `CODEX_PROVIDER_MODE=auto` (or unset);
  - `OPENROUTER_API_KEY` (required; missing/empty → `OPENROUTER_AUTH_MISSING`);
  - `CODEX_TASK_KEY` (manual-mode task identity);
  - `MODEL_TASK_FILE` (strict fallback task file, read from the worktree;
    non-empty, <= 262144 bytes, no BOM, valid UTF-8 → else `INVALID_TASK_FILE`);
  - `MODEL_PLAN_PATH` (required for `plan`/`build`/`review`; absent →
    `INVALID_PLAN_PATH`);
  - `CODEX_PHASE` (`general|plan|build|review`);
  - `CODEX_REVIEW_TARGET` (`plan|implementation`, `review` only → else
    `INVALID_REVIEW_TARGET`);
  - `CODEX_THREAD_MODE` (`resume|fresh`; review/approval always `fresh`);
  - `LOCALAPPDATA` (must be present, non-empty, absolute; used to build the
    isolated `CODEX_HOME` at `%LOCALAPPDATA%\print-engineer-codex\openrouter-home-v1`;
    invalid → `OPENROUTER_CODEX_HOME_INVALID`);
  - `CODEX_REPO` (default `../../..`), `CODEX_BASE_BRANCH` (default `master`),
    `CODEX_WORKTREE_ROOT` (default `../../../.codex-worktrees`),
    `CODEX_STATE` (default `.codex/controller-state.json`);
  - `MODEL_WORKDIR` — consumed only by `--select-only` and
    `--compatibility-probe`, NOT by the normal manual/issue task path.

### 5.2 Exact primary availability/quota gate used by auto mode

- `readPrimaryStatus()` (`tools/codex-controller/src/codex-app-server-client.ts:13`):
  spawns exactly `process.execPath`, the controller-resolved
  `@openai/codex` 0.147.0 `bin/codex.js`, `app-server --listen stdio://
  --strict-config`, no shell, with `primaryEnvironment(process.env)`; performs the
  headerless JSON-RPC sequence `initialize(id 1) → initialized → account/read(id 2,
  refreshToken:false) → account/rateLimits/read(id 3) → model/list(includeHidden:
  true, limit:100, optional cursor pagination, nextCursor:null stop)` under one
  5000 ms deadline; fails closed to `PRIMARY_STATUS_*` codes.
- `classifyPrimary` (`tools/codex-controller/src/provider-decision.ts:7`): primary
  auth is safe only when `account.type==="chatgpt"`, `planType` is literally one
  of `{go,plus,pro}` (`PRIMARY_SUBSCRIPTION_PLAN_TYPES`), and
  `requiresOpenaiAuth===true`; quota snapshot is the authoritative `codex` entry
  (`rateLimitsByLimitId.codex` else `rateLimits` with `limitId==="codex"`);
  `PRIMARY_AVAILABLE` only when `rateLimitReachedType===null`,
  `spendControlReached` is `false`/`null`, `primary` window finite with
  `0 <= usedPercent < 100`, and a present `secondary` also `0 <= usedPercent < 100`;
  preferred primary model is exact `gpt-5.6-sol` present in the complete paginated
  model list with `id===model==="gpt-5.6-sol"` and `"text"` in `inputModalities`.
- `decideProvider` (`tools/codex-controller/src/provider-decision.ts:23`): in `auto`,
  primary is chosen only when auth, plan support, quota, AND preferred-model all
  prove true; anything else selects `openrouter-free`. `PRIMARY_STATUS_UNKNOWN`
  never selects primary.
- The smoke is valid as a fallback test ONLY if this existing structured mechanism
  returns a genuinely non-`PRIMARY_AVAILABLE` result at live-test time. Deliberately
  breaking primary config, deleting/renaming auth, injecting bogus credentials,
  sabotaging connectivity, forcing artificial failure, using an OpenAI paid API key,
  or changing the allowlist are all forbidden.

### 5.3 Exact selector entry point

- `fallbackSelection(worktree, phase)` (`tools/codex-controller/src/index.ts:469`)
  → `selectOpenRouter` (`tools/codex-controller/src/core.ts:41`) →
  `roleForPhase` (`tools/openrouter-free-selector/src/model-selector.ts:6`),
  `fetchCatalog` (`tools/openrouter-free-selector/src/openrouter.ts:33`, exact role
  URL from `catalogUrl` with `supported_parameters=tools`, `input_modalities=text`,
  `output_modalities=text`, `context=32768`, `max_price=0`, `max_output_price=0`,
  role sort/index tail, no `limit`/`offset`, no `category`), `selectFirstEligible`
  (`model-selector.ts:3`, server order, first locally qualified candidate, producer
  exclusion, optional override), then the registry gate and exact-model preflight
  (sections 5.4/5.5).
- Roles: `plan` → PLAN, `build` → BUILD, `review`+`CODEX_REVIEW_TARGET=plan` →
  `plan-review`, `review`+`CODEX_REVIEW_TARGET=implementation` → `review`,
  `general` → `readonly` (NEW — implemented by `auto-readonly-fallback-execution-v1`).

#### General → Readonly selector role

`CODEX_PHASE=general` maps to selector role `readonly`. The readonly catalog
tail is:

```text
sort=intelligence-high-to-low&min_coding_index=0
```

This reuses the exact same catalog-tail as the existing `review` role. The
normal catalog URL still requires:

```text
supported_parameters=tools
input_modalities=text
output_modalities=text
context=32768
max_price=0
max_output_price=0
```

The `readonly` role preserves all existing selector semantics:
- Server-ordered catalog (same query parameters as other roles)
- Exact specific `:free` model identity required
- Zero input price (`prompt: "0"`) and zero output price (`completion: "0"`)
- `openrouter/free` and `openrouter/auto` forbidden
- Compatibility registry required (`requireCompatibility` gate)
- Exact-model preflight required (`preflightExactModel`)
- No candidate #2, no retry, no model switching
- Producer exclusion: `general`/`readonly` has no producer records to exclude

PLAN / BUILD / REVIEW role semantics remain unchanged.

### 5.4 Exact registry gate

- `loadRegistry` + `requireCompatibility` (`tools/openrouter-free-selector/src/compatibility.ts:8-9`):
  exact six-key schema, `model_id` matching `/^[^/,]+\/[^/,]+:free$/` and not
  `openrouter/free`/`openrouter/auto`, `codex_sdk_version==="0.147.0"`,
  `provider_id==="openrouter"`, `wire_api==="responses"`, Z-suffixed millisecond
  timestamps with `valid_until - validated_at === 2,592,000,000` ms; accepts
  `validated_at <= now < valid_until`. Missing, malformed, stale, future, wrong
  provider/protocol/version, or non-exact match → `CODEX_COMPATIBILITY_UNKNOWN`/
  `CODEX_COMPATIBILITY_STALE`; normal fallback stops (no candidate #2).
- Registry path default: `tools/openrouter-free-selector/config/codex-compatible-free-models-v1.json`
  (`index.ts:469`). Current content (exactly one entry):
  `{"model_id":"nvidia/nemotron-3.5-lightning:free","codex_sdk_version":"0.147.0",
  "provider_id":"openrouter","wire_api":"responses",
  "validated_at":"2026-08-20T17:32:10.368Z","valid_until":"2026-09-19T17:32:10.368Z"}`.
- The registry does NOT prove current free availability; the selector rules and
  preflight must still independently prove it at execution time. No hard-coded
  bypass to Lightning exists or is permitted.

### 5.5 Exact zero-cost preflight

- `preflightExactModel` (`tools/openrouter-free-selector/src/openrouter.ts:34`):
  exact-model GET `https://openrouter.ai/api/v1/model/<author>/<slug>` (bearer
  `OPENROUTER_API_KEY`), `data.id === requested id`, then `qualifyModel`
  (`openrouter.ts:19`): `isExactFreeId`, `isStrictZeroPricing` (required `prompt`
  and `completion` present strict lexical-zero strings; optional scalar keys
  `request|image|web_search|internal_reasoning|input_cache_read|input_cache_write`
  zero-or-absent; `overrides` absent or exactly `[]`; no unknown keys), text
  input/output, native `tools`, `context_length` and `top_provider.context_length`
  >= 32768, `expiration_date` absent or in the future. Failure →
  `OPENROUTER_REQUEST_FAILED`/`OPENROUTER_PREFLIGHT_FAILED`, stops before Codex.

### 5.6 Exact provenance output/location

- `tools/openrouter-free-selector/src/provenance.ts`: namespace
  `print-engineer/model-runner/selector-v1` under the worktree Git common dir
  (`git rev-parse --path-format=absolute --git-common-dir`). Files:
  `execution.lock`, `plan-producers/<sha256(canonical plan path)>.json`,
  `build-producers/<sha256(canonical plan path)>.json`.
- Records: `PlanProducer {schema_version:1, kind:"plan_producer", plan_path,
  plan_sha256, model_id}`; `BuildProducer {schema_version:1, kind:"build_producer",
  plan_path, plan_sha256, worktree_state_sha256, model_id}`. Written atomically
  (`atomicWrite`) only after successful Codex exit PLUS an actual valid
  plan-artifact change (PLAN) or worktree-state change (BUILD)
  (`runPlanProducer`/`runBuildProducer`, `core.ts:36-37`); failed or unchanged runs
  never replace provenance. Review/approval/select-only never write.

#### Read-only execution provenance (implemented)

New structured record for the `readonly` selector role (general phase):

```typescript
ReadOnlyExecutionRecord = {
  schema_version: 1;
  kind: "readonly_execution";
  task_key: string;           // CODEX_TASK_KEY or issue-N
  worktree_path: string;      // absolute path of the linked worktree
  worktree_state_sha256: string; // hash from computeWorktreeStateHash
  provider_id: "openrouter";  // actual provider used
  model_id: string;           // exact model ID
  phase: "general";           // controller phase
  role: "readonly";           // selector role used
  completed_at: string;       // ISO 8601 timestamp
  success: true;              // always true for written records
};
```

Storage location:
```text
<git-common-dir>/print-engineer/model-runner/selector-v1/readonly-executions/<sha256(task_key + "|" + worktree_path)>.json
```

The record path is deterministically correlated using:
```text
sha256(task_key + "|" + worktree_path)
```

Do NOT use: `latest`, `mtime`, `newest matching record`, provider-only lookup,
model-only lookup, or worktree-only lookup.

Write conditions (ALL must be true):
1. Execution completed without thrown error
2. Worktree equality check passed (`before === after`)
3. Provider mode was `openrouter-free`
4. Phase was `general` (selector role `readonly`)
5. Write atomically using existing `atomicWrite` convention

For the expected fallback execution:
- `provider_id = openrouter`
- `phase = general`
- `role = readonly`
- `success = true`

Success provenance is written ONLY after:
- execution success
- AND exact `git status --short` before == after.

- Controller state (`.codex/controller-state.json`, `index.ts:392-409`): threads
  map `taskKey → {schemaVersion:2, threadId, branch, worktree, providerMode,
  modelIdentity, role}` persisted only after a successful persistable (fresh,
  non-review) run.

### 5.7 Exact worktree comparison mechanism

- `ensureWorktree` (`index.ts:174`): creates/reuses a linked worktree at
  `CODEX_WORKTREE_ROOT/<taskKey>` on branch `codex/<taskKey>` from
  `origin/<CODEX_BASE_BRANCH>`.
- `validateExistingLinkedWorktree` (`index.ts:119`): required for fallback;
  verifies the path exists, `--show-toplevel` resolves to the worktree, Git dir
  differs from the common dir (linked), the common dir matches `CODEX_REPO`'s
  common dir, and no secret files (`.env`, `.env.local`,
  `config/config.local.yaml`) exist.
- Comparison: `git <worktree> status --short` captured immediately before and
  immediately after; exact string equality required. The compatibility probe
  already implements this via injected `gitStatus` (`compatibility-probe.ts`),
  and any difference fails the run. No cleanup may be used to hide a mutation.

### 5.8 Safe task prompt

- The smoke uses the normal task path and existing task mechanism via
  `MODEL_TASK_FILE`. Do NOT use `--compatibility-probe` for the smoke. Do NOT add
  a smoke-only CLI flag. Do NOT hard-code the smoke task in production code.
- The task must be bounded and read-only. The intended smoke task should require
  only a small repository read, such as reading AGENTS.md and returning a short
  factual result.
- No file edits. No Git mutation. No package installation. No external network
  from the agent. No hardware/MQTT.
- Normal fallback prompts are `buildCodexPrompt(task, phase)` (`index.ts:95`) with
  the task text from `MODEL_TASK_FILE`. The compatibility probe prompt
  (`COMPATIBILITY_PROMPT`) is exclusive to `--compatibility-probe` and is NOT used
  for the smoke.

## Resolved Elements (formerly blocked)

The three previously blocked elements are now established by the approved and
built `auto-readonly-fallback-execution-v1` plan.

### 6.1 Normal CLI command — RESOLVED

Normal CLI execution is now established via the restored `main()` path:

```text
no special control flag
→ parseProviderMode(...)
→ resolveExecutionProvider(...)
→ parsePhase(...)
→ parseThreadMode(...)
→ runManualMode(...) OR runGitHubIssueMode(...)
→ fallbackSelection(...)
→ runCodexTask(...)
```

Special commands remain unchanged:
- `--provider-status`
- `--compatibility-probe`
- `--select-only`

Conflicting/unknown flags remain fail closed.

### 6.2 Read-only live task — RESOLVED

`CODEX_PHASE=general` now maps to selector role `readonly`. The readonly
catalog tail is `sort=intelligence-high-to-low&min_coding_index=0`, reusing the
exact same catalog-tail as the existing `review` role. The task uses the normal
`MODEL_TASK_FILE` mechanism. No smoke-only CLI flag is added.

### 6.3 Read-only provenance — RESOLVED

`ReadOnlyExecutionRecord` provenance is implemented under:
```text
<git-common-dir>/print-engineer/model-runner/selector-v1/readonly-executions/
```

The record path is deterministically correlated using:
```text
sha256(task_key + "|" + worktree_path)
```

The record structurally proves: `schema_version`, `kind`, `task_key`,
`worktree_path`, `worktree_state_sha256`, `provider_id`, `model_id`, `phase`,
`role`, `completed_at`, `success`. Do NOT use `latest`, `mtime`, or provider/model-only lookup.

## Conclusion

Per the plan contract, this plan's one-shot authorization has been **consumed
and FAILED** at `CODEX_COMPATIBILITY_UNKNOWN`. The architecture defect is now
identified and planned for correction in
`compatibility-aware-free-selection-v1.md`. No source, test, or registry change
was made by the smoke. The worktree remained unchanged. No retry is authorized
pending the new compatibility-aware selection plan's full
PLAN -> APPROVE -> BUILD -> REVIEW -> explicit smoke re-approval workflow.

## Future Live Gate (one-shot authorization — CONSUMED AND BLOCKED)

The original one-shot authorization has been consumed. The live smoke failed
at `CODEX_COMPATIBILITY_UNKNOWN` before successful execution or provenance.

A new live attempt requires the following workflow (none of which is
authorized by this plan alone):

1. `compatibility-aware-free-selection-v1` plan submitted and APPROVED
2. BUILD PASS — implementing compatibility-aware selection
3. Independent REVIEW PASS
4. Explicit supervisor re-authorization of exactly ONE new smoke attempt
5. A new or updated smoke plan with fail-stop harness (see Future Smoke
   Harness Correction below)

Do NOT automatically revive the previous one-shot authorization.

### Pre-smoke verification (inference-free)

Before live execution, the command must prove/confirm:

- `git diff --check` passes on the plan file;
- `git status --short` shows expected working-tree state;
- readonly catalog role exact tail: `sort=intelligence-high-to-low&min_coding_index=0`;
- compatibility entry is current and not expired: `now < valid_until=2026-09-19T17:32:10.368Z`;
- deterministic readonly provenance path construction: `sha256(task_key + "|" + worktree_path)`;
- exact task key (from `CODEX_TASK_KEY` or issue number);
- exact target worktree (from `ensureWorktree` result);
- task file (`MODEL_TASK_FILE`) exists and contains bounded read-only contents.

### Live smoke gates

Before inference, the command must prove/confirm:

- mode is `auto` (`CODEX_PROVIDER_MODE=auto` is REQUIRED; do NOT substitute
  `CODEX_PROVIDER_MODE=openrouter-free` because this smoke specifically proves
  real auto fallback);
- primary is genuinely unavailable/quota-blocked via the existing structured
  check (never deliberately broken; if structured primary status says
  `PRIMARY_AVAILABLE`: STOP before inference and report
  `AUTO_FALLBACK_LIVE_GATE_BLOCKED_PRIMARY_AVAILABLE`);
- fallback candidate eligibility under normal selector rules (if not: STOP, report
  `AUTO_FALLBACK_LIVE_GATE_BLOCKED_MODEL_UNAVAILABLE`, no candidate #2);
- registry compatibility is valid at execution time
  (`validated_at=2026-08-20T17:32:10.368Z < now < valid_until=2026-09-19T17:32:10.368Z`);
- the exact zero-cost preflight succeeded.

### Execution gates

Once the task begins: exactly one inference execution, no retry, no mid-run
provider switching, no fallback to another model, no switch back to primary.

### Failure protocol

If ANY stage fails: STOP AND REPORT. A failure does NOT authorize debugging
through another inference attempt. Stages include:

- primary status
- auto provider decision
- catalog retrieval
- zero-price qualification
- registry compatibility
- exact-model preflight
- Codex SDK execution
- worktree equality
- provenance write
- provenance verification

### Possible outcomes (no retry for any outcome)

```text
AUTO_FALLBACK_LIVE_SMOKE_SUCCESS
AUTO_FALLBACK_LIVE_SMOKE_FAILED
AUTO_FALLBACK_LIVE_GATE_BLOCKED_PRIMARY_AVAILABLE
AUTO_FALLBACK_LIVE_GATE_BLOCKED_MODEL_UNAVAILABLE
```

### Worktree equality gate

The target linked worktree must be validated using the existing production
mechanism. Record/inspect its expected current `git status --short`. The critical
invariant is: exact before status == exact after status for the target smoke
execution. Do NOT clean, reset, stash, restore, checkout, or revert to manufacture
equality. Mutation = failure.

## Required Structured Success Evidence

Successful future smoke must prove:

- `requested_provider_mode=auto`
- actual provider: `openrouter`
- exact selected model: `<actual selector result>`
- verified free: `true` / equivalent existing selector evidence
- compatibility: passed
- exact-model preflight: passed
- normal task: completed
- worktree: unchanged
- readonly success provenance: present and exactly correlated

Provenance verification must use exact `task_key` + `worktree_path`
deterministic path construction. Console prose alone is not sufficient where
structured evidence exists.

## Success Requirements (future live smoke)

SUCCESS requires ALL of:

1. mode was `auto`;
2. primary was genuinely unavailable/quota-blocked using existing structured
   evidence;
3. no paid OpenAI API path used;
4. normal selector selected a verified-free model (e.g.,
   `nvidia/nemotron-3.5-lightning:free`);
5. model remained current/free under normal selector rules;
6. registry compatibility gate passed;
7. immediate exact zero-cost preflight passed;
8. provider actually used: `openrouter`;
9. wire/API/provider configuration remained the production configuration
   (`openRouterConfig()` in `core.ts:32`, `agents.enabled=false`);
10. task used real repo/tool access (read `AGENTS.md`, report concisely);
11. requested read-only task completed;
12. provenance proves the actual provider/model/task correlation via
    `ReadOnlyExecutionRecord` at deterministic path;
13. no retry;
14. no candidate #2;
15. no provider/model switching;
16. worktree before/after exactly equal (`git status --short` equality);
17. no Git publication;
18. no hardware/printer/MQTT interaction;
19. external inference spend exactly `$0.00`.

Forbidden throughout: OpenAI paid API, paid OpenRouter model, paid fallback,
manual credits, non-free candidate, `openrouter/free` router, `openrouter/auto`
router, worktree mutation, and any cleanup used to hide a mutation.

## Future Smoke Harness Correction

The future live-smoke PLAN must NOT rely on pasting a long sequence directly
into interactive PowerShell where execution may continue after a thrown error.

Plan one of these fail-stop mechanisms:

- Create a temporary local `.ps1` smoke harness outside the target worktree
  and execute it once with `PowerShell -File`; OR
- Execute one syntactically complete script block/process whose uncaught
  terminating failure prevents later success-report commands from running.

The future harness must:

- Emit PASS only from a success-only control path
- Never emit PASS after any failed gate
- Preserve exactly-one-live-execution semantics
- Not retry
- Not delete evidence automatically

This harness correction is test/operator tooling only. Do not add smoke
logic to production controller source.

## Verification Performed for This Plan

Inference-free commands only, as required:

- `git diff --check -- plans/auto-provider-fallback-live-smoke-v1.md`
- `git status --short`

Targeted inference-free confirmation of:

- readonly catalog role exact tail: `sort=intelligence-high-to-low&min_coding_index=0`;
- compatibility entry current/not expired: `now < valid_until=2026-09-19T17:32:10.368Z`;
- deterministic readonly provenance path construction: `sha256(task_key + "|" + worktree_path)`;
- exact task key (from `CODEX_TASK_KEY`);
- exact target worktree (from `ensureWorktree` result);
- task file (`MODEL_TASK_FILE`) existence and bounded read-only contents.

No compatibility probe. No normal inference during this approval step.
No source, test, or registry file was modified. No live normal task,
compatibility probe, network request, or inference was performed. No commit or
push was made.

## Git / Publication

Even after successful future smoke: this plan does NOT itself authorize
commit/push during the smoke command. Workflow remains:

```text
live smoke PASS
→ supervisor reviews evidence
→ checkpoint commit
→ push
```

Preserve unrelated dirty/untracked user work.
