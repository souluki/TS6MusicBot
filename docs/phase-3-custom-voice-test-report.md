# F5 阶段测试文档：自定义内置语音与混音播放

## 1. 阶段范围

本阶段对应需求规格说明书中的 F5：管理员在网页上传音频并绑定指令，普通用户可以触发全部已配置内置语音；配置为全局一套；播放音乐时默认使用混音策略。

已实现：

- Web“系统配置 → TeamSpeak 配置”新增“自定义内置语音”区域。
- 管理员可以填写指令、显示名称并上传音频。
- 支持 MP3、WAV、OGG、FLAC、M4A、AAC、WebM，单文件最大 50 MiB。
- 音频保存到数据库对应数据目录的 `uploads/voices/`，不使用用户提交的任意路径。
- 管理员可以查看已绑定指令并删除资源。
- 指令自动规避系统内置指令，例如 `播放`、`暂停`、`歌单ID` 不能被覆盖。
- 普通用户在 TS 当前文字频道输入已绑定指令即可触发，例如 `掌声` 或 `!掌声`。
- 有音乐播放时调用 Voice 叠加播放接口，默认背景降低 12 dB，并以约 100 ms attack / 500 ms release 的平滑速度调整背景音量。
- 没有背景音乐时直接播放内置语音。
- 新增 gRPC `PlayOverlay`，后台 Voice 服务使用独立 ffmpeg 解码队列，将语音 PCM 与背景 PCM 混合后再编码发送。

## 2. 使用方法

### 2.1 网页端上传与绑定

1. 登录 Web 管理员账号。
2. 打开“系统配置 → TeamSpeak 配置”。
3. 在“自定义内置语音”中填写指令，例如：`掌声`。
4. 可选填写显示名称，例如：`热烈掌声`。
5. 选择 MP3/WAV 等音频文件，点击“选择音频并上传”。
6. 在已配置列表中确认绑定成功。

上传接口：

```text
PUT /admin/custom-voices/0?command=掌声&label=热烈掌声
Content-Type: audio/mpeg
X-File-Name: clap.mp3
```

删除接口：

```text
DELETE /admin/custom-voices/{voice_id}
```

以上接口需要管理员会话和 CSRF Token；普通用户不具备上传、修改和删除权限。

### 2.2 TS 端触发

在 TS 当前文字频道输入：

```text
掌声
```

或：

```text
!掌声
```

普通用户也可以触发，不要求管理员身份。

播放音乐时预期收到确认消息，并听到背景音乐降低后叠加的自定义语音；没有音乐时直接播放自定义语音。

## 3. 自动化测试用法

在项目根目录执行：

```powershell
cd D:\workspace\codex\TS6MusicBot
backend\.venv\Scripts\python.exe -m pytest -q
```

只运行自定义语音、歌词和歌单专项测试：

```powershell
backend\.venv\Scripts\python.exe -m pytest -q tests/test_backend_regressions.py -k "custom_voice or lyrics or playlist"
```

验证网页构建：

```powershell
cd D:\workspace\codex\TS6MusicBot\web
npm run build
```

验证 Voice Rust 服务和新增 gRPC 接口：

```powershell
cd D:\workspace\codex\TS6MusicBot\voice-service
cargo check
```

## 4. 测试用例与结果

| 编号 | 测试内容 | 预期结果 | 实际结果 |
|---|---|---|---|
| F5-01 | 绑定普通自定义指令 | 指令规范化并可保存 | 通过 |
| F5-02 | 使用系统指令名绑定 | 被拒绝，不能覆盖系统指令 | 通过 |
| F5-03 | 指令含空格 | 被拒绝 | 通过 |
| F5-04 | 上传支持的音频扩展名 | 通过格式校验 | 通过 |
| F5-05 | 上传不支持的文本文件 | 被拒绝 | 通过 |
| F5-06 | 普通用户触发已绑定指令 | 不需要管理员权限即可触发 | 通过 |
| F5-07 | 音乐正在播放时触发 | 调用 `PlayOverlay`，背景降低 12 dB | 通过 |
| F5-08 | 音乐空闲时触发 | 调用普通 `Play` 播放音频 | 通过 |
| F5-09 | Voice 新增 `PlayOverlay` gRPC | Rust 服务编译通过 | 通过 |
| F5-10 | 歌词/歌单回归 | 既有阶段功能保持通过 | 通过 |
| F5-11 | 完整 Python 测试集 | 全部通过 | 46 passed |
| F5-12 | 前端生产构建 | Vite 构建成功 | 通过 |
| F5-13 | Voice Rust 编译检查 | `cargo check` 成功 | 通过 |

本次实际结果：

```text
46 passed, 4 warnings in 1.95s
```

前端构建：

```text
1412 modules transformed
✓ built in 1.65s
```

Rust 构建：

```text
cargo check 成功
```

Rust 输出的 4 个 warning 来自仓库已有的 `vendor/tsproto` 不稳定名称冲突提示，不是本阶段新增错误。

## 5. 可复现测试链路

自动化测试通过临时音频文件和 mock Voice 状态复现两条路径：

```text
TS 指令
  -> 查找绑定的 CustomVoice
  -> 校验文件存在
  -> Voice 状态为 PLAYING -> PlayOverlay(duck_db=12)
  -> Voice 状态为空闲 -> Play
```

对应测试文件：

```text
tests/test_backend_regressions.py
```

专项命令：

```powershell
cd D:\workspace\codex\TS6MusicBot
backend\.venv\Scripts\python.exe -m pytest -q tests/test_backend_regressions.py -k "custom_voice"
```

## 6. 真实环境联调步骤

自动化测试没有连接真实 TeamSpeak 服务器，也没有实际播放音频。联调时：

1. 启动后端、网页、voice-service 和 TS3 连接。
2. 管理员在网页上传一个短 MP3 文件并绑定 `测试语音`。
3. 用普通用户身份在 TS 当前文字频道输入 `测试语音`。
4. 播放一首音乐后再次触发，确认音乐持续播放且背景明显降低，语音清晰叠加。
5. 在音乐空闲时触发，确认语音可以直接播放。
6. 重新加载网页，确认绑定记录仍存在。
7. 删除绑定后再次输入指令，确认不会触发。
8. 尝试绑定 `播放`，确认网页返回系统指令冲突错误。

真实联调时不要上传含有敏感信息的音频，也不要把 Cookie、Token、服务器密码或完整日志写入文档。

## 7. 已知限制与下一阶段入口

- 当前自定义语音采用全局配置，不支持按频道配置，这是需求确定的范围。
- 音频格式依赖运行环境中的 ffmpeg 解码能力；文件格式通过扩展名/Content-Type 初步校验，ffmpeg 无法解码时会在 Voice 日志中报错。
- Voice 叠加播放使用内存队列，单个语音过长或系统负载高时会产生一定延迟。
- 当前没有“暂停背景后播放”选项，默认只实现需求指定的混音策略。
- 下一阶段建议进入 F6：指定频道新用户加入时播放全局欢迎语音；Bot 不在目标频道时跳过，不自动移动。
