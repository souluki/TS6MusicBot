# 阶段 18：网页头像上传路径修复

## 根因

网页上传使用实际资产目录保存头像，但 Voice 配置之前固定写成 `./data/uploads/teamspeak-avatar`。当数据库使用项目根目录的 `tsbot.db` 时，实际文件位于 `uploads/teamspeak-avatar`，Voice 重连后会读取旧文件或找不到新文件。

## 修复内容

- Voice 配置现在根据实际资产目录自动生成相对路径。
- 默认部署使用 `./uploads/teamspeak-avatar`。
- 如果显式配置了仓库外的资产目录，则保留该目录的实际路径。
- 上传压缩、Voice 自动重连逻辑保持不变。

## 测试

```powershell
backend\.venv\Scripts\python.exe -m unittest tests.test_admin_configuration -v
```

预期：头像配置路径测试通过，并且配置路径与 `save_asset()` 实际写入路径一致。

## 手工验证

1. 重启一次后端，使新的路径逻辑加载。
2. 启动已更新的 Voice 服务。
3. 网页进入“设置 → TeamSpeak 配置”。
4. 上传一张小于 1 MiB 的 PNG/JPG 图片并点击“应用配置”。
5. 确认 Voice 自动重连后，TS 中 Bot 显示新头像。
