param(
    [switch]$Quiet
)

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$repoRoot = $PSScriptRoot
. (Join-Path $repoRoot 'scripts/Import-TsbotEnv.ps1')

$pidFile = Join-Path $repoRoot 'logs/launcher-pids.json'
$stoppedCount = 0

function Stop-ByPidSafe {
    param(
        [int]$TargetPid,
        [string]$Label
    )
    if ($TargetPid -le 0) {
        return $false
    }
    $proc = Get-Process -Id $TargetPid -ErrorAction SilentlyContinue
    if (-not $proc) {
        return $false
    }
    try {
        $null = & taskkill.exe /PID $TargetPid /T /F 2>$null
        Stop-Process -Id $TargetPid -Force -ErrorAction SilentlyContinue
        if (-not $Quiet) {
            Write-Host "[已停止] $Label (PID $TargetPid)" -ForegroundColor Yellow
        }
        return $true
    }
    catch {
        return $false
    }
}

if (Test-Path $pidFile) {
    try {
        $saved = Get-Content -Path $pidFile -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($prop in @('web_pid', 'backend_pid', 'voice_pid')) {
            $val = $saved.$prop
            if ($val) {
                if (Stop-ByPidSafe -TargetPid ([int]$val) -Label $prop) {
                    $stoppedCount++
                }
            }
        }
    }
    catch {}
    Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
}

# Fallback: clean up any lingering processes bound to TSBot ports whose path/cmdline belongs to this repo
$grpcAddr = if ($env:TSBOT_VOICE_GRPC_ADDR) { $env:TSBOT_VOICE_GRPC_ADDR.Trim() } else { '127.0.0.1:50051' }
$voicePort = if ($grpcAddr -match ':(\d+)$') { [int]$Matches[1] } else { 50051 }
$backendPort = if ($env:TSBOT_PORT) { [int]($env:TSBOT_PORT.Trim().TrimStart(':')) } else { 8009 }
$webPort = if ($env:TSBOT_WEB_PORT) { [int]($env:TSBOT_WEB_PORT.Trim().TrimStart(':')) } else { 8080 }

$repoNorm = $repoRoot.ToLowerInvariant().Replace('/', '\')
foreach ($port in @($webPort, $backendPort, $voicePort)) {
    $conns = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    foreach ($c in $conns) {
        $procId = [int]$c.OwningProcess
        if ($procId -le 4) { continue }
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $procId" -ErrorAction SilentlyContinue
        $cmdLine = if ($cim -and $cim.CommandLine) { $cim.CommandLine.ToLowerInvariant().Replace('/', '\') } else { '' }
        $exePath = if ($cim -and $cim.ExecutablePath) { $cim.ExecutablePath.ToLowerInvariant().Replace('/', '\') } else { '' }
        if ($cmdLine.Contains($repoNorm) -or $exePath.Contains($repoNorm) -or $cmdLine.Contains('backend.main:app') -or $cmdLine.Contains('vite') -or $exePath.EndsWith('voice-service.exe')) {
            if (Stop-ByPidSafe -TargetPid $procId -Label "Port :$port") {
                $stoppedCount++
            }
        }
    }
}

if (-not $Quiet) {
    if ($stoppedCount -gt 0) {
        Write-Host "TS6 Music Bot 相关服务已全部停止（共清理 $stoppedCount 个进程）。" -ForegroundColor Green
    }
    else {
        Write-Host "当前没有正在运行的 TS6 Music Bot 服务。" -ForegroundColor Gray
    }
}
