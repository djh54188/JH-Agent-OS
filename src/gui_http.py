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

from src.app_api import ApiError, AppInterface, ProbeConfig

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
            elif path == "/api/session-probe":
                self._json(200, self.api.session_probe_status())
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
        if path in PROBE_ACTION_PATHS:
            # Transport-level request hygiene BEFORE the body is read or the
            # application interface is called: a cross-site, non-loopback-host
            # or non-JSON request never reaches the probe service.
            allowed, reason = probe_request_allowed(self.headers, self.server.server_address)
            if not allowed:
                self._json(403, {"ok": False, "read_only": True,
                                 "error": {"code": "probe_origin_refused",
                                           "message": f"拒绝非本页同源请求：{reason}；未派发"}})
                return
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
            elif path == "/api/session-probe/prepare":
                self._json(200, self.api.prepare_session_probe(payload))
            elif path == "/api/session-probe/start":
                self._json(202, self.api.start_session_probe(payload))
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


PROBE_ACTION_PATHS = frozenset({"/api/session-probe/prepare", "/api/session-probe/start"})


def probe_request_allowed(headers, server_address) -> tuple:
    """(allowed, reason) for a probe action request.

    The page that may act is the one this server served: the Host header must be
    a loopback name with this server's port, the Origin must be exactly that
    page's origin (a request without Origin is refused), a Fetch-Metadata site
    other than same-origin is refused, and the body must be JSON (which a
    cross-site form or simple request cannot send without a CORS preflight this
    server never answers).
    """
    port = server_address[1]
    host_header = (headers.get("Host") or "").strip()
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    if host_header not in allowed_hosts:
        return False, f"Host {host_header!r} 不是本机环回地址与本服务端口"
    origin = (headers.get("Origin") or "").strip()
    if origin != f"http://{host_header}":
        return False, f"Origin {origin!r} 与本页来源 http://{host_header} 不一致"
    site = (headers.get("Sec-Fetch-Site") or "").strip()
    if site and site != "same-origin":
        return False, f"Sec-Fetch-Site={site!r}"
    ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return False, f"Content-Type {ctype!r} 不是 application/json"
    return True, ""


def make_server(host: str = "127.0.0.1", port: int = 8765,
                db_path: str | None = None, drafts_path: str | None = None,
                runs_path: str | None = None, evidence_roots=None,
                gui_dir: Path | None = None, session_probe: ProbeConfig | None = None,
                probe_adapter_factory=None, probe_runtime_check=None,
                probe_runner=None) -> ThreadingHTTPServer:
    host = validate_host(host)                      # refuse before binding
    handler = type("BoundGuiHandler", (GuiHandler,), {})
    handler.api = AppInterface(db_path=db_path, drafts_path=drafts_path,
                               runs_path=runs_path, evidence_roots=evidence_roots,
                               session_probe=session_probe,
                               probe_adapter_factory=probe_adapter_factory,
                               probe_runtime_check=probe_runtime_check,
                               probe_runner=probe_runner)
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
    ap.add_argument("--evidence-root", action="append", default=None,
                    help="允许读取证据的根目录（可重复；服务端配置）")
    ap.add_argument("--enable-session-probe", action="store_true",
                    help="启用 GUI 固定标识会话试运行（默认关闭；每次仍须用户确认）")
    ap.add_argument("--session-probe-dir", default=None,
                    help="试运行专用登记/证据/隔离工作区目录（不得与 --runs 同一文件）")
    ap.add_argument("--session-probe-model", default=None, help="试运行模型（服务端决定）")
    ap.add_argument("--session-probe-budget-tokens", type=int, default=None,
                    help="服务端要求的 token 上限；适配器无法强制，设置后将拒发")
    args = ap.parse_args(argv)

    probe = ProbeConfig(enabled=bool(args.enable_session_probe),
                        probe_dir=args.session_probe_dir,
                        model_id=args.session_probe_model or ProbeConfig.model_id,
                        budget_tokens=args.session_probe_budget_tokens)
    httpd = make_server(args.host, args.port, args.db, args.drafts, args.runs,
                        args.evidence_root, session_probe=probe)
    host, port = httpd.server_address[0], httpd.server_address[1]
    mode = "会话试运行已启用（逐次确认）" if probe.enabled else "不触发执行"
    print(f"JH-Agent OS GUI: http://{host}:{port}/  (只读 + 草案；{mode})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
