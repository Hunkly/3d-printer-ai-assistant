# Local approved-plan bridge

For tasks whose approved plan exists only in the user's main checkout and is intentionally untracked, a GitHub issue may name that absolute local plan path.

Codex must treat such a path as read-only input only:

1. Read the named plan before editing.
2. Verify its `## Status` is exactly `APPROVED`.
3. If the file is absent, unreadable, or not approved, stop without modifications.
4. If approved, implement only the plan's required scope in the isolated task worktree.
5. Never copy unrelated untracked content from the main checkout into the worktree.

This mechanism exists only to bridge an explicitly approved local plan into an isolated worktree before the plan itself is committed.
