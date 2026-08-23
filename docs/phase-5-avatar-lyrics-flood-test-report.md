# Phase 5：动态头像与 TS 文字消息限流修复测试报告

## 1. 测试目标

验证以下问题的定位和修复：

- 动态头像请求已经发出，但 TS 客户端头像没有更新。
- 当前 TS 频道没有显示播放歌词。
- 歌曲播放链路本身不能因为头像或歌词失败而中断。

## 2. 根因

TS 服务器对本地测试 Bot 返回了 `ClientIsFlooding`。这是 TeamSpeak 的客户端命令限流，不是网页开关、网易云搜索或 FFmpeg 播放失败。

日志证据：

```text
[2026-08-23 20:19:45] [INFO] [voice] avatar flag command sent: de3dd88d725868f4c1e3133b4f61bfbb
[2026-08-23 20:19:45] [WARN] [voice] set avatar flag rejected: ClientIsFlooding
```

当前设置已确认：

- `voice.netease_dynamic_avatar = true`
- `voice.netease_dynamic_nickname = true`
- `voice.ts_lyrics_enabled = true`

## 3. 本阶段代码调整

`voice-service/src/main.rs` 对 TS 文字消息发送做了两项调整：

1. 每次只发送一条 `sendtextmessage`，两条消息之间至少间隔 100 ms。
2. 使用 TeamSpeak 命令结果确认消息是“已送达”还是“被拒绝”，分别记录 `sendtextmessage delivered` 和 `sendtextmessage rejected`。

这样可以避免歌词快速刷新时触发 TS 限流，并能在日志中区分“本地已排队”和“TS 服务器实际接受”。

动态头像仍使用压缩后的不超过 64 KiB、最长边不超过 256 像素的图片，并等待头像设置命令结果。

## 4. 可复现测试

在项目目录执行：

```powershell
cd D:\workspace\codex\TS6MusicBot\voice-service
cargo check -q
cargo test -q
```

实际结果：

```text
running 3 tests
...
test result: ok. 3 passed; 0 failed
```

启动语音服务后检查：

```powershell
Get-Content D:\workspace\codex\TS6MusicBot\logs\voice-runtime.log -Tail 100 |
  Select-String -Pattern "avatar|sendtextmessage|ClientIsFlooding"
```

## 5. 当前测试结论

- 语音服务已使用新编译版本启动，并成功连接 `110.40.187.101:9987`。
- `/voice/status` 显示 `voice_connected = true`，音频发送诊断没有错误。
- 动态头像和 TS 文字暂时仍无法显示，因为服务器仍处在 `ClientIsFlooding` 限流窗口内。
- 本阶段代码编译和单元测试通过；真实 TS 显示测试需要等待限流解除后，用单首歌曲进行验证。

## 6. 限流解除后的单首验证步骤

1. 等待 TS 服务器限流窗口结束，预计从最后一次 `ClientIsFlooding` 起约 8～10 分钟。
2. 保持队列为空，只加入一首网易云歌曲。
3. 观察头像是否变为该歌曲专辑图、昵称是否变为歌名。
4. 观察目标 TS 文字频道是否按时间戳显示主歌词。
5. 记录以下日志：

```text
avatar updated: <md5>
sendtextmessage delivered: [歌词] ...
```

如果仍出现 `ClientIsFlooding`，不要继续重复播放或批量加歌，应先检查 TS 服务器的客户端 flood 设置和该 Bot 身份的历史请求。
