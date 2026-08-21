# Compatibility-Aware Free Selection v1

Status: APPROVED — implementation is authorized under this approved plan

Revision: 2 (corrected after independent PLAN re-review) — fixes the
loadRegistry type contract, makes compatibility.ts the sole compatibility
authority, removes the per-model "duplicate" status, corrects verification
commands and smoke-state documentation, and removes stale contradictory
language from the previous revision.

## Objective

Fix the architecture defect where normal server-ordered selection picks the first
strict-zero-qualified candidate and THEN checks compatibility post-selection,
causing `CODEX_COMPATIBILITY_UNKNOWN` even when a later server-ordered candidate
has a current valid compatibility entry. Move compatibility into pre-selection
eligibility so the first fully eligible candidate (qualified + not-excluded +
compatible) is selected, and only then exactly one preflight and one inference
follow.

This is a correction to the selection pipeline. It does not change catalog
queries, pricing rules, zero-cost gates, producer semantics, thread identity,
or the no-retry / no-candidate-two / no-model-switching contract.

## Root Cause (Authoritative)

The authorized one-shot live auto smoke produced:

```text
LIVE_SMOKE_RESULT=FAIL
failure=CODEX_COMPATIBILITY_UNKNOWN
auto_decision=openrouter-free
worktree_unchanged=true
readonly_success_record_present=false
retry_count=0
```

Post-run inspection confirmed:

- `selectFirstEligible` walks server-ordered catalog order and checks only
  `qualifyModel` + the exclusion rules.
- It returns the first strict-zero-qualified, non-excluded candidate.
- `requireCompatibility` then runs separately on that already selected model.
- If that model has no current valid registry entry, the whole operation fails
  with `CODEX_COMPATIBILITY_UNKNOWN`.
- A later server-ordered candidate that IS currently compatible is never
  considered, because selection already committed to the first candidate.

This is the defect. The fix is architectural: compatibility becomes part of
eligibility before a model is committed as selected.

## Corrected Selection Flow

### Normal (auto) selection

```text
fresh catalog in server order
→ for each catalog item:
      strict qualifyModel          (unchanged)
      existing exclusion rules     (unchanged)
      current compatibility eligibility  (NEW — before model commitment)
→ choose the first item satisfying ALL three eligibility gates
→ commit selected model
→ perform exactly ONE exact-model preflight
→ if preflight succeeds, perform exactly ONE inference
→ any failure after selection STOPS
```

### Explicit override selection

The explicit `vendor/model:free` override model MUST satisfy the same three
eligibility gates. If it fails any gate, STOP with the appropriate
deterministic failure. No alternative model. No fallthrough.

### What is NOT changed

- Server order remains authoritative — no local scoring, ranking, randomness.
- The first fully eligible compatible model is selected (no candidate #2).
- Exactly one preflight after selection; exactly one inference.
- Inference failure stops (no candidate #2, no retry, no model switch).

## Compatibility Authority (`compatibility.ts`)

`tools/openrouter-free-selector/src/compatibility.ts` is the SOLE
compatibility authority. It owns:

- `loadRegistry(...)` and ALL registry validation;
- `compatibilityStatus(...)` — all current/stale evaluation;
- the authoritative current-validity semantics by REUSING the existing
  `validateEntry` implementation.

`tools/openrouter-free-selector/src/model-selector.ts` only consumes already
validated `CompatibilityEntry[]` and calls `compatibilityStatus(...)`. It MUST
NOT parse timestamps itself, MUST NOT implement window/validity rules
itself, and MUST NOT use `Date.parse` for compatibility determination.

### File: `tools/openrouter-free-selector/src/compatibility.ts`

**`loadRegistry` contract (authoritative):**

```typescript
export function loadRegistry(path: string): CompatibilityEntry[]
```

`loadRegistry` runs the full existing runtime validation BEFORE returning
`CompatibilityEntry[]`, preserving exactly:

- registry root/schema validation (root object, not array, `schema_version ===
  1`, `entries` is an array, and the only allowed root keys are `entries` and
  `schema_version`);
- exact entry schema via the existing `validSchema` type guard (the exact
  6-field name set, exact field formats);
- duplicate `model_id` rejection at load time;
- exact `model_id` format (`/^[^/]+\/[^/]+:free$/`), and rejection of
  `openrouter/free` and `openrouter/auto`;
- exact `codex_sdk_version` (`0.147.0`);
- `provider_id === "openrouter"`;
- `wire_api === "responses"`;
- exact timestamp format;
- the compatibility validity-window contract (the exact 2_592_000-second
  half-open interval `[validated_at, valid_until)` enforced by
  `validSchema` + `validateEntry`).

**Type-safe construction — no unchecked composite cast:**

The implementation MUST NOT use a blanket `as CompatibilityEntry[]` cast. The
existing `validSchema(e: unknown): e is CompatibilityEntry` is a user-defined
type guard. Each validated entry is collected via that narrowing:

```typescript
const validated: CompatibilityEntry[] = [];
const modelIds = new Set<string>();
for (const entry of root.entries) {
  if (!validSchema(entry)) throw new Error("COMPATIBILITY_REGISTRY_INVALID");
  if (modelIds.has(entry.model_id)) throw new Error("COMPATIBILITY_REGISTRY_INVALID");
  modelIds.add(entry.model_id);
  validated.push(entry); // entry is narrowed to CompatibilityEntry by the guard
}
return validated;
```

No explicit array-level cast is required to satisfy TypeScript; each pushed
entry is narrowed by the existing type guard.

**`compatibilityStatus` contract (NEW, authoritative, in compatibility.ts):**

```typescript
export type CompatibilityStatus = "current" | "unknown" | "stale";

export function compatibilityStatus(
  entries: CompatibilityEntry[],
  modelId: string,
  now: Date
): CompatibilityStatus;
```

The per-model status is exactly ONE of three values: `current`, `unknown`,
`stale`. There is NO fourth status.

Implementation reuses the existing authoritative current-validity check:

```typescript
export function compatibilityStatus(
  entries: CompatibilityEntry[],
  modelId: string,
  now: Date
): CompatibilityStatus {
  const matching = entries.filter(e => e.model_id === modelId);
  if (matching.length === 0) return "unknown";
  // matching.length > 1 cannot occur for loadRegistry-validated entries:
  // loadRegistry rejects ANY duplicate model. There is no per-model
  // "duplicate" status and no fourth status value.
  const entry = matching[0];
  return validateEntry(entry, now) ? "current" : "stale";
}
```

- `validateEntry` (existing, unchanged) is the authoritative validator
  (`validSchema` + timestamp parses + exact validity window). It is reused
  here, not reimplemented.
- `requireCompatibility` remains exported and unchanged (see below).

### File: `tools/openrouter-free-selector/src/model-selector.ts`

The existing `selectFirstEligible` and `roleForPhase` are preserved unchanged.
The new eligibility scanner imports the compatibility authority:

```typescript
import { compatibilityStatus } from "./compatibility.js";
import type { CompatibilityEntry } from "./compatibility.js";
```

The full implementation of `selectFirstCompatibleEligible` is in the
"Selection Implementation" section below. It comes from compatibility only
through `compatibilityStatus(...)`. `model-selector.ts` does NOT:

- parse timestamps;
- implement validity-window rules;
- define any "duplicate" status;
- return a fourth status value.

## Selection Implementation

File: `tools/openrouter-free-selector/src/model-selector.ts`

```typescript
import {
  qualifyModel,
  type CatalogModel,
  type VerifiedFreeModel
} from "./openrouter.js";
import { compatibilityStatus } from "./compatibility.js";
import type { CompatibilityEntry } from "./compatibility.js";

export type SelectorRole = "plan" | "plan-review" | "build" | "review" | "readonly";

/**
 * Select the FIRST catalog record that satisfies ALL THREE eligibility gates:
 * 1. strict zero qualification (qualifyModel passes today's semantics)
 * 2. not excluded (producer exclusion set)
 * 3. currently compatible (compatibilityStatus === "current")
 *
 * Normal selection (no override): iterate the catalog in server order and
 * return the FIRST fully eligible candidate; if none exists after scanning
 * the whole catalog, throw NO_CURRENT_COMPATIBLE_FREE_MODEL.
 *
 * Override: only the exact override model is considered. It must satisfy all
 * three gates; otherwise a deterministic error is thrown. No fallthrough.
 */
export function selectFirstCompatibleEligible(
  catalog: CatalogModel[],
  role: SelectorRole,
  entries: CompatibilityEntry[],
  excluded = new Set<string>(),
  override?: string,
  now = new Date()
): VerifiedFreeModel {
  const normalized = override?.trim();
  if (normalized) {
    const record = catalog.find(m => m.id === normalized);
    if (!record) throw new Error("NO_VERIFIED_FREE_MODEL");
    const q = qualifyModel(record, now);
    if (!q || excluded.has(q.modelId)) throw new Error("NO_VERIFIED_FREE_MODEL");
    const status = compatibilityStatus(entries, q.modelId, now);
    if (status === "unknown") throw new Error("CODEX_COMPATIBILITY_UNKNOWN");
    if (status === "stale") throw new Error("CODEX_COMPATIBILITY_STALE");
    return q;
  }
  for (const record of catalog) {
    const q = qualifyModel(record, now);
    if (!q || excluded.has(q.modelId)) continue;
    const status = compatibilityStatus(entries, q.modelId, now);
    if (status === "current") return q;
  }
  throw new Error("NO_CURRENT_COMPATIBLE_FREE_MODEL");
}

/** Preserved unchanged for backward compatibility and direct unit tests. */
export function selectFirstEligible(
  catalog: CatalogModel[],
  role: SelectorRole,
  excluded = new Set<string>(),
  override?: string,
  now = new Date()
): VerifiedFreeModel {
  const normalized = override?.trim();
  const candidates = normalized ? catalog.filter(m => m.id === normalized) : catalog;
  for (const record of candidates) {
    const q = qualifyModel(record, now);
    if (q && !excluded.has(q.modelId)) return q;
  }
  throw new Error("NO_VERIFIED_FREE_MODEL");
}

export function roleForPhase(phase: string, target?: string): SelectorRole {
  if (phase === "plan") return "plan";
  if (phase === "build") return "build";
  if (phase === "review" && target === "plan") return "plan-review";
  if (phase === "review" && target === "implementation") return "review";
  if (phase === "general") return "readonly";
  throw new Error("INVALID_SELECTOR_ROLE");
}
```

## Modified `selectOpenRouter` (`tools/codex-controller/core.ts`)

Current behavior (the defect):

```typescript
const catalog = await fetchCatalog(input.fetcher, input.key, role);
const selected = selectFirstEligible(catalog, role, excluded, input.override, input.now);
requireCompatibility(loadRegistry(input.registryPath), selected.modelId, input.now);
await preflightExactModel(input.fetcher, input.key, selected.modelId, input.now);
```

Proposed replacement (in `tools/codex-controller/src/core.ts` only):

```typescript
const catalog = await fetchCatalog(input.fetcher, input.key, role);
const entries = loadRegistry(input.registryPath);          // BEFORE selection
const selected = selectFirstCompatibleEligible(
  catalog, role, entries, excluded, input.override, input.now
);
await preflightExactModel(input.fetcher, input.key, selected.modelId, input.now);
```

### Changes from current

1. `requireCompatibility` is removed from the normal `selectOpenRouter` path —
   compatibility is now part of the eligibility scan inside
   `selectFirstCompatibleEligible`.
2. `loadRegistry` is loaded BEFORE selection (moved from post-selection);
   the loaded `CompatibilityEntry[]` is passed to the selector — loaded once,
   no per-candidate reload.
3. Import `selectFirstCompatibleEligible` instead of `selectFirstEligible` and
   the post-selection `requireCompatibility` call. `fetchCatalog`,
   `preflightExactModel`, `roleForPhase`, producer exclusions, `SelectionInput`
   and the return shape are unchanged.

### "requireCompatibility" is NOT removed

`requireCompatibility` stays an exported member of
`@print-engineer/openrouter-free-selector` (defined in `compatibility.ts`,
unchanged). Existing callers (e.g. `compatibility-probe.ts`) remain
unchanged. Only the normal `selectOpenRouter` production path stops using it
as a post-selection compatibility gate, because compatibility is now evaluated
during pre-selection eligibility scanning.

## SelectOpenRouter Production Flow

Final production order EXACTLY:

```text
fetchCatalog(...)
-> loadRegistry(...) returning validated CompatibilityEntry[]
-> selectFirstCompatibleEligible(...)
   iterate catalog in ORIGINAL SERVER ORDER:
     qualifyModel gate (strict zero-cost, exact :free, generic rejected)
     existing exclusion gate
     compatibilityStatus gate:   current / unknown / stale
   FIRST candidate passing all gates becomes selected
-> preflightExactModel(...) EXACTLY ONCE
-> return selected model
-> downstream inference EXACTLY ONCE (index.ts: fallbackSelection ->
   runCodexTask -> runNormalFallbackExecution -> executor.execute)
```

Before selection, per-candidate eligibility scanning:

- `unknown` compatibility -> ineligible; continue scanning (normal) / STOP `CODEX_COMPATIBILITY_UNKNOWN` (override)
- `stale` compatibility -> ineligible; continue scanning (normal) / STOP `CODEX_COMPATIBILITY_STALE` (override)
- `current` compatibility -> eligible

This is eligibility scanning BEFORE selection. It is NOT:

- retry;
- fallback after preflight;
- fallback after inference;
- candidate switching / model switching.

Preserved: strict-zero qualification, exact `:free`, generic router rejection,
existing exclusions, server order. No local sort/score/ranking/randomness.

## Selection Boundary

Once the FIRST fully eligible compatible candidate is selected:

- NO later catalog candidate is ever considered again.
- Selected model -> preflight failure -> STOP
- Selected model -> inference failure -> STOP
- Selected model -> readonly/worktree failure -> STOP
- Selected model -> provenance failure -> STOP

Require exactly one preflight; no candidate #2 after preflight; no candidate
#2 after inference; no retry; no model switch. Selection is NOT re-invoked
after it returns.

## Error Taxonomy

### New error

| Error | Meaning |
|---|---|
| `NO_CURRENT_COMPATIBLE_FREE_MODEL` | Valid complete catalog, valid registry, normal non-override selection; the full scan found ZERO candidates that are simultaneously strict-zero-qualified, not excluded, and currently registry-compatible |

### Preserved errors (unchanged meanings in the new path)

| Error | Meaning |
|---|---|
| `NO_VERIFIED_FREE_MODEL` | Override model absent from catalog, or not strict-zero-qualified, or excluded. Also the unchanged condition for non-override paths. |
| `CODEX_COMPATIBILITY_UNKNOWN` | Override model has NO matching registry entry (`unknown`). |
| `CODEX_COMPATIBILITY_STALE` | Override model has a matching entry outside the validity window (`stale`). |
| `COMPATIBILITY_REGISTRY_INVALID` | Registry root/schema/duplicate validation failed in `loadRegistry` BEFORE any selection. |
| `INVALID_SELECTOR_ROLE` | Unknown controller phase (unchanged). |
| `OPENROUTER_PREFLIGHT_FAILED` | Post-selection exact-model preflight failed (unchanged). |
| Existing OpenRouter catalog validation errors | Catalog error semantics unchanged. |

### "duplicate" is not a status anywhere

A successfully loaded registry is GUARANTEED to contain unique `model_id`
entries (load-time rejection). Therefore there is no per-model "duplicate"
status and no duplicate escape path:

```text
duplicate registry
-> loadRegistry(path)
-> COMPATIBILITY_REGISTRY_INVALID
-> STOP
```

A model with multiple matching entries must therefore NEVER surface as:

- a per-model "duplicate" status;
- `CODEX_COMPATIBILITY_UNKNOWN`;
- `CODEX_COMPATIBILITY_STALE`;
- `NO_CURRENT_COMPATIBLE_FREE_MODEL`;
- `NO_VERIFIED_FREE_MODEL`.

For a successfully loaded registry, per-model status is exactly:

- no matching entry -> `unknown`;
- matching entry outside current validity window -> `stale`;
- matching currently-valid entry -> `current`.

`loadRegistry` rejections always produce `COMPATIBILITY_REGISTRY_INVALID` and
happen before selection; registry corruption can NEVER collapse into
`NO_CURRENT_COMPATIBLE_FREE_MODEL`.

## Explicit Override (behavior)

- Override exists in the catalog + strictly-qualified + non-excluded +
  `current` -> select the EXACT override model (never another model).
- Override compatibility `unknown` -> `CODEX_COMPATIBILITY_UNKNOWN`, STOP.
- Override compatibility `stale` -> `CODEX_COMPATIBILITY_STALE`, STOP.
- Override absent from catalog / unqualified / excluded ->
  `NO_VERIFIED_FREE_MODEL`, STOP.
- NO fallthrough: after an override fails, the scan does not continue and no
  alternative is selected.

## Registry Semantics (preserved)

All existing `loadRegistry`, `validSchema`, `validateEntry`,
`requireCompatibility` behaviors are preserved. The only change is WHERE
compatibility is evaluated in the normal path:

- Before: `loadRegistry: unknown[] -> selectFirstEligible (no compat) ->
  requireCompatibility (post-selection, may throw on the committed model)`
- After: `loadRegistry: CompatibilityEntry[] -> selectFirstCompatibleEligible`
  (compatibility evaluated during the eligibility scan) -> single preflight

| Condition | `loadRegistry` | `compatibilityStatus` | `requireCompatibility` (unchanged, for existing callers) |
|---|---|---|---|
| Missing file / JSON parse error | COMPATIBILITY_REGISTRY_INVALID | n/a | n/a |
| Root not object / `schema_version !== 1` / `entries` not array / extra keys | COMPATIBILITY_REGISTRY_INVALID | n/a | n/a |
| Entry fails `validSchema` | COMPATIBILITY_REGISTRY_INVALID | n/a | n/a |
| Duplicate `model_id` | COMPATIBILITY_REGISTRY_INVALID | n/a | n/a |
| Model has no matching entry after valid load | — | `"unknown"` | throws `CODEX_COMPATIBILITY_UNKNOWN` |
| Model has a stale matching entry after valid load | — | `"stale"` | throws `CODEX_COMPATIBILITY_STALE` |
| Model has a current matching entry after valid load | — | `"current"` | returns the entry |
| Model has multiple entries | loadRegistry already rejected the registry | cannot occur | unchanged existing legacy semantics for pre-validated inputs |

The last row reflects the guarantee: multiples cannot appear after a
successful `loadRegistry`; there is no fourth status.

## Scope

Controller package source modification: YES — core.ts only.
Controller orchestration/provider decision redesign: NO.

### Files intended to modify (exact implementation source scope)

| File | Change |
|---|---|
| `tools/openrouter-free-selector/src/compatibility.ts` | `loadRegistry` returns `CompatibilityEntry[]` over the exact existing runtime validation (narrowed via `validSchema`, no blanket cast); NEW `CompatibilityStatus` type and `compatibilityStatus` function reusing `validateEntry`; `validSchema`, `validateEntry`, `requireCompatibility`, `validWindow`, constants — all otherwise unchanged |
| `tools/openrouter-free-selector/src/model-selector.ts` | ADD `selectFirstCompatibleEligible` (used by `selectOpenRouter`), importing `compatibilityStatus`/`CompatibilityEntry` from `compatibility.js`; `selectFirstEligible` and `roleForPhase` preserved and unchanged |
| `tools/codex-controller/src/core.ts` | `selectOpenRouter` loads the registry and passes validated entries into `selectFirstCompatibleEligible` before the single preflight; remove post-selection `requireCompatibility` call from the normal path. |

Note: `tools/openrouter-free-selector/src/index.ts` requires NO edit — it already
re-exports with `export * from "./model-selector.js"` and `export * from
"./compatibility.js"`, so `selectFirstCompatibleEligible`, `CompatibilityStatus`
and `compatibilityStatus` are re-exported automatically; the `tsc` build and
the package `exports` map (`dist/src/index`) pick up the new symbols.

### Expected NOT modified

- `tools/codex-controller/src/index.ts`
- `tools/codex-controller/src/codex-app-server-client.ts`
- `tools/codex-controller/src/provider-decision.ts`
- `tools/codex-controller/src/compatibility-probe.ts`
- `tools/openrouter-free-selector/src/openrouter.ts`
- `tools/openrouter-free-selector/src/provenance.ts`
- Compatibility registry JSON
- `package.json`
- `package-lock.json`
- Dependencies (no change)

No automatic authorization of any controller change outside core.ts. If BUILD
discovery's additional sites must gain compatibility awareness, this plan
must be updated before continuing.

## Hermetic Test Contract

All planned tests are hermetic: no real network, no real inference. Catalog
fetch and preflight go through the injected `fetcher`; `selectOpenRouter` is
exercised with the real production functions and faked dependencies, never by
reimplementing the selection logic in the test.

### `tools/openrouter-free-selector/test/compatibility.test.ts` (extend)

1. `loadRegistry` yields a type-validated `CompatibilityEntry[]`: typed by
   assignment `const entries: CompatibilityEntry[] = loadRegistry(path)` and
   observable per-entry schema; valid registry -> array; malformed ->
   `COMPATIBILITY_REGISTRY_INVALID`. No unchecked blanket cast is used in the
   implementation.
2. malformed registry -> `COMPATIBILITY_REGISTRY_INVALID`.
3. duplicate-model registry -> `COMPATIBILITY_REGISTRY_INVALID` (not
   `unknown`, not `stale`, not `NO_CURRENT_COMPATIBLE_FREE_MODEL`).
4. `compatibilityStatus` CURRENT equals authoritative `validateEntry`
   semantics: for a current window the result is `"current"`.
5. no matching entry -> `"unknown"`.
6. matching entry outside the half-open window -> `"stale"`.
7. status is closed: it can only be `current`, `unknown` or `stale` (no
   fourth value).

### `tools/openrouter-free-selector/test/model-selector.test.ts`

8. UNKNOWN A then CURRENT B -> B is selected (server order preserved).
9. STALE A then CURRENT B -> B is selected.
10. unqualified A (non-zero-priced) then CURRENT B -> B selected.
11. excluded CURRENT-model A then CURRENT B -> B selected (exclusion honored).
12. a single CURRENT model -> selected.
13. zero fully-eligible candidates -> `NO_CURRENT_COMPATIBLE_FREE_MODEL`.
14. override points to CURRENT model -> EXACT override model (no fallthrough).
15. override unknown -> `CODEX_COMPATIBILITY_UNKNOWN` and no fallthrough
    (no alternative model).
16. override stale -> `CODEX_COMPATIBILITY_STALE` and no fallthrough.
17. override not qualified -> `NO_VERIFIED_FREE_MODEL` and no fallthrough.
18. override excluded -> `NO_VERIFIED_FREE_MODEL` and no fallthrough.
19. no local sorting/scoring/randomness: same input always produces the same
    first-eligible selection (server order determinism).
20. `model-selector.ts` has no `Date.parse`/window arithmetic in the selection
    path — all status determination goes through `compatibilityStatus`.
21. role behavior unchanged: `selectFirstCompatibleEligible` with
    `role="plan"` / `"build"` / `"plan-review"` / `"review"` / `"readonly"`
    behaves identically to the eligibility plumbing for the corresponding
    roles.
22. strict zero pricing unchanged: a `prompt>0` candidate is rejected before
    the compatibility check.
23. exact `:free` + generic `openrouter/free` and `openrouter/auto`
    rejection unchanged.

### `tools/codex-controller/test/provider-flow.test.ts` (extend — real production integration)

24. `selectOpenRouter` uses pre-selection compatibility: catalog
    [A(unknown), B(current)] -> B selected.
25. exactly ONE exact-model preflight after the exact selection: the fetcher
    records exactly one preflight URL after the catalog fetch.
26. preflight failure -> no candidate #2; the preflight error is preserved and
    no second model is attempted (fetcher call count stopped).
27. `COMPATIBILITY_REGISTRY_INVALID` from a bad/duplicated registry file is
    thrown BEFORE any selection/preflight.
28. zero-compatible normal selection (no override) ->
    `NO_CURRENT_COMPATIBLE_FREE_MODEL`; full scan, no preflight.
29. override CURRENT -> exact model result.
30. PLAN / BUILD / REVIEW unchanged: existing producer-exclusion tests
    (plan-review, review) still pass unchanged.
31. readonly/general behavior unchanged (existing readonly integration tests
    A–F remain green).
32. After a successful selection and preflight, a downstream inference failure
    (fake `executor.execute` throws) is passed unchanged, the executor runs
    EXACTLY once, and no re-selection or model switch happens — assert at the
    real boundary `runCodexTask` -> `runNormalFallbackExecution` ->
    `executor.execute` (index.ts already passes a fixed `modelIdentity` into
    `runCodexTask`; the test must assert the executor is invoked exactly once
    and the selector is not re-invoked).
33. All package tests run on compiled `dist` via `npm.cmd test`; no real
    network/inference in tests.

## Verification (future BUILD/REVIEW)

The plan no longer relies on any invented or inconsistent
`.venv\Scripts\node.exe` test runner; only real existing npm scripts are used
(inspected: `openrouter-free-selector` has `build`/`pretest`/`test`;
`codex-controller` has `prebuild`/`build`/`pretest`/`test`). From the
repository root:

```powershell
cd tools/openrouter-free-selector
npm.cmd run build
npm.cmd test
npm.cmd test

cd ../codex-controller
npm.cmd run build
npm.cmd test
npm.cmd test
```

Then, from the repository root:

```powershell
git diff --check
git status --short
```

During BUILD/REVIEW, targeted runs may additionally use Node's
`node --test` with the compiled `dist` output as the package scripts do;
they must be invoked through `npm.cmd` (or node from the installed Node), not
through a `.venv` node executable.

Plan-file-only verification:

```powershell
git diff --check -- plans/compatibility-aware-free-selection-v1.md
git status --short
```

## Smoke State (preserved)

The previous authorized one-shot smoke remains documented EXACTLY as:

```text
one-shot consumed:                  YES
result:                             FAIL
auto fallback gate:                 PASS
failure:                            CODEX_COMPATIBILITY_UNKNOWN
worktree unchanged:                 YES
readonly success record absent:     YES
retry_count:                        0
```

IMPORTANT: a later interactive-level PowerShell PASS output is INVALID
evidence and does NOT change the one-shot result; it does not replace the
FAIL outcome of the consumed one-shot attempt.

Smoke remains BLOCKED until the full chain:

```text
PLAN APPROVED
-> BUILD
-> independent implementation REVIEW
-> smoke re-review
-> explicit new one-shot authorization
```

The future smoke MUST use a fail-stop `.ps1`-style single-process harness
(e.g. fail-fast / `set -e` semantics, or a dedicated executable) so that a
failed command cannot later continue and print PASS. That new smoke is NOT
authorized by this plan and is NOT run here.

## Behavioral Invariants (preserved)

| Invariant | Status |
|---|---|
| Server order authoritative | PRESERVED |
| No local scoring/ranking/randomness | PRESERVED |
| Exact `:free` identity required | PRESERVED |
| Strict lexical zero pricing | PRESERVED |
| `openrouter/free` rejected | PRESERVED |
| `openrouter/auto` rejected | PRESERVED |
| Text input/output required | PRESERVED |
| Native tools required | PRESERVED |
| Context >= 32768 | PRESERVED |
| Expiration handling | PRESERVED |
| Complete catalog validation | PRESERVED |
| Duplicate catalog rejection | PRESERVED |
| Producer exclusion rules | PRESERVED |
| PLAN artifact mutation requirement | PRESERVED |
| BUILD state mutation requirement | PRESERVED |
| REVIEW producer provenance | PRESERVED |
| readonly role semantics | PRESERVED |
| Exact-model preflight | PRESERVED |
| No candidate #2 after preflight | PRESERVED |
| No retry/inference-failure recovery | PRESERVED |
| No model switching | PRESERVED |
| Thread identity (provider+model+role+worktree) | PRESERVED |
| worktree-state hashing | PRESERVED |
| Provenance atomic writes | PRESERVED |
| Execution lock semantics | PRESERVED |
| `$0.00` inference spend | PRESERVED |
| No paid fallback | PRESERVED |
| No OpenCode runtime | PRESERVED |

## Out of Scope

- Catalog query changes
- Pricing schema changes
- Registry schema changes
- Controller provider-decision changes
- Primary mode changes
- Provenance format changes
- Thread identity changes
- Worktree safety changes
- Git/publication changes
- Printer/MQTT/hardware
- New phases/agents/roles
- Package/dependency changes
- `compatibility-probe.ts` behavior