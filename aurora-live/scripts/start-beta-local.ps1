# Start AURORA LIVE private local beta (Windows).
# GATE-G3: loopback only. Do NOT tunnel, port-forward, LAN-share, or open firewall for 8090.
# Prerequisites: Python 3.12+, pip packages (waitress, certifi, ...), .env present.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".env")) {
  Write-Error "Missing .env — copy .env.example to .env and fill secrets first."
}

$py = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

Get-Content .env | ForEach-Object {
  $line = $_.Trim()
  if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
    $k, $v = $line.Split("=", 2)
    Set-Item -Path "env:$k" -Value $v
  }
}

New-Item -ItemType Directory -Force -Path data, var | Out-Null

# Refuse to start a second stack on the same port / orphan workers.
$listeners = Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue
if ($listeners) {
  $pids = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
  Write-Error ("Port 8090 already in use by PID(s): {0}. Stop that process first (Task Manager or Stop-Process), then re-run." -f ($pids -join ", "))
}
$existingWorkers = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object {
  $_.CommandLine -and ($_.CommandLine -match "release_worker\.py") -and ($_.CommandLine -match [regex]::Escape($Root))
}
if ($existingWorkers) {
  $wp = ($existingWorkers | ForEach-Object { $_.ProcessId }) -join ", "
  Write-Error "Existing release_worker.py already running (PID $wp). Stop it before re-launching to avoid orphan workers."
}

Write-Host "Running release_check --allow-local..."
& $py release_check.py --env .env --allow-local
if ($LASTEXITCODE -ne 0) { throw "release_check failed" }

# release_worker runs Phase 22 core + Phase 38-40 layer workers by default.
# Set AURORA_START_LAYER_WORKERS=0 to run only the legacy Phase 22 worker.
if (-not $env:AURORA_START_LAYER_WORKERS) { $env:AURORA_START_LAYER_WORKERS = "1" }
Write-Host "Starting release_worker (core + Phase 38-40 layers; AURORA_START_LAYER_WORKERS=$env:AURORA_START_LAYER_WORKERS)..."
$worker = Start-Process -FilePath $py -ArgumentList "release_worker.py" -WorkingDirectory $Root -WindowStyle Minimized -PassThru
$worker.Id | Set-Content -Path (Join-Path $Root "var\beta-worker.pid") -Encoding ascii
Write-Host "release_worker PID=$($worker.Id) (saved var\beta-worker.pid)"


Write-Host "Starting platform on http://127.0.0.1:8090 (loopback only)..."
Write-Host "Create admin (bootstrap secret REQUIRED):"
Write-Host '  POST /api/platform/users  -H "X-Bootstrap-Secret: $env:AURORA_BOOTSTRAP_SECRET"  -d {"email":"you@example.com","role":"admin"}'
$runner = Join-Path $Root "scripts\run_beta_waitress.py"
# Foreground: Ctrl+C stops Waitress. Worker PIDs are under var\beta-*.pid.
& $py $runner
