param(
    [ValidateSet('ask', 'hachimi', 'normal')]
    [string]$Mode = 'ask',
    [int]$PromptTimeoutSeconds = 15,
    [switch]$NoConsoleLoop,
    [switch]$RebuildVoice,
    [switch]$SkipWeb
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$repoRoot = $PSScriptRoot
. (Join-Path $repoRoot 'scripts/Import-TsbotEnv.ps1')

$logsDir = Join-Path $repoRoot 'logs'
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
}
$pidFile = Join-Path $logsDir 'launcher-pids.json'

function Test-TcpPortOpen {
    param(
        [string]$HostName = '127.0.0.1',
        [int]$Port,
        [int]$TimeoutMs = 400
    )
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) {
            return $false
        }
        $client.EndConnect($iar)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Find-FFmpegPath {
    $candidates = @(
        $env:TSBOT_FFMPEG,
        'ffmpeg',
        $(if ($env:ProgramFiles) { Join-Path $env:ProgramFiles 'ffmpeg\bin\ffmpeg.exe' }),
        $(if ($env:LocalAppData) { Join-Path $env:LocalAppData 'Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-*\bin\ffmpeg.exe' }),
        'C:\msys64\mingw64\bin\ffmpeg.exe'
    )
    foreach ($c in $candidates) {
        if (-not $c) { continue }
        if ($c -eq 'ffmpeg') {
            $cmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
            if ($cmd) { return $cmd.Source }
            continue
        }
        $matched = @(Get-ChildItem -Path $c -File -ErrorAction SilentlyContinue)
        if ($matched.Count -gt 0) {
            return $matched[0].FullName
        }
    }
    return $null
}

function Ensure-HachimiBackendDeps {
    $hachimiHome = if ($env:TSBOT_HACHIMI_HOME) { $env:TSBOT_HACHIMI_HOME.Trim() } else { 'D:\ai\gemini' }
    $pgOk = Test-TcpPortOpen -HostName '127.0.0.1' -Port 5432 -TimeoutMs 400
    $embOk = Test-TcpPortOpen -HostName '127.0.0.1' -Port 8766 -TimeoutMs 400
    if ($pgOk -and $embOk) {
        return
    }
    $startDeps = Join-Path $hachimiHome 'scripts\start-deps.ps1'
    if (Test-Path $startDeps) {
        Write-Host "检测到哈基咪记忆库/向量服务未完全启动，正在自动拉起 ($startDeps) ..." -ForegroundColor Cyan
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $startDeps
    }
}

function Set-BotMode {
    param(
        [bool]$EnableHachimi,
        [string]$BackendBaseUrl
    )
    if ($EnableHachimi) {
        Ensure-HachimiBackendDeps
    }
    $body = @{ enabled = $EnableHachimi } | ConvertTo-Json -Compress
    $res = Invoke-RestMethod -Uri "$BackendBaseUrl/hachimi/mode" -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 8
    return $res
}

function Show-ModeBanner {
    param($StatusObj)
    if ($StatusObj.enabled) {
        $pgText = if ($StatusObj.pg_connected) { '已连接共享记忆池' } else { '降级运行（记忆池未连接）' }
        $reasonText = if ($StatusObj.mood_reason) { " | 原因: $($StatusObj.mood_reason)" } else { '' }
        Write-Host ""
        Write-Host "============================================================" -ForegroundColor Magenta
        Write-Host " 当前模式：【哈基咪大脑】陪玩 + 点歌模式 已开启！" -ForegroundColor Magenta
        Write-Host "   记忆池: $pgText | 指令条数: $($StatusObj.directives_count)" -ForegroundColor White
        Write-Host "   实时心情: mood=$($StatusObj.mood) irritation=$($StatusObj.irritation)$reasonText" -ForegroundColor White
        Write-Host "   作息状态: $($StatusObj.energy_state)" -ForegroundColor White
        Write-Host "   用法提示: 频道里直接开麦喊「哈基咪/小咪」或在聊天框自然说话点歌" -ForegroundColor Gray
        Write-Host "============================================================" -ForegroundColor Magenta
    }
    else {
        Write-Host ""
        Write-Host "============================================================" -ForegroundColor Cyan
        Write-Host " 当前模式：【普通 TS6 音乐机器人】模式（纯净原版，未接入哈基咪大脑）" -ForegroundColor Cyan
        Write-Host "   用法提示: 支持原版 Web 面板点歌及 !play / 播放 / 切歌 / 音量 等指令" -ForegroundColor Gray
        Write-Host "============================================================" -ForegroundColor Cyan
    }
}

Write-Host ""
Write-Host ">>> 正在准备启动 TS6 Music Bot ..." -ForegroundColor Cyan

# 1. Clean up any previous TSBot instance first
& (Join-Path $repoRoot 'stop-tsbot.ps1') -Quiet

$grpcAddr = if ($env:TSBOT_VOICE_GRPC_ADDR) { $env:TSBOT_VOICE_GRPC_ADDR.Trim() } else { '127.0.0.1:50051' }
$voicePort = if ($grpcAddr -match ':(\d+)$') { [int]$Matches[1] } else { 50051 }
$backendHost = if ($env:TSBOT_HOST) { ($env:TSBOT_HOST.Trim() -replace '^https?://', '' -replace '/.*$', '' -replace ':.+$', '') } else { '127.0.0.1' }
$backendPort = if ($env:TSBOT_PORT) { [int]($env:TSBOT_PORT.Trim().TrimStart(':')) } else { 8009 }
$webHost = if ($env:TSBOT_WEB_HOST) { ($env:TSBOT_WEB_HOST.Trim() -replace '^https?://', '' -replace '/.*$', '' -replace ':.+$', '') } else { '127.0.0.1' }
$webPort = if ($env:TSBOT_WEB_PORT) { [int]($env:TSBOT_WEB_PORT.Trim().TrimStart(':')) } else { 8080 }
$backendUrl = "http://127.0.0.1:$backendPort"

# 2. Check / prepare Voice Service binary
$voiceExe = Join-Path $repoRoot 'voice-service\target\x86_64-pc-windows-gnu\release\voice-service.exe'
if ($RebuildVoice -or -not (Test-Path $voiceExe)) {
    Write-Host "[1/3] 未找到预编译 voice-service.exe（或指定了重编译），正在构建 Rust 语音服务..." -ForegroundColor Yellow
    $mingwBin = if (Test-Path 'C:\msys64\mingw64\bin\gcc.exe') { 'C:\msys64\mingw64\bin' } else { 'C:\mingw64\bin' }
    $cmakeExe = if (Test-Path 'C:\Cmake\bin\cmake.exe') { 'C:\Cmake\bin\cmake.exe' } else { Join-Path $mingwBin 'cmake.exe' }
    $env:CMAKE = $cmakeExe
    $env:CC = Join-Path $mingwBin 'gcc.exe'
    $env:CXX = Join-Path $mingwBin 'g++.exe'
    $env:CMAKE_GENERATOR = 'MinGW Makefiles'
    $env:CMAKE_MAKE_PROGRAM = Join-Path $mingwBin 'mingw32-make.exe'
    $env:CMAKE_SH = 'CMAKE_SH-NOTFOUND'
    $env:PATH = "C:\Users\10159\.cargo\bin;$mingwBin;$(Split-Path $cmakeExe);$env:PATH"
    Push-Location (Join-Path $repoRoot 'voice-service')
    try {
        & cargo build --target x86_64-pc-windows-gnu --release
        if ($LASTEXITCODE -ne 0) { throw "voice-service 编译失败 (exit $LASTEXITCODE)" }
    }
    finally {
        Pop-Location
    }
}

$ffmpeg = Find-FFmpegPath
if (-not $ffmpeg) {
    throw '未找到 ffmpeg.exe，请先安装 FFmpeg 或在 tsbot.env 中配置 TSBOT_FFMPEG。'
}

$python = Join-Path $repoRoot 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw "未找到 Python 虚拟环境：$python"
}

# 3. Start Voice Service
Write-Host "[1/3] 启动 Voice 语音服务 ($grpcAddr) ..." -ForegroundColor Cyan
$env:TSBOT_FFMPEG = $ffmpeg
$env:TSBOT_TS3_IDENTITY_FILE = Join-Path $repoRoot 'logs\identity.json'
$env:TSBOT_TS3_AVATAR_DIR = Join-Path $repoRoot 'logs'
$mingwRuntime = 'C:\msys64\mingw64\bin'
if (Test-Path $mingwRuntime) {
    $env:PATH = "$mingwRuntime;$(Split-Path $ffmpeg);$env:PATH"
}
else {
    $env:PATH = "$(Split-Path $ffmpeg);$env:PATH"
}

$voiceOutLog = Join-Path $logsDir 'launcher-voice.out.log'
$voiceErrLog = Join-Path $logsDir 'launcher-voice.err.log'
$voiceProc = Start-Process -FilePath $voiceExe -ArgumentList @($grpcAddr) -WorkingDirectory $repoRoot -WindowStyle Hidden -RedirectStandardOutput $voiceOutLog -RedirectStandardError $voiceErrLog -PassThru

$deadline = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $deadline) {
    if ($voiceProc.HasExited) {
        throw "voice-service 启动后异常退出，请查看日志: $voiceErrLog"
    }
    if (Test-TcpPortOpen -HostName '127.0.0.1' -Port $voicePort) { break }
    Start-Sleep -Milliseconds 250
}
if (-not (Test-TcpPortOpen -HostName '127.0.0.1' -Port $voicePort)) {
    throw "等待 voice-service 监听端口 $voicePort 超时，请检查 $voiceErrLog"
}

# 4. Start Backend (always boot cleanly in Normal TS6 Bot mode first!)
Write-Host "[2/3] 启动 Backend 后端服务 ($backendHost`:$backendPort，初始默认：普通 TS6 BOT) ..." -ForegroundColor Cyan
$env:TSBOT_HACHIMI_BOOT_DEFAULT = 'off'
$env:PYTHONIOENCODING = 'utf-8'
$backendOutLog = Join-Path $logsDir 'launcher-backend.out.log'
$backendErrLog = Join-Path $logsDir 'launcher-backend.err.log'
$backendProc = Start-Process -FilePath $python -ArgumentList @('-m', 'uvicorn', 'backend.main:app', '--host', $backendHost, '--port', "$backendPort") -WorkingDirectory $repoRoot -WindowStyle Hidden -RedirectStandardOutput $backendOutLog -RedirectStandardError $backendErrLog -PassThru

$backendReady = $false
$deadline = (Get-Date).AddSeconds(25)
while ((Get-Date) -lt $deadline) {
    if ($backendProc.HasExited) {
        throw "Backend 启动后异常退出，请查看日志: $backendErrLog"
    }
    try {
        $null = Invoke-RestMethod -Uri "$backendUrl/config/public" -Method Get -TimeoutSec 2
        $backendReady = $true
        break
    }
    catch {
        Start-Sleep -Milliseconds 350
    }
}
if (-not $backendReady) {
    throw "等待 Backend HTTP 就绪超时，请查看日志: $backendErrLog"
}

# 5. Start Web UI Preview
$webProc = $null
if (-not $SkipWeb) {
    $viteJs = Join-Path $repoRoot 'web\node_modules\vite\bin\vite.js'
    $nodeCmd = Get-Command node.exe -ErrorAction SilentlyContinue
    $distHtml = Join-Path $repoRoot 'web\dist\index.html'
    if ($nodeCmd -and (Test-Path $viteJs)) {
        if (-not (Test-Path $distHtml)) {
            Write-Host "[3/3] 首次构建 Web 前端资源..." -ForegroundColor Yellow
            Push-Location (Join-Path $repoRoot 'web')
            try {
                & npm.cmd run build
            }
            finally {
                Pop-Location
            }
        }
        $actualWebPort = $webPort
        while (Test-TcpPortOpen -HostName '127.0.0.1' -Port $actualWebPort -TimeoutMs 250) {
            $actualWebPort++
        }
        if ($actualWebPort -ne $webPort) {
            Write-Host "[提示] 端口 $webPort 已被其他程序占用，Web 管理面板自动改用端口 $actualWebPort" -ForegroundColor Yellow
            $webPort = $actualWebPort
        }
        Write-Host "[3/3] 启动 Web 管理面板 (http://$webHost`:$webPort) ..." -ForegroundColor Cyan
        $webOutLog = Join-Path $logsDir 'launcher-web.out.log'
        $webErrLog = Join-Path $logsDir 'launcher-web.err.log'
        $webProc = Start-Process -FilePath $nodeCmd.Source -ArgumentList @($viteJs, 'preview', '--host', $webHost, '--port', "$webPort", '--strictPort') -WorkingDirectory (Join-Path $repoRoot 'web') -WindowStyle Hidden -RedirectStandardOutput $webOutLog -RedirectStandardError $webErrLog -PassThru

        $webDeadline = (Get-Date).AddSeconds(12)
        while ((Get-Date) -lt $webDeadline) {
            if (Test-TcpPortOpen -HostName '127.0.0.1' -Port $webPort) { break }
            Start-Sleep -Milliseconds 250
        }
    }
}

# Save PIDs
$pidPayload = @{
    voice_pid   = $voiceProc.Id
    backend_pid = $backendProc.Id
    web_pid     = if ($webProc) { $webProc.Id } else { 0 }
    started_at  = (Get-Date).ToString('o')
}
$pidPayload | ConvertTo-Json | Set-Content -Path $pidFile -Encoding UTF8

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  TS6 Music Bot 全部服务启动成功！" -ForegroundColor Green
Write-Host "------------------------------------------------------------" -ForegroundColor DarkGray
Write-Host "  - Voice gRPC : $grpcAddr (PID $($voiceProc.Id))"
Write-Host "  - Backend API: http://$backendHost`:$backendPort (PID $($backendProc.Id))"
if ($webProc) {
    Write-Host "  - Web 控制台 : http://$webHost`:$webPort (PID $($webProc.Id))"
}
Write-Host "============================================================" -ForegroundColor Green

# 6. Post-startup mode selection: Hachimi Brain vs Normal TS6 Bot
$enableHachimi = $false
if ($Mode -eq 'hachimi') {
    $enableHachimi = $true
}
elseif ($Mode -eq 'normal') {
    $enableHachimi = $false
}
else {
    Write-Host ""
    Write-Host "请选择本次运行模式（如果不选择，默认保持普通 TS6 BOT）：" -ForegroundColor Yellow
    Write-Host "  [1 / Y] 启用【哈基咪大脑】（语音+文字陪玩 / 共享QQ情绪与记忆 / 少女音TTS）" -ForegroundColor Magenta
    Write-Host "  [2 / N / 回车] 保持【普通 TS6 音乐机器人】（默认）" -ForegroundColor Cyan

    $selected = ''
    try {
        if (-not [Console]::IsInputRedirected) {
            $waitEnd = (Get-Date).AddSeconds([Math]::Max(1, $PromptTimeoutSeconds))
            while ((Get-Date) -lt $waitEnd) {
                $remain = [Math]::Max(0, [int][Math]::Ceiling(($waitEnd - (Get-Date)).TotalSeconds))
                Write-Host "`r请在 $remain 秒内按键选择 [1=哈基咪大脑 / 2=普通TS6Bot]: " -NoNewline -ForegroundColor Yellow
                if ([Console]::KeyAvailable) {
                    $keyInfo = [Console]::ReadKey($true)
                    $ch = $keyInfo.KeyChar.ToString().Trim().ToLowerInvariant()
                    Write-Host $keyInfo.KeyChar
                    if ($ch -in @('1', 'y')) {
                        $selected = 'hachimi'
                    }
                    else {
                        $selected = 'normal'
                    }
                    break
                }
                Start-Sleep -Milliseconds 200
            }
            if (-not $selected) {
                Write-Host " (超时未选择 -> 默认普通 TS6 BOT)" -ForegroundColor Gray
            }
        }
    }
    catch {
        $selected = 'normal'
    }
    $enableHachimi = ($selected -eq 'hachimi')
}

$curStatus = Set-BotMode -EnableHachimi $enableHachimi -BackendBaseUrl $backendUrl
Show-ModeBanner -StatusObj $curStatus

if ($NoConsoleLoop) {
    exit 0
}

Write-Host ""
Write-Host "快捷键控制台已就绪（关闭窗口服务仍会在后台运行，按 Q 可一键安全停止）：" -ForegroundColor Gray
Write-Host "  [H] 随时热切换：哈基咪大脑 <-> 普通 TS6 BOT"
Write-Host "  [S] 查看实时运行状态与哈基咪心情"
Write-Host "  [O] 在浏览器打开 Web 管理面板"
Write-Host "  [Q] 一键停止全部 TS6 Bot 服务并退出"
Write-Host ""

try {
    while ($true) {
        if ($voiceProc.HasExited -or $backendProc.HasExited) {
            Write-Host "[警告] 检测到核心服务已退出，请检查 logs/launcher-*.err.log" -ForegroundColor Red
            break
        }
        if (-not [Console]::IsInputRedirected -and [Console]::KeyAvailable) {
            $k = [Console]::ReadKey($true).KeyChar.ToString().ToLowerInvariant()
            switch ($k) {
                'h' {
                    $st = Invoke-RestMethod -Uri "$backendUrl/hachimi/status" -Method Get -TimeoutSec 4
                    $nextMode = -not [bool]$st.enabled
                    $updated = Set-BotMode -EnableHachimi $nextMode -BackendBaseUrl $backendUrl
                    Show-ModeBanner -StatusObj $updated
                }
                's' {
                    $st = Invoke-RestMethod -Uri "$backendUrl/hachimi/status" -Method Get -TimeoutSec 4
                    Show-ModeBanner -StatusObj $st
                }
                'o' {
                    Start-Process "http://$webHost`:$webPort"
                }
                'q' {
                    & (Join-Path $repoRoot 'stop-tsbot.ps1')
                    break
                }
            }
        }
        Start-Sleep -Milliseconds 300
    }
}
catch {
    Write-Host "控制台退出: $_" -ForegroundColor Yellow
}
