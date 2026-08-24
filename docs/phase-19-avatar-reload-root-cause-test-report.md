# Phase 19：网页头像更新根因修复测试报告

## 1. 本阶段目标

修复网页上传 TeamSpeak 固定头像后，Voice 虽然执行配置重连，但新头像没有再次上传的问题。

## 2. 根因

Voice 首次连接成功并完成头像上传后，会将 `avatar_set_done` 置为 `true`。

网页上传新头像会修改 `logs/voice-service.json`，Voice 检测到变化后重连，但原逻辑没有清除这个状态。重连后的初始化逻辑因此跳过头像上传，导致 TS 继续显示旧头像或默认头像。

## 3. 修复内容

在 `voice-service/src/main.rs` 的配置变更重连分支中，将 `avatar_set_done` 重置为 `false`。每次网页头像配置变化触发重连后，Voice 会重新读取当前头像文件、计算 MD5、上传文件并发送 `clientupdate client_flag_avatar`。

本次没有修改网页上传接口、图片压缩逻辑、动态专辑头像逻辑，也没有回撤之前的头像路径修复。

## 4. 自动化测试

执行目录：`voice-service`

```powershell
cargo test
```

结果：3 个测试通过，0 个失败。

编译：

```powershell
cargo build
```

结果：编译成功。现有 `tsproto` 的 4 条 `unstable_name_collisions` 是依赖源码警告，不是本次错误。

## 5. 现场测试状态

已用修复后的二进制启动 Voice。该进程没有进入头像上传阶段，因为当前测试机到 TS 地址的连接没有收到初始化数据包：

```text
Failed to connect to server at "110.40.187.101:9987":
Packet was not acked
```

辅助检查结果：主机可以 Ping，但 TCP 9987 检测失败。TeamSpeak 实际语音协议还需要 UDP，因此该结果只能说明当前网络/服务器端口状态异常，不能作为头像修复失败的证据。

## 6. 可复现测试步骤

1. 确认 Voice 已使用最新构建启动，并且日志出现 `ts3 connected`。
2. 在网页设置中上传一张新的机器人头像。
3. 等待网页提示 Voice 配置已恢复。
4. 查看 Voice 输出，应该依次看到：

   ```text
   voice configuration changed; requesting TS reconnect
   voice configuration changed; reconnecting with updated settings
   avatar upload started: ...
   avatar flag command sent: ...
   avatar updated: ...
   ```

5. 在 TS 客户端确认机器人头像已变为新图片。

如果没有更新，按日志最后出现的位置判断：

- 没有 `voice configuration changed`：网页没有成功写入 Voice 配置；
- 有重连但没有 `avatar upload started`：头像状态或路径仍有问题；
- 有上传但没有 `avatar updated`：需要检查 TS 文件传输或权限返回；
- 有 `avatar updated` 但客户端不变：需要检查 TS 客户端缓存或服务器端头像刷新。

