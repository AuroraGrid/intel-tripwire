# Local Docker Compose for AURORA LIVE (loopback only).
# Staged start avoids multi-service schema DDL deadlocks on a fresh Postgres volume.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".env")) {
  Write-Error "Missing .env — copy .env.example / .env.production.example and fill secrets."
}

$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
  [System.Environment]::GetEnvironmentVariable("Path", "User")

Write-Host "Building images..."
docker compose --env-file .env build
if ($LASTEXITCODE -ne 0) { throw "docker compose build failed" }

Write-Host "Starting postgres..."
docker compose --env-file .env up -d postgres
if ($LASTEXITCODE -ne 0) { throw "postgres start failed" }

Write-Host "Waiting for postgres healthy..."
$ok = $false
for ($i = 1; $i -le 40; $i++) {
  $id = docker compose ps -q postgres
  if ($id) {
    $status = docker inspect -f "{{.State.Health.Status}}" $id 2>$null
    if ($status -eq "healthy") { $ok = $true; break }
  }
  Start-Sleep -Seconds 2
}
if (-not $ok) { throw "postgres did not become healthy" }

Write-Host "Starting platform alone (schema init)..."
docker compose --env-file .env up -d --no-deps aurora-platform
if ($LASTEXITCODE -ne 0) { throw "platform start failed" }

Write-Host "Waiting for platform live..."
$liveOk = $false
for ($i = 1; $i -le 40; $i++) {
  try {
    $r = Invoke-WebRequest "http://127.0.0.1:8090/api/platform/live" -Headers @{ Host = "localhost" } -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) { $liveOk = $true; Write-Host $r.Content; break }
  } catch {
    Start-Sleep -Seconds 2
  }
}
if (-not $liveOk) {
  docker compose logs aurora-platform --tail 40
  throw "platform did not become live"
}

Write-Host "Starting workers (core + phase 38-40)..."
docker compose --env-file .env up -d aurora-worker aurora-transport-worker aurora-infrastructure-worker aurora-markets-worker
if ($LASTEXITCODE -ne 0) { throw "workers start failed" }

Write-Host "Waiting for ready..."
$readyOk = $false
for ($i = 1; $i -le 40; $i++) {
  try {
    $r = Invoke-WebRequest "http://127.0.0.1:8090/api/platform/ready" -Headers @{ Host = "localhost" } -UseBasicParsing -TimeoutSec 5
    if ($r.StatusCode -eq 200) {
      $readyOk = $true
      Write-Host $r.Content
      break
    }
  } catch {
    Start-Sleep -Seconds 3
  }
}
if (-not $readyOk) {
  docker compose logs aurora-platform aurora-worker --tail 40
  throw "platform ready check failed"
}

docker compose ps
Write-Host ""
Write-Host "Local compose UP on http://127.0.0.1:8090 (loopback only)."
Write-Host "UI: http://127.0.0.1:8090/platform"
Write-Host "Stop: docker compose down"
