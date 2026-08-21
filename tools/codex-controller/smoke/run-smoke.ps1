# Fail-Stop Live Smoke Harness — controlling orchestrator
# Implements plans/fail-stop-live-smoke-harness-v1.md.
#
# This script is the SINGLE controlling process. It must be invoked exactly as:
#   powershell -NoProfile -File run-smoke.ps1 [-DryRun]
#
# Fail-stop invariants:
#   - $ErrorActionPreference='Stop' and Set-StrictMode -Version Latest
#   - Every native command checks $LASTEXITCODE explicitly
#   - PASS is emitted from exactly ONE success-only terminal control path
#   - Any mandatory failure: SMOKE_RESULT=FAIL, exit non-zero, NO live retry
#   - Production launch count is at most ONE (tracked directly by this script)
#
# Machine-readable evidence MUST be persisted as UTF-8 WITHOUT BOM via
# System.IO.File / WriteAllText with UTF8Encoding($false). PowerShell 5.1
# shell redirection (`>`/`2>`) and Set-Content -Encoding utf8 are FORBIDDEN
# for any artifact later parsed by Node.

[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ------------------- Helpers -------------------

function Write-Utf8NoBom {
    param([Parameter(Mandatory=$true)][string]$Path,
          [AllowEmptyString()][string]$Content = '')
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Content, (New-Object System.Text.UTF8Encoding($false)))
}

function Append-Utf8NoBom {
    param([Parameter(Mandatory=$true)][string]$Path,
          [Parameter(Mandatory=$true)][string]$Content)
    $existing = ''
    if (Test-Path -LiteralPath $Path) {
        $existing = [System.IO.File]::ReadAllText($Path, (New-Object System.Text.UTF8Encoding($false)))
    }
    [System.IO.File]::WriteAllText($Path, $existing + $Content, (New-Object System.Text.UTF8Encoding($false)))
}

function Invoke-Native {
    # Runs a native command, fails closed on non-zero exit, returns stdout.
    # IMPORTANT: PowerShell's strict error action preference treats stderr
    # writes by some git versions (e.g. `From <url>...`) as error records,
    # which would terminate with $ErrorActionPreference='Stop'. We temporarily
    # relax to 'Continue' for the duration of the call so stderr lines are
    # captured, not converted to exceptions.
    param([Parameter(Mandatory=$true)][string]$FailureCode,
          [Parameter(Mandatory=$true)][string]$File,
          [Parameter(Mandatory=$true)][string[]]$Arguments)
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $File @Arguments 2>&1
        $exit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousEap
    }
    if ($exit -ne 0) {
        $msg = ($output | Out-String).TrimEnd()
        if ($msg.Length -gt 500) { $msg = $msg.Substring(0, 500) + '...' }
        throw "$FailureCode (exit $exit): $msg"
    }
    return ($output | Out-String).TrimEnd()
}

function ConvertTo-NativeArgument {
    # Windows-compatible command-line argument escaping (matches .NET's
    # ArgumentList / CommandLineToArgvW rules). Each argument becomes a
    # literal fragment that ProcessStartInfo.Arguments will preserve when the
    # child parses its own argv. Backslashes are only special immediately
    # before a quote; a run of backslashes before the closing quote is doubled.
    param([AllowEmptyString()][string]$Argument)
    if ($Argument.Length -eq 0) { return '""' }
    $needsQuotes = $Argument -match '[\s"]'
    $sb = New-Object System.Text.StringBuilder
    if ($needsQuotes) { [void]$sb.Append('"') }
    $backslashRun = 0
    for ($i = 0; $i -lt $Argument.Length; $i++) {
        $ch = $Argument[$i]
        if ($ch -eq '\') {
            $backslashRun++
        } elseif ($ch -eq '"') {
            [void]$sb.Append(('\' * (2 * $backslashRun)))
            [void]$sb.Append('\"')
            $backslashRun = 0
        } else {
            if ($backslashRun -gt 0) {
                [void]$sb.Append(('\' * $backslashRun))
                $backslashRun = 0
            }
            [void]$sb.Append($ch)
        }
    }
    if ($backslashRun -gt 0) {
        if ($needsQuotes) {
            # Doubled because these backslashes precede the closing quote.
            [void]$sb.Append(('\' * (2 * $backslashRun)))
        } else {
            [void]$sb.Append(('\' * $backslashRun))
        }
    }
    if ($needsQuotes) { [void]$sb.Append('"') }
    return $sb.ToString()
}

function Start-CapturedChild {
    # Launch a child process and capture stdout/stderr as UTF-8 WITHOUT BOM.
    # Drains both streams concurrently to avoid pipe deadlock. Arguments are
    # escaped for the Windows command line (§8.2) so spaces/quotes/backslashes
    # are preserved exactly. Optional environment overrides are applied.
    param([Parameter(Mandatory=$true)][string]$FileName,
          [Parameter(Mandatory=$true)][AllowEmptyCollection()][string[]]$Arguments,
          [Parameter(Mandatory=$true)][string]$WorkingDirectory,
          [hashtable]$Environment = @{})
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FileName
    $psi.Arguments = (($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' ')
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $psi.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
    foreach ($key in @($Environment.Keys)) {
        $psi.EnvironmentVariables[$key] = [string]$Environment[$key]
    }
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $outBuilder = New-Object System.Text.StringBuilder
    $errBuilder = New-Object System.Text.StringBuilder
    $outEv = Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived `
        -Action { if ($EventArgs.Data -ne $null) { [void]$Event.MessageData.AppendLine($EventArgs.Data) } } `
        -MessageData $outBuilder
    $errEv = Register-ObjectEvent -InputObject $proc -EventName ErrorDataReceived `
        -Action { if ($EventArgs.Data -ne $null) { [void]$Event.MessageData.AppendLine($EventArgs.Data) } } `
        -MessageData $errBuilder
    $started = $false
    try {
        $started = $proc.Start()
        if (-not $started) { throw "SMOKE_PROCESS_START_FAILED" }
        $proc.BeginOutputReadLine()
        $proc.BeginErrorReadLine()
        $proc.WaitForExit()
    } finally {
        Unregister-Event -SourceIdentifier $outEv.Name -ErrorAction SilentlyContinue
        Unregister-Event -SourceIdentifier $errEv.Name -ErrorAction SilentlyContinue
    }
    return [pscustomobject]@{
        Started = $started
        ExitCode = $proc.ExitCode
        Stdout = $outBuilder.ToString()
        Stderr = $errBuilder.ToString()
    }
}

# ------------------- Fail-stop terminal -------------------

$global:SmokeResult = 'FAIL'
$global:SmokeFailure = ''
$global:SmokeFailureCodes = New-Object System.Collections.Generic.List[string]

function Fail-Stop {
    param([Parameter(Mandatory=$true)][string]$Code,
          [string]$Message = '')
    if ($global:SmokeResult -ne 'PASS') {
        $global:SmokeResult = 'FAIL'
    }
    if (-not $global:SmokeFailure) {
        $global:SmokeFailure = "$($Code): $Message"
    }
    if (-not $global:SmokeFailureCodes.Contains($Code)) {
        $global:SmokeFailureCodes.Add($Code) | Out-Null
    }
    throw [System.Exception]::new($Code)
}

# ------------------- Catch-all (single terminal) -------------------
# The helper functions above are defined regardless. The orchestrator main
# flow runs ONLY when this script is invoked as an entry point (not
# dot-sourced), so the helper functions (e.g. ConvertTo-NativeArgument) can be
# imported hermetically for unit testing without executing any smoke logic.

if ($MyInvocation.InvocationName -ne '.') {
try {
    # ------------------- Prologue (Phase A) -------------------
    if (-not (Test-Path -LiteralPath 'env:LOCALAPPDATA')) {
        Fail-Stop 'SMOKE_NOT_AUTHORIZED' 'LOCALAPPDATA not set'
    }
    $LocalAppData = $env:LOCALAPPDATA
    if (-not (Test-Path -LiteralPath "$LocalAppData" -PathType Container)) {
        Fail-Stop 'OPENROUTER_CODEX_HOME_INVALID' "LOCALAPPDATA not a directory: $LocalAppData"
    }

    $TaskKey = $env:CODEX_TASK_KEY
    if (-not $TaskKey -or $TaskKey.Trim().Length -eq 0) {
        Fail-Stop 'INVALID_TASK_KEY' 'CODEX_TASK_KEY is required'
    }

    # Two distinct roots:
    # - $RepoRoot: the Git repository under test (default: repo containing
    #   the harness; tests override via CODEX_REPO).
    # - $ControllerDir: the controller's source tree (always the main
    #   checkout's tools/codex-controller, since the harness lives there).
    if ($env:CODEX_REPO -and $env:CODEX_REPO.Trim()) {
        $RepoRoot = (Resolve-Path $env:CODEX_REPO).Path
    } else {
        $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
    }
    $ControllerDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    if (-not (Test-Path -LiteralPath $ControllerDir -PathType Container)) {
        Fail-Stop 'SMOKE_BUILD_FAILED' "Controller dir missing: $ControllerDir"
    }
    $ControlJs = Join-Path $ControllerDir 'dist\smoke\smoke-control.js'
    $TaskFile = Join-Path $PSScriptRoot 'task.txt'
    $SmokeDir = Join-Path $LocalAppData 'print-engineer-codex\smoke'
    if (-not (Test-Path -LiteralPath $SmokeDir)) {
        New-Item -ItemType Directory -Path $SmokeDir -Force | Out-Null
    }

    $AuthPath = Join-Path $SmokeDir 'authorization'
    $WorktreeRoot = if ($env:CODEX_WORKTREE_ROOT -and $env:CODEX_WORKTREE_ROOT.Trim()) {
        $w = $env:CODEX_WORKTREE_ROOT
        # Already-absolute paths pass through. Relative paths anchor to RepoRoot.
        # No Resolve-Path here — the worktree root may not exist yet, and
        # Resolve-Path would throw under Set-StrictMode.
        if (-not [System.IO.Path]::IsPathRooted($w)) { Join-Path $RepoRoot $w } else { $w }
    } else { Join-Path $RepoRoot '..\.codex-worktrees' }
    $TargetWorktree = Join-Path $WorktreeRoot $TaskKey
    $BaseBranch = if ($env:CODEX_BASE_BRANCH -and $env:CODEX_BASE_BRANCH.Trim()) { $env:CODEX_BASE_BRANCH } else { 'master' }
    $RegistryPath = Join-Path $RepoRoot 'tools\openrouter-free-selector\config\codex-compatible-free-models-v1.json'

    # The harness must NOT auto-create an authorization. If absent, fail closed.
    if (-not (Test-Path -LiteralPath $AuthPath)) {
        Fail-Stop 'SMOKE_NOT_AUTHORIZED'
    }

    # ------------------- Build -------------------
    # The approved plan requires the build gate (§7 step 0). We ALWAYS rebuild;
    # stale dist output is never trusted on the basis of an environment flag.
    Push-Location -LiteralPath $ControllerDir
    try {
        # Use PowerShell's & operator for npm.cmd so that npm.cmd is
        # invoked the same way the package's npm scripts invoke it. npm
        # writes informational lines to stderr; under $ErrorActionPreference
        # 'Stop' those would become terminating errors, so relax EAP for the
        # duration of the native build call (the exit code is validated below).
        $previousEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $npmOutput = & 'npm.cmd' run build 2>&1 | Out-String
            $npmExit = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousEap
        }
        if ($npmExit -ne 0) {
            $buildOutPath = Join-Path $SmokeDir 'build.stdout.txt'
            Write-Utf8NoBom -Path $buildOutPath -Content $npmOutput
            Fail-Stop 'SMOKE_BUILD_FAILED' "npm run build exit $npmExit; see $buildOutPath"
        }
        if (-not (Test-Path -LiteralPath $ControlJs)) {
            Fail-Stop 'SMOKE_BUILD_FAILED' "smoke-control.js missing at $ControlJs"
        }
    } finally {
        Pop-Location
    }

    # ------------------- Worktree setup (Phase A) -------------------
    # Controller parity (§2.3 / §9): the origin/<base> fetch occurs FIRST,
    # unconditionally — including when a legitimate existing worktree is
    # reused — exactly as `ensureWorktree` does. The parent worktree root is
    # created (mirrors `mkdirSync(worktreeRoot, { recursive: true })`).
    $worktreeParent = Split-Path -Parent $TargetWorktree
    if (-not (Test-Path -LiteralPath $worktreeParent)) {
        New-Item -ItemType Directory -Path $worktreeParent -Force | Out-Null
    }
    Invoke-Native 'SMOKE_FETCH_FAILED' 'git' @('-C', $RepoRoot, 'fetch', 'origin', $BaseBranch) | Out-Null

    if (Test-Path -LiteralPath (Join-Path $TargetWorktree '.git')) {
        # Reuse: must be a valid Git worktree
        $inside = Invoke-Native 'SMOKE_WORKTREE_VALIDATION_FAILED' 'git' @('-C', $TargetWorktree, 'rev-parse', '--is-inside-work-tree')
        $top = Invoke-Native 'SMOKE_WORKTREE_VALIDATION_FAILED' 'git' @('-C', $TargetWorktree, 'rev-parse', '--show-toplevel')
        if ((Resolve-Path $top).Path -ne (Resolve-Path $TargetWorktree).Path) {
            Fail-Stop 'SMOKE_WORKTREE_VALIDATION_FAILED' "worktree top mismatch: $top"
        }
    } else {
        # Check branch existence
        $branchExists = $false
        & git -C $RepoRoot show-ref --verify --quiet "refs/heads/codex/$TaskKey"
        if ($LASTEXITCODE -eq 0) { $branchExists = $true }
        if ($branchExists) {
            Invoke-Native 'SMOKE_WORKTREE_ADD_FAILED' 'git' @('-C', $RepoRoot, 'worktree', 'add', $TargetWorktree, "codex/$TaskKey") | Out-Null
        } else {
            Invoke-Native 'SMOKE_WORKTREE_ADD_FAILED' 'git' @('-C', $RepoRoot, 'worktree', 'add', '-b', "codex/$TaskKey", $TargetWorktree, "origin/$BaseBranch") | Out-Null
        }
    }

    # Validate worktree identity
    $wtTop = (Resolve-Path (Invoke-Native 'SMOKE_WORKTREE_VALIDATION_FAILED' 'git' @('-C', $TargetWorktree, 'rev-parse', '--show-toplevel'))).Path
    if ($wtTop -ne (Resolve-Path $TargetWorktree).Path) {
        Fail-Stop 'SMOKE_WORKTREE_VALIDATION_FAILED' "worktree top mismatch: $wtTop vs $TargetWorktree"
    }
    $wtGitDir = (Invoke-Native 'SMOKE_WORKTREE_VALIDATION_FAILED' 'git' @('-C', $TargetWorktree, 'rev-parse', '--path-format=absolute', '--git-dir')).Trim()
    $wtCommonDir = (Invoke-Native 'SMOKE_WORKTREE_VALIDATION_FAILED' 'git' @('-C', $TargetWorktree, 'rev-parse', '--path-format=absolute', '--git-common-dir')).Trim()
    if ($wtGitDir -eq $wtCommonDir) {
        Fail-Stop 'SMOKE_WORKTREE_VALIDATION_FAILED' 'worktree is not a linked worktree (git-dir == common-dir)'
    }
    $repoCommonDir = (Invoke-Native 'SMOKE_WORKTREE_VALIDATION_FAILED' 'git' @('-C', $RepoRoot, 'rev-parse', '--path-format=absolute', '--git-common-dir')).Trim()
    if ($wtCommonDir -ne $repoCommonDir) {
        Fail-Stop 'SMOKE_WORKTREE_VALIDATION_FAILED' 'worktree common-dir does not match repo common-dir'
    }
    foreach ($secret in @('.env','.env.local','config\config.local.yaml')) {
        if (Test-Path -LiteralPath (Join-Path $TargetWorktree $secret)) {
            Fail-Stop 'SMOKE_WORKTREE_VALIDATION_FAILED' "secret file present: $secret"
        }
    }

    # ------------------- Pre-smoke evidence (Phase A) -------------------
    $timestamp = (Get-Date).ToUniversalTime().ToString('o')
    $wtHead = (Invoke-Native 'SMOKE_WORKTREE_VALIDATION_FAILED' 'git' @('-C', $TargetWorktree, 'rev-parse', 'HEAD')).Trim()
    $wtBranch = (Invoke-Native 'SMOKE_WORKTREE_VALIDATION_FAILED' 'git' @('-C', $TargetWorktree, 'rev-parse', '--abbrev-ref', 'HEAD')).Trim()
    $wtStatusBefore = (Invoke-Native 'SMOKE_WORKTREE_VALIDATION_FAILED' 'git' @('-C', $TargetWorktree, 'status', '--short'))

    # Registry identity (Phase A precheck)
    $regProc = Start-CapturedChild -FileName 'node' -Arguments @($ControlJs, 'registry-identity', "--registry=$RegistryPath") -WorkingDirectory $ControllerDir
    if ($regProc.ExitCode -ne 0) {
        Fail-Stop 'COMPATIBILITY_REGISTRY_INVALID' $regProc.Stderr.Trim()
    }
    $regPayload = $regProc.Stdout | ConvertFrom-Json
    $registrySha256 = [string]$regPayload.registrySha256
    $registryEntryCount = [int]$regPayload.registryEntryCount

    $openrouterKeyPresent = [bool]($env:OPENROUTER_API_KEY -and $env:OPENROUTER_API_KEY.Trim().Length -gt 0)
    $localAppDataPresent = [bool]($LocalAppData -and (Test-Path -LiteralPath $LocalAppData -PathType Container))

    $evidenceJson = [ordered]@{
        timestamp = $timestamp
        repoRoot = (Resolve-Path $RepoRoot).Path
        targetWorktree = (Resolve-Path $TargetWorktree).Path
        worktreeTopBefore = $wtTop
        worktreeCommonDirBefore = $wtCommonDir
        worktreeHeadBefore = $wtHead
        worktreeBranchBefore = $wtBranch
        worktreeStatusBefore = $wtStatusBefore
        taskKey = $TaskKey
        registryPath = (Resolve-Path $RegistryPath).Path
        registrySha256 = $registrySha256
        registryEntryCount = $registryEntryCount
        requestedProviderMode = 'auto'
        openrouterKeyPresent = $openrouterKeyPresent
        localAppDataPresent = $localAppDataPresent
        selectOnlyUsed = $false
        compatibilityProbeUsed = $false
    }

    # ------------------- Phase B — authorize + claim -------------------
    $authProc = Start-CapturedChild -FileName 'node' -Arguments @(
        $ControlJs, 'authorize',
        "--auth=$AuthPath",
        "--task=$TaskKey",
        "--worktree=$((Resolve-Path $TargetWorktree).Path)",
        "--repo=$((Resolve-Path $RepoRoot).Path)"
    ) -WorkingDirectory $ControllerDir
    if ($authProc.ExitCode -ne 0) {
        # Preserve the deterministic structured failure code emitted by
        # smoke-control (SMOKE_NOT_AUTHORIZED, SMOKE_AUTHORIZATION_INVALID,
        # SMOKE_AUTHORIZATION_TARGET_MISMATCH, SMOKE_AUTHORIZATION_EXPIRED,
        # SMOKE_AUTHORIZATION_CLAIMED_MISMATCH) rather than collapsing them
        # into a single generic code.
        $authError = $null
        try {
            $authObj = $authProc.Stdout | ConvertFrom-Json
            $authError = [string]$authObj.error
        } catch { }
        if (-not $authError) {
            $authError = 'SMOKE_AUTHORIZATION_INVALID'
        }
        $message = ($authProc.Stdout + $authProc.Stderr).Trim()
        Fail-Stop $authError $message
    }
    $authPayload = $authProc.Stdout | ConvertFrom-Json
    $evidenceJson.authorizationId = [string]$authPayload.authorizationId
    $evidenceJson.authorizationSha256 = [string]$authPayload.sha256

    # ------------------- Production launch (exactly ONE) -------------------
    $productionLaunches = 0
    $childExitCode = 1
    $controllerStdout = ''
    $controllerStderr = ''

    $stdoutPath = Join-Path $SmokeDir 'controller.stdout.txt'
    $stderrPath = Join-Path $SmokeDir 'controller.stderr.txt'
    $evidencePath = Join-Path $SmokeDir 'evidence.json'
    $statePath = Join-Path $SmokeDir 'controller-state.json'

    if ($DryRun) {
        # Dry-run: hermetic only. SMOKE_MOCK_CMD is the path to a mock .cmd/.exe
        # that publishes controller-shaped stdout; it is run through cmd.exe /c
        # (Windows cannot CreateProcess a .cmd directly with UseShellExecute=false).
        # When absent, a deterministic exit-0 node child is used. Dry-run can
        # NEVER become live and never contacts OpenRouter/Codex.
        $mockCmd = $env:SMOKE_MOCK_CMD
        if (-not $mockCmd) {
            $mockFileName = 'node'
            $mockArgs = @('-e', 'process.exit(0)')
        } else {
            $mockFileName = 'cmd.exe'
            $mockArgs = @('/c', $mockCmd)
        }
        $mockResult = Start-CapturedChild -FileName $mockFileName -Arguments $mockArgs -WorkingDirectory $ControllerDir
        $productionLaunches = 1
        $childExitCode = $mockResult.ExitCode
        $controllerStdout = $mockResult.Stdout
        $controllerStderr = $mockResult.Stderr
    } else {
        # REAL live automatic-fallback production path (§7 step 4). Launch
        # EXACTLY ONE normal production controller child:
        #   node dist/src/index.js   (NO --select-only, NO --compatibility-probe)
        # with CODEX_PROVIDER_MODE=auto plus the task/worktree/task-file
        # environment required by the controller. The command spec is built
        # hermetically by smoke-control (`production-command` subcommand) so
        # there is ONE source of truth that is unit-testable without external
        # inference. The harness itself does NOT select candidate models — the
        # normal production selector owns model selection.
        $prodProc = Start-CapturedChild -FileName 'node' -Arguments @(
            $ControlJs, 'production-command',
            "--controller=$ControllerDir",
            "--task=$TaskKey",
            "--worktree=$((Resolve-Path $TargetWorktree).Path)",
            "--repo=$((Resolve-Path $RepoRoot).Path)",
            "--task-file=$TaskFile",
            "--state=$statePath",
            "--base=$BaseBranch",
            "--worktree-root=$WorktreeRoot"
        ) -WorkingDirectory $ControllerDir
        if ($prodProc.ExitCode -ne 0) {
            Fail-Stop 'SMOKE_LAUNCH_FAILED' 'production-command construction failed'
        }
        $spec = $prodProc.Stdout | ConvertFrom-Json
        $prodEnv = @{}
        foreach ($prop in @($spec.environment.PSObject.Properties)) {
            $prodEnv[$prop.Name] = [string]$prop.Value
        }
        $prodResult = Start-CapturedChild -FileName ([string]$spec.fileName) `
            -Arguments @($spec.arguments) `
            -WorkingDirectory ([string]$spec.workingDirectory) `
            -Environment $prodEnv
        # Exactly ONE production launch immediately around this child. No retry,
        # no second launch, no post-failure launch. A non-zero child exit is
        # captured as evidence and MUST fail the evaluation (final FAIL).
        $productionLaunches = 1
        $childExitCode = $prodResult.ExitCode
        $controllerStdout = $prodResult.Stdout
        $controllerStderr = $prodResult.Stderr
    }

    # Apply secret redaction at capture boundaries (§14). Authorization must
    # never be persisted; boolean presence only. The harness uses any
    # OPENROUTER_API_KEY value the operator set in the environment to redact
    # it from captured output, but the harness itself MUST NOT print the value.
    $openrouterKey = if ($env:OPENROUTER_API_KEY) { $env:OPENROUTER_API_KEY } else { '' }
    $secrets = @($openrouterKey)
    if ($controllerStdout) {
        foreach ($s in $secrets) { if ($s) { $controllerStdout = $controllerStdout.Replace($s, '[REDACTED]') } }
        $controllerStdout = [regex]::Replace($controllerStdout, 'Bearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [REDACTED]')
        $controllerStdout = [regex]::Replace($controllerStdout, 'sk-or-[A-Za-z0-9-]+', 'sk-or-[REDACTED]')
    }
    if ($controllerStderr) {
        foreach ($s in $secrets) { if ($s) { $controllerStderr = $controllerStderr.Replace($s, '[REDACTED]') } }
        $controllerStderr = [regex]::Replace($controllerStderr, 'Bearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [REDACTED]')
        $controllerStderr = [regex]::Replace($controllerStderr, 'sk-or-[A-Za-z0-9-]+', 'sk-or-[REDACTED]')
    }

    Write-Utf8NoBom -Path $stdoutPath -Content $controllerStdout
    Write-Utf8NoBom -Path $stderrPath -Content $controllerStderr

    # ------------------- Post-smoke evidence -------------------
    $wtHeadAfter = (Invoke-Native 'SMOKE_WORKTREE_VALIDATION_FAILED' 'git' @('-C', $TargetWorktree, 'rev-parse', 'HEAD')).Trim()
    $wtBranchAfter = (Invoke-Native 'SMOKE_WORKTREE_VALIDATION_FAILED' 'git' @('-C', $TargetWorktree, 'rev-parse', '--abbrev-ref', 'HEAD')).Trim()
    $wtStatusAfter = (Invoke-Native 'SMOKE_WORKTREE_VALIDATION_FAILED' 'git' @('-C', $TargetWorktree, 'status', '--short'))
    $wtTopAfter = (Resolve-Path (Invoke-Native 'SMOKE_WORKTREE_VALIDATION_FAILED' 'git' @('-C', $TargetWorktree, 'rev-parse', '--show-toplevel'))).Path

    # Try to load smoke controller-state (if the live path wrote one)
    $stateModelIdentity = $null
    $stateProviderMode = $null
    $stateRole = $null
    $stateThreadId = $null
    $stateWorktree = $null
    if (Test-Path -LiteralPath $statePath) {
        try {
            $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
            if ($state.threads -and $state.threads.$TaskKey) {
                $t = $state.threads.$TaskKey
                $stateModelIdentity = [string]$t.modelIdentity
                $stateProviderMode = [string]$t.providerMode
                $stateRole = [string]$t.role
                $stateThreadId = [string]$t.threadId
                $stateWorktree = [string]$t.worktree
            }
        } catch { }
    }

    # Record presence (deterministic path)
    $recordPath = $null
    $recordPresent = $false
    $recordModelId = $null
    $recordProviderId = $null
    $recordWorktree = $null
    try {
        $recordParent = Join-Path $wtCommonDir 'print-engineer\model-runner\selector-v1\readonly-executions'
        if (Test-Path -LiteralPath $recordParent) {
            $keyInput = "$TaskKey|$((Resolve-Path $TargetWorktree).Path)"
            $hash = [System.BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash([System.Text.Encoding]::UTF8.GetBytes($keyInput))).Replace('-','').ToLower()
            $candidate = Join-Path $recordParent "$hash.json"
            if (Test-Path -LiteralPath $candidate) {
                $recordPresent = $true
                $recordPath = $candidate
                try {
                    $r = Get-Content -LiteralPath $candidate -Raw | ConvertFrom-Json
                    $recordModelId = [string]$r.model_id
                    $recordProviderId = [string]$r.provider_id
                    $recordWorktree = [string]$r.worktree_path
                } catch { }
            }
        }
    } catch { }

    $evidenceJson.worktreeTopAfter = $wtTopAfter
    $evidenceJson.worktreeCommonDirAfter = $wtCommonDir
    $evidenceJson.worktreeHeadAfter = $wtHeadAfter
    $evidenceJson.worktreeBranchAfter = $wtBranchAfter
    $evidenceJson.worktreeStatusAfter = $wtStatusAfter
    $evidenceJson.childExitCode = $childExitCode
    $evidenceJson.controllerStdoutPath = $stdoutPath
    $evidenceJson.controllerStderrPath = $stderrPath
    $evidenceJson.controllerStatePath = $statePath
    $evidenceJson.stateModelIdentity = $stateModelIdentity
    $evidenceJson.stateProviderMode = $stateProviderMode
    $evidenceJson.stateRole = $stateRole
    $evidenceJson.stateThreadId = $stateThreadId
    $evidenceJson.stateWorktree = $stateWorktree
    $evidenceJson.recordPresent = $recordPresent
    $evidenceJson.recordModelId = $recordModelId
    $evidenceJson.recordProviderId = $recordProviderId
    $evidenceJson.recordWorktree = $recordWorktree
    $evidenceJson.productionLaunches = $productionLaunches
    # Derived facts (§2.6): structurally guaranteed by the reviewed production
    # contract plus the observed single launch. Explicitly represented so no
    # fictional directly-emitted counter is implied.
    $evidenceJson.preflightCountDerived = 1
    $evidenceJson.inferenceCountDerived = 1
    $evidenceJson.retryCountDerived = $productionLaunches - 1

    Write-Utf8NoBom -Path $evidencePath -Content (ConvertTo-Json -InputObject $evidenceJson -Depth 10)

    # ------------------- Decision (single PASS terminal) -------------------
    $evalProc = Start-CapturedChild -FileName 'node' -Arguments @($ControlJs, 'evaluate', "--evidence=$evidencePath") -WorkingDirectory $ControllerDir
    if ($evalProc.ExitCode -eq 0) {
        $global:SmokeResult = 'PASS'
        $global:SmokeFailure = ''
    } else {
        $global:SmokeResult = 'FAIL'
        try {
            $evalObj = $evalProc.Stdout | ConvertFrom-Json
            $global:SmokeFailure = [string]$evalObj.error
            foreach ($f in @($evalObj.failures)) {
                if (-not $global:SmokeFailureCodes.Contains([string]$f)) {
                    $global:SmokeFailureCodes.Add([string]$f) | Out-Null
                }
            }
        } catch {
            $global:SmokeFailure = 'SMOKE_EVALUATION_FAIL'
        }
    }
}
catch {
    if ($global:SmokeResult -ne 'PASS') {
        $global:SmokeResult = 'FAIL'
        if (-not $global:SmokeFailure) {
            $global:SmokeFailure = $_.Exception.Message
        }
    }
}

# ------------------- Terminal output (single line, success-only PASS) -------------------
$codes = ($global:SmokeFailureCodes | Select-Object -Unique) -join ','
if ($global:SmokeResult -eq 'PASS') {
    Write-Output 'SMOKE_RESULT=PASS'
    exit 0
} else {
    Write-Output "SMOKE_RESULT=FAIL"
    if ($codes) { Write-Output "failures=$codes" }
    if ($global:SmokeFailure) { Write-Output "failure=$($global:SmokeFailure)" }
    exit 1
}
}
