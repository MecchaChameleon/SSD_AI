param([switch]$SmokeTest)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root ".python\python.exe"
$envFile = Join-Path $root ".env"
$toolsDir = Join-Path $root "tools"
$cloudflared = Join-Path $toolsDir "cloudflared.exe"
$runtimeDir = Join-Path $root ".runtime"
$aiOut = Join-Path $runtimeDir "ai.stdout.log"
$aiErr = Join-Path $runtimeDir "ai.stderr.log"
$tunnelOut = Join-Path $runtimeDir "cloudflared.stdout.log"
$tunnelErr = Join-Path $runtimeDir "cloudflared.stderr.log"

function Read-DotEnvValue([string]$name, [string]$path = $envFile) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $line = Get-Content -LiteralPath $path -Encoding UTF8 |
        Where-Object { $_ -match "^\s*$([regex]::Escape($name))\s*=" } |
        Select-Object -Last 1
    if (-not $line) { return $null }
    return (($line -split "=", 2)[1].Trim()).Trim('"').Trim("'")
}

function New-ServiceKey {
    $bytes = New-Object byte[] 48
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Wait-ForHealth {
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:9000/health" -TimeoutSec 2
            if ($health.status -eq "ok") { return $health }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "AI server did not become healthy on port 9000. Check: $aiErr"
}

function Find-TunnelUrl {
    for ($attempt = 1; $attempt -le 60; $attempt++) {
        foreach ($path in @($tunnelOut, $tunnelErr)) {
            if (Test-Path -LiteralPath $path) {
                $match = Select-String -LiteralPath $path -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -AllMatches |
                    Select-Object -Last 1
                if ($match) { return $match.Matches[-1].Value }
            }
        }
        Start-Sleep -Seconds 1
    }
    throw "Cloudflare Quick Tunnel URL was not found. Check: $tunnelErr"
}

if (-not (Test-Path -LiteralPath $python)) {
    throw ".python\python.exe was not found. Install the SSD_AI Python environment and requirements first."
}

New-Item -ItemType Directory -Force -Path $toolsDir, $runtimeDir | Out-Null
Remove-Item -LiteralPath $aiOut, $aiErr, $tunnelOut, $tunnelErr -Force -ErrorAction SilentlyContinue

$serviceKey = Read-DotEnvValue "AI_SERVICE_API_KEY"
if ([string]::IsNullOrWhiteSpace($serviceKey)) {
    $serviceKey = New-ServiceKey
    if ((Test-Path -LiteralPath $envFile) -and (Get-Item -LiteralPath $envFile).Length -gt 0) {
        Add-Content -LiteralPath $envFile -Value "`r`nAI_SERVICE_API_KEY=$serviceKey" -Encoding UTF8
    } else {
        Set-Content -LiteralPath $envFile -Value "AI_SERVICE_API_KEY=$serviceKey" -Encoding UTF8
    }
    Write-Host "Created a new AI_SERVICE_API_KEY in .env." -ForegroundColor Yellow
}
$env:AI_SERVICE_API_KEY = $serviceKey

# 도로명 주소에도 읍면동 수요를 적용할 수 있도록 좌표 역지오코딩 키를 연결한다.
# 로컬 통합 실행에서는 프론트가 이미 사용하는 같은 카카오 REST 키를 재사용한다.
$kakaoKey = Read-DotEnvValue "KAKAO_REST_API_KEY"
if ([string]::IsNullOrWhiteSpace($kakaoKey)) {
    $frontendEnv = Join-Path (Split-Path -Parent $root) "SSD_FE\.env"
    $kakaoKey = Read-DotEnvValue "EXPO_PUBLIC_KAKAO_REST_API_KEY" $frontendEnv
}
if ([string]::IsNullOrWhiteSpace($kakaoKey)) {
    Write-Warning "KAKAO_REST_API_KEY is missing. Road-name addresses may use the Jeju-wide demand fallback."
} else {
    $env:KAKAO_REST_API_KEY = $kakaoKey
}

if (-not (Test-Path -LiteralPath $cloudflared)) {
    Write-Host "Downloading Cloudflared for first-time setup..." -ForegroundColor Cyan
    Invoke-WebRequest `
        -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" `
        -OutFile $cloudflared `
        -UseBasicParsing
}

$existingListeners = Get-NetTCPConnection -LocalPort 9000 -State Listen -ErrorAction SilentlyContinue
foreach ($listener in $existingListeners) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
    if ($process -and $process.Name -match '^python(w)?\.exe$' -and $process.CommandLine -match 'uvicorn\s+app\.main:app') {
        Write-Host "Stopping the existing AI server (PID $($listener.OwningProcess)) and loading the latest code." -ForegroundColor Yellow
        Stop-Process -Id $listener.OwningProcess -Force
        Start-Sleep -Milliseconds 500
    } else {
        throw "Port 9000 is used by another application. Close it and run this launcher again."
    }
}

Write-Host "Starting Localtime AI server..." -ForegroundColor Cyan
$ai = Start-Process `
    -FilePath $python `
    -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '9000') `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $aiOut `
    -RedirectStandardError $aiErr `
    -PassThru

try {
    $health = Wait-ForHealth
    Write-Host "AI server healthy: $($health.modelVersion)" -ForegroundColor Green

    Write-Host "Starting Cloudflare Quick Tunnel..." -ForegroundColor Cyan
    $tunnel = Start-Process `
        -FilePath $cloudflared `
        -ArgumentList @('tunnel', '--url', 'http://127.0.0.1:9000', '--no-autoupdate') `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $tunnelOut `
        -RedirectStandardError $tunnelErr `
        -PassThru

    try {
        $tunnelUrl = Find-TunnelUrl
        Write-Host ""
        Write-Host "============================================================" -ForegroundColor Green
        Write-Host "Copy these two values into Render Dashboard > Environment." -ForegroundColor Green
        Write-Host "AI_SERVICE_BASE_URL=$tunnelUrl" -ForegroundColor White
        Write-Host "AI_SERVICE_API_KEY=$serviceKey" -ForegroundColor White
        Write-Host "============================================================" -ForegroundColor Green
        Write-Host ""
        if ($SmokeTest) {
            Write-Host "Smoke test passed. Stopping the AI server and tunnel." -ForegroundColor Green
            return
        }
        Write-Host "Keep this window open. Press Ctrl+C to stop the AI connection." -ForegroundColor Yellow

        while (-not $ai.HasExited -and -not $tunnel.HasExited) {
            Start-Sleep -Seconds 2
        }
        if ($ai.HasExited) { throw "AI server exited. Check: $aiErr" }
        if ($tunnel.HasExited) { throw "Cloudflare Tunnel exited. Check: $tunnelErr" }
    } finally {
        if ($tunnel -and -not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -ErrorAction SilentlyContinue }
    }
} finally {
    if ($ai -and -not $ai.HasExited) { Stop-Process -Id $ai.Id -Force -ErrorAction SilentlyContinue }
}
