# Codex Project Execution Contract

Codex is the primary planning, implementation, and review agent for this repository. Follow this file as the durable project execution contract unless a higher-priority source of truth says otherwise.

## Project

This repository is the **3D Printer AI Assistant**. Primary current areas include:

- slicer and profile integration
- model analysis
- print recommendation
- Bambu Lab A1 LAN integration
- MCP tools

Repository requirements and approved plan files are authoritative over assumptions.

## Source of truth

Use this priority order:

1. Current explicit user instruction
2. An `APPROVED` plan under `plans/`
3. This `AGENTS.md`
4. Existing repository architecture and tests
5. README and roadmap material
6. Inference

Do not invent missing requirements from TODOs, class names, phase names, or likely future functionality. Do not assume a requested feature is missing; inspect the implementation first.

If an approved plan conflicts materially with the repository, stop and report the conflict instead of silently redesigning the implementation.

## Local repository and internet usage

This is a local Windows repository. The repository root is the current working directory.

- Inspect the repository with local file search, text search, file reads, and PowerShell commands.
- Start with targeted file discovery, then search for relevant symbols, then read only the necessary files.
- Never use GitHub, WebFetch, a remote API, or an invented repository URL to inspect this repository.
- Never use an explore tool or explore subagent.
- Do not ask the user for repository information that can be obtained locally.
Local repository evidence is primary for repository behavior.

External documentation may be consulted when implementation depends on an external library, protocol, API, hardware interface, or version-specific behavior that cannot be established reliably from the repository alone.

Prefer authoritative sources:

1. official documentation
2. official source code / upstream repository
3. protocol specifications
4. reputable implementation references

Clearly distinguish external evidence from repository evidence.

Never use web research as a substitute for inspecting local repository code.

## Workflow

For non-trivial work use:

`PLAN → APPROVE → BUILD → REVIEW`

During planning or research:

- Do not modify files unless explicitly instructed.
- Do not create unnecessary files or run unnecessary long-lived processes.
- Inspect the existing implementation before proposing changes.

When an `APPROVED` plan already exists, Codex acting as Build must not repeat the planning phase. Treat the approved plan as an implementation contract.

Build must:

- read the approved plan
- inspect directly relevant files
- inspect `git status --short` before implementation
- implement exactly the approved scope
- run focused tests
- run focused Ruff and Mypy checks
- inspect `git status --short`, `git diff --stat`, and `git diff` afterward
- report verification results and stop when the requested phase is complete

Build must not:

- reinterpret approved requirements
- redesign architecture without repository evidence
- weaken tests merely to make them pass
- fix unrelated failures
- implement future phases
- continue exploring after enough information exists to edit safely

## Investigation discipline

Inspect the minimum repository surface necessary.

Do not repeatedly:

- read the same file
- run the same diagnostic command
- inspect the same API or type information
- reconsider a conclusion already supported by evidence

A repeated command is allowed only when code changed, previous output was truncated, or the arguments or purpose are materially different.

If an implementation detail remains unresolved after two targeted checks, stop and report the uncertainty instead of looping.

For planning work, do not begin with broad recursive globs such as `src/print_engineer/**/*`, `src/**/*.py`, or `**/*.py`. Follow imports and callers only when necessary. Prefer at most ten targeted file reads before producing the initial plan; explain before exploring more broadly.

## Python environment

Always use the project virtual environment. On Windows, use:

```powershell
.\.venv\Scripts\python.exe
```

Do not use system Python for project tests. Use project executables such as these when present:

```powershell
.\.venv\Scripts\ruff.exe
.\.venv\Scripts\mypy.exe
```

## Testing and verification

Run focused tests first. Typical verification order:

1. Tests directly covering changed behavior
2. Relevant module or unit suite
3. Ruff on changed or relevant files
4. Mypy on changed or relevant files
5. Broader unit suite when justified

Never claim a test passed unless it was actually executed. Never claim implementation is complete unless relevant tests and applicable static checks were run and the diff matches the requested scope.

When a test or check fails, investigate and classify it as:

- introduced by the current change
- pre-existing or unrelated
- environment or runtime issue
- unresolved

Do not automatically fix unrelated failures. Do not change tests solely because production behavior makes an assertion inconvenient; establish the intended behavioral contract first. Do not trust prior agent summaries or Build-agent success claims without independent verification.

## Git and working tree

The working tree may contain unrelated user changes.

Before implementation inspect:

```powershell
git status --short
```

After implementation inspect:

```powershell
git status --short
git diff --stat
git diff
```

Never revert, delete, reset, overwrite, or clean unrelated user changes. Modify only files required by the current task or approved plan. Report unexpected changed files.

## Architecture

Prefer extending existing abstractions over creating parallel systems. Before adding a file, class, protocol, model, or dependency:

- verify an existing abstraction cannot reasonably contain the functionality
- keep the public API surface minimal
- avoid speculative refactoring

Do not redesign working architecture merely because another structure appears cleaner.

## Printer safety

Printer integrations require special care. Unless an explicitly approved plan says otherwise:

- do not start, stop, pause, or resume a print
- do not change temperatures or other printer state
- do not publish MQTT commands
- do not use the Bambu request topic
- do not implement automatic printing
- do not perform hardware actions
- do not connect to physical hardware during unit tests

Read-only printer status work may only connect, subscribe to or read telemetry, normalize status, and disconnect.

For the current Bambu A1 read-only increment there must be **zero MQTT publish paths**. Do not introduce `pushall`, command signing, cloud MQTT, Bambu account login, camera access, FTPS, or printer discovery unless explicitly approved in a later plan.

## Hardware verification

Unit tests must be hermetic and must not require a physical printer. Separate real-hardware checks clearly from automated verification, and never claim hardware behavior was verified unless the checks were actually performed. If hardware verification is requested, perform only explicitly approved operations.

## MCP

- Follow the existing MCP tool-group and `build_tools` pattern.
- Keep read-only MCP tools read-only.
- Preserve machine-readable `code`, `message`, and `details` in `PrinterError` and similar structured errors.
- Do not redesign MCP registration architecture for a single tool.

## Recommendation system and Phase 3A.1

Phase 3A.1 means **Print Configuration and Material Recommendation**, not model analysis. Model analysis with trimesh is existing functionality and is not the Phase 3A.1 goal.

Phase 3A.1 includes:

- print configuration
- material class recommendation
- local filament candidate discovery
- filament compatibility
- deterministic filament ranking
- nozzle recommendation
- process recommendation integration
- optional grounded LLM explanation

Phase 3A.1 is read-only. It must not connect to a physical printer, change printer state, modify slicer profiles, modify the 3D model, automatically slice unless explicitly requested by an approved feature, or implement Phase 3B.

Do not fabricate slicer, profile, or material facts. Preserve the distinction between deterministic repository or profile evidence and LLM-generated narrative. Unknown values must remain unknown. Do not mutate slicer profiles or models unless explicitly required.

Prefer extending these existing recommendation components over creating duplicates:

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

For Phase 3A.1, start with only those modules and inspect direct dependencies as required. Inspect relevant tests after understanding the implementation. Do not begin with a repository-wide glob.

## Scope boundaries

Do not implement undefined future phases merely because they appear in roadmaps. In particular, do not implement Phase 3B without explicit, approved requirements.

Print history and learning, printer control, camera support, automatic slicing, and automatic printing are separate future increments unless an approved plan says otherwise.

## Code review

When reviewing code:

- compare the actual diff against the approved plan
- independently verify tests and static-check claims
- verify tests prove behavior rather than merely exercising mocks that bypass it
- inspect configuration precedence
- inspect lifecycle cleanup and all error paths
- inspect unintended state changes
- distinguish new failures from pre-existing failures

## Definition of done

A task is complete only when:

- requested or approved behavior is implemented
- focused tests pass
- relevant Ruff checks pass
- relevant Mypy checks pass
- the diff matches the approved scope
- no unintended behavior was added
- remaining unrelated failures are clearly reported
- hardware behavior is not claimed without hardware evidence
