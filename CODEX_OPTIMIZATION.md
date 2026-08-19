# Codex usage optimization

This note records the rationale for the context-budget rules in `AGENTS.md`.

The primary cost driver is broad repository inspection and broad verification, not normal source edits. For routine approved-plan builds, Codex should stay on the plan-defined surface:

- read the approved plan once;
- inspect only named files and direct dependencies;
- avoid repository-wide file inventories and symbol scans;
- run exact focused tests first;
- run Ruff/Mypy only on changed or directly relevant files unless the plan requires broader checks;
- do not run full unit suites merely to discover unrelated failures;
- avoid re-reading unchanged files or repeating diagnostics;
- summarize large command results rather than dumping unrelated output into context.

Broader inspection remains appropriate when the task is explicitly architectural, when a focused check exposes an unresolved dependency, or when an approved plan requires a broad regression suite.
