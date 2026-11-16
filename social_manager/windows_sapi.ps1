param(
  [Parameter(Mandatory = $true)][string]$Text,
  [Parameter(Mandatory = $true)][string]$OutputPath,
  [Parameter(Mandatory = $true)][string]$RequestedVoice,
  [Parameter(Mandatory = $true)][int]$Rate
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $voices = @($speaker.GetInstalledVoices() | Where-Object { $_.Enabled })
  if ($voices.Count -eq 0) { throw "Windows SAPI exposes no enabled local voices." }
  $selected = $voices | Where-Object { $_.VoiceInfo.Name -like "*$RequestedVoice*" } | Select-Object -First 1
  if ($null -eq $selected) {
    $selected = $voices | Where-Object { $_.VoiceInfo.Gender.ToString() -eq "Male" } | Select-Object -First 1
  }
  $fallback = $null -eq $selected
  if ($fallback) { $selected = $voices | Select-Object -First 1 }
  $speaker.SelectVoice($selected.VoiceInfo.Name)
  $speaker.Rate = [Math]::Max(-10, [Math]::Min(10, $Rate))
  $directory = Split-Path -Parent $OutputPath
  New-Item -ItemType Directory -Force -Path $directory | Out-Null
  $speaker.SetOutputToWaveFile($OutputPath)
  $speaker.Speak($Text)
  $speaker.SetOutputToNull()
  [PSCustomObject]@{
    voice_name = $selected.VoiceInfo.Name
    gender = $selected.VoiceInfo.Gender.ToString()
    fallback_voice = $fallback
  } | ConvertTo-Json -Compress
}
finally {
  $speaker.Dispose()
}
