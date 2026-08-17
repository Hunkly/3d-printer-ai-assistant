---
name: build-approved-plan
description: Implement an already-APPROVED repository plan exactly. Use when the user identifies a plan under plans/ and asks Codex to build, implement, or execute that approved increment.
---

# Build an Approved Plan

## Purpose

Implement an `APPROVED` plan exactly and stop when the requested increment is complete.

## Entry conditions

- Require the user to identify a plan file under `plans/`.
- Read the plan once.
- Verify its status is `APPROVED`.
- If it is not approved, stop and report exactly:

  `BUILD BLOCKED — plan is not approved.`

## Source of truth

Apply this priority:

1. Current explicit user instruction
2. The `APPROVED` plan
3. `AGENTS.md`
4. Repository architecture and tests

Do not reinterpret approved requirements.

## Before editing

Run:

```powershell
git status --short
```

Record the expected changed-file set from the approved plan.

Inspect only:

- files explicitly listed by the plan
- directly required interfaces and types
- directly relevant tests

Do not broadly explore the repository.

## Anti-loop discipline

Never repeat the same file read, search, diagnostic command, or API inspection unless:

- code changed
- prior output was truncated
- the next invocation has materially different arguments or purpose

If the same implementation uncertainty remains after two targeted checks, stop and report:

`BUILD BLOCKED — unresolved implementation detail: <detail>`

Do not keep researching.

## Implementation

Once sufficient evidence exists, edit. Do not spend another planning phase explaining what is about to be implemented.

Do not:

- redesign architecture
- fix unrelated failures
- weaken tests merely to make them pass
- modify an approved plan
- implement future phases
- revert unrelated working-tree changes

If the approved plan conflicts materially with the repository, stop and report the contradiction with evidence.

## Testing

Use the project virtual environment. On Windows use:

```powershell
.\.venv\Scripts\python.exe
.\.venv\Scripts\ruff.exe
.\.venv\Scripts\mypy.exe
```

Run in this order:

1. Focused tests for changed behavior
2. Relevant module or unit tests when justified
3. Ruff on changed or relevant files
4. Mypy on changed or relevant files

Run broader tests only when required by the plan or clearly justified. Never claim a command passed unless it was actually executed.

## Failure handling

Classify failures as:

- introduced
- pre-existing or unrelated
- environment or runtime
- unresolved

Fix only introduced, in-scope failures. Do not spend multiple iterations investigating an obviously unrelated known failure.

## After editing

Run:

```powershell
git status --short
git diff --stat
git diff
```

Compare actual changes against the expected plan scope. Do not revert unrelated user changes.

## Printer safety

For printer work, follow `AGENTS.md` strictly.

For a read-only printer increment require:

- zero MQTT publish
- no request topic
- no printer commands
- no start, stop, pause, or resume
- no temperature changes
- no camera unless explicitly approved
- no physical hardware in unit tests

## Completion report

Return these sections:

## Changed Files

List the files changed for the approved increment.

## Implementation Summary

Summarize the implemented approved behavior.

## Tests Executed

Report exact commands and exact results.

## Ruff / Mypy

Report exact commands and results.

## Scope Check

Compare expected and actual changed files.

## Deviations

Report `None`, or list exact deviations.

## Remaining Issues

Report only real unresolved issues.

Stop after the requested increment is complete. Do not continue into the next phase automatically.
