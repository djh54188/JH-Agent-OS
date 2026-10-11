"""
JH-Agent OS SQLite Persistence & Restart Recovery Engine v1.0.0
Manages durable storage for tasks, events, tool executions, and reviews.
Enforces:
1. Missing token counts stored as NULL (unknown), never defaulted to 0.
2. Restart recovery: tasks interrupted with unconfirmed side-effect actions are
   transitioned to 'needs_attention' and NEVER blindly replayed.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional
import uuid

from jsonschema import Draft7Validator, FormatChecker
from src.state_machine import is_terminal_state, validate_transition

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = PROJECT_ROOT / "docs" / "schemas"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json_schema(name: str) -> Dict[str, Any]:
    with (SCHEMAS_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


class TaskStorage:
    """SQLite-backed store for tasks, task_events, tool_executions, and reviews."""

    def __init__(self, db_path: str | Path = ":memory:"):
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._task_schema = _load_json_schema("task.v1.json")
        self._init_db()

    def _init_db(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    status TEXT NOT NULL,
                    assigned_worker TEXT,
                    assigned_reviewer TEXT,
                    review_verdict TEXT,
                    error_info TEXT,
                    turns_used INTEGER NOT NULL DEFAULT 0,
                    tokens_used INTEGER,
                    tokens_unknown_count INTEGER NOT NULL DEFAULT 0,
                    repair_count INTEGER NOT NULL DEFAULT 0,
                    has_unconfirmed_side_effect INTEGER NOT NULL DEFAULT 0,
                    task_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_events (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT,
                    actor_role TEXT NOT NULL,
                    tokens_used INTEGER,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
                );

                CREATE TABLE IF NOT EXISTS tool_executions (
                    request_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    has_side_effect INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
                );

                CREATE TABLE IF NOT EXISTS reviews (
                    review_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    overall_score REAL NOT NULL,
                    reviewer_role TEXT NOT NULL,
                    review_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
                );
                """
            )

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def __del__(self) -> None:
        self.close()

    def validate_task_dict(self, task: Dict[str, Any]) -> None:
        Draft7Validator(self._task_schema, format_checker=FormatChecker()).validate(task)

    def create_task(self, task: Dict[str, Any], actor_role: str = "orchestrator") -> Dict[str, Any]:
        """Validate and persist a new task in 'created' state."""
        self.validate_task_dict(task)
        if task["status"] != "created":
            raise ValueError(f"New task must start in 'created' status, got '{task['status']}'")
        now = task.get("created_at") or _utc_now_iso()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO tasks (
                    task_id, schema_version, title, description, priority, status,
                    assigned_worker, assigned_reviewer, review_verdict, error_info,
                    turns_used, tokens_used, tokens_unknown_count, repair_count,
                    has_unconfirmed_side_effect, task_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, 0, 0, 0, ?, ?, ?)
                """,
                (
                    task["task_id"],
                    task["schema_version"],
                    task["title"],
                    task["description"],
                    task["priority"],
                    task["status"],
                    task.get("assigned_worker"),
                    task.get("assigned_reviewer"),
                    task.get("review_verdict"),
                    task.get("error_info"),
                    json.dumps(task, ensure_ascii=False),
                    now,
                    task.get("updated_at") or now,
                ),
            )
        self.record_event(
            task_id=task["task_id"],
            event_type="task_created",
            from_state=None,
            to_state="created",
            actor_role=actor_role,
            tokens_used=None,
            details={"title": task["title"], "priority": task["priority"]},
        )
        return self.get_task(task["task_id"])

    def get_task(self, task_id: str) -> Dict[str, Any]:
        cur = self._conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        row = cur.fetchone()
        if not row:
            raise KeyError(f"Task '{task_id}' not found")
        task_dict = json.loads(row["task_json"])
        task_dict["_runtime"] = {
            "turns_used": row["turns_used"],
            "tokens_used": row["tokens_used"],  # None when unknown
            "tokens_unknown_count": row["tokens_unknown_count"],
            "repair_count": row["repair_count"],
            "has_unconfirmed_side_effect": bool(row["has_unconfirmed_side_effect"]),
        }
        return task_dict

    def transition_task_state(
        self,
        task_id: str,
        target_state: str,
        *,
        actor_role: str = "orchestrator",
        reason: str = "",
        review_verdict: Optional[str] = None,
        error_info: Optional[str] = None,
        assigned_worker: Optional[str] = None,
        assigned_reviewer: Optional[str] = None,
        artifact_refs: Optional[List[str]] = None,
        tool_evidence: Optional[List[str]] = None,
        tokens_delta: Optional[int] = None,
        increment_turn: bool = False,
        increment_repair: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Validate state transition, update task record, and write audit event atomically."""
        cur = self._conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        row = cur.fetchone()
        if not row:
            raise KeyError(f"Task '{task_id}' not found")

        current_state = row["status"]
        effective_verdict = review_verdict if review_verdict is not None else row["review_verdict"]

        try:
            validate_transition(current_state, target_state, review_verdict=effective_verdict)
        except Exception as exc:
            self.record_event(
                task_id=task_id,
                event_type="invalid_state_transition_rejected",
                from_state=current_state,
                to_state=target_state,
                actor_role=actor_role,
                tokens_used=None,
                details={"error": str(exc), "reason": reason},
            )
            raise

        task_dict = json.loads(row["task_json"])
        now = _utc_now_iso()
        task_dict["status"] = target_state
        task_dict["updated_at"] = now
        if review_verdict is not None:
            task_dict["review_verdict"] = review_verdict
        if error_info is not None:
            task_dict["error_info"] = error_info
        if assigned_worker is not None:
            task_dict["assigned_worker"] = assigned_worker
        if assigned_reviewer is not None:
            task_dict["assigned_reviewer"] = assigned_reviewer
        if artifact_refs is not None:
            existing = list(task_dict.get("artifact_refs") or [])
            for ref in artifact_refs:
                if ref not in existing:
                    existing.append(ref)
            task_dict["artifact_refs"] = existing
        if tool_evidence is not None:
            existing_ev = list(task_dict.get("tool_execution_evidence") or [])
            for ev in tool_evidence:
                if ev not in existing_ev:
                    existing_ev.append(ev)
            task_dict["tool_execution_evidence"] = existing_ev

        self.validate_task_dict(task_dict)

        turns_used = row["turns_used"] + (1 if increment_turn else 0)
        repair_count = row["repair_count"] + (1 if increment_repair else 0)
        current_tokens = row["tokens_used"]
        tokens_unknown_count = row["tokens_unknown_count"]

        if tokens_delta is not None:
            current_tokens = (current_tokens or 0) + tokens_delta

        with self._conn:
            self._conn.execute(
                """
                UPDATE tasks
                SET status = ?,
                    assigned_worker = ?,
                    assigned_reviewer = ?,
                    review_verdict = ?,
                    error_info = ?,
                    turns_used = ?,
                    tokens_used = ?,
                    tokens_unknown_count = ?,
                    repair_count = ?,
                    task_json = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (
                    target_state,
                    task_dict.get("assigned_worker"),
                    task_dict.get("assigned_reviewer"),
                    task_dict.get("review_verdict"),
                    task_dict.get("error_info"),
                    turns_used,
                    current_tokens,
                    tokens_unknown_count,
                    repair_count,
                    json.dumps(task_dict, ensure_ascii=False),
                    now,
                    task_id,
                ),
            )

        ev_details = dict(details or {})
        if reason:
            ev_details["reason"] = reason
        self.record_event(
            task_id=task_id,
            event_type="state_transition",
            from_state=current_state,
            to_state=target_state,
            actor_role=actor_role,
            tokens_used=tokens_delta,
            details=ev_details,
        )
        return self.get_task(task_id)

    def record_usage(
        self,
        task_id: str,
        *,
        actor_role: str,
        tokens_used: Optional[int],
        increment_turn: bool = True,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Record model/tool invocation usage.
        If tokens_used is None (unknown), increments tokens_unknown_count and keeps tokens_used NULL if no prior known tokens exist.
        Never defaults unknown token usage to 0.
        """
        cur = self._conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        row = cur.fetchone()
        if not row:
            raise KeyError(f"Task '{task_id}' not found")

        turns_used = row["turns_used"] + (1 if increment_turn else 0)
        current_tokens = row["tokens_used"]
        unknown_count = row["tokens_unknown_count"]

        if tokens_used is None:
            unknown_count += 1
        else:
            current_tokens = (current_tokens or 0) + tokens_used

        with self._conn:
            self._conn.execute(
                """
                UPDATE tasks
                SET turns_used = ?, tokens_used = ?, tokens_unknown_count = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (turns_used, current_tokens, unknown_count, _utc_now_iso(), task_id),
            )

        ev_details = dict(details or {})
        ev_details["tokens_status"] = "unknown" if tokens_used is None else "recorded"
        self.record_event(
            task_id=task_id,
            event_type="usage_recorded",
            from_state=row["status"],
            to_state=row["status"],
            actor_role=actor_role,
            tokens_used=tokens_used,
            details=ev_details,
        )

    def record_tool_start(
        self,
        tool_request: Dict[str, Any],
        *,
        has_side_effect: bool,
    ) -> None:
        """Mark a tool execution as in_flight BEFORE executing it, enabling crash/restart detection."""
        now = _utc_now_iso()
        task_id = tool_request["task_id"]
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO tool_executions (
                    request_id, task_id, step_id, tool_name, risk_level,
                    has_side_effect, status, request_json, result_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'in_flight', ?, NULL, ?, ?)
                """,
                (
                    tool_request["request_id"],
                    task_id,
                    tool_request["step_id"],
                    tool_request["tool_name"],
                    tool_request["risk_level"],
                    1 if has_side_effect else 0,
                    json.dumps(tool_request, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            if has_side_effect:
                self._conn.execute(
                    "UPDATE tasks SET has_unconfirmed_side_effect = 1, updated_at = ? WHERE task_id = ?",
                    (now, task_id),
                )

    def record_tool_completion(
        self,
        tool_result: Dict[str, Any],
        *,
        confirmed_clean: bool = True,
    ) -> None:
        """Update tool execution record upon completion or policy denial."""
        now = _utc_now_iso()
        req_id = tool_result["request_id"]
        task_id = tool_result["task_id"]
        status = tool_result["status"]
        with self._conn:
            self._conn.execute(
                """
                UPDATE tool_executions
                SET status = ?, result_json = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (status, json.dumps(tool_result, ensure_ascii=False), now, req_id),
            )
            if confirmed_clean:
                # Check if any other in_flight side-effect executions remain
                cur = self._conn.execute(
                    """
                    SELECT COUNT(*) AS cnt FROM tool_executions
                    WHERE task_id = ? AND has_side_effect = 1 AND status IN ('in_flight', 'interrupted_unconfirmed')
                    """,
                    (task_id,),
                )
                if cur.fetchone()["cnt"] == 0:
                    self._conn.execute(
                        "UPDATE tasks SET has_unconfirmed_side_effect = 0, updated_at = ? WHERE task_id = ?",
                        (now, task_id),
                    )

    def record_review(self, review_result: Dict[str, Any]) -> None:
        now = review_result.get("created_at") or _utc_now_iso()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO reviews (
                    review_id, task_id, verdict, overall_score, reviewer_role, review_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_result["review_id"],
                    review_result["task_id"],
                    review_result["verdict"],
                    review_result["overall_score"],
                    review_result["reviewer_role"],
                    json.dumps(review_result, ensure_ascii=False),
                    now,
                ),
            )

    def record_event(
        self,
        *,
        task_id: str,
        event_type: str,
        from_state: Optional[str],
        to_state: Optional[str],
        actor_role: str,
        tokens_used: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        now = _utc_now_iso()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO task_events (
                    event_id, task_id, event_type, from_state, to_state,
                    actor_role, tokens_used, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    task_id,
                    event_type,
                    from_state,
                    to_state,
                    actor_role,
                    tokens_used,  # Stored as NULL if None
                    json.dumps(details or {}, ensure_ascii=False),
                    now,
                ),
            )
        return event_id

    def list_events(self, task_id: str) -> List[Dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY rowid ASC",
            (task_id,),
        )
        events = []
        for r in cur.fetchall():
            events.append({
                "event_id": r["event_id"],
                "task_id": r["task_id"],
                "event_type": r["event_type"],
                "from_state": r["from_state"],
                "to_state": r["to_state"],
                "actor_role": r["actor_role"],
                "tokens_used": r["tokens_used"],  # None if NULL in DB
                "details": json.loads(r["details_json"]),
                "created_at": r["created_at"],
            })
        return events

    def list_tool_executions(self, task_id: str) -> List[Dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM tool_executions WHERE task_id = ? ORDER BY rowid ASC",
            (task_id,),
        )
        items = []
        for r in cur.fetchall():
            items.append({
                "request_id": r["request_id"],
                "task_id": r["task_id"],
                "step_id": r["step_id"],
                "tool_name": r["tool_name"],
                "risk_level": r["risk_level"],
                "has_side_effect": bool(r["has_side_effect"]),
                "status": r["status"],
                "request": json.loads(r["request_json"]),
                "result": json.loads(r["result_json"]) if r["result_json"] else None,
            })
        return items

    def recover_interrupted_tasks(self) -> List[Dict[str, Any]]:
        """
        Inspect non-terminal tasks after a process restart.
        Iron Rule: Actions whose side effects cannot be proven safe MUST NOT be automatically replayed.
        Tasks interrupted with in-flight or unconfirmed side effects are moved to 'needs_attention'.
        """
        cur = self._conn.execute(
            "SELECT * FROM tasks WHERE status NOT IN ('succeeded', 'failed', 'cancelled')"
        )
        rows = cur.fetchall()
        recovery_summary = []

        for row in rows:
            task_id = row["task_id"]
            status = row["status"]
            has_unconfirmed = bool(row["has_unconfirmed_side_effect"])

            # Check in-flight tool executions
            t_cur = self._conn.execute(
                "SELECT * FROM tool_executions WHERE task_id = ? AND status = 'in_flight'",
                (task_id,),
            )
            in_flight_tools = t_cur.fetchall()
            any_side_effect_in_flight = any(bool(t["has_side_effect"]) for t in in_flight_tools)

            if has_unconfirmed or any_side_effect_in_flight or status in {"executing", "repairing"}:
                now = _utc_now_iso()
                with self._conn:
                    self._conn.execute(
                        """
                        UPDATE tool_executions
                        SET status = 'interrupted_unconfirmed', updated_at = ?
                        WHERE task_id = ? AND status = 'in_flight'
                        """,
                        (now, task_id),
                    )
                if status != "needs_attention":
                    self.transition_task_state(
                        task_id,
                        "needs_attention",
                        actor_role="orchestrator",
                        reason="unconfirmed_side_effect_no_auto_replay",
                        error_info="Process restarted during execution; unconfirmed side-effect actions cannot be automatically replayed.",
                        details={
                            "recovery_action": "blocked_auto_replay",
                            "in_flight_count": len(in_flight_tools),
                            "had_unconfirmed_side_effect": has_unconfirmed or any_side_effect_in_flight,
                        },
                    )
                recovery_summary.append({
                    "task_id": task_id,
                    "previous_state": status,
                    "recovered_state": "needs_attention",
                    "auto_replay_allowed": False,
                    "reason": "unconfirmed_side_effect_no_auto_replay",
                })
            else:
                # Safe non-executing states (e.g., created, classified, planning, awaiting_approval, needs_attention)
                self.record_event(
                    task_id=task_id,
                    event_type="restart_recovered_safe_state",
                    from_state=status,
                    to_state=status,
                    actor_role="orchestrator",
                    tokens_used=None,
                    details={"recovery_action": "preserved_state", "auto_replay_allowed": status in {"created", "classified", "planning"}},
                )
                recovery_summary.append({
                    "task_id": task_id,
                    "previous_state": status,
                    "recovered_state": status,
                    "auto_replay_allowed": status in {"created", "classified", "planning"},
                    "reason": "no_side_effects_started",
                })

        return recovery_summary
