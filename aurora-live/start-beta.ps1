#Requires -Version 5.1
<#
.SYNOPSIS
  One-shot start for AURORA open beta: Docker + Cloudflare tunnel watchdog.
#>
$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
Set-Location $Root

Write-Host "=== AURORA LIVE open beta start ===" -ForegroundColor Cyan

# Ensure open access env
$envFile = Join-Path $Root ".env"
if (Test-Path $envFile) {
  $raw = Get-Content $envFile -Raw
  if ($raw -notmatch "AURORA_OPEN_ACCESS=1") {
    if ($raw -match "AURORA_OPEN_ACCESS=") {
      $raw = $raw -replace "AURORA_OPEN_ACCESS=\S+", "AURORA_OPEN_ACCESS=1"
    } else {
      $raw = $raw.TrimEnd() + "`nAURORA_OPEN_ACCESS=1`n"
    }
    Set-Content $envFile $raw -NoNewline -Encoding UTF8
  }
}

Write-Host "[1/4] Docker compose up..." -ForegroundColor Yellow
cmd /c "docker compose up -d --build aurora-platform aurora-worker postgres"
if ($LASTEXITCODE -ne 0) {
  Write-Host "docker compose failed (exit $LASTEXITCODE)" -ForegroundColor Red
  exit 1
}

Write-Host "[2/4] Waiting for API..." -ForegroundColor Yellow
$ready = $false
for ($i = 1; $i -le 40; $i++) {
  try {
    $r = Invoke-WebRequest "http://127.0.0.1:8090/api/platform/live" -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) {
      $body = $r.Content | ConvertFrom-Json
      Write-Host "      live open_access=$($body.open_access)" -ForegroundColor Green
      $ready = $true
      break
    }
  } catch {}
  Start-Sleep -Seconds 2
}
if (-not $ready) {
  Write-Host "API not ready — check: docker compose logs aurora-platform" -ForegroundColor Red
  exit 1
}

# Smoke open access
try {
  $me = Invoke-RestMethod "http://127.0.0.1:8090/api/platform/me" -TimeoutSec 10
  Write-Host "[3/4] Open access OK as $($me.email)" -ForegroundColor Green
} catch {
  Write-Host "[3/4] Open access me failed: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "[4/4] Starting tunnel watchdog..." -ForegroundColor Yellow
# Kill old watchdogs for this script path (best-effort)
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
$watch = Join-Path $Root "tools\keep-tunnel-alive.ps1"
if (Test-Path $watch) {
  Start-Process powershell.exe -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", $watch
  ) -WindowStyle Hidden
} else {
  Write-Host "Missing keep-tunnel-alive.ps1" -ForegroundColor Red
}

# Wait for LIVE-LINK
$linkFile = Join-Path $env:USERPROFILE "Desktop\AURORA-Messenger\LIVE-LINK.txt"
$public = $null
for ($i = 1; $i -le 30; $i++) {
  Start-Sleep -Seconds 2
  if (Test-Path $linkFile) {
    $public = (Get-Content $linkFile -Raw).Trim()
    if ($public -match "trycloudflare.com") {
      try {
        $pr = Invoke-WebRequest $public -UseBasicParsing -TimeoutSec 15
        if ($pr.StatusCode -eq 200) { break }
      } catch {}
    }
  }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " LOCAL:  http://127.0.0.1:8090/platform" -ForegroundColor Green
if ($public) {
  Write-Host " PUBLIC: $public" -ForegroundColor Green
  Write-Host " (also in Desktop\AURORA-Messenger\LIVE-LINK.txt)" -ForegroundColor DarkGray
} else {
  Write-Host " PUBLIC: still starting — check LIVE-LINK.txt in a few seconds" -ForegroundColor Yellow
}
Write-Host " NO PASSWORD. Open beta." -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
