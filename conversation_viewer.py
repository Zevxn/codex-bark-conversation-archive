#!/usr/bin/env python3
"""在本机启动 Codex 对话档案查看面板。"""

from __future__ import annotations

import argparse
import functools
import ipaddress
import json
import os
import re
import secrets
import socket
import threading
import time
import uuid
import webbrowser
from contextlib import ExitStack, contextmanager
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit


PROJECT_DIRECTORY = Path(__file__).resolve().parent
APP_DIRECTORY = PROJECT_DIRECTORY / "viewer"
CONFIG_PATH = PROJECT_DIRECTORY / "config.local.json"
MONTHLY_FILE_PATTERN = re.compile(r"^Codex对话记录_\d{4}-\d{2}\.json$")
TRASH_ID_PATTERN = re.compile(r"^\d{8}T\d{6}_[0-9a-f]{10}$")
INDEX_FILE_NAME = "Codex对话索引.json"
TRASH_DIRECTORY_NAME = ".trash"
LOCK_DIRECTORY_NAME = ".locks"
LOCK_TIMEOUT_SECONDS = 15.0
LOCK_POLL_INTERVAL_SECONDS = 0.1
STALE_LOCK_SECONDS = 120.0
MAX_REQUEST_BODY_BYTES = 64 * 1024
RESTORABLE_TRASH_STATUSES = {"completed", "prepared", "rollback_failed"}
VIEWER_API_VERSION = 3


class ArchiveMutationError(RuntimeError):
    """可安全返回给本地页面的归档修改错误。"""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


class ViewerHTTPServer(ThreadingHTTPServer):
    """禁止 Windows 在同一端口启动多个查看器实例。"""

    allow_reuse_address = False

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def find_configured_archive_directory() -> tuple[Path | None, str]:
    """读取本地配置，返回可自动加载的归档目录及状态说明。"""
    if not CONFIG_PATH.is_file():
        return None, "未找到 config.local.json"
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        return None, f"config.local.json 无法读取：{error}"

    configured_value = config.get("history_dir") if isinstance(config, dict) else None
    if not isinstance(configured_value, str) or not configured_value.strip():
        return None, "config.local.json 未配置 history_dir"

    expanded = os.path.expandvars(os.path.expanduser(configured_value.strip()))
    archive_directory = Path(expanded)
    if not archive_directory.is_absolute():
        archive_directory = PROJECT_DIRECTORY / archive_directory
    try:
        archive_directory = archive_directory.resolve(strict=True)
    except (OSError, RuntimeError):
        return None, f"history_dir 不存在：{archive_directory}"
    if not archive_directory.is_dir():
        return None, f"history_dir 不是目录：{archive_directory}"
    try:
        has_monthly_file = any(
            item.is_file() and MONTHLY_FILE_PATTERN.fullmatch(item.name)
            for item in archive_directory.iterdir()
        )
    except OSError as error:
        return None, f"history_dir 无法访问：{error}"
    if not has_monthly_file:
        return None, "history_dir 中没有月度对话记录 JSON"
    return archive_directory, ""


def read_archive_files(archive_directory: Path) -> list[dict[str, object]]:
    """读取自动配置目录第一层中的月度记录和对话索引。"""
    files: list[dict[str, object]] = []
    for path in sorted(archive_directory.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue
        if not MONTHLY_FILE_PATTERN.fullmatch(path.name) and path.name != INDEX_FILE_NAME:
            continue
        stat = path.stat()
        files.append(
            {
                "name": path.name,
                "text": path.read_text(encoding="utf-8-sig"),
                "lastModified": stat.st_mtime_ns // 1_000_000,
            }
        )
    return files


def atomic_write_json(path: Path, data: Any) -> None:
    """先写临时文件再原子替换，避免正式 JSON 只写入一部分。"""
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
    """使用与归档钩子相同的锁文件约定，避免写入与删除互相覆盖。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    file_descriptor: int | None = None
    while True:
        try:
            file_descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(file_descriptor, f"pid={os.getpid()}\ntime={time.time()}\n".encode("utf-8"))
            break
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > STALE_LOCK_SECONDS:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise ArchiveMutationError(f"等待归档文件锁超时：{lock_path.name}")
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


def read_json_document(path: Path, description: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ArchiveMutationError(f"{description}无法读取：{path.name}；{error}") from error
    if not isinstance(document, dict):
        raise ArchiveMutationError(f"{description}顶层必须是 JSON 对象：{path.name}")
    return document


def list_monthly_archive_paths(archive_directory: Path) -> list[Path]:
    try:
        return sorted(
            (
                path
                for path in archive_directory.iterdir()
                if path.is_file() and MONTHLY_FILE_PATTERN.fullmatch(path.name)
            ),
            key=lambda path: path.name,
        )
    except OSError as error:
        raise ArchiveMutationError(f"归档目录无法访问：{error}") from error


def delete_archived_conversation(archive_directory: Path, session_id: str) -> dict[str, object]:
    """把完整会话移入应用回收站，并从月度记录和索引中移除。"""
    monthly_paths = list_monthly_archive_paths(archive_directory)
    if not monthly_paths:
        raise ArchiveMutationError("归档目录中没有可修改的月度对话记录。", 404)

    lock_directory = archive_directory / LOCK_DIRECTORY_NAME
    monthly_lock_stems = {path.stem for path in monthly_paths}
    monthly_lock_stems.add(f"Codex对话记录_{datetime.now().astimezone():%Y-%m}")
    lock_paths = [lock_directory / f"{stem}.lock" for stem in monthly_lock_stems]
    lock_paths.append(lock_directory / "Codex对话索引.lock")

    with ExitStack() as stack:
        for lock_path in sorted(lock_paths, key=lambda path: path.name):
            stack.enter_context(exclusive_file_lock(lock_path))

        # 月份文件可能恰好在等待锁时首次生成。遇到这种情况让用户重试，
        # 避免声称删除完整会话却漏掉新出现的月份。
        locked_names = {path.name for path in monthly_paths}
        current_names = {path.name for path in list_monthly_archive_paths(archive_directory)}
        if current_names != locked_names:
            raise ArchiveMutationError("归档文件刚刚发生变化，请重新执行删除。")

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        affected_documents: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
        deleted_records: list[dict[str, object]] = []
        remaining_record_count = 0

        for monthly_path in monthly_paths:
            original_document = read_json_document(monthly_path, "月度对话记录")
            records = original_document.get("records")
            if not isinstance(records, list):
                raise ArchiveMutationError(f"月度对话记录缺少 records 数组：{monthly_path.name}")

            retained_records: list[object] = []
            removed_from_file: list[dict[str, Any]] = []
            for record in records:
                if isinstance(record, dict) and str(record.get("session_id") or "").strip() == session_id:
                    removed_from_file.append(record)
                else:
                    retained_records.append(record)

            remaining_record_count += len(retained_records)
            if not removed_from_file:
                continue

            updated_document = dict(original_document)
            updated_document["records"] = retained_records
            updated_document["record_count"] = len(retained_records)
            updated_document["updated_at"] = now
            affected_documents.append((monthly_path, original_document, updated_document))
            deleted_records.extend(
                {"source_file": monthly_path.name, "record": record}
                for record in removed_from_file
            )

        if not deleted_records:
            raise ArchiveMutationError("这条对话已经不存在，可能刚刚被删除或归档已更新。", 404)

        index_path = archive_directory / INDEX_FILE_NAME
        original_index: dict[str, Any] | None = None
        updated_index: dict[str, Any] | None = None
        index_entry: object = None
        if index_path.exists():
            original_index = read_json_document(index_path, "对话索引")
            sessions = original_index.get("sessions")
            if not isinstance(sessions, dict):
                raise ArchiveMutationError("对话索引缺少 sessions 对象。")
            index_entry = sessions.get(session_id)
            if session_id in sessions:
                updated_sessions = dict(sessions)
                del updated_sessions[session_id]
                updated_index = dict(original_index)
                updated_index["sessions"] = updated_sessions
                updated_index["updated_at"] = now

        first_record = deleted_records[0]["record"]
        conversation_title = ""
        if isinstance(first_record, dict):
            conversation_title = str(first_record.get("conversation_title") or "").strip()
        if not conversation_title and isinstance(index_entry, dict):
            conversation_title = str(index_entry.get("conversation_title") or "").strip()

        deletion_id = f"{datetime.now().astimezone():%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:10]}"
        trash_path = archive_directory / TRASH_DIRECTORY_NAME / f"{deletion_id}.json"
        trash_document: dict[str, object] = {
            "schema_version": 1,
            "deletion_id": deletion_id,
            "status": "prepared",
            "deleted_at": now,
            "session_id": session_id,
            "conversation_title": conversation_title,
            "deleted_record_count": len(deleted_records),
            "records": deleted_records,
            "index_entry": index_entry,
        }
        atomic_write_json(trash_path, trash_document)

        written_originals: list[tuple[Path, dict[str, Any]]] = []
        try:
            for path, original_document, updated_document in affected_documents:
                atomic_write_json(path, updated_document)
                written_originals.append((path, original_document))
            if original_index is not None and updated_index is not None:
                atomic_write_json(index_path, updated_index)
                written_originals.append((index_path, original_index))
        except Exception as error:
            rollback_errors: list[str] = []
            for path, original_document in reversed(written_originals):
                try:
                    atomic_write_json(path, original_document)
                except Exception as rollback_error:
                    rollback_errors.append(f"{path.name}: {rollback_error}")
            trash_document["status"] = "rollback_failed" if rollback_errors else "rolled_back"
            trash_document["error"] = str(error)
            try:
                atomic_write_json(trash_path, trash_document)
            except Exception:
                pass
            detail = f"；回滚异常：{'；'.join(rollback_errors)}" if rollback_errors else ""
            raise ArchiveMutationError(f"删除写入失败，已尝试恢复原文件：{error}{detail}", 500) from error

        trash_document["status"] = "completed"
        trash_status_warning = ""
        try:
            atomic_write_json(trash_path, trash_document)
        except Exception as error:
            # prepared 状态的回收站文件已经包含恢复所需的完整记录；
            # 不应因为最后的状态标记失败而把一次已完成的删除报告为失败。
            trash_status_warning = f"回收站状态标记未更新：{error}"
        return {
            "deletion_id": deletion_id,
            "session_id": session_id,
            "conversation_title": conversation_title,
            "deleted_record_count": len(deleted_records),
            "affected_file_count": len(affected_documents),
            "remaining_record_count": remaining_record_count,
            "trash_file": f"{TRASH_DIRECTORY_NAME}/{trash_path.name}",
            "index_entry_removed": updated_index is not None,
            "warning": trash_status_warning,
        }


def trash_path_for_id(archive_directory: Path, deletion_id: str) -> Path:
    if not TRASH_ID_PATTERN.fullmatch(deletion_id):
        raise ArchiveMutationError("回收站记录标识无效。", 400)
    return archive_directory / TRASH_DIRECTORY_NAME / f"{deletion_id}.json"


def read_trash_items(archive_directory: Path) -> tuple[list[dict[str, object]], int]:
    """返回可恢复的回收站摘要，不把完整对话正文发送到列表页面。"""
    trash_directory = archive_directory / TRASH_DIRECTORY_NAME
    if not trash_directory.is_dir():
        return [], 0
    items: list[dict[str, object]] = []
    invalid_count = 0
    try:
        paths = sorted(trash_directory.glob("*.json"), key=lambda path: path.name, reverse=True)
    except OSError:
        return [], 1
    for path in paths:
        deletion_id = path.stem
        if not TRASH_ID_PATTERN.fullmatch(deletion_id):
            continue
        try:
            document = read_json_document(path, "回收站记录")
        except ArchiveMutationError:
            invalid_count += 1
            continue
        status = str(document.get("status") or "prepared")
        if status not in RESTORABLE_TRASH_STATUSES:
            continue
        records = document.get("records")
        if not isinstance(records, list):
            invalid_count += 1
            continue
        try:
            deleted_record_count = int(document.get("deleted_record_count") or len(records))
        except (TypeError, ValueError):
            invalid_count += 1
            continue
        items.append(
            {
                "deletion_id": deletion_id,
                "session_id": str(document.get("session_id") or ""),
                "conversation_title": str(document.get("conversation_title") or "未命名对话"),
                "deleted_at": str(document.get("deleted_at") or ""),
                "deleted_record_count": deleted_record_count,
                "status": status,
            }
        )
    return items, invalid_count


def validate_restorable_trash_document(
    document: dict[str, Any],
    deletion_id: str,
) -> tuple[str, dict[str, list[dict[str, Any]]]]:
    status = str(document.get("status") or "prepared")
    if status == "restored":
        raise ArchiveMutationError("这条回收站记录已经恢复。", 409)
    if status not in RESTORABLE_TRASH_STATUSES:
        raise ArchiveMutationError(f"这条回收站记录当前不能恢复：{status}", 409)
    if str(document.get("deletion_id") or "") != deletion_id:
        raise ArchiveMutationError("回收站记录内部标识不一致。")
    session_id = str(document.get("session_id") or "").strip()
    if not session_id:
        raise ArchiveMutationError("回收站记录缺少 session_id。")
    records = document.get("records")
    if not isinstance(records, list) or not records:
        raise ArchiveMutationError("回收站记录中没有可恢复的问答。")

    grouped_records: dict[str, list[dict[str, Any]]] = {}
    for item in records:
        if not isinstance(item, dict):
            raise ArchiveMutationError("回收站 records 中存在无效条目。")
        source_file = item.get("source_file")
        record = item.get("record")
        if not isinstance(source_file, str) or not MONTHLY_FILE_PATTERN.fullmatch(source_file):
            raise ArchiveMutationError("回收站记录包含无效的月度文件名。")
        if not isinstance(record, dict):
            raise ArchiveMutationError("回收站记录包含无效的问答对象。")
        grouped_records.setdefault(source_file, []).append(record)
    return session_id, grouped_records


def record_identity(record: dict[str, Any]) -> str:
    record_id = str(record.get("record_id") or "").strip()
    if record_id:
        return f"record:{record_id}"
    session_id = str(record.get("session_id") or "").strip()
    turn_id = str(record.get("turn_id") or "").strip()
    if session_id or turn_id:
        return f"turn:{session_id}:{turn_id}"
    return "json:" + json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def restore_archived_conversation(archive_directory: Path, deletion_id: str) -> dict[str, object]:
    """把一条回收站记录按原月份恢复，并补回缺失的会话索引。"""
    trash_path = trash_path_for_id(archive_directory, deletion_id)
    if not trash_path.is_file():
        raise ArchiveMutationError("未找到这条回收站记录。", 404)

    initial_trash_document = read_json_document(trash_path, "回收站记录")
    _, initial_groups = validate_restorable_trash_document(initial_trash_document, deletion_id)
    target_paths = {archive_directory / source_file for source_file in initial_groups}
    existing_paths = set(list_monthly_archive_paths(archive_directory))
    all_monthly_paths = existing_paths | target_paths

    lock_directory = archive_directory / LOCK_DIRECTORY_NAME
    lock_stems = {path.stem for path in all_monthly_paths}
    lock_stems.add(f"Codex对话记录_{datetime.now().astimezone():%Y-%m}")
    lock_paths = [lock_directory / f"{stem}.lock" for stem in lock_stems]
    lock_paths.append(lock_directory / "Codex对话索引.lock")

    with ExitStack() as stack:
        for lock_path in sorted(lock_paths, key=lambda path: path.name):
            stack.enter_context(exclusive_file_lock(lock_path))

        trash_document = read_json_document(trash_path, "回收站记录")
        session_id, grouped_records = validate_restorable_trash_document(trash_document, deletion_id)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        pending_writes: list[tuple[Path, dict[str, Any] | None, dict[str, Any]]] = []
        restored_record_count = 0
        skipped_existing_count = 0

        for source_file, records_to_restore in sorted(grouped_records.items()):
            monthly_path = archive_directory / source_file
            if monthly_path.exists():
                original_document: dict[str, Any] | None = read_json_document(monthly_path, "月度对话记录")
                existing_records = original_document.get("records")
                if not isinstance(existing_records, list):
                    raise ArchiveMutationError(f"月度对话记录缺少 records 数组：{monthly_path.name}")
                updated_records = list(existing_records)
            else:
                month = source_file.removeprefix("Codex对话记录_").removesuffix(".json")
                original_document = None
                updated_records = []

            existing_identities = {
                record_identity(record)
                for record in updated_records
                if isinstance(record, dict)
            }
            added_to_file = 0
            for record in records_to_restore:
                identity = record_identity(record)
                if identity in existing_identities:
                    skipped_existing_count += 1
                    continue
                updated_records.append(record)
                existing_identities.add(identity)
                restored_record_count += 1
                added_to_file += 1
            if not added_to_file:
                continue

            if original_document is None:
                updated_document: dict[str, Any] = {
                    "schema_version": 1,
                    "month": month,
                    "updated_at": now,
                    "record_count": len(updated_records),
                    "records": updated_records,
                }
            else:
                updated_document = dict(original_document)
                updated_document["records"] = updated_records
                updated_document["record_count"] = len(updated_records)
                updated_document["updated_at"] = now
            pending_writes.append((monthly_path, original_document, updated_document))

        index_path = archive_directory / INDEX_FILE_NAME
        original_index: dict[str, Any] | None = None
        updated_index: dict[str, Any] | None = None
        if index_path.exists():
            original_index = read_json_document(index_path, "对话索引")
            sessions = original_index.get("sessions")
            if not isinstance(sessions, dict):
                raise ArchiveMutationError("对话索引缺少 sessions 对象。")
        else:
            sessions = {}

        if session_id not in sessions:
            index_entry = trash_document.get("index_entry")
            if not isinstance(index_entry, dict):
                first_record = next(iter(grouped_records.values()))[0]
                index_entry = {
                    "session_id": session_id,
                    "conversation_title": str(first_record.get("conversation_title") or "未命名对话"),
                    "project": str(first_record.get("project") or ""),
                    "cwd": str(first_record.get("cwd") or ""),
                    "created_at": first_record.get("prompt_time") or first_record.get("response_time"),
                    "updated_at": now,
                }
            restored_sessions = dict(sessions)
            restored_sessions[session_id] = index_entry
            updated_index = dict(original_index or {"schema_version": 1})
            updated_index["sessions"] = restored_sessions
            updated_index["updated_at"] = now

        written_originals: list[tuple[Path, dict[str, Any] | None]] = []
        try:
            for path, original_document, updated_document in pending_writes:
                atomic_write_json(path, updated_document)
                written_originals.append((path, original_document))
            if updated_index is not None:
                atomic_write_json(index_path, updated_index)
                written_originals.append((index_path, original_index))
        except Exception as error:
            rollback_errors: list[str] = []
            for path, original_document in reversed(written_originals):
                try:
                    if original_document is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic_write_json(path, original_document)
                except Exception as rollback_error:
                    rollback_errors.append(f"{path.name}: {rollback_error}")
            detail = f"；回滚异常：{'；'.join(rollback_errors)}" if rollback_errors else ""
            raise ArchiveMutationError(f"恢复写入失败，已尝试恢复原文件：{error}{detail}", 500) from error

        trash_document["status"] = "restored"
        trash_document["restored_at"] = now
        trash_document["restored_record_count"] = restored_record_count
        trash_document["skipped_existing_count"] = skipped_existing_count
        warning = ""
        try:
            atomic_write_json(trash_path, trash_document)
        except Exception as error:
            warning = f"恢复已完成，但回收站状态未能更新：{error}"

        remaining_items, _ = read_trash_items(archive_directory)
        return {
            "deletion_id": deletion_id,
            "session_id": session_id,
            "conversation_title": str(trash_document.get("conversation_title") or "未命名对话"),
            "restored_record_count": restored_record_count,
            "skipped_existing_count": skipped_existing_count,
            "affected_file_count": len(pending_writes),
            "remaining_trash_count": len(remaining_items),
            "warning": warning,
        }


def purge_trash_item(archive_directory: Path, deletion_id: str) -> dict[str, object]:
    """永久删除一条尚未恢复的回收站 JSON，不接受任何外部文件路径。"""
    trash_path = trash_path_for_id(archive_directory, deletion_id)
    lock_path = archive_directory / LOCK_DIRECTORY_NAME / "Codex对话索引.lock"
    with exclusive_file_lock(lock_path):
        if not trash_path.is_file():
            raise ArchiveMutationError("未找到这条回收站记录。", 404)
        trash_document = read_json_document(trash_path, "回收站记录")
        status = str(trash_document.get("status") or "prepared")
        if status not in RESTORABLE_TRASH_STATUSES:
            raise ArchiveMutationError("这条记录已经恢复或当前不能彻底删除。", 409)
        if str(trash_document.get("deletion_id") or "") != deletion_id:
            raise ArchiveMutationError("回收站记录内部标识不一致。")
        records = trash_document.get("records")
        if not isinstance(records, list):
            raise ArchiveMutationError("回收站记录中没有有效的 records 数组。")
        conversation_title = str(trash_document.get("conversation_title") or "未命名对话")
        deleted_record_count = int(trash_document.get("deleted_record_count") or len(records))
        try:
            trash_path.unlink()
        except OSError as error:
            raise ArchiveMutationError(f"回收站文件无法删除：{error}", 500) from error

    remaining_items, _ = read_trash_items(archive_directory)
    return {
        "deletion_id": deletion_id,
        "conversation_title": conversation_title,
        "deleted_record_count": deleted_record_count,
        "remaining_trash_count": len(remaining_items),
    }


class ViewerRequestHandler(SimpleHTTPRequestHandler):
    """只提供 viewer 目录，并为本地页面附加安全响应头。"""

    def do_GET(self) -> None:
        if urlsplit(self.path).path == "/api/configured-archive":
            self.send_configured_archive()
            return
        if urlsplit(self.path).path == "/api/trash":
            self.send_trash_items()
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlsplit(self.path).path == "/api/delete-conversation":
            self.delete_conversation()
            return
        if urlsplit(self.path).path == "/api/restore-conversation":
            self.restore_conversation()
            return
        if urlsplit(self.path).path == "/api/purge-trash":
            self.purge_trash()
            return
        self.send_json({"error": "未找到本地接口。"}, 404)

    def send_json(self, data: object, status_code: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def is_loopback_client(self) -> bool:
        try:
            address = ipaddress.ip_address(str(self.client_address[0]).split("%", 1)[0])
        except ValueError:
            return False
        mapped_address = getattr(address, "ipv4_mapped", None)
        return address.is_loopback or bool(mapped_address and mapped_address.is_loopback)

    def authorize_mutation_request(self) -> bool:
        if not self.is_loopback_client():
            self.send_json({"error": "归档修改功能只允许从本机访问。"}, 403)
            return False
        mutation_token = getattr(self.server, "mutation_token", "")
        provided_token = self.headers.get("X-Viewer-Token", "")
        if (
            not isinstance(mutation_token, str)
            or not mutation_token
            or not isinstance(provided_token, str)
            or not secrets.compare_digest(provided_token, mutation_token)
        ):
            self.send_json({"error": "操作授权无效，请刷新页面后重试。"}, 403)
            return False
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin and host and urlsplit(origin).netloc.casefold() != host.casefold():
            self.send_json({"error": "拒绝来自其他页面的归档修改请求。"}, 403)
            return False
        return True

    def send_configured_archive(self) -> None:
        archive_directory = getattr(self.server, "configured_archive_directory", None)
        if not isinstance(archive_directory, Path) or not archive_directory.is_dir():
            self.send_response(204)
            self.end_headers()
            return
        try:
            files = read_archive_files(archive_directory)
        except (OSError, UnicodeError) as error:
            self.log_error("无法读取自动配置的归档目录：%s", error)
            self.send_response(204)
            self.end_headers()
            return
        if not any(MONTHLY_FILE_PATTERN.fullmatch(str(item["name"])) for item in files):
            self.send_response(204)
            self.end_headers()
            return

        can_delete = self.is_loopback_client()
        trash_items, _ = read_trash_items(archive_directory)
        self.send_json(
            {
                "source_name": archive_directory.name,
                "files": files,
                "api_version": VIEWER_API_VERSION,
                "capabilities": {
                    "delete": can_delete,
                    "trash": can_delete,
                    "restore": can_delete,
                    "purge": can_delete,
                },
                "can_delete": can_delete,
                "mutation_token": getattr(self.server, "mutation_token", "") if can_delete else "",
                "trash_count": len(trash_items),
            }
        )

    def delete_conversation(self) -> None:
        if not self.authorize_mutation_request():
            return
        archive_directory = getattr(self.server, "configured_archive_directory", None)
        if self.headers.get_content_type() != "application/json":
            self.send_json({"error": "删除请求必须使用 JSON。"}, 415)
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if content_length <= 0 or content_length > MAX_REQUEST_BODY_BYTES:
            self.send_json({"error": "删除请求内容为空或过大。"}, 413)
            return
        try:
            request_data = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self.send_json({"error": "删除请求不是有效 JSON。"}, 400)
            return
        session_id = request_data.get("session_id") if isinstance(request_data, dict) else None
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 512:
            self.send_json({"error": "缺少有效的 session_id。"}, 400)
            return
        if not isinstance(archive_directory, Path) or not archive_directory.is_dir():
            self.send_json({"error": "配置的归档目录当前不可用。"}, 409)
            return

        try:
            result = delete_archived_conversation(archive_directory, session_id.strip())
        except ArchiveMutationError as error:
            self.log_error("删除归档对话失败：%s", error)
            self.send_json({"error": str(error)}, error.status_code)
            return
        except Exception as error:
            self.log_error("删除归档对话时发生未预期错误：%s", error)
            self.send_json({"error": "删除失败，归档文件未能安全更新。"}, 500)
            return
        self.send_json(result)

    def send_trash_items(self) -> None:
        if not self.authorize_mutation_request():
            return
        archive_directory = getattr(self.server, "configured_archive_directory", None)
        if not isinstance(archive_directory, Path) or not archive_directory.is_dir():
            self.send_json({"error": "配置的归档目录当前不可用。"}, 409)
            return
        items, invalid_count = read_trash_items(archive_directory)
        self.send_json({"items": items, "invalid_count": invalid_count, "count": len(items)})

    def restore_conversation(self) -> None:
        if not self.authorize_mutation_request():
            return
        archive_directory = getattr(self.server, "configured_archive_directory", None)
        if not isinstance(archive_directory, Path) or not archive_directory.is_dir():
            self.send_json({"error": "配置的归档目录当前不可用。"}, 409)
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json({"error": "恢复请求必须使用 JSON。"}, 415)
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if content_length <= 0 or content_length > MAX_REQUEST_BODY_BYTES:
            self.send_json({"error": "恢复请求内容为空或过大。"}, 413)
            return
        try:
            request_data = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self.send_json({"error": "恢复请求不是有效 JSON。"}, 400)
            return
        deletion_id = request_data.get("deletion_id") if isinstance(request_data, dict) else None
        if not isinstance(deletion_id, str) or not TRASH_ID_PATTERN.fullmatch(deletion_id):
            self.send_json({"error": "缺少有效的 deletion_id。"}, 400)
            return
        try:
            result = restore_archived_conversation(archive_directory, deletion_id)
        except ArchiveMutationError as error:
            self.log_error("恢复回收站对话失败：%s", error)
            self.send_json({"error": str(error)}, error.status_code)
            return
        except Exception as error:
            self.log_error("恢复回收站对话时发生未预期错误：%s", error)
            self.send_json({"error": "恢复失败，归档文件未能安全更新。"}, 500)
            return
        self.send_json(result)

    def purge_trash(self) -> None:
        if not self.authorize_mutation_request():
            return
        archive_directory = getattr(self.server, "configured_archive_directory", None)
        if not isinstance(archive_directory, Path) or not archive_directory.is_dir():
            self.send_json({"error": "配置的归档目录当前不可用。"}, 409)
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json({"error": "彻底删除请求必须使用 JSON。"}, 415)
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if content_length <= 0 or content_length > MAX_REQUEST_BODY_BYTES:
            self.send_json({"error": "彻底删除请求内容为空或过大。"}, 413)
            return
        try:
            request_data = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self.send_json({"error": "彻底删除请求不是有效 JSON。"}, 400)
            return
        deletion_id = request_data.get("deletion_id") if isinstance(request_data, dict) else None
        if not isinstance(deletion_id, str) or not TRASH_ID_PATTERN.fullmatch(deletion_id):
            self.send_json({"error": "缺少有效的 deletion_id。"}, 400)
            return
        try:
            result = purge_trash_item(archive_directory, deletion_id)
        except ArchiveMutationError as error:
            self.log_error("彻底删除回收站记录失败：%s", error)
            self.send_json({"error": str(error)}, error.status_code)
            return
        except Exception as error:
            self.log_error("彻底删除回收站记录时发生未预期错误：%s", error)
            self.send_json({"error": "彻底删除失败，回收站文件未能安全移除。"}, 500)
            return
        self.send_json(result)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'none'; "
            "form-action 'none'; "
            "frame-src 'none'",
        )
        super().end_headers()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动 Codex 对话档案本地查看面板")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765；使用 0 可自动选择")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    if not APP_DIRECTORY.is_dir():
        raise SystemExit(f"未找到查看器目录：{APP_DIRECTORY}")

    handler = functools.partial(ViewerRequestHandler, directory=str(APP_DIRECTORY))
    try:
        server = ViewerHTTPServer((arguments.host, arguments.port), handler)
    except OSError as error:
        if getattr(error, "winerror", None) == 10048 or getattr(error, "errno", None) in {48, 98, 10048}:
            print(f"无法启动：{arguments.host}:{arguments.port} 已被占用。")
            print("如果对话面板已经打开，请先关闭旧的启动窗口，再重新双击启动文件。")
            return 1
        raise
    configured_archive_directory, configured_archive_error = find_configured_archive_directory()
    server.configured_archive_directory = configured_archive_directory  # type: ignore[attr-defined]
    server.mutation_token = secrets.token_urlsafe(32)  # type: ignore[attr-defined]
    actual_port = server.server_address[1]
    browser_host = "127.0.0.1" if arguments.host in {"0.0.0.0", "::"} else arguments.host
    url = f"http://{browser_host}:{actual_port}/"

    print(f"Codex 对话档案已启动：{url}")
    if configured_archive_directory:
        print(f"将自动加载归档目录：{configured_archive_directory}")
    else:
        print(f"未启用自动加载：{configured_archive_error}")
    print("按 Ctrl+C 停止服务。")
    if not arguments.no_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止服务…")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
