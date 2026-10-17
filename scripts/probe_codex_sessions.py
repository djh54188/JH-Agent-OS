#!/usr/bin/env python
"""Independent probe for the Codex app-server protocol (JH-018 part 2, R1 hardened).

Talks JSON-RPC 2.0 over stdio to `codex app-server --stdio`: initialize,
thread/list, thread/read, an ownership check, thread/resume, then turn/start on
the addressed thread, and finally matches the completion notification to THIS
thread and THIS turn.

Ownership is checked BEFORE anything is sent. A thread is only addressed when
both of these hold:

  * its recorded `cwd` equals the isolated experiment directory, and
  * its preview contains a marker the caller created inside that experiment.

Passing a thread id alone, or overriding `--cwd`, is not evidence of isolation,
so the probe refuses (exit code 3, nothing sent) when either signal is missing.

Verdict rules (exit non-zero when any breaks): initialize/thread-read/resume/
turn-start errors or timeouts; completion never seen; completion for a different
threadId or a different turn id; final turn status not "completed"; a non-null
turn error; or a client-bound request this probe does not implement (unknown
requests are answered with an error and counted as failures).

`--selftest` runs the whole verdict logic against an offline fake transport and
asserts both directions. No model call, no app-server process.
"""
from __future__ import annotations

import argparse
import json
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

# Client-bound requests this probe implements. Anything else gets a JSON-RPC
# error and is recorded as an unimplemented interaction (never default success).
#   Approval / user-input requests are DECLINED, which withholds permission.
KNOWN_SERVER_REQUESTS = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "item/permissions/requestApproval": {"decision": "decline"},
    "item/tool/requestUserInput": {"answers": []},
    "applyPatchApproval": {"decision": "decline"},
    "execCommandApproval": {"decision": "decline"},
    "mcpServer/elicitation/request": {"action": "decline"},
}


class Verdict:
    def __init__(self):
        self.failures: list = []
        self.checks: list = []

    def require(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            self.failures.append(f"{name}: {detail}" if detail else name)
        return bool(ok)

    @property
    def ok(self) -> bool:
        return not self.failures


def _norm(path: str) -> str:
    return str(Path(path)).replace("\\", "/").rstrip("/").lower()


class CodexAppServer:
    """Real transport: a spawned `codex app-server --stdio` process."""

    def __init__(self, exe: str, cwd: str):
        self.proc = subprocess.Popen(
            [exe, "app-server", "--stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=cwd, text=True, encoding="utf-8", errors="replace", bufsize=1)
        self._q: "queue.Queue[str]" = queue.Queue()
        self._notes: list = []
        self._server_requests: list = []
        self._unknown: list = []
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
                self._server_requests.append(frame)
                method = frame.get("method")
                if method in KNOWN_SERVER_REQUESTS:
                    payload = {"jsonrpc": "2.0", "id": frame["id"],
                               "result": dict(KNOWN_SERVER_REQUESTS[method])}
                else:
                    self._unknown.append(method)
                    payload = {"jsonrpc": "2.0", "id": frame["id"],
                               "error": {"code": -32601,
                                         "message": f"probe does not implement {method}"}}
                self._write(payload)
                continue
            if frame.get("method"):
                self._notes.append(frame)
                continue
            self._q.put(line)

    def _write(self, payload: dict) -> None:
        try:
            self.proc.stdin.write(json.dumps(payload) + "\n")
            self.proc.stdin.flush()
        except Exception:
            pass

    def call(self, method: str, params: dict, timeout: float = 60.0) -> dict:
        self._id += 1
        rid = self._id
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
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

    def notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

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

    def server_requests(self) -> list:
        return [f.get("method") for f in self._server_requests]

    def unknown_server_requests(self) -> list:
        return sorted(set(self._unknown))

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


class FakeCodexTransport:
    """Offline transport for --selftest."""

    def __init__(self, script: dict, notifications: list | None = None,
                 unknown: list | None = None):
        self.script = {k: list(v) for k, v in script.items()}
        self._notes = list(notifications or [])
        self._unknown = list(unknown or [])
        self.calls: list = []

    def call(self, method: str, params: dict, timeout: float = 60.0) -> dict:
        self.calls.append({"method": method, "params": params})
        frames = self.script.get(method)
        if not frames:
            return {"method": method, "timeout": True}
        return frames.pop(0) if len(frames) > 1 else frames[0]

    def notify(self, method: str, params: dict) -> None:
        self.calls.append({"method": method, "params": params, "notification": True})

    def wait_notification(self, method: str, timeout: float) -> dict | None:
        for note in self._notes:
            if note.get("method") == method:
                return note
        return None

    def notification_methods(self) -> list:
        return sorted({n.get("method") for n in self._notes})

    def server_requests(self) -> list:
        return []

    def unknown_server_requests(self) -> list:
        return sorted(set(self._unknown))

    def close(self) -> None:
        pass


def _key_params(frame: dict) -> dict:
    result = frame.get("result") if isinstance(frame.get("result"), dict) else {}
    return {k: result.get(k) for k in sorted(result)}


class OwnershipRefused(RuntimeError):
    pass


def check_ownership(thread: dict, expect_cwd: str, expect_preview: str) -> tuple:
    """Return (ok, observed). Both signals are required."""
    observed = {"cwd": thread.get("cwd"), "preview": thread.get("preview"),
                "id": thread.get("id"), "path": thread.get("path"),
                "source": thread.get("source")}
    reasons = []
    if not expect_cwd:
        reasons.append("no --expect-cwd supplied: isolation unproven")
    elif _norm(thread.get("cwd") or "") != _norm(expect_cwd):
        reasons.append(f"thread cwd {thread.get('cwd')!r} != isolated dir {expect_cwd!r}")
    if not expect_preview:
        reasons.append("no --expect-preview marker supplied: ownership unproven")
    elif expect_preview not in str(thread.get("preview") or ""):
        reasons.append(f"marker {expect_preview!r} absent from thread preview")
    return (not reasons), {"observed": observed, "reasons": reasons}


def run_probe(transport, thread: str, cwd: str, text: str, expect_preview: str,
              turn_timeout: float = 300.0) -> tuple:
    verdict = Verdict()
    report: dict = {"thread": thread, "isolated_cwd": cwd}
    verdict.require("expect_cwd_supplied", bool(cwd), "isolated cwd is required")

    init = transport.call("initialize", {
        "clientInfo": {"name": "jh018-probe", "version": "0.1", "title": "JH-018 probe"}}, timeout=60)
    report["initialize"] = _key_params(init) if "result" in init else {"error": init.get("error")}
    verdict.require("initialize", not init.get("timeout") and "result" in init,
                    str(init.get("error") or "timeout"))
    transport.notify("initialized", {})

    lst = transport.call("thread/list", {}, timeout=60)
    result = lst.get("result") if isinstance(lst.get("result"), dict) else {}
    threads = result.get("data") if isinstance(result.get("data"), list) else []
    report["thread_list"] = {"ok": "result" in lst, "count": len(threads)}
    verdict.require("thread_list", not lst.get("timeout") and "result" in lst,
                    str(lst.get("error") or "timeout"))

    read = transport.call("thread/read", {"threadId": thread}, timeout=60)
    report["thread_read"] = _key_params(read) if "result" in read else {"error": read.get("error")}
    if read.get("timeout") or "result" not in read:
        verdict.require("thread_read", False, str(read.get("error") or "timeout"))
        return report, verdict, False
    verdict.require("thread_read", True)
    thread_obj = (read.get("result") or {}).get("thread") or {}
    report["thread"] = {k: thread_obj.get(k) for k in
                        ("id", "cwd", "preview", "source", "model", "modelProvider", "status")}

    own_ok, own = check_ownership(thread_obj, cwd, expect_preview)
    report["ownership"] = own
    verdict.require("ownership", own_ok, "; ".join(own["reasons"]))
    if not own_ok:
        # Safety refusal: nothing is sent to a thread we cannot prove is ours.
        report["refused_before_send"] = True
        return report, verdict, True

    resume = transport.call("thread/resume", {"threadId": thread, "cwd": cwd}, timeout=90)
    report["thread_resume"] = _key_params(resume) if "result" in resume else {"error": resume.get("error")}
    verdict.require("thread_resume", not resume.get("timeout") and "result" in resume,
                    str(resume.get("error") or "timeout"))

    turn = transport.call("turn/start", {
        "threadId": thread,
        "input": [{"type": "text", "text": text}],
        "cwd": cwd,
        "approvalPolicy": "never",
        "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
    }, timeout=60)
    started_turn_id = None
    if "result" in turn:
        started_turn_id = ((turn.get("result") or {}).get("turn") or {}).get("id")
    report["turn_start"] = {"ok": "result" in turn, "turn_id": started_turn_id,
                            "error": turn.get("error") if "result" not in turn else None}
    verdict.require("turn_start", not turn.get("timeout") and "result" in turn,
                    str(turn.get("error") or "timeout"))
    verdict.require("turn_start.turn_id", bool(started_turn_id), "no turn id returned")
    if not started_turn_id:
        return report, verdict, False

    done = transport.wait_notification("turn/completed", timeout=turn_timeout)
    verdict.require("turn_completed_seen", done is not None, f"no completion within {turn_timeout}s")
    if done is None:
        report["notifications_seen"] = transport.notification_methods()
        return report, verdict, False

    params = done.get("params") or {}
    completed_turn = params.get("turn") or {}
    report["turn_completed"] = {"threadId": params.get("threadId"),
                                "turn_id": completed_turn.get("id"),
                                "status": completed_turn.get("status"),
                                "error": completed_turn.get("error")}
    verdict.require("turn_completed.thread_match", params.get("threadId") == thread,
                    f"completed threadId {params.get('threadId')!r} != {thread!r}")
    verdict.require("turn_completed.turn_match", completed_turn.get("id") == started_turn_id,
                    f"completed turn {completed_turn.get('id')!r} != started {started_turn_id!r}")
    verdict.require("turn_completed.status_completed", completed_turn.get("status") == "completed",
                    f"status={completed_turn.get('status')!r}")
    verdict.require("turn_completed.no_error", not completed_turn.get("error"),
                    f"turn error={completed_turn.get('error')!r}")
    report["notifications_seen"] = transport.notification_methods()
    unknown = transport.unknown_server_requests()
    verdict.require("no_unknown_server_requests", not unknown,
                    f"server sent unimplemented client-bound requests: {unknown}")
    return report, verdict, False


# --------------------------------------------------------------------------
# Offline self-test.
# --------------------------------------------------------------------------

THREAD = "01a14970-40b9-7f40-a118-a32d91e19172"
CWD = r"D:\tmp\jh018-codex"
MARK = "PROBE_OK"
TURN_ID = "01a14976-b98e-7cd2-91a0-0aeff3aa54ad"


def _thread_obj(**over):
    obj = {"id": THREAD, "cwd": CWD, "preview": f"Reply with exactly the token {MARK}",
           "source": "exec", "model": "gpt-6.1-sol", "modelProvider": "openai",
           "status": {"type": "notLoaded"}}
    obj.update(over)
    return obj


def _completed(thread_id=THREAD, turn_id=TURN_ID, status="completed", error=None):
    return {"method": "turn/completed", "params": {
        "threadId": thread_id,
        "turn": {"id": turn_id, "status": status, "error": error, "items": []}}}


def _happy_script():
    return {
        "initialize": [{"id": 1, "result": {"codexHome": "C:/x", "platformOs": "windows",
                                            "platformFamily": "windows", "userAgent": "p"}}],
        "thread/list": [{"id": 2, "result": {"data": [], "nextCursor": None, "backwardsCursor": None}}],
        "thread/read": [{"id": 3, "result": {"thread": _thread_obj()}}],
        "thread/resume": [{"id": 4, "result": {"cwd": CWD}}],
        "turn/start": [{"id": 5, "result": {"turn": {"id": TURN_ID, "status": "inProgress"}}}],
    }


def _run_case(script, notifications, unknown=None, cwd=CWD, marker=MARK):
    transport = FakeCodexTransport(script, notifications, unknown)
    report, verdict, refused = run_probe(transport, THREAD, cwd, "prompt", marker)
    sent = [c["method"] for c in transport.calls if not c.get("notification")]
    return report, verdict, refused, sent


def selftest() -> tuple:
    cases = {}
    report, verdict, refused, sent = _run_case(_happy_script(), [_completed()])
    cases["happy_path"] = {"ok": verdict.ok and not refused, "failures": verdict.failures,
                           "calls": sent}

    def case(name, script=None, notes=None, unknown=None, cwd=CWD, marker=MARK):
        report, verdict, refused, sent = _run_case(script or _happy_script(),
                                                  notes if notes is not None else [_completed()],
                                                  unknown, cwd, marker)
        cases[name] = {"detected": (not verdict.ok), "refused": refused,
                       "failures": verdict.failures, "calls": sent}
        return sent

    case("initialize_error", script={**_happy_script(),
                                     "initialize": [{"id": 1, "error": {"code": -32000, "message": "no"}}]})
    case("thread_read_error", script={**_happy_script(),
                                      "thread/read": [{"id": 3, "error": {"code": -32600, "message": "missing"}}]})
    sent = case("ownership_wrong_cwd", cwd=r"D:\tmp\somewhere-else")
    cases["ownership_wrong_cwd"]["did_not_send"] = not any(
        m in sent for m in ("thread/resume", "turn/start"))
    sent = case("ownership_marker_missing", marker="NOT_PRESENT_MARKER")
    cases["ownership_marker_missing"]["did_not_send"] = not any(
        m in sent for m in ("thread/resume", "turn/start"))
    case("resume_error", script={**_happy_script(),
                                 "thread/resume": [{"id": 4, "error": {"code": -32000, "message": "no"}}]})
    case("turn_start_error", script={**_happy_script(),
                                     "turn/start": [{"id": 5, "error": {"code": -32000, "message": "no"}}]})
    case("completion_timeout", notes=[])
    case("completion_wrong_thread", notes=[_completed(thread_id="01a00000-0000-7000-8000-000000000000")])
    case("completion_wrong_turn", notes=[_completed(turn_id="01a00000-0000-7000-8000-111111111111")])
    case("completion_status_failed", notes=[_completed(status="failed")])
    case("completion_error_present", notes=[_completed(error={"code": "boom"})])
    case("unknown_server_request", unknown=["mcpServer/mystery/request"])

    ok = cases["happy_path"]["ok"] and all(
        v.get("detected") for k, v in cases.items() if k != "happy_path")
    return {"cases": cases, "all_faults_detected": ok}, ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Codex app-server capability probe")
    ap.add_argument("--thread", help="Thread id to address (must be provably isolated)")
    ap.add_argument("--cwd", default=r"D:\tmp\jh018-codex")
    ap.add_argument("--expect-preview", default="",
                    help="Marker created inside the isolated experiment, expected in the thread preview")
    ap.add_argument("--text", default="Reply with exactly the token PROBE_APPSERVER")
    ap.add_argument("--exe", default=None)
    ap.add_argument("--selftest", action="store_true",
                    help="offline fake-transport positive/negative control; no model call")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    if args.selftest:
        body, ok = selftest()
        text = json.dumps({"probe": "codex-selftest", **body}, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
        return 0 if ok else 1

    if not args.thread:
        print(json.dumps({"error": "--thread is required"}, ensure_ascii=False))
        return 2
    exe = args.exe or shutil.which("codex")
    if not exe:
        print(json.dumps({"error": "codex not found on PATH"}, ensure_ascii=False))
        return 1

    transport = CodexAppServer(exe, cwd=args.cwd)
    try:
        report, verdict, refused = run_probe(transport, args.thread, args.cwd, args.text,
                                             args.expect_preview)
        report["codex_exe"] = exe
        report["server_requests"] = transport.server_requests()
        report["unknown_server_requests"] = transport.unknown_server_requests()
    finally:
        transport.close()

    report["probe"] = "codex-app-server"
    report["failures"] = verdict.failures
    report["checks"] = verdict.checks
    report["passed"] = verdict.ok and not refused
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    if refused:
        return 3          # safety refusal: isolation unproven, nothing sent
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
