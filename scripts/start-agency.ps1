[CmdletBinding()]
param(
    [string]$Database = "data\social_manager.sqlite3",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$env:SOCIAL_MANAGER_DATABASE = (Join-Path $projectRoot $Database)
$logRoot = Join-Path $projectRoot ".work\agency-logs"
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

function Test-LocalPort([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

if (-not (Test-Path (Join-Path $projectRoot "dashboard\node_modules"))) {
    throw "Dashboard dependencies are missing. Run: cd dashboard; npm install"
}

if (-not (Test-LocalPort 8000)) {
    $backendOut = Join-Path $logRoot "backend.out.log"
    $backendErr = Join-Path $logRoot "backend.err.log"
    $backend = Start-Process -FilePath "python" -ArgumentList @("-m", "uvicorn", "social_manager.api.app:app", "--host", "127.0.0.1", "--port", "8000") -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr
    Write-Host "Started local API (PID $($backend.Id))."
} else { Write-Host "Local API already listens on port 8000." }

if (-not (Test-LocalPort 3000)) {
    $frontendOut = Join-Path $logRoot "dashboard.out.log"
    $frontendErr = Join-Path $logRoot "dashboard.err.log"
    $dashboard = Start-Process -FilePath "npm.cmd" -ArgumentList @("run", "dev") -WorkingDirectory (Join-Path $projectRoot "dashboard") -WindowStyle Hidden -PassThru -RedirectStandardOutput $frontendOut -RedirectStandardError $frontendErr
    Write-Host "Started local dashboard (PID $($dashboard.Id))."
} else { Write-Host "Local dashboard already listens on port 3000." }

Write-Host "Agency dashboard: http://localhost:3000"
Write-Host "Agency API:       http://127.0.0.1:8000/api/health"
Write-Host "Logs:             $logRoot"
if (-not $NoBrowser) { Start-Process "http://localhost:3000" }
