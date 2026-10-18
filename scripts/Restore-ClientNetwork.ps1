[CmdletBinding(SupportsShouldProcess = $true)]
param()
$ErrorActionPreference = 'Stop'
$statePath = Join-Path (Split-Path $PSScriptRoot) 'client-network.local.json'
if (-not (Test-Path -LiteralPath $statePath)) { Write-Host 'No saved client network changes to restore.'; return }
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
$adapter = @(Get-NetAdapter | Where-Object { [string]$_.InterfaceGuid -eq $state.InterfaceGuid })
if ($adapter.Count -ne 1) { throw 'The configured adapter is missing. Reconnect the same Ethernet/USB adapter.' }
if (-not $WhatIfPreference) {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run PowerShell as Administrator to restore network settings.' }
}
foreach ($store in @('PersistentStore', 'ActiveStore') | Where-Object { $_ -in @($state.RouteAddedStores) }) {
    $routes = @(Get-NetRoute -InterfaceIndex $adapter[0].ifIndex -DestinationPrefix "$($state.ServerAddress)/32" -PolicyStore $store -ErrorAction SilentlyContinue | Where-Object { $_.NextHop -eq '0.0.0.0' })
    foreach ($route in $routes) {
        if ($PSCmdlet.ShouldProcess($adapter[0].Name, "Remove managed $store route $($state.ServerAddress)/32")) {
            Remove-NetRoute -InterfaceIndex $adapter[0].ifIndex -DestinationPrefix "$($state.ServerAddress)/32" -NextHop '0.0.0.0' -PolicyStore $store -Confirm:$false
        }
    }
}
if ($state.AddressAdded) {
    foreach ($store in @('PersistentStore', 'ActiveStore')) {
        $addresses = @(Get-NetIPAddress -InterfaceIndex $adapter[0].ifIndex -IPAddress $state.ClientAddress -PolicyStore $store -ErrorAction SilentlyContinue)
        if ($addresses.Count -and $PSCmdlet.ShouldProcess($adapter[0].Name, "Remove managed $store address $($state.ClientAddress)")) {
            Remove-NetIPAddress -InterfaceIndex $adapter[0].ifIndex -IPAddress $state.ClientAddress -PolicyStore $store -Confirm:$false
        }
    }
    if ($state.OriginalDhcp -eq 'Enabled' -and $PSCmdlet.ShouldProcess($adapter[0].Name, 'Restore DHCP')) {
        Set-NetIPInterface -InterfaceIndex $adapter[0].ifIndex -AddressFamily IPv4 -Dhcp Enabled
    }
}
if ($PSCmdlet.ShouldProcess($statePath, 'Remove completed setup record')) { Remove-Item -LiteralPath $statePath }
if (-not $WhatIfPreference) { Write-Host 'Restore finished. Existing manual addresses, Wi-Fi, gateway and DNS settings were not edited.' }
