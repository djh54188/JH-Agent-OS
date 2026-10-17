#!/usr/bin/env python
"""Independent capability probe for the ZCode app-server (JH-018).

Speaks the ZCode Protocol over stdio (newline-delimited JSON) to a freshly
spawned `zcode app-server --stdio` process. It never talks to a session the
user is working in: every write is confined to a session this script creates
inside an isolated workspace directory it owns.

Modes:
  --enumerate     Call each known protocol method with empty params and record
                  whether it is accepted and which params its schema requires.
  --roundtrip     create -> send -> read -> messages -> usage -> stop, then a
                  second session used to exercise cancel; prints one JSON report.

Usage:
  python scripts/probe_zcode_capabilities.py --enumerate --out <file.json>
  python scripts/probe_zcode_capabilities.py --roundtrip --workspace D:/tmp/jh018-probe --out <file.json>

This is a read-mostly probe of a third-party local interface. It proves nothing
about JH task success, auth, or evidence trust; it reports only what the local
installation actually answers. Production complete stays closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

DEFAULT_CJS = Path(os.environ.get(
    "ZCODE_CJS",
    r"C:\Users\a\AppData\Local\Programs\ZCode\resources\glm\zcode.cjs"))

# The app-server issues server->client requests and blocks until the client
# answers. `session/requestRuntimePreferences` is required by `session/create`;
# its schema needs nativeSearchEnhancementsEnabled (boolean).
SERVER_REQUEST_REPLIES = {
    "session/requestRuntimePreferences": {
        "nativeSearchEnhancementsEnabled": True,
        "memoryEnabled": False,
        "askUserQuestionAutoResolutionEnabled": False,
        "modelContextBudgetStrategy": "preflight-v1",
    },
    "interaction/requestPermission": {"decision": "deny"},
    "interaction/requestUserInput": {"answers": []},
}

# Protocol surface discovered from the shipped bundle (`session/*`, `usage/*`,
# `controller/*`, `interaction/*`, `workspace/*`, `mcp/*`, `automation/*`).
ENUMERATE_METHODS = [
    "session/list", "session/create", "session/read", "session/resume",
    "session/send", "session/stop", "session/cancelBackgroundTask",
    "session/close", "session/compact", "session/fork", "session/debug",
    "session/messages", "session/events", "session/subscribe", "session/usage",
    "session/subagents", "session/setMode", "session/setModel",
    "session/setThoughtLevel", "session/goal", "session/requestRuntimePreferences",
    "usage/stats", "workspace/generateText", "workspace/cancelGenerateText",
    "controller/tasks-index", "controller/workspaces",
]


class AppServer:
    """Minimal ZCode Protocol client over a spawned app-server process."""

    def __init__(self, cjs: Path, cwd: str, ready_timeout: float = 40.0):
        self.cjs = Path(cjs)
        self.proc = subprocess.Popen(
            ["node", str(self.cjs), "app-server", "--stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=cwd, text=True, encoding="utf-8", errors="replace", bufsize=1)
        self._q: "queue.Queue[str]" = queue.Queue()
        self._notifications: list = []
        self._server_requests: list = []
        self._next_id = 0
        threading.Thread(target=self._pump, daemon=True).start()
        self.ready = self._await_ready(ready_timeout)

    def _pump(self) -> None:
        """Read stdout. Server-initiated requests (id + method) are answered
        at once with an empty result and recorded, so the server never blocks
        waiting on this client."""
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except ValueError:
                self._q.put(line)
                continue
            if frame.get("method") and "id" in frame:
                self._server_requests.append(frame)
                try:
                    result = dict(SERVER_REQUEST_REPLIES.get(frame.get("method"), {}))
                    self.proc.stdin.write(json.dumps({"id": frame["id"], "result": result}) + "\n")
                    self.proc.stdin.flush()
                except Exception:
                    pass
                continue
            self._q.put(line)

    def server_requests(self) -> list:
        return [{"method": f.get("method"), "params": f.get("params")}
                for f in self._server_requests]

    def _await_ready(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                frame = json.loads(raw)
            except ValueError:
                continue
            if frame.get("method") == "startup/storageState":
                if frame.get("params", {}).get("phase") == "ready":
                    return True
        return False

    def request(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        self._next_id += 1
        rid = self._next_id
        self.proc.stdin.write(json.dumps({"id": rid, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                frame = json.loads(raw)
            except ValueError:
                continue
            if frame.get("id") == rid:
                return frame
            if frame.get("method"):
                self._notifications.append(frame)
        return {"id": rid, "timeout": True, "method": method}

    def notification_methods(self) -> list:
        return sorted({f.get("method") for f in self._notifications if f.get("method")})

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sid(frame: dict):
    """session/create returns a full snapshot; sessionId lives under .session."""
    result = frame.get("result") if isinstance(frame.get("result"), dict) else {}
    session = result.get("session") if isinstance(result.get("session"), dict) else {}
    return session.get("sessionId") or result.get("sessionId")


def _summarize(frame: dict) -> dict:
    """Reduce one response to a stable, reviewable shape."""
    if "result" in frame:
        result = frame["result"]
        shape = sorted(result.keys()) if isinstance(result, dict) else type(result).__name__
        return {"ok": True, "result_keys": shape}
    err = frame.get("error") or {}
    data = err.get("data") or {}
    paths = []
    try:
        paths = [e.get("path") for e in json.loads(data.get("message", "[]"))]
    except Exception:
        pass
    if not paths:
        for part in str(err.get("message", "")).split(";"):
            part = part.strip()
            if ":" in part:
                paths.append([part.split(":")[0].strip()])
    return {"ok": False, "code": err.get("code"), "required_params": paths}


def run_enumerate(server: AppServer) -> dict:
    out = {}
    for method in ENUMERATE_METHODS:
        out[method] = _summarize(server.request(method, {}, timeout=20.0))
    return out


def _status(server: AppServer, sid: str) -> dict:
    rd = server.request("session/read", {"sessionId": sid}, timeout=30)
    result = rd.get("result") if isinstance(rd.get("result"), dict) else {}
    projection = result.get("projection") if isinstance(result.get("projection"), dict) else {}
    return {"status": projection.get("status"), "turnCount": projection.get("turnCount"),
            "pendingPermissions": len(projection.get("pendingPermissions") or []),
            "keys": sorted(result.keys()) if result else None}


def _model_selection(model: str, reasoning_level: str = "high") -> dict:
    """modelSelection schema: {providerId, modelId, options?: {reasoningLevel}}.
    Some providers (e.g. exo-free) refuse a selection without a reasoning level."""
    provider_id, _, model_id = model.partition("/")
    selection = {"providerId": provider_id, "modelId": model_id}
    if reasoning_level:
        selection["options"] = {"reasoningLevel": reasoning_level}
    return selection


def _poll_until_settled(server: AppServer, sid: str, timeout: float) -> dict:
    """Watch the status transitions the session reports. A turn starts a beat
    after `session/send` returns, so `idle` only counts as settled once the
    session has actually been observed active."""
    seen = []
    turns = 0
    saw_active = False
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = _status(server, sid)
        status = st.get("status")
        turns = max(turns, st.get("turnCount") or 0)
        if not seen or seen[-1] != status:
            seen.append(status)
        if status in ("running", "queued", "paused"):
            saw_active = True
        if saw_active and status in ("idle", "error", "failed", "stopped", "complete"):
            break
        time.sleep(2)
    return {"transitions": seen, "final": seen[-1] if seen else None, "turnCount": turns}


def run_roundtrip(server: AppServer, workspace: str, model: str, reasoning_level: str = "high") -> dict:
    report: dict = {"workspace": workspace, "model_pinned": model}
    created = server.request("session/create", {"workspace": {"workspaceKey": workspace, "workspacePath": workspace}})
    sid = _sid(created)
    report["create_session_id"] = sid
    report["create_ok"] = "result" in created
    if not sid:
        report["create_error"] = created.get("error")
        return report

    report["read_after_create"] = _status(server, sid)
    report["setModel_object"] = _summarize(server.request(
        "session/setModel", {"sessionId": sid, "model": _model_selection(model, reasoning_level)}))
    report["setModel_string"] = _summarize(
        server.request("session/setModel", {"sessionId": sid, "model": _model_selection(model, reasoning_level)}))
    report["read_after_setModel"] = _status(server, sid)

    sent = server.request("session/send", {
        "sessionId": sid,
        "content": "Reply with exactly the token PROBE_OK and nothing else.",
    })
    report["send_dispatched"] = "result" in sent
    report["send"] = _summarize(sent)
    report["lifecycle"] = _poll_until_settled(server, sid, timeout=240)

    msgs = server.request("session/messages", {"sessionId": sid}, timeout=60)
    result = msgs.get("result") if isinstance(msgs.get("result"), dict) else {}
    items = result.get("messages") if isinstance(result.get("messages"), list) else []
    report["messages_ok"] = "result" in msgs
    report["messages_count"] = len(items)
    report["messages_keys"] = sorted(result.keys()) if result else None
    report["usage"] = server.request("session/usage", {"sessionId": sid}, timeout=60)
    report["stop"] = server.request("session/stop", {"sessionId": sid}, timeout=30)
    report["read_after_stop"] = _status(server, sid)
    return report


def run_cancel_trial(server: AppServer, workspace: str, model: str, reasoning_level: str = "high") -> dict:
    """Dispatch a longer task, then stop it mid-flight to observe cancel."""
    report: dict = {"model_pinned": model}
    created = server.request("session/create", {"workspace": {"workspaceKey": workspace, "workspacePath": workspace}})
    sid = _sid(created)
    report["session_id"] = sid
    if not sid:
        report["create_error"] = created.get("error")
        return report
    server.request("session/setModel", {"sessionId": sid, "model": _model_selection(model, reasoning_level)})
    server.request("session/send", {
        "sessionId": sid,
        "content": "Count from 1 to 400 slowly, one number per line, then say DONE.",
    })
    deadline = time.time() + 60
    while time.time() < deadline:
        st = _status(server, sid)
        if st.get("status") in ("running", "queued"):
            break
        time.sleep(2)
    report["status_before_stop"] = _status(server, sid)
    report["stop"] = _summarize(server.request("session/stop", {"sessionId": sid}))
    report["after_stop"] = _poll_until_settled(server, sid, timeout=60)
    return report


def run_events_trial(server: AppServer, workspace: str, model: str, reasoning_level: str = "high") -> dict:
    """Check whether session/events and session/subscribe expose run events."""
    report: dict = {}
    created = server.request("session/create", {"workspace": {"workspaceKey": workspace, "workspacePath": workspace}})
    sid = _sid(created)
    report["session_id"] = sid
    if not sid:
        report["create_error"] = created.get("error")
        return report
    server.request("session/setModel", {"sessionId": sid, "model": _model_selection(model, reasoning_level)})
    server.request("session/send", {
        "sessionId": sid,
        "content": "Reply with exactly the token PROBE_OK and nothing else.",
    })
    report["events"] = server.request("session/events", {"sessionId": sid}, timeout=30)
    _poll_until_settled(server, sid, timeout=180)
    report["events_after"] = server.request("session/events", {"sessionId": sid}, timeout=30)
    report["subscribe"] = _summarize(server.request(
        "session/subscribe", {"sessionId": sid, "deliveryKind": "desktop-continuous"}, timeout=20))
    report["subagents"] = _summarize(server.request("session/subagents", {"sessionId": sid}))
    report["notifications_seen"] = server.notification_methods()
    server.request("session/stop", {"sessionId": sid})
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ZCode app-server capability probe")
    ap.add_argument("--enumerate", action="store_true")
    ap.add_argument("--roundtrip", action="store_true")
    ap.add_argument("--cancel-trial", action="store_true")
    ap.add_argument("--events-trial", action="store_true")
    ap.add_argument("--workspace", default=r"D:\tmp\jh018-probe")
    ap.add_argument("--model", default="new-provider-3/exo-free")
    ap.add_argument("--reasoning-level", default="high")
    ap.add_argument("--cjs", default=str(DEFAULT_CJS))
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    cjs = Path(args.cjs)
    if not cjs.is_file():
        print(json.dumps({"error": f"zcode bundle not found: {cjs}"}, ensure_ascii=False))
        return 1
    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    server = AppServer(cjs, cwd=str(workspace))
    report = {
        "probe": "zcode-app-server",
        "zcode_bundle": str(cjs),
        "zcode_bundle_sha256": _sha256(cjs),
        "app_server_ready": server.ready,
        "isolated_workspace": str(workspace),
    }
    try:
        if args.enumerate:
            report["enumerate"] = run_enumerate(server)
        if args.roundtrip:
            report["roundtrip"] = run_roundtrip(server, str(workspace), args.model, args.reasoning_level)
        if args.cancel_trial:
            report["cancel_trial"] = run_cancel_trial(server, str(workspace), args.model, args.reasoning_level)
        if args.events_trial:
            report["events_trial"] = run_events_trial(server, str(workspace), args.model, args.reasoning_level)
        report["notifications_seen"] = server.notification_methods()
        report["server_initiated_requests"] = server.server_requests()
    finally:
        server.close()

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
