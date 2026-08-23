# TS6MusicBot 阶段 5 测试文档：网易云动态头像、昵称与客户端简介

## 1. 阶段目标

本阶段实现网易云播放时的 TS 客户端资料联动，并全部提供网页开关：

- 网易云动态专辑头像：下载当前歌曲专辑图，交由 Voice 服务上传并更新 Bot 头像。
- 网易云动态歌曲昵称：将 Bot 昵称更新为当前歌曲名。
- 网易云动态客户端简介：按当前项目已有简介生成逻辑更新客户端简介；歌词不会写入简介。
- 只对 `netease:<song_id>` 播放项生效，QQ 音乐和 B 站不会触发本阶段的动态资料更新。
- 头像和昵称更新不要求重启 Voice 服务；头像通过运行时文件上传队列处理。

## 2. 网页端使用方法

1. 打开 Settings → TeamSpeak 配置。
2. 根据需要打开以下开关：
   - “网易云动态专辑头像”
   - “网易云动态歌曲昵称”
   - “网易云动态客户端简介”
3. 保存配置；如果只需要昵称，可以只开启昵称开关。
4. 播放一首网易云歌曲，等待约 0.2 秒的防抖时间。
5. 观察 TS 客户端列表中的 Bot 昵称和头像。

## 3. 实现链路

```text
网易云队列项
  └─ track_id = netease:<id>
      └─ _set_now_playing_queue_item
          └─ 动态资料 worker
              ├─ SetClientNickname → Voice → TS clientupdate
              └─ 下载专辑图 → 本地临时文件 → SetClientAvatar
                                      → Voice 文件上传 → TS clientupdate
```

协议层新增：

- `SetClientNickname`
- `SetClientAvatar`

头像下载限制为 5 MiB，并只接受 PNG、JPEG、GIF、WebP 的真实文件头；下载地址只取播放元数据中的网易云专辑图 URL。

## 4. 自动化测试用法

在项目根目录执行：

```powershell
backend/.venv/Scripts/python.exe -m pytest -q
```

重新生成 Python gRPC 文件并检查 Rust Voice 服务：

```powershell
backend/.venv/Scripts/python.exe -c "from backend.grpc_codegen import ensure_voice_stubs; ensure_voice_stubs()"
Set-Location voice-service
cargo check -q
Set-Location ..
```

检查网页构建：

```powershell
Set-Location web
npm run build
Set-Location ..
```

## 5. 测试覆盖与实际结果

- 网易云动态昵称可以调用 `set_client_nickname`，并且昵称会被清理为单行、最多 64 个字符。
- 非网易云播放项不会调用动态昵称更新。
- 动态头像可以下载受支持图片、保存临时文件并调用 `set_client_avatar`。
- 动态头像只接受 PNG、JPEG、GIF、WebP，超过 5 MiB或格式不正确会跳过更新。
- 动态资料采用 generation 防抖，快速切歌时旧歌曲的异步更新不会覆盖新歌曲。
- 切换到非网易云、停止播放或关闭动态昵称后，昵称恢复 `voice.ts3_nickname`；关闭动态头像后恢复固定头像（固定头像存在时）。
- 头像以图片内容 MD5 去重，不同 URL 但相同图片内容不会重复上传。
- Python 全量测试：`52 passed, 4 warnings`。
- Rust Voice 服务：`cargo check -q` 通过；4 条警告来自 vendor 依赖已有的 `unstable_name_collisions`。
- Web 构建：`npm run build` 通过，Vite 完成 `1412 modules transformed`。
- `git diff --check` 通过；仅有工作区换行符转换提示。

## 6. 可复现的现场 TS 测试

### 昵称

1. 只开启“网易云动态歌曲昵称”。
2. 播放网易云歌曲《测试歌曲》。
3. 预期 Bot 昵称变为“测试歌曲”；切换到 B 站或 QQ 音乐时不触发本阶段更新。

### 头像

1. 开启“网易云动态专辑头像”。
2. 播放一首有专辑图的网易云歌曲。
3. 观察 Voice 日志中的 `dynamic avatar upload started` 和 `avatar updated`。
4. 预期 TS 客户端列表显示对应专辑图。

### 快速切歌

1. 连续快速播放两首网易云歌曲。
2. 观察最终资料。
3. 预期最终昵称和头像对应最后一首，不被前一首的延迟下载任务覆盖。

## 7. 已知限制

- TeamSpeak 头像更新依赖 Bot 的文件上传权限；权限不足时昵称仍可独立更新，头像会记录失败日志。
- 网易云专辑图必须能被后端 HTTP 客户端访问；网络或 CDN 失败时不影响歌曲播放。
- 当前没有实现切换到非网易云后自动恢复上一次固定头像；这是下一步可选增强项。
