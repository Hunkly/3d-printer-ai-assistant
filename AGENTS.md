# Project Agent Instructions

## Local repository

This is a LOCAL Windows repository.

The repository root is the current working directory.

NEVER use GitHub, WebFetch, or any remote API to inspect this repository.

NEVER invent or guess a GitHub repository URL.

NEVER use the explore tool or explore subagent. It is unavailable.

## Repository exploration

Use the locally available tools:

- glob — find files and directories
- grep — search source code
- ead — read files
- ash — execute local PowerShell/command-line operations

For a request to inspect the repository:

1. Use glob first.
2. Use grep to locate relevant symbols/files.
3. Use ead to inspect the relevant files.
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
