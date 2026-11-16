$ErrorActionPreference = "Stop"
$dashboardRoot = Split-Path -Parent $PSScriptRoot
$nextOutput = Join-Path $dashboardRoot ".next-build"

# `.next` is a fully generated build cache. Clearing this exact directory avoids
# stale Windows handles from a prior Next worker; no source, media, or user data
# is removed.
if (Test-Path $nextOutput) {
    Remove-Item -LiteralPath $nextOutput -Recurse -Force
    Start-Sleep -Milliseconds 750
}

Set-Location $dashboardRoot
& (Join-Path $dashboardRoot "node_modules\.bin\next.cmd") build --webpack
exit $LASTEXITCODE
