# Phase 21：TS 循环播放指令测试报告

## 1. 本阶段目标

补充 TeamSpeak 文字指令，用于切换播放队列的循环模式。

## 2. 新增指令

```text
循环 列表
循环 单曲
循环 关闭
```

兼容英文写法：

```text
repeat all
repeat one
repeat off
```

单独输入 `循环` 会返回当前模式和用法提示。

## 3. 模式说明

| 指令 | 模式 | 行为 |
| --- | --- | --- |
| `循环 列表` | `all` | 队列播完后从第一首继续 |
| `循环 单曲` | `one` | 当前歌曲结束后重复当前歌曲 |
| `循环 关闭` | `none` | 队列播完后停止 |

## 4. 自动化测试

定向测试：

```powershell
.\backend\.venv\Scripts\python.exe -m unittest tests.test_backend_regressions.TsChatCommandTests
```

结果：7 个测试通过。

完整后端回归测试：

```powershell
.\backend\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
```

结果：83 个测试通过，0 个失败。

## 5. 手工测试方法

1. 播放一个包含多首歌曲的歌单。
2. 在 TS 当前文字频道输入 `循环 列表`，应回复“已切换为列表循环”。
3. 等待队列最后一首结束，确认从第一首重新播放。
4. 输入 `循环 单曲`，确认当前歌曲结束后重复播放。
5. 输入 `循环 关闭`，确认队列播完后停止。

