"""Real Codex AgentSessionAdapter (JH-020, first real-session slice).

Implements the JH-018 contract (src/agent_session.py) against the Codex
app-server (JSON-RPC 2.0 over stdio). Scope is deliberately minimal: a real
dispatch/collect path for ONE application.

  * submit  -> thread/start (isolated cwd) + turn/start (read-only sandbox)
  * status  -> the adapter's run registry (binding, fingerprint, native turn)
  * collect -> bounded wait for the matching `turn/completed` notification

Native differences live here, never in the contract. `continue_run` and `cancel`
are declared UNSUPPORTED because `turn/steer` / `turn/interrupt` have not been
observed on this transport in this project; bounded remediation is a later
slice. There is no scheduler and no runtime here: one transport process plus a
run registry, keyed by run id, with a separate idempotency index.

The adapter never marks anything succeeded — a `RunState.DECLARED_COMPLETE` is
the executor's claim, and success stays with the control layer. `collect`
reports what it observed and lists what it could not confirm in `unverified`.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.agent_session import (
    CAPABILITIES,
    CollectedResult,
    RunBinding,
    RunState,
    RunStatus,
    NativeTurn,
    SessionAddress,
    SubmitRequest,
    UnsupportedCapability,
    AddressMismatch,
    IdempotencyConflict,
    AgentSessionAdapter,
)

# Client-bound requests the server may send. Anything else is answered with an
# error and recorded — never a fabricated success. Approvals are declined, which
# withholds permission rather than granting it.
KNOWN_SERVER_REQUESTS: Dict[str, Any] = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "item/permissions/requestApproval": {"decision": "decline"},
    "item/tool/requestUserInput": {"answers": []},
    "applyPatchApproval": {"decision": "decline"},
    "execCommandApproval": {"decision": "decline"},
    "mcpServer/elicitation/request": {"action": "decline"},
}

# Read-only at BOTH levels so a dispatched turn cannot write files even if the
# model tries. thread/start takes the SandboxMode enum string; turn/start takes
# the SandboxPolicy object. Network access is off at the turn level.
READ_ONLY_SANDBOX_MODE = "read-only"
READ_ONLY_SANDBOX = {"type": "readOnly", "networkAccess": False}

DEFAULT_CODEX_JS = os.environ.get(
    "CODEX_JS", r"D:\npm-global\node_modules\@openai\codex\bin\codex.js")


def default_codex_argv() -> List[str]:
    node = os.environ.get("NODE_EXE") or shutil.which("node") or "node"
    js = os.environ.get("CODEX_JS", DEFAULT_CODEX_JS)
    return [node, js, "app-server", "--stdio"]


# --- association guards ---------------------------------------------------
# Small, injectable predicates. Each one protects a distinct claim the adapter
# makes, so each can be disabled alone and its regression test must then fail.

def completion_association(params: Dict[str, Any], thread_id: str,
                           turn_id: str) -> Tuple[bool, bool]:
    """(thread matches, turn matches) for a `turn/completed` notification."""
    completed = params.get("turn") or {}
    return (params.get("threadId") == thread_id, completed.get("id") == turn_id)


def completion_status_ok(completed: Dict[str, Any]) -> bool:
    """A turn is only 'completed' when the status says so AND it carries no error."""
    return completed.get("status") == "completed" and not completed.get("error")


def fingerprint_matches(stored: str, current: str) -> bool:
    """An idempotency key may only replay the exact request it registered."""
    return stored == current


def address_matches(run_binding: RunBinding, wanted: RunBinding) -> bool:
    """A run may only be read through the address it was bound to."""
    return run_binding == wanted


class CodexStdioTransport:
    """A spawned `codex app-server --stdio` process speaking JSON-RPC 2.0.

    Mirrors the transport verified by scripts/probe_codex_sessions.py: a reader
    thread demultiplexes responses (id), notifications (method only), and
    server-bound requests (method + id), the last answered only for the known
    set above.
    """

    def __init__(self, argv: List[str], cwd: str):
        self.argv = list(argv)
        self.cwd = cwd
        self.proc = subprocess.Popen(
            self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=cwd, text=True, encoding="utf-8", errors="replace", bufsize=1)
        self._responses: "queue.Queue[dict]" = queue.Queue()
        self._notes: List[dict] = []
        self._unknown: List[str] = []
        self._id = 0
        self._lock = threading.Lock()
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
                method = frame.get("method")
                if method in KNOWN_SERVER_REQUESTS:
                    self._write({"jsonrpc": "2.0", "id": frame["id"],
                                 "result": dict(KNOWN_SERVER_REQUESTS[method])})
                else:
                    self._unknown.append(method)
                    self._write({"jsonrpc": "2.0", "id": frame["id"],
                                 "error": {"code": -32601,
                                           "message": f"adapter does not implement {method}"}})
                continue
            if frame.get("method"):
                self._notes.append(frame)
                continue
            self._responses.put(frame)

    def _write(self, payload: dict) -> None:
        try:
            with self._lock:
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
                frame = self._responses.get(timeout=1.0)
            except queue.Empty:
                continue
            if frame.get("id") == rid:
                return frame
            # A response to a different id is not ours; keep waiting.
        return {"id": rid, "timeout": True, "method": method}

    def notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def notifications(self) -> List[dict]:
        return list(self._notes)

    def wait_completion(self, thread_id: str, turn_id: str, timeout: float) -> dict:
        """Return the terminal turn frame for THIS thread+turn, or a reason dict.

        A frame for any other thread or turn is never accepted here; the caller
        turns a mismatch into an unverified collection.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            for note in list(self._notes):
                if note.get("method") != "turn/completed":
                    continue
                params = note.get("params") or {}
                turn = params.get("turn") or {}
                if params.get("threadId") == thread_id and turn.get("id") == turn_id:
                    return {"found": True, "frame": note}
            time.sleep(0.5)
        return {"found": False, "reason": f"no turn/completed for thread {thread_id!r} turn {turn_id!r} within {timeout}s"}

    def unknown_server_requests(self) -> List[str]:
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


@dataclass
class _Run:
    run_id: str
    binding: RunBinding
    request_snapshot: str
    request_fingerprint: str
    native_session_id: str
    turns: List[NativeTurn] = field(default_factory=list)
    state: str = RunState.ACCEPTED
    native_state: Optional[str] = None
    collected: Optional[CollectedResult] = None
    completion: Optional[Dict[str, Any]] = None
    unknown_server_requests: List[str] = field(default_factory=list)


class CodexAgentSessionAdapter(AgentSessionAdapter):
    """One real application: the Codex app-server, addressed by thread id."""

    app_kind = "codex"

    #: Capabilities actually observed on this transport in this project.
    SUPPORTED = frozenset({"capabilities", "submit", "status", "collect"})
    UNSUPPORTED_REASONS = {
        "continue": "turn/steer not observed on this transport; bounded remediation is a later slice",
        "cancel": "turn/interrupt not observed on this transport",
    }

    def __init__(self, *, cwd: Optional[str] = None,
                 transport_factory: Optional[Callable[[], Any]] = None,
                 registry_path: Optional[str] = None,
                 transport: Optional[Any] = None):
        self._cwd = cwd or os.getcwd()
        self._transport_factory = transport_factory
        self._transport = transport
        self._registry_path = registry_path
        self._runs: Dict[str, _Run] = {}
        self._idem: Dict[str, Tuple[str, str]] = {}
        self._counter = 0
        self._initialized = False

    # --- transport lifecycle ------------------------------------------------
    def _get_transport(self):
        if self._transport is None:
            factory = self._transport_factory or (lambda: CodexStdioTransport(default_codex_argv(), self._cwd))
            self._transport = factory()
        return self._transport

    def _handshake(self) -> None:
        if self._initialized:
            return
        transport = self._get_transport()
        init = transport.call("initialize", {
            "clientInfo": {"name": "jh020-codex-adapter", "version": "0.1",
                           "title": "JH-020 Codex adapter"}}, timeout=60)
        if init.get("timeout") or "result" not in init:
            raise RuntimeError(f"codex app-server initialize failed: {init.get('error') or 'timeout'}")
        transport.notify("initialized", {})
        self._initialized = True

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
            self._initialized = False

    # --- persistence --------------------------------------------------------
    def _persist(self, event: str, run: _Run, extra: Optional[Dict[str, Any]] = None) -> None:
        if not self._registry_path:
            return
        record = {
            "event": event,
            "run_id": run.run_id,
            "app_kind": run.binding.app_kind,
            "instance_id": run.binding.instance_id,
            "session_id": run.binding.session_id,
            "task_id": run.binding.task_id,
            "attempt": run.binding.attempt,
            "request_fingerprint": run.request_fingerprint,
            "native_session_id": run.native_session_id,
            "native_turns": [t.native_turn_id for t in run.turns],
            "state": run.state,
            "native_state": run.native_state,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if extra:
            record.update(extra)
        with open(self._registry_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    # --- contract -----------------------------------------------------------
    def capabilities(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for name in CAPABILITIES:
            if name in self.SUPPORTED:
                out[name] = {"supported": True, "detail": _SUPPORT_DETAIL.get(name, "observed")}
            else:
                out[name] = {"supported": False,
                             "reason": self.UNSUPPORTED_REASONS.get(name, "not observed")}
        return out

    def submit(self, request: SubmitRequest) -> RunStatus:
        self._require("submit")
        snapshot = request.snapshot()
        fingerprint = request.fingerprint()
        if request.idempotency_key:
            known = self._idem.get(request.idempotency_key)
            if known is not None:
                stored_fingerprint, run_id = known
                if not fingerprint_matches(stored_fingerprint, fingerprint):
                    raise IdempotencyConflict(request.idempotency_key)
                run = self._runs[run_id]
                first = run.turns[0] if run.turns else None
                return RunStatus(run_id=run_id, state=run.state, binding=run.binding,
                                 native_turn=first, latest_turn=run.turns[-1] if run.turns else None,
                                 native_state=run.native_state,
                                 note="idempotent replay of the same request")

        self._handshake()
        transport = self._get_transport()
        start = transport.call("thread/start", {
            "cwd": request.workspace,
            "approvalPolicy": "never",
            "sandbox": READ_ONLY_SANDBOX_MODE,
        }, timeout=90)
        if start.get("timeout") or "result" not in start:
            raise RuntimeError(f"thread/start failed: {start.get('error') or 'timeout'}")
        result = start.get("result") or {}
        thread_id = ((result.get("thread") or {}).get("id"))
        if not thread_id:
            raise RuntimeError("thread/start returned no thread id")

        turn = transport.call("turn/start", {
            "threadId": thread_id,
            "input": [{"type": "text", "text": request.instructions}],
            "cwd": request.workspace,
            "approvalPolicy": "never",
            "sandboxPolicy": dict(READ_ONLY_SANDBOX),
        }, timeout=60)
        if turn.get("timeout") or "result" not in turn:
            raise RuntimeError(f"turn/start failed: {turn.get('error') or 'timeout'}")
        turn_id = ((turn.get("result") or {}).get("turn") or {}).get("id")
        if not turn_id:
            raise RuntimeError("turn/start returned no turn id")

        self._counter += 1
        run_id = f"codex-run-{self._counter}-{uuid.uuid4().hex[:8]}"
        native_turn = NativeTurn(native_turn_id=turn_id, native_session_id=thread_id, purpose="initial")
        run = _Run(run_id=run_id, binding=RunBinding.of(request), request_snapshot=snapshot,
                   request_fingerprint=fingerprint, native_session_id=thread_id,
                   turns=[native_turn], state=RunState.RUNNING, native_state="turn-started")
        self._runs[run_id] = run
        if request.idempotency_key:
            self._idem[request.idempotency_key] = (fingerprint, run_id)
        self._persist("submitted", run, {"thread_id": thread_id, "turn_id": turn_id})
        return RunStatus(run_id=run_id, state=run.state, binding=run.binding,
                         native_turn=native_turn, latest_turn=native_turn,
                         native_state=run.native_state,
                         note="turn started; result not collected")

    def _resolve(self, session: SessionAddress, run_id: Optional[str]) -> Optional[_Run]:
        run = self._runs.get(run_id or "")
        if run is None:
            return None
        wanted = RunBinding(session.app_kind, session.instance_id, session.session_id,
                            run.binding.task_id, run.binding.attempt)
        if not address_matches(run.binding, wanted):
            raise AddressMismatch(run_id, run.binding, wanted)
        return run

    def status(self, session: SessionAddress, run_id: Optional[str]) -> RunStatus:
        self._require("status")
        run = self._resolve(session, run_id)
        if run is None:
            return RunStatus(run_id=run_id, state=RunState.UNKNOWN, note="no run with this id")
        latest = run.turns[-1] if run.turns else None
        return RunStatus(run_id=run.run_id, state=run.state, binding=run.binding,
                         native_turn=latest, latest_turn=latest, native_state=run.native_state)

    def collect(self, session: SessionAddress, run_id: Optional[str],
                timeout: float = 300.0) -> CollectedResult:
        self._require("collect")
        run = self._resolve(session, run_id)
        if run is None:
            return CollectedResult(run_id=run_id, declared_status="unreported",
                                   unverified=["collect: no run with this id"])
        if run.collected is not None:
            return run.collected
        transport = self._get_transport()
        turn = run.turns[-1] if run.turns else None
        if turn is None:
            return CollectedResult(run_id=run.run_id, declared_status="unreported",
                                   unverified=["collect: run has no native turn"])
        waited = transport.wait_completion(run.native_session_id, turn.native_turn_id, timeout)
        if not waited.get("found"):
            run.state = RunState.UNKNOWN
            result = CollectedResult(run_id=run.run_id, declared_status="unreported",
                                     raw_refs=[f"{run.native_session_id}/{turn.native_turn_id}"],
                                     unverified=[waited["reason"], "collect bounded wait; no retry"])
            run.collected = result
            self._persist("collect_timeout", run, {"reason": waited["reason"]})
            return result
        frame = waited["frame"]
        params = frame.get("params") or {}
        completed = params.get("turn") or {}
        observed = {
            "threadId": params.get("threadId"),
            "turn_id": completed.get("id"),
            "status": completed.get("status"),
            "error": completed.get("error"),
        }
        unverified: List[str] = ["adapter observation only; review decides success"]
        matches_thread, matches_turn = completion_association(
            params, run.native_session_id, turn.native_turn_id)
        if not matches_thread:
            unverified.append(f"completion thread {params.get('threadId')!r} != {run.native_session_id!r}")
        if not matches_turn:
            unverified.append(f"completion turn {completed.get('id')!r} != {turn.native_turn_id!r}")
        associated = matches_thread and matches_turn
        if not associated:
            unverified.append("completion not attributed to this run's thread and turn")
        status_ok = completion_status_ok(completed)
        if associated and status_ok:
            run.state = RunState.DECLARED_COMPLETE
        else:
            run.state = RunState.FAILED
        run.native_state = completed.get("status")
        run.completion = observed
        run.unknown_server_requests = transport.unknown_server_requests()
        if run.unknown_server_requests:
            unverified.append(f"unimplemented server requests: {run.unknown_server_requests}")
        texts = _assistant_texts(transport.notifications(), run.native_session_id)
        usage = _token_usage(transport.notifications(), run.native_session_id)
        result = CollectedResult(
            run_id=run.run_id,
            # A foreign completion's claim is never adopted as this run's status.
            declared_status=(completed.get("status") or "unreported") if associated else "unattributed",
            artifacts=[{"thread_id": run.native_session_id, "turn_id": turn.native_turn_id,
                        "assistant_text": text} for text in texts],
            usage=usage,
            raw_refs=[f"{run.native_session_id}/{turn.native_turn_id}"],
            unverified=unverified,
        )
        run.collected = result
        self._persist("collected", run, {"completion": observed,
                                         "unverified": unverified,
                                         "assistant_texts": texts})
        return result

    def continue_run(self, session: SessionAddress, run_id: Optional[str],
                     instructions: str, idempotency_key: Optional[str] = None) -> RunStatus:
        raise UnsupportedCapability(self.app_kind, "continue", self.UNSUPPORTED_REASONS["continue"])

    def cancel(self, session: SessionAddress, run_id: Optional[str]) -> RunStatus:
        raise UnsupportedCapability(self.app_kind, "cancel", self.UNSUPPORTED_REASONS["cancel"])

    # --- inspection ---------------------------------------------------------
    def inspect(self, run_id: str) -> Optional[_Run]:
        return self._runs.get(run_id)

    def _require(self, capability: str) -> None:
        if capability not in self.SUPPORTED:
            raise UnsupportedCapability(self.app_kind, capability,
                                        self.UNSUPPORTED_REASONS.get(capability, "not observed"))


_SUPPORT_DETAIL = {
    "capabilities": "static declaration; observed operations listed here",
    "submit": "thread/start + turn/start observed on the codex app-server",
    "status": "local run registry (binding, fingerprint, native turn)",
    "collect": "turn/completed notification matched to this thread and turn",
}


def _assistant_texts(notifications: List[dict], thread_id: str) -> List[str]:
    texts: List[str] = []
    for note in notifications:
        params = note.get("params") or {}
        if params.get("threadId") not in (None, thread_id):
            continue
        item = params.get("item") or {}
        if isinstance(item, dict) and item.get("type") == "agentMessage":
            text = item.get("text")
            if isinstance(text, str) and text:
                texts.append(text)
    return texts


def _token_usage(notifications: List[dict], thread_id: str) -> Dict[str, Any]:
    usage: Dict[str, Any] = {}
    for note in notifications:
        if note.get("method") != "thread/tokenUsage/updated":
            continue
        params = note.get("params") or {}
        if params.get("threadId") not in (None, thread_id):
            continue
        payload = params.get("usage") or params.get("tokenUsage") or {}
        if isinstance(payload, dict):
            usage = dict(payload)
    return usage
