import { Codex } from "@openai/codex-sdk";
import type { ThreadEvent, ThreadItem } from "@openai/codex-sdk";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

interface TaskState {
  threadId: string;
  branch: string;
  worktree?: string;
  issueNumber?: number;
  prUrl?: string;
}

interface ControllerState {
  threads: Record<string, TaskState>;
}

interface GitHubIssue {
  number: number;
  title: string;
  body: string;
  state: string;
  url: string;
}

function run(command: string, args: string[], cwd?: string): string {
  return execFileSync(command, args, {
    cwd,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  }).trim();
}

function git(repo: string, ...args: string[]): string {
  return run("git", ["-C", repo, ...args]);
}

function gh(repo: string, ...args: string[]): string {
  return run("gh", args, repo);
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

function assertGitHubCli(repo: string): void {
  try {
    gh(repo, "auth", "status");
  } catch {
    throw new Error(
      "GitHub CLI is missing or not authenticated. Install gh, then run `gh auth login` once."
    );
  }
}

function getRepositoryName(repo: string): string {
  return gh(repo, "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner");
}

function readIssue(repo: string, repository: string, issueNumber: number): GitHubIssue {
  const raw = gh(
    repo,
    "issue",
    "view",
    String(issueNumber),
    "--repo",
    repository,
    "--json",
    "number,title,body,state,url"
  );
  const issue = JSON.parse(raw) as GitHubIssue;
  if (issue.state.toUpperCase() !== "OPEN") {
    throw new Error(`Issue #${issue.number} is not open; refusing to start new work from it.`);
  }
  return issue;
}

function ensureWorktree(
  repo: string,
  taskKey: string,
  branch: string,
  base: string,
  worktreeRoot: string
): string {
  git(repo, "fetch", "origin", base);
  const worktree = resolve(worktreeRoot, taskKey);
  mkdirSync(worktreeRoot, { recursive: true });

  try {
    git(repo, "worktree", "list", "--porcelain");
    if (git(repo, "worktree", "list", "--porcelain").includes(`worktree ${worktree}`)) {
      return worktree;
    }
  } catch {
    // Fall through and create it.
  }

  let branchExists = true;
  try {
    run("git", ["-C", repo, "show-ref", "--verify", "--quiet", `refs/heads/${branch}`]);
  } catch {
    branchExists = false;
  }

  if (branchExists) {
    git(repo, "worktree", "add", worktree, branch);
  } else {
    git(repo, "worktree", "add", "-b", branch, worktree, `origin/${base}`);
  }
  return worktree;
}

function listUntracked(repo: string): Set<string> {
  const output = execFileSync("git", ["-C", repo, "ls-files", "--others", "--exclude-standard", "-z"], {
    encoding: "utf8",
  });
  return new Set(output.split("\0").filter(Boolean));
}

function stageCodexChanges(repo: string, baselineUntracked: Set<string>): void {
  git(repo, "add", "-u");
  const after = listUntracked(repo);
  for (const path of after) {
    if (!baselineUntracked.has(path)) {
      git(repo, "add", "--", path);
    }
  }
}

function hasStagedChanges(repo: string): boolean {
  try {
    execFileSync("git", ["-C", repo, "diff", "--cached", "--quiet"]);
    return false;
  } catch {
    return true;
  }
}

function commitAndPush(repo: string, branch: string, title: string): string | null {
  if (!hasStagedChanges(repo)) {
    console.log("[controller] no Codex changes to commit");
    return null;
  }

  const commitTitle = `Codex: ${title}`.slice(0, 72);
  git(repo, "commit", "-m", commitTitle);
  git(repo, "push", "-u", "origin", branch);
  return git(repo, "rev-parse", "HEAD");
}

function findOrCreateDraftPr(
  repo: string,
  repository: string,
  branch: string,
  base: string,
  issue: GitHubIssue,
  finalResponse: string
): string {
  try {
    return gh(repo, "pr", "view", branch, "--repo", repository, "--json", "url", "--jq", ".url");
  } catch {
    const body = [
      `Implements #${issue.number}.`,
      "",
      "## Codex report",
      "",
      finalResponse || "Codex completed without a final textual report.",
      "",
      "---",
      "Created automatically by the local Codex controller. This PR is intentionally a draft pending review.",
    ].join("\n");

    return gh(
      repo,
      "pr",
      "create",
      "--repo",
      repository,
      "--draft",
      "--base",
      base,
      "--head",
      branch,
      "--title",
      issue.title,
      "--body",
      body
    );
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

async function runCodexTask(
  taskKey: string,
  task: string,
  workingDirectory: string,
  branch: string,
  state: ControllerState,
  statePath: string
): Promise<string> {
  const codex = new Codex();
  const existing = state.threads[taskKey];
  const thread = existing
    ? codex.resumeThread(existing.threadId, { workingDirectory })
    : codex.startThread({ workingDirectory });

  const prompt = [
    "Follow the repository AGENTS.md execution contract.",
    "Work only on the task below. Do not push, merge, commit, or open a pull request; the controller owns Git publication.",
    "Do not inspect the whole repository unless the task genuinely requires it. Start from the files/plans named in the task and follow direct dependencies only as needed.",
    "Inspect git status before editing. Run focused tests and applicable Ruff/Mypy checks required by AGENTS.md.",
    "At the end, inspect git status, git diff --stat, and git diff, then report what changed and verification results.",
    "",
    `Task: ${task}`,
  ].join("\n");

  console.log(`[controller] task=${taskKey} branch=${branch}`);
  console.log(`[controller] worktree=${workingDirectory}`);
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
    state.threads[taskKey] = { threadId, branch, worktree: workingDirectory };
    saveState(statePath, state);
  }

  return finalResponse;
}

async function runGitHubIssueMode(repo: string, issueNumber: number, base: string, statePath: string): Promise<void> {
  assertGitHubCli(repo);
  const repository = process.env.CODEX_GITHUB_REPO ?? getRepositoryName(repo);
  const issue = readIssue(repo, repository, issueNumber);
  const taskKey = `issue-${issue.number}`;
  const branch = `codex/${taskKey}`;
  const worktreeRoot = resolve(process.env.CODEX_WORKTREE_ROOT ?? "../../../.codex-worktrees");
  const state = loadState(statePath);
  const worktree = ensureWorktree(repo, taskKey, branch, base, worktreeRoot);
  const baselineUntracked = listUntracked(worktree);

  const task = [`GitHub issue #${issue.number}: ${issue.title}`, "", issue.body].join("\n");
  const finalResponse = await runCodexTask(taskKey, task, worktree, branch, state, statePath);

  stageCodexChanges(worktree, baselineUntracked);
  const commit = commitAndPush(worktree, branch, issue.title);
  if (!commit) {
    console.log("[controller] task completed without code changes; no PR created");
    return;
  }

  const prUrl = findOrCreateDraftPr(worktree, repository, branch, base, issue, finalResponse);
  state.threads[taskKey] = {
    ...state.threads[taskKey],
    branch,
    worktree,
    issueNumber: issue.number,
    prUrl,
  };
  saveState(statePath, state);
  console.log(`[controller] pushed ${commit}`);
  console.log(`[controller] draft PR: ${prUrl}`);
}

async function runManualMode(repo: string, base: string, statePath: string): Promise<void> {
  const taskKey = process.env.CODEX_TASK_KEY;
  const task = process.env.CODEX_TASK;
  if (!taskKey || !task) {
    throw new Error(
      "Set CODEX_ISSUE_NUMBER for GitHub mode, or CODEX_TASK_KEY and CODEX_TASK for manual mode."
    );
  }

  const branch = `codex/${taskKey}`;
  const worktreeRoot = resolve(process.env.CODEX_WORKTREE_ROOT ?? "../../../.codex-worktrees");
  const worktree = ensureWorktree(repo, taskKey, branch, base, worktreeRoot);
  const state = loadState(statePath);
  await runCodexTask(taskKey, task, worktree, branch, state, statePath);
}

async function main(): Promise<void> {
  const repo = resolve(process.env.CODEX_REPO ?? "../..");
  const base = process.env.CODEX_BASE_BRANCH ?? "master";
  const statePath = resolve(process.env.CODEX_STATE ?? ".codex/controller-state.json");
  const issueValue = process.env.CODEX_ISSUE_NUMBER;

  if (issueValue) {
    const issueNumber = Number(issueValue);
    if (!Number.isInteger(issueNumber) || issueNumber <= 0) {
      throw new Error("CODEX_ISSUE_NUMBER must be a positive integer.");
    }
    await runGitHubIssueMode(repo, issueNumber, base, statePath);
  } else {
    await runManualMode(repo, base, statePath);
  }
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
