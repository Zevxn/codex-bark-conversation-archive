from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Iterator
from urllib.request import Request, urlopen


APP_NAME = "Codex Bark Conversation Archive"
APP_VERSION = "1.0.0"
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.local.json"
LOG_FILE = SCRIPT_DIR / "codex_bark_hook.log"

# ---------------------------------------------------------------------------
# 默认配置区
# ---------------------------------------------------------------------------
# 运行时优先使用 config.local.json 中的值；未配置的字段使用下面的默认值。

# Bark App 中复制的完整推送地址。
BARK_URL = "https://api.day.app/请替换为你的设备Key"

# 是否发送 Bark 手机通知。
ENABLE_BARK_NOTIFICATION = True

# 是否把“用户问题 + Codex 回答”写入本地月度 JSON。
ENABLE_LOCAL_HISTORY = True

# 是否写入 codex_bark_hook.log 运行日志。
ENABLE_FILE_LOG = True

# 对话记录保存目录。
HISTORY_DIR = Path.home() / "CodexConversationArchive"

# Bark 通知正文最大字符数。
MAX_BODY_LENGTH = 1500

# Bark 通知中最多保留多少字符的用户问题。
MAX_PROMPT_IN_NOTIFICATION = 500

# 根据首条问题生成的本地对话名称最大长度。
CONVERSATION_TITLE_MAX_LENGTH = 36

# 多任务并发写入 JSON 时的文件锁参数。
LOCK_TIMEOUT_SECONDS = 15.0
LOCK_POLL_INTERVAL_SECONDS = 0.1
STALE_LOCK_SECONDS = 120.0

# 忽略 Codex 启动阶段产生的内部 JSON 响应。
# 当回答是 {"exclude": [...]} 或 {"suggestions": [...]} 时，
# 无论列表为空还是包含内容，都不归档、不发送 Bark。
IGNORED_INTERNAL_RESPONSE_KEYS = {"exclude", "suggestions"}

DEFAULT_CONFIG: dict[str, Any] = {
    "bark_url": BARK_URL,
    "enable_bark_notification": ENABLE_BARK_NOTIFICATION,
    "enable_local_history": ENABLE_LOCAL_HISTORY,
    "enable_file_log": ENABLE_FILE_LOG,
    "history_dir": str(HISTORY_DIR),
    "max_body_length": MAX_BODY_LENGTH,
    "max_prompt_in_notification": MAX_PROMPT_IN_NOTIFICATION,
    "conversation_title_max_length": CONVERSATION_TITLE_MAX_LENGTH,
    "lock_timeout_seconds": LOCK_TIMEOUT_SECONDS,
    "lock_poll_interval_seconds": LOCK_POLL_INTERVAL_SECONDS,
    "stale_lock_seconds": STALE_LOCK_SECONDS,
    "ignored_internal_response_keys": sorted(IGNORED_INTERNAL_RESPONSE_KEYS),
}


def load_configuration(config_path: Path = CONFIG_FILE) -> dict[str, Any]:
    """读取并校验本地 JSON 配置，未提供的字段保留脚本默认值。"""
    config = dict(DEFAULT_CONFIG)

    if not config_path.exists():
        return config

    try:
        local_config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"无法读取配置文件 {config_path}：{error}") from error

    if not isinstance(local_config, dict):
        raise TypeError(f"配置文件 {config_path} 的顶层必须是 JSON 对象。")

    unknown_keys = sorted(set(local_config) - set(config))
    if unknown_keys:
        raise ValueError(f"配置文件包含未知字段：{', '.join(unknown_keys)}")

    boolean_keys = {
        "enable_bark_notification",
        "enable_local_history",
        "enable_file_log",
    }
    positive_integer_keys = {
        "max_body_length",
        "max_prompt_in_notification",
        "conversation_title_max_length",
    }
    positive_number_keys = {
        "lock_timeout_seconds",
        "lock_poll_interval_seconds",
        "stale_lock_seconds",
    }

    for key, value in local_config.items():
        if key in boolean_keys and not isinstance(value, bool):
            raise TypeError(f"配置项 {key} 必须是布尔值。")
        if key in positive_integer_keys and (
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
        ):
            raise TypeError(f"配置项 {key} 必须是正整数。")
        if key in positive_number_keys and (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or value <= 0
        ):
            raise TypeError(f"配置项 {key} 必须是正数。")
        if key in {"bark_url", "history_dir"} and (
            not isinstance(value, str) or not value.strip()
        ):
            raise TypeError(f"配置项 {key} 必须是非空字符串。")
        if key == "ignored_internal_response_keys" and (
            not isinstance(value, list)
            or not all(isinstance(item, str) and item for item in value)
        ):
            raise TypeError(
                "配置项 ignored_internal_response_keys "
                "必须是由非空字符串组成的数组。"
            )

        config[key] = value

    return config


CONFIG_LOAD_ERROR: str | None = None
try:
    _CONFIG = load_configuration()
except (OSError, RuntimeError, TypeError, ValueError) as error:
    CONFIG_LOAD_ERROR = f"{type(error).__name__}: {error}"
    _CONFIG = dict(DEFAULT_CONFIG)

BARK_URL = str(_CONFIG["bark_url"])
ENABLE_BARK_NOTIFICATION = bool(_CONFIG["enable_bark_notification"])
ENABLE_LOCAL_HISTORY = bool(_CONFIG["enable_local_history"])
ENABLE_FILE_LOG = bool(_CONFIG["enable_file_log"])
HISTORY_DIR = Path(str(_CONFIG["history_dir"])).expanduser()
if not HISTORY_DIR.is_absolute():
    HISTORY_DIR = SCRIPT_DIR / HISTORY_DIR
MAX_BODY_LENGTH = int(_CONFIG["max_body_length"])
MAX_PROMPT_IN_NOTIFICATION = int(_CONFIG["max_prompt_in_notification"])
CONVERSATION_TITLE_MAX_LENGTH = int(_CONFIG["conversation_title_max_length"])
LOCK_TIMEOUT_SECONDS = float(_CONFIG["lock_timeout_seconds"])
LOCK_POLL_INTERVAL_SECONDS = float(_CONFIG["lock_poll_interval_seconds"])
STALE_LOCK_SECONDS = float(_CONFIG["stale_lock_seconds"])
IGNORED_INTERNAL_RESPONSE_KEYS = set(_CONFIG["ignored_internal_response_keys"])


# 运行数据目录。
CACHE_DIR = HISTORY_DIR / ".prompt_cache"
LOCK_DIR = HISTORY_DIR / ".locks"
CONVERSATION_INDEX_PATH = HISTORY_DIR / "Codex对话索引.json"

# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def now_local() -> datetime:
    """返回带本地时区的当前时间。"""
    return datetime.now().astimezone()


def format_time(value: datetime) -> str:
    """生成适合 JSON 保存的 ISO 8601 时间。"""
    return value.isoformat(timespec="seconds")


def format_display_time(value: datetime) -> str:
    """生成适合 Bark 正文显示的时间。"""
    return value.strftime("%Y-%m-%d %H:%M:%S")


def write_log(level: str, message: str) -> None:
    """按配置写入运行日志，不向 Codex 标准输出输出普通文本。"""
    if not ENABLE_FILE_LOG:
        return

    try:
        with LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(
                f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
                f"[{level}] {message}\n"
            )
    except Exception:
        # 日志失败不能影响 Codex 正常执行。
        pass


def emit_hook_result(payload: dict[str, Any] | None = None) -> None:
    """向 Codex 返回合法 JSON，并强制使用 UTF-8。"""
    result = payload if payload is not None else {}
    output = json.dumps(result, ensure_ascii=False) + "\n"

    try:
        sys.stdout.buffer.write(output.encode("utf-8"))
        sys.stdout.buffer.flush()
    except Exception:
        fallback = json.dumps(result, ensure_ascii=True) + "\n"
        sys.stdout.write(fallback)
        sys.stdout.flush()


def clean_single_line(value: object) -> str:
    """清理单行内容，用于事件名称、模型名称和项目名称。"""
    return " ".join(str(value or "").split())


def clean_multiline_text(value: object) -> str:
    """保留正文换行和列表结构，并压缩连续空行。"""
    text = str(value or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    cleaned_lines: list[str] = []
    previous_line_is_empty = False

    for original_line in text.split("\n"):
        line = original_line.rstrip()

        if line.strip():
            cleaned_lines.append(line)
            previous_line_is_empty = False
        elif not previous_line_is_empty:
            cleaned_lines.append("")
            previous_line_is_empty = True

    return "\n".join(cleaned_lines).strip()


def truncate_text(text: str, max_length: int) -> str:
    """按字符数截断文本并添加省略号。"""
    if max_length <= 0:
        return ""
    if len(text) <= max_length:
        return text
    if max_length == 1:
        return "…"
    return text[: max_length - 1].rstrip() + "…"


def is_ignored_internal_response(value: object) -> bool:
    """识别不应归档或通知的 Codex 启动阶段内部 JSON 响应。

    仅屏蔽以下两种单字段 JSON 对象，列表是否为空不影响判断：
    {"exclude": [...]}
    {"suggestions": [...]}

    普通正文中偶然包含这些字符串不会被屏蔽。
    """
    text = clean_multiline_text(value)
    if not text:
        return False

    # 兼容回答被 Markdown JSON 代码块包裹的情形。
    fenced_match = re.fullmatch(
        r"```(?:json)?\s*\n?(.*?)\n?```",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced_match:
        text = fenced_match.group(1).strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False

    if not isinstance(data, dict) or len(data) != 1:
        return False

    key, list_value = next(iter(data.items()))
    return (
        key in IGNORED_INTERNAL_RESPONSE_KEYS
        and isinstance(list_value, list)
    )


def restore_path_control_characters(value: str) -> str:
    """修复 Windows 路径中被 JSON 转义误解析的控制字符。"""
    replacements = {
        "\b": r"\b",
        "\f": r"\f",
        "\n": r"\n",
        "\r": r"\r",
        "\t": r"\t",
    }

    for control_character, replacement in replacements.items():
        value = value.replace(control_character, replacement)

    return value


def repair_invalid_json_backslashes(text: str) -> str:
    """修复 JSON 字符串内部未经转义的 Windows 路径反斜杠。"""
    valid_simple_escapes = {'"', "\\", "/", "b", "f", "n", "r", "t"}
    hexadecimal_characters = set("0123456789abcdefABCDEF")

    output: list[str] = []
    index = 0
    inside_string = False

    while index < len(text):
        character = text[index]

        if not inside_string:
            output.append(character)
            if character == '"':
                inside_string = True
            index += 1
            continue

        if character == '"':
            output.append(character)
            inside_string = False
            index += 1
            continue

        if character != "\\":
            output.append(character)
            index += 1
            continue

        if index + 1 >= len(text):
            output.append("\\\\")
            index += 1
            continue

        next_character = text[index + 1]

        if next_character in valid_simple_escapes:
            output.append("\\")
            output.append(next_character)
            index += 2
            continue

        if (
            next_character == "u"
            and index + 5 < len(text)
            and all(
                character in hexadecimal_characters
                for character in text[index + 2 : index + 6]
            )
        ):
            output.append(text[index : index + 6])
            index += 6
            continue

        output.append("\\\\")
        index += 1

    return "".join(output)


def load_hook_event(raw_input: str) -> dict[str, Any]:
    """解析 Codex 事件；失败时自动修复 Windows 路径反斜杠。"""
    try:
        event = json.loads(raw_input)
    except json.JSONDecodeError as first_error:
        context_start = max(0, first_error.pos - 120)
        context_end = min(len(raw_input), first_error.pos + 120)
        error_context = raw_input[context_start:context_end]

        write_log(
            "WARNING",
            "首次解析钩子 JSON 失败，将尝试修复 Windows 路径反斜杠。"
            f"错误：{first_error}；附近内容：{error_context!r}",
        )

        repaired_input = repair_invalid_json_backslashes(raw_input)

        try:
            event = json.loads(repaired_input)
        except json.JSONDecodeError as second_error:
            raise RuntimeError(
                "自动修复反斜杠后仍无法解析 Codex 钩子输入："
                f"{second_error}"
            ) from second_error

    if not isinstance(event, dict):
        raise TypeError(
            "Codex 钩子输入应当是 JSON 对象，实际类型为："
            f"{type(event).__name__}"
        )

    return event


def read_hook_input() -> str:
    """从标准输入读取 Codex 传入的 JSON。"""
    raw_bytes = sys.stdin.buffer.read()

    if raw_bytes:
        return raw_bytes.decode("utf-8-sig", errors="replace")

    if len(sys.argv) >= 2 and not sys.argv[1].startswith("--"):
        return sys.argv[1]

    return ""


def ensure_data_directories() -> None:
    """创建归档、缓存和锁目录。"""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_DIR.mkdir(parents=True, exist_ok=True)


def safe_identifier(value: object, fallback: str) -> str:
    """将 session_id/turn_id 转为可安全用作文件名的字符串。"""
    text = clean_single_line(value) or fallback
    text = re.sub(r"[^0-9A-Za-z._-]+", "_", text)
    return text[:180] or fallback


def get_session_id(event: dict[str, Any]) -> str:
    return clean_single_line(event.get("session_id")) or "unknown_session"


def get_turn_id(event: dict[str, Any]) -> str:
    return clean_single_line(event.get("turn_id")) or "unknown_turn"


def get_event_name(event: dict[str, Any]) -> str:
    value = (
        event.get("hook_event_name")
        or event.get("hookEventName")
        or event.get("event_name")
        or event.get("eventName")
        or event.get("type")
        or ""
    )
    return clean_single_line(value)


def get_cwd(event: dict[str, Any]) -> str:
    value = (
        event.get("cwd")
        or event.get("working_directory")
        or event.get("workingDirectory")
        or ""
    )
    return restore_path_control_characters(clean_single_line(value))


def get_project_name(event: dict[str, Any]) -> str:
    cwd = get_cwd(event).rstrip("\\/")
    if not cwd:
        return "Codex"

    try:
        project_name = PureWindowsPath(cwd).name
        if project_name:
            return project_name
    except Exception:
        pass

    try:
        project_name = Path(cwd).name
        if project_name:
            return project_name
    except Exception:
        pass

    return "Codex"


def get_model_name(event: dict[str, Any]) -> str:
    value = (
        event.get("model")
        or event.get("model_name")
        or event.get("modelName")
        or ""
    )
    return clean_single_line(value) or "未知模型"


def get_permission_mode(event: dict[str, Any]) -> str:
    value = (
        event.get("permission_mode")
        or event.get("permissionMode")
        or ""
    )
    return clean_single_line(value)


def get_transcript_path(event: dict[str, Any]) -> str | None:
    value = event.get("transcript_path") or event.get("transcriptPath")
    if value is None:
        return None
    text = restore_path_control_characters(clean_single_line(value))
    return text or None


def get_user_prompt(event: dict[str, Any]) -> str:
    value = (
        event.get("prompt")
        or event.get("user_prompt")
        or event.get("userPrompt")
        or ""
    )
    return clean_multiline_text(value)


def get_assistant_message(event: dict[str, Any]) -> str:
    value = (
        event.get("last_assistant_message")
        or event.get("last-assistant-message")
        or event.get("lastAssistantMessage")
        or event.get("assistant_message")
        or event.get("message")
        or ""
    )
    message = clean_multiline_text(value)
    return message or "Codex 当前任务轮次已经结束。"


def cache_file_for_event(event: dict[str, Any]) -> Path:
    session_id = safe_identifier(get_session_id(event), "unknown_session")
    turn_id = safe_identifier(get_turn_id(event), "unknown_turn")
    return CACHE_DIR / f"{session_id}__{turn_id}.json"


def atomic_write_json(path: Path, data: Any) -> None:
    """先写临时文件再原子替换，避免中途损坏正式 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")

    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


@contextmanager
def exclusive_file_lock(lock_path: Path) -> Iterator[None]:
    """使用排他创建锁文件，防止多个 Stop 同时覆盖月度 JSON。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    file_descriptor: int | None = None

    while True:
        try:
            file_descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            os.write(
                file_descriptor,
                f"pid={os.getpid()}\ntime={format_time(now_local())}\n".encode("utf-8"),
            )
            break
        except FileExistsError:
            try:
                lock_age = time.time() - lock_path.stat().st_mtime
                if lock_age > STALE_LOCK_SECONDS:
                    lock_path.unlink(missing_ok=True)
                    write_log("WARNING", f"删除超时锁文件：{lock_path}")
                    continue
            except OSError:
                pass

            if time.monotonic() >= deadline:
                raise TimeoutError(f"等待文件锁超时：{lock_path}")
            time.sleep(LOCK_POLL_INTERVAL_SECONDS)

    try:
        yield
    finally:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 提问缓存、对话名称与月度归档
# ---------------------------------------------------------------------------

def generate_conversation_title(prompt: str) -> str:
    """根据该对话第一次提交的问题生成稳定的本地对话名称。"""
    for raw_line in clean_multiline_text(prompt).splitlines():
        line = clean_single_line(raw_line)
        if not line:
            continue

        # 去掉常见 Markdown 标记或编号，使标题更自然。
        line = re.sub(r"^(?:[#>*+-]+\s*|\d+[.)、]\s*)+", "", line).strip()
        if line:
            return truncate_text(line, CONVERSATION_TITLE_MAX_LENGTH)

    return "未命名对话"


def read_conversation_index() -> dict[str, Any]:
    """读取本地对话名称索引；文件不存在时返回空结构。"""
    if not CONVERSATION_INDEX_PATH.exists():
        return {
            "schema_version": 1,
            "updated_at": None,
            "sessions": {},
        }

    try:
        document = json.loads(
            CONVERSATION_INDEX_PATH.read_text(encoding="utf-8-sig")
        )
    except Exception as error:
        raise RuntimeError(
            f"读取对话名称索引失败：{CONVERSATION_INDEX_PATH}；{error}"
        ) from error

    if not isinstance(document, dict):
        raise TypeError("对话名称索引必须是 JSON 对象。")

    sessions = document.get("sessions")
    if not isinstance(sessions, dict):
        document["sessions"] = {}

    return document


def get_or_create_conversation_title(
    event: dict[str, Any],
    prompt: str,
    prompt_time: datetime,
) -> str:
    """按 session_id 创建或读取本地对话名称，跨月份保持稳定。"""
    ensure_data_directories()
    session_id = get_session_id(event)
    lock_path = LOCK_DIR / "Codex对话索引.lock"

    with exclusive_file_lock(lock_path):
        document = read_conversation_index()
        sessions = document["sessions"]
        existing = sessions.get(session_id)

        if isinstance(existing, dict):
            title = clean_single_line(existing.get("conversation_title"))
            if title:
                existing["updated_at"] = format_time(prompt_time)
                existing["project"] = get_project_name(event)
                existing["cwd"] = get_cwd(event)
                document["updated_at"] = format_time(prompt_time)
                atomic_write_json(CONVERSATION_INDEX_PATH, document)
                return title

        title = generate_conversation_title(prompt)
        sessions[session_id] = {
            "session_id": session_id,
            "conversation_title": title,
            "project": get_project_name(event),
            "cwd": get_cwd(event),
            "created_at": format_time(prompt_time),
            "updated_at": format_time(prompt_time),
        }
        document["schema_version"] = 1
        document["updated_at"] = format_time(prompt_time)
        atomic_write_json(CONVERSATION_INDEX_PATH, document)
        return title


def lookup_conversation_title(event: dict[str, Any]) -> str | None:
    """根据 session_id 从本地索引中读取对话名称。"""
    try:
        document = read_conversation_index()
        sessions = document.get("sessions", {})
        existing = sessions.get(get_session_id(event))
        if isinstance(existing, dict):
            title = clean_single_line(existing.get("conversation_title"))
            return title or None
    except Exception as error:
        write_log("WARNING", f"读取对话名称失败：{error}")

    return None


def save_prompt_cache(event: dict[str, Any], prompt_time: datetime) -> Path:
    """一轮一个文件保存用户问题，防止并行任务相互覆盖。"""
    ensure_data_directories()
    cache_path = cache_file_for_event(event)
    prompt = get_user_prompt(event)
    conversation_title = get_or_create_conversation_title(
        event,
        prompt,
        prompt_time,
    )

    cache_record = {
        "schema_version": 1,
        "session_id": get_session_id(event),
        "turn_id": get_turn_id(event),
        "project": get_project_name(event),
        "conversation_title": conversation_title,
        "cwd": get_cwd(event),
        "model_at_submit": get_model_name(event),
        "permission_mode_at_submit": get_permission_mode(event),
        "transcript_path": get_transcript_path(event),
        "prompt_time": format_time(prompt_time),
        "user_prompt": prompt,
    }

    atomic_write_json(cache_path, cache_record)
    return cache_path


def load_prompt_cache(event: dict[str, Any]) -> tuple[dict[str, Any] | None, Path]:
    ensure_data_directories()
    cache_path = cache_file_for_event(event)

    if not cache_path.exists():
        return None, cache_path

    try:
        data = json.loads(cache_path.read_text(encoding="utf-8-sig"))
    except Exception as error:
        raise RuntimeError(f"读取用户问题缓存失败：{cache_path}；{error}") from error

    if not isinstance(data, dict):
        raise TypeError(f"用户问题缓存不是 JSON 对象：{cache_path}")

    return data, cache_path


def parse_iso_time(value: object) -> datetime | None:
    text = clean_single_line(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def build_conversation_record(
    event: dict[str, Any],
    prompt_cache: dict[str, Any] | None,
    response_time: datetime,
) -> dict[str, Any]:
    """合并 UserPromptSubmit 和 Stop 数据，形成一条完整问答记录。"""
    prompt_cache = prompt_cache or {}

    prompt_time_text = clean_single_line(prompt_cache.get("prompt_time")) or None
    prompt_time = parse_iso_time(prompt_time_text)
    duration_seconds: float | None = None

    if prompt_time is not None:
        try:
            duration_seconds = max(
                0.0,
                round((response_time - prompt_time).total_seconds(), 3),
            )
        except TypeError:
            duration_seconds = None

    user_prompt = clean_multiline_text(prompt_cache.get("user_prompt"))
    prompt_status = "matched" if user_prompt else "missing"
    conversation_title = (
        clean_single_line(prompt_cache.get("conversation_title"))
        or lookup_conversation_title(event)
        or generate_conversation_title(user_prompt)
    )

    return {
        "record_id": f"{get_session_id(event)}:{get_turn_id(event)}",
        "session_id": get_session_id(event),
        "turn_id": get_turn_id(event),
        "project": get_project_name(event),
        "conversation_title": conversation_title,
        "cwd": get_cwd(event),
        "model": get_model_name(event),
        "permission_mode": get_permission_mode(event),
        "transcript_path": get_transcript_path(event),
        "prompt_time": prompt_time_text,
        "response_time": format_time(response_time),
        "duration_seconds": duration_seconds,
        "prompt_status": prompt_status,
        "user_prompt": user_prompt or "未获取到用户问题（未找到对应的 UserPromptSubmit 缓存）。",
        "assistant_response": get_assistant_message(event),
        "stop_hook_active": bool(event.get("stop_hook_active", False)),
    }


def monthly_history_path(response_time: datetime) -> Path:
    month = response_time.strftime("%Y-%m")
    return HISTORY_DIR / f"Codex对话记录_{month}.json"


def append_monthly_history(
    record: dict[str, Any],
    response_time: datetime,
) -> Path:
    """将问答追加到月度 JSON；同一轮重复触发时更新而不是重复添加。"""
    ensure_data_directories()
    history_path = monthly_history_path(response_time)
    month = response_time.strftime("%Y-%m")
    lock_path = LOCK_DIR / f"Codex对话记录_{month}.lock"

    with exclusive_file_lock(lock_path):
        if history_path.exists():
            try:
                document = json.loads(history_path.read_text(encoding="utf-8-sig"))
            except Exception as error:
                backup_path = history_path.with_name(
                    f"{history_path.stem}_损坏备份_{response_time:%Y%m%d_%H%M%S}.json"
                )
                try:
                    os.replace(history_path, backup_path)
                except OSError:
                    pass
                write_log(
                    "ERROR",
                    f"月度 JSON 无法读取，已尝试备份后重建：{history_path}；{error}",
                )
                document = None
        else:
            document = None

        if not isinstance(document, dict):
            document = {
                "schema_version": 1,
                "month": month,
                "updated_at": format_time(response_time),
                "record_count": 0,
                "records": [],
            }

        records = document.get("records")
        if not isinstance(records, list):
            records = []
            document["records"] = records

        record_id = record.get("record_id")
        replaced = False

        for index, existing in enumerate(records):
            if isinstance(existing, dict) and existing.get("record_id") == record_id:
                merged_record = dict(existing)
                merged_record.update(record)

                # 同一 Stop 事件若被重复触发，而问题缓存已经在第一次归档后删除，
                # 不应让“未获取到问题”覆盖第一次已经正确保存的问题。
                if (
                    record.get("prompt_status") == "missing"
                    and existing.get("prompt_status") == "matched"
                ):
                    for field in (
                        "prompt_status",
                        "prompt_time",
                        "duration_seconds",
                        "user_prompt",
                    ):
                        merged_record[field] = existing.get(field)

                records[index] = merged_record
                replaced = True
                break

        if not replaced:
            records.append(record)

        document["schema_version"] = 1
        document["month"] = month
        document["updated_at"] = format_time(response_time)
        document["record_count"] = len(records)

        atomic_write_json(history_path, document)

    return history_path


# ---------------------------------------------------------------------------
# Bark 通知
# ---------------------------------------------------------------------------

def validate_bark_url() -> None:
    url = BARK_URL.strip()
    if not url.startswith(("https://", "http://")):
        raise ValueError("BARK_URL 不是有效的 HTTP 或 HTTPS 地址。")
    if (
        "请替换" in url
        or "xxxxxxxx" in url
        or url.rstrip("/") == "https://api.day.app"
    ):
        raise ValueError("请在脚本顶部的 BARK_URL 中填写真实 Bark 设备地址。")


def send_bark(title: str, body: str) -> None:
    validate_bark_url()

    payload = {
        "title": title,
        "body": body,
        "group": "Codex",
        "sound": "glass",
        "level": "active",
    }

    request = Request(
        BARK_URL.rstrip("/"),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": f"{APP_NAME}/{APP_VERSION}",
        },
        method="POST",
    )

    with urlopen(request, timeout=15) as response:
        status_code = getattr(response, "status", 200)
        response_body = response.read().decode("utf-8", errors="replace")

        if not 200 <= status_code < 300:
            raise RuntimeError(f"Bark 服务器返回 HTTP {status_code}。")

    if response_body.strip():
        try:
            response_data = json.loads(response_body)
        except json.JSONDecodeError:
            response_data = None

        if isinstance(response_data, dict):
            bark_code = response_data.get("code")
            if bark_code is not None and str(bark_code) != "200":
                bark_message = response_data.get("message", "")
                raise RuntimeError(
                    f"Bark 返回失败：code={bark_code}，message={bark_message}"
                )


def build_bark_body(record: dict[str, Any], response_time: datetime) -> str:
    """在有限通知长度内优先保留模型、问题和回答开头。"""
    model_name = clean_single_line(record.get("model")) or "未知模型"
    project_name = clean_single_line(record.get("project")) or "Codex"
    conversation_title = (
        clean_single_line(record.get("conversation_title")) or "未命名对话"
    )
    user_prompt = clean_multiline_text(record.get("user_prompt"))
    assistant_response = clean_multiline_text(record.get("assistant_response"))

    prompt_for_notification = truncate_text(
        user_prompt,
        MAX_PROMPT_IN_NOTIFICATION,
    )

    prefix = (
        f"项目：{project_name}\n"
        f"对话：{conversation_title}\n"
        f"模型：{model_name}\n"
        f"完成时间：{format_display_time(response_time)}\n\n"
        f"用户问题：\n{prompt_for_notification}\n\n"
        f"Codex 回答：\n"
    )

    remaining = max(0, MAX_BODY_LENGTH - len(prefix))
    answer_for_notification = truncate_text(assistant_response, remaining)
    return prefix + answer_for_notification


# ---------------------------------------------------------------------------
# 两类钩子事件
# ---------------------------------------------------------------------------

def any_output_enabled() -> bool:
    """只要通知或永久归档任一开启，就需要保存本轮问题缓存。"""
    return ENABLE_BARK_NOTIFICATION or ENABLE_LOCAL_HISTORY


def handle_user_prompt_submit(event: dict[str, Any]) -> int:
    if not any_output_enabled():
        write_log(
            "INFO",
            "Bark 通知和本地对话记录均已关闭，跳过用户问题缓存。",
        )
        emit_hook_result()
        return 0

    prompt_time = now_local()
    prompt = get_user_prompt(event)

    if not prompt:
        write_log("WARNING", "UserPromptSubmit 事件中没有 prompt 内容。")

    cache_path = save_prompt_cache(event, prompt_time)
    write_log(
        "INFO",
        "已记录用户问题："
        f"session={get_session_id(event)}；turn={get_turn_id(event)}；"
        f"cache={cache_path}",
    )
    emit_hook_result()
    return 0


def handle_stop(event: dict[str, Any]) -> int:
    response_time = now_local()
    cache_path = cache_file_for_event(event)
    assistant_message = get_assistant_message(event)

    if is_ignored_internal_response(assistant_message):
        # 启动阶段内部响应不属于正常问答。删除可能存在的临时问题缓存，
        # 并在任何月度归档或 Bark 通知操作之前结束处理。
        try:
            cache_path.unlink(missing_ok=True)
        except OSError as error:
            write_log(
                "WARNING",
                f"删除被忽略内部响应的问题缓存失败：{cache_path}；{error}",
            )

        write_log(
            "INFO",
            "已忽略 Codex 内部 JSON 响应，不归档且不发送 Bark："
            f"session={get_session_id(event)}；turn={get_turn_id(event)}；"
            f"response={truncate_text(assistant_message, 300)}",
        )
        emit_hook_result()
        return 0

    if not any_output_enabled():
        # 如果用户在本轮执行期间才关闭两个功能，顺手删除可能已经生成的缓存。
        try:
            cache_path.unlink(missing_ok=True)
        except OSError as error:
            write_log("WARNING", f"删除问题缓存失败：{cache_path}；{error}")

        write_log(
            "INFO",
            "Bark 通知和本地对话记录均已关闭，Stop 事件不执行输出。",
        )
        emit_hook_result()
        return 0

    prompt_cache: dict[str, Any] | None = None
    errors: list[str] = []
    enabled_operations_succeeded = True

    try:
        prompt_cache, cache_path = load_prompt_cache(event)
        if prompt_cache is None:
            write_log(
                "WARNING",
                "未找到对应的用户问题缓存："
                f"session={get_session_id(event)}；turn={get_turn_id(event)}",
            )
    except Exception as error:
        error_message = f"读取问题缓存失败：{type(error).__name__}: {error}"
        errors.append(error_message)
        enabled_operations_succeeded = False
        write_log("ERROR", error_message)

    record = build_conversation_record(event, prompt_cache, response_time)

    if ENABLE_LOCAL_HISTORY:
        try:
            history_path = append_monthly_history(record, response_time)
            write_log(
                "INFO",
                "问答已写入月度 JSON："
                f"{history_path}；record_id={record['record_id']}",
            )
        except Exception as error:
            error_message = f"保存月度 JSON 失败：{type(error).__name__}: {error}"
            errors.append(error_message)
            enabled_operations_succeeded = False
            write_log("ERROR", error_message)
    else:
        write_log("INFO", "本地对话记录已关闭，跳过月度 JSON 写入。")

    if ENABLE_BARK_NOTIFICATION:
        try:
            short_title = truncate_text(
                clean_single_line(record.get("conversation_title")) or "未命名对话",
                28,
            )
            title = f"Codex 已完成：{short_title}"
            body = build_bark_body(record, response_time)
            send_bark(title=title, body=body)
            write_log(
                "INFO",
                f"Bark 通知发送成功：{title}；模型：{record['model']}",
            )
        except Exception as error:
            error_message = f"Bark 通知发送失败：{type(error).__name__}: {error}"
            errors.append(error_message)
            enabled_operations_succeeded = False
            write_log("ERROR", error_message)
    else:
        write_log("INFO", "Bark 通知已关闭，跳过手机推送。")

    # 所有已开启的输出都成功后，删除本轮临时问题缓存。
    # 若某项失败则保留缓存，便于排查或后续手动恢复。
    if enabled_operations_succeeded:
        try:
            cache_path.unlink(missing_ok=True)
        except OSError as error:
            write_log("WARNING", f"删除问题缓存失败：{cache_path}；{error}")

    if errors:
        enabled_names: list[str] = []
        if ENABLE_LOCAL_HISTORY:
            enabled_names.append("本地对话记录")
        if ENABLE_BARK_NOTIFICATION:
            enabled_names.append("Bark 通知")

        emit_hook_result(
            {
                "systemMessage": (
                    f"{'、'.join(enabled_names)}存在异常，请查看 "
                    "codex_bark_hook.log。"
                )
            }
        )
    else:
        emit_hook_result()

    return 0


# ---------------------------------------------------------------------------
# 手动测试
# ---------------------------------------------------------------------------

def run_test_mode() -> int:
    """测试 Bark 的换行、问题和回答显示。"""
    if not ENABLE_BARK_NOTIFICATION:
        write_log("INFO", "Bark 通知开关已关闭，跳过手动通知测试。")
        emit_hook_result(
            {
                "systemMessage": (
                    "Bark 通知当前已关闭；请在 config.local.json 中将 "
                    "enable_bark_notification 设为 true。"
                )
            }
        )
        return 0

    try:
        test_time = now_local()
        record = {
            "project": "TestProject",
            "conversation_title": "Bark 通知测试对话",
            "model": "test-model",
            "user_prompt": "这是用户问题测试。\n\n请确认问题与回答能够分段显示。",
            "assistant_response": (
                "这是 Codex 回答测试。\n\n"
                "- 第一项\n"
                "- 第二项"
            ),
        }
        send_bark(
            title="Codex Bark 测试",
            body=build_bark_body(record, test_time),
        )
        write_log("INFO", "Bark 手动测试通知发送成功。")
        emit_hook_result({"systemMessage": "Bark 测试通知已发送。"})
        return 0
    except Exception as error:
        error_message = f"{type(error).__name__}: {error}"
        write_log("ERROR", f"Bark 手动测试失败：{error_message}")
        emit_hook_result(
            {
                "systemMessage": (
                    "Bark 测试通知发送失败，请查看 codex_bark_hook.log。"
                )
            }
        )
        return 0


def run_archive_test_mode() -> int:
    """不依赖 Codex，测试当前已开启的本地归档和 Bark 功能。"""
    try:
        ensure_data_directories()
        session_id = f"manual-{uuid.uuid4()}"
        turn_id = f"turn-{uuid.uuid4()}"
        prompt_time = now_local()

        prompt_event = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": session_id,
            "turn_id": turn_id,
            "cwd": r"D:\TestProject",
            "model": "test-model",
            "permission_mode": "default",
            "prompt": "这是月度归档测试问题。",
        }
        save_prompt_cache(prompt_event, prompt_time)

        time.sleep(0.05)
        stop_event = {
            "hook_event_name": "Stop",
            "session_id": session_id,
            "turn_id": turn_id,
            "cwd": r"D:\TestProject",
            "model": "test-model",
            "permission_mode": "default",
            "last_assistant_message": "这是月度归档测试回答。",
            "stop_hook_active": False,
        }
        return handle_stop(stop_event)
    except Exception as error:
        error_message = f"{type(error).__name__}: {error}"
        write_log("ERROR", f"月度归档测试失败：{error_message}")
        emit_hook_result(
            {
                "systemMessage": (
                    "月度归档测试失败，请查看 codex_bark_hook.log。"
                )
            }
        )
        return 0


def main() -> int:
    if CONFIG_LOAD_ERROR is not None:
        write_log("ERROR", f"加载配置失败：{CONFIG_LOAD_ERROR}")
        emit_hook_result(
            {
                "systemMessage": (
                    "Codex Bark 配置文件加载失败，请查看 "
                    "codex_bark_hook.log。"
                )
            }
        )
        return 0

    if "--test" in sys.argv:
        return run_test_mode()

    if "--test-archive" in sys.argv:
        return run_archive_test_mode()

    try:
        raw_input = read_hook_input()

        if not raw_input.strip():
            write_log("WARNING", "脚本启动，但没有收到 Codex 钩子输入。")
            emit_hook_result()
            return 0

        event = load_hook_event(raw_input)
        event_name = get_event_name(event).casefold()

        if event_name == "userpromptsubmit":
            return handle_user_prompt_submit(event)

        if event_name == "stop":
            return handle_stop(event)

        write_log("INFO", f"忽略未处理事件：{get_event_name(event) or '未知事件'}")
        emit_hook_result()
        return 0

    except Exception as error:
        error_message = f"{type(error).__name__}: {error}"
        write_log("ERROR", f"钩子脚本执行失败：{error_message}")
        emit_hook_result(
            {
                "systemMessage": (
                    "Codex 钩子脚本执行失败，请查看 codex_bark_hook.log。"
                )
            }
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
