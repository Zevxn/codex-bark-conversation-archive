# Codex Bark Conversation Archive

An unofficial Codex lifecycle hook that sends task-completion notifications to
Bark on iPhone and archives paired user prompts and Codex responses into monthly
JSON files.

## Highlights

- Pairs `UserPromptSubmit` and `Stop` by `session_id + turn_id`.
- Optional Bark notifications and optional local archives.
- Stable local conversation titles derived from the first prompt.
- Monthly JSON files with project, model, timing, prompt, and response data.
- File locking and atomic writes for concurrent Codex tasks.
- Windows malformed-backslash recovery.
- Python standard library only.

## Quick start

```powershell
git clone https://github.com/YOUR_USERNAME/codex-bark-conversation-archive.git
cd codex-bark-conversation-archive
Copy-Item config.example.json config.local.json
```

Edit `config.local.json`. Next, open `hooks.example.json` and replace both
sample script paths with the absolute path to your local `codex_bark_hook.py`.
On Windows, use forward slashes in the JSON path, for example:

```json
"commandWindows": "python \"D:/Tools/codex-bark-conversation-archive/codex_bark_hook.py\""
```

Merge the `hooks` object from the edited example into
`%USERPROFILE%\.codex\hooks.json`. Preserve any existing hook configuration
instead of overwriting the whole file. Restart the ChatGPT desktop app, then
trust and enable both `UserPromptSubmit` and `Stop`.

Test Bark:

```powershell
python codex_bark_hook.py --test
```

Test the full archive flow:

```powershell
python codex_bark_hook.py --test-archive
```

Never commit `config.local.json`, a real Bark key, logs, caches, or conversation
archives. See [README.md](../README.md) for the complete Chinese guide.

## License

MIT License.
