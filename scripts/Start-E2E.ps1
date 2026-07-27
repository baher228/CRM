$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Backend virtual environment not found at $Python"
}

$TempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$DataRoot = Join-Path $TempRoot ('CRMWorkspace-Playwright-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
$env:CRM_ENV = 'test'
$env:CRM_SECURITY_BYPASS = 'true'
$env:CRM_DEV_CORS = 'false'
$env:CRM_DATA_DIR = $DataRoot
$env:CRM_DB_PATH = Join-Path $DataRoot 'crm.sqlite3'
$env:CRM_INCLUDE_DEMO_DATA = 'false'
$env:CRM_INCLUDE_DEMO_LEADS = 'false'
$env:CRM_INTEGRATIONS_FAKE = 'true'
$env:CRM_DISCOVERY_FAKE = 'true'
$env:CRM_PORT = '8765'
$env:CRM_APP_ORIGIN = 'http://127.0.0.1:8765'

Push-Location (Join-Path $RepoRoot 'backend')
try {
    & $Python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
} finally {
    Pop-Location
    $ResolvedDataRoot = [IO.Path]::GetFullPath($DataRoot)
    if ($ResolvedDataRoot.StartsWith($TempRoot, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $ResolvedDataRoot).StartsWith('CRMWorkspace-Playwright-', [StringComparison]::OrdinalIgnoreCase) -and
        (Test-Path -LiteralPath $ResolvedDataRoot)) {
        Remove-Item -LiteralPath $ResolvedDataRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
