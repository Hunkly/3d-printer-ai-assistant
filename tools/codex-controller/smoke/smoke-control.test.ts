import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  type Evidence,
  type AuthorizationPayload,
  baseEvidence,
  canonicalPath,
  pathsEqual,
  sha256Text,
  redactSecrets,
  extractModelId,
  extractProviderMode,
  extractThreadId,
  inferenceOccurred,
  correlateModelIds,
  validateAuthorization,
  claimAuthorization,
  evaluateSuccess,
  registryIdentity,
  buildProductionCommand,
} from "./smoke-control.js";

// ------------------- Encoding & type contract -------------------

test("1. baseEvidence() satisfies every required Evidence field", () => {
  const e = baseEvidence();
  // Path-backed stdout path, not inline.
  assert.equal(typeof e.controllerStdoutPath, "string");
  assert.equal(typeof e.controllerStderrPath, "string");
  assert.equal(typeof e.controllerStatePath, "string");
  // Required enums/booleans.
  assert.equal(e.recordPresent, false);
  assert.equal(e.productionLaunches, 0);
  assert.equal(e.selectOnlyUsed, false);
  assert.equal(e.compatibilityProbeUsed, false);
  // Boolean presence evidence (§10).
  assert.equal(e.openrouterKeyPresent, false);
  assert.equal(e.localAppDataPresent, false);
  // Every key listed in §10 must exist.
  const required: (keyof Evidence)[] = [
    "timestamp", "repoRoot", "targetWorktree",
    "worktreeTopBefore", "worktreeCommonDirBefore",
    "worktreeHeadBefore", "worktreeBranchBefore", "worktreeStatusBefore",
    "worktreeTopAfter", "worktreeCommonDirAfter",
    "worktreeHeadAfter", "worktreeBranchAfter", "worktreeStatusAfter",
    "taskKey", "registryPath", "registrySha256", "registryEntryCount",
    "requestedProviderMode", "openrouterKeyPresent", "localAppDataPresent",
    "childExitCode",
    "controllerStdoutPath", "controllerStderrPath", "controllerStatePath",
    "recordPresent", "productionLaunches",
    "preflightCountDerived", "inferenceCountDerived", "retryCountDerived",
    "selectOnlyUsed", "compatibilityProbeUsed",
  ];
  for (const k of required) assert.ok(k in e, `Evidence missing field ${k}`);
  // Path-backed evidence: the type exposes path fields, NOT inline content
  // (e.g. no `controllerStdout: string`). The harness supplies real temp
  // file paths at runtime; tests assert the property NAMES exist.
  assert.equal("controllerStdout" in e, false, "controllerStdout must be path-backed, not inline");
  assert.equal("controllerStderr" in e, false, "controllerStderr must be path-backed, not inline");
});

test("2. canonicalPath lower-cases and strips trailing separators on Windows", () => {
  const a = canonicalPath("C:\\Users\\foo\\bar\\");
  const b = canonicalPath("c:/users/foo/bar");
  assert.equal(pathsEqual(a, b), true);
});

test("3. redactSecrets replaces OPENROUTER_API_KEY, Bearer tokens, and sk-or- keys", () => {
  const text = "OPENROUTER_API_KEY=sk-or-abcDEF123 token Authorization: Bearer eyJabc.x.y";
  const out = redactSecrets(text, ["sk-or-abcDEF123"]);
  assert.equal(out.includes("sk-or-abcDEF123"), false);
  assert.equal(out.includes("[REDACTED]"), true);
  assert.equal(out.includes("Bearer [REDACTED]"), true);
  assert.equal(/sk-or-[A-Za-z0-9-]+/.test(out), false);
});

test("4. extractors find controller markers and survive multi-line stdout", () => {
  const stdout = [
    "[controller] starting Codex stream...",
    "[controller] task=t1 branch=codex/t1 phase=general thread=resume provider=openrouter-free model=nvidia/nemotron-3.5-lightning:free",
    "[codex] thread 0c9d0f01-77aa-4f8d-bb13-5c7c9e0d3e77",
    "[codex] turn started",
    "[codex] turn completed; input=10, cached=2, uncached=8, output=4",
    "Codex: hi",
  ].join("\n");
  assert.equal(extractModelId(stdout), "nvidia/nemotron-3.5-lightning:free");
  assert.equal(extractProviderMode(stdout), "openrouter-free");
  assert.equal(extractThreadId(stdout), "0c9d0f01-77aa-4f8d-bb13-5c7c9e0d3e77");
  assert.equal(inferenceOccurred(stdout), true);
});

test("5. correlateModelIds returns ok | conflict | missing deterministically", () => {
  assert.equal(correlateModelIds(["a", "a", "a"]), "ok");
  assert.equal(correlateModelIds(["a", "b", "a"]), "conflict");
  assert.equal(correlateModelIds(["a", undefined, "a"]), "missing");
  assert.equal(correlateModelIds([]), "missing");
});

// ------------------- Authorization contract -------------------

function writeAuth(tmp: string, payload: Partial<AuthorizationPayload>): string {
  const p = join(tmp, "authorization");
  writeFileSync(p, JSON.stringify(payload), "utf8");
  return p;
}

function validPayload(overrides: Partial<AuthorizationPayload> = {}): AuthorizationPayload {
  return {
    schema_version: 1,
    authorization_id: "01234567-89ab-4cde-9012-3456789abcde",
    purpose: "automatic-openrouter-fallback-smoke-v1",
    expected_task_key: "smoke-readonly-v1",
    expected_target_worktree: "C:/Users/Viktor/Desktop/projects/.codex-worktrees/smoke-readonly-v1",
    expected_repository_root: "C:/Users/Viktor/Desktop/projects/3d-printer-ai-assistant",
    issued_at: "2026-08-20T17:32:10.368Z",
    expires_at: "2026-12-31T00:00:00.000Z",
    ...overrides,
  };
}

test("6. validateAuthorization rejects wrong purpose", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-auth-purpose-"));
  try {
    const authPath = writeAuth(tmp, validPayload({ purpose: "other-purpose" }));
    assert.throws(
      () => validateAuthorization(authPath, {
        taskKey: "smoke-readonly-v1",
        targetWorktree: "C:/Users/Viktor/Desktop/projects/.codex-worktrees/smoke-readonly-v1",
        repoRoot: "C:/Users/Viktor/Desktop/projects/3d-printer-ai-assistant",
      }),
      /SMOKE_AUTHORIZATION_INVALID/,
    );
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("7. validateAuthorization rejects wrong task key (target mismatch)", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-auth-task-"));
  try {
    const authPath = writeAuth(tmp, validPayload({ expected_task_key: "different-key" }));
    assert.throws(
      () => validateAuthorization(authPath, {
        taskKey: "smoke-readonly-v1",
        targetWorktree: "C:/Users/Viktor/Desktop/projects/.codex-worktrees/smoke-readonly-v1",
        repoRoot: "C:/Users/Viktor/Desktop/projects/3d-printer-ai-assistant",
      }),
      /SMOKE_AUTHORIZATION_TARGET_MISMATCH/,
    );
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("8. validateAuthorization rejects wrong worktree", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-auth-wt-"));
  try {
    const authPath = writeAuth(tmp, validPayload({ expected_target_worktree: "C:/Users/Viktor/Desktop/projects/.codex-worktrees/other" }));
    assert.throws(
      () => validateAuthorization(authPath, {
        taskKey: "smoke-readonly-v1",
        targetWorktree: "C:/Users/Viktor/Desktop/projects/.codex-worktrees/smoke-readonly-v1",
        repoRoot: "C:/Users/Viktor/Desktop/projects/3d-printer-ai-assistant",
      }),
      /SMOKE_AUTHORIZATION_TARGET_MISMATCH/,
    );
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("9. validateAuthorization rejects wrong repository root", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-auth-repo-"));
  try {
    const authPath = writeAuth(tmp, validPayload({ expected_repository_root: "C:/different/root" }));
    assert.throws(
      () => validateAuthorization(authPath, {
        taskKey: "smoke-readonly-v1",
        targetWorktree: "C:/Users/Viktor/Desktop/projects/.codex-worktrees/smoke-readonly-v1",
        repoRoot: "C:/Users/Viktor/Desktop/projects/3d-printer-ai-assistant",
      }),
      /SMOKE_AUTHORIZATION_TARGET_MISMATCH/,
    );
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("10. validateAuthorization rejects expired window", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-auth-expired-"));
  try {
    const authPath = writeAuth(tmp, validPayload({ issued_at: "2020-01-01T00:00:00.000Z", expires_at: "2020-01-02T00:00:00.000Z" }));
    assert.throws(
      () => validateAuthorization(authPath, {
        taskKey: "smoke-readonly-v1",
        targetWorktree: "C:/Users/Viktor/Desktop/projects/.codex-worktrees/smoke-readonly-v1",
        repoRoot: "C:/Users/Viktor/Desktop/projects/3d-printer-ai-assistant",
        now: new Date("2026-08-20T00:00:00.000Z"),
      }),
      /SMOKE_AUTHORIZATION_EXPIRED/,
    );
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("11. validateAuthorization rejects malformed JSON", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-auth-malformed-"));
  try {
    const authPath = join(tmp, "authorization");
    writeFileSync(authPath, "{not-json", "utf8");
    assert.throws(
      () => validateAuthorization(authPath, {
        taskKey: "smoke-readonly-v1",
        targetWorktree: "C:/Users/Viktor/Desktop/projects/.codex-worktrees/smoke-readonly-v1",
        repoRoot: "C:/Users/Viktor/Desktop/projects/3d-printer-ai-assistant",
      }),
      /SMOKE_AUTHORIZATION_INVALID/,
    );
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("11b. validateAuthorization accepts PowerShell 5.1 7-fractional-digit UTC timestamps", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-auth-iso-"));
  try {
    // (Get-Date).ToUniversalTime().ToString('o') -> 7 fractional digits.
    const authPath = writeAuth(tmp, validPayload({
      issued_at: "2026-08-20T23:28:45.8694822Z",
      expires_at: "2026-08-20T23:58:45.8694822Z",
    }));
    const ctx = {
      taskKey: "smoke-readonly-v1",
      targetWorktree: "C:/Users/Viktor/Desktop/projects/.codex-worktrees/smoke-readonly-v1",
      repoRoot: "C:/Users/Viktor/Desktop/projects/3d-printer-ai-assistant",
      now: new Date("2026-08-20T23:40:00.000Z"),
    };
    const p = validateAuthorization(authPath, ctx);
    assert.equal(p.issued_at, "2026-08-20T23:28:45.8694822Z");
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("11c. validateAuthorization rejects overlong or non-UTC fractional timestamps", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-auth-iso-bad-"));
  try {
    // 10 fractional digits is outside the 1-9 safe contract.
    const authPath = writeAuth(tmp, validPayload({
      issued_at: "2026-08-20T23:28:45.1234567890Z",
      expires_at: "2026-12-31T00:00:00.000Z",
    }));
    assert.throws(
      () => validateAuthorization(authPath, {
        taskKey: "smoke-readonly-v1",
        targetWorktree: "C:/Users/Viktor/Desktop/projects/.codex-worktrees/smoke-readonly-v1",
        repoRoot: "C:/Users/Viktor/Desktop/projects/3d-printer-ai-assistant",
      }),
      /SMOKE_AUTHORIZATION_INVALID/,
    );
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("12. claimAuthorization returns not-authorized when file is missing", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-auth-missing-"));
  try {
    const result = claimAuthorization(join(tmp, "authorization"), {
      taskKey: "smoke-readonly-v1",
      targetWorktree: "C:/Users/Viktor/Desktop/projects/.codex-worktrees/smoke-readonly-v1",
      repoRoot: "C:/Users/Viktor/Desktop/projects/3d-printer-ai-assistant",
    });
    assert.equal(result.authorized, false);
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("13. claimAuthorization succeeds exactly once; loser sees missing path", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-auth-race-"));
  try {
    const authPath = writeAuth(tmp, validPayload());
    const ctx = {
      taskKey: "smoke-readonly-v1",
      targetWorktree: "C:/Users/Viktor/Desktop/projects/.codex-worktrees/smoke-readonly-v1",
      repoRoot: "C:/Users/Viktor/Desktop/projects/3d-printer-ai-assistant",
    };
    const first = claimAuthorization(authPath, ctx);
    const second = claimAuthorization(authPath, ctx);
    assert.equal(first.authorized, true);
    assert.equal(typeof first.authorizationId, "string");
    assert.equal(typeof first.sha256, "string");
    assert.equal(second.authorized, false);
    // The original path must be gone; only the consumed artifact remains.
    assert.equal(existsSync(authPath), false);
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("14. claimAuthorization never restores a claimed file even after a downstream failure", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-auth-restore-"));
  try {
    const authPath = writeAuth(tmp, validPayload());
    const ctx = {
      taskKey: "smoke-readonly-v1",
      targetWorktree: "C:/Users/Viktor/Desktop/projects/.codex-worktrees/smoke-readonly-v1",
      repoRoot: "C:/Users/Viktor/Desktop/projects/3d-printer-ai-assistant",
    };
    const first = claimAuthorization(authPath, ctx);
    assert.equal(first.authorized, true);
    // Even after we intentionally try again, the original path stays gone.
    const again = claimAuthorization(authPath, ctx);
    assert.equal(again.authorized, false);
    assert.equal(existsSync(authPath), false);
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

// ------------------- evaluateSuccess contract -------------------

function writeStdout(tmp: string, text: string): string {
  const p = join(tmp, "stdout.txt");
  writeFileSync(p, text, "utf8");
  return p;
}

function passingEvidence(tmp: string): Evidence {
  const stdout = [
    "[controller] task=t1 branch=codex/t1 phase=general thread=resume provider=openrouter-free model=nvidia/nemotron-3.5-lightning:free",
    "[codex] thread 0c9d0f01-77aa-4f8d-bb13-5c7c9e0d3e77",
    "[codex] turn completed; input=10, cached=2, uncached=8, output=4",
    "Codex: ok",
  ].join("\n");
  return {
    ...baseEvidence(),
    timestamp: new Date().toISOString(),
    repoRoot: "C:/Users/Viktor/Desktop/projects/3d-printer-ai-assistant",
    targetWorktree: "C:/Users/Viktor/Desktop/projects/.codex-worktrees/smoke-readonly-v1",
    worktreeTopBefore: "C:/wt",
    worktreeTopAfter: "C:/wt",
    worktreeCommonDirBefore: "C:/repo/.git",
    worktreeCommonDirAfter: "C:/repo/.git",
    worktreeHeadBefore: "abc",
    worktreeHeadAfter: "abc",
    worktreeBranchBefore: "codex/t1",
    worktreeBranchAfter: "codex/t1",
    worktreeStatusBefore: "",
    worktreeStatusAfter: "",
    taskKey: "smoke-readonly-v1",
    registryPath: "C:/registry.json",
    registrySha256: "0".repeat(64),
    registryEntryCount: 1,
    requestedProviderMode: "auto",
    openrouterKeyPresent: true,
    localAppDataPresent: true,
    childExitCode: 0,
    controllerStdoutPath: writeStdout(tmp, stdout),
    controllerStderrPath: join(tmp, "stderr.txt"),
    controllerStatePath: join(tmp, "state.json"),
    stateModelIdentity: "nvidia/nemotron-3.5-lightning:free",
    stateProviderMode: "openrouter-free",
    stateRole: "readonly",
    stateThreadId: "0c9d0f01-77aa-4f8d-bb13-5c7c9e0d3e77",
    stateWorktree: "C:/Users/Viktor/Desktop/projects/.codex-worktrees/smoke-readonly-v1",
    recordModelId: "nvidia/nemotron-3.5-lightning:free",
    recordProviderId: "openrouter",
    recordWorktree: "C:/Users/Viktor/Desktop/projects/.codex-worktrees/smoke-readonly-v1",
    recordPresent: true,
    productionLaunches: 1,
    preflightCountDerived: 1,
    inferenceCountDerived: 1,
    retryCountDerived: 0,
    selectOnlyUsed: false,
    compatibilityProbeUsed: false,
  };
}

test("15. evaluateSuccess uses controllerStdoutPath to read real file content", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-ev-pass-"));
  try {
    const e = passingEvidence(tmp);
    const decision = evaluateSuccess(e);
    assert.equal(decision.pass, true);
    assert.deepEqual(decision.failures, []);
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("16. evaluateSuccess fails when child exit code is non-zero", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-ev-exit-"));
  try {
    const e = { ...passingEvidence(tmp), childExitCode: 1 };
    const decision = evaluateSuccess(e);
    assert.equal(decision.pass, false);
    assert.ok(decision.failures.includes("terminal_execution_result_not_success"));
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("17. evaluateSuccess fails when productionLaunches is not 1", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-ev-launches-"));
  try {
    const e = { ...passingEvidence(tmp), productionLaunches: 2 };
    const decision = evaluateSuccess(e);
    assert.equal(decision.pass, false);
    assert.ok(decision.failures.includes("production_task_invocation_count_not_1"));
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("18. evaluateSuccess fails when selectOnlyUsed or compatibilityProbeUsed is true", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-ev-forbidden-"));
  try {
    const a = evaluateSuccess({ ...passingEvidence(tmp), selectOnlyUsed: true });
    const b = evaluateSuccess({ ...passingEvidence(tmp), compatibilityProbeUsed: true });
    assert.equal(a.pass, false);
    assert.equal(b.pass, false);
    assert.ok(a.failures.includes("select_only_forbidden"));
    assert.ok(b.failures.includes("compatibility_probe_forbidden"));
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("19. evaluateSuccess fails when stdout model id is missing", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-ev-nomodel-"));
  try {
    const e = passingEvidence(tmp);
    // Overwrite stdout with no [controller] model= marker.
    writeFileSync(e.controllerStdoutPath, "[controller] task=t1 phase=general\n[codex] turn completed\nCodex: ok\n", "utf8");
    const decision = evaluateSuccess(e);
    assert.equal(decision.pass, false);
    assert.ok(decision.failures.includes("model_identity_missing"));
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("20. evaluateSuccess fails when stdout model id conflicts with state/record", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-ev-conflict-"));
  try {
    const e = passingEvidence(tmp);
    // Same as passingEvidence stdout, but state says something else.
    e.stateModelIdentity = "vendor/conflict:free";
    const decision = evaluateSuccess(e);
    assert.equal(decision.pass, false);
    assert.ok(decision.failures.includes("conflicting_model_ids"));
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("21. evaluateSuccess fails when record is missing", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-ev-norec-"));
  try {
    const e = { ...passingEvidence(tmp), recordPresent: false };
    const decision = evaluateSuccess(e);
    assert.equal(decision.pass, false);
    assert.ok(decision.failures.includes("readonly_provenance_absent"));
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("22. evaluateSuccess fails when thread id is missing", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-ev-nothread-"));
  try {
    const e = { ...passingEvidence(tmp), stateThreadId: undefined };
    const decision = evaluateSuccess(e);
    assert.equal(decision.pass, false);
    assert.ok(decision.failures.includes("thread_session_identity_missing"));
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("23. evaluateSuccess fails when worktree state mutates between before and after", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-ev-mut-"));
  try {
    const e = { ...passingEvidence(tmp), worktreeStatusAfter: " M file.txt" };
    const decision = evaluateSuccess(e);
    assert.equal(decision.pass, false);
    assert.ok(decision.failures.includes("unexpected_target_worktree_mutation"));
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("24. evaluateSuccess fails when requestedProviderMode is not auto", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-ev-mode-"));
  try {
    const e = { ...passingEvidence(tmp), requestedProviderMode: "primary" };
    const decision = evaluateSuccess(e);
    assert.equal(decision.pass, false);
    assert.ok(decision.failures.includes("auto_gate_failed"));
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

// ------------------- Derived counters (§2.6 / §10) -------------------

test("24b. evaluateSuccess reports derived counters when predicates hold", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-ev-derived-"));
  try {
    const e = passingEvidence(tmp);
    const decision = evaluateSuccess(e);
    assert.equal(decision.pass, true);
    assert.equal(decision.preflightCountDerived, 1);
    assert.equal(decision.inferenceCountDerived, 1);
    assert.equal(decision.retryCountDerived, 0);
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("24c. evaluateSuccess fails when derived counters violate the contract", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-ev-derived-bad-"));
  try {
    const a = evaluateSuccess({ ...passingEvidence(tmp), preflightCountDerived: 2 });
    assert.equal(a.pass, false);
    assert.ok(a.failures.includes("preflight_count_derived_invalid"));
    const b = evaluateSuccess({ ...passingEvidence(tmp), inferenceCountDerived: 0 });
    assert.equal(b.pass, false);
    assert.ok(b.failures.includes("inference_count_derived_invalid"));
    const c = evaluateSuccess({ ...passingEvidence(tmp), retryCountDerived: 1 });
    assert.equal(c.pass, false);
    assert.ok(c.failures.includes("retry_count_derived_invalid"));
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

// ------------------- HEAD / branch identity fail-closed (§3.5) -------------------

test("24d. evaluateSuccess fails when worktree HEAD changes between captures", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-ev-head-"));
  try {
    const e = { ...passingEvidence(tmp), worktreeHeadAfter: "deadbeef" };
    const decision = evaluateSuccess(e);
    assert.equal(decision.pass, false);
    assert.ok(decision.failures.includes("unexpected_target_worktree_mutation"));
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("24e. evaluateSuccess fails when worktree branch changes between captures", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-ev-branch-"));
  try {
    const e = { ...passingEvidence(tmp), worktreeBranchAfter: "codex/other" };
    const decision = evaluateSuccess(e);
    assert.equal(decision.pass, false);
    assert.ok(decision.failures.includes("unexpected_target_worktree_mutation"));
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

// ------------------- Production command construction (live boundary) -------------------

test("24f. buildProductionCommand constructs the REAL live entry point with no forbidden flags", () => {
  const spec = buildProductionCommand({
    controllerDir: "C:/tools/codex-controller",
    taskKey: "smoke-readonly-v1",
    targetWorktree: "C:/projects/.codex-worktrees/smoke-readonly-v1",
    repoRoot: "C:/projects/3d-printer-ai-assistant",
    taskFile: "C:/tools/codex-controller/smoke/task.txt",
    statePath: "C:/AppData/print-engineer-codex/smoke/controller-state.json",
    baseBranch: "master",
    worktreeRoot: "C:/projects/.codex-worktrees",
  });
  assert.equal(spec.fileName, "node");
  assert.deepEqual(spec.arguments, ["dist/src/index.js"]);
  assert.equal(spec.arguments.includes("--select-only"), false);
  assert.equal(spec.arguments.includes("--compatibility-probe"), false);
  assert.equal(spec.workingDirectory, "C:/tools/codex-controller");
  // Approved automatic-fallback environment.
  assert.equal(spec.environment.CODEX_PROVIDER_MODE, "auto");
  assert.equal(spec.environment.CODEX_PHASE, "general");
  assert.equal(spec.environment.CODEX_TASK_KEY, "smoke-readonly-v1");
  assert.equal(spec.environment.CODEX_WORKTREE_ROOT, "C:/projects/.codex-worktrees");
  assert.equal(spec.environment.CODEX_REPO, "C:/projects/3d-printer-ai-assistant");
  assert.equal(spec.environment.CODEX_BASE_BRANCH, "master");
  assert.equal(spec.environment.MODEL_TASK_FILE, "C:/tools/codex-controller/smoke/task.txt");
  assert.equal(spec.environment.CODEX_STATE, "C:/AppData/print-engineer-codex/smoke/controller-state.json");
});

// ------------------- Encoding guard -------------------

test("25. UTF-8 without BOM round-trips through Node readFileSync (encoding guard)", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-ev-utf8-"));
  try {
    const p = join(tmp, "evidence.json");
    const text = JSON.stringify({ ok: true, message: "héllo αβγ — 世界" });
    // Write byte-exact UTF-8 without BOM (simulate harness Write-Utf8NoBom).
    const buf = Buffer.from(text, "utf8");
    writeFileSync(p, buf);
    const read = readFileSync(p, "utf8");
    assert.equal(read, text);
    assert.equal(read.charCodeAt(0), text.charCodeAt(0));
    // No BOM.
    assert.notEqual(read.charCodeAt(0), 0xfeff);
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("26. UTF-16LE bytes (which PowerShell redirection would emit) cannot be parsed as JSON by Node under UTF-8", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-ev-utf16-"));
  try {
    const p = join(tmp, "evidence.json");
    const text = JSON.stringify({ ok: true });
    // Encode as UTF-16LE — what `>` / `2>` would emit on Windows PowerShell 5.1.
    const buf = Buffer.from(text, "utf16le");
    writeFileSync(p, buf);
    const raw = readFileSync(p, "utf8");
    // The point of this assertion: Node readFileSync(..., "utf8") on a
    // UTF-16LE stream yields replacement characters / scrambled bytes that
    // cannot round-trip back to the original JSON. The harness therefore
    // forbids redirection (`>` / `2>`) for any Node-parsed evidence.
    assert.notEqual(raw, text);
    let parsed: unknown = undefined;
    let parseError: unknown = undefined;
    try { parsed = JSON.parse(raw); } catch (error) { parseError = error; }
    assert.ok(parseError !== undefined || (typeof parsed === "object" && parsed !== null && (parsed as { ok?: unknown }).ok !== true));
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

// ------------------- Registry identity -------------------

test("27. registryIdentity computes sha256 and entry count for a valid registry", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-ev-reg-"));
  try {
    const registryPath = join(tmp, "registry.json");
    const now = new Date();
    const valid = new Date(now.getTime() + 30 * 24 * 3600 * 1000).toISOString();
    const payload = {
      schema_version: 1,
      entries: [{
        model_id: "vendor/a:free",
        codex_sdk_version: "0.147.0",
        provider_id: "openrouter",
        wire_api: "responses",
        validated_at: now.toISOString(),
        valid_until: valid,
      }],
    };
    writeFileSync(registryPath, JSON.stringify(payload), "utf8");
    const id = registryIdentity(registryPath);
    assert.equal(typeof id.registrySha256, "string");
    assert.equal(id.registrySha256.length, 64);
    assert.equal(id.registryEntryCount, 1);
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("28. registryIdentity fails closed on invalid registry", () => {
  const tmp = mkdtempSync(join(tmpdir(), "smoke-ev-badreg-"));
  try {
    const registryPath = join(tmp, "registry.json");
    writeFileSync(registryPath, "{}", "utf8");
    assert.throws(() => registryIdentity(registryPath), /COMPATIBILITY_REGISTRY_INVALID/);
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

// ------------------- sha256 / pathsEqual edge cases -------------------

test("29. sha256Text is stable across calls and yields 64 hex chars", () => {
  const a = sha256Text("hello world");
  const b = sha256Text("hello world");
  assert.equal(a, b);
  assert.equal(a.length, 64);
  assert.match(a, /^[0-9a-f]{64}$/);
});

test("30. pathsEqual treats Windows-style paths case-insensitively", () => {
  assert.equal(pathsEqual("C:\\Foo\\Bar", "c:/foo/bar"), true);
  assert.equal(pathsEqual("C:\\Foo\\Bar", "c:/foo/baz"), false);
});
