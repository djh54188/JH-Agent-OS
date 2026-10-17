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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.cli import build_task_summary, open_readonly_storage

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
DEFAULT_DRAFTS_PATH = Path(__file__).resolve().parents[1] / "data" / "gui" / "goal-drafts.json"


class ApiError(Exception):
    """Structured application-interface error (never a bare exception upstream)."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _error(exc: ApiError) -> Dict[str, Any]:
    return {"ok": False, "read_only": True,
            "error": {"code": exc.code, "message": exc.message}}


class AppInterface:
    """Unified application interface over the existing read-only capabilities."""

    def __init__(self, db_path: str | Path | None = None,
                 drafts_path: str | Path | None = None):
        root = Path(__file__).resolve().parents[1]
        self.db_path = Path(db_path) if db_path else root / "data" / "jh_agent_os.sqlite3"
        self.drafts_path = Path(drafts_path) if drafts_path else DEFAULT_DRAFTS_PATH

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
        unsupported = [name for name, info in CAPABILITY_SURFACE.items()
                       if not info.get("supported")]
        return {"ok": True, "read_only": True,
                "capabilities": CAPABILITY_SURFACE,
                "unsupported": unsupported,
                "note": "本批只读 + 草案；不触发 Agent/模型/工具执行"}

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
        drafts = self._read_drafts()
        drafts.append(draft)
        self._write_drafts(drafts)
        return {"ok": True, "read_only": False, "draft": draft,
                "note": "已保存为草案；未触发任何执行"}

    def list_drafts(self) -> Dict[str, Any]:
        drafts = self._read_drafts()
        return {"ok": True, "read_only": True, "drafts": drafts, "count": len(drafts),
                "note": "草案不是任务；不会出现在任务索引中"}

    def _read_drafts(self) -> List[Dict[str, Any]]:
        if not self.drafts_path.is_file():
            return []
        try:
            data = json.loads(self.drafts_path.read_text(encoding="utf-8"))
        except ValueError:
            raise ApiError("drafts_unreadable", "草案文件损坏，未覆盖原文件", 500)
        return data if isinstance(data, list) else []

    def _write_drafts(self, drafts: List[Dict[str, Any]]) -> None:
        self.drafts_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.drafts_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(drafts, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.drafts_path)
