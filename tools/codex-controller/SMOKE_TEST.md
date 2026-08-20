# Codex Controller Smoke Test

From `tools/codex-controller`, use these non-destructive checks. Invalid environment values are parsed before task validation, worktree creation, or SDK execution, so these rejection checks spend no Codex tokens.

```powershell
npm run build
npm test

Remove-Item Env:CODEX_PROVIDER_MODE -ErrorAction SilentlyContinue
npm start -- --provider-status
# Expected: provider_mode=auto and safe machine-readable status fields; no inference.

$env:CODEX_PROVIDER_MODE = "invalid"
npm start -- --provider-status
# Expected: INVALID_PROVIDER_MODE before execution.

$env:CODEX_PROVIDER_MODE = "openrouter-free"
Remove-Item Env:OPENROUTER_API_KEY -ErrorAction SilentlyContinue
npm start -- --provider-status
# Expected: safe status fields followed by OPENROUTER_AUTH_MISSING; no metadata or inference request.

$env:CODEX_PHASE = "invalid"
Remove-Item Env:CODEX_THREAD_MODE -ErrorAction SilentlyContinue
npm start
# Expected: CODEX_PHASE must be one of: general, plan, build, review.

$env:CODEX_PHASE = "general"
$env:CODEX_THREAD_MODE = "invalid"
npm start
# Expected: CODEX_THREAD_MODE must be one of: resume, fresh.
```

Documentation/default audit:

- `CODEX_PHASE=review` with no `CODEX_THREAD_MODE` selects `fresh` and does not replace the persisted task thread.
- `general`, `plan`, and `build` with no `CODEX_THREAD_MODE` select `resume`.
- Explicit `resume` and `fresh` override eligible non-review defaults; REVIEW/approval rejects `resume`.

Clear the smoke-test variables afterward:

```powershell
Remove-Item Env:CODEX_PHASE,Env:CODEX_THREAD_MODE -ErrorAction SilentlyContinue
```
