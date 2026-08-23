# Phase 5 头像上传大小修复测试报告

## 1. 问题现象

网页端上传默认头像成功，网易云专辑图也能下载到本地，但 TeamSpeak 客户端看不到头像。旧日志没有显示头像错误，因为头像事件只通过 gRPC 发送，没有写入 `voice-runtime.log`。

## 2. 根因

TeamSpeak 文件传输阶段返回：

```text
avatar filetransfer failed: PermissionInvalidSize
```

当前默认头像约 220 KB，超过服务器的 `i_client_max_avatar_filesize` 权限；网易云专辑图可能超过 300 KB，因此固定头像和动态头像会同时失败。

## 3. 修复内容

- 上传默认头像时自动转为 JPEG。
- 最大尺寸限制为 256×256。
- 目标文件大小限制为 64 KiB。
- `client_flag_avatar` 改为带 `return_code` 的命令，等待 TeamSpeak 返回成功或具体错误。
- Voice 日志现在会记录头像上传开始、文件传输失败、命令拒绝和最终成功。
- Voice 使用 `TSBOT_FFMPEG` 指定的绝对路径，避免 Windows 安装 FFmpeg 后旧进程 PATH 未刷新。

## 4. 自动化测试

项目根目录执行：

```powershell
.\backend\.venv\Scripts\python.exe -m unittest tests.test_admin_configuration
Set-Location voice-service
cargo test -q
Set-Location ..
```

预期：头像配置测试和 Voice Rust 测试全部通过；Rust vendor 依赖可能出现既有的 `unstable_name_collisions` 警告。

## 5. 实机复现与结果

### 固定头像

1. 在网页设置中上传机器人头像，或使用当前 `data/uploads/teamspeak-avatar`。
2. 重启 Voice 服务。
3. 查看：

```powershell
Select-String -Path .\logs\voice-runtime.log -Pattern "avatar" -CaseSensitive:$false
```

成功日志应包含：

```text
avatar upload started: ...
avatar flag command sent: ...
avatar updated: ...
```

本次实测已出现 `avatar updated`，固定头像上传链路成功。

### 动态头像

1. 网页开启“网易云动态专辑头像”。
2. 播放一首网易云歌曲。
3. 查看同一日志。
4. 预期出现 `dynamic avatar upload started` 和 `avatar updated`，TeamSpeak 客户端列表显示新的专辑图。

如果出现 `ClientIsFlooding`，先等待服务器限流解除，再测试下一首；这表示请求频率受服务器限制，不是图片大小错误。

## 6. 当前环境备注

当前 `data/uploads/teamspeak-avatar` 已压缩到约 14 KB，原文件备份在 `tmp/teamspeak-avatar.original`。Voice 配置已写入 FFmpeg 绝对路径。
