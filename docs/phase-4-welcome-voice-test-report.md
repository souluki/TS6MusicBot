# TS6MusicBot 阶段 4 测试文档：指定频道欢迎语音

## 1. 阶段目标

本阶段实现需求规格中的“新用户进入指定 TS 频道时播放自定义欢迎语音”：

- 网页端配置欢迎语音目标频道 ID。
- 网页端从已上传的内置语音中选择一条作为全局欢迎语音。
- 普通用户进入目标频道后可以触发欢迎语音，不需要发送指令。
- Bot 当前不在目标频道时跳过，不自动移动 Bot。
- 欢迎语音与正在播放的音乐共用阶段 3 的混音能力，音乐背景默认降低 12 dB。
- 只针对当前 TeamSpeak 连接；本阶段不扩展到其他平台。

## 2. 实现位置

| 模块 | 作用 |
|---|---|
| `backend/runtime_config.py` | 增加 `voice.welcome_channel_id` 网页配置项 |
| `backend/main.py` | 保存全局欢迎音频选择、判断频道、触发音频 |
| `backend/models.py` | 复用 `CustomVoice` 音频资源表，不增加额外音频文件表 |
| `voice-service/src/main.rs` | 从 TS Bookkeeping 事件识别新用户，并上报用户频道/Bot 频道 |
| `proto/voice.proto` | 增加 `UserJoinedEvent` 与订阅过滤开关 |
| `backend/voice_client.py` | 订阅用户加入事件 |
| `web/src/views/SettingsView.vue` | 配置频道 ID、上传音频并选择全局欢迎语音 |

## 3. 网页端使用方法

1. 启动后端和网页端，打开 Settings 的 TeamSpeak 配置。
2. 在“欢迎语音目标频道 ID”中填写数字频道 ID，例如 `123`。
3. 在“自定义内置语音”中填写指令和名称，选择 MP3/WAV/OGG 等音频上传。
4. 在音频列表点击“设为欢迎语音”。全局只保留一个选择。
5. 让一名普通用户进入目标频道。
6. 预期 Bot 在目标频道播放所选欢迎音频；如果 Bot 不在目标频道，则不播放，也不自动移动。

## 4. 自动化测试用法

在项目根目录执行：

```powershell
backend/.venv/Scripts/python.exe -m pytest -q tests/test_backend_regressions.py
```

执行 Voice 服务协议和 Rust 编译检查：

```powershell
backend/.venv/Scripts/python.exe -c "from backend.grpc_codegen import ensure_voice_stubs; ensure_voice_stubs()"
Set-Location voice-service
cargo check -q
Set-Location ..
```

执行网页构建检查：

```powershell
Set-Location web
npm run build
Set-Location ..
```

## 5. 自动化测试覆盖

- 只有“用户频道 == 目标频道 且 Bot 当前频道 == 目标频道”时才允许欢迎触发。
- 目标频道为空时不触发。
- 目标频道正确但 Bot 在其他频道时不查找音频、不播放、不移动 Bot。
- 目标频道正确且 Bot 在目标频道时，使用全局选定的 `CustomVoice` 音频。
- 欢迎音频复用普通内置语音的播放路径；音乐播放中走 `PlayOverlay`，空闲时走普通 `Play`。
- Voice gRPC 订阅接口能区分 `user_joined` 事件和聊天/播放/日志事件。

本阶段实际结果：

- Python 阶段回归测试：`49 passed, 4 warnings`。
- Rust Voice 服务：`cargo check -q` 通过；仅保留 vendor 依赖已有的 4 条 `unstable_name_collisions` 警告。
- Web 构建：`npm run build` 通过，Vite 完成 `1412 modules transformed`。
- `git diff --check`：通过；仅提示工作区文件的 LF/CRLF 自动转换信息，没有空白错误。

后续补强了全局 10 秒、单用户 5 分钟冷却以及 Bot 自身事件过滤；补强后全项目回归为 `52 passed, 4 warnings`。

## 6. 可复现的 TS 联调测试

### 正常播放

1. 在网页上传一段短音频并点击“设为欢迎语音”。
2. 填写真实目标频道 ID并保存配置。
3. 重启或等待 Voice 服务读取新配置。
4. 确认 Bot 当前位于目标频道。
5. 使用一个尚未在服务器可见的普通 TeamSpeak 客户端进入目标频道。
6. 观察 Voice 日志和 TS 频道音频，预期只播放一次欢迎语音。

### 跳过测试

1. 保持欢迎目标频道 ID和欢迎音频配置不变。
2. 将 Bot 留在其他频道，或让 Bot 离开目标频道。
3. 让普通用户进入目标频道。
4. 预期不播放欢迎语音，Bot 不自动移动。

### 混音测试

1. 先播放一首音乐。
2. 在 Bot 所在的目标频道让新用户加入。
3. 预期音乐不中断，欢迎语音叠加播放，音乐背景明显降低后恢复。

## 7. 本阶段限制

- 当前通过 TS 客户端库的“新客户端可见”事件触发；如果 Bot 没有权限看到目标频道中的用户事件，欢迎语音也会跳过。
- 欢迎音频为全局单选，不支持按频道配置多套音频。
- 当前只支持频道 ID，不支持从网页下拉搜索频道名称。
- 自动化测试验证了判断和调用链；真实 TS 用户加入、权限和声卡播放仍需按第 6 节进行现场联调。
