[CmdletBinding()]
param([string]$Database = "data\social_manager.sqlite3")

$ErrorActionPreference = "Continue"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

function Report([string]$Name, [bool]$Passed, [string]$Detail) {
    $status = if ($Passed) { "PASS" } else { "CHECK" }
    Write-Host ("[{0}] {1} - {2}" -f $status, $Name, $Detail)
}

$python = Get-Command python -ErrorAction SilentlyContinue
Report "Python" ($null -ne $python) ($(if ($python) { & python --version } else { "not found" }))
$node = Get-Command node -ErrorAction SilentlyContinue
Report "Node.js" ($null -ne $node) ($(if ($node) { & node --version } else { "not found" }))
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
Report "npm" ($null -ne $npm) ($(if ($npm) { & npm.cmd --version } else { "not found" }))
Report "Dashboard dependencies" (Test-Path "dashboard\node_modules") "dashboard/node_modules"
Report "Remotion dependencies" (Test-Path "remotion\node_modules") "remotion/node_modules"
Report "Database" (Test-Path $Database) $Database
Report "Output directory" (Test-Path "output") "output"
Report "FFprobe via Remotion" (Test-Path "remotion\node_modules\@remotion\compositor-win32-x64-gnu") "bundled Remotion compositor"
$voiceCommand = 'Add-Type -AssemblyName System.Speech; $speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer; try { $speaker.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo.Name } } finally { $speaker.Dispose() }'
$voices = & powershell -NoProfile -Command $voiceCommand 2>$null
Report "Windows SAPI voices" ($LASTEXITCODE -eq 0) ($(if ($LASTEXITCODE -eq 0) { $voices -join ", " } else { "unable to enumerate" }))
$codex = Get-Command codex -ErrorAction SilentlyContinue
Report "Optional Codex CLI" ($null -ne $codex) ($(if ($codex) { "available; still requires explicit enablement" } else { "not installed or not on PATH" }))
$meta = [bool]$env:META_ACCESS_TOKEN
$youtube = [bool]$env:YOUTUBE_ACCESS_TOKEN
Report "Meta credential in current shell" $meta ($(if ($meta) { "configured" } else { "not loaded" }))
Report "YouTube credential in current shell" $youtube ($(if ($youtube) { "configured" } else { "not loaded" }))
