# TS6MusicBot 阶段 7 测试文档：内置语音启用/停用与回归

## 1. 阶段目标

本阶段补齐 F5/F7 中“管理员可以启用、停用内置语音”的配置闭环：

- Web 设置页显示每条内置语音的启用状态。
- 管理员可以点击“停用”或“启用”，不需要重新上传文件。
- 停用状态保存在现有 `app_settings` 表，不修改已有 `custom_voices` 表结构。
- 普通用户触发已停用指令时，后端返回明确提示，不调用 Voice 播放。
- 欢迎语音也遵守同一启用状态；被停用的欢迎音频不会播放。
- 上传的新内置语音默认启用，保持原有行为兼容。

## 2. 使用方法

1. 启动 backend 和 Web，使用管理员登录。
2. 打开“设置 → TeamSpeak 配置 → 自定义内置语音”。
3. 上传一条音频并绑定指令，例如：

   ```text
   指令：掌声
   显示名称：掌声提示
   ```

4. 确认列表显示“已启用”，在 TeamSpeak 当前文字频道输入 `掌声`，确认音频正常播放。
5. 点击该条目右侧的“停用”，确认状态变为“已停用”。
6. 再次输入 `掌声`，预期 Bot 回复“内置语音已停用”，且 Voice 不播放音频。
7. 点击“启用”，再次输入 `掌声`，预期恢复正常播放。
8. 如果该音频被设置为全局欢迎语音，停用期间新用户进入目标频道也不得触发；重新启用后恢复。

## 3. 实现链路

```text
Web 点击停用/启用
  -> PUT /admin/custom-voices/{id}/enabled?enabled=false|true
  -> app_settings 保存 voice.custom_voice.{id}.enabled
  -> TS 指令或欢迎事件触发
  -> backend 检查启用状态
  -> 停用：返回提示，不调用 voice.play/play_overlay
  -> 启用：沿用原有直接播放或 ducking 混音链路
```

对应代码：

- `backend/main.py`：状态读取、保存、管理接口和播放前拦截。
- `web/src/views/SettingsView.vue`：启用状态展示和操作按钮。
- `tests/test_backend_regressions.py`：停用语音不播放的回归测试。

## 4. 自动化测试

专项命令：

```powershell
cd D:\workspace\codex\TS6MusicBot
backend\.venv\Scripts\python.exe -m pytest -q tests/test_backend_regressions.py -k "CustomVoice"
```

专项结果：

```text
7 passed, 42 deselected, 4 warnings in 0.63s
```

本阶段新增/覆盖的关键用例：

| 编号 | 测试内容 | 预期结果 | 结果 |
|---|---|---|---|
| F7-01 | 内置语音指令规范化 | 保留合法指令，拒绝系统指令和空格 | 通过 |
| F7-02 | 支持音频格式校验 | MP3/WAV 通过，文本文件拒绝 | 通过 |
| F7-03 | 音乐播放中触发 | 走 ducking 混音，背景音量降低 12 dB | 通过 |
| F7-04 | 空闲时触发 | 直接播放内置音频 | 通过 |
| F7-05 | 普通用户触发 | 不要求管理员权限 | 通过 |
| F7-06 | 停用后触发 | 返回停用提示，不调用 Voice 播放 | 通过 |
| F7-07 | 启用状态持久化 | 停用/启用状态可从 `app_settings` 读回 | 通过 |

全量回归命令：

```powershell
backend\.venv\Scripts\python.exe -m pytest -q
```

全量结果：

```text
61 passed, 4 warnings in 5.09s
```

前端构建命令及结果：

```powershell
cd D:\workspace\codex\TS6MusicBot\web
npm run build
```

```text
1412 modules transformed
✓ built in 1.67s
```

4 个 warning 是项目原有 FastAPI `on_event` 弃用提示，本阶段没有新增 warning。后端 `compileall` 和 `git diff --check` 也通过。

## 5. 真实环境复现

自动化测试没有连接真实 TeamSpeak 或音频输出设备。现场验收时：

1. 启动 backend、voice-service 和 Web。
2. 上传一条短 MP3，绑定 `掌声`，确认列表为“已启用”。
3. 在 TS 频道输入 `掌声`，确认听到音频。
4. 在网页点击“停用”，再次输入 `掌声`，确认只收到停用提示，没有声音。
5. 重新启用并重复输入，确认声音恢复。
6. 将同一条音频设为全局欢迎语音，分别验证启用和停用状态下的新用户进入行为。

## 6. 已知限制

- 本阶段只补齐启用/停用开关；音频时长、实际解码能力和真实听感仍需在 Voice 服务运行后现场验收。
- 欢迎语音仍按全局单选和目标频道规则工作，不扩展为按频道多套音频。
- 当前真实 TeamSpeak 联调尚未执行，原因是本轮自动化环境没有运行中的 TeamSpeak、Voice gRPC 和 Web 实例。
