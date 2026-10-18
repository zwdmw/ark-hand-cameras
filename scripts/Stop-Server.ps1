[CmdletBinding()]
param([string]$BaseUrl = 'http://127.0.0.1:8770')
$ErrorActionPreference = 'Stop'
$request = [Net.HttpWebRequest]::Create($BaseUrl.TrimEnd('/') + '/api/shutdown')
$request.Proxy = $null
$request.Method = 'POST'
$request.ContentType = 'application/json'
$request.Timeout = 15000
$body = [Text.Encoding]::UTF8.GetBytes('{"stop":true}')
$request.ContentLength = $body.Length
$stream = $request.GetRequestStream()
try { $stream.Write($body, 0, $body.Length) } finally { $stream.Dispose() }
$response = $request.GetResponse()
$response.Dispose()
$deadline = (Get-Date).AddSeconds(25)
do {
    $task = Get-ScheduledTask -TaskName 'HandCameraSDK-Server' -ErrorAction SilentlyContinue
    if (-not $task -or $task.State -ne 'Running') { Write-Host 'Service stopped. See server.log for camera release and trigger restoration details.'; return }
    Start-Sleep -Milliseconds 300
} while ((Get-Date) -lt $deadline)
throw 'The service is still stopping. Check server.log before using MVS.'
