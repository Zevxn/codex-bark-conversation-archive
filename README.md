# Codex Bark Conversation Archive

一个面向 Codex / ChatGPT 桌面应用的开源钩子脚本：在任务完成后向 iPhone 的 Bark 发送通知，并把用户问题与 Codex 回答按月保存为本地 JSON。

> 非官方项目，与 OpenAI、ChatGPT、Codex 和 Bark 的开发者不存在隶属或背书关系。

[English README](docs/README.en.md)

## 功能

- 监听 `UserPromptSubmit`，记录本轮用户问题；
- 监听 `Stop`，获取本轮 Codex 回答；
- 使用 `session_id + turn_id` 精确配对问题与回答；
- Bark 手机通知可独立启用或关闭；
- 本地月度 JSON 归档可独立启用或关闭；
- 运行日志可独立启用或关闭；
- 根据同一对话第一次提问生成稳定的本地对话名称；
- 保存项目、模型、权限模式、开始时间、完成时间与耗时；
- 支持多个 Codex 任务并行运行，使用文件锁和原子写入避免覆盖；
- 修复 Windows 路径中的非法 JSON 反斜杠；
- 自动过滤 Codex 启动阶段的内部 JSON 响应；
- 只使用 Python 标准库，无需安装第三方依赖。

## 内部消息过滤

Codex 启动时可能执行环境建议或安全筛选任务，并产生类似以下回答：

```json
{"exclude":[]}
```

```json
{"exclude":["某项内容"]}
```

```json
{"suggestions":[]}
```

```json
{"suggestions":[{"title":"某项建议"}]}
```

当整个回答是一个仅包含 `exclude` 或 `suggestions` 字段的 JSON 对象，并且字段值为列表时，无论列表为空还是包含内容，脚本都会直接忽略该轮响应：

- 不发送 Bark；
- 不写入月度对话记录；
- 删除可能产生的临时问题缓存。

被 Markdown JSON 代码块包裹的相同内容也会被过滤。

普通正文中只是提到这些字符串时不会被误过滤，例如：

```text
程序返回了 {"suggestions":[]}，说明当前没有建议。
```

## 工作流程

```text
用户提交问题
  ↓
UserPromptSubmit
  ↓
按 session_id + turn_id 临时保存问题
  ↓
Codex 生成回答
  ↓
Stop
  ↓
检查是否属于应过滤的内部 JSON 响应
  ├─ 是：删除临时缓存并结束
  └─ 否：继续处理
       ↓
合并问题、回答、项目、模型和时间
       ↓
写入月度 JSON（可选）
       ↓
发送 Bark 通知（可选）
       ↓
删除本轮临时缓存
```

## 前置条件

- Windows 上安装了 Python 3；
- PowerShell 中可以执行 `python --version`；
- ChatGPT 桌面应用中的 Codex 钩子功能可用；
- 使用 Bark 通知时，需要在 iPhone 上安装 Bark。

建议使用 Python 3.10 或更高版本。

## 快速开始

### 1. 下载项目

下载 Releases 中的 ZIP，或克隆仓库：

```powershell
git clone https://github.com/你的用户名/codex-bark-conversation-archive.git
cd codex-bark-conversation-archive
Copy-Item config.example.json config.local.json
```

### 2. 修改本地 JSON 配置

脚本运行时优先读取项目根目录中的：

```text
config.local.json
```

未在 JSON 中填写的字段会继续使用 `codex_bark_hook.py` 顶部的默认值。配置示例：

```json
{
  "bark_url": "https://api.day.app/你的设备Key",
  "enable_bark_notification": true,
  "enable_local_history": true,
  "enable_file_log": true,
  "history_dir": "~/CodexConversationArchive",
  "max_body_length": 1500,
  "max_prompt_in_notification": 500,
  "conversation_title_max_length": 36,
  "lock_timeout_seconds": 15.0,
  "lock_poll_interval_seconds": 0.1,
  "stale_lock_seconds": 120.0,
  "ignored_internal_response_keys": ["exclude", "suggestions"]
}
```

Windows 自定义目录建议使用正斜杠：

```json
"history_dir": "D:/CodexConversationArchive"
```

`config.local.json` 已在 `.gitignore` 中忽略，真实 Bark Key 不会被 Git 跟踪。

### 3. 功能开关

| Bark 通知 | 本地归档 | 效果 |
|---|---|---|
| `true` | `true` | 手机通知，同时写入月度 JSON |
| `true` | `false` | 只通知，不永久保存问答 |
| `false` | `true` | 只保存，不通知 |
| `false` | `false` | 两项功能均关闭 |

日志独立控制：

```json
"enable_file_log": true
```

表示写入：

```text
codex_bark_hook.log
```

如需关闭，改为：

```json
"enable_file_log": false
```

则不创建、不更新运行日志。

### 4. 配置 hooks.json

打开项目中的 `hooks.example.json`，把 `UserPromptSubmit` 和 `Stop` 两处命令里的脚本路径改为本项目的实际路径。Windows 路径建议使用 `/`：

```json
"commandWindows": "python \"D:/Tools/codex-bark-conversation-archive/codex_bark_hook.py\""
```

然后把修改后的 `hooks` 对象复制或合并到：

```text
%USERPROFILE%\.codex\hooks.json
```

如果 `hooks.json` 已有其他配置，请保留原内容并合并对应字段，不要直接覆盖整个文件。

### 5. 信任并启用钩子

重新打开 ChatGPT 桌面应用，进入 Codex 钩子设置页：

1. 刷新钩子列表；
2. 确认出现 `UserPromptSubmit` 和 `Stop`；
3. 分别审核、信任并启用两个钩子。

只启用 `Stop` 而未启用 `UserPromptSubmit` 时，归档中可能无法取得对应的用户问题。

### 6. 测试

检查语法：

```powershell
python -m py_compile codex_bark_hook.py
```

测试 Bark：

```powershell
python codex_bark_hook.py --test
```

测试完整的提问、回答、归档和通知链路：

```powershell
python codex_bark_hook.py --test-archive
```

测试完成后，可以在 `HISTORY_DIR` 指定的目录中检查月度记录。

## 输出结构

假设 `config.local.json` 中配置：

```json
"history_dir": "~/CodexConversationArchive"
```

运行数据大致如下：

```text
CodexConversationArchive
├─ Codex对话记录_2026-07.json
├─ Codex对话记录_2026-08.json
├─ Codex对话索引.json
├─ .prompt_cache
└─ .locks
```

脚本目录中可能产生：

```text
codex_bark_hook.log
```

当 `enable_file_log` 为 `false` 时不会写入该日志。

## 月度对话记录

完整问答仍然按回答完成月份分别保存：

```text
Codex对话记录_YYYY-MM.json
```

例如：

```text
Codex对话记录_2026-07.json
Codex对话记录_2026-08.json
```

同一月份中的多轮问答保存在对应文件的 `records` 列表中。

单条记录示例：

```json
{
  "record_id": "session-id:turn-id",
  "session_id": "session-id",
  "turn_id": "turn-id",
  "project": "项目名称",
  "conversation_title": "根据首条问题生成的本地名称",
  "cwd": "D:\\Projects\\example",
  "model": "模型名称",
  "permission_mode": "default",
  "prompt_time": "2026-07-24T10:00:00+08:00",
  "response_time": "2026-07-24T10:03:20+08:00",
  "duration_seconds": 200.0,
  "prompt_status": "matched",
  "user_prompt": "用户问题",
  "assistant_response": "Codex 回答",
  "stop_hook_active": false
}
```

## Codex 对话索引

```text
Codex对话索引.json
```

不是完整问答文件。它用于保存：

```text
session_id
→ 本地对话名称
→ 项目名称
→ 工作目录
→ 创建时间
→ 最近更新时间
```

同一个 Codex 对话跨越月份时，脚本仍可根据 `session_id` 使用原来的本地对话名称。

本地 `conversation_title` 根据同一 `session_id` 的第一次提问生成，不一定与 ChatGPT 左侧栏中的标题相同。

删除索引不会删除已经保存的月度问答，但旧对话再次运行时可能重新生成不同的本地标题。

## 配置项

| JSON 字段 | 默认值 | 说明 |
|---|---:|---|
| `bark_url` | 占位地址 | Bark 完整推送地址 |
| `enable_bark_notification` | `true` | 是否发送 Bark 通知 |
| `enable_local_history` | `true` | 是否写入月度 JSON |
| `enable_file_log` | `true` | 是否写入运行日志 |
| `history_dir` | `~/CodexConversationArchive` | 本地归档目录 |
| `max_body_length` | `1500` | Bark 正文最大字符数 |
| `max_prompt_in_notification` | `500` | Bark 中问题最大字符数 |
| `conversation_title_max_length` | `36` | 本地对话名称最大长度 |
| `lock_timeout_seconds` | `15.0` | 等待文件锁的最长时间 |
| `lock_poll_interval_seconds` | `0.1` | 文件锁轮询间隔 |
| `stale_lock_seconds` | `120.0` | 自动清理超时锁的阈值 |
| `ignored_internal_response_keys` | `["exclude", "suggestions"]` | 需要过滤的内部响应字段 |



## 常见问题

### 有钩子图标，但手机没有通知

钩子图标只表示脚本被调用，不代表 Bark 请求成功。

确认 `config.local.json` 中：

```json
"enable_bark_notification": true
```

并检查 `bark_url` 是否填写正确。

日志开启时可以查看：

```powershell
Get-Content codex_bark_hook.log -Tail 40
```

### 为什么启动 Codex 时没有收到某些通知

脚本会主动过滤以下纯 JSON 响应：

```json
{"exclude":[...]}
```

```json
{"suggestions":[...]}
```

这是预期行为。这些响应不会归档，也不会发送 Bark。

### 归档中没有用户问题

确认 `UserPromptSubmit` 已经审核、信任并启用，并检查是否先于 `Stop` 正常触发。

### 出现 `Invalid \escape`

这通常是 Windows 路径中的反斜杠造成的 JSON 转义问题。脚本会尝试修复 Codex 钩子输入，但 `hooks.json` 中仍建议使用 `/`。

### 多个任务同时执行会不会串台

脚本使用 `(session_id, turn_id)` 配对问题和回答。每轮问题缓存相互独立，并通过文件锁保护月度 JSON。

### `.prompt_cache` 会自动清空吗

正常完成且所有已启用输出均成功后，该轮缓存会被删除。

异常中断或归档、通知失败时，缓存可能保留，用于排查问题。

### `Codex对话索引.json` 是否按月保存

不是。它是跨月份使用的长期索引。

完整问答仍然按月保存在：

```text
Codex对话记录_YYYY-MM.json
```

## 本地对话查看面板

Windows 用户可以直接双击：

```text
启动对话面板.cmd
```

脚本会启动本地服务并打开浏览器。若 `config.local.json` 中的 `history_dir` 有效且包含月度对话记录，面板会自动加载；否则停留在手动选择界面。自动加载的数据源支持把完整对话移入归档目录下的本地 `.trash` 回收站，并可从看板中恢复。完整说明见[本地对话查看面板](docs/VIEWER.md)。

## 文档

- [本地对话查看面板](docs/VIEWER.md)
- [故障排查](docs/TROUBLESHOOTING.md)
- [JSON 数据格式](docs/JSON_FORMAT.md)

## 许可证

本项目采用 [MIT License](LICENSE)。
