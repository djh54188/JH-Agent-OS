"""Hark-派单-05 synthetic browser preview server (Linux, cloud copy only).

Serves a GUI directory (index.html at /, files at /static/) and answers the
read-only API paths the 「Agent / 会话」 view calls with SYNTHETIC envelopes. It
does not import src/, does not read any registry or database, does not call a
model and has no write endpoint. Every synthetic run id starts with
"SYNTHETIC-"; nothing here is a real run record.

    python preview_server.py --gui <gui dir> --port <port>
"""
import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

RUN_IDS = ["SYNTHETIC-RUN-A", "SYNTHETIC-RUN-B", "SYNTHETIC-RUN-C", "SYNTHETIC-RUN-D"]
NOTE = "合成预览数据：非真实运行记录，不代表实时在线；declared_complete 是执行方声明，不代表任务验收成功。"


def run(run_id, index):
    return {
        "run_id": run_id, "events": ["reserved", "dispatched", "collected"],
        "app_kind": "codex", "instance_id": "codex-app-server/synthetic", "session_id": "new:synthetic",
        "role": "worker", "requested_model": "synthetic-model", "native_model": "synthetic-model",
        "logical_task_id": "synthetic-task-%d" % index, "attempt": 1,
        "request_fingerprint": "%x" % index * 64, "idempotency_key": "synthetic-key-%d" % index,
        "native_session_id": "synthetic-thread-%d" % index, "native_turns": ["synthetic-turn-%d" % index],
        "record_state": "declared_complete", "native_state": "completed",
        "first_recorded_at": "合成时间 %d" % index, "last_recorded_at": "合成时间 %d" % index,
        "usage": {"turn": {"totalTokens": 100 + index}, "thread_total": {"totalTokens": 900 + index}},
        "evidence": {"state": "missing", "detail": "合成预览：没有证据文件"},
        "unverified": ["合成预览数据，未核验"], "declared_status": "completed",
        "dispatch_uncertain_reason": None, "snapshot": {"synthetic": True},
    }


def envelope(path):
    if path == "/api/capabilities":
        return {"ok": True, "unsupported": ["dispatch"], "note": "合成预览：只读"}
    if path == "/api/sessions":
        return {"ok": True, "read_only": True, "transport": "static-registry", "count": 1,
                "sessions": [{"session_id": "sim-1", "app_kind": "zcode", "instance_id": "synthetic",
                              "role": "worker", "model_id": None, "connected": False, "simulated": True,
                              "evidence": {"source": "synthetic"}, "note": "未接入"}],
                "note": "全部为静态登记：connected=false、simulated=true"}
    if path == "/api/agent-runs":
        return {"ok": True, "read_only": True, "live": False, "state": "ok", "count": len(RUN_IDS),
                "message": "", "note": NOTE,
                "source": {"configured": True, "path": "synthetic-preview", "browser_supplied": False},
                "runs": [run(r, i + 1) for i, r in enumerate(RUN_IDS)]}
    if path.startswith("/api/agent-runs/"):
        run_id = unquote(path[len("/api/agent-runs/"):])
        if run_id not in RUN_IDS:
            return {"ok": True, "read_only": True, "state": "absent", "run": None,
                    "message": "合成预览：没有该运行记录", "records": []}
        detail = run(run_id, RUN_IDS.index(run_id) + 1)
        detail["link"] = {"checked": False, "linked": False, "note": "合成预览：未查询任务库"}
        return {"ok": True, "read_only": True, "state": "ok", "run": detail, "records": []}
    if path in ("/api/tasks", "/api/drafts"):
        return {"ok": True, "read_only": True, "database_present": False, "tasks": [], "drafts": [], "count": 0}
    return {"ok": False, "error": {"code": "not_found", "message": "合成预览没有该接口"}}


def make_handler(gui):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path.startswith("/api/"):
                self.send(200, json.dumps(envelope(path), ensure_ascii=False).encode(), "application/json; charset=utf-8")
                return
            name = "index.html" if path == "/" else path[len("/static/"):] if path.startswith("/static/") else None
            target = (gui / name).resolve() if name else None
            if not target or gui not in target.parents or not target.is_file():
                self.send(404, b"not found", "text/plain")
                return
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self.send(200, target.read_bytes(), ctype)

        def do_POST(self):
            self.send(405, json.dumps({"ok": False, "error": {"code": "read_only"}}).encode(), "application/json")
    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gui", required=True)
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(Path(args.gui).resolve()))
    print(server.server_address[1], flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
