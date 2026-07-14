param(
    [switch]$Reset,
    [switch]$Stop,
    [switch]$NoBrowser,
    [string]$Email = "hr185882@gmail.com"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$EnvFile = Join-Path $Root ".env"
$TokenFile = Join-Path $Root ".aurora-local-token.txt"
$BaseUrl = "http://127.0.0.1:8090"

function New-RandomSecret {
    param([int]$Bytes = 48)
    $buffer = New-Object byte[] $Bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buffer) } finally { $rng.Dispose() }
    [Convert]::ToBase64String($buffer).TrimEnd('=').Replace('+','-').Replace('/','_')
}

function Invoke-AuroraJson {
    param([string]$Method, [string]$Path, [hashtable]$Headers = @{}, [object]$Body = $null)
    $params = @{ Method = $Method; Uri = "$BaseUrl$Path"; Headers = $Headers; UseBasicParsing = $true }
    if ($null -ne $Body) {
        $params.ContentType = "application/json"
        $params.Body = ($Body | ConvertTo-Json -Compress)
    }
    Invoke-RestMethod @params
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker is not installed or not on PATH." }
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running. Open Docker Desktop, wait for Engine running, then rerun this script." }

if ($Stop) {
    docker compose down
    Write-Host "AURORA stopped."
    exit 0
}

if ($Reset) {
    docker compose down -v --remove-orphans
    Remove-Item $EnvFile,$TokenFile -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path $EnvFile)) {
    @"
POSTGRES_PASSWORD=$(New-RandomSecret)
AURORA_BOOTSTRAP_SECRET=$(New-RandomSecret)
AURORA_WEBHOOK_SECRET=$(New-RandomSecret)
AURORA_CORS_ORIGIN=$BaseUrl
AURORA_ALLOWED_HOSTS=localhost,127.0.0.1
AURORA_TRUSTED_PROXIES=
AURORA_AUTH_RATE_LIMIT=10
AURORA_WRITE_RATE_LIMIT=120
AURORA_RATE_WINDOW_SECONDS=60
AURORA_MAX_BODY_BYTES=1000000
AURORA_METRICS_ENABLED=1
AURORA_REQUIRE_WORKER=1
AURORA_SOURCE_TIMEOUT_SECONDS=12
GUNICORN_WORKERS=2
GUNICORN_TIMEOUT=30
AURORA_REFRESH_INTERVAL_SECONDS=300
AURORA_DELIVERY_INTERVAL_SECONDS=10
AURORA_WORKER_LEASE_SECONDS=120
AURORA_WORKER_FAILURE_RETRY_SECONDS=30
AURORA_WORKER_STALE_SECONDS=120
AURORA_DELIVERY_MAX_ATTEMPTS=5
AURORA_DELIVERY_BACKOFF_SECONDS=30
AURORA_DELIVERY_MAX_BACKOFF_SECONDS=3600
"@ | Set-Content -Path $EnvFile -Encoding ascii
}

python release_check.py --env .env --allow-local
if ($LASTEXITCODE -ne 0) { throw "Local release configuration validation failed." }

docker compose up --build -d
if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed." }

$ready = $false
for ($attempt = 1; $attempt -le 40; $attempt++) {
    Start-Sleep -Seconds 3
    try {
        $status = Invoke-AuroraJson GET "/api/platform/ready"
        if ($status.status -eq "ready") { $ready = $true; break }
    } catch {}
}
if (-not $ready) {
    docker compose ps
    docker compose logs --tail 100 aurora-platform aurora-worker
    throw "AURORA did not become ready."
}

if (-not (Test-Path $TokenFile)) {
    $bootstrap = (Get-Content $EnvFile | Where-Object { $_ -like 'AURORA_BOOTSTRAP_SECRET=*' }).Split('=',2)[1]
    $created = Invoke-AuroraJson POST "/api/platform/users" @{ "X-Bootstrap-Secret" = $bootstrap } @{ email = $Email; role = "admin" }
    $created.token | Set-Content -Path $TokenFile -Encoding ascii
}

$token = (Get-Content $TokenFile -Raw).Trim()
Set-Clipboard -Value $token
Write-Host ""
Write-Host "AURORA is running: $BaseUrl/platform"
Write-Host "Admin token copied to clipboard and saved locally in .aurora-local-token.txt"
Write-Host "Stop:  .\setup-local-windows.ps1 -Stop"
Write-Host "Reset: .\setup-local-windows.ps1 -Reset"
if (-not $NoBrowser) { Start-Process "$BaseUrl/platform" }
