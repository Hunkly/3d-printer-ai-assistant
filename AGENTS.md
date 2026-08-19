# Codex Project Execution Contract

Codex is the primary planning, implementation, and review agent for this repository. Follow this contract unless a higher-priority source of truth says otherwise.

## Project and source of truth

This repository is the **3D Printer AI Assistant**. Current areas include slicer/profile integration, model analysis, print recommendations, Bambu Lab A1 LAN integration, and MCP tools.

Use this priority order:

1. Current explicit user instruction
2. An `APPROVED` plan under `plans/`
3. This `AGENTS.md`
4. Existing architecture and tests
5. README/roadmap
6. Inference

Do not invent requirements from TODOs, phase names, class names, or likely future work. Do not assume a requested feature is missing; inspect the relevant implementation first. If an approved plan materially conflicts with the repository, stop and report the conflict.

## Default workflow

For non-trivial work use `PLAN → APPROVE → BUILD → REVIEW`.

When an `APPROVED` plan exists, **do not plan again**. Treat it as the implementation contract.

Build must:

- read the approved plan once;
- inspect `git status --short` before editing;
- inspect only directly relevant files and dependencies;
- implement exactly the approved scope;
- run focused tests and focused Ruff/Mypy checks;
- inspect final `git status --short`, `git diff --stat`, and the task-relevant diff;
- report verification results and stop.

Do not reinterpret approved requirements, redesign unrelated architecture, weaken tests to pass, fix unrelated failures, or implement future phases.

## Context and usage budget

Optimize for **minimum sufficient repository context**. Broad exploration is expensive and is not the default.

For an approved-plan build:

- start from files, symbols, and tests named by the plan;
- do **not** begin with repository-wide `rg --files`, recursive globs, broad symbol scans, or architecture inventories;
- before the first edit, prefer no more than **5 targeted file reads** unless a direct dependency requires more;
- follow imports/callers only when needed to make the requested edit safely;
- do not reread unchanged files or rerun unchanged diagnostics;
- after two targeted checks fail to resolve a detail, stop and report the uncertainty instead of exploring indefinitely;
- do not inspect unrelated plans, tests, adapters, or phases;
- do not run a full test suite, full `ruff check src tests`, or full `mypy src tests` merely to discover unrelated failures;
- do not dump large command outputs into context when a filtered/targeted command can answer the question;
- prefer exact test node IDs, exact modules, changed-file Ruff checks, and changed/relevant-module Mypy checks;
- broaden verification only when the approved plan explicitly requires it, a focused check reveals a cross-cutting risk, or the change genuinely affects a shared public contract.

For planning/research, prefer at most **10 targeted file reads** before producing the initial plan. Explain why before exploring more broadly.

Repository evidence is primary. Use external documentation only when behavior depends on an external API/library/protocol/version that cannot be established locally; prefer official docs/source/specifications.

## Python and verification

Use the project virtual environment on Windows:

```powershell
.\.venv\Scripts\python.exe
.\.venv\Scripts\ruff.exe
.\.venv\Scripts\mypy.exe
```

Typical verification order:

1. Exact tests covering changed behavior
2. Relevant test module/suite if justified
3. Ruff on changed/relevant files
4. Mypy on changed/relevant files/modules
5. Broader regression suite only when justified

Never claim a check passed unless it was executed. Classify failures as introduced, pre-existing/unrelated, environment/runtime, or unresolved. Do not automatically fix unrelated failures.

## Git and user changes

The working tree may contain unrelated user work. Never revert, delete, reset, overwrite, clean, stage, or modify unrelated changes. Modify only files required by the current task/approved plan and report unexpected changed files.

## Architecture

Prefer existing abstractions over parallel systems. Before adding a file, class, protocol, model, or dependency, verify an existing abstraction cannot reasonably contain the functionality. Keep public API surface minimal and avoid speculative refactoring.

## Printer safety

Unless an explicitly approved plan says otherwise:

- do not start, stop, pause, or resume a print;
- do not change temperatures or printer state;
- do not publish MQTT commands or use the Bambu request topic;
- do not implement automatic printing;
- do not perform physical-hardware actions;
- do not connect to physical hardware during unit tests.

Read-only printer status work may only connect, subscribe/read telemetry, normalize status, and disconnect. For the current Bambu A1 read-only work there must be **zero MQTT publish paths**. Do not introduce `pushall`, command signing, cloud MQTT, Bambu account login, camera access, FTPS, or printer discovery unless a later approved plan explicitly requires it.

Hardware verification must remain separate from hermetic automated tests. Never claim hardware behavior was verified unless it actually was. Perform only explicitly approved hardware operations.

## MCP

Follow the existing MCP tool-group and `build_tools` pattern. Keep read-only tools read-only. Preserve machine-readable `code`, `message`, and `details` in structured errors. Do not redesign MCP registration for one tool.

## Recommendation scope

Phase 3A.1 is **Print Configuration and Material Recommendation** and is read-only. Prefer extending the existing recommendation modules rather than duplicating them:

- `src/print_engineer/recommendation/engine.py`
- `src/print_engineer/recommendation/context.py`
- `src/print_engineer/recommendation/filament.py`
- `src/print_engineer/recommendation/setup.py`
- `src/print_engineer/recommendation/rules.py`
- `src/print_engineer/recommendation/prompt.py`
- `src/print_engineer/core/recommendation.py`
- `src/print_engineer/core/interfaces/recommender.py`
- `ProfileRepository`
- `ProfileMaterializer`

Do not fabricate slicer/profile/material facts. Keep deterministic evidence separate from LLM narrative. Unknown values remain unknown.

Do not implement undefined future phases merely because they appear in roadmaps. Phase 3B, print history/learning, printer control, camera support, automatic slicing, and automatic printing are separate future increments unless explicitly approved.

## Review and definition of done

Review the actual diff against the approved plan. Verify tests/static-check claims independently when reviewing. Check lifecycle cleanup, error paths, configuration precedence, unintended state changes, and whether tests prove behavior rather than bypassing it with mocks.

A task is done only when requested behavior is implemented, focused tests and relevant static checks pass, the diff matches scope, unintended behavior was not added, unrelated failures are reported, and hardware behavior is not claimed without evidence.
