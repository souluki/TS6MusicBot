# Phase 9：全功能自测报告

## 1. 测试范围

- Python 后端单元测试
- Rust 语音服务单元测试
- 前端生产构建
- 后端 HTTP 接口
- 前端 HTTP 页面
- 语音服务连接、单首歌曲播放、动态头像、动态昵称和 TS 文字歌词

## 2. 自动化测试结果

### 后端

```text
Ran 74 tests
OK
```

修正了一项测试隔离问题：动态昵称恢复测试不再依赖真实数据库中的昵称配置。

### Rust 语音服务

```text
running 3 tests
test result: ok. 3 passed; 0 failed
```

### 前端

```text
vite build
✓ 1412 modules transformed
```

### Python 与代码检查

- `compileall` 通过。
- `git diff --check` 无实际空白错误。

## 3. 本地服务检查

后端和前端曾成功启动：

- 前端 `http://127.0.0.1:5173/` 返回 HTTP 200。
- 后端 `http://127.0.0.1:8009/docs` 返回 HTTP 200。
- `/queue` 返回空队列，未产生测试歌曲堆积。

## 4. TS 实机测试结果

语音服务编译成功，但 Windows 在启动可执行文件时仍返回：

```text
应用程序控制策略已阻止此文件
voice-service.exe
```

因此本阶段无法完成以下实机验证：

- 连接 TeamSpeak
- 播放单首歌曲
- 动态昵称
- 动态头像
- TS 当前文字频道歌词

这不是 Rust 编译错误，也不是 TS 服务器返回的错误；是 Windows 应用程序控制策略仍在阻止新生成的可执行文件。后端在语音服务未启动时正确返回 `voice_connected: false`，并已停止，避免持续重试。

## 5. 后续复测方法

Windows 安全策略关闭或重启系统后，在项目根目录执行：

```powershell
$env:TSBOT_VOICE_PROFILE='debug'
.\run-voicemake.ps1
```

看到 `voice-service listening on 127.0.0.1:50051` 和 `ts3 connected` 后，再按启动指南启动后端和前端，只播放一首网易云歌曲进行验证。
