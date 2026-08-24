# 阶段 17：TeamSpeak 配置自动重连

## 本阶段内容

- Voice 发现 `logs/voice-service.json` 配置变化后，不再退出进程并依赖 Windows 子进程重新拉起。
- Voice 保持 gRPC 服务运行，只断开当前 TeamSpeak 连接。
- 重新读取新配置后自动连接 TeamSpeak。
- 头像、昵称、服务器地址、频道、密码等 Voice 配置统一使用这条自动重连链路。
- 服务器部署时不需要人工重启 Voice，也不依赖 `.exe` 自拉起。

## 自动化测试

在项目根目录执行：

```powershell
cd voice-service
cargo test
cd ..
```

本次实际结果：Voice 测试 `3 passed; 0 failed`；正式 `cargo build` 已成功。此前的文件占用和独立目录 CMake 报错已绕过，当前 `target\debug\voice-service.exe` 已包含自动重连逻辑。

## 手工测试步骤

1. 启动 Voice、后端和网页。
2. 确认 Voice 已连接 TS。
3. 在网页“设置 → TeamSpeak 配置”上传新的机器人头像，并点击“应用配置”。
4. 观察 Voice 日志，应出现：
   - `voice configuration changed; requesting TS reconnect`
   - `voice configuration changed; reconnecting with updated settings`
   - `ts3 connected`
5. 确认 Voice 的 gRPC 端口没有因为配置更新而长期消失，后端网页不需要重新启动。
6. 确认 TS 中 Bot 重新进入服务器后显示新头像。

## 预期结果

- 网页等待 Voice 配置版本更新并显示“已重启成功”。
- Voice 服务进程保持运行，只重新建立 TS 连接。
- 如果 TS 服务器暂时不可用，Voice 按原有退避策略自动重试。
