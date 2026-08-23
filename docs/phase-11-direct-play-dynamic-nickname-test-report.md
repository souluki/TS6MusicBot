# 阶段 11：直接播放搜索歌曲的动态昵称修复测试报告

## 1. 问题

在 TeamSpeak 文字频道中执行搜索结果的直接播放命令，例如：

```text
搜索 水手
播放 190381
```

Bot 能够回复“立即播放”，但昵称仍保持固定昵称，没有切换为歌曲名。

## 2. 排查结论

后端的直接播放路径会创建网易云队列项，并调用动态资料更新：

```text
track_id = netease:<歌曲ID>
title = <歌曲名>
```

数据库中的以下配置也已确认开启：

```text
voice.netease_dynamic_nickname = true
voice.netease_dynamic_avatar = true
```

问题分为两层：

1. 语音服务的 TeamSpeak `clientupdate` 发送方式此前只发出原始数据包，不等待 TeamSpeak 返回结果；昵称更新被 TS 拒绝时，后端仍会收到“已接受”的假成功状态。
2. 对歌曲“水手”进行直接 RPC 验证时，TeamSpeak 返回了 `ParameterInvalidSize`。该歌名只有 2 个字符，触发了 TS 的昵称最小长度限制。

## 3. 修复内容

- 语音服务为昵称/简介这类客户端资料命令增加排队。
- 每次只发送一个资料命令，并等待 TeamSpeak 的 `MessageResult`。
- TS 接受或拒绝时写入语音运行日志。
- 后端收到语音服务拒绝时写入后端日志，便于区分配置问题、限流和 TS 权限问题。
- 昵称 RPC 现在等待 TS 的真实回执，不再把“已进入队列”误报为成功。
- 遇到临时限流时，后端会在约 0.6 秒、1.2 秒后进行两次重试。
- 歌名少于 3 个字符时追加歌手名，例如 `水手 - 郑智化`，避免 TS 拒绝短昵称。
- 未修改启动脚本、端口、绝对路径配置或其他播放逻辑。

## 4. 自动化测试

在项目根目录执行：

```powershell
.\backend\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
cargo test --manifest-path voice-service/Cargo.toml
```

测试结果：

| 测试项 | 结果 |
|---|---:|
| Python 后端回归测试 | 77/77 通过 |
| Rust 语音服务单元测试 | 3/3 通过 |
| Rust 编译检查 | 通过 |

Rust 依赖产生了已有的 `unstable_name_collisions` 警告，但没有编译错误，不影响本次修复。

## 5. 手工复现测试

### 准备

1. 在网页设置中确认“网易云动态歌曲昵称”已开启。
2. 关闭旧的 TS Bot 进程，避免旧版本语音服务仍占用端口。
3. 重新构建并启动本次修改后的语音服务、后端和网页端。

### 测试步骤

1. 让 Bot 进入目标 TS 频道。
2. 在当前 TS 文字频道发送：

   ```text
   搜索 水手
   ```

3. 发送：

   ```text
   播放 190381
   ```

4. 观察 Bot 是否开始播放。
5. 观察 Bot 昵称是否变为“水手”。
6. 检查日志：

   ```powershell
   Select-String -Path .\logs\voice-runtime.log -Pattern "client profile|nickname" -CaseSensitive:$false
   Select-String -Path .\logs\backend-runtime.log -Pattern "动态昵称|昵称更新" -CaseSensitive:$false
   ```

### 预期结果

- 搜索结果和播放反馈正常。
- Bot 昵称切换为当前网易云歌曲名。
- 语音日志应出现：

  ```text
  client nickname update requested: 水手
  client profile update command sent
  client profile update accepted
  ```

### 失败时收集的信息

- 若出现 `client profile update rejected`，说明 TS 已返回明确拒绝原因，保留该行完整内容。
- 若出现 `client profile update send failed`，说明语音服务发送命令失败。
- 若后端出现“动态昵称更新被语音服务拒绝”，说明 gRPC 已连通，但 TS 没接受昵称更新。
- 若日志完全没有上述内容，优先确认运行的是否仍是旧版语音服务，以及是否只重启了后端而没有重启 TS Bot。

## 6. 当前验证边界

本阶段已完成代码编译、自动化测试和真实 TS 手工测试。

手工测试结果：执行“搜索 水手”后播放 `190381`，Bot 正常播放，昵称按短歌名规则成功更新，语音服务可正常启动和停止。

## 7. Windows Ctrl+C 说明

在 Windows 中用 `cargo run` 启动时，`Ctrl+C` 可能只结束 Cargo 父进程，留下仍占用 50051 端口的 `voice-service.exe` 子进程。表现为终端返回提示符，但 Bot 仍留在 TS 中。

停止时应确认并结束实际服务进程：

```powershell
netstat -ano | Select-String ':50051'
Stop-Process -Id <上一步显示的 PID> -Force
```

重新测试时直接运行 `target\debug\voice-service.exe`，避免 Cargo 子进程残留。
