[CmdletBinding()]
param(
    [string]$InputPath,
    [string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
if (-not $InputPath) {
    $InputPath = Join-Path $repositoryRoot ".local\alpaca-validation\disclosure-input.json"
}
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repositoryRoot ".local\alpaca-validation"
}
$InputPath = [System.IO.Path]::GetFullPath($InputPath)
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)

if (-not (Test-Path -LiteralPath $InputPath -PathType Leaf)) {
    throw "Disclosure candidate not found: $InputPath"
}

function Read-MaskedValue {
    param([Parameter(Mandatory = $true)][string]$Prompt)

    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        if ([string]::IsNullOrWhiteSpace($value)) {
            throw "$Prompt cannot be empty"
        }
        return $value
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

$previousKeyId = [Environment]::GetEnvironmentVariable("ALPACA_API_KEY_ID", "Process")
$previousSecret = [Environment]::GetEnvironmentVariable("ALPACA_API_SECRET_KEY", "Process")
$previousPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH", "Process")
$keyId = $null
$secretKey = $null

try {
    Write-Host "Enter the Alpaca credentials from the dashboard. Input is masked and is not saved to disk."
    $keyId = Read-MaskedValue "Alpaca API Key ID"
    $secretKey = Read-MaskedValue "Alpaca API Secret Key"
    [Environment]::SetEnvironmentVariable("ALPACA_API_KEY_ID", $keyId, "Process")
    [Environment]::SetEnvironmentVariable("ALPACA_API_SECRET_KEY", $secretKey, "Process")
    [Environment]::SetEnvironmentVariable(
        "PYTHONPATH", (Join-Path $repositoryRoot "backend\src"), "Process")

    New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
    $candidate = Join-Path $OutputDirectory "candidate.json"
    $processed = Join-Path $OutputDirectory "processed.json"
    $audit = Join-Path $OutputDirectory "audit.json"
    $dashboard = Join-Path $OutputDirectory "dashboard.html"

    & python -m unison_snapshot build-alpaca-market-validation `
        --input $InputPath `
        --output $candidate `
        --processed-output $processed `
        --audit-output $audit `
        --html-output $dashboard
    if ($LASTEXITCODE -ne 0) {
        throw "Alpaca validation failed with exit code $LASTEXITCODE"
    }

    $auditData = Get-Content -Raw -LiteralPath $audit | ConvertFrom-Json
    Write-Host "Validation complete."
    Write-Host "Requested tickers: $($auditData.symbol_count)"
    Write-Host "Covered tickers:   $($auditData.market_row_count)"
    Write-Host "Missing tickers:   $($auditData.missing_ticker_count)"
    Write-Host "Price points:      $($auditData.price_point_count)"
    Write-Host "Dashboard:         $dashboard"
}
finally {
    [Environment]::SetEnvironmentVariable("ALPACA_API_KEY_ID", $previousKeyId, "Process")
    [Environment]::SetEnvironmentVariable("ALPACA_API_SECRET_KEY", $previousSecret, "Process")
    [Environment]::SetEnvironmentVariable("PYTHONPATH", $previousPythonPath, "Process")
    $keyId = $null
    $secretKey = $null
}
