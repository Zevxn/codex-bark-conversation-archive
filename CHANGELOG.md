# Changelog

All notable changes to this project will be documented in this file.

## 1.0.0 - 2026-07-24

- Added `UserPromptSubmit` and `Stop` event pairing by `session_id + turn_id`.
- Added optional Bark notifications.
- Added monthly JSON conversation archives.
- Added stable local conversation titles based on the first prompt.
- Added file locking and atomic JSON writes for concurrent Codex tasks.
- Added recovery for malformed Windows path backslashes in hook JSON.
- Added `config.local.json` support, with local values overriding script defaults.
- Added configurable internal-response filtering through
  `ignored_internal_response_keys`; matching single-key JSON responses such as
  `{"exclude": [...]}` and `{"suggestions": [...]}` are not archived or sent to Bark.
- Added manual Bark and archive test modes.
