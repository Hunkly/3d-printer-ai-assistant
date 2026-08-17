---
name: review-approved-plan
description: Independently review a repository implementation against an APPROVED plan without modifying files. Use when the user identifies a plan under plans/ and asks Codex to verify implementation correctness, tests, architecture, scope, safety, or readiness for approval.
---

# Review an Approved Plan

## Purpose

Independently determine whether an implementation:

- matches the `APPROVED` plan
- is functionally correct
- is adequately tested
- respects repository architecture
- stays within scope
- introduces no unintended behavior

Treat Build-agent claims as non-evidence. Remain read-only: never modify or revert files.

## Entry conditions

- Require the user to identify a plan under `plans/`.
- Read the plan once.
- Verify its status is `APPROVED`.
- If it is not approved, stop and report exactly:

  `REVIEW BLOCKED — plan is not approved.`

## Source of truth

Apply this priority:

1. Current explicit user instruction
2. The `APPROVED` plan
3. `AGENTS.md`
4. Actual repository implementation and tests

Do not reinterpret the approved plan.

## Repository state

Begin with:

```powershell
git status --short
git diff --stat
git diff
```

Determine:

- expected files from the approved plan
- changed tracked files
- new or untracked files
- unrelated pre-existing working-tree changes
- unexpected scope changes

Never revert or modify anything. Remember that normal `git diff` does not show untracked-file contents. Read new or untracked files directly when they belong to the implementation.

## Requirement checklist

Extract every observable requirement from the approved plan. Record each as:

```text
PASS — <requirement>
Evidence: <specific code/test/runtime result>

FAIL — <requirement>
Evidence: <specific defect>

PARTIAL — <requirement>
Evidence: <implemented portion and missing portion>

UNKNOWN — <requirement>
Reason: <why it cannot be established>
```

Never mark `PASS` based only on:

- Build-agent claims
- comments or docstrings
- a test name
- a mocked result that bypasses the actual behavior under review

## End-to-end review

Trace important requirements end-to-end:

`input/config → resolution → implementation → output/error → tests`

Reject fixes that merely move a failure to another layer.

For configuration features explicitly inspect:

- precedence
- fallback behavior
- missing-value behavior
- secret versus config resolution
- tests proving the real resolution path

For serialization and API features inspect:

- exact output fields
- types
- error contracts
- compatibility with callers

## Architecture review

Check that:

- existing abstractions are reused
- responsibilities remain in the appropriate layer
- no duplicate implementation exists
- no unnecessary public APIs or dependencies were added
- no architecture redesign occurred outside the approved plan
- backward compatibility is preserved where required

## Test quality review

Require tests to prove behavior, not merely execute code. Look specifically for:

- mocks that bypass the code path supposedly tested
- fakes that manually raise the expected error instead of exercising its real cause
- tests that would pass with an incorrect implementation
- weakened assertions
- missing edge cases
- missing lifecycle-cleanup tests
- missing configuration-precedence tests
- missing structured-error assertions
- untested invalid input or failure paths

For regression fixes, verify that the test reproduces the actual root cause.

## Network and printer review

For printer or network work verify:

- no accidental MQTT publish in read-only work
- no request-topic usage unless explicitly approved
- no state-changing calls or automatic printing
- no camera access unless approved
- lifecycle cleanup on success and every exception path
- authentication, unreachable, timeout, and invalid-payload handling
- unit tests require no physical hardware
- fake transports cannot silently hide write operations

For the current read-only Bambu increment, require **zero MQTT publish paths**.

## Failure attribution

Classify failed tests, Ruff, and Mypy checks as:

- `INTRODUCED`
- `PRE-EXISTING / UNRELATED`
- `ENVIRONMENT / RUNTIME`
- `UNKNOWN`

Do not automatically reproduce every unrelated failure on pristine `HEAD`. Attribute in this order:

1. Check whether a changed file is on the failing code or import path.
2. Check whether the relevant test or fixture changed.
3. Determine whether changed behavior can logically cause the failure.
4. Check for reliable existing baseline evidence.

If attribution remains uncertain, perform a targeted baseline comparison. Do not repeatedly investigate an obviously unrelated known failure.

## Anti-loop discipline

Never repeat the same file read, search, diagnostic, test command, or API inspection unless:

- code changed
- prior output was truncated
- arguments or diagnostic purpose materially differ

If uncertainty remains after two targeted investigations, mark it `UNKNOWN`. If that unknown affects correctness or safety, do not approve.

## Verification commands

Use the project virtual environment. On Windows use:

```powershell
.\.venv\Scripts\python.exe
.\.venv\Scripts\ruff.exe
.\.venv\Scripts\mypy.exe
```

Run:

1. Focused tests covering changed behavior
2. Relevant module or unit tests when justified
3. Ruff on changed or relevant files
4. Mypy on changed or relevant files
5. A broader unit suite when required by the plan or needed for regression confidence

Do not trust Build's reported results. Execute required verification independently. Never claim a check passed unless it was executed.

## Scope review

Report separately:

- unrelated changes
- unnecessary refactors
- unapproved dependencies
- future-phase functionality
- modified tests outside approved scope
- safety-boundary violations

Do not fix them.

## Recommendation rules

Choose `APPROVE` only when every required behavior passes, no blocking unknown exists, focused verification is green, no introduced static-check failure exists, scope matches the plan, and safety constraints hold.

Choose `APPROVE WITH FIXES` when the architecture and overall approach are valid, defects are concrete and limited, no redesign is required, and specific fixes remain before completion.

Choose `DO NOT APPROVE` when requirements are materially missing, implementation contradicts the plan, tests hide failures, safety boundaries are violated, architecture requires reconsideration, or blocking behavior cannot be verified.

## Final report

Return exactly these sections:

## Verification Result

`PASS`, `FAIL`, or `PARTIAL`.

## Repository State

Expected and actual implementation files plus unrelated working-tree changes.

## Requirement Checklist

Every requirement with `PASS`, `FAIL`, `PARTIAL`, or `UNKNOWN` and evidence.

## Implementation Review

Architecture and behavior findings.

## Test Quality Review

Whether tests genuinely prove required behavior.

## Test Results

Exact commands and exact results.

## Ruff / Mypy

Exact commands and exact results.

## Scope Violations

`None`, or exact violations.

## Critical Findings

Only issues that block completion or deserve correction before approval.

## Recommendation

Choose exactly one:

- `APPROVE`
- `APPROVE WITH FIXES`
- `DO NOT APPROVE`

End with exactly:

`REVIEW ONLY — no files were modified.`
