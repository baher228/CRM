[CmdletBinding()]
param(
    [string]$Destination,
    [ValidateRange(0, 3650)]
    [int]$Daily = 30,
    [ValidateRange(0, 1200)]
    [int]$Monthly = 12,
    [ValidateRange(0, 100)]
    [int]$Annual = 7
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $RepoRoot 'backend'
$Python = Join-Path $BackendRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Backend virtual environment not found at $Python"
}
if (-not $Destination) {
    if (-not $env:LOCALAPPDATA) {
        throw 'LOCALAPPDATA is not available; pass -Destination explicitly.'
    }
    $Destination = Join-Path $env:LOCALAPPDATA 'CRMWorkspace\backups'
}
$Destination = [IO.Path]::GetFullPath($Destination)

$Code = @'
import json
import sys
from dataclasses import asdict
from app.integrations_v1.backup import create_backup, prune_backups

destination, daily, monthly, annual = sys.argv[1], *(int(value) for value in sys.argv[2:])
info = create_backup(destination)
deleted = prune_backups(destination, daily=daily, monthly=monthly, annual=annual)
print(json.dumps(dict(asdict(info), pruned=len(deleted))))
'@

Push-Location $BackendRoot
try {
    & $Python -c $Code $Destination $Daily $Monthly $Annual
    if ($LASTEXITCODE -ne 0) {
        throw "CRM backup failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
