# Codex controller

Small local bridge for running Codex against this repository while preserving the repository's `AGENTS.md` execution contract.

Version 0.1 deliberately does **not** poll GitHub, push commits, or open pull requests. It proves the safe local half of the bridge first: task -> task branch -> persistent Codex thread -> local edits/tests/report.

## Prerequisites

- Node.js 18+
- Git
- Codex installed/authenticated locally with your ChatGPT account
- a clean working tree before the controller switches branches

## Install

From the repository root in PowerShell:

```powershell
cd tools\codex-controller
npm install
npm run build
```

## Run a task

From `tools\codex-controller`:

```powershell
$env:CODEX_TASK_KEY = "bootstrap-smoke-test"
$env:CODEX_TASK = "Inspect the repository and report the current architecture. Do not modify files."
npm start
```

The controller uses the repository two directories above this package by default and uses `master` as the base branch. Override these when necessary:

```powershell
$env:CODEX_REPO = "C:\path\to\3d-printer-ai-assistant"
$env:CODEX_BASE_BRANCH = "master"
$env:CODEX_STATE = "C:\path\to\controller-state.json"
```

## Behavior

For task key `123-some-feature`, the controller:

1. refuses to switch branches if the working tree is dirty;
2. fetches the configured base branch;
3. switches to or creates `codex/123-some-feature` from `origin/master`;
4. starts a new Codex SDK thread or resumes the saved thread for that task key;
5. tells Codex to follow `AGENTS.md`, perform only the requested task, verify its work, and not push/merge/open PRs;
6. stores the Codex thread ID in the local state file so revisions can resume the same thread;
7. prints Codex's final report.

The default state file is `.codex/controller-state.json` relative to the controller process. Do not commit controller state.

## Next increment

After the smoke test works locally, add the GitHub half:

- receive an explicitly marked GitHub task;
- map issue/task ID to a Codex task key;
- run/resume Codex;
- verify the resulting diff;
- commit and push the task branch;
- create or update a pull request;
- feed review feedback back into the same Codex thread.

Keep GitHub orchestration mechanical. `AGENTS.md` and approved plans remain the project's sources of truth.
