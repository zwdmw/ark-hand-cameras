$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
try {
    $config = Get-Content -LiteralPath (Join-Path $root 'server-config.local.json') -Raw | ConvertFrom-Json
    if ($config.MvsPythonPath) { $env:MVS_PYTHON_PATH = $config.MvsPythonPath }
    if ($config.MvsRuntimePath) { $env:MVS_RUNTIME_PATH = $config.MvsRuntimePath }
    $process = Start-Process -FilePath $config.PythonExe -ArgumentList ('"' + (Join-Path $root 'server.py') + '"') -WorkingDirectory $root -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $root 'server-stdout.log') -RedirectStandardError (Join-Path $root 'server-stderr.log')
    exit $process.ExitCode
} catch {
    $_ | Out-String | Set-Content -LiteralPath (Join-Path $root 'server-launch-error.log') -Encoding UTF8
    exit 1
}
