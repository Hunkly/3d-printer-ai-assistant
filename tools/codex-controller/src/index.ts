import { Codex } from "@openai/codex-sdk";
import type { ThreadEvent, ThreadItem } from "@openai/codex-sdk";
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

  let exists = false;
  try {
    execFileSync("git", ["-C", repo, "show-ref", "--verify", "--quiet", `refs/heads/${branch}`]);
    exists = true;
  } catch {
    exists = false;
  }

  if (exists) {
    git(repo, "switch", branch);
  } else {
    git(repo, "switch", "-c", branch, `origin/${base}`);
  }
}

function printCompletedItem(item: ThreadItem): string | null {
  switch (item.type) {
    case "agent_message":
      console.log(`\nCodex: ${item.text}\n`);
      return item.text;
    case "command_execution": {
      const exitText = item.exit_code !== undefined ? ` (exit ${item.exit_code})` : "";
      console.log(`[command] ${item.command}${exitText}`);
      return null;
    }
    case "file_change":
      for (const change of item.changes) {
        console.log(`[file] ${change.kind}: ${change.path}`);
      }
      return null;
    default:
      return null;
  }
}

function printUpdatedItem(item: ThreadItem): void {
  if (item.type === "todo_list") {
    console.log("[todo]");
    for (const todo of item.items) {
      console.log(`  ${todo.completed ? "x" : "-"} ${todo.text}`);
    }
  }
}

function printEvent(event: ThreadEvent): string | null {
  switch (event.type) {
    case "thread.started":
      console.log(`[codex] thread ${event.thread_id}`);
      return null;
    case "turn.started":
      console.log("[codex] turn started");
      return null;
    case "item.completed":
      return printCompletedItem(event.item);
    case "item.started":
    case "item.updated":
      printUpdatedItem(event.item);
      return null;
    case "turn.completed":
      console.log(
        `[codex] turn completed; input=${event.usage.input_tokens}, cached=${event.usage.cached_input_tokens}, output=${event.usage.output_tokens}`
      );
      return null;
    case "turn.failed":
      throw new Error(`Codex turn failed: ${event.error.message}`);
    case "error":
      throw new Error(`Codex stream failed: ${event.message}`);
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

  console.log(`[controller] task=${taskKey} branch=${branch}`);
  console.log("[controller] starting Codex stream...");

  const { events } = await thread.runStreamed(prompt);
  let finalResponse = "";

  for await (const event of events) {
    const response = printEvent(event);
    if (response !== null) {
      finalResponse = response;
    }
  }

  if (!existing) {
    const threadId = thread.id;
    if (!threadId) {
      throw new Error("Codex did not return a persistent thread ID; cannot save resumable task state.");
    }
    state.threads[taskKey] = { threadId, branch };
    saveState(statePath, state);
  }

  if (!finalResponse) {
    console.warn("[controller] Codex completed without a final agent message.");
  }
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
