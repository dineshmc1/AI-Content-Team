[CmdletBinding()]
param(
    [string]$Database = "data\social_manager.sqlite3",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
Set-Location $projectRoot

# This performs real deterministic initialization and starts the local control surface.
# Creative tasks are still handed to ChatGPT Desktop and remain visibly waiting until
# their validated output is imported; this launcher does not claim unattended Desktop control.
python -m social_manager intelligence-init --database $Database
& (Join-Path $projectRoot "scripts\start-agency.ps1") -Database $Database -NoBrowser:$NoBrowser
python -m social_manager agency-run --database $Database
Write-Host "The daily workflow performed deterministic local planning. Any displayed Desktop handoffs still require validated human-operated ChatGPT Desktop output."
