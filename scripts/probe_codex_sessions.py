#!/usr/bin/env python
"""Independent probe for the Codex app-server protocol (JH-018, part 2).

Talks JSON-RPC 2.0 over stdio to `codex app-server --stdio`: initialize,
thread/list, thread/read, then turn/start on a thread this probe is pointed at,
collecting the notifications the server actually emits.

It only sends to a thread given on the command line, which must be a thread the
caller created in an isolated directory. It never selects a thread belonging to
someone else's live session, and never starts a review against the user's own
agent thread.

Usage:
  python scripts/probe_codex_sessions.py --thread <thread-id> --cwd D:/tmp/jh018-codex \
      --text "Reply with exactly PROBE_APPSERVER" --out <file.json>

Reports only what the local installation answers. Success here is interface
capability, not JH task success; production complete stays closed.
"""
from __future__ import annotations

import argparse
import json
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path


class CodexAppServer:
    """Minimal JSON-RPC 2.0 client for `codex app-server --stdio`."""

    def __init__(self, exe: str, cwd: str, timeout_s: float = 60.0):
        self.proc = subprocess.Popen(
            [exe, "app-server", "--stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=cwd, text=True, encoding="utf-8", errors="replace", bufsize=1)
        self._q: "queue.Queue[str]" = queue.Queue()
        self._server_requests: list = []
        self._notes: list = []
        self._id = 0
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            if frame.get("method") and "id" in frame:
                # server -> client request (approvals, user input): record and deny
                self._server_requests.append(frame)
                self._reply(frame["id"], {"decision": "decline"})
                continue
            if frame.get("method"):
                self._notes.append(frame)
                continue
            self._q.put(line)

    def _reply(self, rid, result) -> None:
        try:
            self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
            self.proc.stdin.flush()
        except Exception:
            pass

    def call(self, method: str, params: dict, timeout: float = 60.0) -> dict:
        self._id += 1
        rid = self._id
        self.proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            frame = json.loads(raw)
            if frame.get("id") == rid:
                return frame
        return {"id": rid, "timeout": True, "method": method}

    def wait_notification(self, method: str, timeout: float) -> dict | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for note in list(self._notes):
                if note.get("method") == method:
                    return note
            time.sleep(0.5)
        return None

    def notification_methods(self) -> list:
        seen = []
        for note in self._notes:
            if note.get("method") not in seen:
                seen.append(note.get("method"))
        return seen

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def _key_params(frame: dict) -> dict:
    result = frame.get("result") if isinstance(frame.get("result"), dict) else {}
    return {k: result.get(k) for k in sorted(result)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Codex app-server capability probe")
    ap.add_argument("--thread", required=True, help="Isolated thread id to address")
    ap.add_argument("--cwd", default=r"D:\tmp\jh018-codex")
    ap.add_argument("--text", default="Reply with exactly the token PROBE_APPSERVER")
    ap.add_argument("--exe", default=None)
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    exe = args.exe or shutil.which("codex")
    if not exe:
        print(json.dumps({"error": "codex not found on PATH"}, ensure_ascii=False))
        return 1

    server = CodexAppServer(exe, cwd=args.cwd)
    report: dict = {"probe": "codex-app-server", "codex_exe": exe, "thread": args.thread}
    try:
        init = server.call("initialize", {
            "clientInfo": {"name": "jh018-probe", "version": "0.1", "title": "JH-018 probe"}})
        report["initialize"] = _key_params(init)
        # Some servers expect the `initialized` notification before other calls.
        try:
            server.proc.stdin.write(json.dumps(
                {"jsonrpc": "2.0", "method": "initialized", "params": {}}) + "\n")
            server.proc.stdin.flush()
        except Exception:
            pass

        lst = server.call("thread/list", {}, timeout=60)
        result = lst.get("result") if isinstance(lst.get("result"), dict) else {}
        threads = result.get("data") if isinstance(result.get("data"), list) else []
        report["thread_list_keys"] = sorted(result)
        report["thread_list_count"] = len(threads)
        report["thread_list_ids"] = [t.get("id") or t.get("threadId") for t in threads[:5]]

        read = server.call("thread/read", {"threadId": args.thread}, timeout=60)
        report["thread_read_ok"] = "result" in read
        report["thread_read_keys"] = sorted((read.get("result") or {})) if "result" in read else None
        report["thread_read_error"] = read.get("error")

        # A persisted thread is `notLoaded`; resume it before starting a turn.
        resume = server.call("thread/resume", {"threadId": args.thread, "cwd": args.cwd}, timeout=90)
        report["thread_resume_ok"] = "result" in resume
        report["thread_resume_error"] = resume.get("error") if "result" not in resume else None

        turn = server.call("turn/start", {
            "threadId": args.thread,
            "input": [{"type": "text", "text": args.text}],
            "cwd": args.cwd,
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
        }, timeout=60)
        report["turn_start"] = _key_params(turn) if "result" in turn else {"error": turn.get("error")}
        report["turn_start_ok"] = "result" in turn

        done = server.wait_notification("turn/completed", timeout=300)
        report["turn_completed_seen"] = done is not None
        if done:
            report["turn_completed_params_keys"] = sorted(done.get("params") or {})
        report["notifications_seen"] = server.notification_methods()
        report["server_requests"] = [r.get("method") for r in server._server_requests]
    finally:
        server.close()

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
