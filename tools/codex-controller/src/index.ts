import { Codex } from "@openai/codex-sdk";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

interface ControllerState {
  threads: Record<string, { threadId: string; branch: string }>;
}

function git(repo: string, ...args: string[]): string {
  return execFileSync("git", ["-C", repo, ...args], { encoding: "utf8" }).trim();
}

function requireSafeWorkingTree(repo: string): void {
  const lines = git(repo, "status", "--porcelain")
    .split("\n")
    .filter(Boolean);
  const trackedChanges = lines.filter((line) => !line.startsWith("??"));

  if (trackedChanges.length > 0) {
    throw new Error(
      "Working tree has tracked changes. Commit/stash them before letting the controller switch branches."
    );
  }

  const untracked = lines.filter((line) => line.startsWith("??"));
  if (untracked.length > 0) {
    console.warn(
      "Leaving untracked files untouched while switching branches:\n" +
        untracked.map((line) => `  ${line.slice(3)}`).join("\n")
    );
  }
}

function loadState(path: string): ControllerState {
  try {
    return JSON.parse(readFileSync(path, "utf8")) as ControllerState;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return { threads: {} };
    }
    throw error;
  }
}

function saveState(path: string, state: ControllerState): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(state, null, 2) + "\n", "utf8");
}

function ensureTaskBranch(repo: string, branch: string, base: string): void {
  requireSafeWorkingTree(repo);
  git(repo, "fetch", "origin", base);

  const exists = (() => {
    try {
      git(repo, "rev-parse", "--verify", branch);
      return true;
    } catch {
      return false;
    }
  })();

  if (exists) {
    git(repo, "switch", branch);
  } else {
    git(repo, "switch", "-c", branch, `origin/${base}`);
  }
}

async function main(): Promise<void> {
  const taskKey = process.env.CODEX_TASK_KEY;
  const task = process.env.CODEX_TASK;
  const repo = resolve(process.env.CODEX_REPO ?? "../..");
  const base = process.env.CODEX_BASE_BRANCH ?? "master";
  const statePath = resolve(process.env.CODEX_STATE ?? ".codex/controller-state.json");

  if (!taskKey || !task) {
    throw new Error("Set CODEX_TASK_KEY and CODEX_TASK before starting the controller.");
  }

  const branch = `codex/${taskKey}`;
  ensureTaskBranch(repo, branch, base);

  const state = loadState(statePath);
  const codex = new Codex();
  const existing = state.threads[taskKey];
  const thread = existing
    ? codex.resumeThread(existing.threadId, { workingDirectory: repo })
    : codex.startThread({ workingDirectory: repo });

  const prompt = [
    "Follow the repository AGENTS.md execution contract.",
    "Work only on the task below. Do not push, merge, or open a pull request.",
    "Inspect git status before editing. Run the focused tests and applicable Ruff/Mypy checks required by AGENTS.md.",
    "At the end, inspect git status, git diff --stat, and git diff, then report what changed and verification results.",
    "",
    `Task: ${task}`,
  ].join("\n");

  const result = await thread.run(prompt);

  if (!existing) {
    const threadId = thread.id;
    if (!threadId) {
      throw new Error("Codex did not return a persistent thread ID; cannot save resumable task state.");
    }
    state.threads[taskKey] = { threadId, branch };
    saveState(statePath, state);
  }

  process.stdout.write(result.finalResponse + "\n");
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
