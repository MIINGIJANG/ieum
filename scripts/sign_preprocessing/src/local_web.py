"""Loopback-only review UI; state changes require a per-process token."""

import json
import mimetypes
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from local_runtime import BusyError


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, runtime, webroot):
        self.runtime = runtime
        self.webroot = Path(webroot)
        self.token = secrets.token_urlsafe(32)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def host_ok(self):
        return self.headers.get("Host", "") in [
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        ]

    def respond(self, body, status=200, ctype="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.host_ok():
            return self.respond({"error": "허용되지 않은 호스트"}, 403)
        route = urlsplit(self.path)
        if route.path == "/":
            data = (
                (self.server.webroot / "index.html")
                .read_text()
                .replace("__TOKEN__", self.server.token)
            )
            return self.respond(data.encode(), ctype="text/html; charset=utf-8")
        if route.path == "/api/status":
            return self.respond(self.server.runtime.snapshot())
        if route.path == "/manual":
            return self.send_file(self.server.webroot.parent / "tools/수어_좌표_수동보정.html")
        if route.path == "/file":
            rel = parse_qs(route.query).get("path", [""])[0]
            root = self.server.runtime.root
            path = (root / rel).resolve()
            if not path.is_relative_to(root) or not path.is_file() or path.name.startswith("."):
                return self.respond({"error": "파일이 없습니다."}, 404)
            return self.send_file(path)
        return self.respond({"error": "없는 주소"}, 404)

    def send_file(self, path):
        size = path.stat().st_size
        start = 0
        end = size - 1
        status = 200
        wanted = self.headers.get("Range")
        if wanted:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", wanted)
            if not m or (not m[1] and not m[2]):
                return self.respond({"error": "범위 오류"}, 416)
            if m[1]:
                start = int(m[1])
                end = min(int(m[2]), end) if m[2] else end
            else:
                start = max(0, size - int(m[2]))
            if start > end or start >= size:
                return self.respond({"error": "범위 오류"}, 416)
            status = 206
        self.send_response(status)
        self.send_header(
            "Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if path.suffix.lower() == ".zip":
            self.send_header("Content-Disposition", 'attachment; filename="ieum_results.zip"')
        self.end_headers()
        try:
            with path.open("rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    data = f.read(min(256 * 1024, remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    remaining -= len(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        if not self.host_ok() or self.headers.get("X-Review-Token") != self.server.token:
            return self.respond({"error": "접근 확인 실패. 관리 화면을 새로 여세요."}, 403)
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.respond({"error": "Content-Length는 정수여야 합니다."}, 400)
        if not 0 < n <= 64 * 1024**2:
            return self.respond({"error": "요청 크기 오류 (최대 64MiB)"}, 413)
        try:
            body = json.loads(self.rfile.read(n))
            kind = urlsplit(self.path).path.removeprefix("/api/")
            return self.respond(self.server.runtime.action(kind, body))
        except BusyError as e:
            return self.respond({"error": str(e)}, 409)
        except Exception as e:
            return self.respond({"error": str(e)}, 400)
