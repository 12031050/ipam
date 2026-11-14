# -*- coding: utf-8 -*-
"""IPAM fnOS 版服务入口：HTTP 服务、静态资源与安装期初始化。仅使用 Python 标准库。"""

import argparse
import http.server
import json
import mimetypes
import os
import signal
import socket
import socketserver
import sys
import urllib.parse
from http.cookies import SimpleCookie

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import Config, gateway_user, init_admin, init_db, session_user  # noqa: E402
from router import RawResponse, Request, json_bytes  # noqa: E402

MAX_BODY = 24 * 1024 * 1024
MIME = {
    ".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
    ".ico": "image/x-icon", ".woff2": "font/woff2", ".map": "application/json",
}
ALLOWED_EXT = set(MIME)


def make_handler(cfg, router, base=""):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "IPAM"
        sys_version = ""

        def log_message(self, fmt, *args):
            sys.stderr.write("[%s] %s %s\n" % (self.log_date_time_string(), self.address_string(), fmt % args))

        def _send(self, status, body, content_type, headers=None, cookie_header=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store" if content_type.startswith("application/json") else "public, max-age=300")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            if cookie_header:
                self.send_header("Set-Cookie", cookie_header)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _error(self, status, message):
            self._send(status, json_bytes({"ok": False, "error": message}),
                       "application/json; charset=utf-8")

        def _static(self, path):
            rel = urllib.parse.unquote(path).lstrip("/") or "index.html"
            if ".." in rel.replace("\\", "/").split("/"):
                return self._error(400, "非法路径")
            ui_root = os.path.realpath(cfg.ui)
            target = os.path.realpath(os.path.join(ui_root, rel))
            if target != ui_root and not target.startswith(ui_root + os.sep):
                return self._error(403, "禁止访问")
            ext = os.path.splitext(target)[1].lower()
            if not os.path.isfile(target) or ext not in ALLOWED_EXT:
                target = os.path.join(ui_root, "index.html")
                ext = ".html"
            try:
                with open(target, "rb") as f:
                    body = f.read()
            except OSError:
                return self._error(404, "页面不存在")
            self._send(200, body, MIME.get(ext, "application/octet-stream"))

        def _handle(self):
            try:
                parsed = urllib.parse.urlsplit(self.path)
                path = urllib.parse.unquote(parsed.path) or "/"
                if base and (path == base or path.startswith(base + "/")):
                    path = path[len(base):] or "/"
                query = {k: v[-1] for k, v in urllib.parse.parse_qs(
                    parsed.query, keep_blank_values=True).items()}
                headers = {k.lower(): v for k, v in self.headers.items()}
                cookies = {}
                try:
                    c = SimpleCookie()
                    c.load(self.headers.get("Cookie", ""))
                    cookies = {k: v.value for k, v in c.items()}
                except Exception:
                    pass
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length > MAX_BODY:
                    return self._error(413, "请求体过大")
                raw = self.rfile.read(length) if length else b""
                req = Request(cfg, self.command, path, query, headers, cookies, raw,
                              self.client_address[0] if isinstance(self.client_address, tuple) else "unix", "")
                token = cookies.get("ipam_session", "")
                req.user = session_user(cfg, token)
                if not req.user and cfg.trust_gateway and headers.get("x-trim-username"):
                    req.user = gateway_user(cfg, headers.get("x-trim-username"),
                                            headers.get("x-trim-isadmin", "false"))

                if path.startswith("/api/") or path == "/api":
                    status, extra, payload = router.handle(req)
                    if isinstance(payload, RawResponse):
                        self._send(status, payload.data, payload.content_type, payload.headers)
                    else:
                        self._send(status, json_bytes(payload), "application/json; charset=utf-8",
                                   extra, extra.get("Set-Cookie"))
                else:
                    self._static(path)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                import traceback
                traceback.print_exc()
                try:
                    self._error(500, "服务器内部错误：%s" % e)
                except Exception:
                    pass

        def do_GET(self):
            self._handle()

        def do_HEAD(self):
            self._handle()

        def do_POST(self):
            self._handle()

        def do_PUT(self):
            self._handle()

        def do_DELETE(self):
            self._handle()

        def do_PATCH(self):
            self._handle()

    return Handler


class ThreadingTCPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class ThreadingUnixServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    # Windows 无 AF_UNIX，仅在 fnOS/Linux 上实例化该类
    address_family = getattr(socket, "AF_UNIX", socket.AF_INET)

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = 0

    def get_request(self):
        conn, _ = super().get_request()
        return conn, ("unix", 0)


def run_server(cfg):
    from api import build_router
    init_db(cfg)
    router = build_router(cfg)
    base = os.environ.get("IPAM_BASE", "").rstrip("/")
    handler = make_handler(cfg, router, base)
    if cfg.socket_path:
        if os.path.exists(cfg.socket_path):
            os.unlink(cfg.socket_path)
        srv = ThreadingUnixServer(cfg.socket_path, handler)
        os.chmod(cfg.socket_path, 0o660)
        print("IPAM %s listening on unix socket %s (base=%s)" % (
            os.environ.get("IPAM_VERSION", "1.0.4"), cfg.socket_path, base or "/"), flush=True)
    else:
        srv = ThreadingTCPServer((cfg.host, cfg.port), handler)
        print("IPAM listening on http://%s:%s (base=%s)" % (cfg.host, cfg.port, base or "/"), flush=True)

    def shutdown(signum, frame):
        print("IPAM stopping (signal %s)" % signum, flush=True)
        try:
            srv.shutdown()
        except Exception:
            pass
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        srv.serve_forever(poll_interval=0.3)
    finally:
        srv.server_close()
        if cfg.socket_path and os.path.exists(cfg.socket_path):
            os.unlink(cfg.socket_path)


def main():
    ap = argparse.ArgumentParser(description="IPAM fnOS service")
    ap.add_argument("--init", action="store_true", help="初始化数据库与管理员后退出")
    ap.add_argument("--admin", default="admin", help="初始管理员用户名")
    ap.add_argument("--password-stdin", action="store_true", help="从标准输入读取初始密码")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--host", default="")
    ap.add_argument("--socket", default="")
    args = ap.parse_args()

    if args.port:
        os.environ["IPAM_PORT"] = str(args.port)
    if args.host:
        os.environ["IPAM_HOST"] = args.host
    if args.socket:
        os.environ["IPAM_SOCKET_PATH"] = args.socket

    cfg = Config()
    init_db(cfg)
    if args.init:
        password = sys.stdin.read().strip() if args.password_stdin else os.environ.get("IPAM_INIT_PASSWORD", "")
        created = init_admin(cfg, args.admin, password)
        print("INIT_ADMIN_CREATED=%s" % ("1" if created else "0"))
        return 0
    run_server(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
