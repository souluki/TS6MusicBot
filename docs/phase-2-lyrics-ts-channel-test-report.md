# F4 阶段测试文档：主歌词时间戳同步到当前 TS 文字频道

## 1. 阶段范围

本阶段对应需求规格说明书中的 F4：只使用主歌词，不显示翻译和罗马音；有时间戳时按播放进度同步到当前 TS 文字频道；没有时间戳时整段歌词只发送一次。

已实现：

- 播放歌曲开始时自动加载当前队列项歌词。
- 网易云只读取 `lrc.lyric` 主歌词，不读取 `tlyric` 翻译歌词。
- 解析标准 LRC 时间戳，例如 `[01:23.45]歌词内容`。
- 以后台任务根据实际播放位置发送歌词，发送目标固定为 TS 当前频道（`target_mode=2`）。
- 歌曲暂停时保留当前歌词进度，恢复后继续按时间戳推进。
- 暂停或恢复的 Voice gRPC 调用失败时回滚 backend 播放时钟，避免歌词进度被错误冻结或推进。
- 拖动播放进度或切歌后，旧歌词任务不会继续向频道发送内容。
- 播放已经开始后歌词才返回时，从当前播放位置开始，不会把已经过去的歌词瞬间刷屏发送。
- seek 向前或向后都会重新定位到新播放位置对应的最近主歌词，避免发送旧游标内容。
- 相邻重复歌词不会重复发送；发送失败会短暂重试，避免一次瞬时 TS/gRPC 错误直接丢失当前歌词。
- 频道歌词消息默认带 `♪ ` 前缀，纯文本整段也只增加一次前缀。
- seek 回到与上一句文本相同的重复副歌时，会清除旧的文本去重状态，重新发送新位置对应的歌词。
- 没有任何时间戳但存在纯文本歌词时，整段主歌词一次发送。
- 仅有 LRC 元数据、翻译或罗马音标签而没有主歌词时静默跳过，不把标签内容当作主歌词发送。
- 单条 TS 消息最长 700 个字符，超出部分截断并追加省略号，避免超过现有 Bot 消息限制。
- 新增 `plain_lyrics` 响应字段，仅用于无时间戳歌词的后端回退；网页原有歌词显示不受影响。
- 网页 TeamSpeak 设置提供“当前频道歌词同步”开关，默认开启；关闭会取消当前歌词任务，重新开启从当前播放位置重新调度。

本阶段暂不包含：歌词翻译/罗马音、多语言选择、逐字卡拉 OK 和歌词历史消息清理。

## 2. 使用方法

### 2.1 TeamSpeak 使用

1. 启动 TS6MusicBot、voice-service，并确保 Bot 已进入目标语音频道。
2. 在 TS 文字频道播放一首网易云歌曲。
3. 观察当前 TS 文字频道：有时间戳歌词时，歌词会按照歌曲播放进度逐行发送。
4. 执行 `暂停` 后确认歌词不继续推进；执行 `恢复` 后继续推进。
5. 执行 `跳过` 或播放下一首后，上一首歌词不应继续发送。

对于无时间戳歌词，预期只收到一条包含整段主歌词的消息。

### 2.2 时间戳对齐验证

选择包含明显时间戳的歌曲，记录歌词行时间戳和 TS 消息到达时间。允许少量网络和 TS 转发延迟；后台调度以实际播放位置为准，每次最多等待 250 ms 后重新检查，因此不会把“任务启动时间”误当成歌曲开始时间。

### 2.3 后端歌词接口

原有接口仍可使用：

```text
GET /lyrics/{queue_item_id}
```

带时间戳时返回：

```json
{
  "lyrics": [
    {"time": 12.34, "text": "主歌词"}
  ],
  "plain_lyrics": ""
}
```

无时间戳时返回：

```json
{
  "lyrics": [],
  "plain_lyrics": "第一行\n第二行"
}
```

## 3. 自动化测试用法

在项目根目录执行 PowerShell：

```powershell
cd D:\workspace\codex\TS6MusicBot
backend\.venv\Scripts\python.exe -m pytest -q
```

只运行 F4 和歌单阶段相关测试：

```powershell
backend\.venv\Scripts\python.exe -m pytest -q tests/test_backend_regressions.py -k "lyrics or playlist"
```

验证前端生产构建：

```powershell
cd D:\workspace\codex\TS6MusicBot\web
npm run build
```

## 4. 测试用例与结果

| 编号 | 测试内容 | 预期结果 | 实际结果 |
|---|---|---|---|
| F4-01 | 解析标准 LRC 时间戳 | 得到按时间排序的主歌词行 | 通过 |
| F4-02 | LRC 同时包含主歌词和翻译标签 | 只保留主歌词 | 通过 |
| F4-03 | 纯文本、无时间戳歌词 | 解析为 `plain_lyrics`，不生成时间轴 | 通过 |
| F4-04 | 时间戳达到播放位置 | 发送当前歌词到 `target_mode=2` 当前频道 | 通过 |
| F4-05 | 时间戳尚未达到 | 不提前发送，继续等待播放位置 | 通过 |
| F4-06 | 歌词任务 generation 已过期 | 不发送旧歌曲歌词 | 通过 |
| F4-07 | 纯文本歌词发送 | 整段只调用一次频道消息发送 | 通过 |
| F4-08 | 超长纯文本歌词 | 限制为 700 字符以内并追加省略号 | 通过 |
| F4-09 | 原有歌单阶段回归 | 原有功能保持可用 | 通过 |
| F4-10 | 完整 Python 测试集 | 全部通过 | 73 passed |
| F4-11 | 前端生产构建 | Vite 构建成功 | 通过 |
| F4-12 | `_set_now_playing_queue_item` 播放入口 | 自动启动歌词任务，清空播放项时取消任务 | 通过 |
| F4-13 | seek 向前/向后 | 定位到新位置对应的最后一条主歌词，不发送更早历史歌词 | 通过 |
| F4-14 | 播放中歌词接口延迟返回 | 最多补发当前位置对应的一句，不批量补发历史歌词 | 通过 |
| F4-15 | 相邻重复歌词 | 只发送一次 | 通过 |
| F4-16 | 瞬时 TS/gRPC 发送失败 | 当前歌词自动重试 | 通过 |
| F4-17 | Voice 首个真实 PCM 帧事件 | 首个音频输出后才启动后端歌词时钟 | 通过 |
| F4-18 | seek 等待 Voice 下一次首帧 | seek 缓冲期间歌词时钟冻结，首帧后从目标位置继续 | 通过 |
| F4-19 | Rust 当前频道路由纯函数 | `target_mode=2` 保持为 TeamSpeak 当前频道，只有 3 映射为服务器消息 | 通过 |
| F4-20 | QQ 纯文本歌词回退 | 无时间戳时整段主歌词一次发送 | 通过 |
| F4-21 | QQ 歌词接口端点 | 接口实际返回 `plain_lyrics`，由频道 worker 一次发送 | 通过 |
| F4-22 | 暂停时播放时钟 | 暂停期间位置不推进，恢复后继续累计 | 通过 |
| F4-23 | 纯文本歌词发送失败后重连 | 在当前歌曲有效期间持续重试，恢复后仍只发送一条 | 通过 |
| F4-24 | 默认歌词前缀与长度 | 时间戳歌词和纯文本歌词均使用 `♪ ` 前缀，完整消息不超过 700 字符 | 通过 |
| F4-25 | 网页歌词同步开关 | 关闭取消任务，开启从当前歌曲重新调度 | 通过 |
| F4-26 | Voice 断线待发通知 | TS 连接失效时保留已入队歌词，重连后优先发送 | 通过 |
| F4-27 | seek 后重复副歌文本 | 重新发送新位置对应的相同文本，不被旧去重状态误抑制 | 通过 |
| F4-28 | 仅元数据/翻译标签 | 不发送标签内容，保持静默 | 通过 |
| F4-29 | voice-service 初始 TS 连接失败 | actor 不 panic，gRPC 服务继续可用 | 通过本地 smoke test |
| F4-30 | 暂停/恢复 gRPC 失败 | 回滚歌词时钟，不保留错误状态 | 通过 |
| F4-31 | TeamSpeak 当前频道命令构造 | `targetmode=2` 且歌词内容按 TS 协议转义 | 通过 |

本次实际执行结果：

```text
73 passed, 4 warnings in 5.42s
```

前端构建结果：

```text
1412 modules transformed
✓ built in 1.70s
```

4 个 warning 是项目已有的 FastAPI `on_event` 弃用提示。本阶段没有新增 warning。

歌词发送链路使用 `target_mode=2`，由 voice-service 映射为 TeamSpeak 当前频道；若通知队列暂时满，voice-service 返回失败，后端会对当前歌词进行重试，不会直接丢弃。

Voice actor 另外维护断线期间的待发送通知队列；连接对象失效时不会把已经从 gRPC 队列取出的歌词静默丢弃，重连后会优先恢复发送。

voice-service 初始连接 TeamSpeak 失败时不再重复轮询已完成的 tsclientlib 连接 Future，避免无 TS 环境下 actor 崩溃；待发通知仍保留在队列中。

TeamSpeak `sendtextmessage` 命令由统一构造函数生成，并验证 `targetmode=2` 与消息中的空格按协议编码为 `\s`。

## 5. 可复现测试链路

自动化测试通过 mock 固定播放位置和歌词接口，复现以下链路：

```text
当前队列项
  -> 读取网易云主歌词 lrc.lyric
  -> LRC 时间戳解析
  -> Voice 首个真实 PCM 帧事件启动后端时钟
  -> 读取当前播放位置
  -> 到达时间戳
  -> voice.send_notice(text, target_mode=2)
```

对应测试文件：

```text
tests/test_backend_regressions.py
```

专项复现命令：

```powershell
cd D:\workspace\codex\TS6MusicBot
backend\.venv\Scripts\python.exe -m pytest -q tests/test_backend_regressions.py -k "TsLyrics"
```

本轮 F4 专项测试结果：

```text
23 passed, 38 deselected, 4 warnings in 3.47s
```

Voice 路由单元测试：

```powershell
cd D:\workspace\codex\TS6MusicBot\voice-service
cargo test -q
```

```text
3 passed, 0 failed
```

本轮 backend→voice-service gRPC smoke test（未连接真实 TeamSpeak）：

```text
Ping -> 0.1.0
GetStatus -> STATE_IDLE
SendNotice(target_mode=2) -> True
```

复现命令（PowerShell，TeamSpeak 未启动也可验证 actor 不崩溃）：

```powershell
cd D:\workspace\codex\TS6MusicBot\voice-service
$env:TSBOT_TS3_HOST = "127.0.0.1"
$env:TSBOT_TS3_PORT = "9987"
$env:TSBOT_TS3_IDENTITY_FILE = "tmp\f4-integration-identity.json"
$env:TSBOT_VOICE_CONFIG_FILE = "tmp\f4-integration-voice-config.json"
cargo run -q -- 127.0.0.1:50051
```

另开 PowerShell 调用 `backend.voice_client` 的 `ping`、`get_status` 和 `send_notice(..., target_mode=2)`。预期 gRPC 可用，voice-service 日志不出现 `async fn resumed after completion`；没有 TeamSpeak 时不能预期频道收到消息。

对应的最小探测命令：

```powershell
cd D:\workspace\codex\TS6MusicBot
backend\.venv\Scripts\python.exe -c "import asyncio; from backend.voice_client import VoiceClient; print(asyncio.run(VoiceClient().ping()))"
backend\.venv\Scripts\python.exe -c "import asyncio; from backend.voice_client import VoiceClient; print(asyncio.run(VoiceClient().get_status()))"
backend\.venv\Scripts\python.exe -c "import asyncio; from backend.voice_client import VoiceClient; print(asyncio.run(VoiceClient().send_notice('F4 gRPC probe', target_mode=2)))"
```

## 6. 真实环境联调步骤

自动化测试没有连接真实网易云 API、TeamSpeak 服务器或 voice-service。联调时按以下步骤执行：

1. 在 Web 设置中完成网易云登录或配置可用的网易云 Cookie。
2. 启动后端、网页和 voice-service。
3. 选择一首确认有主歌词时间戳的网易云歌曲。
4. 在当前 TS 文字频道记录第一句歌词的时间点。
5. 暂停约 10 秒，确认暂停期间没有新歌词消息。
6. 恢复播放，确认歌词从暂停后的播放位置继续。
7. 拖动进度到后面的时间点，确认歌词跳到新位置附近。
8. 切换下一首，确认上一首的歌词任务停止。
9. 再选择一首无时间戳歌词，确认整段只发送一条消息。

Voice 服务的 `PlaybackEvent.STARTED` 现在在首个真实 PCM 帧进入输出链路后发出；backend 收到该事件后才启动本歌曲的歌词时钟。这样可以把 ffmpeg 启动、网络取流和预缓冲时间排除在歌词播放时钟之外。

本轮现场探测命令：

```powershell
foreach ($p in 50051,9987,8080,8081) {
  Test-NetConnection -ComputerName 127.0.0.1 -Port $p -WarningAction SilentlyContinue -InformationLevel Quiet
}
```

探测结果：50051（Voice gRPC）和 9987（TeamSpeak）未监听；8081 未监听；8080 有响应但不是当前后端 API（`/voice/status` 等接口返回 404）。因此本轮不能伪造真实频道消息和听感验收结果。

用户随后提供真实 TeamSpeak 主机名 `touhoufans.top`。在当前开发环境再次进行只读探测：DNS A 记录解析为 `198.18.0.73`，9987 探测失败；随后以 `TSBOT_TS3_HOST=touhoufans.top` 启动隔离 Voice 服务探针，服务本身可监听本地 gRPC，但等待期间未出现 `ts3 connected` 日志。该地址属于当前环境的隔离解析结果，尚未证明真实 TeamSpeak 服务可达，因此 F4 的“实际频道收到歌词”和时间误差现场验收仍保持“未验证”。本次探针未发送频道消息，也未修改正式运行配置。

用户进一步确认实际 TS6 地址为 `110.40.187.101`。本轮改用该 IP 直连启动隔离 Voice 服务，监听 `127.0.0.1:50053`；等待 15 秒未出现 `ts3 connected`。随后执行 gRPC 检查：`Ping=0.1.0`、`GetStatus=STATE_IDLE`，说明本地 Voice 服务正常，但尚未建立 TeamSpeak 会话。本轮未发送频道消息，也未修改正式运行配置。

进一步排查发现，默认 `logs/voice-service.json` 会在 Voice 启动时覆盖同名环境变量；该文件当时仍为 `TSBOT_TS3_HOST=127.0.0.1`，因此早先探针实际连接的是本机。使用不存在的临时 `TSBOT_VOICE_CONFIG_FILE` 隔离配置后，debug 日志确认实际目标为 `110.40.187.101:9987`，并记录了本地 UDP 源端口、连续 Init 重试以及最终 `Connection timed out: Packet was not acked`。

系统层检查显示当前运行环境使用 `198.18.0.1/30` 虚拟网卡和 `198.18.0.2` DNS；外部 DNS 查询也返回 `198.18.x.x` 隔离地址。由此判断：配置覆盖问题已定位；在配置修正后，剩余失败主要来自当前开发运行环境的网络隔离，不能据此判断 TS6 服务器本身离线。真实联调应在能直接访问 `110.40.187.101` UDP 9987 的宿主机或服务器上执行。

本轮按用户要求通过系统设置保存逻辑将 `voice.ts3_host` 改为 `110.40.187.101`，端口保持 `9987`，并生成新的 Voice 配置版本。Voice 服务重新读取配置后，debug 日志确认目标地址正确，但仍报 `Packet was not acked`；后端 gRPC 状态可用不等于 TS6 会话已建立，当前仍没有 `ts3 connected` 和真实频道消息。

随后将同一 Voice 服务切换到宿主机网络环境重新测试，配置、IP、端口和身份均不变。握手立即成功，日志出现 `Resender: Changed state, to: Connected`，并持续输出正常的音频发送诊断；后端 `GET /voice/status` 也保持可用。对比证明：之前失败的直接原因是 Codex 隔离网络无法完成外部 UDP 9987 回包，不是 TS6 服务器、地址、端口或本阶段连接代码问题。宿主机网络测试期间未主动发送频道测试消息。

真实联调时不要把 Cookie、Token、服务器密码或完整日志发布到测试文档和聊天消息中。

## 7. 已知限制与下一阶段入口

- TS 频道消息不会自动删除旧歌词消息，因此历史歌词会保留在频道聊天记录中。
- 时间戳同步受网易云 API、后端调度、网络和 TeamSpeak 转发延迟影响；当前实现以播放位置为基准，调度检查周期不超过 250 ms，发送失败会重试。
- gRPC `SendNotice` 返回成功只代表 voice-service 接收了通知；只有 TeamSpeak 连接成功并完成 `sendtextmessage` 后，才能确认当前频道实际收到消息。
- 真实时间误差仍需在可播放的 TeamSpeak 环境中测量；自动化测试已经覆盖后端时钟、首帧启动、seek 冻结和 300 ms 目标的时钟夹具。
- 没有时间戳的歌词只能整段发送一次，无法进行时间同步。
- 网页设置中的“当前频道歌词同步”开关已完成；关闭后取消歌词任务，重新开启后从当前播放位置重新调度。
- 代码阶段 F5/F6/F7 已继续完成；当前下一步是启动真实 TeamSpeak、Voice 和网易云环境，执行本报告第 6 节的现场验收。
