$envFile = Join-Path (Split-Path -Parent $PSScriptRoot) ".env.local"
$allowedKeys = @(
    "BAMBU_IP"
    "BAMBU_SERIAL"
    "BAMBU_ACCESS_CODE"
)

if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw "Local environment file not found: .env.local"
}

$parsedValues = @{}
$lineNumber = 0

foreach ($line in [System.IO.File]::ReadAllLines($envFile)) {
    $lineNumber++
    $trimmedLine = $line.Trim()

    if ($trimmedLine.Length -eq 0 -or $trimmedLine.StartsWith("#")) {
        continue
    }

    $separatorIndex = $line.IndexOf("=")
    if ($separatorIndex -lt 0) {
        throw "Malformed .env.local line $lineNumber`: expected KEY=VALUE."
    }

    $name = $line.Substring(0, $separatorIndex).Trim()
    $value = $line.Substring($separatorIndex + 1).Trim()

    if ($name.Length -eq 0) {
        throw "Malformed .env.local line $lineNumber`: key name is empty."
    }
    if ($name -notin $allowedKeys) {
        throw "Unsupported variable in .env.local: $name"
    }
    if ($parsedValues.ContainsKey($name)) {
        throw "Duplicate variable in .env.local: $name"
    }

    $parsedValues[$name] = $value
}

foreach ($name in $allowedKeys) {
    if (-not $parsedValues.ContainsKey($name)) {
        throw "Missing required variable in .env.local: $name"
    }
    if ([string]::IsNullOrWhiteSpace([string]$parsedValues[$name])) {
        throw "Required variable is empty in .env.local: $name"
    }
}

foreach ($name in $allowedKeys) {
    [System.Environment]::SetEnvironmentVariable(
        $name,
        [string]$parsedValues[$name],
        [System.EnvironmentVariableTarget]::Process
    )
}

Write-Output "Loaded local Bambu environment variables:"
$allowedKeys | ForEach-Object { Write-Output $_ }
