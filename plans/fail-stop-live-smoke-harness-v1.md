# Fail-Stop Live Smoke Harness v1

Status: APPROVED — implementation is authorized under this approved plan. This approval does NOT authorize any smoke.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a single-process, fail-stop, one-shot PowerShell smoke harness
that can safely execute exactly ONE future explicitly-authorized automatic
OpenRouter fallback smoke against the normal `auto` provider path, with
structured evidence that is machine-correlated and fail-closed.

**Architecture:** A dedicated `.ps1` orchestrator (`run-smoke.ps1`) is the ONE
controlling harness process with `$ErrorActionPreference='Stop'`, explicit
`$LASTEXITCODE` validation for native git calls, and UTF-8-safe child
capture. "Single process" means ONE controlling harness process plus controlled
child subprocesses (git, node control module, and exactly ONE production
controller/Codex task subprocess) — never a pasted interactive sequence. All
decision logic (structured authorization validation/claim, evidence
correlation, success/failure predicates, secret redaction) lives in a pure,
hermetic-testable TypeScript control module (`smoke-control.ts`) invoked by the
`.ps1` as controlled children. Child stdout/stderr are captured under harness
control via `System.Diagnostics.Process` with `RedirectStandardOutput` /
`RedirectStandardError` and explicit strict UTF-8 (no BOM) decoding; ALL
machine-readable files are persisted byte-exact UTF-8 without BOM via
`[System.IO.File]::WriteAllText(..., New-Object System.Text.UTF8Encoding($false))`.
The harness lives in the MAIN repository checkout (`tools/codex-controller/smoke/`),
never inside the target linked worktree; the one production task it launches is
the existing normal controller CLI `node dist/src/index.js` (no special flags)
with `CODEX_PROVIDER_MODE=auto`, exercising the compatibility-aware selection,
single preflight, single inference, readonly worktree guard, and
`ReadOnlyExecutionRecord` provenance changed by the approved
`compatibility-aware-free-selection-v1`.

**Tech Stack:** PowerShell 5.1 (orchestrator; `System.Diagnostics.Process` +
`System.Text.UTF8Encoding($false)`), Node.js + TypeScript (control module and
hermetic tests via `node --test`), git (evidence capture), existing
`@print-engineer/openrouter-free-selector` provenance/registry modules. No new
dependencies.

**Spec:** This plan implements the fail-stop harness blocker recorded in the
SMOKE READINESS RE-REVIEW (`NOT READY`), and the "Future Smoke Harness
Correction" section of `plans/auto-provider-fallback-live-smoke-v1.md`. It
supersedes `plans/auto-provider-fallback-live-smoke-v1.md` as the ACTIVE smoke
procedure; the old plan remains as the authoritative historical record of the
CONSUMED FAILED one-shot attempt. It does NOT authorize any smoke.

## Global Constraints

- PLAN ONLY. No harness implementation, no live smoke, no OpenRouter/Codex
  execution, no `--select-only`, no `--compatibility-probe`, no registry
  mutation, no hardware/MQTT, no stage/commit/push in this planning step.
- The future harness MUST be one top-level PowerShell process (one
  `powershell -NoProfile -File` invocation), never a pasted interactive command
  sequence. Child processes are allowed ONLY as controlled subprocesses of that
  one harness process.
- The harness MUST NOT be created inside the target smoke worktree, and MUST
  not write any file into the target worktree.
- PASS must exist in exactly ONE success-only terminal control path.
- A failed mandatory command must stop the sequence, produce
  `SMOKE_RESULT=FAIL`, exit non-zero, prevent all later mandatory actions, and
  make a later PASS impossible.
- Exactly ONE production task maximum; no retry loop, no candidate #2, no
  model switching, no second task execution.
- Never print or persist `OPENROUTER_API_KEY`, provider API keys, credentials,
  or secrets.
- Never clean/revert the target worktree automatically (destroys evidence).
- No registry mutation is authorized merely to make the smoke pass; the
  production selector remains server-order authoritative and the registry is
  evaluated at smoke runtime. `nvidia/nemotron-3.5-lightning:free` is NOT
  hardcoded as the required selection.
- Default authorization state: NOT AUTHORIZED. Authorization is one-shot,
  target-bound, and claimed atomically; a failure does not authorize retry;
  PASS does not authorize another run.
- Machine-readable evidence encoding is dictated by the harness: strict UTF-8
  WITHOUT BOM everywhere. PowerShell 5.1 defaults (`>`/`2>` redirection →
  UTF-16LE; `Set-Content -Encoding utf8` → UTF-8 WITH BOM; `Out-File`
  defaults) are FORBIDDEN for anything later parsed by Node.

---

## 1. Problem

The SMOKE READINESS RE-REVIEW returned `NOT READY` for the future automatic
OpenRouter fallback live smoke with exactly one blocking reason: **no
safe single-process fail-stop smoke harness exists**.

The historical failure mode (recorded in `plans/auto-provider-fallback-live-smoke-v1.md`)
was that the smoke commands were pasted into an interactive PowerShell session;
a thrown statement returned control to the interactive prompt, and the later
pasted statements continued executing, printing a bogus `LIVE_SMOKE_RESULT=PASS`
block with empty `actual_provider`, `model_id`, `phase`, `role`, `completion`
and no provenance record. That trailing PASS is INVALID evidence and does not
change the consumed one-shot result. The harness must exist specifically so
this cannot happen again.

Additional correctness requirements identified in the independent review and
incorporated here:

- Encoding of machine-readable evidence on Windows PowerShell 5.1 must be
  byte-exact UTF-8 without BOM (see Section 8), otherwise Node could never read
  the harness's own evidence back (UTF-16 NUL bytes / JSON BOM).
- The smoke `Evidence` TypeScript contract and its unit-test examples must be
  ONE consistent strict-TypeScript-compatible contract (no `controllerStdout`
  property when the type defines `controllerStdoutPath`; `baseEvidence()` must
  satisfy every required field).
- The one-shot authorization must be a structured, bound payload
  (task/worktree/repo/expiry/purpose), validated and atomically claimed only
  AFTER all safe setup finished, so ordinary setup failure cannot waste the
  one-shot authorization.
- Worktree setup must match the real controller `ensureWorktree` semantics
  (fetch, existing worktree reuse, existing-branch `add` vs fresh `add -b`).
- `preflight_count` / `inference_count` / `retry_count` are DERIVED facts
  (structurally guaranteed by the reviewed production contract plus the
  observed single launch), NOT directly emitted production counters; the
  evidence distinguishes observed facts from derived facts by name.

## 2. Existing Architecture / Evidence (re-verified)

All facts below were re-verified by source inspection for this review/corrected
plan.

### 2.1 Repository and worktrees

- Repo root: `C:/Users/Viktor/Desktop/projects/3d-printer-ai-assistant`
  (HEAD `844cdfc`, branch `master`); Git common dir
  `.../3d-printer-ai-assistant/.git`.
- `git worktree list`:
  - main checkout (master)
  - `C:/Users/Viktor/Desktop/projects/.codex-worktrees/issue-1`
  - `C:/Users/Viktor/Desktop/projects/.codex-worktrees/issue-3`
- Controller default worktree root:
  `resolve(process.env.CODEX_WORKTREE_ROOT ?? "../../../.codex-worktrees")`
  from cwd `tools/codex-controller` → `C:/Users/Viktor/Desktop/projects/.codex-worktrees`;
  target worktree → `resolve(worktreeRoot, taskKey)`. Worktrees are therefore
  EXTERNAL to the main checkout.

### 2.2 Production entry point (the real automatic fallback path)

`tools/codex-controller/src/index.ts` (verified):

- `dispatchControllerCommand(flags, env, deps)`: normal execution requires
  `flags.length === 0`. ONLY recognized flags: `--provider-status`,
  `--compatibility-probe`, `--select-only`; combinations/duplicates rejected.
- Normal path: `resolveExecutionProvider(auto)` → `readPrimaryStatus()` →
  `decideProvider` (openrouter-free unless primary fully available) →
  `runManualMode` (when `CODEX_TASK_KEY`) → `fallbackSelection` →
  `runCodexTask`.
- `runControllerCli` returns `0` on success and `1` on any failure; `main()`
  sets `process.exitCode`. A child-process exit code is therefore a mandatory,
  machine-readable terminal result.
- `fallbackSelection(worktree, phase, env)`: requires `OPENROUTER_API_KEY`
  (else `OPENROUTER_AUTH_MISSING`); `override` for `general` = `env.MODEL_REVIEW`
  (unused in practice); default registry path
  `resolve("../openrouter-free-selector/config/codex-compatible-free-models-v1.json")`.
- `fallbackTask(worktree, env)`: `const path = resolve(worktree, value)` — an
  ABSOLUTE `MODEL_TASK_FILE` resolves outside the worktree, so the task file
  never needs to be inside the target worktree; enforces non-empty,
  `<= 262144` bytes, no BOM, valid UTF-8 (else `INVALID_TASK_FILE`).
- `runCodexTask` for `providerMode==="openrouter-free"` and `phase==="general"`:
  - captures `git status --short` of the worktree BEFORE execution and AFTER;
    any difference throws `READONLY_WORKTREE_MUTATED`;
  - writes a `ReadOnlyExecutionRecord` via `writeReadOnlyExecutionRecord`
    (`index.ts:124`) → `readonlyExecutionPaths(worktree, taskKey, worktree)`;
  - persists (when fresh) thread state
    `state.threads[taskKey] = {schemaVersion:2, threadId, branch, worktree,
    providerMode, modelIdentity, role}` into `CODEX_STATE`.
- `tools/codex-controller/src/core.ts` `selectOpenRouter` (per approved
  `compatibility-aware-free-selection-v1`): `fetchCatalog` → `loadRegistry`
  BEFORE selection → `selectFirstCompatibleEligible` (server order; strict zero
  qualification + exclusion + `compatibilityStatus === "current"`) → EXACTLY
  ONE `preflightExactModel` → returns the selected model → EXACTLY ONE
  downstream inference. No candidate #2, no retry, no model switch.
- `tools/openrouter-free-selector/src/provenance.ts`:
  - `ReadOnlyExecutionRecord` shape + `validateReadOnlyExecutionRecord`;
  - deterministic path:
    `<git-common-dir>/print-engineer/model-runner/selector-v1/readonly-executions/<sha256(taskKey + "|" + worktreePath)>.json`
    via `readonlyExecutionPaths(repo, taskKey, worktreePath)`.

### 2.3 Controller `ensureWorktree` semantics (verified `index.ts:202–235`)

```text
git <repo> fetch origin <base>
worktree = resolve(worktreeRoot, taskKey)            # root dir creation
if <worktree>/.git exists:
    try git -C <worktree> rev-parse --is-inside-work-tree | reuse
    else throw "Existing worktree path is not a valid Git worktree"
branchExists = (git <repo> show-ref --verify --quiet refs/heads/codex/<taskKey> exit 0)
if branchExists: git <repo> worktree add <worktree> <branch>
else:            git <repo> worktree add -b <branch> <worktree> origin/<base>
```

`validateExistingLinkedWorktree` (`index.ts:147`) requires: path exists;
`rev-parse --show-toplevel` resolves to the exact worktree path; `--git-dir`
differs from `--git-common-dir` (linked); the worktree common dir equals the
repo common dir; no secret files
(`.env`, `.env.local`, `config/config.local.yaml`).

### 2.4 Registry currentness (runtime, never hardcoded)

The registry currently contains exactly one entry
(`nvidia/nemotron-3.5-lightning:free`, validated `2026-08-20T17:32:10.368Z`,
valid until `2026-09-19T17:32:10.368Z`). The harness MUST NOT hardcode this
entry. At smoke runtime the harness validates the registry as it exists
(`loadRegistry`); the production selector chooses the first currently compatible
server-order candidate. If no candidate is currently compatible, production
fails (`NO_CURRENT_COMPATIBLE_FREE_MODEL` / `COMPATIBILITY_REGISTRY_INVALID`) →
harness FAIL. No registry mutation.

### 2.5 Environment/controller constraints (verified)

- `LOCALAPPDATA` required (else `OPENROUTER_CODEX_HOME_INVALID`); the
  controller uses `%LOCALAPPDATA%\print-engineer-codex\openrouter-home-v1` as
  its isolated `CODEX_HOME`. The harness uses
  `%LOCALAPPDATA%\print-engineer-codex\smoke\` (authorization, evidence, smoke
  controller-state) — same base the controller already uses, external to repo
  and worktree.
- `fallbackEnvironment` allowlists `OPENROUTER_API_KEY` and `LOCALAPPDATA`;
  Bambu/hardware env vars are removed and `CODEX_HOME` is overwritten.
- No smoke-only CLI flag exists in the controller and none may be added.

### 2.6 Observed vs derived counters (the evidence language)

- DIRECTLY OBSERVED (by the harness): exactly ONE child launch
  (`production_launches = 1`, counted by the harness itself); one child exit
  code; one model identity committed across stdout/state/record; one
  ReadOnly record; thread identity; worktree pre/post git state; registry
  identity.
- DERIVED (structurally guaranteed by the verified production contract plus the
  observed single launch): the production selector performs EXACTLY ONE
  preflight per committed model and EXACTLY ONE inference (`selectOpenRouter`
  → single `preflightExactModel` → single downstream `executor.execute`; the
  implementation has no loops):
  - `preflight_count_derived = 1`
  - `inference_count_derived = 1`
  - `retry_count_derived = production_launches - 1 = 0`
- The `_derived` suffix is mandatory so no fictional emitted counter is implied.

## 3. Harness Location

### 3.1 Where the harness lives

**`tools/codex-controller/smoke/`** inside the MAIN repository checkout:

- `run-smoke.ps1` (controlling orchestrator)
- `smoke-control.ts` (decision, authorization, evidence)
- `smoke-control.test.ts` and `smoke-harness.test.ts`
- `task.txt` (read-only smoke task, passed by ABSOLUTE `MODEL_TASK_FILE`)

### 3.2 Repository under test / target worktree

- Repository under test: the main checkout (source of `tools/codex-controller`,
  `tools/openrouter-free-selector`, the registry). The harness runs the
  controller's compiled `dist` from this checkout (`cwd = tools/codex-controller`).
- Target worktree: a dedicated linked worktree at
  `C:/Users/Viktor/Desktop/projects/.codex-worktrees/<CODEX_TASK_KEY>`, created
  by controller-equivalent `ensureWorktree` semantics during harness setup
  (Section 9), before baseline capture.

### 3.3 Why the harness cannot contaminate the target-worktree check

1. The target worktree is a SEPARATE Git checkout (its own `--git-dir`); files
   placed in the main checkout (incl. `tools/codex-controller/smoke/`) never
   appear in the target worktree's `git status --short`.
2. `MODEL_TASK_FILE` absolute path points into the harness dir; `fallbackTask`
   (`resolve(worktree, value)`) for absolute values reads outside the worktree;
   nothing is written into the target worktree by the harness.
3. Authorization, evidence, smoke controller-state live in
   `%LOCALAPPDATA%\print-engineer-codex\smoke\` — outside both repo and worktree.
4. The harness captures `git status --short` on ONLY the target worktree
   before/after and requires exact equality; cleanup is forbidden.

### 3.4 Tracked location

- Harness `.ps1`, `.ts`, `task.txt` → repository-tracked.
- `dist/` output → git-ignored (`tools/codex-controller/.gitignore`).
- Authorization, evidence, smoke controller-state → external
  (`%LOCALAPPDATA%\print-engineer-codex\smoke\`).
- Target smoke worktree → external
  (`C:/Users/Viktor/Desktop/projects/.codex-worktrees/<taskKey>`).

### 3.5 Exact target identification + validation

Computed exactly as the controller: `resolve(CODEX_WORKTREE_ROOT ?? "<repo>/../.codex-worktrees", CODEX_TASK_KEY)`.
The harness validates the target BEFORE and AFTER (mirroring
`validateExistingLinkedWorktree`):

- path exists; `git -C <wt> rev-parse --show-toplevel` === canonical worktree
  path;
- `git -C <wt> rev-parse --path-format=absolute --git-dir` !==
  `--git-common-dir` (linked);
- worktree common dir === repo common dir;
- no secret files (`.env`, `.env.local`, `config/config.local.yaml`);
- before/after identity equality (top, common dir, HEAD, branch).

## 4. Exact Future Implementation Files

### 4.1 Create

| File | Responsibility |
|---|---|
| `tools/codex-controller/smoke/run-smoke.ps1` | Controlling orchestrator; authorization validation/claim; setup (build, worktree per §9); pre/post evidence; exactly ONE production (or dry-run mock) child via `System.Diagnostics.Process`; single `SMOKE_RESULT` terminal path; non-zero exit on FAIL. |
| `tools/codex-controller/smoke/smoke-control.ts` | Pure decision logic: authorization payload types/validate/claim; `registryIdentity`; `redactSecrets`; extractors; correlation predicates; `evaluateSuccess`; subcommand CLI (`authorize`, `registry-identity`, `evaluate`). No network. |
| `tools/codex-controller/smoke/smoke-control.test.ts` | Hermetic unit tests (encoding + type + correlation; no network). |
| `tools/codex-controller/smoke/smoke-harness.test.ts` | Hermetic `.ps1 -DryRun` spawn tests over temp git repos (no network; dry-run only). |
| `tools/codex-controller/smoke/task.txt` | Bounded read-only task (read root `AGENTS.md`; concise; no edits/network/hardware). |

### 4.2 Modify

| File | Change |
|---|---|
| `tools/codex-controller/tsconfig.json` | Add `"smoke/**/*.ts"` to `include` (compiles to `dist/smoke/`; `rootDir: "."` unchanged). |
| `tools/codex-controller/package.json` | Extend `test` script to append `dist/smoke/smoke-control.test.js dist/smoke/smoke-harness.test.js`. |
| `tools/codex-controller/SMOKE_TEST.md` | Document the harness hermetic tests and that `run-smoke.ps1` is the SINGLE active live-smoke procedure (supersedes pasted interactive sequences). |

No controller source (`src/`), no selector source, no registry, no
`package.json` dependency, no `package-lock.json` change. No new dependency.

## 5. Authorization Mechanism (structured, target-bound, atomically claimed)

Replaces a plain token concept with a STRUCTURED payload (no secrets inside).

### 5.1 Payload schema

Pending payload path: `%LOCALAPPDATA%\print-engineer-codex\smoke\authorization`
(JSON, UTF-8 no BOM):

```json
{
  "schema_version": 1,
  "authorization_id": "<uuid-v4>",
  "purpose": "automatic-openrouter-fallback-smoke-v1",
  "expected_task_key": "smoke-readonly-v1",
  "expected_target_worktree": "C:/Users/Viktor/Desktop/projects/.codex-worktrees/smoke-readonly-v1",
  "expected_repository_root": "C:/Users/Viktor/Desktop/projects/3d-printer-ai-assistant",
  "issued_at": "<ISO-8601-UTC>",
  "expires_at": "<ISO-8601-UTC>"
}
```

Mandatory fields (validated by `validateAuthorization`): `schema_version === 1`,
`purpose === "automatic-openrouter-fallback-smoke-v1"` (exact), non-empty
`authorization_id` (UUID v4 pattern), non-empty `expected_task_key`, absolute
canonical `expected_target_worktree`, absolute canonical
`expected_repository_root`, valid ISO-8601 `issued_at` and `expires_at`. No
secrets anywhere in the payload.

### 5.2 Canonical binding rules (PowerShell and Node agree)

- Node normalizes each side with `fs.realpathSync` (fallback `path.resolve`)
  and removes ONE trailing path separator, then compares
  case-insensitively (`String.prototype.toLowerCase()` — the Windows
  convention). Raw string comparison is never used.
- The harness `.ps1` computes the same canonical candidates it passes into the
  `authorize` subcommand.
- Binding mismatch → `SMOKE_AUTHORIZATION_TARGET_MISMATCH` before any live
  task → FAIL, exit non-zero.
- Expiry: `issued_at <= now(UTC) < expires_at`; outside →
  `SMOKE_AUTHORIZATION_EXPIRED`. Malformed payload (schema / wrong purpose /
  missing field / non-JSON) → `SMOKE_AUTHORIZATION_INVALID`. All fail closed.

### 5.3 Two-phase authorization (SAFE SETUP vs LIVE COMMIT)

Phase A — SAFE SETUP / VALIDATION (the authorization file is never touched):

1. Prologue checks (LOCALAPPDATA, task key, build).
2. Worktree preparation (§9) and pre-smoke evidence capture.
3. Registry precheck (`registry-identity` fails →
   `COMPATIBILITY_REGISTRY_INVALID` → FAIL).

Failure in Phase A → `SMOKE_RESULT=FAIL`, exit non-zero, authorization
remains UNCONSUMED.

Phase B — LIVE COMMIT (immediately before the ONE live controller task):

```
1. node smoke-control authorize --auth <authPath> --task <taskKey>
                                --worktree <wt> --repo <repo>
   a. read + decode the pending payload (strict UTF-8 no BOM);
        missing file                       -> SMOKE_NOT_AUTHORIZED
        malformed/wrong schema/wrong purpose -> SMOKE_AUTHORIZATION_INVALID
   b. verify binding: taskKey, canonical worktree, canonical repo, purpose
   c. verify expiry window: issued_at <= now < expires_at
   d. ATOMIC CLAIM: unique renameSync(authPath, authPath.consumed.<ts>);
      only ONE racing harness may win (loser sees path gone
      -> SMOKE_NOT_AUTHORIZED)
   e. re-read the claimed payload and re-validate (schema/binding/expiry);
      compare sha256(claimed) vs sha256(before-claim) — any mismatch:
      SMOKE_AUTHORIZATION_CLAIMED_MISMATCH (fail closed, no live work)
   f. return { authorized: true, authorization_id, sha256 }
2. ONLY the invocation whose atomic claim succeeded continues to the ONE live
   task.
3. Once claimed, the authorization is consumed PERMANENTLY:
   - any later failure -> FAIL, exit non-zero, NO re-claim;
   - PASS does NOT create another authorization;
   - the consumed path never re-appears (post-claim failure never restores).
```

Guarantees:

- default state NOT authorized: file absent → `SMOKE_NOT_AUTHORIZED`;
- reuse impossible: original path destroyed by the atomic claim;
- a second harness invocation reusing the same (consumed) authorization sees
  the path missing → NOT AUTHORIZED → no live launch;
- authorization bound to the exact task/worktree/repo/expiry window;
- ordinary setup failure (Phase A) never wastes the authorization.

## 6. Authorization Instantiation (operator step at smoke time)

Executed ONLY at the explicit authorization step (never by BUILD/REVIEW/this
plan). The supervisor computes canonical paths and writes the structured
payload (Section 5.1) with a short validity window. Example operator command
(documented for BUILD; executed only when the authorization gate is reached):

```powershell
$tokenId = [guid]::NewGuid().ToString()
$payload = @{
  schema_version = 1
  authorization_id = $tokenId
  purpose = "automatic-openrouter-fallback-smoke-v1"
  expected_task_key = $env:CODEX_TASK_KEY
  expected_target_worktree = (Resolve-Path (Join-Path $env:CODEX_WORKTREE_ROOT $env:CODEX_TASK_KEY)).Path
  expected_repository_root = (Resolve-Path $env:CODEX_REPO).Path
  issued_at = (Get-Date).ToUniversalTime().ToString("o")
  expires_at = (Get-Date).ToUniversalTime().AddMinutes(30).ToString("o")
}
$authDir = Join-Path $env:LOCALAPPDATA "print-engineer-codex\smoke"
New-Item -ItemType Directory -Path $authDir -Force | Out-Null
[System.IO.File]::WriteAllText(
  (Join-Path $authDir "authorization"),
  ($payload | ConvertTo-Json),
  (New-Object System.Text.UTF8Encoding($false))
)
```

This step is NOT a harness function and is NOT automated. It writes the exact
one-shot bound payload; the harness then consumes it exactly once.

## 7. Fail-Stop Execution Flow — single process + controlled children

Invoked exactly as: `powershell -NoProfile -File run-smoke.ps1`
(optionally `-DryRun`).

```text
0. PROLOGUE (Phase A, no authorization involved)
   $ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
   checks: LOCALAPPDATA present + absolute; CODEX_TASK_KEY present;
   resolve RepoRoot, ControllerDir, ControlJs, TaskFile, ControlAuthDir.
   npm --prefix tools/codex-controller run build
   (any failure -> FAIL, exit 1; authorization unclaimed)

1. WORKTREE SETUP (Phase A, §9) — controller-equivalent ensureWorktree:
   fetch origin <base>; reuse existing worktree (validate) OR
   add / add -b by existing branch; validate identity + no secrets.
   Setup failure -> FAIL; authorization untouched.

2. PRE-SMOKE EVIDENCE (Phase A, §10) — timestamp, repoRoot, targetWorktree,
   worktree identity Before (top, common-dir, HEAD, branch, git status --short),
   taskKey, registry identity (sha256 + validated count; failure -> FAIL),
   expected-policy line, env-presence booleans (never values).
   Evidence persistence failure -> FAIL; authorization untouched.

3. AUTHORIZE + CLAIM (Phase B, §5.3) — immediately before the live task:
   atomic claim; failure -> deterministic SMOKE_RESULT=FAIL
   (SMOKE_AUTHORIZATION_*), NO live task.

4. EXACTLY ONE PRODUCTION TASK (live: `node dist/src/index.js` with no flags;
   dry: SMOKE_MOCK_CMD) — launched via System.Diagnostics.Process:
   UseShellExecute=false; RedirectStandardOutput=true;
   RedirectStandardError=true; StandardOutputEncoding = Strict UTF-8 no BOM;
   StandardErrorEncoding = Strict UTF-8 no BOM.
   WaitForExit; childExit = proc.ExitCode. NO RETRY LOOP.
   A child failure still captures post-evidence (diagnostic) but the decision
   MUST fail; PASS is unreachable.

5. POST-SMOKE EVIDENCE — worktree identity After (top, common-dir, HEAD,
   branch, status).

6. DECISION — evaluate evidence.json (UTF-8 no BOM) via the controlled node
   subcommand; a single terminal result.

7. TERMINAL — the ONLY PASS location:
   if decision.pass -> print SMOKE_RESULT=PASS; exit 0
   else             -> print SMOKE_RESULT=FAIL (+ failures); exit 1

Catch-all: any uncaught throw / native-git failure -> SMOKE_RESULT=FAIL,
failure=<message>, exit 1.
```

Fail-stop guarantees:

- `$ErrorActionPreference='Stop'` turns non-terminating cmdlet errors into
  terminating errors;
- every native git call is followed by `if ($LASTEXITCODE -ne 0) { throw }`
  EXCEPT the single production task child, whose non-zero exit is captured as
  evidence and MUST fail the evaluation;
- any throw jumps to the catch-all → `SMOKE_RESULT=FAIL`, exit 1;
- there is no loop, no paste, no second task path.

## 8. Encoding-Safe Child & File Contract (CRITICAL)

### 8.1 Rules for ALL machine-readable capture

- PowerShell 5.1 output redirection (`>`, `2>`) → UTF-16LE → FORBIDDEN.
- `Set-Content -Encoding utf8` / `Out-File` default → UTF-8 BOM / platform
  default → FORBIDDEN for Node-read artifacts.
- Node-side readers use `readFileSync(path, "utf8")` — a real UTF-8 decode —
  so produced files MUST be byte-exact UTF-8 WITHOUT BOM.

### 8.2 Harness captures child stdout/stderr

```powershell
function Start-CapturedChild {
  param([string]$FileName, [string[]]$Arguments, [string]$WorkingDirectory)
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $FileName
  $psi.Arguments = ($Arguments -join " ")
  $psi.WorkingDirectory = $WorkingDirectory
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.CreateNoWindow = $true
  $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
  $psi.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
  $proc = New-Object System.Diagnostics.Process
  $proc.StartInfo = $psi
  $outBuilder = New-Object System.Text.StringBuilder
  $errBuilder = New-Object System.Text.StringBuilder
  $outEv = Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived `
      -Action { if ($EventArgs.Data -ne $null) { [void]$Event.MessageData.AppendLine($EventArgs.Data) } } `
      -MessageData $outBuilder
  $errEv = Register-ObjectEvent -InputObject $proc -EventName ErrorDataReceived `
      -Action { if ($EventArgs.Data -ne $null) { [void]$Event.MessageData.AppendLine($EventArgs.Data) } } `
      -MessageData $errBuilder
  try {
    if (-not $proc.Start()) { throw "SMOKE_PROCESS_START_FAILED" }
    $proc.BeginOutputReadLine()
    $proc.BeginErrorReadLine()
    $proc.WaitForExit()
  } finally {
    Unregister-Event -SourceIdentifier $outEv.Name -ErrorAction SilentlyContinue
    Unregister-Event -SourceIdentifier $errEv.Name -ErrorAction SilentlyContinue
  }
  return @{ ExitCode = $proc.ExitCode; Stdout = $outBuilder.ToString(); Stderr = $errBuilder.ToString() }
}
```

- Both streams MUST be drained concurrently (event handlers above) to avoid
  pipe deadlock; `WaitForExit()` after `BeginOutputReadLine`/`BeginErrorReadLine`.
- `Start()` failure (e.g. node missing) → `SMOKE_LAUNCH_FAILED` → FAIL, no
  second launch.
- The child exit code (`ExitCode`) is the ONLY terminal result source;
  `ExitCode` null/abnormal kill → FAIL.
- The BUILD applies correct .NET-compatible argument quoting when constructing
  `Arguments` (the sample shows the properties, not every quoting edge).

### 8.3 Persistence rule

```powershell
function Write-Utf8NoBom {
  param([string]$Path, [string]$Content)
  [System.IO.File]::WriteAllText($Path, $Content, (New-Object System.Text.UTF8Encoding($false)))
}
```

Applies to: `controller.stdout.txt`, `controller.stderr.txt`, `evidence.json`,
`decision.stderr.txt`, and ANY intermediate structured file parsed by Node.

## 9. Worktree Setup (controller parity, §2.3)

In Phase A, exactly:

```powershell
# mirror index.ts ensureWorktree
Invoke-Native "SMOKE_FETCH_FAILED" git -C $RepoRoot fetch origin $BaseBranch
if (-not (Test-Path -LiteralPath (Join-Path $TargetWorktree ".git"))) {
  Invoke-Native "SMOKE_GIT_REF_FAILED" git -C $RepoRoot show-ref --verify --quiet "refs/heads/codex/$TaskKey"
  if ($LASTEXITCODE -eq 0) {
    # legit existing branch: add existing worktree
    Invoke-Native "SMOKE_WORKTREE_ADD_FAILED" git -C $RepoRoot worktree add $TargetWorktree "codex/$TaskKey"
  } else {
    # fresh branch from origin/base
    Invoke-Native "SMOKE_WORKTREE_ADD_FAILED" git -C $RepoRoot worktree add -b "codex/$TaskKey" $TargetWorktree "origin/$BaseBranch"
  }
}
```

- An already-existing legitimate worktree is VALIDATED (§3.5) and reused (same
  as the controller).
- Existing branch + no worktree → `worktree add` (no `-b`); fresh → `add -b`.
- After any creation/reuse: full identity validation (§3.5).
- Any setup failure → `SMOKE_WORKTREE_VALIDATION_FAILED` → FAIL, non-zero,
  authorization untouched.

Documented note: an interrupted previous smoke may leave a pre-existing smoke
worktree; the harness never cleans it; its content is part of the baseline and
before/after equality is still judged (equal baseline → equal after). The smoke
never repairs that baseline.

## 10. Evidence Capture Contract

### Pre-smoke (Phase A; before any claim)

- `timestamp` (ISO-8601 UTC)
- `repoRoot`, `targetWorktree` (canonical)
- worktree identity BEFORE + `worktreeStatusBefore` (`git status --short`)
- registry identity: `registryPath`, `registrySha256`, `registryEntryCount`
  (via `loadRegistry`; invalid → `COMPATIBILITY_REGISTRY_INVALID`)
- `requestedProviderMode: "auto"` + `expectedPolicy:
  "zero-cost|free|read-only|exactly-one-inference"`
- environment presence booleans (`openrouterKeyPresent`,
  `localAppDataPresent`); never values

### Auth (Phase B, post-request)

- `authorizationId`, `authorizationSha256` (payload hash — no secret in payload)

### Live task

- `production_launches = 1` (harness-counted child launches; MUST equal 1)
- `childExitCode`
- `controllerStdoutPath`, `controllerStderrPath` (UTF-8 no BOM files)
- `controllerStatePath`

### Post-smoke

- `worktreeTopAfter`, `worktreeCommonDirAfter`, `worktreeHeadAfter`,
  `worktreeBranchAfter`, `worktreeStatusAfter`

### Evaluated (fail-closed inside `evaluate`)

- selected model: stdout `[controller] ... model=<id>` === controller-state
  `modelIdentity` === record `model_id`; all three required; mismatch/missing →
  FAIL
- provider: stdout `provider=openrouter-free` (or state) === state
  `providerMode`; record `provider_id === "openrouter"`; else FAIL
- thread/session: state `threadId` required; stdout `[codex] thread <id>`, if
  present, must equal; both absent → FAIL
- worktree/session: state.`worktree === targetWorktree`; record.`worktree_path
  === targetWorktree`
- readonly archive at deterministic path:
  `readonlyExecutionPaths(worktree, taskKey, worktree).record`, read + validated
  via `loadReadOnlyExecutionRecord`; missing → FAIL (never "latest"/mtime)
- inference evidence: `childExitCode === 0` + `inferenceOccurred(stdout)` +
  record present
- derived: `preflight_count_derived=1`, `inference_count_derived=1`
  (see §2.6)
- observed: `retry_count_derived = production_launches - 1`, MUST equal 0

### Secret boundaries

- `OPENROUTER_API_KEY` values: never read, printed, or persisted; boolean
  presence only.
- All captured stdout/stderr and the assembled evidence pass through
  `redactSecrets` before any persistence or print (token / Bearer / `sk-or-`).
- Authorization payload contains no secret; only `authorizationSha256` is
  recorded/inspectable.

## 11. Success Contract (single PASS terminal path)

```text
auto fallback gate:                  PASS (requested auto + provider openrouter-free)
normal automatic selection:          PASS (no override/flags; server-ordered selection)
one committed model:                 YES
model correlation:                   PASS (3 sources agree, all present)
provider correlation:                PASS
thread/session identity:           PASS (state threadId present; consistent stdout)
worktree/session correlation:        PASS
preflight_count_derived:             1
inference_count_derived:             1
retry_count_derived:                 0
production_launches:                1
model switching:                     NO
model inference occurred:            YES
worktree unchanged:                  YES (before == after; identity stable)
readonly/provenance contract:        PASS
terminal execution result:          SUCCESS (childExitCode == 0)
all evidence unambiguous:           YES
```

Only when every predicate above succeeds may the harness print
`SMOKE_RESULT=PASS` and exit 0.

## 12. Failure Contract

Any mandatory failure → `SMOKE_RESULT=FAIL`, exit non-zero, no later smoke.
Failure conditions include at least:

- `SMOKE_NOT_AUTHORIZED`, `SMOKE_AUTHORIZATION_INVALID`,
  `SMOKE_AUTHORIZATION_TARGET_MISMATCH`, `SMOKE_AUTHORIZATION_EXPIRED`,
  `SMOKE_AUTHORIZATION_CLAIMED_MISMATCH`
- `SMOKE_LAUNCH_FAILED`, `SMOKE_PROCESS_START_FAILED`
- `SMOKE_BUILD_FAILED`, `SMOKE_FETCH_FAILED`,
  `SMOKE_WORKTREE_VALIDATION_FAILED`, `INVALID_TASK_KEY`,
  `OPENROUTER_CODEX_HOME_INVALID`
- `protocol`/`registry`/`session`: `catalog_validation_failure`,
  `registry_validation_failure`, `zero_current_compatible_free_candidates`,
  `preflight_failure`, `inference_failure`,
  `readonly_worktree_mutation`, `readonly_provenance_absent`
- correlation: `conflicting_model_ids`, `model_identity_missing`,
  `thread_session_identity_missing`, `thread_identity_conflict`,
  `worktree_session_correlation_failed`, `identity_ambiguity`
- safety: `retry_observed` (production_launches > 1),
  `unexpected_target_worktree_mutation`, `harness_internal_command_failure`

A failure never begins another smoke; a Phase-A failure never touches
authorization; a post-claim failure never restores the claimed file.

## 13. Worktree Safety

- Only read-only git operations are used during evidence capture; the only
  mutation of the target is the read-only-role live execution itself.
- cleanup/reset/stash/restore/checkout/revert are FORBIDDEN in the harness.
- `worktreeStatusBefore === worktreeStatusAfter` AND identity unchanged are
  REQUIRED for PASS; any difference → FAIL → STOP → exit non-zero; mutation
  evidence is preserved for review.

## 14. Secret Handling

- Boolean presence only for `OPENROUTER_API_KEY`; never the value.
- `redactSecrets` applied at every capture/persistence boundary.
- Authorization payload contains NO secret; only hash/ID in evidence.
- All evidence files are UTF-8-no-BOM under `%LOCALAPPDATA%`; nothing is
  tracked in the repository.

## 15. Hermetic Test Contract

No real network / OpenRouter / Codex / inference / hardware / registry mutation
in any test. `npm run build` (strict TypeScript) MUST pass before tests are run.

### 15.1 Type & evidence contract tests (`smoke-control.test.ts`)

- Compile: `tsc` strict build gate.
- `baseEvidence()` satisfies EVERY required `Evidence` field
  (including `recordPresent`, `productionLaunches`, `controllerStdoutPath`).
- `evaluateSuccess` reads ACTUAL FILE content via `controllerStdoutPath`
  (tests write mock stdout to a temp file; never inline `controllerStdout`).
- Failure predicates: `productionLaunches !== 1`; `childExitCode !== 0`;
  model missing/conflict; `recordPresent === false`; thread missing;
  `worktreeStatusBefore !== worktreeStatusAfter` or identity change;
  `compatibilityProbeUsed`; `selectOnlyUsed`.
- Encoding assertions: a `writeAllText`-style UTF-8-no-BOM file parses under
  Node; a stale UTF-16LE or BOM-bearing artifact FAILS a validity test.

### 15.2 Authorization tests (hermetic; temp files)

- absent → not authorized (no throw)
- wrong task key → `SMOKE_AUTHORIZATION_TARGET_MISMATCH`
- wrong worktree → mismatch
- wrong repo root → mismatch
- expired → `SMOKE_AUTHORIZATION_EXPIRED`
- malformed JSON / wrong purpose → `SMOKE_AUTHORIZATION_INVALID`
- atomic one-winner: two concurrent claims → exactly one wins; loser sees
  `SMOKE_NOT_AUTHORIZED`
- consumed auth reuse: rerun with same path → `SMOKE_NOT_AUTHORIZED`
- post-claim failure does not restore the authorization file (still absent)

### 15.3 Derived counters

- `evaluateSuccess` reports `production_launches === 1` and the derived
  `retry_count_derived`/`preflight_count_derived`/`inference_count_derived`
  when all predicates hold; any violation → FAIL.

### 15.4 Harness tests (`smoke-harness.test.ts`, dry-run only)

Spawns `run-smoke.ps1 -DryRun` against a temp real git repo + linked temp
worktree; a mock `.cmd` publishes controller-shaped stdout with configurable
exit code; `SMOKE_MOCK_CMD`, `SMOKE_AUTH_PATH`, and fixture repo/env points:

- failing mock → exit != 0, `SMOKE_RESULT=FAIL`, never `SMOKE_RESULT=PASS`;
  marker shows the mock ran exactly once.
- succeeding mock + consistent fixtures → exit 0, `SMOKE_RESULT=PASS` exactly
  once; marker one line.
- no auth fixture → `SMOKE_NOT_AUTHORIZED`; marker absent (mock never ran).
- same auth path after successful claim → `SMOKE_NOT_AUTHORIZED` again (no
  reuse).
- fake `OPENROUTER_API_KEY` echoed by the mock appears NOWHERE in harness
  stdout/stderr.
- worktree mutated between captures → `SMOKE_RESULT=FAIL` + `unexpected_target_worktree_mutation`; fixture NOT cleaned.
- ENCODING: fixture stdout containing non-ASCII (e.g. task text) round-trips
  unchanged; the persisted file parses under
  `readFileSync(path, "utf8")` with NO BOM / NO UTF-16 residues; controller
  markers remain parseable.
- dry-run safety: `-DryRun` uses only `SMOKE_MOCK_CMD`; the real live command
  (`node dist/src/index.js`) is executed ONLY when `-DryRun` is absent AND an
  authorization was claimed (dry-run can never become live).

## 16. Verification (future BUILD / REVIEW)

Hermetic only (no live smoke, no OpenRouter/Codex):

```powershell
cd tools/codex-controller
npm.cmd run build        # strict TypeScript must pass
npm.cmd test             # pretest(build) + node --test incl. both smoke suites
```

Then from the repo root:

```powershell
git diff --check -- plans/fail-stop-live-smoke-harness-v1.md
git status --short
```

BUILD/REVIEW never runs `run-smoke.ps1` without `-DryRun`; never creates or
consumes a real authorization; never launches a live task.

## 17. Forbidden Operations (during the whole lifecycle)

- any live smoke / normal Codex execution / `--select-only` /
  `--compatibility-probe` / registry mutation / hardware/MQTT
- modifications to controller production source (`src/`), selector source, the
  registry JSON, or package dependencies
- adding smoke logic to production controller source (harness is test/operator
  tooling only)
- pasted interactive smoke sequences — anywhere, ever
- creating or consuming a live authorization outside the explicit gate

## 18. Previous Smoke History (preserved exactly)

```text
one-shot consumed:                  YES
result:                             FAIL
auto fallback gate:                 PASS
failure:                            CODEX_COMPATIBILITY_UNKNOWN
worktree unchanged:                 YES
readonly success provenance absent: YES
retry_count:                        0
```

The later interactive PowerShell `LIVE_SMOKE_RESULT=PASS` output is
INVALID EVIDENCE and does not change the consumed one-shot result.

## 19. Future Gate Sequence (unchanged)

```text
HARNESS PLAN APPROVED
-> HARNESS BUILD
-> independent HARNESS REVIEW
-> SMOKE READINESS RE-REVIEW
-> explicit NEW one-shot authorization
-> exactly ONE smoke
```

Creation of the authorization file happens ONLY at the explicit authorization
step (Section 6) by an authorized supervisor. This plan does NOT authorize or
perform any smoke; `PLAN`, `BUILD`, `REVIEW`, `READINESS` never authorize a
smoke. Only the separate explicit one-shot authorization does.

The future live smoke runs:

```powershell
powershell -NoProfile -File tools\codex-controller\smoke\run-smoke.ps1
```

---

## Tasks

### Task 1: Wiring + `smoke-control.ts` (strict TypeScript contract)

**Files:** `tools/codex-controller/tsconfig.json`, `tools/codex-controller/package.json`
modified; create `tools/codex-controller/smoke/smoke-control.ts`,
`tools/codex-controller/smoke/smoke-control.test.ts`.

- [ ] **Step 1** — tsconfig + package.json (exact same as §4.2).
- [ ] **Step 2** — write `smoke-control.test.ts` (see §15.1, §15.2, §15.3);
  FIRST write the failing tests, then the implementation.
- [ ] **Step 3** — run `npm.cmd run build` → FAIL (module missing), proving the
  tests are real.
- [ ] **Step 4** — implement `smoke-control.ts` (contract below).
- [ ] **Step 5** — run `npm.cmd run build` then `node --test dist/smoke/smoke-control.test.js`;
  full `npm.cmd test` green.
- [ ] **Step 6** — commit (scoped).

`smoke-control.ts` (reference implementation sketch — the BUILD writes the
complete typed module with the tests first):

```ts
import { createHash } from "node:crypto";
import { existsSync, lstatSync, readFileSync, realpathSync, renameSync } from "node:fs";
import { resolve } from "node:path";
import {
  loadRegistry, readonlyExecutionPaths, loadReadOnlyExecutionRecord,
} from "@print-engineer/openrouter-free-selector";

export interface Evidence {
  timestamp: string; repoRoot: string; targetWorktree: string;
  worktreeTopBefore: string; worktreeCommonDirBefore: string;
  worktreeHeadBefore: string; worktreeBranchBefore: string;
  worktreeStatusBefore: string; worktreeTopAfter: string;
  worktreeCommonDirAfter: string; worktreeHeadAfter: string;
  worktreeBranchAfter: string; worktreeStatusAfter: string;
  taskKey: string; registryPath: string; registrySha256: string;
  registryEntryCount: number; requestedProviderMode: string;
  childExitCode: number; controllerStdoutPath: string;
  controllerStderrPath: string; controllerStatePath: string;
  stateModelIdentity?: string; stateProviderMode?: string; stateRole?: string;
  stateThreadId?: string; stateWorktree?: string;
  recordModelId?: string; recordProviderId?: string; recordWorktree?: string;
  recordPresent: boolean;                // REQUIRED
  productionLaunches: number;            // REQUIRED (harness-counted)
  authorizationId?: string; authorizationSha256?: string;
  selectOnlyUsed: boolean; compatibilityProbeUsed: boolean;
}

export interface AuthorizationPayload {
  schema_version: number; authorization_id: string; purpose: string;
  expected_task_key: string; expected_target_worktree: string;
  expected_repository_root: string; issued_at: string; expires_at: string;
}

export function canonicalPath(p: string): string {
  let resolved = p;
  try { resolved = realpathSync(p); } catch { /* fall back below */ }
  return resolve(resolved).replace(/[\\/]+$/, "");
}

export function pathsEqual(a: string, b: string): boolean {
  return canonicalPath(a).toLowerCase() === canonicalPath(b).toLowerCase();
}

export function sha256Text(text: string): string {
  return createHash("sha256").update(text).digest("hex");
}

export function redactSecrets(text: string, secrets: readonly string[]): string {
  let out = text;
  for (const secret of secrets) if (secret) out = out.split(secret).join("[REDACTED]");
  return out
    .replace(/Bearer\s+[A-Za-z0-9._~+/=-]+/g, "Bearer [REDACTED]")
    .replace(/sk-or-[A-Za-z0-9-]+/g, "sk-or-[REDACTED]");
}

export function extractModelId(stdout: string): string | undefined {
  const m = /\[controller\][^\n]*\bmodel=([^\s]+)/.exec(stdout);
  return m?.[1];
}
export function extractProviderMode(stdout: string): string | undefined {
  const m = /\[controller\][^\n]*\bprovider=(primary|openrouter-free)/.exec(stdout);
  return m?.[1];
}
export function extractThreadId(stdout: string): string | undefined {
  const m = /\[codex\] thread\s+(\S+)/.exec(stdout);
  return m?.[1];
}
export function inferenceOccurred(stdout: string): boolean {
  return /\[codex\] turn completed/.test(stdout) || /^Codex:/m.test(stdout);
}

export type Correlation = "ok" | "conflict" | "missing";
export function correlateModelIds(values: readonly (string | undefined)[]): Correlation {
  const present = values.filter((v): v is string => typeof v === "string" && v.length > 0);
  if (present.length === 0 || present.length !== values.length) return "missing";
  return present.every((v) => v === present[0]) ? "ok" : "conflict";
}

export function validateAuthorization(
  authPath: string,
  context: { taskKey: string; targetWorktree: string; repoRoot: string; now?: Date },
): AuthorizationPayload {
  const raw = readFileSync(authPath, "utf8");
  const parsed = JSON.parse(raw) as Record<string, unknown>;
  const p = parsed as unknown as AuthorizationPayload;
  const required: (keyof AuthorizationPayload)[] = [
    "schema_version", "authorization_id", "purpose", "expected_task_key",
    "expected_target_worktree", "expected_repository_root", "issued_at", "expires_at",
  ];
  for (const k of required) if (!(k in parsed)) throw new Error("SMOKE_AUTHORIZATION_INVALID");
  if (p.schema_version !== 1) throw new Error("SMOKE_AUTHORIZATION_INVALID");
  if (p.purpose !== "automatic-openrouter-fallback-smoke-v1") throw new Error("SMOKE_AUTHORIZATION_INVALID");
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(p.authorization_id))
    throw new Error("SMOKE_AUTHORIZATION_INVALID");
  if (p.expected_task_key !== context.taskKey) throw new Error("SMOKE_AUTHORIZATION_TARGET_MISMATCH");
  if (!pathsEqual(p.expected_target_worktree, context.targetWorktree)) throw new Error("SMOKE_AUTHORIZATION_TARGET_MISMATCH");
  if (!pathsEqual(p.expected_repository_root, context.repoRoot)) throw new Error("SMOKE_AUTHORIZATION_TARGET_MISMATCH");
  const issued = Date.parse(p.issued_at), expiry = Date.parse(p.expires_at);
  if (Number.isNaN(issued) || Number.isNaN(expiry)) throw new Error("SMOKE_AUTHORIZATION_INVALID");
  const now = (context.now ?? new Date()).getTime();
  if (!(issued <= now && now < expiry)) throw new Error("SMOKE_AUTHORIZATION_EXPIRED");
  return p;
}

export function claimAuthorization(
  authPath: string,
  context: { taskKey: string; targetWorktree: string; repoRoot: string; now?: Date },
): { authorized: boolean; authorizationId?: string; sha256?: string } {
  if (!existsSync(authPath)) return { authorized: false };
  const st = lstatSync(authPath);
  if (!st.isFile() || st.isSymbolicLink()) throw new Error("SMOKE_AUTHORIZATION_INVALID");
  const before = readFileSync(authPath, "utf8");
  const p = validateAuthorization(authPath, context);              // (b)+(c) pre-claim
  const claimedPath = `${authPath}.consumed.${Date.now()}`;
  renameSync(authPath, claimedPath);                               // (d) atomic claim
  const after = readFileSync(claimedPath, "utf8");                 // (e) re-read
  if (after !== before) throw new Error("SMOKE_AUTHORIZATION_CLAIMED_MISMATCH");
  validateAuthorization(claimedPath, context);                     // (e) re-validate claimed
  return { authorized: true, authorizationId: p.authorization_id, sha256: sha256Text(after) };
}

export interface SuccessDecision { pass: boolean; failures: string[] }

export function evaluateSuccess(evidence: Evidence): SuccessDecision {
  const failures: string[] = [];
  const require = (ok: boolean, name: string) => { if (!ok) failures.push(name); };
  if (evidence.selectOnlyUsed) failures.push("select_only_forbidden");
  if (evidence.compatibilityProbeUsed) failures.push("compatibility_probe_forbidden");
  require(evidence.requestedProviderMode === "auto", "auto_gate_failed");
  require(evidence.childExitCode === 0, "terminal_execution_result_not_success");
  require(evidence.productionLaunches === 1, "production_task_invocation_count_not_1");
  const stdout = readFileSync(evidence.controllerStdoutPath, "utf8");
  const provider = extractProviderMode(stdout) ?? evidence.stateProviderMode;
  require(provider === "openrouter-free", "auto_gate_failed");
  require(evidence.stateProviderMode === "openrouter-free", "auto_gate_failed");
  require(evidence.recordProviderId === "openrouter", "provider_model_correlation_failed");
  const modelOut = extractModelId(stdout);
  const modelCorr = correlateModelIds([modelOut, evidence.stateModelIdentity, evidence.recordModelId]);
  require(modelCorr === "ok", modelCorr === "conflict" ? "conflicting_model_ids" : "model_identity_missing");
  require(Boolean(evidence.stateThreadId), "thread_session_identity_missing");
  const threadOut = extractThreadId(stdout);
  if (threadOut !== undefined) require(threadOut === evidence.stateThreadId, "thread_identity_conflict");
  require(evidence.stateWorktree !== undefined && pathsEqual(evidence.stateWorktree, evidence.targetWorktree),
    "worktree_session_correlation_failed");
  require(evidence.recordWorktree !== undefined && pathsEqual(evidence.recordWorktree, evidence.targetWorktree),
    "worktree_session_correlation_failed");
  const unchanged =
    evidence.worktreeStatusBefore === evidence.worktreeStatusAfter &&
    evidence.worktreeTopBefore === evidence.worktreeTopAfter &&
    evidence.worktreeCommonDirBefore === evidence.worktreeCommonDirAfter;
  require(unchanged, "unexpected_target_worktree_mutation");
  require(evidence.recordPresent, "readonly_provenance_absent");
  require(inferenceOccurred(stdout), "inference_not_observed");
  // Derived facts are structurally implied by the above conjunction (§2.6):
  // preflight_count_derived = 1, inference_count_derived = 1,
  // retry_count_derived = productionLaunches - 1 = 0.
  return { pass: failures.length === 0, failures };
}
```

(Final module also exports `registryIdentity` and a small CLI (`authorize`,
`registry-identity`, `evaluate`); the CLI prints JSON only to stdout and never
prints `SMOKE_RESULT` — the `.ps1` owns the single PASS/FAIL terminal line.)

### Task 2: `run-smoke.ps1` + `task.txt`

**Files:** create `tools/codex-controller/smoke/run-smoke.ps1`,
`tools/codex-controller/smoke/task.txt`.

- [ ] **Step 1** — write `task.txt` (exact content: read `AGENTS.md` at the
  root of the named worktree; report the exact title and first execution-contract
  priority item; do not modify any file; no network/hardware/MQTT; no
  commit/push/publish; under 200 words).
- [ ] **Step 2** — implement `run-smoke.ps1` building blocks:
  - `Start-CapturedChild` (System.Diagnostics.Process, strict UTF-8 no BOM;
    used for the node control subprocesses AND the single production child);
  - `Write-Utf8NoBom`;
  - `Capture-WorktreeIdentity` (§3.5);
  - Phase A: build, fetch, worktree setup (§9), pre-evidence, registry;
  - Phase B: `authorize` (atomic claim), then exactly ONE production child
    (live `node dist/src/index.js` with NO flags / dry-run mock) with
    `$_.ExitCode` captured, NO retry loop;
  - post-evidence; `evaluate`; single PASS terminal (exit 0); else
    `SMOKE_RESULT=FAIL` + failure codes (exit 1); catch-all FAIL.
- [ ] **Step 3** — manual hermetic sanity in a temp workspace (no network):
  dry-run with a mock exit 0 → exactly one `SMOKE_RESULT=PASS`, exit 0; rerun
  with the same consumed auth → `SMOKE_NOT_AUTHORIZED`, exit non-zero.
- [ ] **Step 4** — commit (scoped).

### Task 3: Hermetic harness tests (`smoke-harness.test.ts`)

**Files:** create `tools/codex-controller/smoke/smoke-harness.test.ts`.

- [ ] **Step 1** — implement the §15.4 test contract (failing/succeeding mock,
  no-auth, consumed-reuse, secret leak, worktree mutation, encoding
  round-trip incl. non-ASCII, single-run marker, never-live gating).
- [ ] **Step 2** — run `npm.cmd run build` then the new suite → FAIL (harness
  behavior missing → proves the tests are real).
- [ ] **Step 3** — iterate until green; never weaken an assertion.
- [ ] **Step 4** — `npm.cmd test` fully green; no network/inference.
- [ ] **Step 5** — commit (scoped).

### Task 4: Documentation update

**Files:** modify `tools/codex-controller/SMOKE_TEST.md`.

- [ ] **Step 1** — append a "Fail-stop live smoke harness" section:
  - `run-smoke.ps1` is the SINGLE active live-smoke procedure; pasted interactive
    smoke sequences are forbidden;
  - hermetic verification commands (`npm.cmd run build`, `npm.cmd test`);
  - live smoke requires the explicit one-shot authorization from
    `plans/fail-stop-live-smoke-harness-v1.md` and is NOT part of automated
    verification.
- [ ] **Step 2** — verify `npm.cmd test` green; plan-file `git diff --check` clean.
- [ ] **Step 3** — commit (scoped).

### Task 5: Final verification (no live operations)

- [ ] **Step 1** — confirm no harness/auth files, no smoke, no
  `--select-only`/`--compatibility-probe`, no stage/commit/push
  (apart from the scoped task commits of the BUILD).
- [ ] **Step 2** — from the repo root: `git diff --check`;
  `git status --short`; `cd tools/codex-controller; npm.cmd test` — all green.
- [ ] **Step 3** — hand over with the documented findings; the chain continues
  only as:

```text
independent HARNESS REVIEW
-> SMOKE READINESS RE-REVIEW
-> explicit NEW one-shot authorization
-> exactly ONE smoke
```

## Self-Review

- Spec coverage → every review-task requirement is mapped (encoding §8,
  type/test contract §15.1, bound auth §5, claim §5.3, setup parity §9,
  counters §2.6 and §10–§11, tests §15, scope §4).
- The `Evidence` type, `smoke-control.ts`, `.ps1` and tests form ONE consistent
  strict-TypeScript, path-backed contract; `baseEvidence()` in the unit tests
  constructs ALL required fields.
- No placeholders; concrete code for every step; real verification commands
  based on actual package scripts.
- PREVIOUS HISTORY (Section 18) and FUTURE GATES (Section 19) preserved exactly;
  current smoke authorization explicitly NO.