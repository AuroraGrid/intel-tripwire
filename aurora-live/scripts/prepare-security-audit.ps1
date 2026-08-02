# Prepare AURORA LIVE for a cybersecurity friend audit (full feature stack, local).
# - Ensures .env exists
# - Optional: lock open access for a safer lab
# - Builds and starts Docker Compose (loopback :8090)
# - Prints smoke checks and what to send the auditor
#
# Usage:
#   .\scripts\prepare-security-audit.ps1
#   .\scripts\prepare-security-audit.ps1 -LockedLab   # open access OFF + friend password prompt
#   .\scripts\prepare-security-audit.ps1 -NoStart     # only print briefing paths

param(
  [switch]$LockedLab,
  [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "== AURORA LIVE security-audit prep ==" -ForegroundColor Cyan
Write-Host "Root: $Root"

if (-not (Test-Path ".env")) {
  if (Test-Path ".env.example") {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example — review secrets before sharing anything public." -ForegroundColor Yellow
  } else {
    Write-Error "Missing .env and .env.example. Run setup-local-windows.ps1 or create .env first."
  }
}

function Set-EnvKey([string]$Key, [string]$Value) {
  $lines = Get-Content ".env" -ErrorAction Stop
  $found = $false
  $out = foreach ($line in $lines) {
    if ($line -match "^\s*#") { $line; continue }
    if ($line -match "^\s*$") { $line; continue }
    if ($line -match "^\s*([^=]+)=(.*)$") {
      $k = $Matches[1].Trim()
      if ($k -eq $Key) {
        $found = $true
        "$Key=$Value"
        continue
      }
    }
    $line
  }
  if (-not $found) { $out += "$Key=$Value" }
  $out | Set-Content ".env" -Encoding utf8
}

if ($LockedLab) {
  Write-Host "Locked lab: AURORA_OPEN_ACCESS=0" -ForegroundColor Yellow
  $pw = Read-Host "Friend password for auditor lab (input hidden)" -AsSecureString
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw)
  try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
  }
  if (-not $plain) { Write-Error "Password required for -LockedLab" }
  Set-EnvKey "AURORA_OPEN_ACCESS" "0"
  Set-EnvKey "AURORA_FRIEND_PASSWORD" $plain
  Set-EnvKey "AURORA_CORS_ORIGIN" "http://127.0.0.1:8090"
  Set-EnvKey "AURORA_ALLOWED_HOSTS" "127.0.0.1,localhost"
  Write-Host "Wrote locked-lab auth settings into .env (do not commit)." -ForegroundColor Green
} else {
  Write-Host "Open-lab default: leave AURORA_OPEN_ACCESS as in .env (often 1 for friend demo)." -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "Auditor package (send these, NOT .env):" -ForegroundColor Cyan
Write-Host "  1) docs/SECURITY_AUDIT.md"
Write-Host "  2) GitHub repo or zip of aurora-live/ excluding .env data/ var/"
Write-Host "  3) Optional: invite them to run this script themselves"
Write-Host ""

if ($NoStart) {
  Write-Host "NoStart set — not launching Docker."
  exit 0
}

$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
  Write-Error "Docker not found. Install Docker Desktop, then re-run."
}

Write-Host "Building and starting full stack (docker compose)..." -ForegroundColor Cyan
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw "docker compose failed" }

Write-Host "Waiting for health..."
$ok = $false
for ($i = 1; $i -le 40; $i++) {
  Start-Sleep -Seconds 3
  try {
    $r = Invoke-WebRequest "http://127.0.0.1:8090/api/platform/live" -UseBasicParsing -TimeoutSec 5
    if ($r.StatusCode -eq 200) { $ok = $true; break }
  } catch { }
  Write-Host "  try $i ..."
}
if (-not $ok) {
  Write-Host "Stack may still be starting. Check: docker compose logs -f aurora-platform" -ForegroundColor Yellow
} else {
  Write-Host "LIVE OK" -ForegroundColor Green
  try {
    $h = (Invoke-WebRequest "http://127.0.0.1:8090/api/platform/health" -UseBasicParsing -TimeoutSec 15).Content
    Write-Host "health: $h"
  } catch { Write-Host "health: $($_.Exception.Message)" }
}

Write-Host ""
Write-Host "Open:  http://127.0.0.1:8090/platform" -ForegroundColor Green
Write-Host "Brief: docs/SECURITY_AUDIT.md"
Write-Host "Stop:  docker compose down"
