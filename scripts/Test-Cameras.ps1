[CmdletBinding()]
param(
    [string]$BaseUrl = 'http://192.168.1.12:8770',
    [ValidateRange(1, 100)][int]$ExpectedCameras = 2,
    [ValidateRange(1, 120)][int]$TimeoutSeconds = 8,
    [ValidateRange(1, 120)][double]$MaxFrameAgeSeconds = 5,
    [switch]$OpenBrowser
)
$ErrorActionPreference = 'Stop'

function Get-CameraStatus {
    $request = [Net.HttpWebRequest]::Create($BaseUrl.TrimEnd('/') + '/api/cameras')
    $request.Proxy = $null
    $request.Timeout = $TimeoutSeconds * 1000
    $request.ReadWriteTimeout = $TimeoutSeconds * 1000
    $response = $request.GetResponse()
    $reader = [IO.StreamReader]::new($response.GetResponseStream())
    try { $reader.ReadToEnd() | ConvertFrom-Json }
    finally { $reader.Dispose(); $response.Dispose() }
}

try {
    $first = Get-CameraStatus
    if ($first.service -ne 'Hikrobot dual-camera server') { throw 'The endpoint is not the camera service.' }
    $cameras = @($first.cameras)
    if ($cameras.Count -ne $ExpectedCameras) { throw "Expected $ExpectedCameras cameras, found $($cameras.Count)." }
    Start-Sleep -Milliseconds 1200
    $second = Get-CameraStatus
    if (@($second.cameras).Count -ne $ExpectedCameras) { throw 'The camera count changed during the check.' }
    foreach ($camera in $second.cameras) {
        $before = @($cameras | Where-Object { $_.serial -eq $camera.serial })
        if ($before.Count -ne 1 -or $camera.status -ne 'running' -or -not $camera.last_frame) {
            throw "Camera $($camera.serial) is not running: $($camera.error)"
        }
        if ($null -eq $camera.frame_age_seconds -or $camera.frame_age_seconds -gt $MaxFrameAgeSeconds) {
            throw "Camera $($camera.serial) has stale frames."
        }
        if ($camera.frame_count -le $before[0].frame_count) { throw "Camera $($camera.serial) frames are not advancing." }
    }
    $second.cameras | Select-Object serial, model, status, @{Name='Size';Expression={"$($_.last_frame.width)x$($_.last_frame.height)"}}, @{Name='FPS';Expression={[math]::Round($_.fps, 2)}}, frame_count, lost_packets_total | Format-Table -AutoSize
    Write-Host "PASS: both camera connections are live. $BaseUrl/"
    if ($OpenBrowser) { Start-Process ($BaseUrl.TrimEnd('/') + '/') }
} catch {
    Write-Error ("Camera connection check failed: " + $_.Exception.Message + "`nCheck the Ethernet cable, ARK power, client IPv4/host route and docs/troubleshooting.md.")
    exit 1
}
