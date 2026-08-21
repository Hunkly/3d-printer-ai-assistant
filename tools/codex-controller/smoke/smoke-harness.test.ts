import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  mkdtempSync, rmSync, writeFileSync, existsSync, readFileSync, mkdirSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

// Hermetic harness tests. Spawns `run-smoke.ps1 -DryRun` against temp git repos
// + linked temp worktrees + a temp smoke dir. Never invokes the live path.
// Never reaches OpenRouter or real Codex inference.
//
// Registry note: the harness always resolves the registry under $RepoRoot. To
// keep tests hermetic and self-contained, each test writes a valid registry
// into the temp repo's expected location.

const POWERSHELL = "powershell.exe";
const PS1 = join(process.cwd(), "smoke", "run-smoke.ps1");

const REAL_REGISTRY_PATH = join(process.cwd(), "..", "openrouter-free-selector", "config", "codex-compatible-free-models-v1.json");

function git(args: string[], cwd: string): string {
  return execFileSync("git", args, { cwd, encoding: "utf8" }).trim();
}

function ensureGit(): void {
  execFileSync("git", ["--version"], { encoding: "utf8" });
}

function ensurePowerShell(): void {
  try { execFileSync(POWERSHELL, ["-NoProfile", "-Command", "$PSVersionTable.PSVersion"], { encoding: "utf8" }); }
  catch { throw new Error("PowerShell is required for harness tests"); }
}

function tempRepo(): string {
  const root = mkdtempSync(join(tmpdir(), "smoke-harness-"));
  git(["init", "-q", "-b", "master"], root);
  git(["config", "user.email", "harness@test"], root);
  git(["config", "user.name", "harness"], root);
  // Initial commit so HEAD/branches work.
  writeFileSync(join(root, "README.md"), "harness");
  git(["add", "."], root);
  git(["commit", "-q", "-m", "init"], root);
  // Set up a local bare "origin" so the harness's `git fetch origin master`
  // (mirroring controller `ensureWorktree`) succeeds.
  const origin = mkdtempSync(join(tmpdir(), "smoke-harness-origin-"));
  git(["init", "-q", "--bare", "-b", "master"], origin);
  git(["remote", "add", "origin", origin], root);
  git(["push", "-q", "origin", "master"], root);
  // Copy the real compatibility registry into the expected temp-repo path.
  const registryDir = join(root, "tools", "openrouter-free-selector", "config");
  mkdirSync(registryDir, { recursive: true });
  const tempRegistry = join(registryDir, "codex-compatible-free-models-v1.json");
  if (existsSync(REAL_REGISTRY_PATH)) {
    writeFileSync(tempRegistry, readFileSync(REAL_REGISTRY_PATH, "utf8"), "utf8");
  } else {
    // Synthesize a valid registry so loadRegistry succeeds.
    const now = new Date();
    const valid = new Date(now.getTime() + 30 * 24 * 3600 * 1000).toISOString();
    writeFileSync(tempRegistry, JSON.stringify({
      schema_version: 1,
      entries: [{
        model_id: "nvidia/nemotron-3.5-lightning:free",
        codex_sdk_version: "0.147.0",
        provider_id: "openrouter",
        wire_api: "responses",
        validated_at: now.toISOString(),
        valid_until: valid,
      }],
    }), "utf8");
  }
  return root;
}

function tempSmokeDir(): string {
  // We use a unique LOCALAPPDATA-equivalent dir via env override pattern:
  // the harness reads $env:LOCALAPPDATA directly. We point it at a temp dir
  // by setting LOCALAPPDATA in the child PowerShell process.
  const dir = mkdtempSync(join(tmpdir(), "smoke-harness-local-"));
  mkdirSync(join(dir, "print-engineer-codex", "smoke"), { recursive: true });
  return dir;
}

interface HarnessResult { stdout: string; stderr: string; exitCode: number; }

function runHarnessSync(env: Record<string, string>, args: string[] = []): HarnessResult {
  const result = spawnSync(POWERSHELL, ["-NoProfile", "-File", PS1, ...args], {
    encoding: "utf8",
    env: { ...process.env, ...env },
    maxBuffer: 32 * 1024 * 1024,
  });
  return {
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    exitCode: result.status ?? 1,
  };
}

function writeAuth(smokeDir: string, payload: Record<string, unknown>): string {
  const p = join(smokeDir, "print-engineer-codex", "smoke", "authorization");
  mkdirSync(dirname(p), { recursive: true });
  writeFileSync(p, JSON.stringify(payload), "utf8");
  return p;
}

function validPayload(repoRoot: string, targetWorktree: string, taskKey: string): Record<string, unknown> {
  return {
    schema_version: 1,
    authorization_id: "01234567-89ab-4cde-9012-3456789abcde",
    purpose: "automatic-openrouter-fallback-smoke-v1",
    expected_task_key: taskKey,
    expected_target_worktree: targetWorktree,
    expected_repository_root: repoRoot,
    issued_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
  };
}

function writeMockCmd(tmp: string, lines: string[], exitCode = 0): string {
  // A simple .cmd that prints the lines and exits with the given code.
  const p = join(tmp, "mock.cmd");
  const body = ["@echo off", ...lines, `exit /b ${exitCode}`].join("\r\n");
  writeFileSync(p, body, "utf8");
  return p;
}

function sha256Hex(text: string): string {
  return createHash("sha256").update(text).digest("hex");
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test("harness tests: prerequisites", () => {
  ensureGit();
  ensurePowerShell();
});

test("harness: missing authorization fails closed (no live launch, no PASS)", () => {
  const repo = tempRepo();
  const smoke = tempSmokeDir();
  try {
    const env = {
      LOCALAPPDATA: smoke,
      CODEX_REPO: repo,
      CODEX_WORKTREE_ROOT: join(repo, ".codex-worktrees"),
      CODEX_BASE_BRANCH: "master",
      CODEX_TASK_KEY: "harness-missing",
    };
    const r = runHarnessSync(env, ["-DryRun"]);
    assert.equal(r.exitCode, 1, `expected exit 1, got ${r.exitCode}: ${r.stdout}\n${r.stderr}`);
    assert.equal(r.stdout.includes("SMOKE_RESULT=PASS"), false);
    assert.ok(r.stdout.includes("SMOKE_RESULT=FAIL"));
    assert.ok(/SMOKE_NOT_AUTHORIZED/.test(r.stdout + r.stderr));
    // Mock must not have run: evidence dir should not exist or be empty.
    const evDir = join(smoke, "print-engineer-codex", "smoke");
    assert.equal(existsSync(join(evDir, "controller.stdout.txt")), false);
  } finally { rmSync(repo, { recursive: true, force: true }); rmSync(smoke, { recursive: true, force: true }); }
});

test("harness: failing mock produces FAIL with non-zero exit (no PASS path reached)", () => {
  const repo = tempRepo();
  const smoke = tempSmokeDir();
  try {
    const worktrees = join(repo, ".codex-worktrees");
    const targetWorktree = join(worktrees, "harness-fail");
    git(["worktree", "add", "-b", "codex/harness-fail", targetWorktree, "master"], repo);
    const auth = writeAuth(smoke, validPayload(repo, targetWorktree, "harness-fail"));

    // Mock echoes one line then exits 1.
    const mock = writeMockCmd(repo, ["echo mock-ran-once"], 1);

    const env = {
      LOCALAPPDATA: smoke,
      CODEX_REPO: repo,
      CODEX_WORKTREE_ROOT: worktrees,
      CODEX_BASE_BRANCH: "master",
      CODEX_TASK_KEY: "harness-fail",
      SMOKE_MOCK_CMD: `${mock}`,
    };
    const r = runHarnessSync(env, ["-DryRun"]);
    assert.equal(r.exitCode, 1, `expected exit 1, got ${r.exitCode}: ${r.stdout}\n${r.stderr}`);
    assert.equal(r.stdout.includes("SMOKE_RESULT=PASS"), false);
    assert.ok(r.stdout.includes("SMOKE_RESULT=FAIL"));
    // Mock output captured to controller.stdout.txt exactly once.
    const stdoutPath = join(smoke, "print-engineer-codex", "smoke", "controller.stdout.txt");
    assert.equal(existsSync(stdoutPath), true);
    const stdoutText = readFileSync(stdoutPath, "utf8");
    const matches = (stdoutText.match(/mock-ran-once/g) ?? []).length;
    assert.equal(matches, 1, `mock should have run exactly once, got ${matches}`);
    // Authorization consumed.
    assert.equal(existsSync(auth), false);
  } finally { rmSync(repo, { recursive: true, force: true }); rmSync(smoke, { recursive: true, force: true }); }
});

test("harness: succeeding mock + valid fixtures emits PASS exactly once and exit 0", () => {
  const repo = tempRepo();
  const smoke = tempSmokeDir();
  try {
    const worktrees = join(repo, ".codex-worktrees");
    const targetWorktree = join(worktrees, "harness-pass");
    git(["worktree", "add", "-b", "codex/harness-pass", targetWorktree, "master"], repo);
    const auth = writeAuth(smoke, validPayload(repo, targetWorktree, "harness-pass"));

    // Mock that exits 0 and emits a pass-style controller stdout.
    // We intentionally do NOT provide a real readonly provenance file,
    // so the harness evaluates FAIL on "readonly_provenance_absent" unless
    // we synthesize a valid one. We do that below.
    const mockLines = [
      "echo [controller] task=harness-pass branch=codex/harness-pass phase=general thread=resume provider=openrouter-free model=nvidia/nemotron-3.5-lightning:free",
      "echo [codex] thread 0c9d0f01-77aa-4f8d-bb13-5c7c9e0d3e77",
      "echo [codex] turn completed; input=10, cached=2, uncached=8, output=4",
      "echo Codex: hi",
    ];
    const mock = writeMockCmd(repo, mockLines, 0);

    // Synthesize a state file so the harness can pick up modelIdentity, etc.
    const smokeDir = join(smoke, "print-engineer-codex", "smoke");
    mkdirSync(smokeDir, { recursive: true });
    const statePath = join(smokeDir, "controller-state.json");
    writeFileSync(statePath, JSON.stringify({
      threads: {
        "harness-pass": {
          schemaVersion: 2,
          threadId: "0c9d0f01-77aa-4f8d-bb13-5c7c9e0d3e77",
          branch: "codex/harness-pass",
          worktree: targetWorktree,
          providerMode: "openrouter-free",
          modelIdentity: "nvidia/nemotron-3.5-lightning:free",
          role: "general",
        },
      },
    }, null, 2), "utf8");

    // Synthesize a deterministic readonly execution record.
    const wtCommonDir = git(["rev-parse", "--path-format=absolute", "--git-common-dir"], targetWorktree);
    const recordDir = join(wtCommonDir, "print-engineer", "model-runner", "selector-v1", "readonly-executions");
    mkdirSync(recordDir, { recursive: true });
    const keyInput = `harness-pass|${targetWorktree}`;
    const hash = sha256Hex(keyInput);
    const recordPath = join(recordDir, `${hash}.json`);
    writeFileSync(recordPath, JSON.stringify({
      schema_version: 1,
      kind: "readonly_execution",
      task_key: "harness-pass",
      worktree_path: targetWorktree,
      worktree_state_sha256: "0".repeat(64),
      provider_id: "openrouter",
      model_id: "nvidia/nemotron-3.5-lightning:free",
      phase: "general",
      role: "readonly",
      completed_at: new Date().toISOString(),
      success: true,
    }, null, 2), "utf8");

    const env = {
      LOCALAPPDATA: smoke,
      CODEX_REPO: repo,
      CODEX_WORKTREE_ROOT: worktrees,
      CODEX_BASE_BRANCH: "master",
      CODEX_TASK_KEY: "harness-pass",
      SMOKE_MOCK_CMD: `${mock}`,
    };
    const r = runHarnessSync(env, ["-DryRun"]);
    assert.equal(r.exitCode, 0, `expected exit 0, got ${r.exitCode}: ${r.stdout}\n${r.stderr}`);
    assert.equal(r.stdout.includes("SMOKE_RESULT=PASS"), true);
    const passCount = (r.stdout.match(/SMOKE_RESULT=PASS/g) ?? []).length;
    assert.equal(passCount, 1, `PASS should appear exactly once, got ${passCount}`);
    // Authorization consumed.
    assert.equal(existsSync(auth), false);
  } finally { rmSync(repo, { recursive: true, force: true }); rmSync(smoke, { recursive: true, force: true }); }
});

test("harness: consumed authorization cannot be reused (second invocation -> NOT_AUTHORIZED)", () => {
  const repo = tempRepo();
  const smoke = tempSmokeDir();
  try {
    const worktrees = join(repo, ".codex-worktrees");
    const targetWorktree = join(worktrees, "harness-reuse");
    git(["worktree", "add", "-b", "codex/harness-reuse", targetWorktree, "master"], repo);
    const auth = writeAuth(smoke, validPayload(repo, targetWorktree, "harness-reuse"));
    const mock = writeMockCmd(repo, ["echo mock-1"], 1);
    const env = {
      LOCALAPPDATA: smoke,
      CODEX_REPO: repo,
      CODEX_WORKTREE_ROOT: worktrees,
      CODEX_BASE_BRANCH: "master",
      CODEX_TASK_KEY: "harness-reuse",
      SMOKE_MOCK_CMD: `${mock}`,
    };
    // First invocation consumes the auth (FAIL because mock exits 1).
    const r1 = runHarnessSync(env, ["-DryRun"]);
    assert.equal(r1.exitCode, 1);
    assert.equal(existsSync(auth), false);
    // Second invocation: auth is gone -> SMOKE_NOT_AUTHORIZED, no live launch.
    const r2 = runHarnessSync(env, ["-DryRun"]);
    assert.equal(r2.exitCode, 1);
    assert.ok(/SMOKE_NOT_AUTHORIZED/.test(r2.stdout + r2.stderr));
    // The mock output is captured to controller.stdout.txt. After the second
    // invocation the smoke dir is wiped, so we read r1's evidence file.
    const stdoutPath = join(smoke, "print-engineer-codex", "smoke", "controller.stdout.txt");
    assert.equal(existsSync(stdoutPath), true, "first invocation must capture mock output");
    const stdoutText = readFileSync(stdoutPath, "utf8");
    const runs = (stdoutText.match(/mock-1/g) ?? []).length;
    assert.equal(runs, 1, `mock should have run exactly once across two invocations, got ${runs}`);
  } finally { rmSync(repo, { recursive: true, force: true }); rmSync(smoke, { recursive: true, force: true }); }
});

test("harness: secret echoed by mock never appears in harness stdout/stderr/evidence", () => {
  const repo = tempRepo();
  const smoke = tempSmokeDir();
  try {
    const worktrees = join(repo, ".codex-worktrees");
    const targetWorktree = join(worktrees, "harness-secret");
    git(["worktree", "add", "-b", "codex/harness-secret", targetWorktree, "master"], repo);
    writeAuth(smoke, validPayload(repo, targetWorktree, "harness-secret"));
    const fakeSecret = "sk-or-fake-secret-0123456789abcdef";
    const fakeBearer = "Bearer eyJhbGciOi.fake.token";
    const mockLines = [
      `echo OPENROUTER_API_KEY=${fakeSecret}`,
      `echo Authorization: ${fakeBearer}`,
      "echo done",
    ];
    const mock = writeMockCmd(repo, mockLines, 1);
    const env = {
      LOCALAPPDATA: smoke,
      CODEX_REPO: repo,
      CODEX_WORKTREE_ROOT: worktrees,
      CODEX_BASE_BRANCH: "master",
      CODEX_TASK_KEY: "harness-secret",
      SMOKE_MOCK_CMD: `${mock}`,
    };
    const r = runHarnessSync(env, ["-DryRun"]);
    // The mock output is captured verbatim to controller.stdout.txt by the
    // harness, which is the harness's own evidence file. The PLAN §10/§14
    // requires redaction at capture/persistence boundaries: the harness must
    // therefore NOT contain the raw secret in its own stdout/stderr AND must
    // not persist it in any file. We assert that no captured evidence file
    // contains the raw secret.
    const evDir = join(smoke, "print-engineer-codex", "smoke");
    for (const file of ["controller.stdout.txt", "controller.stderr.txt", "evidence.json"]) {
      const p = join(evDir, file);
      if (existsSync(p)) {
        const text = readFileSync(p, "utf8");
        assert.equal(text.includes(fakeSecret), false, `${file} must not contain raw secret`);
        assert.equal(text.includes(fakeBearer), false, `${file} must not contain raw bearer token`);
      }
    }
    // Harness own stdout/stderr must never carry the secret.
    assert.equal((r.stdout + r.stderr).includes(fakeSecret), false);
    assert.equal((r.stdout + r.stderr).includes(fakeBearer), false);
  } finally { rmSync(repo, { recursive: true, force: true }); rmSync(smoke, { recursive: true, force: true }); }
});

test("harness: mocked production command is invoked exactly once per invocation", () => {
  const repo = tempRepo();
  const smoke = tempSmokeDir();
  try {
    const worktrees = join(repo, ".codex-worktrees");
    const targetWorktree = join(worktrees, "harness-once");
    git(["worktree", "add", "-b", "codex/harness-once", targetWorktree, "master"], repo);
    writeAuth(smoke, validPayload(repo, targetWorktree, "harness-once"));
    // Mock writes a marker that includes an invocation count to a side-effect file.
    // We rely on the harness persisting controller.stdout.txt to count calls.
    const markerPath = join(smoke, "print-engineer-codex", "smoke", "controller.stdout.txt");
    // The mock uses `echo` to print a single line; we verify the line appears
    // exactly once (proving the mock ran once).
    const mock = writeMockCmd(repo, ["echo SMOKE_MARKER_ONE_RUN"], 1);
    const env = {
      LOCALAPPDATA: smoke,
      CODEX_REPO: repo,
      CODEX_WORKTREE_ROOT: worktrees,
      CODEX_BASE_BRANCH: "master",
      CODEX_TASK_KEY: "harness-once",
      SMOKE_MOCK_CMD: `${mock}`,
    };
    runHarnessSync(env, ["-DryRun"]);
    assert.equal(existsSync(markerPath), true);
    const content = readFileSync(markerPath, "utf8");
    const occurrences = (content.match(/SMOKE_MARKER_ONE_RUN/g) ?? []).length;
    assert.equal(occurrences, 1, `mock must run exactly once; got ${occurrences}`);
  } finally { rmSync(repo, { recursive: true, force: true }); rmSync(smoke, { recursive: true, force: true }); }
});

test("harness: worktree mutation between pre and post capture fails closed", () => {
  const repo = tempRepo();
  const smoke = tempSmokeDir();
  try {
    const worktrees = join(repo, ".codex-worktrees");
    const targetWorktree = join(worktrees, "harness-mut");
    git(["worktree", "add", "-b", "codex/harness-mut", targetWorktree, "master"], repo);
    writeAuth(smoke, validPayload(repo, targetWorktree, "harness-mut"));
    // Mock mutates the worktree (adds a file) AND emits controller markers.
    const mockLines = [
      `echo mutated > "${join(targetWorktree, "mutated.txt").replace(/\//g, "\\")}"`,
      "echo [controller] task=harness-mut branch=codex/harness-mut phase=general thread=resume provider=openrouter-free model=nvidia/nemotron-3.5-lightning:free",
      "echo [codex] thread 0c9d0f01-77aa-4f8d-bb13-5c7c9e0d3e77",
      "echo [codex] turn completed",
      "echo Codex: ok",
    ];
    const mock = writeMockCmd(repo, mockLines, 0);
    const env = {
      LOCALAPPDATA: smoke,
      CODEX_REPO: repo,
      CODEX_WORKTREE_ROOT: worktrees,
      CODEX_BASE_BRANCH: "master",
      CODEX_TASK_KEY: "harness-mut",
      SMOKE_MOCK_CMD: `${mock}`,
    };
    const r = runHarnessSync(env, ["-DryRun"]);
    assert.equal(r.exitCode, 1);
    assert.ok(r.stdout.includes("SMOKE_RESULT=FAIL"));
    // The mutation must NOT be cleaned up; the harness never repairs the
    // worktree. Verify the mutated file is still there.
    assert.equal(existsSync(join(targetWorktree, "mutated.txt")), true,
      "harness must never auto-clean mutated worktree files");
  } finally { rmSync(repo, { recursive: true, force: true }); rmSync(smoke, { recursive: true, force: true }); }
});

test("harness: encoding round-trip — non-ASCII stdout from mock survives capture and parse", () => {
  const repo = tempRepo();
  const smoke = tempSmokeDir();
  try {
    const worktrees = join(repo, ".codex-worktrees");
    const targetWorktree = join(worktrees, "harness-utf8");
    git(["worktree", "add", "-b", "codex/harness-utf8", targetWorktree, "master"], repo);
    writeAuth(smoke, validPayload(repo, targetWorktree, "harness-utf8"));
    const nonAscii = "héllo αβγ — 世界";
    // The mock switches the cmd console to UTF-8 (chcp 65001) so node's
    // UTF-8 stdout survives intact through cmd.exe to the harness.
    const mockLines = [
      "chcp 65001 > nul",
      `node -e "process.stdout.write('${nonAscii}\\n')"`,
      "echo [codex] turn completed",
    ];
    const mock = writeMockCmd(repo, mockLines, 1);
    const env = {
      LOCALAPPDATA: smoke,
      CODEX_REPO: repo,
      CODEX_WORKTREE_ROOT: worktrees,
      CODEX_BASE_BRANCH: "master",
      CODEX_TASK_KEY: "harness-utf8",
      SMOKE_MOCK_CMD: `${mock}`,
    };
    runHarnessSync(env, ["-DryRun"]);
    // The captured stdout must round-trip the non-ASCII characters.
    const stdoutPath = join(smoke, "print-engineer-codex", "smoke", "controller.stdout.txt");
    assert.equal(existsSync(stdoutPath), true);
    const raw = readFileSync(stdoutPath, "utf8");
    assert.equal(raw.includes(nonAscii), true, `captured stdout must contain "${nonAscii}"`);
    // No UTF-16LE NUL bytes (i.e. no PowerShell `>` redirection residue).
    assert.equal(raw.includes(" "), false,
      "captured stdout must not contain UTF-16 NUL bytes from redirection");
    // No BOM before the file content.
    assert.notEqual(raw.charCodeAt(0), 0xfeff, "captured stdout must not start with a BOM");
  } finally { rmSync(repo, { recursive: true, force: true }); rmSync(smoke, { recursive: true, force: true }); }
});

test("harness: dry-run never invokes the real live command (only the mock)", () => {
  // Structural guarantee: the .ps1's live branch (without -DryRun) is
  // explicitly blocked by SMOKE_NOT_AUTHORIZED. With -DryRun, only the
  // mock is invoked. Verify by ensuring no node dist/src/index.js is launched.
  // We do this by setting SMOKE_MOCK_CMD to a sentinel that records its own
  // argv and asserting that dist/src/index.js does not appear anywhere.
  const repo = tempRepo();
  const smoke = tempSmokeDir();
  try {
    const worktrees = join(repo, ".codex-worktrees");
    const targetWorktree = join(worktrees, "harness-dry");
    git(["worktree", "add", "-b", "codex/harness-dry", targetWorktree, "master"], repo);
    writeAuth(smoke, validPayload(repo, targetWorktree, "harness-dry"));
    const argLog = join(repo, "argv.txt");
    const mock = writeMockCmd(repo, [`echo %* > "${argLog.replace(/\\/g, "\\\\")}"`], 1);
    const env = {
      LOCALAPPDATA: smoke,
      CODEX_REPO: repo,
      CODEX_WORKTREE_ROOT: worktrees,
      CODEX_BASE_BRANCH: "master",
      CODEX_TASK_KEY: "harness-dry",
      SMOKE_MOCK_CMD: `${mock}`,
    };
    const r = runHarnessSync(env, ["-DryRun"]);
    const recorded = existsSync(argLog) ? readFileSync(argLog, "utf8") : "";
    assert.equal(recorded.includes("dist\\src\\index.js"), false,
      "dry-run must never invoke dist/src/index.js");
    assert.equal(recorded.includes("dist/src/index.js"), false,
      "dry-run must never invoke dist/src/index.js");
    assert.equal(recorded.includes("--select-only"), false,
      "harness must never invoke --select-only");
    assert.equal(recorded.includes("--compatibility-probe"), false,
      "harness must never invoke --compatibility-probe");
    // Harness returned FAIL (mock exits 1, plus missing fixtures).
    assert.equal(r.exitCode, 1);
  } finally { rmSync(repo, { recursive: true, force: true }); rmSync(smoke, { recursive: true, force: true }); }
});

test("harness: existing branch without worktree is reused (worktree add, no -b)", () => {
  const repo = tempRepo();
  const smoke = tempSmokeDir();
  try {
    const worktrees = join(repo, ".codex-worktrees");
    const targetWorktree = join(worktrees, "harness-existing-branch");
    // Pre-create the branch on the repo side, but no worktree yet.
    git(["branch", "codex/harness-existing-branch"], repo);
    writeAuth(smoke, validPayload(repo, targetWorktree, "harness-existing-branch"));
    const mock = writeMockCmd(repo, ["echo ran"], 1);
    const env = {
      LOCALAPPDATA: smoke,
      CODEX_REPO: repo,
      CODEX_WORKTREE_ROOT: worktrees,
      CODEX_BASE_BRANCH: "master",
      CODEX_TASK_KEY: "harness-existing-branch",
      SMOKE_MOCK_CMD: `${mock}`,
    };
    const r = runHarnessSync(env, ["-DryRun"]);
    // Mock exits 1 so this is a FAIL smoke but it must have reached the
    // mock stage (proving the worktree was created).
    assert.equal(existsSync(targetWorktree), true, "worktree must be created for existing branch path");
    assert.equal(r.exitCode, 1);
  } finally { rmSync(repo, { recursive: true, force: true }); rmSync(smoke, { recursive: true, force: true }); }
});

test("harness: fresh branch is created via -b from origin/base", () => {
  // tempRepo already sets up a local bare origin and pushes master to it.
  const repo = tempRepo();
  const smoke = tempSmokeDir();
  try {
    const worktrees = join(repo, ".codex-worktrees");
    const targetWorktree = join(worktrees, "harness-fresh-branch");
    writeAuth(smoke, validPayload(repo, targetWorktree, "harness-fresh-branch"));
    const mock = writeMockCmd(repo, ["echo ran"], 1);
    const env = {
      LOCALAPPDATA: smoke,
      CODEX_REPO: repo,
      CODEX_WORKTREE_ROOT: worktrees,
      CODEX_BASE_BRANCH: "master",
      CODEX_TASK_KEY: "harness-fresh-branch",
      SMOKE_MOCK_CMD: `${mock}`,
    };
    const r = runHarnessSync(env, ["-DryRun"]);
    assert.equal(existsSync(targetWorktree), true, "fresh worktree must be created via -b origin/master");
    assert.equal(r.exitCode, 1);
  } finally { rmSync(repo, { recursive: true, force: true }); rmSync(smoke, { recursive: true, force: true }); }
});

test("harness: missing CODEX_TASK_KEY fails closed with INVALID_TASK_KEY", () => {
  const repo = tempRepo();
  const smoke = tempSmokeDir();
  try {
    const env = {
      LOCALAPPDATA: smoke,
      CODEX_REPO: repo,
      CODEX_WORKTREE_ROOT: join(repo, ".codex-worktrees"),
      CODEX_BASE_BRANCH: "master",
      // Force the task key to be unset/empty regardless of ambient environment.
      CODEX_TASK_KEY: "",
    };
    const r = runHarnessSync(env, ["-DryRun"]);
    assert.equal(r.exitCode, 1);
    assert.ok(r.stdout.includes("SMOKE_RESULT=FAIL"));
    assert.ok(/INVALID_TASK_KEY/.test(r.stdout + r.stderr));
  } finally { rmSync(repo, { recursive: true, force: true }); rmSync(smoke, { recursive: true, force: true }); }
});

test("harness: expired authorization propagates SMOKE_AUTHORIZATION_EXPIRED", () => {
  const repo = tempRepo();
  const smoke = tempSmokeDir();
  try {
    const worktrees = join(repo, ".codex-worktrees");
    const targetWorktree = join(worktrees, "harness-expired");
    git(["worktree", "add", "-b", "codex/harness-expired", targetWorktree, "master"], repo);
    const payload = validPayload(repo, targetWorktree, "harness-expired");
    payload.issued_at = "2020-01-01T00:00:00.000Z";
    payload.expires_at = "2020-01-02T00:00:00.000Z";
    writeAuth(smoke, payload);
    const r = runHarnessSync({ ...{
      LOCALAPPDATA: smoke,
      CODEX_REPO: repo,
      CODEX_WORKTREE_ROOT: worktrees,
      CODEX_BASE_BRANCH: "master",
      CODEX_TASK_KEY: "harness-expired",
    } }, ["-DryRun"]);
    assert.equal(r.exitCode, 1);
    assert.ok(/SMOKE_AUTHORIZATION_EXPIRED/.test(r.stdout + r.stderr));
  } finally { rmSync(repo, { recursive: true, force: true }); rmSync(smoke, { recursive: true, force: true }); }
});

test("harness: target-mismatched authorization propagates SMOKE_AUTHORIZATION_TARGET_MISMATCH", () => {
  const repo = tempRepo();
  const smoke = tempSmokeDir();
  try {
    const worktrees = join(repo, ".codex-worktrees");
    const targetWorktree = join(worktrees, "harness-mismatch");
    git(["worktree", "add", "-b", "codex/harness-mismatch", targetWorktree, "master"], repo);
    const payload = validPayload(repo, targetWorktree, "harness-mismatch");
    payload.expected_target_worktree = join(worktrees, "harness-other");
    writeAuth(smoke, payload);
    const r = runHarnessSync({ ...{
      LOCALAPPDATA: smoke,
      CODEX_REPO: repo,
      CODEX_WORKTREE_ROOT: worktrees,
      CODEX_BASE_BRANCH: "master",
      CODEX_TASK_KEY: "harness-mismatch",
    } }, ["-DryRun"]);
    assert.equal(r.exitCode, 1);
    assert.ok(/SMOKE_AUTHORIZATION_TARGET_MISMATCH/.test(r.stdout + r.stderr));
  } finally { rmSync(repo, { recursive: true, force: true }); rmSync(smoke, { recursive: true, force: true }); }
});

test("harness: production-command subcommand emits the real live spec (authorized non-DryRun boundary)", () => {
  // The .ps1 live branch consumes the spec from this hermetic subcommand. This
  // proves the authorized non-DryRun path constructs the REAL production
  // command (`node dist/src/index.js`, no forbidden flags) without any
  // external inference.
  const controlJs = join(process.cwd(), "dist", "smoke", "smoke-control.js");
  const out = execFileSync("node", [
    controlJs, "production-command",
    "--controller=C:/tools/codex-controller",
    "--task=smoke-readonly-v1",
    "--worktree=C:/wt/smoke-readonly-v1",
    "--repo=C:/repo",
    "--task-file=C:/tools/codex-controller/smoke/task.txt",
    "--state=C:/AppData/print-engineer-codex/smoke/controller-state.json",
    "--base=master",
    "--worktree-root=C:/wt",
  ], { encoding: "utf8" });
  const parsed = JSON.parse(out);
  assert.equal(parsed.ok, true);
  assert.equal(parsed.fileName, "node");
  assert.deepEqual(parsed.arguments, ["dist/src/index.js"]);
  assert.equal(parsed.arguments.includes("--select-only"), false);
  assert.equal(parsed.arguments.includes("--compatibility-probe"), false);
  assert.equal(parsed.workingDirectory, "C:/tools/codex-controller");
  assert.equal(parsed.environment.CODEX_PROVIDER_MODE, "auto");
  assert.equal(parsed.environment.CODEX_TASK_KEY, "smoke-readonly-v1");
  assert.equal(parsed.environment.MODEL_TASK_FILE, "C:/tools/codex-controller/smoke/task.txt");
  assert.equal(parsed.environment.CODEX_STATE, "C:/AppData/print-engineer-codex/smoke/controller-state.json");
});

test("harness: ConvertTo-NativeArgument produces Windows-safe command-line escaping", () => {
  // Import the REAL escaping function from run-smoke.ps1 by dot-sourcing the
  // orchestrator (its main flow is guarded so dot-sourcing only defines the
  // helper functions). Verifies spaces, embedded quotes, and trailing
  // backslashes are preserved through ProcessStartInfo argument construction.
  const psScript = [
    "& {",
    `  . '${PS1.replace(/'/g, "''")}'`,
    "  $out = [ordered]@{",
    "    ordinary = (ConvertTo-NativeArgument 'plain')",
    "    spaced = (ConvertTo-NativeArgument 'C:/path with space/x y.js')",
    "    quoted = (ConvertTo-NativeArgument 'has \"quote\" inside')",
    "    trailing = (ConvertTo-NativeArgument 'ends\\')",
    "    trailingSpaced = (ConvertTo-NativeArgument 'C:\\dir with space\\')",
    "    empty = (ConvertTo-NativeArgument '')",
    "  }",
    "  $out | ConvertTo-Json -Compress",
    "}",
  ].join("\n");
  const out = execFileSync(POWERSHELL, ["-NoProfile", "-Command", psScript], { encoding: "utf8" }).trim();
  const result = JSON.parse(out) as Record<string, string>;
  assert.equal(result.ordinary, "plain");
  assert.equal(result.spaced, '"C:/path with space/x y.js"');
  assert.equal(result.quoted, '"has \\"quote\\" inside"');
  assert.equal(result.trailing, "ends\\");
  assert.equal(result.trailingSpaced, '"C:\\dir with space\\\\"');
  assert.equal(result.empty, '""');
});
