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

# Run a native command for its exit code alone, with output discarded and its
# warnings unable to become terminating errors.
#
# `docker info *>$null` looked equivalent and was not. Windows PowerShell 5.1
# wraps each stderr line from a native executable in an ErrorRecord, and with
# $ErrorActionPreference = 'Stop' the first one terminates the script. A
# healthy Docker daemon prints warnings -- "WARNING: No blkio
# throttle.read_bps_device support" among them -- so this script refused to
# run on machines where Docker was working perfectly. Letting cmd.exe discard
# the streams keeps PowerShell from reinterpreting them.
function Test-NativeCommand {
    param([string]$CommandLine)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & cmd.exe /c "$CommandLine >NUL 2>NUL"
        return $LASTEXITCODE -eq 0
    } finally {
        $ErrorActionPreference = $previous
    }
}

if (-not (Test-NativeCommand 'docker compose version')) {
    Stop-WithError 'docker compose is not available (need Docker Compose v2)'
}

if (-not (Test-NativeCommand 'docker info')) {
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
    Write-Ok 'wrote .env'
}

# Everything below runs on every invocation, not only on creation, so an .env
# from an older clone gains the settings added since and a placeholder secret
# is replaced. This used to fire only when the line was empty while
# .env.example shipped `SECRET_ENCRYPTION_KEY=REPLACE_ME__...`, so a fresh
# clone got a key that is not a key -- and nothing said so until the first
# Source was saved.
function Get-EnvValue {
    param([string]$Key)
    $line = Select-String -Path .env -Pattern "^\s*$Key=" -ErrorAction SilentlyContinue |
        Select-Object -Last 1
    if (-not $line) { return '' }
    return ($line.Line -replace "^\s*$Key=", '').Split('#')[0].Trim()
}

function Set-EnvValue {
    param([string]$Key, [string]$Value)
    $out = Get-Content .env | ForEach-Object {
        if ($_ -like "$Key=*") { "$Key=$Value" } else { $_ }
    }
    Set-Content -Path .env -Value $out -Encoding utf8
}

function New-RandomKey {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_')
}

# A password is typed by a person, so it is shorter than a key and avoids the
# characters that get lost across a copy and paste.
function New-RandomPassword {
    $raw = (New-RandomKey) -replace '[=+/_-]', ''
    return $raw.Substring(0, [Math]::Min(20, $raw.Length))
}

# The administrator's password, generated per deployment rather than shipped.
#
# It used to be `Admin@123456`, written in .env.example, seeded by every
# install, and printed on the sign-in page beside four other accounts that
# shared it. Anybody who could open that page had a platform administrator.
$script:AdminPasswordGenerated = ''
function Set-AdminPasswordIfUnset {
    $current = Get-EnvValue 'SEED_ADMIN_PASSWORD'
    $unset = ($current -eq '') -or ($current -like 'REPLACE_ME*') -or
             ($current -like '*change-me*') -or ($current -like '*changeme*')
    if (-not $unset) { return }
    $generated = New-RandomPassword
    Set-EnvValue 'SEED_ADMIN_PASSWORD' $generated
    $script:AdminPasswordGenerated = $generated
    Write-Ok 'generated SEED_ADMIN_PASSWORD'
}

function Set-SecretIfUnset {
    param([string]$Key)
    $current = Get-EnvValue $Key
    $shipped = ($current -eq '') -or ($current -like 'REPLACE_ME*') -or
               ($current -like '*change-me*') -or ($current -like '*changeme*')
    if (-not $shipped) { return }
    Set-EnvValue $Key (New-RandomKey)
    Write-Ok "generated $Key"
}

# An .env from an older clone has none of the settings added since, and a
# setting missing from the file is a setting nobody knows exists.
if (Test-Path .env.example) {
    $addedAny = $false
    foreach ($line in Get-Content .env.example) {
        if ($line -notmatch '^[A-Z][A-Z0-9_]*=') { continue }
        $key = $line.Split('=')[0]
        if (Select-String -Path .env -Pattern "^\s*$key=" -Quiet) { continue }
        if (-not $addedAny) {
            Add-Content -Path .env -Value '' -Encoding utf8
            Add-Content -Path .env -Value '# -- run.ps1: khoa moi co trong .env.example --' -Encoding utf8
            $addedAny = $true
        }
        Add-Content -Path .env -Value $line -Encoding utf8
        Write-Info "added $key"
    }
    if ($addedAny) {
        Write-Warn 'new settings were appended to .env; what they do is in .env.example'
    }
}

Set-AdminPasswordIfUnset
Set-SecretIfUnset 'SECRET_ENCRYPTION_KEY'
Set-SecretIfUnset 'JWT_SECRET'

$keyValue = Get-EnvValue 'SECRET_ENCRYPTION_KEY'
if ($keyValue.Length -ne 44) {
    Stop-WithError @"
SECRET_ENCRYPTION_KEY in .env is $($keyValue.Length) characters; it has to be 44
    (32 bytes, urlsafe base64). Nothing else would complain until the first
    Source is saved, so this stops here.
"@
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

$adminEmail = Get-EnvValue 'SEED_ADMIN_EMAIL'
if (-not $adminEmail) { $adminEmail = 'admin@appbi.local' }
Write-Host ''
Write-Info "web UI    http://localhost:$proxyPort"
Write-Info "API       http://localhost:$apiPort"
Write-Info "engine    $engineType"
Write-Info "sign in   $adminEmail"
if ($script:AdminPasswordGenerated) {
    # Printed once, on the run that created it, and never again -- and never
    # in the web UI. After this it lives in .env, which is gitignored.
    Write-Host ''
    Write-Info 'mat khau quan tri vua duoc sinh cho ban cai nay:'
    Write-Info "    $($script:AdminPasswordGenerated)"
    Write-Info 'luu lai ngay. Lan chay sau se khong in nua; no nam trong .env'
    Write-Info 'o khoa SEED_ADMIN_PASSWORD. Doi gia tri do roi chay lai run.ps1'
    Write-Info 'thi mat khau trong co so du lieu doi theo.'
} else {
    Write-Info 'mat khau   xem SEED_ADMIN_PASSWORD trong .env'
}
Write-Host ''
Write-Info '.\run.ps1 -Status      what is running'
Write-Info '.\run.ps1 -Logs api    follow a service'
Write-Info '.\run.ps1 -Stop        stop without losing anything'
Write-Host ''
