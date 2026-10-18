[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$InterfaceAlias,
    [string]$ClientAddress = '192.168.1.200',
    [string]$ServerAddress = '192.168.1.12',
    [switch]$SkipTest
)
$ErrorActionPreference = 'Stop'
$statePath = Join-Path (Split-Path $PSScriptRoot) 'client-network.local.json'

function Assert-IPv4([string]$Address) {
    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($Address, [ref]$parsed) -or $parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
        throw "Invalid IPv4 address: $Address"
    }
    return $parsed.ToString()
}
$ClientAddress = Assert-IPv4 $ClientAddress
$ServerAddress = Assert-IPv4 $ServerAddress
$clientOctets = $ClientAddress.Split('.')
$serverOctets = $ServerAddress.Split('.')
if (($clientOctets[0..2] -join '.') -ne ($serverOctets[0..2] -join '.') -or $clientOctets[3] -in @('0', '255') -or $ClientAddress -eq $ServerAddress) {
    throw 'Use distinct client/server IPv4 addresses in the same /24 subnet (no .0 or .255).'
}
if ($ClientAddress -in @('192.168.1.11', '192.168.1.12', '192.168.1.85')) {
    throw 'This address is reserved for ARK or a camera. Use an unused address such as 192.168.1.200.'
}
if ($InterfaceAlias) { $adapter = Get-NetAdapter -Name $InterfaceAlias -ErrorAction Stop }
else {
    $candidates = @(Get-NetAdapter -Physical | Where-Object { $_.Status -eq 'Up' -and ($_.MediaType -eq '802.3' -or $_.NdisPhysicalMedium -eq 14) })
    if ($candidates.Count -ne 1) {
        Get-NetAdapter -Physical | Format-Table Name, Status, LinkSpeed
        throw 'Specify the wired adapter explicitly: -InterfaceAlias "Ethernet" (use the actual name shown above).'
    }
    $adapter = $candidates[0]
}
if ($adapter.Status -ne 'Up') { throw 'The chosen Ethernet link is down. Connect the cable and power on ARK first.' }
if (-not $adapter.HardwareInterface -or ($adapter.MediaType -ne '802.3' -and $adapter.NdisPhysicalMedium -ne 14)) {
    throw 'Choose a physical Ethernet adapter, not Wi-Fi or a VPN adapter.'
}
if (-not $WhatIfPreference) {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run PowerShell as Administrator for the one-time network setup. You can preview with -WhatIf without elevation.'
    }
}
$other = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -eq $ClientAddress -and $_.InterfaceIndex -ne $adapter.ifIndex })
if ($other.Count) { throw 'The requested client address is already assigned to another adapter on this PC.' }
$existing = @(Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -eq $ClientAddress })
if ($existing.Count -and $existing[0].PrefixLength -ne 24) { throw 'The requested client address exists with a different subnet mask.' }
if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if ($state.InterfaceGuid -ne [string]$adapter.InterfaceGuid -or $state.ClientAddress -ne $ClientAddress -or $state.ServerAddress -ne $ServerAddress) {
        throw 'A different setup is saved. Run Restore-ClientNetwork.ps1 before changing this setup.'
    }
} else {
    $ipInterface = Get-NetIPInterface -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4
    $state = [pscustomobject]@{
        InterfaceGuid = [string]$adapter.InterfaceGuid
        OriginalDhcp = [string]$ipInterface.Dhcp
        ClientAddress = $ClientAddress
        ServerAddress = $ServerAddress
        AddressAdded = $false
        RouteAddedStores = @()
    }
}
function Save-NetworkState { $state | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $statePath -Encoding UTF8 }
if (-not $existing.Count -and $PSCmdlet.ShouldProcess($adapter.Name, "Add persistent IPv4 $ClientAddress/24 without gateway or DNS (DHCP on this Ethernet adapter will be disabled)")) {
    # Save ownership before changing Windows settings so interrupted setup can be restored.
    $state.AddressAdded = $true
    Save-NetworkState
    New-NetIPAddress -InterfaceIndex $adapter.ifIndex -IPAddress $ClientAddress -PrefixLength 24 -AddressFamily IPv4 | Out-Null
    $deadline = (Get-Date).AddSeconds(8)
    do {
        $newAddress = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -IPAddress $ClientAddress
        if ($newAddress.AddressState -eq 'Duplicate') { throw 'IPv4 address conflict detected. Restore and choose another client address.' }
        if ($newAddress.AddressState -eq 'Preferred') { break }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)
    if ($newAddress.AddressState -ne 'Preferred') { throw 'The client address did not become ready. See the saved state and troubleshooting guide.' }
}
# A /32 host route makes ARK use this cable even when Wi-Fi is also 192.168.1.x.
foreach ($store in @('PersistentStore', 'ActiveStore')) {
    $route = @(Get-NetRoute -InterfaceIndex $adapter.ifIndex -DestinationPrefix "$ServerAddress/32" -PolicyStore $store -ErrorAction SilentlyContinue)
    if ($route.Count -and @($route | Where-Object { $_.NextHop -ne '0.0.0.0' }).Count) { throw 'An existing host route uses a gateway. Resolve that route before setup.' }
    if (-not $route.Count -and $PSCmdlet.ShouldProcess($adapter.Name, "Add $store on-link route $ServerAddress/32")) {
        if ($store -eq 'PersistentStore') {
            $activeBefore = @(Get-NetRoute -InterfaceIndex $adapter.ifIndex -DestinationPrefix "$ServerAddress/32" -PolicyStore ActiveStore -ErrorAction SilentlyContinue)
            if (-not $activeBefore.Count -and 'ActiveStore' -notin @($state.RouteAddedStores)) {
                $state.RouteAddedStores = @($state.RouteAddedStores) + 'ActiveStore'
            }
        }
        $state.RouteAddedStores = @(@($state.RouteAddedStores) + $store | Select-Object -Unique)
        Save-NetworkState
        New-NetRoute -DestinationPrefix "$ServerAddress/32" -InterfaceIndex $adapter.ifIndex -NextHop '0.0.0.0' -RouteMetric 1 -PolicyStore $store | Out-Null
    }
}
if (-not $WhatIfPreference) {
    Write-Host "Configured $($adapter.Name): $ClientAddress/24 -> $ServerAddress via Ethernet. Wi-Fi settings were preserved."
    if (-not $SkipTest) {
        & (Join-Path $PSScriptRoot 'Test-Cameras.ps1') -BaseUrl "http://${ServerAddress}:8770"
    }
}
