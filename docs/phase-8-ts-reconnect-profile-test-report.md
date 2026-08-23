# 阶段 8 测试文档：TeamSpeak 重连后恢复动态身份

## 1. 阶段目标

补齐需求规格说明书中“TeamSpeak 连接重连后恢复头像、简介和当前播放身份”的边界：

- voice-service 重新连接 TeamSpeak 成功后发送 `ts3 connected` 日志事件。
- backend 订阅 voice-service 日志事件，识别连接成功事件。
- backend 重新读取当前播放项，按网页开关恢复网易云动态歌曲昵称、专辑头像和客户端简介。
- 不把断开、播放日志或其他普通日志误判为重连成功。
- 恢复任务采用已有的 generation、防抖、图片 MD5 去重和错误隔离机制，不阻塞播放。

## 2. 实现位置

| 模块 | 文件 | 作用 |
|---|---|---|
| backend 事件处理 | `backend/main.py` | 新增 `_handle_voice_log_event`，并让事件订阅包含 `include_log=True` |
| 动态身份恢复 | `backend/main.py` | 收到 `ts3 connected` 后调用 `_refresh_ts_profile_from_current()` |
| voice-service | `voice-service/src/main.rs` | 复用已有的 TeamSpeak 连接成功日志事件 |
| 自动化测试 | `tests/test_backend_regressions.py` | 验证连接成功触发恢复，其他日志不触发 |

## 3. 使用方法

### 3.1 自动化测试

在项目根目录执行：

```powershell
cd D:\workspace\codex\TS6MusicBot
backend\.venv\Scripts\python.exe -m pytest -q
```

只运行本阶段和动态身份相关测试：

```powershell
backend\.venv\Scripts\python.exe -m pytest -q tests\test_backend_regressions.py -k "DynamicNeteaseProfile or reconnect"
```

### 3.2 真实环境复现

1. 在 Web 设置中启用需要验证的“网易云动态专辑头像”“网易云动态歌曲昵称”和“网易云动态客户端简介”。
2. 启动 backend、voice-service 和 TeamSpeak 服务器，播放一首网易云歌曲。
3. 记录当前头像、昵称和客户端简介。
4. 让 voice-service 与 TeamSpeak 的连接短暂断开，再恢复连接。
5. 观察重连后的 Bot 身份：头像、昵称和客户端简介应恢复为当前歌曲对应内容。
6. 在日志中确认出现连接成功事件；不应因普通播放日志重复触发完整身份刷新。
7. 切歌、暂停或关闭某个网页开关后重复断线重连，确认每个开关仍独立生效，音乐播放和歌词任务不受影响。

真实联调需要本机可访问 TeamSpeak `9987` 端口和 voice gRPC `50051` 端口；不要在测试文档或聊天消息中记录密码、Cookie、Token 或 Identity。

## 4. 测试用例与结果

| 编号 | 测试内容 | 预期结果 | 实际结果 |
|---|---|---|---|
| R8-01 | `ts3 connected` 日志 | 调用当前动态身份刷新 | 通过 |
| R8-02 | `ts3 disconnected` 日志 | 不触发身份刷新 | 通过 |
| R8-03 | 其他 voice 日志 | 不触发身份刷新 | 通过 |
| R8-04 | 动态昵称开关 | 重连刷新遵守昵称开关 | 通过，复用已有测试 |
| R8-05 | 动态头像开关和 MD5 去重 | 重连不重复上传同一头像 | 通过，复用已有实现和测试 |
| R8-06 | 客户端简介开关 | 重连刷新遵守简介开关 | 通过，复用已有实现和测试 |
| R8-07 | Python 完整回归 | 所有既有功能保持通过 | 69 passed |
| R8-08 | 前端生产构建 | Web 可正常构建 | 通过 |
| R8-09 | voice-service Rust 测试 | 协议与服务测试通过 | 2 passed, 0 failed |

本次实际执行结果：

```text
69 passed, 4 warnings in 5.39s
```

前端构建：

```text
1412 modules transformed
✓ built in 1.67s
```

Rust：

```text
2 passed, 0 failed
```

4 个 Python warning 为项目已有的 FastAPI `on_event` 弃用提示；Rust warning 为 vendor `tsproto` 的既有 `unstable_name_collisions` 提示，本阶段未新增编译错误。

## 5. 本轮现场探测结果

本轮仅执行只读端口探测：

```powershell
foreach ($port in 50051,9987,8080,8081) {
  Test-NetConnection -ComputerName 127.0.0.1 -Port $port -WarningAction SilentlyContinue -InformationLevel Quiet
}
```

结果：

- `50051` Voice gRPC：未监听。
- `9987` TeamSpeak：未监听。
- `8081`：未监听。
- `8080`：有响应，但 `/voice/status` 等当前后端 API 返回 404，不能作为本项目后端验收依据。

因此本阶段“重连后恢复身份”的真实 TeamSpeak 听感和客户端显示验收尚未执行；自动化测试通过不等于现场联调通过。待服务启动后按第 3.2 节复现，并把现场结果追加到本文件。

## 6. 已知限制

- 当前实现恢复的是动态头像、动态歌曲昵称和客户端简介；播放流本身是否能在 TeamSpeak 短暂断线后自动继续，仍取决于 voice-service 的实际音频重连状态。
- 如果重连后当前播放项已经被切换，generation 校验会阻止旧身份覆盖新歌曲。
- 如果图片下载、头像上传或客户端更新失败，保留已有身份，不阻塞音乐和歌词任务。
