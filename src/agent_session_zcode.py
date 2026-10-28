"""ZCode headless host as an AgentSessionAdapter (Hark-派单-27, OFFLINE CANDIDATE).

A thin bridge: the tool loop belongs to ZCode (`zcode.cjs --prompt ... --output-format
stream-json`). This module only binds one run, persists it before dispatch, launches the host
with an isolated home and an explicit provider/model, and reads back the host's own NDJSON
events. It is NOT a ModelAdapter, does not translate protocols, and never fakes Gemini CLI
events for the B checker.

Facts used (from the official ZCode source/docs at zai-org/ZCode 3c5b2276, 2026-10-28; the
user's installed zcode.cjs is a different build and NONE of these are verified against it):
  * `--prompt`, `--cwd`, `--mode plan|build|edit|yolo|guarded`, `--disallowed-tools`,
    `--workflow-mode`, `--output-format stream-json`, `--no-color`; no `--model` flag.
  * stream-json = NDJSON session events {eventId, payload, seq, sessionId, timestamp, traceId,
    turnId, type} ending with one {"type": "result", sessionId, traceId, turnId?, response,
    usage?, eventCount, projection}. Tool events are "tool.updated" with payload.kind
    scheduled/started/progress/result/error; permission.requested / permission.resolved;
    model_request appears as "session.updated" with payload {providerId, modelId, messageCount}.
  * The headless permission broker denies every ask except CreateWorkflow/AmendWorkflow.
  * Personal provider config: ~/.zcode/v2/provider_config.json (or
    ZCODE_PERSONAL_PROVIDER_CONFIG_FILE); data base dir ZCODE_DATA_BASE_DIR; `.env` is read
    upward from the working directory; usage may be absent (absent is unknown, not 0).
  * `plan` mode is not an OS sandbox; Read/Grep/Glob take absolute paths. Reads outside the
    workspace are therefore DETECTED afterwards (non-pass), never claimed to be prevented.

Scope: capabilities / submit / status / collect only. continue_run and cancel raise
UnsupportedCapability. Read and search only; anything else is refused or non-pass.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat as _stat
import subprocess
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.agent_session import (
    AddressMismatch,
    AgentSessionAdapter,
    CollectedResult,
    IdempotencyConflict,
    NativeTurn,
    RunBinding,
    RunState,
    RunStatus,
    SessionAddress,
    SubmitRequest,
    UnsupportedCapability,
)

APP_KIND = "zcode"
NEW_SESSION = "new"
ALLOWED_TOOLS = ("Read", "Grep", "Glob")
ALLOWED_SCOPE = frozenset({"read", "search"})
#: Removed at registration (best effort: names from the official contracts / docs; the
#: authoritative gate is the post-run allowlist, which fails any other tool name).
DISALLOWED_TOOLS = (
    "Agent", "AmendWorkflow", "ApplyPatch", "AskUserQuestion", "Bash", "CreateWorkflow", "Edit",
    "EnterPlanMode", "ExitPlanMode", "NodeRepl", "SaveWorkflow", "SendMessage", "Skill", "Task",
    "TaskOutput", "TaskStop", "TodoRead", "TodoWrite", "ToolSearch", "WebFetch", "WebSearch", "Write",
)
API_TYPES = ("anthropic-messages", "openai-chat-completions", "openai-responses")
REGISTRY_EVENTS = ("reserved", "dispatched", "dispatch_failed", "exited", "collected")
_RUN_ID = re.compile(r"^zcode-run-[0-9a-f]{12}$")
_ENV_KEEP = ("PATH", "SYSTEMROOT", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR")
RUN_CONFIG = {                       # ~/.zcode/cli/config.json inside the isolated home
    "features": {"mcp": False, "subagent": False, "memory": False, "skill": False},
    "memory": {"use": False}, "plugins": {"enabled": False}, "skills": {"enabled": False},
    "hooks": {"enabled": False},
}
UNVERIFIED_HOST = (
    "installed zcode.cjs build: flags, event schema, tool names and plan-mode behaviour are taken "
    "from the official source, not observed on this build",
    "host does not confine Read/Grep/Glob to the workspace; out-of-workspace access is only "
    "detected after the fact",
    "the provider/model the endpoint actually serves is not verifiable here (alias only)",
)


class RegistryUnusable(RuntimeError):
    """Registry missing-with-traces, corrupt, locked, or holding another run: never 'fresh'."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class ZCodeConfig:
    instance_id: str
    argv_prefix: Tuple[str, ...]          # e.g. (node.exe, zcode.cjs) - explicit, never PATH lookup
    host_file: str                        # the file whose bytes are pinned (zcode.cjs)
    host_sha256: str
    evidence_root: str
    run_home_root: str                    # isolated HOME / data dirs, OUTSIDE the evidence root
    provider_id: str
    model_id: str
    base_url: str
    api_type: str = "anthropic-messages"
    key_env: Optional[str] = None         # env var read at dispatch only; None = no key written
    mode: str = "plan"

    def problems(self) -> List[str]:
        out = []
        for name in ("instance_id", "host_file", "host_sha256", "evidence_root", "run_home_root",
                     "provider_id", "model_id", "base_url"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                out.append(f"{name} must be given explicitly")
        if not self.argv_prefix or not all(isinstance(a, str) and os.path.isabs(a) for a in self.argv_prefix):
            out.append("argv_prefix must be absolute paths")
        if self.api_type not in API_TYPES:
            out.append("api_type is not a documented ZCode API type")
        if not re.match(r"^https?://[^\s]+$", self.base_url or ""):
            out.append("base_url must be an http(s) URL")
        if self.mode != "plan":
            out.append("only plan mode is allowed for read/search runs")
        if not re.match(r"^[0-9a-f]{64}$", self.host_sha256 or ""):
            out.append("host_sha256 must be a lowercase sha256")
        if self.evidence_root and self.run_home_root:
            a, b = os.path.realpath(self.evidence_root), os.path.realpath(self.run_home_root)
            if a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep):
                out.append("run_home_root and evidence_root must be separate trees")
        return out

    def provider_document(self, api_key: Optional[str]) -> Dict[str, Any]:
        """Isolated personal provider file: one provider, one model, explicit default."""
        access: Dict[str, Any] = {"type": "api-key", "apiKey": api_key if api_key is not None else ""}
        return {"schemaVersion": 1, "config": {
            "providerConfigRules": {"providerRules": [{
                "providerId": self.provider_id, "providerName": self.provider_id,
                "config": {"group": "standard-personal", "access": access,
                           "api": {"type": self.api_type, "baseUrl": self.base_url},
                           "personalModelIds": [self.model_id]}}]},
            "modelConfigRules": {"providerModelRules": [], "manualProviderModelRules": []},
            "defaultModelSelection": {"providerId": self.provider_id, "modelId": self.model_id}}}


def ancestor_env_files(path: str) -> List[str]:
    found, cur = [], os.path.realpath(path)
    while True:
        cand = os.path.join(cur, ".env")
        if os.path.lexists(cand):
            found.append(cand)
        parent = os.path.dirname(cur)
        if parent == cur:
            return found
        cur = parent


# --------------------------------------------------------------------------- stream parsing
def _inside(path: Any, workspace: str) -> Optional[bool]:
    """True/False when decidable, None when the value is not a usable path."""
    if path is None:
        return True                                   # documented default: the working directory
    if not isinstance(path, str) or not path or "\x00" in path:
        return None
    full = path if os.path.isabs(path) else os.path.join(workspace, path)
    real, root = os.path.realpath(full), os.path.realpath(workspace)
    return real == root or real.startswith(root + os.sep)


def parse_stream(data: bytes, *, workspace: str, provider_id: str, model_id: str) -> Dict[str, Any]:
    """The host's NDJSON as-is -> facts + problems. Nothing is rewritten into another format."""
    fail: List[str] = []
    unknown: List[str] = []
    lines: List[Dict[str, Any]] = []
    for n, raw in enumerate(data.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            unknown.append(f"line {n} is not JSON")
            continue
        if not isinstance(obj, dict):
            unknown.append(f"line {n} is not an object")
            continue
        lines.append(obj)
    results = [o for o in lines if o.get("type") == "result"]
    events = [o for o in lines if o.get("type") != "result"]
    result = results[-1] if results else None
    if len(results) != 1 or (lines and lines[-1].get("type") != "result"):
        unknown.append("stream does not end with exactly one result line")
    session_id = result.get("sessionId") if result else None
    trace_id = result.get("traceId") if result else None
    if result is not None and not (isinstance(session_id, str) and session_id):
        unknown.append("result carries no sessionId")
    calls: Dict[str, Dict[str, Any]] = {}
    models: List[Tuple[Any, Any]] = []
    turns, unrecognized = set(), 0
    for ev in events:
        if session_id is None or ev.get("sessionId") != session_id or ev.get("traceId") != trace_id:
            fail.append("event not attributable to this run's session/trace")
            continue
        if ev.get("turnId") is not None:
            turns.add(str(ev.get("turnId")))
        typ, p = ev.get("type"), ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        if typ == "tool.updated":
            cid, kind = p.get("toolCallId"), p.get("kind")
            if not isinstance(cid, str) or not cid:
                fail.append("tool event without toolCallId")
                continue
            if kind in ("scheduled", "started"):
                c = calls.setdefault(cid, {"toolCallId": cid, "turnId": ev.get("turnId"), "results": []})
                if c["turnId"] != ev.get("turnId"):
                    fail.append(f"tool call {cid} spans turns")
                name = p.get("toolName")
                if name is not None:
                    if c.get("toolName") not in (None, name):
                        fail.append(f"tool call {cid} changes name")
                    c["toolName"] = name
                if "input" in p:
                    c["input"] = p.get("input")
                if kind == "started" and p.get("readOnly") is False:
                    c["writes"] = True
            elif kind in ("result", "error"):
                if cid not in calls:
                    fail.append(f"tool {kind} for a call never requested in this run ({cid[:40]})")
                    continue
                if calls[cid]["turnId"] != ev.get("turnId"):
                    fail.append(f"tool {kind} for {cid[:40]} arrives in another turn")
                calls[cid]["results"].append({"kind": kind, "payload": p})
            elif kind not in ("progress", "batch"):
                unrecognized += 1
        elif typ == "permission.requested":
            fail.append(f"host asked permission for {str(p.get('toolName'))[:40]} (not approved by this bridge)")
        elif typ == "permission.resolved":
            if p.get("decision") not in ("deny",):
                fail.append("a permission request was resolved other than deny")
        elif typ == "turn.failed":
            fail.append("host reports a failed turn")
        elif typ == "session.updated" and "providerId" in p and "modelId" in p and "messageCount" in p:
            models.append((p.get("providerId"), p.get("modelId")))
        elif typ == "session.updated" and ("fromModelSelection" in p or "toModelSelection" in p):
            fail.append("host switched the execution model (fallback)")
        elif typ == "session.updated" and isinstance(p.get("modelSelection"), dict) \
                and p["modelSelection"].get("origin") == "registryFallback":
            fail.append("host selected a fallback model")
        elif typ not in ("session.created", "session.updated", "turn.started", "turn.completed",
                         "message.upserted", "model.streaming", "session.titleUpdated", "session.closed"):
            unrecognized += 1
    for c in calls.values():
        name = c.get("toolName")
        if name not in ALLOWED_TOOLS:
            fail.append(f"tool outside read/search requested: {str(name)[:40]}")
            continue
        if c.get("writes"):
            fail.append(f"{name} call {c['toolCallId'][:40]} declared side effects")
        inp = c.get("input") if isinstance(c.get("input"), dict) else None
        if inp is None:
            unknown.append(f"{name} call {c['toolCallId'][:40]} input not observed")
            continue
        target = inp.get("file_path") if name == "Read" else inp.get("path")
        if name == "Read" and target is None:
            unknown.append("Read call without file_path")
            continue
        inside = _inside(target, workspace)
        if inside is None:
            unknown.append(f"{name} path not decidable")
        elif not inside:
            fail.append(f"{name} reached outside the workspace")
        if not c["results"]:
            unknown.append(f"{name} call {c['toolCallId'][:40]} has no result")
    if not models:
        unknown.append("model_request not observed: provider/model binding unconfirmed")
    for prov, mod in models:
        if (prov, mod) != (provider_id, model_id):
            fail.append("host requested a model other than the bound provider/model")
    usage = result.get("usage") if result and isinstance(result.get("usage"), dict) else None
    projection = result.get("projection") if result and isinstance(result.get("projection"), dict) else {}
    return {
        "session_id": session_id, "trace_id": trace_id,
        "turn_id": result.get("turnId") if result else None, "turn_ids": sorted(turns),
        "tool_calls": [calls[k] for k in sorted(calls)], "models": models,
        "usage": usage, "usage_known": usage is not None,
        "projection_status": projection.get("status"), "turn_count": projection.get("turnCount"),
        "response": result.get("response") if result else None,
        "unrecognized_events": unrecognized, "fail": fail, "unknown": unknown,
    }


# --------------------------------------------------------------------------- registry
class _Registry:
    def __init__(self, root: str):
        self.root = root
        self.path = os.path.join(root, "registry.jsonl")
        self.lock_path = os.path.join(root, "registry.lock")

    def __enter__(self):
        os.makedirs(self.root, exist_ok=True)
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise RegistryUnusable("registry lock present (live or stale); not broken automatically")
        os.close(fd)
        return self

    def __exit__(self, *exc):
        os.remove(self.lock_path)

    def load(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            traces = [n for n in os.listdir(self.root) if n not in ("registry.lock",)] \
                if os.path.isdir(self.root) else []
            if traces:
                raise RegistryUnusable("evidence root has traces but no registry")
            return []
        out = []
        with open(self.path, "rb") as fh:
            data = fh.read()
        if data and not data.endswith(b"\n"):
            raise RegistryUnusable("registry ends with a partial record")
        for raw in data.splitlines():
            try:
                rec = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise RegistryUnusable("registry record is not JSON")
            if not isinstance(rec, dict) or rec.get("event") not in REGISTRY_EVENTS \
                    or not isinstance(rec.get("run_id"), str) or not _RUN_ID.match(rec["run_id"]):
                raise RegistryUnusable("registry record has an unknown shape")
            out.append(rec)
        runs = {r["run_id"] for r in out}
        if out and out[0]["event"] != "reserved":
            raise RegistryUnusable("registry does not start with a reservation")
        for rid in runs:
            if not os.path.isdir(os.path.join(self.root, "runs", rid)) and \
                    any(r["event"] != "reserved" for r in out if r["run_id"] == rid):
                raise RegistryUnusable("registered run has no evidence directory")
        return out

    def append(self, rec: Dict[str, Any]) -> None:
        line = (json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)


# --------------------------------------------------------------------------- adapter
@dataclass
class _Live:
    process: Any
    stdout_path: str
    stderr_path: str


class ZCodeAgentSessionAdapter(AgentSessionAdapter):
    app_kind = APP_KIND

    def __init__(self, config: ZCodeConfig, *, environ: Optional[Dict[str, str]] = None):
        bad = config.problems()
        if bad:
            raise ValueError("; ".join(bad))
        self.config = config
        self._environ = dict(os.environ if environ is None else environ)
        self._live: Dict[str, _Live] = {}

    # ---------------------------------------------------------------- capabilities
    def capabilities(self) -> Dict[str, Any]:
        return {
            "app_kind": APP_KIND, "instance_id": self.config.instance_id,
            "tool_loop": "host (ZCode)", "tools_allowed": list(ALLOWED_TOOLS), "scope": sorted(ALLOWED_SCOPE),
            "capabilities": {
                "capabilities": {"supported": True},
                "submit": {"supported": True, "detail": "new native session only; persisted before launch"},
                "status": {"supported": True},
                "collect": {"supported": True, "detail": "host NDJSON kept as-is; never a success verdict"},
                "continue": {"supported": False, "reason": "out of scope for Hark-27"},
                "cancel": {"supported": False, "reason": "out of scope for Hark-27"},
            },
            "binding": {"provider_id": self.config.provider_id, "model_id": self.config.model_id,
                        "api_type": self.config.api_type, "mode": self.config.mode},
            "verified": [], "unverified": list(UNVERIFIED_HOST),
        }

    def continue_run(self, session, run_id, instructions, idempotency_key=None):
        raise UnsupportedCapability(APP_KIND, "continue", "not provided by this bridge")

    def cancel(self, session, run_id):
        raise UnsupportedCapability(APP_KIND, "cancel", "not provided by this bridge")

    # ---------------------------------------------------------------- admission
    def _admission(self, request: SubmitRequest) -> None:
        s, cfg = request.session, self.config
        if s.app_kind != APP_KIND or s.instance_id != cfg.instance_id:
            raise ValueError("request is not addressed to this ZCode instance")
        if s.session_id != NEW_SESSION:
            raise UnsupportedCapability(APP_KIND, "submit", "resuming an existing native session is not supported")
        if s.model_id != cfg.model_id:
            raise ValueError("request model is not the explicitly bound model")
        if not set(request.allowed_scope) or not set(request.allowed_scope) <= ALLOWED_SCOPE:
            raise UnsupportedCapability(APP_KIND, "submit", "only read/search scope is supported")
        if not request.idempotency_key:
            raise ValueError("an explicit idempotency key is required")
        if not isinstance(request.instructions, str) or not request.instructions.strip():
            raise ValueError("instructions must be non-empty")
        ws = request.workspace
        if not (isinstance(ws, str) and os.path.isabs(ws) and os.path.isdir(ws)):
            raise ValueError("workspace must be an existing absolute directory")
        real = os.path.realpath(ws)
        for other in (cfg.evidence_root, cfg.run_home_root):
            o = os.path.realpath(other)
            if real == o or real.startswith(o + os.sep) or o.startswith(real + os.sep):
                raise ValueError("workspace overlaps the evidence root or the run home")
        if ancestor_env_files(ws):
            raise ValueError("a .env file in the workspace or its ancestors would be loaded by the host")

    # ---------------------------------------------------------------- submit
    def submit(self, request: SubmitRequest) -> RunStatus:
        self._admission(request)
        reg = _Registry(self.config.evidence_root)
        fp = request.fingerprint()
        with reg:
            records = reg.load()
            reserved = [r for r in records if r["event"] == "reserved"]
            same = [r for r in reserved if r.get("idempotency_key") == request.idempotency_key]
            if same:
                if same[0].get("fingerprint") != fp:
                    raise IdempotencyConflict(request.idempotency_key)
                return self._status_locked(reg, records, same[0]["run_id"], replay=True)
            if reserved:
                raise RegistryUnusable(f"evidence root already holds {reserved[0]['run_id']}; "
                                       "no new key, attempt or re-dispatch on this root")
            run_id = f"zcode-run-{uuid.uuid4().hex[:12]}"
            binding = RunBinding.of(request)
            reg.append({"event": "reserved", "run_id": run_id, "idempotency_key": request.idempotency_key,
                        "fingerprint": fp, "snapshot": request.snapshot(), "binding": binding.__dict__,
                        "workspace": os.path.realpath(request.workspace),
                        "provider_id": self.config.provider_id, "model_id": self.config.model_id,
                        "api_type": self.config.api_type, "host_sha256": self.config.host_sha256})
            return self._launch(reg, run_id, request)

    def _env(self, home: str, provider_file: str) -> Dict[str, str]:
        env = {k: self._environ[k] for k in _ENV_KEEP if k in self._environ}
        env.update({"HOME": home, "USERPROFILE": home, "ZCODE_DATA_BASE_DIR": home,
                    "APPDATA": os.path.join(home, "AppData", "Roaming"),
                    "LOCALAPPDATA": os.path.join(home, "AppData", "Local"),
                    "XDG_CONFIG_HOME": os.path.join(home, ".config"),
                    "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE": provider_file, "NO_COLOR": "1"})
        return env

    def argv(self, request: SubmitRequest) -> List[str]:
        return list(self.config.argv_prefix) + [
            "--prompt", request.instructions, "--cwd", os.path.realpath(request.workspace),
            "--mode", self.config.mode, "--disallowed-tools", " ".join(DISALLOWED_TOOLS),
            "--workflow-mode", "disabled", "--output-format", "stream-json", "--no-color"]

    def _launch(self, reg: _Registry, run_id: str, request: SubmitRequest) -> RunStatus:
        cfg = self.config
        run_dir = os.path.join(cfg.evidence_root, "runs", run_id)
        os.makedirs(run_dir)
        stage = "host-pin"
        try:
            if sha256_file(cfg.host_file) != cfg.host_sha256:
                raise ValueError("host file bytes are not the pinned build")
            stage = "key"
            key = None
            if cfg.key_env:
                key = self._environ.get(cfg.key_env)
                if not key:
                    raise ValueError("bound key variable is absent")
            stage = "isolated-home"
            home = os.path.join(cfg.run_home_root, run_id)
            os.makedirs(os.path.join(home, ".zcode", "v2"))
            os.makedirs(os.path.join(home, ".zcode", "cli"))
            provider_file = os.path.join(home, ".zcode", "v2", "provider_config.json")
            fd = os.open(provider_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(cfg.provider_document(key), fh)
            with open(os.path.join(home, ".zcode", "cli", "config.json"), "w", encoding="utf-8") as fh:
                json.dump(RUN_CONFIG, fh)
        except (OSError, ValueError) as exc:
            reg.append({"event": "dispatch_failed", "run_id": run_id, "stage": stage,
                        "launched": False, "error_class": type(exc).__name__})
            return RunStatus(run_id=run_id, state=RunState.FAILED, binding=RunBinding.of(request),
                             native_state=f"not-launched:{stage}", evidence_ref=run_dir)
        stdout_path, stderr_path = os.path.join(run_dir, "stdout.jsonl"), os.path.join(run_dir, "stderr.txt")
        try:
            with open(stdout_path, "xb") as out, open(stderr_path, "xb") as err:
                proc = subprocess.Popen(self.argv(request), cwd=os.path.realpath(request.workspace),
                                        env=self._env(home, provider_file), stdin=subprocess.DEVNULL,
                                        stdout=out, stderr=err, shell=False)
        except OSError as exc:
            reg.append({"event": "dispatch_failed", "run_id": run_id, "stage": "launch",
                        "launched": False, "error_class": type(exc).__name__})
            return RunStatus(run_id=run_id, state=RunState.FAILED, binding=RunBinding.of(request),
                             native_state="launch-failed", evidence_ref=run_dir)
        self._live[run_id] = _Live(proc, stdout_path, stderr_path)
        reg.append({"event": "dispatched", "run_id": run_id, "pid": proc.pid})
        return RunStatus(run_id=run_id, state=RunState.RUNNING, binding=RunBinding.of(request),
                         native_state="process-started", evidence_ref=run_dir)

    # ---------------------------------------------------------------- status / collect
    def _resolve(self, records, session: SessionAddress, run_id: Optional[str]) -> Dict[str, Any]:
        res = [r for r in records if r["event"] == "reserved" and r["run_id"] == run_id]
        if not res:
            raise KeyError("run id is not registered on this evidence root")
        b = res[0]["binding"]
        actual = (session.app_kind, session.instance_id, session.session_id)
        if (b["app_kind"], b["instance_id"], b["session_id"]) != actual:
            raise AddressMismatch(run_id, (b["app_kind"], b["instance_id"], b["session_id"]), actual)
        return res[0]

    def status(self, session: SessionAddress, run_id: Optional[str]) -> RunStatus:
        reg = _Registry(self.config.evidence_root)
        with reg:
            records = reg.load()
            self._resolve(records, session, run_id)
            return self._status_locked(reg, records, run_id)

    def _status_locked(self, reg, records, run_id, replay: bool = False) -> RunStatus:
        mine = [r for r in records if r["run_id"] == run_id]
        res = mine[0]
        binding = RunBinding(**res["binding"])
        run_dir = os.path.join(self.config.evidence_root, "runs", run_id)
        last = {r["event"]: r for r in mine}
        note = "idempotent replay: same run, nothing relaunched" if replay else ""
        if "dispatch_failed" in last:
            return RunStatus(run_id, RunState.FAILED, binding, native_state=f"not-launched:{last['dispatch_failed']['stage']}",
                             evidence_ref=run_dir, note=note)
        if "dispatched" not in last:
            return RunStatus(run_id, RunState.UNKNOWN, binding, native_state="submit-outcome-unknown",
                             evidence_ref=run_dir, note=(note + "; " if note else "") + "frozen: no re-dispatch")
        if "exited" not in last:
            live = self._live.get(run_id)
            if live is None:
                return RunStatus(run_id, RunState.UNKNOWN, binding, native_state="outcome-unknown-after-reopen",
                                 evidence_ref=run_dir, note=(note + "; " if note else "") + "frozen: no re-dispatch")
            rc = live.process.poll()
            if rc is None:
                return RunStatus(run_id, RunState.RUNNING, binding, native_state="process-running",
                                 evidence_ref=run_dir, note=note)
            rec = {"event": "exited", "run_id": run_id, "returncode": rc}
            reg.append(rec)
            last["exited"] = rec
        if "collected" not in last:
            facts = self._facts(res, last["exited"]["returncode"], run_dir)
            rec = {"event": "collected", "run_id": run_id, "state": facts["state"],
                   "declared_status": facts["declared_status"], "evidence": facts["evidence"],
                   "session_id": facts["parsed"]["session_id"], "turn_id": facts["parsed"]["turn_id"]}
            reg.append(rec)
            last["collected"] = rec
        col = last["collected"]
        turn = None
        if isinstance(col.get("session_id"), str):
            turn = NativeTurn(native_turn_id=str(col.get("turn_id") or "unexposed"), native_session_id=col["session_id"])
        return RunStatus(run_id, col["state"], binding, native_turn=turn, latest_turn=turn,
                         native_state=f"exit:{last['exited']['returncode']};declared:{col['declared_status']}",
                         evidence_ref=run_dir, note=note)

    def _facts(self, res: Dict[str, Any], returncode: int, run_dir: str) -> Dict[str, Any]:
        evidence, data = {}, b""
        for name in ("stdout.jsonl", "stderr.txt"):
            p = os.path.join(run_dir, name)
            try:
                st = os.lstat(p)
                if not _stat.S_ISREG(st.st_mode):
                    raise OSError("not a regular file")
                with open(p, "rb") as fh:
                    blob = fh.read()
                evidence[name] = {"bytes": len(blob), "sha256": sha256_bytes(blob)}
                if name == "stdout.jsonl":
                    data = blob
            except OSError:
                evidence[name] = None
        parsed = parse_stream(data, workspace=res["workspace"], provider_id=res["provider_id"],
                              model_id=res["model_id"])
        if evidence.get("stdout.jsonl") is None:
            parsed["unknown"].append("stdout evidence missing")
        clean_exit = returncode == 0 and parsed["projection_status"] in ("idle", "completed")
        if parsed["fail"] or returncode != 0:
            state, declared = RunState.FAILED, ("host_completed" if clean_exit else "host_not_completed")
        elif parsed["unknown"]:
            state, declared = RunState.UNKNOWN, ("host_completed" if clean_exit else "unreported")
        else:
            state, declared = (RunState.DECLARED_COMPLETE, "host_completed") if clean_exit else \
                (RunState.UNKNOWN, "unreported")
        return {"state": state, "declared_status": declared, "evidence": evidence, "parsed": parsed}

    def collect(self, session: SessionAddress, run_id: Optional[str]) -> CollectedResult:
        st = self.status(session, run_id)
        reg = _Registry(self.config.evidence_root)
        with reg:
            records = reg.load()
        mine = {r["event"]: r for r in records if r["run_id"] == run_id}
        res = mine["reserved"]
        if "collected" not in mine:
            return CollectedResult(run_id=run_id, declared_status="unreported",
                                   unverified=[f"run is {st.state} ({st.native_state}); nothing collected",
                                               *UNVERIFIED_HOST])
        run_dir = os.path.join(self.config.evidence_root, "runs", run_id)
        facts = self._facts(res, mine["exited"]["returncode"], run_dir)
        unverified = list(UNVERIFIED_HOST) + facts["parsed"]["unknown"] + facts["parsed"]["fail"]
        if facts["evidence"] != mine["collected"]["evidence"]:
            unverified.append("evidence bytes changed after collection was recorded")
        if not facts["parsed"]["usage_known"]:
            unverified.append("usage not reported by the host (unknown, not 0)")
        p = facts["parsed"]
        artifact = {"native_session_id": p["session_id"], "native_trace_id": p["trace_id"],
                    "native_turn_id": p["turn_id"], "turn_ids": p["turn_ids"],
                    "provider_id": res["provider_id"], "model_id": res["model_id"],
                    "observed_models": p["models"], "tool_calls": p["tool_calls"],
                    "assistant_text": p["response"], "exit_code": mine["exited"]["returncode"],
                    "evidence": facts["evidence"], "mechanical_problems": p["fail"],
                    "state": mine["collected"]["state"]}
        return CollectedResult(run_id=run_id, declared_status=mine["collected"]["declared_status"],
                               artifacts=[artifact], usage=dict(p["usage"]) if p["usage"] else {},
                               raw_refs=[x for x in (p["session_id"], p["trace_id"]) if isinstance(x, str)],
                               unverified=unverified)


# --------------------------------------------------------------------------- public view
_STATES = frozenset({"unknown", "queued", "accepted", "running", "declared_complete", "failed",
                     "cancel_requested", "cancelled"})
_DECLARED = frozenset({"host_completed", "host_not_completed", "unreported"})


def public_summary(result: CollectedResult) -> Dict[str, Any]:
    """Controlled fields only: no free text, no paths, no host-reflected strings."""
    art = result.artifacts[0] if result.artifacts else {}
    tools = art.get("tool_calls") or []
    names = [c.get("toolName") if c.get("toolName") in ALLOWED_TOOLS else "other" for c in tools]
    ev = art.get("evidence") or {}
    return {
        "run_id": result.run_id if isinstance(result.run_id, str) and _RUN_ID.match(result.run_id) else None,
        "state": art.get("state") if art.get("state") in _STATES else "unrecognized",
        "declared_status": result.declared_status if result.declared_status in _DECLARED else "unrecognized",
        "tool_calls": {n: names.count(n) for n in sorted(set(names))},
        "mechanical_problem_count": len(art.get("mechanical_problems") or []),
        "unverified_count": len(result.unverified),
        "usage_known": bool(result.usage),
        "evidence_sha256": {k: (v or {}).get("sha256") for k, v in sorted(ev.items())
                            if k in ("stdout.jsonl", "stderr.txt")},
        "semantic_review": "required (not judged here)",
    }
