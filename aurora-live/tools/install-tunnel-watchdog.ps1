# Installs a per-user Startup entry so the tunnel watchdog starts at login.
$ErrorActionPreference = "Stop"
$Root = "C:\Users\Asif Computer\aurora-live-work\intel-tripwire-main\aurora-live"
$Watch = Join-Path $Root "tools\keep-tunnel-alive.ps1"
$Startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$Cmd = Join-Path $Startup "AURORA-Cloudflare-Tunnel.cmd"

if (-not (Test-Path $Watch)) { throw "Missing $Watch" }
if (-not (Test-Path $Startup)) { New-Item -ItemType Directory -Path $Startup -Force | Out-Null }

@"
@echo off
cd /d "$Root"
start "AURORA-tunnel-watchdog" /MIN powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$Watch"
"@ | Set-Content -Path $Cmd -Encoding ASCII

# Start now
Start-Process powershell.exe -ArgumentList @(
  "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", $Watch
) -WindowStyle Hidden

Write-Host "Installed: $Cmd"
Write-Host "Watchdog started. Current link file:"
Write-Host "  $env:USERPROFILE\Desktop\AURORA-Messenger\LIVE-LINK.txt"
Write-Host ""
Write-Host "IMPORTANT: trycloudflare quick tunnels change URL if the process fully restarts."
Write-Host "The watchdog keeps downtime short and updates LIVE-LINK.txt automatically."
