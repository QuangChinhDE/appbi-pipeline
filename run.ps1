<#
.SYNOPSIS
    One command to take a checkout to a running stack (Windows PowerShell).

.DESCRIPTION
    The PowerShell twin of run.sh, for machines without Git Bash. Both drive the
    whole Compose project rather than individual services: bringing up `api`
    alone leaves the migration job unrun and the worker on last week's image,
    which then fails in ways that look like product bugs.

    Which services make up "everything" comes from COMPOSE_FILE in .env, so the
    engine choice stays in one place instead of being duplicated here.

.EXAMPLE
    .\run.ps1                # rebuild changed images and bring everything up
    .\run.ps1 -Pull          # git pull first, then the above
    .\run.ps1 -Fresh         # recreate containers from scratch (keeps volumes)
    .\run.ps1 -Clean         # also delete the databases, then rebuild
    .\run.ps1 -Status        # what is running
    .\run.ps1 -Stop          # stop everything, keep containers and volumes
    .\run.ps1 -Down          # remove containers and networks, keep volumes
    .\run.ps1 -Logs api      # follow one service
#>
[CmdletBinding()]
param(
    [switch]$Pull,
    [switch]$Fresh,
    [switch]$Clean,
    [switch]$NoBuild,
    [switch]$Status,
    [switch]$Stop,
    [switch]$Down,
    [string]$Logs
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

function Write-Step { param($m) Write-Host "`n==> $m" -ForegroundColor White }
function Write-Info { param($m) Write-Host "    $m" }
function Write-Warn { param($m) Write-Host "    ! $m" -ForegroundColor Yellow }
function Write-Ok   { param($m) Write-Host "    ok $m" -ForegroundColor Green }
function Stop-WithError {
    param($m)
    Write-Host "`n!!  $m" -ForegroundColor Red
    exit 1
}

# ── prerequisites ─────────────────────────────────────────────────────────
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-WithError 'docker is not installed or not on PATH'
}

docker compose version *>$null
if ($LASTEXITCODE -ne 0) {
    Stop-WithError 'docker compose is not available (need Docker Compose v2)'
}

docker info *>$null
if ($LASTEXITCODE -ne 0) {
    Stop-WithError 'the Docker daemon is not responding. Start Docker Desktop, then run this again.'
}

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    & docker compose @Args
}

# ── environment ───────────────────────────────────────────────────────────
# The credential store is encrypted with SECRET_ENCRYPTION_KEY. Generating a
# new one silently makes every stored credential undecryptable, so .env is
# created once from the example and then left alone.
if (-not (Test-Path .env)) {
    if (-not (Test-Path .env.example)) {
        Stop-WithError '.env is missing and there is no .env.example to copy'
    }
    Write-Step 'Creating .env from .env.example'
    Copy-Item .env.example .env
    $lines = Get-Content .env
    if ($lines -match '^SECRET_ENCRYPTION_KEY=\s*$') {
        $bytes = New-Object byte[] 32
        [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
        $key = [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_')
        $lines = $lines -replace '^SECRET_ENCRYPTION_KEY=\s*$', "SECRET_ENCRYPTION_KEY=$key"
        Set-Content -Path .env -Value $lines -Encoding utf8
        Write-Ok 'generated SECRET_ENCRYPTION_KEY'
    }
    Write-Warn 'review .env before using this for anything real'
}

# Back up .env on every run. A rewritten key is unrecoverable and takes the
# whole credential store with it; a dated copy makes that a five-second fix.
if (-not (Test-Path .env.backups)) { New-Item -ItemType Directory .env.backups | Out-Null }
$latest = Get-ChildItem .env.backups\env-*.bak -ErrorAction SilentlyContinue |
          Sort-Object LastWriteTime -Descending | Select-Object -First 1
$changed = $true
if ($latest) {
    $changed = (Get-FileHash .env).Hash -ne (Get-FileHash $latest.FullName).Hash
}
if ($changed) {
    Copy-Item .env ".env.backups\env-$(Get-Date -Format 'yyyyMMdd-HHmmss').bak"
    Get-ChildItem .env.backups\env-*.bak | Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 20 | Remove-Item -Force -ErrorAction SilentlyContinue
}

function Get-EnvValue {
    param([string]$Key, [string]$Default)
    $fromProcess = [Environment]::GetEnvironmentVariable($Key)
    if ($fromProcess) { return $fromProcess }
    if (Test-Path .env) {
        $match = Get-Content .env |
            ForEach-Object { ($_ -split '#')[0] } |
            Where-Object { $_ -match "^\s*$Key=" } |
            Select-Object -Last 1
        if ($match) {
            $value = ($match -split '=', 2)[1].Trim()
            if ($value) { return $value }
        }
    }
    return $Default
}
$proxyPort  = Get-EnvValue 'PROXY_PORT' '8080'
$apiPort    = Get-EnvValue 'API_PORT' '8010'
$engineType = Get-EnvValue 'ENGINE_TYPE' 'AIRBYTE_EMBEDDED'

# ── non-build actions ─────────────────────────────────────────────────────
if ($Status) {
    Invoke-Compose ps --format 'table {{.Name}}\t{{.Service}}\t{{.Status}}'
    exit 0
}
if ($Logs) {
    Invoke-Compose logs -f --tail 200 $Logs
    exit $LASTEXITCODE
}
if ($Stop) {
    Write-Step 'Stopping'
    Invoke-Compose stop
    Write-Ok 'stopped (containers and volumes kept)'
    exit 0
}
if ($Down) {
    Write-Step 'Removing containers and networks'
    Invoke-Compose down
    Write-Ok 'removed (volumes kept)'
    exit 0
}

# ── pull ──────────────────────────────────────────────────────────────────
if ($Pull) {
    Write-Step 'Pulling the latest code'
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Stop-WithError 'git is not installed'
    }
    if (git status --porcelain) {
        Write-Warn 'you have uncommitted changes; git pull may refuse to run'
    }
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) { Stop-WithError 'git pull failed. Resolve it, then run this again.' }
    Write-Ok "at $(git rev-parse --short HEAD)"
}

# ── clean ─────────────────────────────────────────────────────────────────
if ($Clean) {
    Write-Step 'Deleting all data'
    Write-Warn 'this removes the product database, the demo warehouse, and engine state.'
    Write-Warn 'credentials survive only because SECRET_ENCRYPTION_KEY in .env is untouched,'
    Write-Warn 'but everything stored in the database is going away.'
    $confirm = Read-Host "    Type 'delete' to confirm"
    if ($confirm -ne 'delete') { Stop-WithError 'cancelled' }
    Invoke-Compose down -v --remove-orphans
    Write-Ok 'volumes removed'
}

# ── build ─────────────────────────────────────────────────────────────────
$env:BUILD_SHA = (git rev-parse --short HEAD 2>$null)
if (-not $env:BUILD_SHA) { $env:BUILD_SHA = 'unknown' }
$env:BUILD_TIME = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')

if (-not $NoBuild) {
    Write-Step 'Building images from the current checkout'
    Write-Info 'containers run code baked into the image, so this is what makes'
    Write-Info 'a git pull actually take effect'
    Invoke-Compose build
    if ($LASTEXITCODE -ne 0) { Stop-WithError 'build failed. Fix the error above, then run this again.' }
    Write-Ok "images built ($env:BUILD_SHA)"
}

# ── up ────────────────────────────────────────────────────────────────────
Write-Step 'Starting the stack'
$upArgs = @('up', '-d', '--remove-orphans')
if ($Fresh) { $upArgs += '--force-recreate' }
Invoke-Compose @upArgs
if ($LASTEXITCODE -ne 0) {
    Stop-WithError "the stack did not start. '.\run.ps1 -Logs api' usually says why."
}

# ── wait until it is actually serving ─────────────────────────────────────
# `up -d` returns once containers are created, which is well before the API can
# answer. Waiting here is what makes this safe to chain in a deploy script.
Write-Step 'Waiting for the API to serve'

# Any HTTP answer means the service is listening. A thrown exception is not
# treated as "down": /readyz reports degraded state with a 503 that still
# proves the process is up, and the proxy answers / with a 307 redirect.
function Get-HttpStatus {
    param([string]$Uri)
    try {
        $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5 `
            -MaximumRedirection 0 -ErrorAction Stop
        return [int]$response.StatusCode
    } catch {
        if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
        return 0
    }
}

$deadline = (Get-Date).AddMinutes(5)
$status = 0
while ((Get-Date) -lt $deadline) {
    $status = Get-HttpStatus "http://127.0.0.1:$apiPort/readyz"
    if ($status -ne 0) { break }
    Start-Sleep -Seconds 3
}

if ($status -eq 200) {
    Write-Ok 'API is ready'
} elseif ($status -ne 0) {
    Write-Warn "the API is up but /readyz answered $status; a dependency is degraded"
    Write-Warn "detail: curl http://127.0.0.1:$apiPort/readyz?deep=1"
} else {
    Write-Warn 'the API did not answer within 5 minutes'
    Write-Warn 'check: .\run.ps1 -Logs api'
}

if ((Get-HttpStatus "http://127.0.0.1:$proxyPort/") -ne 0) {
    Write-Ok 'web UI is serving'
} else {
    Write-Warn "the web UI is not answering on port $proxyPort yet; give it a moment"
}

# ── summary ───────────────────────────────────────────────────────────────
Write-Step 'Running'
Invoke-Compose ps --format 'table {{.Name}}\t{{.Service}}\t{{.Status}}'

$demoEmail = Get-EnvValue 'NEXT_PUBLIC_DEMO_EMAIL' ''
Write-Host ''
Write-Info "web UI    http://localhost:$proxyPort"
Write-Info "API       http://localhost:$apiPort"
Write-Info "engine    $engineType"
if ($demoEmail) { Write-Info "sign in   $demoEmail" }
Write-Host ''
Write-Info '.\run.ps1 -Status      what is running'
Write-Info '.\run.ps1 -Logs api    follow a service'
Write-Info '.\run.ps1 -Stop        stop without losing anything'
Write-Host ''
