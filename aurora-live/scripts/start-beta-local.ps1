# Start AURORA LIVE private local beta (Windows).
# Prerequisites: Python 3.12+, pip packages (waitress, gunicorn optional, certifi, ...), .env present.
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

Write-Host "Running release_check --allow-local..."
& $py release_check.py --env .env --allow-local
if ($LASTEXITCODE -ne 0) { throw "release_check failed" }

Write-Host "Starting release_worker..."
Start-Process -FilePath $py -ArgumentList "release_worker.py" -WorkingDirectory $Root -WindowStyle Minimized

Write-Host "Starting platform on http://127.0.0.1:8090 ..."
Write-Host "Create admin: POST /api/platform/users {`"email`":`"you@example.com`",`"role`":`"admin`"}"
& $py -c "from waitress import serve; from release_wsgi import application; print('AURORA beta listening on http://127.0.0.1:8090'); serve(application, host='127.0.0.1', port=8090, threads=8)"
