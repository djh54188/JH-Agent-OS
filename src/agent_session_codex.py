"""Real Codex AgentSessionAdapter (JH-020 real-session slice, rectified).

Implements the JH-018 contract (src/agent_session.py) against the Codex
app-server (JSON-RPC 2.0 over stdio). Scope stays minimal: a real dispatch /
collect path for ONE application, creating a NEW isolated session only.

Corrections applied after the independent review of 14e42a0:

* R1 — every collected fact is bound to the exact native thread AND turn. Item
  and token-usage notifications carry `threadId` and `turnId`; anything that
  cannot be tied to this run's turn is rejected and counted, and usage is split
  into this turn's usage versus the thread's cumulative usage.
* R2 — the request, run, idempotency key and fingerprint are registered BEFORE
  any external call, and the registry is proven writable first. A dispatch that
  times out keeps its unknown outcome and any known native ids, and an identical
  replay never dispatches again — including after the registry is reopened.
* R3 — the application/instance/session address is validated before dispatch.
  Only a NEW isolated session of this application is supported; an explicit
  model requirement must be honored and verified, and a finite budget this slice
  cannot enforce is refused instead of ignored.

Native differences live here, never in the contract. `continue_run` and `cancel`
remain UNSUPPORTED (`turn/steer` / `turn/interrupt` were never observed). There
is no scheduler and no runtime: one transport plus a run registry.

The adapter never marks anything succeeded — it reports what it observed and
lists what it could not confirm in `unverified`.
"""
from __future__ import annotations

import hashlib
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

#: Application instances this adapter may dispatch to.
DEFAULT_INSTANCE_IDS = frozenset({"codex-app-server/local"})
#: A session address only means "create a NEW isolated session" this slice.
NEW_SESSION_PREFIX = "new:"


class UnsupportedAddress(RuntimeError):
    """The requested application/instance/session address is not dispatchable."""


class UnsupportedRequest(RuntimeError):
    """An explicit request constraint this slice cannot honor is refused."""


class RegistryUnavailable(RuntimeError):
    """The durable dispatch record could not be established before dispatch."""


def default_codex_argv() -> List[str]:
    node = os.environ.get("NODE_EXE") or shutil.which("node") or "node"
    js = os.environ.get("CODEX_JS", DEFAULT_CODEX_JS)
    return [node, js, "app-server", "--stdio"]


# --- guard predicates -----------------------------------------------------
# Small, injectable predicates. Each protects a distinct claim the adapter
# makes, so each can be disabled alone and its regression test must then fail.

def is_new_session_request(session_id: Optional[str]) -> bool:
    """True only for an address that asks for a NEW isolated session."""
    if session_id is None:
        return True
    if not isinstance(session_id, str):
        return False
    value = session_id.strip().lower()
    return value == "" or value == "new" or value.startswith(NEW_SESSION_PREFIX)


def notification_binding(params: Dict[str, Any], thread_id: str,
                         turn_id: str) -> Tuple[bool, bool]:
    """(thread matches, turn matches) for a notification's params block."""
    return (params.get("threadId") == thread_id, params.get("turnId") == turn_id)


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


def model_is_honored(requested: Optional[str], reported: Any) -> bool:
    """An explicit model requirement must match what the server reports."""
    if not requested:
        return True
    return isinstance(reported, str) and reported == requested


def budget_is_dispatchable(budget_tokens: Optional[int]) -> bool:
    """This slice cannot enforce a finite token budget, so it must refuse one."""
    return budget_tokens is None


def append_record(path: Optional[str], record: Dict[str, Any]) -> None:
    """Append one registry record, raising on failure so dispatch can abort."""
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bound_notifications(notifications: List[dict], thread_id: str, turn_id: str,
                        method: str) -> Tuple[List[dict], int]:
    """Notifications of `method` bound to EXACTLY this thread and turn.

    Returns (accepted, ignored_count). Anything whose thread or turn cannot be
    confirmed as this run's is ignored, never adopted.
    """
    accepted: List[dict] = []
    ignored = 0
    for note in notifications:
        if note.get("method") != method:
            continue
        params = note.get("params") or {}
        thread_ok, turn_ok = notification_binding(params, thread_id, turn_id)
        if thread_ok and turn_ok:
            accepted.append(note)
        else:
            ignored += 1
    return accepted, ignored


def _split_usage(token_usage: Dict[str, Any]) -> Dict[str, Any]:
    """Separate the CURRENT turn's usage from the thread's cumulative usage."""
    if not isinstance(token_usage, dict):
        return {}
    out: Dict[str, Any] = {}
    if isinstance(token_usage.get("last"), dict):
        out["turn"] = dict(token_usage["last"])
    if isinstance(token_usage.get("total"), dict):
        out["thread_total"] = dict(token_usage["total"])
    if "modelContextWindow" in token_usage:
        out["model_context_window"] = token_usage.get("modelContextWindow")
    return out


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
    native_session_id: str = ""
    native_model: Optional[str] = None
    idempotency_key: Optional[str] = None
    turns: List[NativeTurn] = field(default_factory=list)
    state: str = RunState.QUEUED
    native_state: Optional[str] = None
    collected: Optional[CollectedResult] = None
    completion: Optional[Dict[str, Any]] = None
    unknown_server_requests: List[str] = field(default_factory=list)
    raw_evidence: Optional[Dict[str, Any]] = None
    recovered: bool = False


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
                 instance_ids: Optional[Tuple[str, ...]] = None,
                 transport: Optional[Any] = None):
        self._cwd = cwd or os.getcwd()
        self._transport_factory = transport_factory
        self._transport = transport
        self._registry_path = registry_path
        self._instance_ids = frozenset(instance_ids or DEFAULT_INSTANCE_IDS)
        self._runs: Dict[str, _Run] = {}
        self._idem: Dict[str, Tuple[str, str]] = {}
        self._counter = 0
        self._initialized = False
        if registry_path:
            self._load_registry()

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

    # --- registry -----------------------------------------------------------
    def _persist(self, event: str, run: _Run, extra: Optional[Dict[str, Any]] = None) -> dict:
        """Append one record. Raises OSError so a failed write aborts dispatch."""
        record: Dict[str, Any] = {
            "event": event,
            "run_id": run.run_id,
            "app_kind": run.binding.app_kind,
            "instance_id": run.binding.instance_id,
            "session_id": run.binding.session_id,
            "task_id": run.binding.task_id,
            "attempt": run.binding.attempt,
            "request_fingerprint": run.request_fingerprint,
            "request_snapshot": run.request_snapshot,
            "idempotency_key": run.idempotency_key,
            "native_session_id": run.native_session_id or None,
            "native_turns": [t.native_turn_id for t in run.turns],
            "native_model": run.native_model,
            "state": run.state,
            "native_state": run.native_state,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if extra:
            record.update(extra)
        append_record(self._registry_path, record)
        return record

    def _write_raw_evidence(self, run: _Run, notifications: List[dict]) -> Dict[str, Any]:
        """Persist the raw notification stream we actually observed, with a hash.

        This is a locatable, hashable subset of the native stream — not a claim
        that it is the complete original rollout, which lives in the host store.
        """
        payload = "".join(json.dumps(n, ensure_ascii=False, sort_keys=True) + "\n" for n in notifications)
        digest = _sha256_text(payload)
        if not self._registry_path:
            return {"framed": len(notifications), "sha256": digest, "path": None}
        raw_dir = os.path.join(os.path.dirname(os.path.abspath(self._registry_path)), "raw")
        os.makedirs(raw_dir, exist_ok=True)
        path = os.path.join(raw_dir, f"{run.run_id}.notifications.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(payload)
        return {"path": path, "sha256": digest, "framed": len(notifications)}

    def _load_registry(self) -> None:
        """Rebuild the idempotency index so a reopened registry blocks redispatch."""
        try:
            with open(self._registry_path, "r", encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            # Unreadable or absent: nothing to recover. A write at dispatch time
            # still fails loudly, so an unusable registry cannot silently pass.
            return
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            run_id = record.get("run_id")
            if not run_id:
                continue
            run = self._runs.get(run_id)
            if run is None:
                binding = RunBinding(record.get("app_kind") or self.app_kind,
                                     record.get("instance_id"), record.get("session_id"),
                                     record.get("task_id"), record.get("attempt"))
                run = _Run(run_id=run_id, binding=binding,
                           request_snapshot=record.get("request_snapshot") or "",
                           request_fingerprint=record.get("request_fingerprint") or "",
                           idempotency_key=record.get("idempotency_key"),
                           recovered=True)
                self._runs[run_id] = run
                self._counter = max(self._counter, 1)
            run.state = record.get("state") or run.state
            run.native_state = record.get("native_state") or run.native_state
            run.native_session_id = record.get("native_session_id") or run.native_session_id
            run.native_model = record.get("native_model") or run.native_model
            turns = record.get("native_turns") or []
            if turns:
                run.turns = [NativeTurn(native_turn_id=t, native_session_id=run.native_session_id)
                             for t in turns]
            if record.get("event") == "collected":
                run.collected = CollectedResult(
                    run_id=run_id, declared_status=record.get("declared_status") or "unreported",
                    artifacts=record.get("artifacts") or [], usage=record.get("usage") or {},
                    raw_refs=record.get("raw_refs") or [],
                    unverified=(record.get("unverified") or []) + ["recovered registry record; no live transport"],
                )
            key = record.get("idempotency_key")
            if key:
                self._idem[key] = (run.request_fingerprint, run_id)

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

    def _validate_dispatch(self, request: SubmitRequest) -> None:
        """Refuse anything this slice cannot dispatch to or honor — before any send."""
        session = request.session
        if session.app_kind != self.app_kind:
            raise UnsupportedAddress(
                f"adapter {self.app_kind!r} cannot dispatch to app_kind {session.app_kind!r}")
        if session.instance_id not in self._instance_ids:
            raise UnsupportedAddress(
                f"unsupported application instance {session.instance_id!r}; supported: {sorted(self._instance_ids)}")
        if not is_new_session_request(session.session_id):
            raise UnsupportedAddress(
                f"unsupported session address {session.session_id!r}; this slice only creates a NEW isolated session")
        if not budget_is_dispatchable(request.budget_tokens):
            raise UnsupportedRequest(
                "a finite token budget cannot be enforced by this adapter; refusing rather than ignoring it")

    def submit(self, request: SubmitRequest) -> RunStatus:
        self._require("submit")
        self._validate_dispatch(request)
        snapshot = request.snapshot()
        fingerprint = request.fingerprint()
        key = request.idempotency_key
        if key:
            known = self._idem.get(key)
            if known is not None:
                stored_fingerprint, run_id = known
                if not fingerprint_matches(stored_fingerprint, fingerprint):
                    raise IdempotencyConflict(key)
                run = self._runs[run_id]
                first = run.turns[0] if run.turns else None
                return RunStatus(run_id=run_id, state=run.state, binding=run.binding,
                                 native_turn=first, latest_turn=run.turns[-1] if run.turns else None,
                                 native_state=run.native_state,
                                 note="idempotent replay; not re-dispatched")

        # Register the request/run/key/fingerprint BEFORE any external call, and
        # prove the registry is writable first (a failed write aborts dispatch).
        self._counter += 1
        run_id = f"codex-run-{self._counter}-{uuid.uuid4().hex[:8]}"
        run = _Run(run_id=run_id, binding=RunBinding.of(request), request_snapshot=snapshot,
                   request_fingerprint=fingerprint, idempotency_key=key,
                   state=RunState.QUEUED, native_state="reserved")
        self._runs[run_id] = run
        if key:
            self._idem[key] = (fingerprint, run_id)
        try:
            self._persist("reserved", run)
        except OSError as exc:
            raise RegistryUnavailable(f"registry not writable; refusing to dispatch: {exc}") from None

        self._handshake()
        transport = self._get_transport()

        start_params: Dict[str, Any] = {"cwd": request.workspace, "approvalPolicy": "never",
                                        "sandbox": READ_ONLY_SANDBOX_MODE}
        if request.session.model_id:
            start_params["model"] = request.session.model_id
        start = transport.call("thread/start", start_params, timeout=90)
        if start.get("timeout"):
            run.state = RunState.UNKNOWN
            run.native_state = "dispatch-uncertain"
            self._persist("dispatch_uncertain", run, {"stage": "thread/start"})
            return RunStatus(run_id=run_id, state=run.state, binding=run.binding,
                             native_state=run.native_state,
                             note="thread/start timed out after send; outcome unknown, will not be re-dispatched")
        if "result" not in start:
            run.state = RunState.FAILED
            run.native_state = "thread/start-refused"
            self._persist("dispatch_failed", run, {"stage": "thread/start", "error": start.get("error")})
            raise RuntimeError(f"thread/start failed: {start.get('error')}")
        result = start.get("result") or {}
        thread_id = ((result.get("thread") or {}).get("id"))
        if not thread_id:
            run.state = RunState.FAILED
            run.native_state = "thread/start-no-id"
            self._persist("dispatch_failed", run, {"stage": "thread/start"})
            raise RuntimeError("thread/start returned no thread id")
        reported_model = result.get("model")
        if not model_is_honored(request.session.model_id, reported_model):
            run.state = RunState.FAILED
            run.native_state = "model-not-honored"
            run.native_session_id = thread_id
            self._persist("dispatch_failed", run, {"stage": "thread/start", "reported_model": reported_model})
            raise UnsupportedRequest(
                f"requested model {request.session.model_id!r} was not honored (server reports {reported_model!r})")

        run.native_session_id = thread_id
        run.native_model = reported_model

        turn = transport.call("turn/start", {
            "threadId": thread_id,
            "input": [{"type": "text", "text": request.instructions}],
            "cwd": request.workspace,
            "approvalPolicy": "never",
            "sandboxPolicy": dict(READ_ONLY_SANDBOX),
        }, timeout=60)
        if turn.get("timeout"):
            run.state = RunState.UNKNOWN
            run.native_state = "dispatch-uncertain"
            self._persist("dispatch_uncertain", run, {"stage": "turn/start", "native_session_id": thread_id})
            return RunStatus(run_id=run_id, state=run.state, binding=run.binding,
                             native_state=run.native_state,
                             note="turn/start timed out after send; native thread kept, outcome unknown, "
                                  "will not be re-dispatched")
        if "result" not in turn:
            run.state = RunState.FAILED
            run.native_state = "turn/start-refused"
            self._persist("dispatch_failed", run, {"stage": "turn/start", "error": turn.get("error")})
            raise RuntimeError(f"turn/start failed: {turn.get('error')}")
        turn_id = ((turn.get("result") or {}).get("turn") or {}).get("id")
        if not turn_id:
            run.state = RunState.FAILED
            run.native_state = "turn/start-no-id"
            self._persist("dispatch_failed", run, {"stage": "turn/start"})
            raise RuntimeError("turn/start returned no turn id")

        native_turn = NativeTurn(native_turn_id=turn_id, native_session_id=thread_id, purpose="initial")
        run.turns = [native_turn]
        run.state = RunState.RUNNING
        run.native_state = "turn-started"
        self._persist("dispatched", run, {"thread_id": thread_id, "turn_id": turn_id})
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
        if run.recovered:
            return CollectedResult(run_id=run.run_id, declared_status="unreported",
                                   unverified=["recovered registry record; no live transport"])
        transport = self._get_transport()
        turn = run.turns[-1] if run.turns else None
        if turn is None:
            return CollectedResult(run_id=run.run_id, declared_status="unreported",
                                   unverified=["collect: run has no native turn"])
        waited = transport.wait_completion(run.native_session_id, turn.native_turn_id, timeout)
        notifications = transport.notifications()
        if not waited.get("found"):
            run.state = RunState.UNKNOWN
            raw = self._write_raw_evidence(run, notifications)
            run.raw_evidence = raw
            result = CollectedResult(run_id=run.run_id, declared_status="unreported",
                                     raw_refs=[f"{run.native_session_id}/{turn.native_turn_id}"],
                                     unverified=[waited["reason"], "collect bounded wait; no retry"])
            run.collected = result
            self._persist("collect_timeout", run, {"reason": waited["reason"], "raw": raw})
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

        # R1: adopted facts must be bound to EXACTLY this thread and turn.
        text_notes, ignored_texts = bound_notifications(
            notifications, run.native_session_id, turn.native_turn_id, "item/completed")
        texts = [note.get("params", {}).get("item", {}).get("text")
                 for note in text_notes
                 if note.get("params", {}).get("item", {}).get("type") == "agentMessage"]
        texts = [t for t in texts if isinstance(t, str) and t]
        if ignored_texts:
            unverified.append(
                f"ignored {ignored_texts} agent item notification(s) not bound to this thread and turn")
        usage_notes, ignored_usage = bound_notifications(
            notifications, run.native_session_id, turn.native_turn_id, "thread/tokenUsage/updated")
        if ignored_usage:
            unverified.append(
                f"ignored {ignored_usage} token-usage notification(s) not bound to this thread and turn")
        if usage_notes:
            usage = _split_usage((usage_notes[-1].get("params") or {}).get("tokenUsage") or {})
            usage["source"] = {"thread_id": run.native_session_id, "turn_id": turn.native_turn_id}
        else:
            usage = {}
            unverified.append("token usage for this turn not proven; no bound usage notification observed")
        raw = self._write_raw_evidence(run, notifications)
        run.raw_evidence = raw
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
        self._persist("collected", run, {
            "declared_status": result.declared_status,
            "completion": observed,
            "usage": usage,
            "assistant_texts": texts,
            "unverified": unverified,
            "artifacts": result.artifacts,
            "raw_refs": result.raw_refs,
            "raw": raw,
        })
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
