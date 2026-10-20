"""JH-019 统一应用接口 (application interface).

One entry point that every presentation layer (GUI now, CLI/other later) calls.
It is deliberately thin: it **reuses** the existing read-only capabilities
(`TaskStorage` reads and `src.cli.build_task_summary`) instead of copying the
task state machine, the review rules or the acceptance logic, and it does not
reach into SQLite itself — storage is the only module that talks to the DB.

Scope of this batch: queries + a clearly marked goal **draft**. It never
triggers an Agent, a model or a tool, and it never decides task success — success
is read from the recorded facts (`task_result.status`) and nothing else.

Truthfulness rules enforced here:

* an unsupported capability is reported as unsupported, never simulated;
* every registered Agent session is marked `connected: false`, `simulated: true`
  and carries the evidence note from the JH-018 probe (measured / unmeasured /
  not found);
* a missing database, an unknown task, an empty result and an error are each
  returned as their own explicit shape — no placeholder data, no fake状态.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.cli import build_task_summary, open_readonly_storage
from src.agent_session_codex import (
    RegistryUnavailable,
    parse_registry_text,
    raw_evidence_state,
    read_registry_text,
)
from src.run_history_consistency import HistoryInconsistent, check_history
from src.session_probe import ProbeConfig, ProbeRefused, SessionProbeService

# Capabilities the interface surface is designed to expose. Anything the
# collaboration core can do but that is NOT wired in this batch is listed with
# supported=false and a reason, so the GUI can display the gap instead of
# inventing a state.
CAPABILITY_SURFACE: Dict[str, Dict[str, Any]] = {
    "workbench": {"supported": True, "note": "任务索引与阻塞项（只读）"},
    "task_detail": {"supported": True, "note": "任务详情/产物/证据（只读，复用既有查询）"},
    "agents_sessions": {"supported": True, "note": "会话登记视图（静态登记，未接入适配器）"},
    "review_delivery": {"supported": True, "note": "审查与交付视图（只读，复用既有审核绑定）"},
    "goal_draft": {"supported": True, "note": "目标输入保存为显式标记的草案"},
    "agent_run_history": {"supported": True,
                          "note": "真实会话运行记录（服务端配置的登记文件；只读，不派发/不收集）"},
    "dispatch": {"supported": False, "reason": "JH-020：真实派发未接入"},
    "collect": {"supported": False, "reason": "JH-020：交付收集未接入"},
    "review_trigger": {"supported": False, "reason": "JH-020：自动触发独立审查未接入"},
}

# Static session registry. These are candidate applications measured in JH-018;
# nothing is connected in this batch. `evidence` records what the probe actually
# observed so the GUI can show capability limits rather than a green connection.
SESSION_REGISTRY: List[Dict[str, Any]] = [
    {
        "session_id": "sim-zcode-desktop-01",
        "app_kind": "zcode",
        "instance_id": "local-desktop",
        "role": "worker",
        "model_id": None,
        "connected": False,
        "simulated": True,
        "evidence": {
            "addressing": "实测：sess_<uuid> + workspace 键",
            "dispatch": "实测（app-server session/send）；本批未接入",
            "collect": "实测（session/messages、session/usage、session/events）",
            "cancel": "实测（session/stop，请求语义）",
            "review": "未实测",
        },
        "note": "静态登记：未接入适配器，无真实连接",
    },
    {
        "session_id": "sim-codex-review-01",
        "app_kind": "codex",
        "instance_id": "local-cli",
        "role": "reviewer",
        "model_id": None,
        "connected": False,
        "simulated": True,
        "evidence": {
            "addressing": "实测：thread_id + thread/list",
            "dispatch": "实测（thread/resume+turn/start、exec resume）；本批未接入",
            "collect": "实测（item/*、turn/completed usage）",
            "cancel": "未实测（turn/interrupt 仅验 schema）",
            "review": "未实测（review/start 仅验 schema）",
        },
        "note": "静态登记：未接入适配器，无真实连接",
    },
]

DRAFT_KIND = "jh019-goal-draft/v1"
# A stored draft entry must carry all of these; a file missing any of them is
# structurally wrong and is reported, not silently read as an empty list.
REQUIRED_DRAFT_KEYS = ("kind", "draft_id", "draft", "execution", "title", "goal")

# Bounded retry for the final atomic rename. On Windows a just-written temp file
# (or the destination) can be transiently locked by an indexer/scanner/open
# reader, which fails the rename with PermissionError even though the bytes are
# complete. Retrying the rename briefly resolves that class of race; the rename
# stays atomic and a persistent failure still leaves the old file untouched.
REPLACE_ATTEMPTS = 5
REPLACE_BACKOFF_SECONDS = 0.02
DEFAULT_DRAFTS_PATH = Path(__file__).resolve().parents[1] / "data" / "gui" / "goal-drafts.json"
# The run registry is a SERVER-side setting: the browser never supplies a path,
# it can only ask for the list or for one run id that must exist in the history.
DEFAULT_RUNS_PATH = Path(__file__).resolve().parents[1] / "data" / "gui" / "agent-runs.jsonl"

# Registry states, each reported as its own shape. None of them is folded into
# another and none of them triggers a repair of the file on disk.
RUNS_ABSENT = "absent"
RUNS_EMPTY = "empty"
RUNS_OK = "ok"
RUNS_UNREADABLE = "unreadable"
RUNS_CORRUPT = "corrupt"


def _raw_field(raw: Any, key: str) -> Any:
    """Read one field of a raw reference without assuming it is an object."""
    return raw.get(key) if isinstance(raw, dict) else None


def _binding_from_snapshot(snapshot: Optional[str]) -> Dict[str, Any]:
    """Read the requested address back out of the recorded canonical snapshot.

    The snapshot is the immutable record of the dispatch request, so the role
    and requested model come from what was actually registered rather than from
    a guess. A snapshot that cannot be parsed is reported, not invented.
    """
    if not isinstance(snapshot, str) or not snapshot:
        return {"parsed": False, "reason": "没有规范化请求快照"}
    try:
        data = json.loads(snapshot)
    except ValueError:
        return {"parsed": False, "reason": "请求快照无法解析"}
    if not isinstance(data, dict):
        return {"parsed": False, "reason": "请求快照不是对象"}
    return {"parsed": True, "role": data.get("role"), "requested_model": data.get("model_id"),
            "workspace": data.get("workspace"), "schema": data.get("schema"),
            "budget_tokens": data.get("budget_tokens"),
            "allowed_scope": data.get("allowed_scope")}


class ApiError(Exception):
    """Structured application-interface error (never a bare exception upstream)."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


# Single-process write boundary: one lock per resolved drafts path, held across
# the whole read → append → publish cycle. Multi-process coordination is out of
# scope for this batch.
_DRAFT_LOCKS: Dict[str, "threading.RLock"] = {}
_DRAFT_LOCKS_GUARD = threading.Lock()


def _drafts_lock(path: Path) -> "threading.RLock":
    key = str(Path(path).resolve())
    with _DRAFT_LOCKS_GUARD:
        lock = _DRAFT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _DRAFT_LOCKS[key] = lock
        return lock


def _atomic_replace(tmp_name: str, destination: Path) -> None:
    """os.replace with a bounded retry for transient Windows sharing errors."""
    last: Optional[BaseException] = None
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(tmp_name, destination)
            return
        except PermissionError as exc:
            last = exc
            if attempt + 1 < REPLACE_ATTEMPTS:
                time.sleep(REPLACE_BACKOFF_SECONDS * (attempt + 1))
    raise last


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _error(exc: ApiError) -> Dict[str, Any]:
    return {"ok": False, "read_only": True,
            "error": {"code": exc.code, "message": exc.message}}


class AppInterface:
    """Unified application interface over the existing read-only capabilities."""

    def __init__(self, db_path: str | Path | None = None,
                 drafts_path: str | Path | None = None,
                 runs_path: str | Path | None = None,
                 evidence_roots=None,
                 session_probe: Optional[ProbeConfig] = None,
                 probe_adapter_factory=None, probe_runtime_check=None, probe_runner=None):
        root = Path(__file__).resolve().parents[1]
        self.db_path = Path(db_path) if db_path else root / "data" / "jh_agent_os.sqlite3"
        self.drafts_path = Path(drafts_path) if drafts_path else DEFAULT_DRAFTS_PATH
        self.runs_path = Path(runs_path) if runs_path else DEFAULT_RUNS_PATH
        # Evidence may only be read from server-owned roots. The default is the
        # `raw` directory beside the configured registry; any other root must be
        # configured explicitly on the server. A record's own `path` field can
        # narrow nothing and widen nothing.
        if evidence_roots:
            self.evidence_roots = tuple(Path(item) for item in evidence_roots)
        else:
            self.evidence_roots = (self.runs_path.parent / "raw",)
        # Hark-派单-06: the opt-in GUI session probe. Off unless the SERVER passes
        # an enabled ProbeConfig; its registry is its own, never the history
        # source above, and it is read back through this same read-only reader.
        db_for_reader = self.db_path
        self.session_probe = SessionProbeService(
            session_probe or ProbeConfig(enabled=False),
            history_runs_path=self.runs_path,
            reader_factory=lambda registry, raw: AppInterface(
                db_path=db_for_reader, runs_path=registry, evidence_roots=(raw,)),
            adapter_factory=probe_adapter_factory, runtime_check=probe_runtime_check,
            runner=probe_runner)

    # -- storage -----------------------------------------------------------
    def _open(self):
        """Open the task database read-only, or report that it is absent.

        A missing database is a normal, explicitly reported state for the GUI
        (fresh checkout, no tasks produced yet) — never an empty-looking success.
        """
        if not self.db_path.is_file():
            raise ApiError("database_missing",
                           f"任务数据库不存在：{self.db_path}（尚无任务记录）", 404)
        try:
            return open_readonly_storage(self.db_path)
        except Exception as exc:  # pragma: no cover - defensive
            raise ApiError("database_unreadable", f"任务数据库无法只读打开：{exc}", 500)

    # -- capabilities ------------------------------------------------------
    def capabilities(self) -> Dict[str, Any]:
        surface = dict(CAPABILITY_SURFACE)
        enabled = bool(self.session_probe.config.enabled)
        surface["session_probe"] = (
            {"supported": True, "note": "固定标识会话试运行（服务端已启用；仍须用户逐次确认）"}
            if enabled else
            {"supported": False, "reason": "会话试运行默认关闭；服务端未启用"})
        unsupported = [name for name, info in surface.items()
                       if not info.get("supported")]
        return {"ok": True, "read_only": True,
                "capabilities": surface,
                "unsupported": unsupported,
                "note": ("只读 + 草案；会话试运行已由服务端启用（逐次确认，确认后调用真实模型）"
                         if enabled else "本批只读 + 草案；不触发 Agent/模型/工具执行")}

    # -- workbench ---------------------------------------------------------
    def list_tasks(self, limit: int = 50) -> Dict[str, Any]:
        if not self.db_path.is_file():
            # A list with no database is an explicit empty state, not a failure
            # and not a silently empty success.
            return {"ok": True, "read_only": True, "database_present": False,
                    "tasks": [], "count": 0,
                    "note": f"任务数据库不存在：{self.db_path}（尚无任务记录）"}
        storage = self._open()
        try:
            rows = storage.list_tasks(limit=limit)
        finally:
            storage.close()
        return {"ok": True, "read_only": True, "database_present": True,
                "tasks": rows, "count": len(rows)}

    # -- task detail -------------------------------------------------------
    def task_detail(self, task_id: str) -> Dict[str, Any]:
        storage = self._open()
        try:
            try:
                summary = build_task_summary(storage, task_id)
            except KeyError:
                raise ApiError("task_not_found", f"任务不存在：{task_id}", 404)
            events = storage.list_events(task_id)
            versions = storage.list_artifact_versions(task_id)
            return {"ok": True, "read_only": True,
                    "task_result": summary["task_result"],
                    "events_count": len(events),
                    "artifact_versions": [
                        {"version": v.get("version"), "artifact_id": v.get("artifact_id"),
                         "sha256": v.get("sha256"), "artifact_type": v.get("artifact_type")}
                        for v in versions
                    ],
                    "note": "状态与成功与否均取自记录事实；本界面不判定成功"}
        finally:
            storage.close()

    # -- agents / sessions -------------------------------------------------
    def list_sessions(self) -> Dict[str, Any]:
        sessions = []
        for entry in SESSION_REGISTRY:
            item = dict(entry)
            item["evidence"] = dict(entry["evidence"])
            sessions.append(item)
        return {"ok": True, "read_only": True,
                "transport": "static-registry (JH-019: no adapter wired)",
                "sessions": sessions, "count": len(sessions),
                "note": "全部为静态登记：connected=false、simulated=true；未接入适配器，无真实连接"}

    # -- review & delivery -------------------------------------------------
    def review_delivery(self, task_id: str) -> Dict[str, Any]:
        storage = self._open()
        try:
            try:
                summary = build_task_summary(storage, task_id)
            except KeyError:
                raise ApiError("task_not_found", f"任务不存在：{task_id}", 404)
            task = summary["task_result"]
            bindings = storage.list_review_bindings(task_id)
            return {"ok": True, "read_only": True,
                    "task_id": task_id,
                    # status/verdict are the recorded facts; the UI must render
                    # them, not derive them.
                    "task_status": task["status"],
                    "review_verdict": task["review_verdict"],
                    "latest_review": task["latest_review"],
                    "acceptance_results": task["acceptance_results"],
                    "delivery_scope": task["delivery_scope"],
                    "review_bindings_count": len(bindings),
                    "current_artifact": task["current_artifact"],
                    "note": "审查通过与否来自既有审核绑定；交付是否完成来自 delivery_scope"}
        finally:
            storage.close()

    # -- agent run history (read-only; server-configured source) -----------
    def _read_runs(self):
        """(state, runs, message). Reads the configured registry; never writes it.

        Reuses the accepted validator: every record is checked for the fields
        the recovery/binding/idempotency rebuild needs. A bad line is never
        skipped and the file is never repaired, migrated or truncated.
        """
        try:
            text = read_registry_text(self.runs_path)
        except RegistryUnavailable as exc:
            return RUNS_UNREADABLE, [], str(exc)
        if text is None:
            return RUNS_ABSENT, [], f"登记文件不存在：{self.runs_path}（尚无运行记录）"
        try:
            records = parse_registry_text(text)
        except RegistryUnavailable as exc:
            return RUNS_CORRUPT, [], str(exc)
        if not records:
            return RUNS_EMPTY, [], f"登记文件存在但没有任何记录：{self.runs_path}"
        # Cross-record consistency is checked BEFORE grouping, summarising or
        # touching any evidence: one contradiction makes the whole history
        # corrupt, with no partial summary and no repair of the source.
        try:
            check_history(records)
        except HistoryInconsistent as exc:
            return RUNS_CORRUPT, [], str(exc)
        grouped: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for record in records:
            run_id = record["run_id"]
            if run_id not in grouped:
                grouped[run_id] = {"run_id": run_id, "events": [], "records": []}
                order.append(run_id)
            grouped[run_id]["events"].append(record["event"])
            grouped[run_id]["records"].append(record)
        return RUNS_OK, [grouped[run_id] for run_id in order], ""

    def _task_link(self, task_id: Optional[str]) -> Dict[str, Any]:
        """Whether the run's logical task exists in the task database.

        Only reads; a missing database or task is reported as not linked and
        never turns into a created task record.
        """
        if not task_id:
            return {"checked": False, "linked": False,
                    "note": "本次派发没有逻辑任务 id；未关联任何任务记录（不创建任务）"}
        if not self.db_path.is_file():
            return {"checked": False, "linked": False,
                    "note": "任务数据库不存在，无法核对关联；未创建任务记录"}
        storage = self._open()
        try:
            try:
                storage.get_task(task_id)
            except KeyError:
                return {"checked": True, "linked": False,
                        "note": "任务库中没有该 id 的任务；未关联（不创建任务记录）"}
            return {"checked": True, "linked": True, "note": "任务库中存在该任务记录"}
        finally:
            storage.close()

    def _run_view(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """One run as recorded, with the display fields the UI needs."""
        records = entry["records"]
        first, last = records[0], records[-1]
        collected = next((r for r in reversed(records) if r["event"] == "collected"), None)
        uncertain = next((r for r in records if r["event"] == "dispatch_uncertain"), None)
        raw = (collected or {}).get("raw") or None
        evidence_state, evidence_detail = raw_evidence_state(
            raw, allowed_roots=self.evidence_roots, registry_dir=self.runs_path.parent)
        usage = (collected or {}).get("usage") or {}
        binding = _binding_from_snapshot(first.get("request_snapshot"))
        return {
            "run_id": entry["run_id"],
            "events": list(entry["events"]),
            "app_kind": first.get("app_kind"),
            "instance_id": first.get("instance_id"),
            "session_id": first.get("session_id"),
            "role": binding.get("role") if binding.get("parsed") else None,
            "requested_model": binding.get("requested_model") if binding.get("parsed") else None,
            "native_model": last.get("native_model") or (collected or {}).get("native_model"),
            "logical_task_id": first.get("task_id"),
            "attempt": first.get("attempt"),
            "request_fingerprint": first.get("request_fingerprint"),
            "idempotency_key": first.get("idempotency_key"),
            "native_session_id": last.get("native_session_id"),
            "native_turns": list(last.get("native_turns") or []),
            "record_state": last.get("state"),
            "native_state": last.get("native_state"),
            "last_recorded_at": last.get("at"),
            "first_recorded_at": first.get("at"),
            "usage": {"turn": usage.get("turn"), "thread_total": usage.get("thread_total"),
                      "model_context_window": usage.get("model_context_window"),
                      "source": usage.get("source")},
            "evidence": {"state": evidence_state, "detail": evidence_detail,
                         "path": _raw_field(raw, "path"), "sha256": _raw_field(raw, "sha256"),
                         "frames": _raw_field(raw, "frames"), "bytes": _raw_field(raw, "bytes")},
            "unverified": list((collected or {}).get("unverified") or []),
            "declared_status": (collected or {}).get("declared_status"),
            "dispatch_uncertain_reason": (uncertain or {}).get("reason"),
            "snapshot": binding,
        }

    def list_agent_runs(self) -> Dict[str, Any]:
        """History of real agent-session runs, from the configured registry only."""
        state, entries, message = self._read_runs()
        payload: Dict[str, Any] = {
            "ok": True, "read_only": True,
            "source": {"configured": True, "path": str(self.runs_path),
                       "browser_supplied": False,
                       "evidence_roots": [str(item) for item in self.evidence_roots],
                       "note": "登记来源与证据允许根由服务端配置；浏览器不能传任意磁盘路径"},
            "state": state,
            "runs": [self._run_view(entry) for entry in entries] if state == RUNS_OK else [],
            "count": len(entries) if state == RUNS_OK else 0,
            "message": message,
            "live": False,
            "note": ("历史记录，不代表实时在线；declared_complete 是执行方声明，"
                     "不代表任务验收成功。查询不启动 app-server、不调用模型、不发送会话消息。"),
        }
        if state in (RUNS_CORRUPT, RUNS_UNREADABLE):
            payload["error"] = {"code": f"runs_{state}", "message": message}
        return payload

    def agent_run_detail(self, run_id: str) -> Dict[str, Any]:
        """One run by id. The id is looked up in the history, never used as a path."""
        state, entries, message = self._read_runs()
        if state != RUNS_OK:
            payload = self.list_agent_runs()
            payload["run"] = None
            return payload
        entry = next((item for item in entries if item["run_id"] == run_id), None)
        if entry is None:
            raise ApiError("run_not_found", f"登记中没有该 run：{run_id}", 404)
        view = self._run_view(entry)
        view["link"] = self._task_link(view.get("logical_task_id"))
        return {"ok": True, "read_only": True,
                "source": {"configured": True, "path": str(self.runs_path),
                           "browser_supplied": False,
                           "evidence_roots": [str(item) for item in self.evidence_roots]},
                "state": RUNS_OK, "run": view, "records": entry["records"],
                "live": False,
                "note": ("历史记录，不代表实时在线；declared_complete 不代表任务验收成功；"
                         "证据状态来自磁盘字节核对（verified/missing/mismatch/unverifiable）")}

    # -- session probe (Hark-派单-06; opt-in, confirmed, at most once) -----
    def session_probe_status(self) -> Dict[str, Any]:
        """Read-only view of the probe: availability, plan and the latest run."""
        return self.session_probe.status()

    def prepare_session_probe(self, payload: Any) -> Dict[str, Any]:
        try:
            return self.session_probe.prepare(payload)
        except ProbeRefused as exc:
            raise ApiError(exc.code, exc.message, exc.http_status)

    def start_session_probe(self, payload: Any) -> Dict[str, Any]:
        try:
            return self.session_probe.start(payload)
        except ProbeRefused as exc:
            raise ApiError(exc.code, exc.message, exc.http_status)

    # -- goal drafts (this batch does NOT execute) -------------------------
    def save_goal_draft(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ApiError("draft_malformed", "草案必须是对象")
        title = str(payload.get("title") or "").strip()
        goal = str(payload.get("goal") or "").strip()
        if not title or not goal:
            raise ApiError("draft_incomplete", "草案需要非空的 title 与 goal")
        for forbidden in ("dispatch", "execute", "run", "start_task"):
            if payload.get(forbidden):
                # Drafts are inert input; asking a draft to run is refused
                # rather than silently ignored.
                raise ApiError("draft_cannot_execute",
                               f"草案不会执行任何操作（收到 {forbidden}）；真实派发属 JH-020", 409)
        draft = {
            "kind": DRAFT_KIND,
            "draft_id": "draft-" + uuid.uuid4().hex[:12],
            "created_at": _now(),
            "draft": True,
            "execution": "not_triggered",
            "title": title,
            "goal": goal,
            "target_session_id": payload.get("target_session_id"),
            "note": "显式标记的草案：不会派发、不调用模型、不启动工具",
        }
        # One process-wide lock per drafts file spans the ENTIRE read → append →
        # publish cycle, so two concurrent saves in this process cannot lose a
        # record or collide on a temporary file. Multi-process coordination is
        # deliberately out of scope for this batch (single local process).
        with _drafts_lock(self.drafts_path):
            drafts = self._read_drafts_locked()
            drafts.append(draft)
            self._publish_drafts_locked(drafts)
        return {"ok": True, "read_only": False, "draft": draft,
                "note": "已保存为草案；未触发任何执行"}

    def list_drafts(self) -> Dict[str, Any]:
        with _drafts_lock(self.drafts_path):
            drafts = self._read_drafts_locked()
        return {"ok": True, "read_only": True, "drafts": drafts, "count": len(drafts),
                "note": "草案不是任务；不会出现在任务索引中"}

    def _read_drafts_locked(self) -> List[Dict[str, Any]]:
        """Strict read; the caller holds the drafts lock.

        Three distinct outcomes, none of which is "pretend it is empty":
        absent file → empty list; unparseable bytes → `drafts_unreadable`;
        parseable JSON of the wrong shape → `drafts_malformed`. A failed read
        never rewrites the original bytes.
        """
        if not self.drafts_path.is_file():
            return []
        raw = self.drafts_path.read_bytes()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError("drafts_unreadable", "草案文件无法解析，未覆盖原文件", 500)
        if not isinstance(data, list):
            raise ApiError("drafts_malformed",
                           f"草案文件顶层应为数组，实际为 {type(data).__name__}；未覆盖原文件", 500)
        for index, entry in enumerate(data):
            if not isinstance(entry, dict):
                raise ApiError("drafts_malformed",
                               f"草案文件第 {index} 条不是对象；未覆盖原文件", 500)
            missing = [key for key in REQUIRED_DRAFT_KEYS if key not in entry]
            if missing:
                raise ApiError("drafts_malformed",
                               f"草案文件第 {index} 条缺少字段 {missing}；未覆盖原文件", 500)
        return data

    def _publish_drafts_locked(self, drafts: List[Dict[str, Any]]) -> None:
        """Write to a uniquely named temp file in the same directory, then
        atomically replace. A failure at any point leaves the previous file
        byte-identical, and concurrent writers cannot share a temp path."""
        parent = self.drafts_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(drafts, ensure_ascii=False, indent=2).encode("utf-8")
        handle, tmp_name = tempfile.mkstemp(dir=str(parent), prefix=".goal-drafts-",
                                            suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            _atomic_replace(tmp_name, self.drafts_path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
