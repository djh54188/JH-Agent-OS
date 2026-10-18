#!/usr/bin/env python
"""JH-019 minimal local GUI server (transport only).

A stdlib-only HTTP server that (a) serves the static presentation assets from
`gui/` and (b) exposes the unified application interface (`src.app_api`) as JSON
under `/api/...`. It contains no business rules: every endpoint delegates to the
application interface, and the static files contain no database access, no tool
launching and no success decisions.

Runs locally on 127.0.0.1 only. This batch never triggers an Agent, a model or a
tool; `/api/drafts` only stores a marked draft.

Usage:
    python -m src.gui_http --port 8765 [--db <sqlite>] [--drafts <json>]
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from src.app_api import ApiError, AppInterface

ROOT = Path(__file__).resolve().parents[1]
GUI_DIR = ROOT / "gui"

# Only these static files are served; nothing outside gui/ is reachable.
STATIC_TYPES = {".html": "text/html; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8"}


class GuiHandler(BaseHTTPRequestHandler):
    server_version = "JH-GUI/0.1"
    api: AppInterface
    gui_dir: Path

    # -- helpers -----------------------------------------------------------
    def log_message(self, fmt, *args):        # quieter default logging
        sys.stderr.write("[gui] " + (fmt % args) + "\n")

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _api_error(self, exc: ApiError) -> None:
        self._json(exc.http_status, {"ok": False, "read_only": True,
                                     "error": {"code": exc.code, "message": exc.message}})

    # -- static ------------------------------------------------------------
    def _serve_static(self, rel: str) -> None:
        name = rel.lstrip("/") or "index.html"
        target = (self.gui_dir / name).resolve()
        if self.gui_dir.resolve() not in target.parents and target != self.gui_dir.resolve():
            self._json(403, {"ok": False, "error": {"code": "forbidden", "message": "越界路径"}})
            return
        if not target.is_file():
            self._json(404, {"ok": False, "error": {"code": "not_found",
                                                    "message": f"静态资源不存在：{name}"}})
            return
        ctype = STATIC_TYPES.get(target.suffix) or mimetypes.guess_type(name)[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)

    # -- routes ------------------------------------------------------------
    def do_GET(self) -> None:                                  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/" or path == "/index.html":
                self._serve_static("index.html")
            elif path.startswith("/static/"):
                self._serve_static(path[len("/static/"):])
            elif path == "/api/capabilities":
                self._json(200, self.api.capabilities())
            elif path == "/api/tasks":
                self._json(200, self.api.list_tasks())
            elif path.startswith("/api/tasks/") and path.endswith("/delivery"):
                task_id = path[len("/api/tasks/"):-len("/delivery")]
                self._json(200, self.api.review_delivery(task_id))
            elif path.startswith("/api/tasks/"):
                self._json(200, self.api.task_detail(path[len("/api/tasks/"):]))
            elif path == "/api/sessions":
                self._json(200, self.api.list_sessions())
            elif path.startswith("/api/agent-runs/"):
                run_id = path[len("/api/agent-runs/"):]
                self._json(200, self.api.agent_run_detail(run_id))
            elif path == "/api/agent-runs":
                self._json(200, self.api.list_agent_runs())
            elif path == "/api/drafts":
                self._json(200, self.api.list_drafts())
            else:
                self._json(404, {"ok": False, "error": {"code": "not_found",
                                                        "message": f"未知路径：{path}"}})
        except ApiError as exc:
            self._api_error(exc)
        except Exception as exc:                       # never drop the connection
            self._json(500, {"ok": False, "error": {"code": "internal_error",
                                                    "message": f"服务内部错误：{exc}"}})

    def do_POST(self) -> None:                                 # noqa: N802
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            self._json(400, {"ok": False, "error": {"code": "bad_json",
                                                    "message": "请求体不是合法 JSON"}})
            return
        try:
            if path == "/api/drafts":
                self._json(201, self.api.save_goal_draft(payload))
            else:
                self._json(404, {"ok": False, "error": {"code": "not_found",
                                                        "message": f"未知路径：{path}"}})
        except ApiError as exc:
            self._api_error(exc)
        except Exception as exc:                       # never drop the connection
            self._json(500, {"ok": False, "error": {"code": "internal_error",
                                                    "message": f"服务内部错误：{exc}"}})


# Only loopback binding is permitted in this batch. The check runs BEFORE the
# socket is created, so a non-loopback request fails without ever listening.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def validate_host(host: str) -> str:
    """Return the host if it is a loopback literal, else refuse.

    This batch deliberately exposes no remote access and no authentication
    platform: anything that would listen beyond loopback is rejected outright.
    """
    candidate = str(host).strip()
    if candidate not in LOOPBACK_HOSTS:
        raise ValueError(
            f"本批只允许环回绑定；拒绝主机 {candidate!r}（允许：{sorted(LOOPBACK_HOSTS)}）")
    return candidate


def make_server(host: str = "127.0.0.1", port: int = 8765,
                db_path: str | None = None, drafts_path: str | None = None,
                runs_path: str | None = None,
                gui_dir: Path | None = None) -> ThreadingHTTPServer:
    host = validate_host(host)                      # refuse before binding
    handler = type("BoundGuiHandler", (GuiHandler,), {})
    handler.api = AppInterface(db_path=db_path, drafts_path=drafts_path,
                               runs_path=runs_path)
    handler.gui_dir = Path(gui_dir) if gui_dir else GUI_DIR
    return ThreadingHTTPServer((host, int(port)), handler)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="JH-Agent OS minimal local GUI")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--db", default=None, help="任务数据库（只读打开）")
    ap.add_argument("--drafts", default=None, help="草案 JSON 路径")
    ap.add_argument("--runs", default=None,
                    help="运行登记 JSONL 路径（服务端配置；浏览器不能指定）")
    args = ap.parse_args(argv)

    httpd = make_server(args.host, args.port, args.db, args.drafts, args.runs)
    host, port = httpd.server_address[0], httpd.server_address[1]
    print(f"JH-Agent OS GUI: http://{host}:{port}/  (只读 + 草案；不触发执行)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
