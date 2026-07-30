#!/usr/bin/env python3
"""在本机启动 Codex 对话档案查看面板。"""

from __future__ import annotations

import argparse
import functools
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


APP_DIRECTORY = Path(__file__).resolve().parent / "viewer"


class ViewerRequestHandler(SimpleHTTPRequestHandler):
    """只提供 viewer 目录，并为本地页面附加安全响应头。"""

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
            "connect-src 'none'; "
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
    actual_port = server.server_address[1]
    browser_host = "127.0.0.1" if arguments.host in {"0.0.0.0", "::"} else arguments.host
    url = f"http://{browser_host}:{actual_port}/"

    print(f"Codex 对话档案已启动：{url}")
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
