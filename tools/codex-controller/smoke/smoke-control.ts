// Fail-Stop Live Smoke Harness — strict TypeScript control module
// Implements authorization, registry identity, evidence correlation,
// and success/failure predicates. Pure (no network, no I/O outside the
// declared file paths), hermetic, and unit-testable.
//
// The companion PowerShell orchestrator (run-smoke.ps1) owns the single
// SMOKE_RESULT terminal line and the fail-stop control flow; this module
// only answers questions, never prints PASS/FAIL on its own.

import { createHash } from "node:crypto";
import { existsSync, lstatSync, readFileSync, realpathSync, renameSync } from "node:fs";
import { resolve } from "node:path";
import { loadRegistry } from "@print-engineer/openrouter-free-selector";

// ---------------------------------------------------------------------------
// Evidence contract — see plans/fail-stop-live-smoke-harness-v1.md §10/§11.
// ---------------------------------------------------------------------------

export interface Evidence {
  timestamp: string;
  repoRoot: string;
  targetWorktree: string;
  worktreeTopBefore: string;
  worktreeCommonDirBefore: string;
  worktreeHeadBefore: string;
  worktreeBranchBefore: string;
  worktreeStatusBefore: string;
  worktreeTopAfter: string;
  worktreeCommonDirAfter: string;
  worktreeHeadAfter: string;
  worktreeBranchAfter: string;
  worktreeStatusAfter: string;
  taskKey: string;
  registryPath: string;
  registrySha256: string;
  registryEntryCount: number;
  requestedProviderMode: string;
  // Boolean presence evidence only — never values (§10).
  openrouterKeyPresent: boolean;
  localAppDataPresent: boolean;
  childExitCode: number;
  controllerStdoutPath: string;
  controllerStderrPath: string;
  controllerStatePath: string;
  stateModelIdentity?: string;
  stateProviderMode?: string;
  stateRole?: string;
  stateThreadId?: string;
  stateWorktree?: string;
  recordModelId?: string;
  recordProviderId?: string;
  recordWorktree?: string;
  recordPresent: boolean;
  productionLaunches: number;
  // Derived facts (§2.6) — structurally guaranteed by the reviewed production
  // contract plus the observed single launch. Represented explicitly so no
  // fictional directly-emitted counter is implied.
  preflightCountDerived: number;
  inferenceCountDerived: number;
  retryCountDerived: number;
  authorizationId?: string;
  authorizationSha256?: string;
  selectOnlyUsed: boolean;
  compatibilityProbeUsed: boolean;
}

export interface AuthorizationPayload {
  schema_version: number;
  authorization_id: string;
  purpose: string;
  expected_task_key: string;
  expected_target_worktree: string;
  expected_repository_root: string;
  issued_at: string;
  expires_at: string;
}

export interface SuccessDecision {
  pass: boolean;
  failures: string[];
  preflightCountDerived: number;
  inferenceCountDerived: number;
  retryCountDerived: number;
}
export interface ClaimResult { authorized: boolean; authorizationId?: string; sha256?: string; }
export interface RegistryIdentity { registrySha256: string; registryEntryCount: number; }

export interface ProductionCommandSpec {
  fileName: string;
  arguments: string[];
  workingDirectory: string;
  environment: Record<string, string>;
}

export interface ProductionCommandContext {
  controllerDir: string;
  taskKey: string;
  targetWorktree: string;
  repoRoot: string;
  taskFile: string;
  statePath: string;
  baseBranch: string;
  worktreeRoot: string;
}

const AUTHORIZATION_PURPOSE = "automatic-openrouter-fallback-smoke-v1";
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
// Valid UTC ISO-8601 with the required trailing Z and a safe fractional-second
// form. The approved operator procedure uses PowerShell 5.1
// `(Get-Date).ToUniversalTime().ToString("o")` which emits 7 fractional digits
// (e.g. 2026-08-20T23:28:45.8694822Z); accept 1-9 fractional digits. Date.parse
// still gates validity, so malformed/non-UTC timestamps fail closed.
const ISO_8601 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{1,9}Z$/;

// ---------------------------------------------------------------------------
// baseEvidence — every REQUIRED field (§15.1). Path-backed; no inline data.
// ---------------------------------------------------------------------------

export function baseEvidence(): Evidence {
  return {
    timestamp: "",
    repoRoot: "",
    targetWorktree: "",
    worktreeTopBefore: "",
    worktreeCommonDirBefore: "",
    worktreeHeadBefore: "",
    worktreeBranchBefore: "",
    worktreeStatusBefore: "",
    worktreeTopAfter: "",
    worktreeCommonDirAfter: "",
    worktreeHeadAfter: "",
    worktreeBranchAfter: "",
    worktreeStatusAfter: "",
    taskKey: "",
    registryPath: "",
    registrySha256: "",
    registryEntryCount: 0,
    requestedProviderMode: "auto",
    openrouterKeyPresent: false,
    localAppDataPresent: false,
    childExitCode: 1,
    controllerStdoutPath: "",
    controllerStderrPath: "",
    controllerStatePath: "",
    recordPresent: false,
    productionLaunches: 0,
    preflightCountDerived: 0,
    inferenceCountDerived: 0,
    retryCountDerived: 0,
    selectOnlyUsed: false,
    compatibilityProbeUsed: false,
  };
}

// ---------------------------------------------------------------------------
// Path utilities — Windows case-insensitive canonicalization (§5.2).
// ---------------------------------------------------------------------------

export function canonicalPath(p: string): string {
  let resolved: string = p;
  try { resolved = realpathSync(p); } catch { /* fall back to raw */ }
  return resolve(resolved).replace(/[\\/]+$/, "");
}

export function pathsEqual(a: string, b: string): boolean {
  return canonicalPath(a).toLowerCase() === canonicalPath(b).toLowerCase();
}

export function sha256Text(text: string): string {
  return createHash("sha256").update(text).digest("hex");
}

// ---------------------------------------------------------------------------
// Redaction — applied at every capture/persistence boundary (§14).
// ---------------------------------------------------------------------------

export function redactSecrets(text: string, secrets: readonly string[]): string {
  let out = text;
  for (const secret of secrets) {
    if (secret) out = out.split(secret).join("[REDACTED]");
  }
  return out
    .replace(/Bearer\s+[A-Za-z0-9._~+/=-]+/g, "Bearer [REDACTED]")
    .replace(/sk-or-[A-Za-z0-9-]+/g, "sk-or-[REDACTED]");
}

// ---------------------------------------------------------------------------
// Extractors — robust against arbitrary surrounding stdout (§14).
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Correlation — closed value set (§14).
// ---------------------------------------------------------------------------

export type Correlation = "ok" | "conflict" | "missing";

export function correlateModelIds(values: readonly (string | undefined)[]): Correlation {
  const present = values.filter((v): v is string => typeof v === "string" && v.length > 0);
  if (present.length === 0 || present.length !== values.length) return "missing";
  return present.every((v) => v === present[0]) ? "ok" : "conflict";
}

// ---------------------------------------------------------------------------
// Authorization validation + atomic claim (§5.3).
// ---------------------------------------------------------------------------

export interface AuthorizationContext {
  taskKey: string;
  targetWorktree: string;
  repoRoot: string;
  now?: Date;
}

export function validateAuthorization(
  authPath: string,
  context: AuthorizationContext,
): AuthorizationPayload {
  const raw = readFileSync(authPath, "utf8");
  let parsed: unknown;
  try { parsed = JSON.parse(raw); } catch { throw new Error("SMOKE_AUTHORIZATION_INVALID"); }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("SMOKE_AUTHORIZATION_INVALID");
  }
  const p = parsed as Record<string, unknown>;

  for (const k of [
    "schema_version", "authorization_id", "purpose",
    "expected_task_key", "expected_target_worktree",
    "expected_repository_root", "issued_at", "expires_at",
  ] as const) {
    if (!(k in p)) throw new Error("SMOKE_AUTHORIZATION_INVALID");
  }
  if (p.schema_version !== 1) throw new Error("SMOKE_AUTHORIZATION_INVALID");
  if (p.purpose !== AUTHORIZATION_PURPOSE) throw new Error("SMOKE_AUTHORIZATION_INVALID");
  if (typeof p.authorization_id !== "string" || !UUID_V4.test(p.authorization_id)) {
    throw new Error("SMOKE_AUTHORIZATION_INVALID");
  }
  if (typeof p.expected_task_key !== "string" || p.expected_task_key.length === 0) {
    throw new Error("SMOKE_AUTHORIZATION_INVALID");
  }
  if (typeof p.expected_target_worktree !== "string" || p.expected_target_worktree.length === 0) {
    throw new Error("SMOKE_AUTHORIZATION_INVALID");
  }
  if (typeof p.expected_repository_root !== "string" || p.expected_repository_root.length === 0) {
    throw new Error("SMOKE_AUTHORIZATION_INVALID");
  }
  if (typeof p.issued_at !== "string" || !ISO_8601.test(p.issued_at)) {
    throw new Error("SMOKE_AUTHORIZATION_INVALID");
  }
  if (typeof p.expires_at !== "string" || !ISO_8601.test(p.expires_at)) {
    throw new Error("SMOKE_AUTHORIZATION_INVALID");
  }
  if (p.expected_task_key !== context.taskKey) {
    throw new Error("SMOKE_AUTHORIZATION_TARGET_MISMATCH");
  }
  if (!pathsEqual(String(p.expected_target_worktree), context.targetWorktree)) {
    throw new Error("SMOKE_AUTHORIZATION_TARGET_MISMATCH");
  }
  if (!pathsEqual(String(p.expected_repository_root), context.repoRoot)) {
    throw new Error("SMOKE_AUTHORIZATION_TARGET_MISMATCH");
  }
  const issued = Date.parse(String(p.issued_at));
  const expiry = Date.parse(String(p.expires_at));
  if (Number.isNaN(issued) || Number.isNaN(expiry)) {
    throw new Error("SMOKE_AUTHORIZATION_INVALID");
  }
  const now = (context.now ?? new Date()).getTime();
  if (!(issued <= now && now < expiry)) {
    throw new Error("SMOKE_AUTHORIZATION_EXPIRED");
  }
  return parsed as AuthorizationPayload;
}

export function claimAuthorization(
  authPath: string,
  context: AuthorizationContext,
): ClaimResult {
  if (!existsSync(authPath)) return { authorized: false };
  const st = lstatSync(authPath);
  if (!st.isFile() || st.isSymbolicLink()) {
    throw new Error("SMOKE_AUTHORIZATION_INVALID");
  }
  // Pre-claim validation (binding, expiry).
  const before = readFileSync(authPath, "utf8");
  const validated = validateAuthorization(authPath, context);

  // Atomic claim via unique rename; loser sees path missing.
  const claimedPath = `${authPath}.consumed.${Date.now()}-${process.pid}-${Math.random().toString(36).slice(2, 10)}`;
  try {
    renameSync(authPath, claimedPath);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return { authorized: false };
    }
    throw error;
  }

  // Re-read + re-validate the claimed payload to detect alteration.
  const after = readFileSync(claimedPath, "utf8");
  if (after !== before) {
    throw new Error("SMOKE_AUTHORIZATION_CLAIMED_MISMATCH");
  }
  validateAuthorization(claimedPath, context);

  return {
    authorized: true,
    authorizationId: validated.authorization_id,
    sha256: sha256Text(after),
  };
}

// ---------------------------------------------------------------------------
// Registry identity — uses the authoritative loadRegistry (§10).
// ---------------------------------------------------------------------------

export function registryIdentity(registryPath: string): RegistryIdentity {
  // loadRegistry validates schema + entries; any failure throws.
  const entries = loadRegistry(registryPath);
  const raw = readFileSync(registryPath, "utf8");
  return {
    registrySha256: sha256Text(raw),
    registryEntryCount: entries.length,
  };
}

// ---------------------------------------------------------------------------
// Production command construction — the REAL live automatic-fallback entry
// point (`node dist/src/index.js`, no flags) with the approved normal
// automatic-fallback environment. This is a pure, hermetic seam: the harness
// never selects candidate models itself; the normal production selector owns
// model selection. The .ps1 consumes this spec via the `production-command`
// subcommand so there is exactly ONE source of truth, and it is unit-testable
// without any external inference.
// ---------------------------------------------------------------------------

export function buildProductionCommand(ctx: ProductionCommandContext): ProductionCommandSpec {
  return {
    fileName: "node",
    // NO --select-only, NO --compatibility-probe, no other flags.
    arguments: ["dist/src/index.js"],
    workingDirectory: ctx.controllerDir,
    environment: {
      // Approved automatic fallback environment (§3.2 / §7 step 4).
      CODEX_PROVIDER_MODE: "auto",
      CODEX_PHASE: "general",
      CODEX_TASK_KEY: ctx.taskKey,
      CODEX_WORKTREE_ROOT: ctx.worktreeRoot,
      CODEX_REPO: ctx.repoRoot,
      CODEX_BASE_BRANCH: ctx.baseBranch,
      MODEL_TASK_FILE: ctx.taskFile,
      CODEX_STATE: ctx.statePath,
    },
  };
}

// ---------------------------------------------------------------------------
// evaluateSuccess — single PASS terminal predicate (§11). Reads controller
// stdout from the path-backed file, never from an inline field.
// ---------------------------------------------------------------------------

export function evaluateSuccess(evidence: Evidence): SuccessDecision {
  const failures: string[] = [];
  const require = (ok: boolean, name: string): void => { if (!ok) failures.push(name); };

  if (evidence.selectOnlyUsed) failures.push("select_only_forbidden");
  if (evidence.compatibilityProbeUsed) failures.push("compatibility_probe_forbidden");
  require(evidence.requestedProviderMode === "auto", "auto_gate_failed");
  require(evidence.childExitCode === 0, "terminal_execution_result_not_success");
  require(evidence.productionLaunches === 1, "production_task_invocation_count_not_1");

  // Derived facts (§2.6): structurally guaranteed by the reviewed production
  // contract plus the observed single launch. They are DERIVED, never directly
  // emitted by Codex/OpenRouter; validate the derived contract explicitly.
  require(evidence.preflightCountDerived === 1, "preflight_count_derived_invalid");
  require(evidence.inferenceCountDerived === 1, "inference_count_derived_invalid");
  require(
    evidence.retryCountDerived === evidence.productionLaunches - 1 && evidence.retryCountDerived === 0,
    "retry_count_derived_invalid",
  );

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
  if (threadOut !== undefined) {
    require(threadOut === evidence.stateThreadId, "thread_identity_conflict");
  }

  require(
    evidence.stateWorktree !== undefined && pathsEqual(evidence.stateWorktree, evidence.targetWorktree),
    "worktree_session_correlation_failed",
  );
  require(
    evidence.recordWorktree !== undefined && pathsEqual(evidence.recordWorktree, evidence.targetWorktree),
    "worktree_session_correlation_failed",
  );

  // Worktree identity must be unchanged across ALL mandatory fields: top,
  // common dir, HEAD, branch, and status (§3.5 / §10 / §13).
  const unchanged =
    evidence.worktreeTopBefore === evidence.worktreeTopAfter &&
    evidence.worktreeCommonDirBefore === evidence.worktreeCommonDirAfter &&
    evidence.worktreeHeadBefore === evidence.worktreeHeadAfter &&
    evidence.worktreeBranchBefore === evidence.worktreeBranchAfter &&
    evidence.worktreeStatusBefore === evidence.worktreeStatusAfter;
  require(unchanged, "unexpected_target_worktree_mutation");

  require(evidence.recordPresent, "readonly_provenance_absent");
  require(inferenceOccurred(stdout), "inference_not_observed");

  return {
    pass: failures.length === 0,
    failures,
    preflightCountDerived: evidence.preflightCountDerived,
    inferenceCountDerived: evidence.inferenceCountDerived,
    retryCountDerived: evidence.retryCountDerived,
  };
}

// ---------------------------------------------------------------------------
// CLI — only used by the PowerShell orchestrator. The CLI NEVER prints
// SMOKE_RESULT; that is owned by run-smoke.ps1.
// ---------------------------------------------------------------------------

type CliSubcommand = "authorize" | "registry-identity" | "evaluate" | "production-command";

function parseArgs(argv: readonly string[]): { subcommand: string; flags: Record<string, string> } {
  if (argv.length === 0) throw new Error("SMOKE_CONTROL_USAGE");
  const subcommand = argv[0]!;
  const flags: Record<string, string> = {};
  for (let i = 1; i < argv.length; i++) {
    const arg = argv[i]!;
    if (!arg.startsWith("--")) throw new Error("SMOKE_CONTROL_USAGE");
    const eq = arg.indexOf("=");
    if (eq < 0) throw new Error("SMOKE_CONTROL_USAGE");
    const k = arg.slice(2, eq);
    const v = arg.slice(eq + 1);
    flags[k] = v;
  }
  return { subcommand, flags };
}

function fail(message: string, code = 1): never {
  // Strict UTF-8 without BOM to stdout (Node writes raw UTF-8 by default).
  process.stdout.write(JSON.stringify({ ok: false, error: message }) + "\n");
  process.exit(code);
}

function ok(payload: Record<string, unknown>): void {
  process.stdout.write(JSON.stringify({ ok: true, ...payload }) + "\n");
}

function requireFlag(flags: Record<string, string>, key: string): string {
  const v = flags[key];
  if (typeof v !== "string" || v.length === 0) fail("SMOKE_CONTROL_USAGE");
  return v;
}

function runCli(): void {
  const argv = process.argv.slice(2);
  let parsed: { subcommand: string; flags: Record<string, string> };
  try { parsed = parseArgs(argv); }
  catch { fail("SMOKE_CONTROL_USAGE"); }

  switch (parsed.subcommand as CliSubcommand) {
    case "authorize": {
      const authPath = requireFlag(parsed.flags, "auth");
      const taskKey = requireFlag(parsed.flags, "task");
      const worktree = requireFlag(parsed.flags, "worktree");
      const repo = requireFlag(parsed.flags, "repo");
      try {
        const r = claimAuthorization(authPath, { taskKey, targetWorktree: worktree, repoRoot: repo });
        if (!r.authorized) {
          // Distinct stable identifier for the harness catch-all.
          process.stdout.write(JSON.stringify({ ok: false, error: "SMOKE_NOT_AUTHORIZED" }) + "\n");
          process.exit(2);
        }
        ok({ authorizationId: r.authorizationId, sha256: r.sha256 });
      } catch (error) {
        const message = error instanceof Error ? error.message : "SMOKE_AUTHORIZATION_INVALID";
        fail(message);
      }
      return;
    }
    case "registry-identity": {
      const registryPath = requireFlag(parsed.flags, "registry");
      try {
        const id = registryIdentity(registryPath);
        ok({ registrySha256: id.registrySha256, registryEntryCount: id.registryEntryCount });
      } catch (error) {
        const message = error instanceof Error ? error.message : "COMPATIBILITY_REGISTRY_INVALID";
        fail(message);
      }
      return;
    }
    case "evaluate": {
      const evidencePath = requireFlag(parsed.flags, "evidence");
      let evidence: Evidence;
      try {
        evidence = JSON.parse(readFileSync(evidencePath, "utf8")) as Evidence;
      } catch { fail("SMOKE_EVIDENCE_INVALID"); }
      try {
        const decision = evaluateSuccess(evidence);
        if (decision.pass) {
          ok({
            pass: true,
            preflight_count_derived: decision.preflightCountDerived,
            inference_count_derived: decision.inferenceCountDerived,
            retry_count_derived: decision.retryCountDerived,
          });
        } else {
          process.stdout.write(JSON.stringify({
            ok: false,
            error: "SMOKE_EVALUATION_FAIL",
            failures: decision.failures,
            preflight_count_derived: decision.preflightCountDerived,
            inference_count_derived: decision.inferenceCountDerived,
            retry_count_derived: decision.retryCountDerived,
          }) + "\n");
          process.exit(3);
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : "SMOKE_EVALUATION_FAIL";
        fail(message);
      }
      return;
    }
    case "production-command": {
      const controllerDir = requireFlag(parsed.flags, "controller");
      const taskKey = requireFlag(parsed.flags, "task");
      const worktree = requireFlag(parsed.flags, "worktree");
      const repo = requireFlag(parsed.flags, "repo");
      const taskFile = requireFlag(parsed.flags, "task-file");
      const state = requireFlag(parsed.flags, "state");
      const base = requireFlag(parsed.flags, "base");
      const worktreeRoot = requireFlag(parsed.flags, "worktree-root");
      const spec = buildProductionCommand({
        controllerDir,
        taskKey,
        targetWorktree: worktree,
        repoRoot: repo,
        taskFile,
        statePath: state,
        baseBranch: base,
        worktreeRoot,
      });
      ok({ fileName: spec.fileName, arguments: spec.arguments, workingDirectory: spec.workingDirectory, environment: spec.environment });
      return;
    }
    default:
      fail("SMOKE_CONTROL_USAGE");
  }
}

// Only run the CLI when invoked directly (not when imported by tests).
import { fileURLToPath } from "node:url";
const invokedDirectly = (() => {
  try {
    return process.argv[1] !== undefined &&
      fileURLToPath(import.meta.url) === resolve(process.argv[1]);
  } catch { return false; }
})();
if (invokedDirectly) runCli();
