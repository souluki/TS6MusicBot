# S1 阶段测试文档：网易云歌单 ID/链接检索与批量入队

## 1. 阶段范围

本阶段对应需求规格说明书中的 F1/S1，音乐源限定为网易云音乐。

已实现：

- TeamSpeak 文字指令 `歌单ID 123456`，支持可选前缀 `!歌单ID 123456`。
- TeamSpeak 文字指令 `歌单ID https://music.163.com/#/playlist?id=123456`。
- 同时支持别名 `歌单链接`、`playlistid`、`playlist-id`。
- 后端统一校验数字歌单 ID 和 `music.163.com` 标准歌单链接，拒绝其它域名。
- 复用现有歌单批量入队流程；网易云只返回 `trackIds` 时，后端按批次补全歌曲详情。
- 网页“发现歌单”页面增加 ID/链接输入框，可以先检索歌单，再一次性加入队列。
- 无效输入、空歌单、网易云查询失败和单曲入队失败均返回可读提示。

本阶段暂不包含：QQ 音乐/B 站歌单、歌词同步、动态头像、内置语音和欢迎语音。

## 2. 使用方法

### 2.1 TeamSpeak 测试

在 Bot 当前可监听的 TS 文字频道输入：

```text
歌单ID 123456
```

或输入网易云分享链接：

```text
!歌单ID https://music.163.com/#/playlist?id=123456
```

预期行为：Bot 查询歌单，逐首加入现有播放队列；如果当前播放器空闲且至少成功加入一首，则沿用现有逻辑自动开始播放。Bot 会回复类似：

```text
已从歌单《示例歌单》加入 20 首歌曲
```

如果部分歌曲不可用，会附加失败数量，例如“加入 18 首歌曲，2 首失败”。

### 2.2 网页测试

1. 启动后端和网页服务，打开 Web 控制台的“发现歌单”页面。
2. 在“输入网易云歌单 ID 或链接”输入框填写数字 ID 或网易云歌单链接。
3. 点击“检索歌单”。
4. 核对歌单名称、创建者和歌曲数量。
5. 点击“全部加入队列”，确认后观察入队进度和结果提示。

网页调用的后端接口为：

```text
GET /netease/playlist/resolve?value=<数字ID或网易云链接>
```

该接口返回规范化的歌单信息和已补全的歌曲列表；网页继续复用现有的 `/queue/netease` 单曲入队接口，因此不会引入第二套队列逻辑。

## 3. 自动化测试用法

在项目根目录执行 PowerShell：

```powershell
cd D:\workspace\codex\TS6MusicBot
backend\.venv\Scripts\python.exe -m pytest -q
```

只运行本阶段相关的后端回归测试：

```powershell
backend\.venv\Scripts\python.exe -m pytest -q tests/test_backend_regressions.py
```

验证前端 TypeScript/Vue 生产构建：

```powershell
cd D:\workspace\codex\TS6MusicBot\web
npm run build
```

## 4. 测试用例与结果

| 编号 | 测试内容 | 预期结果 | 实际结果 |
|---|---|---|---|
| S1-01 | 纯数字 ID `歌单ID 123456` | 查询指定网易云歌单并批量入队 | 通过 |
| S1-02 | `!歌单ID 123456` | 可识别可选 `!` 前缀 | 通过 |
| S1-03 | `music.163.com/#/playlist?id=...` | 从 URL 提取 ID 并批量入队 | 通过 |
| S1-04 | `music.163.com/playlist?id=...` | 从标准查询参数 URL 提取 ID | 通过 |
| S1-05 | 其它域名伪造链接 | 不访问网易云接口，返回无效提示 | 通过 |
| S1-06 | 非数字、无参数指令 | 返回用法或错误提示 | 通过 |
| S1-07 | 单曲详情缺少可用 ID | 跳过该曲并统计失败数量 | 通过 |
| S1-08 | 网页解析接口输入数字 ID | 返回歌单及歌曲列表 | 通过 |
| S1-09 | 网页解析接口输入非法链接 | 返回 HTTP 400 | 通过 |
| S1-10 | 既有 `playlist + select` 流程 | 重构后行为保持不变 | 通过 |
| S1-11 | 完整 Python 测试集 | 全部通过 | 36 passed |
| S1-12 | 前端生产构建 | Vite 构建成功 | 通过 |

本次实际执行结果：

```text
36 passed, 4 warnings in 2.06s
```

前端构建结果：

```text
1412 modules transformed
✓ built in 8.20s
```

4 个 warning 是项目现有 FastAPI `on_event` 弃用提示，不影响本阶段功能；本阶段没有新增 warning。

## 5. 可复现手工测试

### 5.1 使用固定测试桩验证 TS 批量入队

自动化测试不依赖真实网易云网络请求，使用 mock 返回两首歌曲，验证了以下链路：

```text
TS 消息
  -> 指令解析
  -> 网易云歌单 ID/URL 解析
  -> playlist_detail
  -> trackIds/歌曲详情补全
  -> _enqueue_netease_song × N
  -> TS 回复入队统计
```

对应测试文件：

```text
tests/test_backend_regressions.py
```

可复现命令：

```powershell
cd D:\workspace\codex\TS6MusicBot
backend\.venv\Scripts\python.exe -m pytest -q tests/test_backend_regressions.py -k "playlist_id or playlist_resolve or playlist_search"
```

### 5.2 使用真实环境验证

真实环境测试需要可访问网易云 API，并在 Web 设置中完成网易云登录/授权（如果歌单或歌曲需要登录态）。准备完成后：

1. 启动 TS6MusicBot。
2. 在 Web“发现歌单”页面输入一个实际存在的网易云歌单 ID。
3. 记录歌单名称、歌曲数量、成功入队数量和失败数量。
4. 在 TS 文字频道再次输入同一个 ID，确认回复统计与队列变化一致。
5. 用同一歌单的标准网易云链接重复测试，确认解析到同一个歌单 ID。
6. 使用 `https://example.com/playlist?id=123` 验证系统拒绝非网易云域名。

真实环境测试时不要把 Cookie、Token 或 TS 密码写入测试文档、提交记录或聊天消息。

## 6. 已知限制与下一阶段入口

- 当前批量入队仍按现有单曲接口逐首处理，大歌单会需要较长时间；后续可增加并发上限和进度任务接口。
- 网易云 API 返回不可用歌曲时会跳过并统计失败，但不会自动替换歌曲。
- 本阶段只实现网易云歌单 ID/链接，不处理 QQ 音乐、B 站或其它平台链接。
- 下一阶段建议进入 F4：主歌词获取、时间戳解析、按播放时间更新 TS 当前文字频道，并保持“无时间戳时整段一次性发送”的需求约束。
