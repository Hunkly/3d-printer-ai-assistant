# Auto Read-Only Fallback Execution v1

Status: APPROVED — implementation is authorized, including the reviewed Readonly Catalog Role Scope Correction.

> **Scope Correction Notice:** The original APPROVED plan (line 3) assumed `openrouter.ts` would remain unchanged. The interrupted BUILD revealed that `roleForPhase("general")` returning `"readonly"` requires `catalogUrl("readonly")` to succeed, which in turn requires a `ROLE.readonly` entry in `openrouter.ts`. Without it, production throws `INVALID_SELECTOR_ROLE`. This correction adds `openrouter.ts` to the authorized source scope and documents the justification for the catalog-tail reuse. See **Readonly Catalog Role Scope Correction** below.

## Objective

Restore the normal no-flag CLI task execution path that was lost in commit 0c1318a, add a bounded read-only selector role for `CODEX_PHASE=general` that works with the existing fallback architecture, enforce read-only worktree safety via exact `git status --short` equality, and introduce a narrowly scoped structured read-only execution provenance record that proves provider_id, exact model_id, task/worktree correlation, successful completion, and unchanged worktree — all while preserving the existing provider architecture, selector zero-cost/preflight/registry contracts, and special commands unchanged.

This is an **implementation plan**. It does not perform live inference, network requests, compatibility probes, or hardware operations.

## Relationship to Existing Approved Plans

Precedence (unchanged):
1. `Codex OpenRouter Free Provider v1` (APPROVED) — provider modes, machine-readable primary status, auto decision, fallback selection/registry/preflight, provenance, thread identity, $0.00 spend
2. `OpenRouter Free Model Selector v1` (APPROVED) — catalog ordering, zero-cost rules, preflight, producer provenance, worktree-state hashing, locks
3. `Multi-Model Execution Fallback v1` (APPROVED) — worktree, task/plan validation, prompt, secret, Git/publication, safety contracts
4. `Register nvidia/nemotron-3.5-lightning:free Compatibility v1` (APPROVED) — the one current registry entry

This plan **does not** change any of the above. It restores lost wiring, adds one bounded selector role, adds one provenance record kind, and adds worktree equality guards.

## Current Regression (Authoritative Fact)

`main()` in `tools/codex-controller/src/index.ts:475-477` currently:
- Calls `runControllerCli(process.argv.slice(2), ...)`
- `runControllerCli` → `dispatchControllerCommand`
- `dispatchControllerCommand` with empty flags:
  - Passes `INVALID_COMMAND` guard (no unknown flags, no duplicates, length ≤ 1)
  - Matches none of the three special-flag branches (`--provider-status`, `--compatibility-probe`, `--select-only`)
  - Returns `undefined`
- `runControllerCli` prints nothing, returns exit code 0
- **No task is executed**

Historical `main()` before 0c1318a dispatched to `runGitHubIssueMode` / `runManualMode` which call `runCodexTask` → `fallbackSelection` / `resolveExecutionProvider`.

**Orphaned functions that must be wired back:**
- `runManualMode` (index.ts:450)
- `runGitHubIssueMode` (index.ts:414)
- `resolveExecutionProvider` (index.ts:470)
- `fallbackSelection` (index.ts:469)
- `runCodexTask` (index.ts:365)

## Plan Scope — Minimal Source Changes

### 1. Restore Normal CLI Default Path (tools/codex-controller/src/index.ts)

**File:** `tools/codex-controller/src/index.ts`

**Change `main()` / `runControllerCli` / `dispatchControllerCommand`:**

When `flags.length === 0` (no special flags), dispatch to normal task execution:
- Determine `providerMode = parseProviderMode(env.CODEX_PROVIDER_MODE)` (defaults to `auto`)
- Call `resolveExecutionProvider(providerMode)` → `{ provider, model }`
- If `provider === "primary"`:
  - `modelIdentity = "gpt-5.6-sol"` (PREFERRED_MODEL)
  - Execute via `runManualMode` or `runGitHubIssueMode` with `providerMode="primary"`
- If `provider === "openrouter-free"`:
  - `modelIdentity` will be resolved inside `runManualMode`/`runGitHubIssueMode` via `fallbackSelection`
  - Execute via `runManualMode` or `runGitHubIssueMode` with `providerMode="openrouter-free"`

**Mode selection logic (existing, preserved):**
- `CODEX_ISSUE_NUMBER` set → GitHub issue mode (`runGitHubIssueMode`)
- `CODEX_TASK_KEY` set (and for primary, `CODEX_TASK` set) → manual mode (`runManualMode`)
- Neither set → error: "Set CODEX_ISSUE_NUMBER for GitHub mode, or CODEX_TASK_KEY and CODEX_TASK for manual mode."

**Special commands remain unchanged:**
- `--provider-status` → existing behavior
- `--compatibility-probe` → existing behavior
- `--select-only` → existing behavior
- Invalid/conflicting flags → `INVALID_COMMAND` (fail closed)

### 2. Add Bounded Read-Only Selector Role for `general` Phase (tools/openrouter-free-selector/src/model-selector.ts)

**File:** `tools/openrouter-free-selector/src/model-selector.ts`

**Change `roleForPhase` function:**

Add a new selector role `readonly` (or `general-readonly`) that:
- Maps from `CODEX_PHASE=general` when `providerMode === "openrouter-free"`
- **Preserves all existing selector semantics:**
  - Server-ordered catalog (same query parameters as other roles)
  - Exact specific `:free` model identity required
  - Zero input price (`prompt: "0"`) and zero output price (`completion: "0"`) — strict lexical zero
  - `openrouter/free` and `openrouter/auto` forbidden
  - Compatibility registry required (`requireCompatibility` gate)
  - Exact-model preflight required (`preflightExactModel`)
  - No candidate #2, no retry, no model switching
  - Producer exclusion logic: `general`/`readonly` has no producer records to exclude (unlike plan/build/review)

**Role mapping table (updated):**

| Controller phase | Review target | Selector role |
|---|---|---|
| `plan` | none | `plan` |
| `build` | none | `build` |
| `review` | `plan` | `plan-review` |
| `review` | `implementation` | `review` |
| `general` | n/a | `readonly` (NEW) |

**Implementation:**
- Add `"readonly"` to `SelectorRole` type union
- In `roleForPhase`: `if (phase === "general") return "readonly";`
- In `selectFirstEligible`: `readonly` uses same catalog, same qualification, same registry gate, same preflight — no producer exclusion needed

**No changes to:**
- `fetchCatalog` query parameters (reuse existing)
- `qualifyModel` / `isStrictZeroPricing` / `isExactFreeId`
- `loadRegistry` / `requireCompatibility`
- `preflightExactModel`
- Compatibility registry JSON

**Authorized change (new):**
- `openrouter.ts` — add `readonly` key to the `ROLE` catalog-query map (see Readonly Catalog Role Scope Correction)

### 3. Add Read-Only Worktree Equality Guard (tools/codex-controller/src/index.ts)

**File:** `tools/codex-controller/src/index.ts`

**New helper function:**
```typescript
function captureGitStatus(worktree: string): string {
  return git(worktree, "status", "--short");
}
```

**Integration points:**
- In `runCodexTask` (or a new wrapper used by both `runManualMode` and `runGitHubIssueMode` when `providerMode === "openrouter-free"` AND `phase === "general"`):
  1. `const before = captureGitStatus(workingDirectory);` — immediately before Codex execution
  2. Execute Codex task
  3. `const after = captureGitStatus(workingDirectory);` — immediately after Codex execution
  4. `if (before !== after) throw new Error("READONLY_WORKTREE_MUTATED");`
- **Never** clean/reset/stash/revert to hide mutation. Mutation = failure.
- This guard applies **only** to read-only (`general`/`readonly`) fallback executions.
- PLAN/BUILD/REVIEW phases continue to use their existing producer-based provenance (which already validates changes).

### 4. Structured Read-Only Execution Provenance (tools/openrouter-free-selector/src/provenance.ts)

**File:** `tools/openrouter-free-selector/src/provenance.ts`

**New record type:**
```typescript
export type ReadOnlyExecutionRecord = {
  schema_version: 1;
  kind: "readonly_execution";
  task_key: string;           // CODEX_TASK_KEY or issue-N
  worktree_path: string;      // absolute path of the linked worktree
  worktree_state_sha256: string; // hash from computeWorktreeStateHash (proves unchanged)
  provider_id: "openrouter";  // actual provider used
  model_id: string;           // exact model ID (e.g., "nvidia/nemotron-3.5-lightning:free")
  phase: "general";           // or "readonly"
  role: "readonly";           // selector role used
  completed_at: string;       // ISO 8601 timestamp (exact completion time)
  success: true;              // always true for written records
};
```

**Storage location:**
- Same provenance namespace: `<git-common-dir>/print-engineer/model-runner/selector-v1/`
- New subdirectory: `readonly-executions/`
- Filename: `<sha256(task_key + "|" + worktree_path)>.json` (deterministic key, no mtime lookup)

**Write conditions (ALL must be true):**
1. Execution completed without thrown error
2. Worktree equality check passed (`before === after`)
3. Provider mode was `openrouter-free`
4. Phase was `general` (selector role `readonly`)
5. Write atomically using existing `atomicWrite` convention

**Do NOT write if:**
- Execution failed/threw
- Worktree mutated
- Provider was `primary`
- Phase was not `general`

**Do NOT store:**
- Prompt/task contents
- Model response
- Stdout/stderr
- Secrets/headers/environment values
- Raw errors
- AGENTS.md contents

**Integration:**
- In `runCodexTask` (or wrapper): after successful `general`/`readonly` fallback execution AND worktree equality passes, call a new `writeReadOnlyExecutionRecord(...)` function
- Use existing `provenancePaths`-style helper for deterministic path resolution
- Reuse `atomicWrite` from provenance.ts

### 5. Wire Read-Only Path Through Controller (tools/codex-controller/src/index.ts)

**In `runCodexTask` (or new internal function):**
- Detect `providerMode === "openrouter-free" && phase === "general"`
- Capture `git status --short` before execution
- Execute via `runNormalFallbackExecution` (existing)
- Capture `git status --short` after execution
- Enforce equality
- On success: write read-only provenance record
- On mutation: throw `READONLY_WORKTREE_MUTATED` (no provenance written)
- On other failure: no provenance written

**Task input for `general` phase:**
- Uses existing `fallbackTask(worktree)` which reads `MODEL_TASK_FILE` from worktree
- Do NOT hard-code AGENTS.md smoke prompt — normal task-file mechanism

## Test Scope — Focused Hermetic Tests

### tools/codex-controller/test/provider-flow.test.ts (extend)

New test cases:
1. **No-flag CLI reaches normal manual execution** — `dispatchControllerCommand([], env)` with `CODEX_TASK_KEY` + `CODEX_TASK` + `CODEX_PROVIDER_MODE=auto` invokes `runManualMode` path
2. **GitHub issue mode remains reachable** — `CODEX_ISSUE_NUMBER` triggers `runGitHubIssueMode`
3. **All three special commands unchanged** — `--provider-status`, `--compatibility-probe`, `--select-only` still work exactly as before
4. **Conflicting flags fail closed** — `--provider-status --select-only` → `INVALID_COMMAND`
5. **Normal auto uses structured primary decision** — `auto` mode calls `resolveExecutionProvider` → `readPrimaryStatus` → `classifyPrimary` → `decideProvider`
6. **Primary unavailable → openrouter-free** — mocked `PRIMARY_UNAVAILABLE` selects fallback
7. **Primary available → primary** — mocked `PRIMARY_AVAILABLE` selects primary with `gpt-5.6-sol`
8. **No paid API path** — primary path never receives `OPENROUTER_API_KEY`; fallback never receives `OPENAI_API_KEY`
9. **General maps to bounded read-only selector role** — `CODEX_PHASE=general` + `openrouter-free` → `roleForPhase` returns `"readonly"` (not `INVALID_SELECTOR_ROLE`)
10. **PLAN/BUILD/REVIEW selector semantics unchanged** — existing role mappings still work
11. **Read-only worktree equality guard** — unchanged worktree succeeds; mutated worktree throws `READONLY_WORKTREE_MUTATED`
12. **No cleanup hides mutation** — mutation throws before any reset/stash
13. **Success writes exactly one correlated read-only provenance record** — verify file exists, content matches schema
14. **Record proves actual provider_id** — `provider_id: "openrouter"`
15. **Record proves exact model_id** — e.g., `nvidia/nemotron-3.5-lightning:free`
16. **Record proves exact task correlation** — `task_key` matches `CODEX_TASK_KEY`
17. **Record proves exact worktree correlation** — `worktree_path` and `worktree_state_sha256` match
18. **Record proves successful completion** — `success: true`, `completed_at` present
19. **Record proves unchanged worktree** — `worktree_state_sha256` equals pre-execution hash
20. **Failed execution writes no success record** — error path creates no file
21. **Mutated execution writes no success record** — `READONLY_WORKTREE_MUTATED` creates no file
22. **Existing plan/build provenance remains valid** — `plan_producer` / `build_producer` unchanged
23. **Provenance contains no prompt/response/stdout/secrets** — schema validation
24. **No real network/inference in tests** — all mocked

### tools/openrouter-free-selector/test/model-selector.test.ts (extend)

New test cases:
1. **`roleForPhase("general")` returns `"readonly"`** — no `INVALID_SELECTOR_ROLE`
2. **`readonly` role uses server-ordered catalog** — same `fetchCatalog` call as other roles
3. **`readonly` role requires exact `:free` identity** — `isExactFreeId` enforced
4. **`readonly` role requires zero-cost qualification** — `isStrictZeroPricing` enforced
5. **`readonly` role forbids `openrouter/free` and `openrouter/auto`** — same as other roles
6. **`readonly` role requires compatibility registry** — `requireCompatibility` called
7. **`readonly` role requires exact-model preflight** — `preflightExactModel` called
8. **`readonly` role has no producer exclusion** — `excluded` set empty
9. **No candidate #2 / no retry / no model switching** — same failure semantics
10. **PLAN/BUILD/REVIEW roles unchanged** — regression coverage

### tools/openrouter-free-selector/test/provenance.test.ts (extend)

New test cases:
1. **`ReadOnlyExecutionRecord` schema validation** — required fields, types, constraints
2. **Deterministic filename from task_key + worktree_path** — no mtime lookup
3. **Atomic write uses existing `atomicWrite`** — same lock/directory conventions
4. **Write only on success + unchanged worktree** — mutation/failure paths write nothing
5. **Record contains no forbidden fields** — prompt, response, stdout, secrets, headers, env, errors, AGENTS.md
6. **Namespace/path conventions preserved** — same `PROVENANCE_NAMESPACE`, git-common-dir root

## Explicit Non-Changes (Guardrails)

| Area | Change? |
|---|---|
| Primary gate (`readPrimaryStatus`, `classifyPrimary`, `decideProvider`) | NO |
| Selector zero-cost rules (`qualifyModel`, `isStrictZeroPricing`, `isExactFreeId`) | NO |
| Selector preflight (`preflightExactModel`) | NO |
| Compatibility registry (`loadRegistry`, `requireCompatibility`, JSON schema) | NO |
| Catalog query (`fetchCatalog` parameters) | NO |
| Retry / candidate #2 / model switching | NO |
| `openrouter/free` / `openrouter/auto` forbidden | NO (preserved) |
| Special commands (`--provider-status`, `--compatibility-probe`, `--select-only`) | NO |
| `package.json` / `package-lock.json` / dependencies | NO |
| `codex-app-server-client.ts` | NO |
| `compatibility.ts` / `compatibility-probe.ts` semantics | NO |
| Printer/MQTT/hardware | NO |
| Git stage/commit/push | NO |

## Blocked Elements — Build Stop Conditions

If any of the following cannot be established from existing source after targeted inspection, mark the plan **BLOCKED** at build time rather than inventing:

1. Exact `git status --short` capture mechanism — confirmed in `index.ts` line 219 (`git(w,"status","--short")`) and `compatibility-probe.ts`
2. Exact `computeWorktreeStateHash` for worktree correlation — confirmed in `provenance.ts:17-18`
3. Exact `atomicWrite` convention — confirmed in `provenance.ts:26`
4. Exact provenance namespace path resolution — confirmed in `provenance.ts:19-20` (`provenancePaths`)
5. Exact task/worktree identifiers for correlation — `CODEX_TASK_KEY` (manual) or `issue-${number}` (GitHub), worktree path from `ensureWorktree`
6. `runCodexTask` signature supports the new flow — confirmed at `index.ts:365-412`

## Readonly Catalog Role Scope Correction

APPROVED — openrouter.ts and the readonly catalog mapping are authorized within the exact bounded scope below.

### 1. Authorization of openrouter.ts

`tools/openrouter-free-selector/src/openrouter.ts` is now an authorized source file because the new readonly selector role requires a corresponding catalog query mapping in the `ROLE` catalog-query map.

**Rationale:** The original plan stated "No changes to openrouter.ts catalog/preflight logic." However, `roleForPhase("general")` returns `"readonly"`, and `fetchCatalog(..., "readonly")` calls `catalogUrl("readonly")`, which reads `ROLE["readonly"]`. Without a `ROLE.readonly` entry, the function throws `INVALID_SELECTOR_ROLE`. This is not a logic change — it is a necessary wiring addition to the existing catalog-query registry.

### 2. Catalog-Tail Semantics

The readonly catalog mapping must use an existing bounded server-order query, not introduce local scoring/ranking.

**Current `ROLE` catalog-query map (openrouter.ts:31):**

| Role | Catalog Query Tail |
|---|---|
| `plan` | `sort=intelligence-high-to-low&min_intelligence_index=0&min_agentic_index=0` |
| `plan-review` | `sort=intelligence-high-to-low&min_intelligence_index=0&min_agentic_index=0` |
| `build` | `sort=coding-high-to-low&min_coding_index=0&min_agentic_index=0` |
| `review` | `sort=intelligence-high-to-low&min_coding_index=0` |
| `readonly` (NEW) | `sort=intelligence-high-to-low&min_coding_index=0` |

The `readonly` entry reuses the **exact same catalog-tail** as the existing `review` role. This is justified because:

1. The `review` catalog-tail (`sort=intelligence-high-to-low&min_coding_index=0`) already selects models with non-zero coding capability and high intelligence ordering.
2. The `general`/`readonly` phase is a bounded, read-only, non-creative task (status queries, informational responses). It requires no producer-specific catalog tail (unlike `plan`/`build` which use `min_agentic_index=0`).
3. The `review` tail provides the broadest bounded server-order subset that satisfies the zero-cost, tools-capable, text-in/text-out, 32k+ context constraints — appropriate for a general read-only role.
4. No local scoring or ranking is introduced. Ordering is entirely server-provided.
5. This is the minimum appropriate behavior: the `general` phase does not need the specialized `plan`/`build` agentic-index filtering, nor does it need to narrow further than `review`.

**If this exact reuse cannot be justified from the existing architecture, mark the correction BLOCKED rather than inventing a new ranking/query.**

### 3. What This Correction Does NOT Authorize

This correction does NOT authorize any changes to:

- `qualifyModel(...)`
- `isStrictZeroPricing(...)`
- `isExactFreeId(...)`
- `fetchCatalog` completeness validation
- `preflightExactModel(...)`
- `OPENROUTER_BASE_URL`
- Pricing rules
- Context requirements
- `tools` requirement
- Model expiration handling

### 4. What This Correction Preserves

This correction preserves all of the following invariants:

- Server-provided ordering (no local scoring/ranking)
- Exact specific `:free` identity (e.g., `nvidia/nemotron-3.5-lightning:free`)
- Prompt price exactly zero (`"0"` lexical)
- Completion price exactly zero (`"0"` lexical)
- `openrouter/free` rejected
- `openrouter/auto` rejected
- Compatibility registry required
- Exact-model preflight required
- No candidate #2
- No retry
- No model switching

### 5. PLAN / BUILD / REVIEW Catalog Semantics Unchanged

The `plan`, `plan-review`, `build`, and `review` role catalog queries remain exactly as defined in the original approved plan. No changes to their `ROLE` entries.

### 6. Corrected Authorized Source Scope

| File | Authorization |
|---|---|
| `tools/codex-controller/src/index.ts` | YES — CLI restoration, worktree guard, provenance write |
| `tools/openrouter-free-selector/src/model-selector.ts` | YES — `SelectorRole` extension, `roleForPhase` |
| `tools/openrouter-free-selector/src/openrouter.ts` | **YES — added by this correction** (`ROLE.readonly` entry) |
| `tools/openrouter-free-selector/src/provenance.ts` | YES — `ReadOnlyExecutionRecord` |

| File | Authorization |
|---|---|
| `tools/codex-controller/test/provider-flow.test.ts` | YES |
| `tools/openrouter-free-selector/test/model-selector.test.ts` | YES |
| `tools/openrouter-free-selector/test/openrouter.test.ts` | **YES — added by this correction** if needed to prove readonly catalog query |
| `tools/openrouter-free-selector/test/provenance.test.ts` | YES |

| File | Authorization |
|---|---|
| `core.ts` | NO |
| `compatibility.ts` | NO |
| `compatibility-probe.ts` | NO |
| Registry changes | NO |
| Package/lock/dependency changes | NO |

### 7. Corrected Test Scope

At minimum, future BUILD tests must prove:

- `roleForPhase("general") === "readonly"`
- `catalogUrl("readonly")` succeeds (no `INVALID_SELECTOR_ROLE`)
- readonly catalog URL still contains: `supported_parameters=tools`, `input_modalities=text`, `output_modalities=text`, `context=32768`, `max_price=0`, `max_output_price=0`
- readonly uses the exact approved server-order tail (`sort=intelligence-high-to-low&min_coding_index=0`)
- unknown roles still throw `INVALID_SELECTOR_ROLE`
- `plan`/`plan-review`/`build`/`review` catalog URLs remain unchanged
- zero-price qualification logic remains unchanged
- no local candidate scoring is introduced

### 8. Interrupted BUILD State

- Existing partial BUILD changes are preserved in the working tree
- No partial source/test change is approved retroactively until this corrected plan passes independent review
- BUILD must not continue yet
- No source should be reverted merely because it was produced before the scope omission was discovered
- After approval, the next BUILD session must inspect and continue the existing partial implementation rather than restart from scratch

## Verification Commands (Pre-Commit)

```powershell
git diff --check -- plans/auto-readonly-fallback-execution-v1.md
git status --short
```

## Expected Outcome Summary

| Requirement | Planned |
|---|---|
| Regression restoration planned | YES |
| Normal CLI default path defined | YES |
| Read-only/general fallback role defined | YES |
| PLAN/BUILD/REVIEW semantics preserved | YES |
| Read-only worktree equality guard defined | YES |
| Structured read-only provenance defined | YES |
| `provider_id` structurally persisted | YES |
| `model_id`/`task`/`worktree`/`completion` correlation defined | YES |
| Primary gate redesign | NO |
| Selector zero-cost/preflight redesign | NO |
| Registry change | NO |
| Retry/model switching change | NO |
| Source modified | NO (plan only) |
| Tests modified | NO (plan only) |
| Live task performed | NO |
| Compatibility probe performed | NO |
| Inference/network | NO |
| Commit | NO |
| Push | NO |

## Future Live Gate (Not Authorized Here)

After this plan is **APPROVED**, **BUILD PASS**, and independent **REVIEW PASS**, the blocked plan `auto-provider-fallback-live-smoke-v1.md` must be explicitly reconsidered. This plan does not authorize any live task.