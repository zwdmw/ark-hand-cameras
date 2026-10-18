[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)][string]$PythonExe,
    [string]$MvsPythonPath,
    [string]$MvsRuntimePath,
    [switch]$ReplaceExistingTask
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot
$taskName = 'HandCameraSDK-Server'
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
if ($MvsPythonPath) { $env:MVS_PYTHON_PATH = $MvsPythonPath }
if ($MvsRuntimePath) { $env:MVS_RUNTIME_PATH = $MvsRuntimePath }
Push-Location $root
try {
    & $PythonExe (Join-Path $PSScriptRoot 'check_server_environment.py')
    if ($LASTEXITCODE -ne 0) { throw 'Python/SDK check failed. Install requirements-server.txt and the 64-bit MVS SDK first.' }
} finally { Pop-Location }
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and -not $ReplaceExistingTask) { throw 'The camera task already exists. Use -ReplaceExistingTask only for an intentional reinstall after stopping it.' }
if ($existing -and $existing.State -eq 'Running') { throw 'Stop the existing camera service normally before reinstalling it.' }
if (-not $WhatIfPreference) {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run PowerShell as Administrator to install the server task and firewall rule.' }
}
if ($PSCmdlet.ShouldProcess($root, 'Install a SYSTEM startup task and a restricted inbound 8770 firewall rule')) {
    $config = @{PythonExe = $PythonExe; MvsPythonPath = $MvsPythonPath; MvsRuntimePath = $MvsRuntimePath}
    $config | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'server-config.local.json') -Encoding UTF8
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + (Join-Path $PSScriptRoot 'Run-Server.ps1') + '"') -WorkingDirectory $root
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $trigger.Delay = 'PT15S'
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 5 -RestartInterval ([TimeSpan]::FromMinutes(1))
    Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Trigger $trigger -Settings $settings -Description 'Hikrobot dual camera HTTP/MJPEG service' -Force | Out-Null
    $ruleName = 'HandCameraSDK-HTTP'
    if (-not (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name $ruleName -DisplayName 'Hand Cameras HTTP 8770 (robot LAN)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8770 -RemoteAddress '192.168.1.0/24', '169.254.0.0/16' -Profile Any | Out-Null
    }
    Start-ScheduledTask -TaskName $taskName
    Write-Host 'Installed and started. Wait up to 45 seconds, then run scripts\Test-Cameras.ps1 -BaseUrl http://127.0.0.1:8770'
}
