"""GUI real session probe (Hark-派单-06): one fixed-token round trip, opt-in.

An application service, not a scheduler. It lets the GUI ask for exactly one
thing: after the server has enabled it AND the user has explicitly confirmed,
create a NEW isolated read-only Codex worker session through the existing real
`CodexAgentSessionAdapter`, send a fixed echo instruction carrying a
server-generated token, collect the reply bound to that run's thread and turn,
and show the reply, the usage and the raw evidence.

Boundaries (pinned by tests/test_hark06_session_probe.py):

* Default off. Opening, refreshing or querying never dispatches. A start needs
  the server switch, a server-issued single-use probe id and `confirm: true`.
* The browser supplies nothing but `{probe_id, confirm}`. Model, workspace,
  registry, evidence location and instruction are server-side; any other field
  is refused, so no free task, command, path or existing session can ride in.
* At most one dispatch per confirmation. The probe id is the adapter's
  idempotency key; a repeated, concurrent, timed-out or post-restart start never
  dispatches again. One registry source holds at most one unresolved probe:
  reserved / dispatched / dispatch_uncertain / collect_timeout block new probes
  and stay `unknown — 待人工处理`; nothing is cancelled or completed on its behalf.
* Refuses before any external call when the registry is unreadable, corrupt or
  inconsistent, when the probe registry would be the read-only history source,
  when the runtime is missing, or when the server demands a finite token budget
  (the adapter cannot enforce one; it is refused, not ignored).
* Reply, usage and evidence are read back from the probe's own registry with the
  accepted validators and the digest check against disk bytes. Usage that is not
  bound to this run's thread and turn is not adopted, and unknown usage is never
  written as zero.
* A completed probe is a completed ROUND TRIP. It grants no task success, and the
  static session registry stays `connected=false`.

Only `src.app_api` wires this service; core state machine, review and budget
modules never import it.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.agent_session import RunState, SessionAddress, SubmitRequest
from src.agent_session_codex import CodexAgentSessionAdapter, default_codex_argv

PROBE_SCHEMA = "jh020-gui-session-probe/v1"
PROBE_APP_KIND = "codex"
PROBE_INSTANCE = "codex-app-server/local"
PROBE_ROLE = "worker"
DEFAULT_PROBE_MODEL = "gpt-6.1-sol"
DEFAULT_COLLECT_TIMEOUT = 240.0
CONFIRM_TTL_SECONDS = 600.0

REGISTRY_NAME = "probe-runs.jsonl"
RAW_DIR_NAME = "raw"
WORKSPACE_DIR_NAME = "ws"

#: The only fields a start request may carry.
ALLOWED_START_FIELDS = frozenset({"probe_id", "confirm"})
PROBE_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")

#: Last events after which a probe is resolved. Everything else is unresolved:
#: unfinished prefixes, and the terminal-but-unknown dispatch_uncertain /
#: collect_timeout, which stay with a human.
RESOLVED_EVENTS = frozenset({"collected", "dispatch_failed"})
UNKNOWN_EVENTS = frozenset({"dispatch_uncertain", "collect_timeout"})

#: Native item types that mean a tool ran. A probe must observe none.
TOOL_ITEM_TYPES = frozenset({"commandExecution", "fileChange", "mcpToolCall", "webSearch",
                             "dynamicToolCall", "collabAgentToolCall", "imageView"})


class ProbeRefused(Exception):
    """A probe request refused before any dispatch."""

    def __init__(self, code: str, message: str, http_status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


@dataclass(frozen=True)
class ProbeConfig:
    """Server-side probe settings. The browser can change none of them."""

    enabled: bool = False
    probe_dir: Optional[str] = None
    model_id: Optional[str] = DEFAULT_PROBE_MODEL
    budget_tokens: Optional[int] = None
    collect_timeout: float = DEFAULT_COLLECT_TIMEOUT


# --- guard predicates (each protects one claim; each can be weakened alone) ---

def confirmation_given(payload: Dict[str, Any]) -> bool:
    """Only the literal boolean true is a confirmation."""
    return payload.get("confirm") is True


def payload_fields_allowed(payload: Dict[str, Any]) -> bool:
    """A start carries nothing but the probe id and the confirmation."""
    return set(payload) <= ALLOWED_START_FIELDS


def probe_enabled(config: "ProbeConfig") -> bool:
    """The server switch; off by default."""
    return config.enabled is True


def registry_usable(state: Any) -> bool:
    """Only an absent, empty or fully consistent registry may be extended."""
    return state in ("absent", "empty", "ok")


def run_is_resolved(events: List[str]) -> bool:
    """A probe is resolved only after `collected` or `dispatch_failed`."""
    return bool(events) and events[-1] in RESOLVED_EVENTS


def inflight_blocks(inflight: Optional[Dict[str, Any]]) -> bool:
    """A probe already accepted by this process blocks any other."""
    return inflight is not None


def already_used(probe_id: str, consumed: set, recorded: bool) -> bool:
    """A probe id that was consumed here or is in the registry is never dispatched again."""
    return probe_id in consumed or recorded


def budget_satisfiable(budget_tokens: Optional[int]) -> bool:
    """The adapter cannot enforce a finite token budget; such a demand is refused."""
    return budget_tokens is None


def usage_bound_to_run(usage: Dict[str, Any], thread_id: Optional[str],
                       turn_id: Optional[str]) -> bool:
    """Usage is adopted only when its source is exactly this run's thread and turn."""
    source = (usage or {}).get("source") or {}
    return (bool(thread_id) and bool(turn_id)
            and source.get("thread_id") == thread_id and source.get("turn_id") == turn_id)


def echo_matches(texts: List[str], token: str) -> bool:
    """The reply is the fixed token, alone."""
    return len(texts) == 1 and isinstance(texts[0], str) and texts[0].strip() == token


def probe_token(probe_id: str) -> str:
    return f"JH-PROBE-{probe_id.upper()}"


def probe_instruction(token: str) -> str:
    return (f"Reply with exactly this token and nothing else: {token}. "
            "Do not use any tools, do not read or write files.")


def codex_runtime_problem() -> Optional[str]:
    """Why the local Codex runtime cannot be started, or None. No process is spawned."""
    argv = default_codex_argv()
    node, script = argv[0], argv[1]
    if not (os.path.isfile(node) or shutil.which(node)):
        return f"node runtime not found: {node}"
    if not os.path.isfile(script):
        return f"codex app-server script not found: {script}"
    return None


def default_adapter_factory(registry_path: str, workspace: str):
    """The real adapter: a fresh instance that reloads the probe registry."""
    return CodexAgentSessionAdapter(cwd=workspace, registry_path=registry_path,
                                    instance_ids=(PROBE_INSTANCE,))


def _thread_runner(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="jh-session-probe", daemon=True).start()


def _listing(root: Path) -> Optional[List[str]]:
    if not root.is_dir():
        return None
    out = []
    for current, _dirs, files in os.walk(root):
        for name in files:
            out.append(str((Path(current) / name).relative_to(root)))
    return sorted(out)


def _tool_items(path: Optional[str]) -> Optional[List[str]]:
    """Tool item types seen in a VERIFIED raw evidence file; None when unreadable."""
    if not path:
        return None
    try:
        lines = Path(path).read_bytes().decode("utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    seen = []
    for line in lines:
        try:
            frame = json.loads(line)
        except ValueError:
            continue
        item = ((frame.get("params") or {}).get("item") or {}) if isinstance(frame, dict) else {}
        kind = item.get("type") if isinstance(item, dict) else None
        if kind in TOOL_ITEM_TYPES:
            seen.append(kind)
    return seen


class SessionProbeService:
    """One opt-in probe source: one registry, one raw-evidence root, one lock."""

    def __init__(self, config: ProbeConfig, *, history_runs_path: Optional[Path],
                 reader_factory: Callable[[Path, Path], Any],
                 adapter_factory: Optional[Callable[[str, str], Any]] = None,
                 runtime_check: Optional[Callable[[], Optional[str]]] = None,
                 runner: Optional[Callable[[Callable[[], None]], None]] = None,
                 clock: Callable[[], float] = time.time):
        self.config = config
        base = Path(config.probe_dir) if config.probe_dir else (
            Path(__file__).resolve().parents[1] / "data" / "gui" / "session-probe")
        self.probe_dir = base
        self.registry_path = base / REGISTRY_NAME
        self.raw_root = base / RAW_DIR_NAME
        self.workspace_root = base / WORKSPACE_DIR_NAME
        self.history_runs_path = Path(history_runs_path) if history_runs_path else None
        self._reader_factory = reader_factory
        self._adapter_factory = adapter_factory or default_adapter_factory
        # Shown on every status so an offline/synthetic preview can never be
        # mistaken for a real model result.
        self.adapter_source = "real_codex_adapter" if adapter_factory is None else "injected_offline_adapter"
        self._runtime_check = runtime_check or codex_runtime_problem
        self._runner = runner or _thread_runner
        self._clock = clock
        self._lock = threading.Lock()
        self._prepared: Dict[str, float] = {}
        self._consumed: set = set()
        self._inflight: Optional[Dict[str, Any]] = None
        self._last_error: Optional[Dict[str, Any]] = None
        self.submit_calls = 0            # adapter.submit calls made by this service

    # -- reading ---------------------------------------------------------------
    def _reader(self):
        return self._reader_factory(self.registry_path, self.raw_root)

    def _registry_view(self) -> Dict[str, Any]:
        """The probe registry through the accepted read-only history reader."""
        return self._reader().list_agent_runs()

    def _same_file(self, a: Path, b: Optional[Path]) -> bool:
        if b is None:
            return False
        try:
            return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
        except (OSError, ValueError):
            return True

    def _blockers(self, view: Dict[str, Any]) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []

        def block(code: str, message: str) -> None:
            out.append({"code": code, "message": message})

        if not probe_enabled(self.config):
            block("probe_disabled", "服务端未启用会话试运行（默认关闭）")
        if self._same_file(self.registry_path, self.history_runs_path):
            block("probe_registry_is_history_source",
                  "试运行登记与只读历史来源是同一文件；拒绝把历史来源变成可写来源")
        for path in (self.probe_dir, self.registry_path, self.raw_root, self.workspace_root):
            if os.path.islink(path):
                block("probe_path_is_link", f"试运行目录含链接，拒绝写入：{path}")
        if self.probe_dir.exists() and not self.probe_dir.is_dir():
            block("probe_dir_invalid", f"试运行目录不是目录：{self.probe_dir}")
        if not budget_satisfiable(self.config.budget_tokens):
            block("probe_budget_unenforceable",
                  f"服务端要求有限 token 预算 {self.config.budget_tokens}，适配器无法强制；拒发")
        problem = self._runtime_check()
        if problem:
            block("probe_runtime_missing", f"本地 Codex 运行时不可用：{problem}")
        state = view.get("state")
        if not registry_usable(state):
            if state in ("corrupt", "unreadable"):
                block(f"probe_registry_{state}", f"试运行登记不可用：{view.get('message')}")
            else:
                block("probe_registry_unknown", f"试运行登记状态未知：{state!r}")
        for run in view.get("runs") or []:
            events = run.get("events") or []
            last = events[-1] if events else None
            if not run_is_resolved(events):
                block("probe_unresolved",
                      f"存在未解决的试运行 {run.get('run_id')}（最后事件 {last}）；结果未知，待人工处理，不会自动重发")
        if inflight_blocks(self._inflight):
            block("probe_in_flight", f"试运行 {self._inflight['probe_id']} 正在进行")
        return out

    # -- views -----------------------------------------------------------------
    def _plan(self) -> Dict[str, Any]:
        """What a probe WILL call. Shown before confirmation; nothing here is sent."""
        return {
            "app_kind": PROBE_APP_KIND, "instance_id": PROBE_INSTANCE,
            "session": "new（新建隔离会话；不接受既有会话）", "role": PROBE_ROLE,
            "model": self.config.model_id,
            "sandbox": "read-only（thread 与 turn 两级）", "network": "off",
            "approval_policy": "never（审批请求一律拒绝）",
            "instruction_template": probe_instruction("JH-PROBE-<服务端生成>"),
            "workspace_root": str(self.workspace_root),
            "registry": str(self.registry_path), "evidence_root": str(self.raw_root),
            "budget": ("适配器不支持硬 token 预算；真实调用会使用模型额度"
                       if self.config.budget_tokens is None else
                       f"服务端要求 {self.config.budget_tokens} tokens 上限，无法强制，拒发"),
            "collect_timeout_seconds": self.config.collect_timeout,
        }

    def _run_view(self, run: Dict[str, Any], reader) -> Dict[str, Any]:
        events = run.get("events") or []
        last = events[-1] if events else None
        probe_id = run.get("idempotency_key")
        token = probe_token(probe_id) if isinstance(probe_id, str) and PROBE_ID_PATTERN.match(probe_id) else None
        detail = reader.agent_run_detail(run["run_id"])
        records = detail.get("records") or []
        collected = next((r for r in reversed(records) if r.get("event") == "collected"), None)
        snapshot = {}
        try:
            snapshot = json.loads(records[0].get("request_snapshot") or "{}") if records else {}
        except ValueError:
            snapshot = {}
        instruction_bound = bool(token) and snapshot.get("instructions") == probe_instruction(token)
        texts = list((collected or {}).get("assistant_texts") or [])
        turns = run.get("native_turns") or []
        thread_id = run.get("native_session_id")
        turn_id = turns[-1] if turns else None
        usage = run.get("usage") or {}
        bound = usage_bound_to_run(usage, thread_id, turn_id)
        evidence = run.get("evidence") or {}
        tools = _tool_items(evidence.get("path")) if evidence.get("state") == "verified" else None
        if collected is None:
            echo = "unknown"
        else:
            echo = "matched" if (token and instruction_bound and echo_matches(texts, token)) else "mismatch"
        inflight = self._inflight is not None and self._inflight.get("probe_id") == probe_id
        if inflight:
            outcome = "in_progress"
        elif last == "collected":
            ok = (run.get("record_state") == RunState.DECLARED_COMPLETE and echo == "matched"
                  and evidence.get("state") == "verified" and tools == [])
            outcome = "round_trip_complete" if ok else "round_trip_not_proven"
        elif last == "dispatch_failed":
            outcome = "dispatch_failed"
        else:
            outcome = "unknown_manual"
        workspace = self.workspace_root / probe_id if token else None
        return {
            "run_id": run["run_id"], "probe_id": probe_id, "token": token,
            "events": events, "record_state": run.get("record_state"),
            "native_state": run.get("native_state"), "native_model": run.get("native_model"),
            "requested_model": run.get("requested_model"),
            "native_session_id": thread_id, "native_turn_id": turn_id,
            "first_recorded_at": run.get("first_recorded_at"),
            "last_recorded_at": run.get("last_recorded_at"),
            "instruction_bound": instruction_bound,
            "reply_texts": texts, "echo": echo,
            "usage": ({"turn": usage.get("turn"), "thread_total": usage.get("thread_total"),
                       "source": usage.get("source")} if bound else None),
            "usage_state": "bound" if bound else ("unbound" if usage.get("turn") or usage.get("thread_total")
                                                  else "unknown"),
            "evidence": evidence, "tool_items": tools,
            "workspace_files": _listing(workspace) if workspace else None,
            "unverified": run.get("unverified") or [],
            "declared_status": run.get("declared_status"),
            "outcome": outcome,
            "note": "试运行往返完成只表示固定标识往返；不授予任何任务成功（Task succeeded）",
        }

    def status(self) -> Dict[str, Any]:
        """Read-only. Never prepares, never dispatches, never writes."""
        view = self._registry_view()
        with self._lock:
            blockers = self._blockers(view)
            inflight = dict(self._inflight) if self._inflight else None
            last_error = dict(self._last_error) if self._last_error else None
        runs = view.get("runs") or []
        latest = self._run_view(runs[-1], self._reader()) if runs else None
        return {
            "ok": True, "read_only": True, "schema": PROBE_SCHEMA,
            "enabled": bool(self.config.enabled), "available": not blockers,
            "adapter_source": self.adapter_source,
            "blockers": blockers, "plan": self._plan(),
            "registry": {"state": view.get("state"), "message": view.get("message"),
                         "count": view.get("count"), "path": str(self.registry_path)},
            "inflight": inflight, "last_error": last_error, "latest": latest,
            "history_count": len(runs), "live_session_registry_changed": False,
            "note": ("真实调用会使用模型额度；试运行往返完成不等于任务验收成功；"
                     "静态会话登记仍为 connected=false。"),
        }

    # -- actions ---------------------------------------------------------------
    def prepare(self, payload: Any) -> Dict[str, Any]:
        """Issue a single-use probe id. Nothing is sent and nothing is written."""
        if payload not in (None, {}):
            raise ProbeRefused("probe_field_forbidden", "准备请求不接受任何字段", 400)
        with self._lock:
            blockers = self._blockers(self._registry_view())
            if blockers:
                raise ProbeRefused(blockers[0]["code"], blockers[0]["message"],
                                   403 if blockers[0]["code"] == "probe_disabled" else 409)
            now = self._clock()
            self._prepared = {k: v for k, v in self._prepared.items() if v > now}
            probe_id = secrets.token_hex(8)
            self._prepared[probe_id] = now + CONFIRM_TTL_SECONDS
        token = probe_token(probe_id)
        return {"ok": True, "read_only": True, "probe_id": probe_id, "token": token,
                "instruction": probe_instruction(token), "expires_in_seconds": CONFIRM_TTL_SECONDS,
                "plan": self._plan(), "dispatched": False,
                "note": "仅生成一次性试运行编号；确认后才会派发"}

    def start(self, payload: Any) -> Dict[str, Any]:
        """Dispatch at most once for a confirmed, prepared probe id."""
        if not isinstance(payload, dict):
            raise ProbeRefused("probe_malformed", "请求必须是对象", 400)
        if not payload_fields_allowed(payload):
            extra = sorted(set(payload) - ALLOWED_START_FIELDS)
            raise ProbeRefused("probe_field_forbidden",
                               f"试运行只接受 probe_id 与 confirm；拒绝字段 {extra}", 400)
        if not confirmation_given(payload):
            raise ProbeRefused("probe_not_confirmed", "未明确确认（confirm 必须为 true）；未派发", 400)
        probe_id = payload.get("probe_id")
        if not isinstance(probe_id, str) or not PROBE_ID_PATTERN.match(probe_id):
            raise ProbeRefused("probe_id_invalid", "试运行编号无效；未派发", 400)
        with self._lock:
            # Read the registry INSIDE the lock so a start can never decide on a
            # history that a just-finished probe has already extended.
            view = self._registry_view()
            recorded = any(run.get("idempotency_key") == probe_id for run in view.get("runs") or [])
            if already_used(probe_id, self._consumed, recorded):
                return {"ok": True, "read_only": False, "probe_id": probe_id,
                        "accepted": False, "replay": True, "dispatched_now": False,
                        "note": "该试运行编号已使用；不会重发"}
            expiry = self._prepared.get(probe_id)
            if expiry is None or expiry <= self._clock():
                self._prepared.pop(probe_id, None)
                raise ProbeRefused("probe_not_prepared",
                                   "试运行编号未签发或已过期（服务重开后需重新准备）；未派发", 409)
            blockers = self._blockers(view)
            if blockers:
                raise ProbeRefused(blockers[0]["code"], blockers[0]["message"],
                                   403 if blockers[0]["code"] == "probe_disabled" else 409)
            del self._prepared[probe_id]
            self._consumed.add(probe_id)
            self._inflight = {"probe_id": probe_id, "phase": "accepted",
                              "accepted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            self._last_error = None
        self._runner(lambda: self._execute(probe_id))
        return {"ok": True, "read_only": False, "probe_id": probe_id, "accepted": True,
                "replay": False, "dispatched_now": True,
                "note": "已受理一次真实试运行；结果以登记与证据为准"}

    def _build_request(self, probe_id: str, workspace: Path) -> SubmitRequest:
        token = probe_token(probe_id)
        session = SessionAddress(app_kind=PROBE_APP_KIND, instance_id=PROBE_INSTANCE,
                                 session_id=f"new:gui-probe-{probe_id}",
                                 model_id=self.config.model_id, role=PROBE_ROLE)
        return SubmitRequest(task_id=f"gui-probe-{probe_id}", attempt=1, session=session,
                             instructions=probe_instruction(token), workspace=str(workspace),
                             allowed_scope=(), idempotency_key=probe_id, artifacts=(),
                             budget_tokens=self.config.budget_tokens)

    def _set_phase(self, phase: str) -> None:
        with self._lock:
            if self._inflight is not None:
                self._inflight["phase"] = phase

    def _execute(self, probe_id: str) -> None:
        adapter = None
        try:
            workspace = self.workspace_root / probe_id
            workspace.mkdir(parents=True, exist_ok=False)
            request = self._build_request(probe_id, workspace)
            adapter = self._adapter_factory(str(self.registry_path), str(workspace))
            self._set_phase("dispatching")
            self.submit_calls += 1
            status = adapter.submit(request)
            if status.state == RunState.RUNNING and status.native_turn is not None:
                self._set_phase("collecting")
                adapter.collect(request.session, status.run_id, timeout=self.config.collect_timeout)
        except Exception as exc:                       # recorded, never retried
            with self._lock:
                self._last_error = {"probe_id": probe_id, "type": type(exc).__name__,
                                    "message": str(exc)}
        finally:
            if adapter is not None:
                try:
                    adapter.close()
                except Exception:
                    pass
            with self._lock:
                self._inflight = None
