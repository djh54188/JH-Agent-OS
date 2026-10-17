#!/usr/bin/env python
"""Independent capability probe for the ZCode app-server (JH-018, R1 hardened).

Speaks the ZCode Protocol over stdio (newline-delimited JSON) to a freshly
spawned `zcode app-server --stdio` process. Every write is confined to a
session this script creates inside an isolated workspace directory it owns; it
never talks to a session the user is working in.

Verdict rules (a probe run FAILS, exit non-zero, when any of these break):

* the app-server never reaches the ready state;
* a required step returns a protocol error, times out, or omits a result;
* `session/send` is not accepted, or is accepted for a DIFFERENT sessionId;
* the run never becomes active, or ends in `error`/`failed`/`stopped`;
* `session/usage` reports zero model requests, or belongs to another session;
* `session/messages` has no assistant message for the model we pinned;
* the server sends a client-bound request this probe does not implement
  (unknown requests are answered with an error and counted as failures —
  answering them "successfully" by default is forbidden).

`--enumerate` is a SEPARATE mode with a separate judgement: it only records,
per method, whether the method reached the server and whether its schema
rejected empty params. Its result is never used as a roundtrip success.

`--selftest` runs the whole verdict logic against an offline fake transport and
asserts both directions (happy path passes, each planted fault fails). It needs
no model call and no app-server process.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

DEFAULT_CJS = Path(os.environ.get(
    "ZCODE_CJS",
    r"C:\Users\a\AppData\Local\Programs\ZCode\resources\glm\zcode.cjs"))

# Client-bound requests this probe implements. Only methods listed here get a
# reply; the payloads are explicit and minimal, never a fabricated success.
#   session/requestRuntimePreferences: schema needs nativeSearchEnhancementsEnabled.
#   interaction/requestOfficialMcpAuthHeaders: {ok:true,headers:{}} states
#     "no official MCP auth headers are available" — an empty header set, not a
#     claim that anything ran.
#   interaction/requestPermission: deny.
#   interaction/requestUserInput: no answers supplied.
KNOWN_SERVER_REQUESTS = {
    "session/requestRuntimePreferences": {
        "nativeSearchEnhancementsEnabled": True,
        "memoryEnabled": False,
        "askUserQuestionAutoResolutionEnabled": False,
        "modelContextBudgetStrategy": "preflight-v1",
    },
    "interaction/requestOfficialMcpAuthHeaders": {"ok": True, "headers": {}},
    "interaction/requestPermission": {"decision": "deny"},
    "interaction/requestUserInput": {"answers": []},
}

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

ACTIVE_STATES = ("running", "queued", "paused")
TERMINAL_BAD = ("error", "failed", "stopped")


class Verdict:
    """Collects every check so one run reports all faults, not just the first."""

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


def _err(frame: dict) -> str:
    error = frame.get("error") or {}
    code = error.get("code")
    message = str(error.get("message") or error.get("data", {}).get("name") or "")
    return f"code={code} {message}".strip()


def _is_error(frame: dict) -> bool:
    return "error" in frame


def _is_timeout(frame: dict) -> bool:
    return bool(frame.get("timeout"))


def _result_of(frame: dict) -> dict:
    result = frame.get("result")
    return result if isinstance(result, dict) else {}


def _sid_of(frame: dict):
    result = _result_of(frame)
    session = result.get("session") if isinstance(result.get("session"), dict) else {}
    return session.get("sessionId") or result.get("sessionId")


class AppServer:
    """Real transport: a spawned `zcode app-server --stdio` process."""

    def __init__(self, cjs: Path, cwd: str, ready_timeout: float = 40.0):
        self.cjs = Path(cjs)
        self.proc = subprocess.Popen(
            ["node", str(self.cjs), "app-server", "--stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=cwd, text=True, encoding="utf-8", errors="replace", bufsize=1)
        self._q: "queue.Queue[str]" = queue.Queue()
        self._notifications: list = []
        self._server_requests: list = []
        self._unknown_requests: list = []
        self._next_id = 0
        threading.Thread(target=self._pump, daemon=True).start()
        self.ready = self._await_ready(ready_timeout)

    def _pump(self) -> None:
        """Answer client-bound requests. Known methods get their explicit
        payload; anything else is answered with an error and recorded, so the
        probe can never silently pass by faking support."""
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
                method = frame.get("method")
                if method in KNOWN_SERVER_REQUESTS:
                    payload = {"id": frame["id"], "result": dict(KNOWN_SERVER_REQUESTS[method])}
                else:
                    self._unknown_requests.append(method)
                    payload = {"id": frame["id"],
                               "error": {"code": -32601,
                                         "message": f"probe does not implement {method}"}}
                try:
                    self.proc.stdin.write(json.dumps(payload) + "\n")
                    self.proc.stdin.flush()
                except Exception:
                    pass
                continue
            self._q.put(line)

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

    def server_requests(self) -> list:
        return [f.get("method") for f in self._server_requests]

    def unknown_server_requests(self) -> list:
        return sorted(set(self._unknown_requests))

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


class FakeTransport:
    """Offline transport for --selftest: replays scripted frames.

    `script` maps a method to a list of frames; repeated calls consume in order
    and the last frame repeats. A missing method yields a timeout frame.
    """

    def __init__(self, script: dict, ready: bool = True):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list = []
        self.ready = ready
        self._unknown: list = []

    def request(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        self.calls.append({"method": method, "params": params})
        frames = self.script.get(method)
        if not frames:
            return {"method": method, "timeout": True}
        frame = frames.pop(0) if len(frames) > 1 else frames[0]
        return frame

    def notification_methods(self) -> list:
        return []

    def server_requests(self) -> list:
        return []

    def unknown_server_requests(self) -> list:
        return list(self._unknown)

    def close(self) -> None:
        pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _summarize(frame: dict) -> dict:
    if _is_timeout(frame):
        return {"ok": False, "timeout": True}
    if "result" in frame:
        result = frame["result"]
        keys = sorted(result.keys()) if isinstance(result, dict) else type(result).__name__
        return {"ok": True, "result_keys": keys}
    return {"ok": False, "error": _err(frame)}


def _model_selection(model: str, reasoning_level: str = "high") -> dict:
    provider_id, _, model_id = model.partition("/")
    selection = {"providerId": provider_id, "modelId": model_id}
    if reasoning_level:
        selection["options"] = {"reasoningLevel": reasoning_level}
    return selection


def _status(transport, sid: str) -> dict:
    rd = transport.request("session/read", {"sessionId": sid}, timeout=30)
    result = _result_of(rd)
    projection = result.get("projection") if isinstance(result.get("projection"), dict) else {}
    return {"timeout": _is_timeout(rd), "error": _err(rd) if _is_error(rd) else None,
            "status": projection.get("status"), "turnCount": projection.get("turnCount"),
            "sessionId": (result.get("session") or {}).get("sessionId")
            if isinstance(result.get("session"), dict) else None}


def _poll_until_settled(transport, sid: str, timeout: float, verdict: Verdict, label: str,
                        interval: float = 2.0) -> dict:
    seen, turns, saw_active = [], 0, False
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = _status(transport, sid)
        status = st.get("status")
        turns = max(turns, st.get("turnCount") or 0)
        if not seen or seen[-1] != status:
            seen.append(status)
        if status in ACTIVE_STATES:
            saw_active = True
        if saw_active and (status == "idle" or status in TERMINAL_BAD):
            break
        time.sleep(interval)
    final = seen[-1] if seen else None
    verdict.require(f"{label}.observed_active", saw_active,
                    f"never became active; transitions={seen}")
    verdict.require(f"{label}.terminal_ok", final == "idle" or (saw_active and final not in TERMINAL_BAD),
                    f"final status={final!r} transitions={seen}")
    return {"transitions": seen, "final": final, "turnCount": turns}


def _check_unknown_server_requests(transport, verdict: Verdict) -> list:
    unknown = transport.unknown_server_requests()
    verdict.require("no_unknown_server_requests", not unknown,
                    f"server sent unimplemented client-bound requests: {unknown}")
    return unknown


def run_enumerate(transport) -> dict:
    """Schema-only mode. Kept OUT of the roundtrip verdict: a schema rejection
    from empty params is an expected answer here, not a success signal."""
    out = {}
    for method in ENUMERATE_METHODS:
        frame = transport.request(method, {}, timeout=20.0)
        if _is_timeout(frame):
            out[method] = {"reached": False, "timeout": True}
        elif "result" in frame:
            out[method] = {"reached": True, "accepted_empty_params": True}
        else:
            code = (frame.get("error") or {}).get("code")
            out[method] = {"reached": code != -32601, "schema_error": code == -32602,
                           "code": code}
    return out


def _create_session(transport, workspace: str, verdict: Verdict, label: str):
    frame = transport.request(
        "session/create",
        {"workspace": {"workspaceKey": workspace, "workspacePath": workspace}},
        timeout=60)
    if _is_timeout(frame):
        verdict.require(f"{label}.create", False, "timeout")
        return None, frame
    if _is_error(frame):
        verdict.require(f"{label}.create", False, _err(frame))
        return None, frame
    sid = _sid_of(frame)
    verdict.require(f"{label}.create", bool(sid) and str(sid).startswith("sess_"),
                    f"missing/odd sessionId: {sid!r}")
    return sid, frame


def run_roundtrip(transport, workspace: str, model: str, reasoning_level: str,
                  expect_token: str = "PROBE_OK", poll_timeout: float = 240.0,
                  poll_interval: float = 2.0) -> tuple:
    verdict = Verdict()
    report: dict = {"workspace": workspace, "model_pinned": model}
    verdict.require("app_server_ready", transport.ready, "app-server never reported ready")

    sid, created = _create_session(transport, workspace, verdict, "roundtrip")
    report["create"] = _summarize(created)
    report["session_id"] = sid
    if not sid:
        return report, verdict

    report["read_after_create"] = _status(transport, sid)
    set_model = transport.request("session/setModel",
                                  {"sessionId": sid, "model": _model_selection(model, reasoning_level)})
    report["setModel"] = _summarize(set_model)
    verdict.require("roundtrip.setModel", not _is_timeout(set_model) and not _is_error(set_model),
                    _err(set_model) if _is_error(set_model) else "timeout")

    sent = transport.request("session/send",
                             {"sessionId": sid, "content": f"Reply with exactly the token {expect_token} and nothing else."})
    report["send"] = _summarize(sent)
    if _is_timeout(sent):
        verdict.require("roundtrip.send", False, "timeout")
    elif _is_error(sent):
        verdict.require("roundtrip.send", False, _err(sent))
    else:
        result = _result_of(sent)
        verdict.require("roundtrip.send.accepted", result.get("accepted") is True,
                        f"accepted={result.get('accepted')!r}")
        verdict.require("roundtrip.send.same_session", result.get("sessionId") == sid,
                        f"send answered for {result.get('sessionId')!r}, expected {sid!r}")

    report["lifecycle"] = _poll_until_settled(transport, sid, poll_timeout, verdict, "roundtrip",
                                              poll_interval)

    msgs = transport.request("session/messages", {"sessionId": sid}, timeout=60)
    result = _result_of(msgs)
    items = result.get("messages") if isinstance(result.get("messages"), list) else []
    assistant = [m for m in items
                 if isinstance(m, dict) and (m.get("info") or {}).get("role") == "assistant"]
    report["messages"] = {"ok": not _is_error(msgs) and not _is_timeout(msgs),
                          "count": len(items), "assistant_count": len(assistant)}
    verdict.require("roundtrip.messages", not _is_timeout(msgs) and not _is_error(msgs)
                    and len(assistant) >= 1, f"no assistant message (count={len(items)})")
    if assistant:
        info = assistant[-1].get("info") or {}
        report["messages"]["last_assistant_model"] = {"providerId": info.get("providerId"),
                                                      "modelId": info.get("modelId")}
        want = _model_selection(model, reasoning_level)
        verdict.require("roundtrip.messages.model_binding",
                        info.get("providerId") == want["providerId"]
                        and info.get("modelId") == want["modelId"],
                        f"assistant model {info.get('providerId')}/{info.get('modelId')} != pinned")

    usage = transport.request("session/usage", {"sessionId": sid}, timeout=60)
    report["usage"] = usage
    uresult = _result_of(usage)
    verdict.require("roundtrip.usage", not _is_timeout(usage) and not _is_error(usage)
                    and uresult.get("sessionId") == sid, "usage missing / wrong session")
    verdict.require("roundtrip.usage.model_requests", (uresult.get("modelRequestCount") or 0) >= 1,
                    f"modelRequestCount={uresult.get('modelRequestCount')!r}")

    report["stop"] = _summarize(transport.request("session/stop", {"sessionId": sid}, timeout=30))
    report["read_after_stop"] = _status(transport, sid)
    report["unknown_server_requests"] = _check_unknown_server_requests(transport, verdict)
    return report, verdict


def run_cancel_trial(transport, workspace: str, model: str, reasoning_level: str,
                     active_wait: float = 60.0) -> tuple:
    """Dispatch a longer task, confirm it is genuinely running, then stop it."""
    verdict = Verdict()
    report: dict = {"model_pinned": model}
    verdict.require("app_server_ready", transport.ready, "app-server never reported ready")
    sid, created = _create_session(transport, workspace, verdict, "cancel")
    report["session_id"] = sid
    if not sid:
        return report, verdict
    transport.request("session/setModel",
                      {"sessionId": sid, "model": _model_selection(model, reasoning_level)})
    sent = transport.request("session/send", {
        "sessionId": sid,
        "content": "Count from 1 to 400 slowly, one number per line, then say DONE.",
    })
    verdict.require("cancel.send", not _is_timeout(sent) and not _is_error(sent)
                    and _result_of(sent).get("accepted") is True, _err(sent) if _is_error(sent) else "not accepted")

    deadline = time.time() + active_wait
    running = False
    while time.time() < deadline:
        if _status(transport, sid).get("status") in ACTIVE_STATES:
            running = True
            break
        time.sleep(2)
    report["was_running_before_stop"] = running
    verdict.require("cancel.reached_running", running, "never observed an active state to cancel")

    stop = transport.request("session/stop", {"sessionId": sid}, timeout=30)
    report["stop"] = _summarize(stop)
    verdict.require("cancel.stop_request", not _is_timeout(stop) and not _is_error(stop), _err(stop))
    report["after_stop"] = _poll_until_settled(transport, sid, 60, verdict, "cancel.after_stop")
    report["unknown_server_requests"] = _check_unknown_server_requests(transport, verdict)
    return report, verdict


def run_events_trial(transport, workspace: str, model: str, reasoning_level: str,
                    poll_timeout: float = 180.0) -> tuple:
    verdict = Verdict()
    report: dict = {}
    verdict.require("app_server_ready", transport.ready, "app-server never reported ready")
    sid, created = _create_session(transport, workspace, verdict, "events")
    report["session_id"] = sid
    if not sid:
        return report, verdict
    transport.request("session/setModel",
                      {"sessionId": sid, "model": _model_selection(model, reasoning_level)})
    sent = transport.request("session/send", {
        "sessionId": sid,
        "content": "Reply with exactly the token PROBE_OK and nothing else."})
    verdict.require("events.send", not _is_timeout(sent) and not _is_error(sent)
                    and _result_of(sent).get("accepted") is True, _err(sent) if _is_error(sent) else "not accepted")
    _poll_until_settled(transport, sid, poll_timeout, verdict, "events")

    events = transport.request("session/events", {"sessionId": sid}, timeout=30)
    items = _result_of(events).get("events")
    typed = [e for e in (items or []) if isinstance(e, dict) and e.get("type")]
    report["events"] = {"ok": not _is_timeout(events) and not _is_error(events),
                        "count": len(items or []), "typed_count": len(typed),
                        "types": sorted({e["type"] for e in typed})}
    verdict.require("events.typed_events", bool(typed), "session/events returned no typed events")
    report["subscribe"] = _summarize(transport.request(
        "session/subscribe", {"sessionId": sid, "deliveryKind": "desktop-continuous"}, timeout=20))
    report["notifications_seen"] = transport.notification_methods()
    transport.request("session/stop", {"sessionId": sid})
    report["unknown_server_requests"] = _check_unknown_server_requests(transport, verdict)
    return report, verdict


# --------------------------------------------------------------------------
# Offline self-test: the verdict logic must pass the happy path and fail every
# planted fault. No model call, no app-server process.
# --------------------------------------------------------------------------

def _frame_result(**kwargs) -> dict:
    return {"id": 1, "result": kwargs}


def _happy_script(sid="sess_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"):
    assistant = {"info": {"role": "assistant", "providerId": "new-provider-3", "modelId": "exo-free"},
                 "content": "PROBE_OK"}
    return {
        "session/create": [_frame_result(session={"sessionId": sid})],
        "session/setModel": [_frame_result(session={"sessionId": sid})],
        "session/send": [_frame_result(accepted=True, sessionId=sid, stateRevision=2)],
        # first frame answers the pre-send read, then the run goes active
        "session/read": [
            _frame_result(projection={"status": "idle", "turnCount": 0}),
            _frame_result(projection={"status": "running", "turnCount": 1}),
            _frame_result(projection={"status": "idle", "turnCount": 1}),
        ],
        "session/messages": [_frame_result(messages=[assistant])],
        "session/usage": [_frame_result(sessionId=sid, modelRequestCount=1, totalTokens=100)],
        "session/stop": [_frame_result()],
        "session/events": [_frame_result(events=[{"type": "turn.started", "seq": 1}])],
        "session/subscribe": [_frame_result(ok=True)],
    }


def _fault(case: str) -> Verdict:
    script = _happy_script()
    if case == "create_protocol_error":
        script["session/create"] = [{"id": 1, "error": {"code": -32603, "message": "boom"}}]
    elif case == "create_timeout":
        script["session/create"] = [{"method": "session/create", "timeout": True}]
    elif case == "setmodel_error":
        script["session/setModel"] = [{"id": 1, "error": {"code": -32603, "message": "no model"}}]
    elif case == "send_not_accepted":
        script["session/send"] = [_frame_result(accepted=False, sessionId="sess_x")]
    elif case == "send_wrong_session":
        script["session/send"] = [_frame_result(accepted=True, sessionId="sess_other")]
    elif case == "never_active":
        script["session/read"] = [_frame_result(projection={"status": "idle", "turnCount": 0})]
    elif case == "failed_terminal":
        script["session/read"] = [
            _frame_result(projection={"status": "idle", "turnCount": 0}),
            _frame_result(projection={"status": "running", "turnCount": 1}),
            _frame_result(projection={"status": "failed", "turnCount": 1}),
        ]
    elif case == "zero_usage":
        script["session/usage"] = [_frame_result(sessionId="sess_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                                                modelRequestCount=0)]
    elif case == "usage_wrong_session":
        script["session/usage"] = [_frame_result(sessionId="sess_other", modelRequestCount=1)]
    elif case == "no_assistant_message":
        script["session/messages"] = [_frame_result(messages=[])]
    elif case == "assistant_wrong_model":
        script["session/messages"] = [_frame_result(messages=[
            {"info": {"role": "assistant", "providerId": "p", "modelId": "other"}, "content": "x"}])]
    elif case == "unknown_server_request":
        script["_unknown_server_requests"] = [["interaction/mystery"]]
    elif case == "not_ready":
        script["_ready"] = False
    else:
        raise KeyError(case)

    ready = script.pop("_ready", True)
    unknown = script.pop("_unknown_server_requests", [])
    transport = FakeTransport(script, ready=ready)
    transport._unknown = list(unknown)
    return transport


def selftest() -> tuple:
    """Return (report, ok). Every fault must be caught; the happy path must pass."""
    cases = {}
    happy = FakeTransport(_happy_script())
    _, verdict = run_roundtrip(happy, r"D:\tmp\jh018-selftest", "new-provider-3/exo-free", "high",
                               poll_timeout=2.0, poll_interval=0.02)
    cases["happy_path"] = {"ok": verdict.ok, "failures": verdict.failures}

    faults = ["create_protocol_error", "create_timeout", "setmodel_error", "send_not_accepted",
              "send_wrong_session", "never_active", "failed_terminal", "zero_usage",
              "usage_wrong_session", "no_assistant_message", "assistant_wrong_model",
              "unknown_server_request", "not_ready"]
    for name in faults:
        transport = _fault(name)
        _, verdict = run_roundtrip(transport, r"D:\tmp\jh018-selftest",
                                   "new-provider-3/exo-free", "high", poll_timeout=2.0, poll_interval=0.02)
        cases[name] = {"detected": not verdict.ok, "failures": verdict.failures}

    # Cancel trial: a stop request that is rejected must fail the trial.
    bad_cancel = FakeTransport(_happy_script())
    bad_cancel.script["session/stop"] = [{"id": 1, "error": {"code": -32603, "message": "no"}}]
    _, cancel_verdict = run_cancel_trial(bad_cancel, ".", "new-provider-3/exo-free", "high",
                                        active_wait=1.0)
    cases["cancel_stop_rejected"] = {"detected": not cancel_verdict.ok,
                                     "failures": cancel_verdict.failures}

    ok = cases["happy_path"]["ok"] and all(v.get("detected") for k, v in cases.items()
                                           if k != "happy_path")
    return {"cases": cases, "all_faults_detected": ok}, ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ZCode app-server capability probe")
    ap.add_argument("--enumerate", action="store_true")
    ap.add_argument("--roundtrip", action="store_true")
    ap.add_argument("--cancel-trial", action="store_true")
    ap.add_argument("--events-trial", action="store_true")
    ap.add_argument("--selftest", action="store_true",
                    help="offline fake-transport positive/negative control; no model call")
    ap.add_argument("--workspace", default=r"D:\tmp\jh018-probe")
    ap.add_argument("--model", default="new-provider-3/exo-free")
    ap.add_argument("--reasoning-level", default="high")
    ap.add_argument("--cjs", default=str(DEFAULT_CJS))
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    if args.selftest:
        report_body, ok = selftest()
        text = json.dumps({"probe": "zcode-selftest", **report_body}, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
        return 0 if ok else 1

    cjs = Path(args.cjs)
    if not cjs.is_file():
        print(json.dumps({"error": f"zcode bundle not found: {cjs}"}, ensure_ascii=False))
        return 1
    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    transport = AppServer(cjs, cwd=str(workspace))
    report = {
        "probe": "zcode-app-server",
        "zcode_bundle": str(cjs),
        "zcode_bundle_sha256": _sha256(cjs),
        "app_server_ready": transport.ready,
        "isolated_workspace": str(workspace),
    }
    live_modes = args.roundtrip or args.cancel_trial or args.events_trial
    verdict = Verdict()
    try:
        if args.enumerate:
            # Schema-only: separate judgement, never a roundtrip success signal.
            enum = run_enumerate(transport)
            report["enumerate"] = enum
            report["enumerate_judgement"] = {
                "methods_probed": len(enum),
                "reached_server": sum(1 for v in enum.values() if v.get("reached")),
                "not_exposed": sorted(k for k, v in enum.items() if not v.get("reached")),
                "note": "schema answers are expected here and are NOT roundtrip success",
            }
        if args.roundtrip:
            report["roundtrip"], rt_verdict = run_roundtrip(
                transport, str(workspace), args.model, args.reasoning_level)
            verdict.checks.extend(rt_verdict.checks)
            verdict.failures.extend(rt_verdict.failures)
        if args.cancel_trial:
            report["cancel_trial"], ct_verdict = run_cancel_trial(
                transport, str(workspace), args.model, args.reasoning_level)
            verdict.checks.extend(ct_verdict.checks)
            verdict.failures.extend(ct_verdict.failures)
        if args.events_trial:
            report["events_trial"], et_verdict = run_events_trial(
                transport, str(workspace), args.model, args.reasoning_level)
            verdict.checks.extend(et_verdict.checks)
            verdict.failures.extend(et_verdict.failures)
        report["notifications_seen"] = transport.notification_methods()
        report["server_initiated_requests"] = transport.server_requests()
        report["unknown_server_requests"] = transport.unknown_server_requests()
    finally:
        transport.close()

    report["failures"] = verdict.failures
    report["checks"] = verdict.checks
    if live_modes:
        report["passed"] = verdict.ok
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    if live_modes and not verdict.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
