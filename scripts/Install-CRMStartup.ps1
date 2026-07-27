[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [string]$TaskName = 'CRM Workspace',
    [switch]$Uninstall,
    [switch]$StartNow
)

$ErrorActionPreference = 'Stop'
if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    return
}

$StartScript = Join-Path $PSScriptRoot 'Start-CRM.ps1'
if (-not (Test-Path -LiteralPath $StartScript -PathType Leaf)) {
    throw "Launcher not found at $StartScript"
}
$PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`" -Port $Port -NoBrowser"
$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $Identity
$Principal = New-ScheduledTaskPrincipal -UserId $Identity -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Description 'Starts the loopback-only CRM Workspace for the current Windows user.' `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Force | Out-Null

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
}

