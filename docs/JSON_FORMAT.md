# JSON 数据格式

## 月度文件

文件名：

```text
Codex对话记录_YYYY-MM.json
```

顶层结构：

```json
{
  "schema_version": 1,
  "month": "2026-07",
  "updated_at": "2026-07-24T10:03:20+08:00",
  "record_count": 1,
  "records": []
}
```

每条 `records` 记录包含：

| 字段 | 含义 |
|---|---|
| `record_id` | `session_id:turn_id` 组成的唯一键 |
| `session_id` | Codex 对话标识 |
| `turn_id` | 当前轮次标识 |
| `project` | `cwd` 最后一级目录 |
| `conversation_title` | 根据同一对话首条问题生成的本地名称 |
| `cwd` | 当前工作目录 |
| `model` | 当前模型标识 |
| `permission_mode` | 当前权限模式 |
| `transcript_path` | Codex 提供的会话记录路径，可能为空 |
| `prompt_time` | 提问时间 |
| `response_time` | 回答完成时间 |
| `duration_seconds` | 从提问到回答结束的近似耗时 |
| `prompt_status` | `matched` 或 `missing` |
| `user_prompt` | 完整用户问题 |
| `assistant_response` | 完整 Codex 回答 |
| `stop_hook_active` | Stop 是否已触发过继续执行 |

## 对话索引

`Codex对话索引.json` 按 `session_id` 保存本地对话名称，使同一对话跨月份时仍沿用同一名称。

## 兼容性

`transcript_path` 指向的内部 transcript 格式不是稳定的钩子接口；本项目只把路径作为元数据保存，不解析其内容。
