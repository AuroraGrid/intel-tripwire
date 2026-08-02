# AURORA: keep Cloudflare quick tunnel alive and publish the current public URL.
# Quick tunnels CANNOT keep the same URL forever if the process fully dies —
# this script restarts immediately and updates LIVE-LINK.txt so you always have the current link.

$ErrorActionPreference = "SilentlyContinue"
$Tools = $PSScriptRoot
$Root = Split-Path $Tools -Parent
if (-not (Test-Path (Join-Path $Tools "cloudflared.exe"))) {
  $Root = "C:\Users\Asif Computer\aurora-live-work\intel-tripwire-main\aurora-live"
  $Tools = Join-Path $Root "tools"
}
$Exe = Join-Path $Tools "cloudflared.exe"
if (-not (Test-Path $Exe)) {
  $Exe = Join-Path $env:LOCALAPPDATA "Cloudflare\cloudflared.exe"
}
$Log = Join-Path $Tools "cf-watchdog.log"
$ErrLog = Join-Path $Tools "cf.err.log"
$OutLog = Join-Path $Tools "cf.out.log"
$LinkFile = Join-Path $env:USERPROFILE "Desktop\AURORA-Messenger\LIVE-LINK.txt"
$LinkDir = Split-Path $LinkFile -Parent
$Origin = "http://127.0.0.1:8090"
$UrlPattern = 'https://[a-z0-9-]+\.trycloudflare\.com'

function Write-Log($msg) {
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $Log -Value $line -Encoding UTF8
}

function Wait-OriginReady {
  for ($i = 0; $i -lt 30; $i++) {
    try {
      $r = Invoke-WebRequest -Uri "$Origin/api/platform/live" -UseBasicParsing -TimeoutSec 3
      if ($r.StatusCode -eq 200) { return $true }
    } catch {}
    Start-Sleep -Seconds 2
  }
  return $false
}

function Get-UrlFromLog {
  if (-not (Test-Path $ErrLog)) { return $null }
  $m = Select-String -Path $ErrLog -Pattern $UrlPattern | Select-Object -Last 1
  if ($m -and $m.Line -match $UrlPattern) { return $Matches[0] }
  return $null
}

function Publish-Url($url) {
  if (-not $url) { return }
  if (-not (Test-Path $LinkDir)) { New-Item -ItemType Directory -Path $LinkDir -Force | Out-Null }
  $platform = "$url/platform"
  Set-Content -Path $LinkFile -Value $platform -Encoding UTF8
  $copy = Join-Path $LinkDir "COPY-PASTE.txt"
  @"
AURORA GRID open beta (auto-updated link)

$platform

No password. No token. Just open.
If you see Cloudflare 1033, wait 10s or ask for the latest link from LIVE-LINK.txt
"@ | Set-Content -Path $copy -Encoding UTF8
  Write-Log "Published $platform"
}

function Start-Tunnel {
  Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 1
  Remove-Item $ErrLog, $OutLog -ErrorAction SilentlyContinue
  $args = @(
    "tunnel", "--url", $Origin,
    "--protocol", "http2",
    "--no-autoupdate",
    "--edge-ip-version", "4"
  )
  $p = Start-Process -FilePath $Exe -ArgumentList $args `
    -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog `
    -WindowStyle Hidden -PassThru
  Write-Log "Started cloudflared pid=$($p.Id)"
  # Wait for URL + registered connection
  $url = $null
  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 1
    if (-not (Get-Process -Id $p.Id -ErrorAction SilentlyContinue)) {
      Write-Log "cloudflared exited early"
      return $null
    }
    $url = Get-UrlFromLog
    if ($url) {
      $reg = Select-String -Path $ErrLog -Pattern "Registered tunnel connection" -ErrorAction SilentlyContinue
      if ($reg) {
        Publish-Url $url
        return $p
      }
    }
  }
  if ($url) { Publish-Url $url }
  return $p
}

if (-not (Test-Path $Exe)) {
  Write-Log "FATAL: cloudflared.exe not found"
  exit 1
}

Write-Log "Watchdog starting. Origin=$Origin Exe=$Exe"
if (-not (Wait-OriginReady)) {
  Write-Log "WARN: origin not ready yet; will still try tunnel"
}

$proc = $null
while ($true) {
  $alive = $false
  if ($proc -and (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) {
    $alive = $true
  } elseif (Get-Process cloudflared -ErrorAction SilentlyContinue) {
    $alive = $true
    $proc = Get-Process cloudflared | Select-Object -First 1
  }

  if (-not $alive) {
    Write-Log "Tunnel down — restarting (this is why you saw Error 1033)"
    $proc = Start-Tunnel
    if (-not $proc) {
      Write-Log "Start failed; sleep 8s"
      Start-Sleep -Seconds 8
      continue
    }
  }

  # Periodically re-publish URL from log
  $u = Get-UrlFromLog
  if ($u) { Publish-Url $u }

  Start-Sleep -Seconds 8
}
