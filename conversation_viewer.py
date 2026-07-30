#!/usr/bin/env python3
"""在本机启动 Codex 对话档案查看面板。"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


PROJECT_DIRECTORY = Path(__file__).resolve().parent
APP_DIRECTORY = PROJECT_DIRECTORY / "viewer"
CONFIG_PATH = PROJECT_DIRECTORY / "config.local.json"
MONTHLY_FILE_PATTERN = re.compile(r"^Codex对话记录_\d{4}-\d{2}\.json$")
INDEX_FILE_NAME = "Codex对话索引.json"


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


class ViewerRequestHandler(SimpleHTTPRequestHandler):
    """只提供 viewer 目录，并为本地页面附加安全响应头。"""

    def do_GET(self) -> None:
        if urlsplit(self.path).path == "/api/configured-archive":
            self.send_configured_archive()
            return
        super().do_GET()

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

        payload = json.dumps(
            {"source_name": archive_directory.name, "files": files},
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

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
    server = ThreadingHTTPServer((arguments.host, arguments.port), handler)
    configured_archive_directory, configured_archive_error = find_configured_archive_directory()
    server.configured_archive_directory = configured_archive_directory  # type: ignore[attr-defined]
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
