[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $RepoRoot 'backend'
$Python = Join-Path $BackendRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Backend virtual environment not found at $Python"
}
if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot 'frontend\dist\index.html') -PathType Leaf)) {
    throw 'Production frontend is missing. Run the frontend build before starting CRM Workspace.'
}

$DataRoot = if ($env:CRM_DATA_DIR) {
    [IO.Path]::GetFullPath($env:CRM_DATA_DIR)
} elseif ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA 'CRMWorkspace'
} else {
    throw 'LOCALAPPDATA is not available.'
}
$LogRoot = Join-Path $DataRoot 'logs'
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null

$env:CRM_ENV = 'production'
$env:CRM_DEV_CORS = 'false'
$env:CRM_SECURITY_BYPASS = 'false'
$env:CRM_INTEGRATIONS_FAKE = 'false'
$env:CRM_DISCOVERY_FAKE = 'false'
$env:CRM_INCLUDE_DEMO_DATA = 'false'
$env:CRM_INCLUDE_DEMO_LEADS = 'false'
$env:CRM_COOKIE_SECURE = 'false' # Loopback production is HTTP; HttpOnly + SameSite remain enforced.
$env:CRM_PORT = [string]$Port
$env:CRM_APP_ORIGIN = "http://127.0.0.1:$Port"

$HealthUri = "http://127.0.0.1:$Port/api/health"
$SessionUri = "http://127.0.0.1:$Port/api/v1/session"
$Running = $false
try {
    $null = Invoke-WebRequest -UseBasicParsing -Uri $HealthUri -TimeoutSec 2
    $Running = $true
} catch {
    $Running = $false
}

if ($Running) {
    try {
        $null = Invoke-WebRequest -UseBasicParsing -Uri $SessionUri -TimeoutSec 2
        throw "Port $Port is already serving an unsecured or development CRM process. Stop it before using the production launcher."
    } catch {
        $StatusCode = [int]$_.Exception.Response.StatusCode
        if ($StatusCode -ne 401) {
            throw
        }
    }
}

if (-not $Running) {
    $Process = Start-Process -FilePath $Python `
        -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', [string]$Port) `
        -WorkingDirectory $BackendRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogRoot 'server.out.log') `
        -RedirectStandardError (Join-Path $LogRoot 'server.err.log') `
        -PassThru
    Set-Content -LiteralPath (Join-Path $DataRoot 'server.pid') -Value $Process.Id -Encoding ascii

    $Deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        if ($Process.HasExited) {
            throw "CRM Workspace stopped during startup. See $LogRoot"
        }
        Start-Sleep -Milliseconds 500
        try {
            $null = Invoke-WebRequest -UseBasicParsing -Uri $HealthUri -TimeoutSec 2
            $Running = $true
        } catch {
            $Running = $false
        }
    } until ($Running -or [DateTime]::UtcNow -ge $Deadline)
    if (-not $Running) {
        Stop-Process -Id $Process.Id -ErrorAction SilentlyContinue
        throw "CRM Workspace did not become ready within 30 seconds. See $LogRoot"
    }
    try {
        $null = Invoke-WebRequest -UseBasicParsing -Uri $SessionUri -TimeoutSec 2
        throw 'CRM Workspace started without production session enforcement.'
    } catch {
        $StatusCode = [int]$_.Exception.Response.StatusCode
        if ($StatusCode -ne 401) {
            Stop-Process -Id $Process.Id -ErrorAction SilentlyContinue
            throw
        }
    }
}

$SecretPath = Join-Path $DataRoot 'bootstrap.secret'
if (-not (Test-Path -LiteralPath $SecretPath -PathType Leaf)) {
    throw 'The security bootstrap secret was not created. Ensure install_local_security(app) is enabled.'
}
$BootstrapUri = "http://127.0.0.1:$Port/api/v1/session/bootstrap"
$BootstrapPage = Invoke-WebRequest -UseBasicParsing -Uri $BootstrapUri -TimeoutSec 5
if ($BootstrapPage.Content -notlike '*location.hash.slice(1)*') {
    throw 'The running server does not expose the secure browser bootstrap endpoint.'
}
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $SecretPath '/inheritance:r' '/grant:r' "${Identity}:(R,W)" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Could not restrict the bootstrap secret to the current Windows user.'
}

if (-not $NoBrowser) {
    $Secret = (Get-Content -LiteralPath $SecretPath -Raw).Trim()
    if (-not $Secret) {
        throw 'The security bootstrap secret is empty.'
    }
    Start-Process "$BootstrapUri#$Secret"
}
