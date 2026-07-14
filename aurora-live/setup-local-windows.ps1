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
    return [Convert