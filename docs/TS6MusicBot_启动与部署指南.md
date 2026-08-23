# TS6MusicBot 启动与部署指南

本文中的命令都以项目根目录为当前目录，使用相对路径，不依赖某一台电脑的盘符或用户名。

## 一、首次部署准备

先在 VS Code 或 PowerShell 打开 `TS6MusicBot` 项目根目录，然后执行：

```powershell
Copy-Item .\tsbot.env.example .\tsbot.env
```

编辑 `tsbot.env`，至少确认以下内容：

```text
TSBOT_TS3_HOST=你的 TeamSpeak 服务器地址
TSBOT_TS3_PORT=9987
TSBOT_FFMPEG=ffmpeg
TSBOT_VOICE_GRPC_ADDR=127.0.0.1:50051
```

服务器上需要提前安装并加入 PATH：

- Python 3
- Node.js/npm
- Rust/cargo
- CMake
- MinGW-w64（Windows 编译语音服务需要）
- FFmpeg

安装项目依赖（只需首次执行）：

```powershell
py -3 -m venv .\backend\.venv
.\backend\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
npm.cmd --prefix .\web install
```

## 二、Windows 本地测试启动顺序

需要打开三个 PowerShell 窗口，并确保每个窗口都位于项目根目录。

### 窗口 1：启动语音服务和 TS Bot

```powershell
.\run-voicemake.ps1
```

这个脚本会自动构建并启动 `voice-service`，连接 `tsbot.env` 中配置的 TeamSpeak 服务器。

### 窗口 2：启动后端

```powershell
.\run-backend.ps1
```

后端默认地址：`http://127.0.0.1:8009`。

### 窗口 3：启动前端开发服务器

```powershell
npm.cmd --prefix .\web run dev
```

前端默认地址：`http://127.0.0.1:5173`。

如果要模拟部署后的前端预览方式，使用下面的命令替代上一条：

```powershell
.\run-web.ps1
```

预览服务器默认使用 `TSBOT_WEB_PORT`，通常是 `8080`。

## 三、Linux 服务器启动顺序

在项目根目录执行一次：

```bash
chmod +x ./run-voicemake.sh ./run-backend.sh ./run-web.sh
```

然后分别打开三个终端，按以下顺序执行：

```bash
./run-voicemake.sh
```

```bash
./run-backend.sh
```

```bash
./run-web.sh
```

## 四、启动后检查

在新的 PowerShell 窗口执行：

```powershell
Invoke-RestMethod http://127.0.0.1:8009/voice/status | ConvertTo-Json
```

确认返回内容中的：

```text
"voice_connected": true
```

再打开：

```text
http://127.0.0.1:5173/settings
```

或使用预览模式时打开：

```text
http://127.0.0.1:8080/settings
```

## 五、测试顺序

1. 先确认 Bot 已进入目标 TS 频道。
2. 只搜索并播放一首网易云歌曲。
3. 确认 TS 语音频道能听到声音。
4. 确认 Bot 昵称变为歌曲名。
5. 确认头像变为专辑图。
6. 确认当前 TS 文字频道显示带时间戳同步的主歌词。
7. 每项测试之间间隔几秒，不要连续刷新或批量加入歌单，避免触发 TS Anti-Flood。

## 六、停止顺序

先在窗口 1 按 `Ctrl+C` 停止语音服务，再停止后端和前端：

```text
窗口 1：Ctrl+C
窗口 2：Ctrl+C
窗口 3：Ctrl+C
```

如果窗口已经关闭，可以使用项目自带的停止脚本（Linux/nohup 部署）：

```bash
./nohup-stop.sh
```

