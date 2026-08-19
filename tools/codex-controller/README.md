# Codex controller

Small local bridge for running Codex against this repository while preserving the repository's `AGENTS.md` execution contract.

The controller supports two modes:

- manual task mode for direct local experiments;
- GitHub issue mode for issue -> isolated worktree -> Codex -> commit/push -> draft PR.

## Prerequisites

- Node.js 18+
- Git
- Codex installed/authenticated locally with your ChatGPT account
- GitHub CLI (`gh`) installed and authenticated for GitHub issue mode

Check GitHub CLI once:

```powershell
gh --version
gh auth status
```

If necessary:

```powershell
gh auth login
```

## Install

From the repository root in PowerShell:

```powershell
cd tools\codex-controller
npm install
npm run build
```

## GitHub issue mode

From `tools\codex-controller`:

```powershell
$env:CODEX_ISSUE_NUMBER = "12"
npm start
```

The controller will:

1. read issue #12 with GitHub CLI;
2. create or reuse branch `codex/issue-12`;
3. create or reuse an isolated Git worktree outside the normal checkout;
4. start or resume the Codex thread for that issue;
5. tell Codex to follow `AGENTS.md` and not commit/push itself;
6. run Codex with live streamed progress;
7. stage tracked changes plus only new untracked files created during that Codex run;
8. commit and push the task branch if changes exist;
9. create a draft PR linked to the issue, or reuse the existing PR;
10. persist the thread/worktree/PR mapping for future revisions.

The user's normal repository checkout is not switched to the Codex task branch in GitHub mode.

## Manual mode

```powershell
$env:CODEX_TASK_KEY = "bootstrap-smoke-test"
$env:CODEX_TASK = "Inspect the repository and report the current architecture. Do not modify files."
npm start
```

Manual mode also uses an isolated worktree, but it does not publish changes to GitHub automatically.

## Configuration

The controller uses the repository two directories above this package and `master` as the base branch by default.

```powershell
$env:CODEX_REPO = "C:\path\to\3d-printer-ai-assistant"
$env:CODEX_BASE_BRANCH = "master"
$env:CODEX_GITHUB_REPO = "Hunkly/3d-printer-ai-assistant"
$env:CODEX_STATE = "C:\path\to\controller-state.json"
$env:CODEX_WORKTREE_ROOT = "C:\path\to\.codex-worktrees"
```

The default state file is `.codex/controller-state.json` relative to the controller process. Do not commit controller state.

## Safety model

- `AGENTS.md` and approved plans remain the sources of truth.
- The controller is mechanical orchestration, not a second planning agent.
- GitHub-task work is isolated from the user's normal checkout with Git worktrees.
- Codex is explicitly told not to commit, push, merge, or open PRs.
- The controller records untracked files already present in the task worktree and will not stage them later.
- PRs are created as drafts and require review before merge.
- Hardware behavior remains governed entirely by the repository safety contract and approved plans.

## Next increment

After issue mode is proven, add review-feedback intake so a GitHub review/request-changes can be fed back into the same persisted Codex thread automatically.
