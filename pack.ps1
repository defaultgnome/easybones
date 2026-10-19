# Pack the addon into dist/easybones_v<version>.zip, manifest at zip root (Blender requires it).
# -Validate: also run `blender --command extension validate` on the zip (needs blender in PATH).
# Stdout is only the zip path (release workflow captures it); everything else goes to stderr.
param([switch]$Validate)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$m = Select-String -Path blender_manifest.toml -Pattern '^version *= *"(.*)"' | Select-Object -First 1
if (-not $m) { [Console]::Error.WriteLine('version not found in blender_manifest.toml'); exit 1 }
$Version = $m.Matches[0].Groups[1].Value
$Out = "dist/easybones_v$Version.zip"
New-Item -ItemType Directory -Force dist | Out-Null
if (Test-Path $Out) { Remove-Item $Out }
Compress-Archive -Path __init__.py, blender_manifest.toml -DestinationPath $Out
if ($Validate) {
  if (-not (Get-Command blender -ErrorAction SilentlyContinue)) {
    [Console]::Error.WriteLine('blender not found in PATH'); exit 1
  }
  & blender --command extension validate $Out 2>&1 | ForEach-Object { [Console]::Error.WriteLine($_) }
  if ($LASTEXITCODE -eq 0) {
    [Console]::Error.WriteLine('validate: OK')
  } else {
    [Console]::Error.WriteLine('validate: FAILED'); exit 1
  }
}
Write-Output $Out
