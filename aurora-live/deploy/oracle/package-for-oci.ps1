# Package AURORA for copy to Oracle free-tier VM (excludes local junk).
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Out = Join-Path $env:USERPROFILE "Desktop\aurora-oci-deploy.zip"
$Stage = Join-Path $env:TEMP "aurora-oci-stage"

if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Path $Stage | Out-Null

$include = @(
  "Dockerfile", "requirements.txt", "release_wsgi.py", "release_worker.py", "release_engine.py",
  "platform_wsgi.py", "production_wsgi.py", "storage.py", "identity.py", "operations.py",
  "database.py", "app.py", "feeds.py", "delivery.py", "observability.py", "webhook_security.py",
  "worker.py", "worker_delivery.py", "worker_state.py", "durable_public.py", "durable_rate_limit.py",
  "phase8_wsgi.py", "phase8_runtime.py", "phase9_engine.py", "phase9_sources.py", "phase9_scale.py",
  "phase9_repairs.py", "phase9_final_repairs.py",
  "static", "deploy"
)

# All phase*.py at root
Get-ChildItem $Root -Filter "phase*.py" | ForEach-Object {
  Copy-Item $_.FullName (Join-Path $Stage $_.Name) -Force
}

foreach ($item in $include) {
  $src = Join-Path $Root $item
  if (Test-Path $src) {
    $dest = Join-Path $Stage $item
    if (Test-Path $src -PathType Container) {
      Copy-Item $src $dest -Recurse -Force
    } else {
      $parent = Split-Path $dest -Parent
      if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
      Copy-Item $src $dest -Force
    }
  }
}

if (Test-Path $Out) { Remove-Item $Out -Force }
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $Out -Force
Remove-Item $Stage -Recurse -Force
Write-Host "Created: $Out"
Write-Host "Copy to VM:"
Write-Host "  scp `"$Out`" ubuntu@YOUR_PUBLIC_IP:~/"
Write-Host "On VM:"
Write-Host "  sudo mkdir -p /opt/aurora-live && sudo chown `$USER:`$USER /opt/aurora-live"
Write-Host "  unzip -o ~/aurora-oci-deploy.zip -d /opt/aurora-live"
Write-Host "  bash /opt/aurora-live/deploy/oracle/setup-vm.sh"
Write-Host "  cd /opt/aurora-live && bash deploy/oracle/deploy.sh"
