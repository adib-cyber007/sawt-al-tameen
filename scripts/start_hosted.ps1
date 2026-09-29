# Start the hosted backend and ngrok independently of this PowerShell window.
# From the repository root: powershell -File scripts/start_hosted.ps1
param([int]$WaitSeconds = 120)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$hostedDir = Join-Path $projectRoot '.hosted'
$candidates = @(
    (Join-Path $projectRoot '.testenv\Scripts\python.exe'),
    (Join-Path $projectRoot '.venv\Scripts\python.exe')
)
$python = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) {
    throw 'No project Python environment found. Create .venv and install the project dependencies first.'
}

$domainLine = Get-Content -LiteralPath (Join-Path $projectRoot '.env') |
    Where-Object { $_ -match '^\s*NGROK_STATIC_DOMAIN\s*=' } | Select-Object -First 1
if (-not $domainLine) { throw 'NGROK_STATIC_DOMAIN is missing from .env.' }
$domain = ((($domainLine -split '=', 2)[1] -split '\s+#', 2)[0]).Trim().Trim('"', "'", '/')
$domain = $domain -replace '^https?://', ''
if ($domain -notmatch '^[a-zA-Z0-9.-]+$') { throw 'NGROK_STATIC_DOMAIN must be a bare hostname.' }
$healthUrl = "https://$domain/health"

function Test-PublicHealth {
    try {
        # ngrok's free-domain interstitial returns HTTP 200 with HTML to PowerShell clients.
        $reply = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 5 `
            -Headers @{ 'ngrok-skip-browser-warning' = 'true' }
        return $reply.status -eq 'ok'
    } catch { return $false }
}

if (Test-PublicHealth) {
    Write-Output "Already available: https://$domain/voice"
    exit 0
}

$portLine = Get-Content -LiteralPath (Join-Path $projectRoot '.env') |
    Where-Object { $_ -match '^\s*PREAUTH_HOSTED_PORT\s*=' } | Select-Object -First 1
$port = if ($portLine) { [int](((($portLine -split '=', 2)[1] -split '\s+#', 2)[0]).Trim().Trim('"', "'")) } else { 8000 }
$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    throw "Port $port is already in use but the public URL is unavailable. Check the existing backend before starting another copy."
}

New-Item -ItemType Directory -Path $hostedDir -Force | Out-Null
$stdoutPath = Join-Path $hostedDir 'launcher.stdout.log'
$stderrPath = Join-Path $hostedDir 'launcher.stderr.log'
$process = Start-Process -FilePath $python -ArgumentList 'scripts/hosted.py' `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    $process.Refresh()
    if ($process.HasExited) {
        $tail = Get-Content -LiteralPath $stderrPath -Tail 12 -ErrorAction SilentlyContinue
        throw "Hosted launcher exited (code $($process.ExitCode)). $($tail -join [Environment]::NewLine)"
    }
    $completed = (Test-Path -LiteralPath $stdoutPath -PathType Leaf) -and
        (Select-String -LiteralPath $stdoutPath -Pattern 'Running. Press Ctrl+C' -SimpleMatch -Quiet)
    if ($completed -and (Test-PublicHealth)) {
        Write-Output "Live: https://$domain/voice (launcher PID $($process.Id))"
        Write-Output "Logs: $stdoutPath"
        exit 0
    }
    Start-Sleep -Seconds 2
}

throw "Startup did not complete within $WaitSeconds seconds. Check $stdoutPath and $stderrPath."
