# Project Agent Instructions

## Local repository

This is a LOCAL Windows repository.

The repository root is the current working directory.

NEVER use GitHub, WebFetch, or any remote API to inspect this repository.

NEVER invent or guess a GitHub repository URL.

NEVER use the explore tool or explore subagent. It is unavailable.

## Repository exploration

Use the locally available tools:

- glob   find files and directories
- grep   search source code
- 
ead   read files
- ash   execute local PowerShell/command-line operations

For a request to inspect the repository:

1. Use glob first.
2. Use grep to locate relevant symbols/files.
3. Use 
ead to inspect the relevant files.
4. Use ash only when command-line inspection is useful.

Do NOT use webfetch for local repository inspection.

Do NOT ask the user for repository information that can be obtained locally.

## Internet usage

Use webfetch only when the user explicitly requests external web research or when the task explicitly requires external documentation.

Never use external web access as a substitute for inspecting the local repository.

## Development workflow

Respect explicit research > approval > implementation phases.

During research:
- Do not modify files unless explicitly instructed.
- Do not create unnecessary files.
- Do not run long-running processes unnecessarily.

When implementation is approved:
- Implement only the approved scope.
- Run relevant tests.
- Report verification results.
- Stop when the requested phase is complete.

# Project Instructions

## Current Development Phase

The current active feature is:

**Phase 3A.1 — Print Configuration & Material Recommendation**

IMPORTANT:
Do NOT interpret Phase 3A.1 as "Model analysis (trimesh)".

Model analysis (trimesh) is existing functionality and is NOT the goal of Phase 3A.1.

Phase 3A.1 means:

- print configuration
- material class recommendation
- local filament candidate discovery
- filament compatibility
- deterministic filament ranking
- nozzle recommendation
- process recommendation integration
- optional grounded LLM explanation

## Scope

Phase 3A.1 is READ-ONLY.

It must NOT:

- connect to a physical printer
- start printing
- stop printing
- modify printer state
- modify slicer profiles
- modify the 3D model
- automatically slice unless explicitly requested by an existing approved feature
- implement Phase 3B

## Source of Truth

When implementing Phase 3A.1:

1. Follow the user's explicit requirements.
2. Follow this file.
3. Inspect the existing implementation before proposing changes.
4. Do not infer the meaning of Phase 3A.1 from README phase descriptions.
5. Do not assume a requested feature is missing.
6. Verify whether existing code already implements the requirement.

## Architecture

Prefer extending existing components over creating duplicate systems.

Important existing modules include:

- `src/print_engineer/recommendation/engine.py`
- `src/print_engineer/recommendation/context.py`
- `src/print_engineer/recommendation/filament.py`
- `src/print_engineer/recommendation/setup.py`
- `src/print_engineer/recommendation/rules.py`
- `src/print_engineer/recommendation/prompt.py`
- `src/print_engineer/core/recommendation.py`
- `ProfileRepository`
- `ProfileMaterializer`

Before creating a new class or module, verify that equivalent functionality does not already exist.

## Verification

Never claim implementation is complete unless:

- relevant tests have actually been run
- failures have been investigated
- static checks have been run where applicable
- the implementation matches the requested scope

Do not trust previous agent summaries. Verify independently.

## Exploration Budget

For planning tasks:

- Do NOT use broad recursive globs such as `src/print_engineer/**/*`, `src/**/*.py`, or `**/*.py`.
- Do NOT enumerate the entire repository.
- Start from the modules explicitly identified in this document.
- Follow imports/callers only when necessary.
- Inspect relevant tests only after understanding the implementation.
- Prefer at most 10 targeted file reads before producing the initial plan.
- If you believe broader exploration is necessary, explain why before doing it.

## Phase 3A.1 Starting Point

For Phase 3A.1, start by inspecting only:

- `src/print_engineer/recommendation/engine.py`
- `src/print_engineer/recommendation/context.py`
- `src/print_engineer/recommendation/filament.py`
- `src/print_engineer/recommendation/setup.py`
- `src/print_engineer/recommendation/rules.py`
- `src/print_engineer/recommendation/prompt.py`
- `src/print_engineer/core/recommendation.py`
- `src/print_engineer/core/interfaces/recommender.py`

Then inspect direct dependencies only where required.

Do not begin with a repository-wide glob.

