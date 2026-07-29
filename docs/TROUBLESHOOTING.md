# 故障排查

## 1. Bark 没有收到通知

运行：

```powershell
python codex_bark_hook.py --test
Get-Content codex_bark_hook.log -Tail 50
```

检查：

- `enable_bark_notification` 是否为 `true`；
- `bark_url` 是否为 Bark 首页复制的设备地址；
- iPhone 是否允许 Bark 通知；
- 网络、代理或证书是否阻止 Python 访问 Bark；
- Bark 返回码是否为 `200`。

## 2. 只有回答，没有用户问题

确认 `UserPromptSubmit` 和 `Stop` 都已信任并启用。日志正常顺序应为：

```text
已记录用户问题
问答已写入月度 JSON
Bark 通知发送成功
```

## 3. `Invalid \escape`

Codex 在 Windows 上传入的某些路径可能含未经正确转义的反斜杠。脚本会在标准 JSON 解析失败后尝试修复。`hooks.json` 中的路径仍建议使用 `/`。

## 4. JSON 文件损坏

月度文件读取失败时，脚本会尝试把原文件重命名为带“损坏备份”字样的文件，然后重建月度 JSON。检查日志和备份文件，不要直接覆盖备份。

## 5. 留下 `.lock` 文件

脚本会自动清理超过 `stale_lock_seconds` 的锁。没有任务运行时，也可以手动删除 `.locks` 中的旧文件。

## 6. `.prompt_cache` 中有残留

异常中断、通知失败或归档失败时，缓存会保留。确认没有相关任务仍在运行后，可以手动清理旧缓存。

## 7. ChatGPT 界面出现钩子图标但无结果

钩子图标只代表命令被匹配或启动。真实结果以 `codex_bark_hook.log` 和 Bark 返回值为准。
