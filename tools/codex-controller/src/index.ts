import { Codex } from "@openai/codex-sdk";
import type { ThreadEvent, ThreadItem } from "@openai/codex-sdk";
import { parseProviderMode, parseReviewTarget, primaryEnvironment, fallbackEnvironment, openRouterConfig, PREFERRED_MODEL, selectOpenRouter,defaultProvenanceOps,runBuildProducer,runPlanProducer,isFallbackIsolationError,type FallbackIsolation } from "./core.js";
import { readPrimaryStatus } from "./codex-app-server-client.js";
import { decideProvider, statusLines } from "./provider-decision.js";
import {SdkCodexExecutor,type CodexExecutor,type ExecutionResult} from "./codex-executor.js";
import {beforeExecutorDiagnostics,CompatibilityProbeDiagnosticsError,diagnosticLines,isCompatibilityProbeRateLimited} from "./compatibility-probe.js";
import type {PrimaryStatus} from "./provider-decision.js";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";

interface TaskState {
  schemaVersion?: 2;
  threadId: string;
  branch: string;
  worktree?: string;
  issueNumber?: number;
  prUrl?: string;
  providerMode?: "primary" | "openrouter-free";
  modelIdentity?: string;
  role?: string;
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

type CodexPhase = "general" | "plan" | "build" | "review";
type ThreadMode = "resume" | "fresh";

const VALID_PHASES: CodexPhase[] = ["general", "plan", "build", "review"];
const VALID_THREAD_MODES: ThreadMode[] = ["resume", "fresh"];

function parsePhase(value: string | undefined): CodexPhase {
  const normalized = value?.trim().toLowerCase() || "general";
  if (!VALID_PHASES.includes(normalized as CodexPhase)) {
    throw new Error(`CODEX_PHASE must be one of: ${VALID_PHASES.join(", ")}.`);
  }
  return normalized as CodexPhase;
}

function parseThreadMode(value: string | undefined, phase: CodexPhase): ThreadMode {
  const normalized = value?.trim().toLowerCase();
  if (!normalized) return phase === "review" ? "fresh" : "resume";
  if (!VALID_THREAD_MODES.includes(normalized as ThreadMode)) {
    throw new Error(`CODEX_THREAD_MODE must be one of: ${VALID_THREAD_MODES.join(", ")}.`);
  }
  if (phase === "review" && normalized === "resume") {
    throw new Error("Review and plan approval require CODEX_THREAD_MODE=fresh.");
  }
  return normalized as ThreadMode;
}

function phaseInstructions(phase: CodexPhase): string[] {
  switch (phase) {
    case "plan":
      return [
        "PLAN / RESEARCH MODE.",
        "Inspect only the repository evidence needed for the requested plan/research and prefer the AGENTS.md planning context budget.",
        "Do not implement production behavior. Modify only planning/documentation files explicitly allowed by the task.",
        "Do not run tests, Ruff, or Mypy as routine verification; run a targeted check only to establish a factual planning claim or when the task explicitly requires it.",
      ];
    case "build":
      return [
        "BUILD MODE.",
        "If an APPROVED plan is supplied, treat it as the implementation contract and do not plan again.",
        "Inspect only plan-named files and necessary direct dependencies. Use focused verification: exact/relevant tests and Ruff/Mypy on changed/relevant files as required by AGENTS.md or the plan.",
        "Do not run broad/full suites merely to search for unrelated failures. Broaden only when the plan requires it or a focused result proves broader risk. Stop when the approved increment is complete.",
      ];
    case "review":
      return [
        "INDEPENDENT REVIEW MODE.",
        "Independently inspect the actual diff/implementation against the approved contract; do not trust Build-agent claims as evidence.",
        "Do not implement fixes unless the task explicitly authorizes a narrowly defined review-only change. Inspect only the approved scope, relevant diff, and necessary dependencies.",
        "Independently run focused verification when the review contract needs it; do not automatically run a full unit suite or reopen settled architecture without an actual contradiction. Classify findings and stop.",
      ];
    case "general":
      return [
        "GENERAL MODE.",
        "Stay narrowly scoped and perform verification appropriate to the actual task. Do not automatically run tests, Ruff, or Mypy when the task does not require them.",
      ];
  }
}

function buildCodexPrompt(task: string, phase: CodexPhase): string {
  return [
    "Follow the repository AGENTS.md execution contract.",
    "Work only on the task below. Do not push, merge, commit, or open a pull request; the controller owns Git publication.",
    "Start from files/plans named in the task. Avoid broad repository inspection and follow direct dependencies only as needed.",
    "Inspect git status before editing when editing is possible.",
    "At the end, report what changed and verification performed; when files may have changed, inspect git status, git diff --stat, and the task-relevant diff.",
    ...phaseInstructions(phase),
    "",
    `Task: ${task}`,
  ].join("\n");
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

function captureGitStatus(worktree: string): string {
  return git(worktree, "status", "--short");
}

async function writeReadOnlyExecutionRecord(
  taskKey: string,
  worktree: string,
  modelId: string,
  worktreeStateHash: string
): Promise<void> {
  const { atomicWrite, readonlyExecutionPaths } = await import("@print-engineer/openrouter-free-selector");
  const paths = readonlyExecutionPaths(worktree, taskKey, worktree);
  const record = {
    schema_version: 1,
    kind: "readonly_execution",
    task_key: taskKey,
    worktree_path: worktree,
    worktree_state_sha256: worktreeStateHash,
    provider_id: "openrouter" as const,
    model_id: modelId,
    phase: "general" as const,
    role: "readonly" as const,
    completed_at: new Date().toISOString(),
    success: true as const,
  };
  await atomicWrite(paths.record, record);
}
function validateExistingLinkedWorktree(worktree:string,env:NodeJS.ProcessEnv=process.env){if(!existsSync(worktree))throw new Error("INVALID_WORKTREE");const top=resolve(git(worktree,"rev-parse","--show-toplevel")),gitDir=resolve(git(worktree,"rev-parse","--path-format=absolute","--git-dir")),common=resolve(git(worktree,"rev-parse","--path-format=absolute","--git-common-dir"));if(top!==resolve(worktree)||gitDir===common)throw new Error("INVALID_WORKTREE");const repo=resolve(env.CODEX_REPO??"../..");if(resolve(git(repo,"rev-parse","--path-format=absolute","--git-common-dir"))!==common)throw new Error("INVALID_WORKTREE");for(const secret of [".env",".env.local","config/config.local.yaml"])if(existsSync(resolve(worktree,...secret.split("/"))))throw new Error("SECRET_FILE_PRESENT");return worktree;}
function fallbackTask(worktree:string,env:NodeJS.ProcessEnv=process.env){const value=env.MODEL_TASK_FILE;if(!value?.trim())throw new Error("INVALID_TASK_FILE");const path=resolve(worktree,value);const bytes=readFileSync(path);if(!bytes.length||bytes.length>262144||bytes.subarray(0,3).equals(Buffer.from([239,187,191])))throw new Error("INVALID_TASK_FILE");const text=new TextDecoder("utf-8",{fatal:true}).decode(bytes);if(!text.trim())throw new Error("INVALID_TASK_FILE");return text;}

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

  if (existsSync(resolve(worktree, ".git"))) {
    try {
      run("git", ["-C", worktree, "rev-parse", "--is-inside-work-tree"]);
      return worktree;
    } catch {
      throw new Error(`Existing worktree path is not a valid Git worktree: ${worktree}`);
    }
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
      const uncachedInputTokens = Math.max(
        event.usage.input_tokens - event.usage.cached_input_tokens,
        0
      );
      console.log(
        `[codex] turn completed; input=${event.usage.input_tokens}, cached=${event.usage.cached_input_tokens}, uncached=${uncachedInputTokens}, output=${event.usage.output_tokens}`
      );
      return null;
    case "turn.failed":
      throw new Error(`Codex turn failed: ${event.error.message}`);
    case "error":
      throw new Error(`Codex stream failed: ${event.message}`);
  }
}

export async function runNormalFallbackExecution(
  phase: CodexPhase,
  executor: CodexExecutor,
  model: string,
  workingDirectory: string,
  prompt: string,
  env: NodeJS.ProcessEnv = process.env,
  resumeId?: string,
  isolation: FallbackIsolation = {}
): Promise<ExecutionResult> {
  return executor.execute(
    {config: openRouterConfig(), env: fallbackEnvironment(env, isolation)},
    {workingDirectory, model},
    prompt,
    resumeId
  );
}

export async function runCodexTask(
  taskKey: string,
  task: string,
  workingDirectory: string,
  branch: string,
  state: ControllerState,
  statePath: string,
  phase: CodexPhase,
  threadMode: ThreadMode,
  providerMode: "primary"|"openrouter-free",
  modelIdentity: string,
  executor?: CodexExecutor,
  isolation: FallbackIsolation = {},
  gitStatus?: (worktree: string) => string
): Promise<string> {
  const existing = state.threads[taskKey];
  const shouldResume = threadMode === "resume" && phase !== "review" && existing !== undefined && existing.schemaVersion===2 && existing.providerMode===providerMode && existing.modelIdentity===modelIdentity && existing.role===phase && existing.worktree===workingDirectory;
  let thread:any;

  const prompt = buildCodexPrompt(task, phase);

  console.log(`[controller] task=${taskKey} branch=${branch} phase=${phase} thread=${threadMode} provider=${providerMode} model=${modelIdentity}`);
  console.log(`[controller] worktree=${workingDirectory}`);
  console.log("[controller] starting Codex stream...");
  let finalResponse = "";
  const execute=async()=>{if(providerMode === "primary"){const codex = new Codex({env:primaryEnvironment(process.env)});thread = shouldResume ? codex.resumeThread(existing!.threadId, { workingDirectory, model:modelIdentity }) : codex.startThread({ workingDirectory, model:modelIdentity });const {events}=await thread.runStreamed(prompt);for await(const event of events){const response=printEvent(event);if(response!==null)finalResponse=response;}}else{const result=await runNormalFallbackExecution(phase,executor??new SdkCodexExecutor(),modelIdentity,workingDirectory,prompt,process.env,shouldResume?existing!.threadId:undefined,isolation);thread={id:result.threadId};for(const event of result.events){const response=printEvent(event);if(response!==null)finalResponse=response;}}};

  // Read-only worktree guard for general phase with openrouter-free
  const isReadOnlyFallback = providerMode === "openrouter-free" && phase === "general";
  const statusFn = gitStatus ?? captureGitStatus;
  let beforeStatus = "";
  if (isReadOnlyFallback) {
    beforeStatus = statusFn(workingDirectory);
  }

  if(providerMode==="openrouter-free"&&(phase==="plan"||phase==="build")){const {computeWorktreeStateHash,provenancePaths}=await import("@print-engineer/openrouter-free-selector");const planPath=process.env.MODEL_PLAN_PATH;if(!planPath)throw new Error("INVALID_PLAN_PATH");const canonical=planPath.replaceAll("\\","/");const paths=provenancePaths(workingDirectory,canonical);const ops=await defaultProvenanceOps(workingDirectory,canonical,paths,execute,()=>computeWorktreeStateHash(workingDirectory));if(phase==="plan")await runPlanProducer(modelIdentity,ops);else await runBuildProducer(modelIdentity,ops);}else await execute();

  if (isReadOnlyFallback) {
    const afterStatus = statusFn(workingDirectory);
    if (beforeStatus !== afterStatus) {
      throw new Error("READONLY_WORKTREE_MUTATED");
    }
    // Write readonly execution provenance record
    const { computeWorktreeStateHash } = await import("@print-engineer/openrouter-free-selector");
    const worktreeStateHash = computeWorktreeStateHash(workingDirectory);
    await writeReadOnlyExecutionRecord(taskKey, workingDirectory, modelIdentity, worktreeStateHash);
  }

  const shouldPersist = !shouldResume && !(phase === "review" && threadMode === "fresh");
  if (shouldPersist) {
    const threadId = thread.id;
    if (!threadId) {
      throw new Error("Codex did not return a persistent thread ID; cannot save resumable task state.");
    }
    state.threads[taskKey] = {
      ...existing,
      schemaVersion:2,
      threadId,
      branch,
      worktree: workingDirectory,
      providerMode,
      modelIdentity,
      role:phase,
    };
    saveState(statePath, state);
  }

  return finalResponse;
}

async function runGitHubIssueMode(repo: string, issueNumber: number, base: string, statePath: string, phase: CodexPhase, threadMode: ThreadMode, providerMode:"primary"|"openrouter-free",modelIdentity?:string,executor?:CodexExecutor,isolation:FallbackIsolation={},fetcher:typeof fetch=fetch,registryPath?:string): Promise<void> {
  assertGitHubCli(repo);
  const repository = process.env.CODEX_GITHUB_REPO ?? getRepositoryName(repo);
  const issue = readIssue(repo, repository, issueNumber);
  const taskKey = `issue-${issue.number}`;
  const branch = `codex/${taskKey}`;
  const worktreeRoot = resolve(process.env.CODEX_WORKTREE_ROOT ?? "../../../.codex-worktrees");
  const state = loadState(statePath);
  const worktree = ensureWorktree(repo, taskKey, branch, base, worktreeRoot);
  if(providerMode==="openrouter-free")validateExistingLinkedWorktree(worktree);
  const baselineUntracked = listUntracked(worktree);
  modelIdentity ??= (await fallbackSelection(worktree,phase,process.env,fetcher,registryPath)).modelId;

  const task = providerMode==="openrouter-free"?fallbackTask(worktree):[`GitHub issue #${issue.number}: ${issue.title}`, "", issue.body].join("\n");
  const finalResponse = await runCodexTask(taskKey, task, worktree, branch, state, statePath, phase, threadMode,providerMode,modelIdentity,executor,isolation);

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

async function runManualMode(repo: string, base: string, statePath: string, phase: CodexPhase, threadMode: ThreadMode,providerMode:"primary"|"openrouter-free",modelIdentity?:string,executor?:CodexExecutor,isolation:FallbackIsolation={},fetcher:typeof fetch=fetch,registryPath?:string): Promise<void> {
  const taskKey = process.env.CODEX_TASK_KEY;
  const configuredTask = process.env.CODEX_TASK;
  if (!taskKey || (providerMode==="primary"&&!configuredTask)) {
    throw new Error(
      "Set CODEX_ISSUE_NUMBER for GitHub mode, or CODEX_TASK_KEY and CODEX_TASK for manual mode."
    );
  }

  const branch = `codex/${taskKey}`;
  const worktreeRoot = resolve(process.env.CODEX_WORKTREE_ROOT ?? "../../../.codex-worktrees");
  const worktree = ensureWorktree(repo, taskKey, branch, base, worktreeRoot);
  if(providerMode==="openrouter-free")validateExistingLinkedWorktree(worktree);
  modelIdentity ??= (await fallbackSelection(worktree,phase,process.env,fetcher,registryPath)).modelId;
  const task=providerMode==="openrouter-free"?fallbackTask(worktree):configuredTask!;
  const state = loadState(statePath);
  await runCodexTask(taskKey, task, worktree, branch, state, statePath, phase, threadMode,providerMode,modelIdentity,executor,isolation);
}

async function fallbackSelection(worktree:string,phase:CodexPhase,env:NodeJS.ProcessEnv=process.env,fetcher:typeof fetch=fetch,registryPath=resolve("../openrouter-free-selector/config/codex-compatible-free-models-v1.json")){const {canonicalPlanPath,computeWorktreeStateHash,provenancePaths,readPlanArtifact}=await import("@print-engineer/openrouter-free-selector");const key=env.OPENROUTER_API_KEY;if(!key?.trim())throw new Error("OPENROUTER_AUTH_MISSING");const target=parseReviewTarget(env.CODEX_REVIEW_TARGET);if(phase==="review"&&!target)throw new Error("INVALID_REVIEW_TARGET");const planPath=env.MODEL_PLAN_PATH;let plan,paths;if(phase!=="general"){if(!planPath)throw new Error("INVALID_PLAN_PATH");const canonical=canonicalPlanPath(worktree,planPath);const status=phase==="plan"||target==="plan"?"PROPOSED":"APPROVED";if(phase!=="plan")plan=readPlanArtifact(worktree,canonical,status);paths=provenancePaths(worktree,plan?.canonical??canonical);}const override=phase==="plan"?env.MODEL_PLAN:phase==="build"?env.MODEL_BUILD:target==="plan"?env.MODEL_PLAN_REVIEW:env.MODEL_REVIEW;return selectOpenRouter({phase,reviewTarget:target,key,fetcher,registryPath,plan,planProducerPath:paths?.plan,buildProducerPath:paths?.build,stateHash:phase==="review"&&target==="implementation"?computeWorktreeStateHash(worktree):undefined,override});}
async function resolveExecutionProvider(mode:ReturnType<typeof parseProviderMode>,readStatus:()=>Promise<PrimaryStatus>=()=>readPrimaryStatus()){const status=mode==="openrouter-free"?undefined:await readStatus();const provider=mode==="openrouter-free"?mode:decideProvider(mode,status!);return {provider,model:provider==="primary"?PREFERRED_MODEL:undefined};}
export interface ControllerDispatchDependencies {fetcher:typeof fetch;executor?:CodexExecutor;readPrimaryStatus?:()=>Promise<PrimaryStatus>;validateWorktree?:(worktree:string)=>string;gitStatus?:(worktree:string)=>string;selectFallback?:(worktree:string,phase:CodexPhase,env:NodeJS.ProcessEnv)=>Promise<any>;registryPath?:string;isolation?:FallbackIsolation}
export async function dispatchControllerCommand(flags:string[],env:NodeJS.ProcessEnv=process.env,deps:ControllerDispatchDependencies={fetcher:fetch}):Promise<string[]|undefined>{
  if(flags.some(x=>!["--provider-status","--select-only","--compatibility-probe"].includes(x))||new Set(flags).size!==flags.length||flags.length>1)throw new Error("INVALID_COMMAND");
  const providerMode=parseProviderMode(env.CODEX_PROVIDER_MODE);
  const readStatus=deps.readPrimaryStatus??(()=>readPrimaryStatus());
  if(flags[0]==="--provider-status"){
    const status=providerMode==="openrouter-free"?undefined:await readStatus();
    const key=Boolean(env.OPENROUTER_API_KEY?.trim());
    const lines=statusLines(providerMode,status,key);
    if(providerMode==="primary")decideProvider(providerMode,status!);
    return lines;
  }
  if(flags[0]==="--compatibility-probe"){
    try{
      const {executeCompatibilityProbe,safeProbeLines}=await import("./compatibility-probe.js");
      if(!env.MODEL_WORKDIR)throw 0;
      const worktree=(deps.validateWorktree??(w=>validateExistingLinkedWorktree(w,env)))(resolve(env.MODEL_WORKDIR));
      const report=await executeCompatibilityProbe(env,worktree,{fetcher:deps.fetcher,executor:deps.executor??new SdkCodexExecutor(),gitStatus:deps.gitStatus??(w=>git(w,"status","--short")),now:()=>new Date(),isolation:deps.isolation});
      return safeProbeLines(report);
    }catch(error){
      if(isFallbackIsolationError(error)||isCompatibilityProbeRateLimited(error))throw error;
      if(error instanceof CompatibilityProbeDiagnosticsError)throw error;
      throw new CompatibilityProbeDiagnosticsError(beforeExecutorDiagnostics());
    }
  }
  if(flags[0]==="--select-only"){
    if(providerMode!=="openrouter-free")throw new Error("SELECT_ONLY_REQUIRES_OPENROUTER_FREE");
    if(!env.MODEL_WORKDIR)throw new Error("INVALID_WORKTREE");
    const worktree=(deps.validateWorktree??(w=>validateExistingLinkedWorktree(w,env)))(resolve(env.MODEL_WORKDIR));
    fallbackTask(worktree,env);
    const phase=parsePhase(env.CODEX_PHASE);
    const selected=deps.selectFallback?await deps.selectFallback(worktree,phase,env):await fallbackSelection(worktree,phase,env,deps.fetcher,deps.registryPath);
    return [`selector_source=openrouter`,`role=${selected.role}`,`model=${selected.modelId}`,`verified_free=true`,`context_length=${Math.min(selected.record.context_length,Number(selected.record.top_provider?.context_length))}`,`selection_mode=${selected.selectionMode}`];
  }
  // Normal execution path (no special flags)
  if (flags.length === 0) {
    const { provider, model } = await resolveExecutionProvider(providerMode, readStatus);
    const finalProviderMode = provider === "primary" ? "primary" : "openrouter-free";
    const phase = parsePhase(env.CODEX_PHASE);
    const threadMode = parseThreadMode(env.CODEX_THREAD_MODE, phase);
    const repo = resolve(env.CODEX_REPO ?? "../..");
    const statePath = resolve(env.CODEX_STATE ?? ".codex/controller-state.json");
    const base = env.CODEX_BASE_BRANCH?.trim() || "master";

    if (env.CODEX_ISSUE_NUMBER) {
      const issueNumber = parseInt(env.CODEX_ISSUE_NUMBER, 10);
      if (isNaN(issueNumber)) throw new Error("INVALID_ISSUE_NUMBER");
      await runGitHubIssueMode(repo, issueNumber, base, statePath, phase, threadMode, finalProviderMode, model, deps.executor, deps.isolation, deps.fetcher, deps.registryPath);
    } else if (env.CODEX_TASK_KEY) {
      await runManualMode(repo, base, statePath, phase, threadMode, finalProviderMode, model, deps.executor, deps.isolation, deps.fetcher, deps.registryPath);
    } else {
      throw new Error("Set CODEX_ISSUE_NUMBER for GitHub mode, or CODEX_TASK_KEY and CODEX_TASK for manual mode.");
    }
    return undefined;
  }
  return undefined;
}

export async function runControllerCli(flags:string[],env:NodeJS.ProcessEnv,deps:ControllerDispatchDependencies={fetcher:fetch},writeLine:(line:string)=>void=console.log,writeError:(line:string)=>void=console.error):Promise<number>{try{const dispatched=await dispatchControllerCommand(flags,env,deps);if(dispatched){for(const line of dispatched)writeLine(line);}return 0;}catch(error){const message=error instanceof Error?error.message:"CONTROLLER_FAILED";writeError(/^[A-Z][A-Z0-9_]*$/.test(message)?message:"COMPATIBILITY_PROBE_FAILED");if(error instanceof CompatibilityProbeDiagnosticsError)for(const line of diagnosticLines(error.diagnostics))writeError(line);return 1;}}
async function main(): Promise<void> {
  process.exitCode=await runControllerCli(process.argv.slice(2),process.env,{fetcher:fetch});
}

if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href)main().catch((error: unknown) => {
  const message=error instanceof Error?error.message:"CONTROLLER_FAILED";
  console.error(/^[A-Z][A-Z0-9_]*$/.test(message)?message:"CONTROLLER_FAILED");
  process.exitCode = 1;
});
