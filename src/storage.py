"""
JH-Agent OS SQLite Persistence & Restart Recovery Engine v1.0.0
Manages durable storage for tasks, events, tool executions, and reviews.
Enforces:
1. Missing token counts stored as NULL (unknown), never defaulted to 0.
2. Restart recovery: tasks interrupted with unconfirmed side-effect actions are
   transitioned to 'needs_attention' and NEVER blindly replayed.
"""

from datetime import datetime, timezone
from contextlib import contextmanager
from functools import wraps
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional
import uuid

from jsonschema import Draft7Validator, FormatChecker
from src.state_machine import InvalidStateTransitionError, is_terminal_state, validate_transition

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = PROJECT_ROOT / "docs" / "schemas"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json_schema(name: str) -> Dict[str, Any]:
    with (SCHEMAS_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


class StaleAttemptError(Exception):
    """A delayed response belongs to an obsolete attempt or a finished task."""


def atomic(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.transaction():
            return method(self, *args, **kwargs)
    return wrapped


class TaskStorage:
    """SQLite-backed store for tasks, task_events, tool_executions, and reviews."""

    def __init__(self, db_path: str | Path = ":memory:", *, read_only: bool = False):
        self.db_path = str(db_path)
        self.read_only = bool(read_only)
        self._lock = threading.RLock()
        self._task_schema = _load_json_schema("task.v1.json")
        if self.read_only:
            if self.db_path == ":memory:":
                raise FileNotFoundError("Cannot open :memory: database in read-only mode")
            db_file = Path(self.db_path).resolve()
            if not db_file.is_file():
                raise FileNotFoundError(f"Database file does not exist: '{db_file}'")
            uri = f"file:{db_file.as_posix()}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA query_only = ON")
        else:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._init_db()

    @contextmanager
    def transaction(self):
        if self.read_only:
            raise PermissionError("TaskStorage is opened in read-only mode; writes and migrations are forbidden")
        # Nested storage operations share the outer transaction, including audit events.
        with self._lock:
            outer = not self._conn.in_transaction
            if outer:
                self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                if outer:
                    self._conn.commit()
            except BaseException:
                if outer:
                    self._conn.rollback()
                raise

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

                CREATE TABLE IF NOT EXISTS task_control (
                    task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
                    deadline REAL, owner_id TEXT, active_attempt_id TEXT,
                    pending_json TEXT, approvals_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS model_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    actor_role TEXT NOT NULL, status TEXT NOT NULL,
                    context_json TEXT NOT NULL, response_json TEXT
                );
                CREATE TABLE IF NOT EXISTS review_bindings (
                    review_id TEXT PRIMARY KEY REFERENCES reviews(review_id),
                    task_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL, acceptance_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifact_versions (
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    version INTEGER NOT NULL,
                    artifact_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    envelope_json TEXT NOT NULL,
                    generation_attempt_id TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, version)
                );
                CREATE TABLE IF NOT EXISTS memory_records (
                    memory_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    category TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO task_control(task_id) SELECT task_id FROM tasks;
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

    @atomic
    def create_task(self, task: Dict[str, Any], actor_role: str = "orchestrator") -> Dict[str, Any]:
        """Validate and persist a new task in 'created' state."""
        self.validate_task_dict(task)
        if task["status"] != "created":
            raise ValueError(f"New task must start in 'created' status, got '{task['status']}'")
        now = task.get("created_at") or _utc_now_iso()
        with self.transaction():
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
            self._conn.execute("INSERT INTO task_control(task_id) VALUES (?)", (task["task_id"],))
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

    @atomic
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

        if target_state == "succeeded":
            self._validate_success(task_id)

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
            current_tokens = None if tokens_unknown_count else (current_tokens or 0) + tokens_delta

        with self.transaction():
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

    @atomic
    def record_usage(
        self,
        task_id: str,
        *,
        actor_role: str,
        tokens_used: Optional[int],
        increment_turn: bool = True,
        details: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
    ) -> None:
        """
        Record model/tool invocation usage.
        Unknown usage increments tokens_unknown_count and keeps the aggregate NULL thereafter.
        Never defaults unknown token usage to 0.
        """
        if tokens_used is not None and (isinstance(tokens_used, bool) or not isinstance(tokens_used, int) or tokens_used < 0):
            raise ValueError("Invalid provider token usage")
        if event_id and self._conn.execute("SELECT 1 FROM task_events WHERE event_id = ?", (event_id,)).fetchone():
            return
        cur = self._conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        row = cur.fetchone()
        if not row:
            raise KeyError(f"Task '{task_id}' not found")

        turns_used = row["turns_used"] + (1 if increment_turn else 0)
        current_tokens = row["tokens_used"]
        unknown_count = row["tokens_unknown_count"]

        if tokens_used is None:
            unknown_count += 1
            current_tokens = None
        else:
            current_tokens = None if unknown_count else (current_tokens or 0) + tokens_used

        with self.transaction():
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
            event_id=event_id,
        )

    @atomic
    def record_tool_start(
        self,
        tool_request: Dict[str, Any],
        *,
        has_side_effect: bool,
        allowed_states: tuple = ("executing",),
    ) -> bool:
        """Mark a tool execution as in_flight BEFORE executing it, enabling crash/restart detection.

        allowed_states: worker-proposed tools default to the executing phase only;
        the control-layer post-review apply runs in the reviewing phase and passes
        that state explicitly. Late worker tool requests stay rejected."""
        now = _utc_now_iso()
        task_id = tool_request["task_id"]
        task = self.get_task(task_id)
        if task["status"] not in allowed_states:
            self.record_event(task_id=task_id, event_type="late_tool_request_rejected",
                              from_state=task["status"], to_state=task["status"],
                              actor_role="orchestrator", details={"request_id": tool_request["request_id"]})
            return False
        prior = self._conn.execute("SELECT * FROM tool_executions WHERE request_id = ?", (tool_request["request_id"],)).fetchone()
        if prior:
            if json.loads(prior["request_json"]) != tool_request or prior["status"] != "success":
                raise ValueError("Tool request ID reused with changed parameters or uncertain result")
            return False
        with self.transaction():
            self._conn.execute(
                """
                INSERT INTO tool_executions (
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
        return True

    @atomic
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
        prior = self._conn.execute("SELECT * FROM tool_executions WHERE request_id = ?", (req_id,)).fetchone()
        if not prior or prior["task_id"] != task_id or prior["tool_name"] != tool_result["tool_name"]:
            raise ValueError("Tool result does not match the recorded request")
        with self.transaction():
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

    @atomic
    def record_review(self, review_result: Dict[str, Any]) -> None:
        now = review_result.get("created_at") or _utc_now_iso()
        with self.transaction():
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

    @atomic
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
        event_id: Optional[str] = None,
    ) -> str:
        event_id = event_id or f"evt_{uuid.uuid4().hex[:12]}"
        now = _utc_now_iso()
        with self.transaction():
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

    def get_control(self, task_id):
        with self._lock:
            row = self._conn.execute("SELECT * FROM task_control WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"Task '{task_id}' has no control record")
            return dict(row)

    def has_uncertain_attempt(self, task_id):
        return self._conn.execute(
            "SELECT 1 FROM model_attempts WHERE task_id = ? AND status IN ('in_flight', 'interrupted_unconfirmed') LIMIT 1",
            (task_id,),
        ).fetchone() is not None

    @atomic
    def set_deadline(self, task_id, deadline):
        self._conn.execute("UPDATE task_control SET deadline = COALESCE(deadline, ?) WHERE task_id = ?", (deadline, task_id))

    @atomic
    def claim_task(self, task_id, owner_id):
        return self._conn.execute(
            "UPDATE task_control SET owner_id = ? WHERE task_id = ? AND owner_id IS NULL",
            (owner_id, task_id),
        ).rowcount == 1

    @atomic
    def release_task(self, task_id, owner_id):
        self._conn.execute("UPDATE task_control SET owner_id = NULL WHERE task_id = ? AND owner_id = ?", (task_id, owner_id))

    @atomic
    def hold_request(self, task_id, request):
        self._conn.execute("UPDATE task_control SET pending_json = ? WHERE task_id = ?", (json.dumps(request), task_id))

    @atomic
    def approve_request(self, task_id, request_id, fingerprint):
        control = self.get_control(task_id)
        pending = json.loads(control["pending_json"] or "null")
        if not pending or pending["request_id"] != request_id:
            raise ValueError("Approval does not identify the pending request")
        approvals = json.loads(control["approvals_json"])
        approvals[request_id] = fingerprint(pending)
        self._conn.execute("UPDATE task_control SET approvals_json = ?, pending_json = NULL WHERE task_id = ?", (json.dumps(approvals), task_id))

    @atomic
    def begin_attempt(self, task_id, actor_role, context):
        task = self.get_task(task_id)
        if is_terminal_state(task["status"]):
            raise StaleAttemptError("Cannot start a model attempt for a terminal task")
        attempt_id = f"attempt_{uuid.uuid4().hex}"
        self._conn.execute("INSERT INTO model_attempts VALUES (?, ?, ?, 'in_flight', ?, NULL)",
                           (attempt_id, task_id, actor_role, json.dumps(context)))
        self._conn.execute("UPDATE task_control SET active_attempt_id = ? WHERE task_id = ?", (attempt_id, task_id))
        return attempt_id

    @atomic
    def complete_attempt(self, task_id, attempt_id, response, tokens_used):
        attempt = self._conn.execute("SELECT * FROM model_attempts WHERE attempt_id = ? AND task_id = ?", (attempt_id, task_id)).fetchone()
        if not attempt:
            raise StaleAttemptError("Unknown attempt")
        if attempt["status"] == "completed":
            return False  # Duplicate completion cannot double-charge usage.
        if self.get_control(task_id)["active_attempt_id"] != attempt_id or is_terminal_state(self.get_task(task_id)["status"]):
            raise StaleAttemptError("Response belongs to a stale attempt")
        self._conn.execute("UPDATE model_attempts SET status = 'completed', response_json = ? WHERE attempt_id = ?",
                           (json.dumps(response), attempt_id))
        self.record_usage(task_id, actor_role=attempt["actor_role"], tokens_used=tokens_used,
                          event_id=f"usage_{attempt_id}", details={"attempt_id": attempt_id})
        return True

    @atomic
    def bind_review(self, review, attempt_id, artifacts, acceptance):
        task_id = review["task_id"]
        attempt = self._conn.execute("SELECT task_id, actor_role, status FROM model_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        if not attempt or attempt["task_id"] != task_id or attempt["actor_role"] != "reviewer" or attempt["status"] != "completed":
            raise StaleAttemptError("Review does not match a completed reviewer attempt")
        if self.get_control(task_id)["active_attempt_id"] != attempt_id:
            raise StaleAttemptError("Review belongs to a stale attempt")
        self.record_review(review)
        self._conn.execute("INSERT INTO review_bindings VALUES (?, ?, ?, ?, ?)",
                           (review["review_id"], task_id, attempt_id, json.dumps(artifacts), json.dumps(acceptance)))

    def list_review_bindings(self, task_id: str) -> List[Dict[str, Any]]:
        """Ordered review bindings with their review payload and acceptance results (read-only)."""
        cur = self._conn.execute(
            "SELECT b.review_id, b.attempt_id, b.artifacts_json, b.acceptance_json, r.review_json "
            "FROM review_bindings b JOIN reviews r USING(review_id) "
            "WHERE b.task_id = ? ORDER BY r.rowid ASC",
            (task_id,),
        )
        return [
            {
                "review_id": r["review_id"],
                "attempt_id": r["attempt_id"],
                "review": json.loads(r["review_json"]),
                "acceptance": json.loads(r["acceptance_json"]),
                "artifacts": json.loads(r["artifacts_json"]),
            }
            for r in cur.fetchall()
        ]

    def list_model_attempts(self, task_id: str) -> List[Dict[str, Any]]:
        """Ordered model attempts with status and context (read-only evidence view)."""
        cur = self._conn.execute(
            "SELECT * FROM model_attempts WHERE task_id = ? ORDER BY rowid ASC",
            (task_id,),
        )
        return [
            {
                "attempt_id": r["attempt_id"],
                "actor_role": r["actor_role"],
                "status": r["status"],
                "context": json.loads(r["context_json"]),
                "response": json.loads(r["response_json"]) if r["response_json"] else None,
            }
            for r in cur.fetchall()
        ]

    def _validate_success(self, task_id):
        task = self.get_task(task_id)
        control = self.get_control(task_id)
        proof = self._conn.execute(
            "SELECT r.review_json, b.acceptance_json, b.artifacts_json FROM review_bindings b JOIN reviews r USING(review_id) "
            "WHERE b.task_id = ? AND b.attempt_id = ?", (task_id, control["active_attempt_id"]),
        ).fetchone()
        reason = None
        if not proof:
            reason = "Missing current independent review and acceptance evidence"
        else:
            review = json.loads(proof["review_json"])
            acceptance = json.loads(proof["acceptance_json"])
            artifacts = json.loads(proof["artifacts_json"])
            if review["verdict"] != "pass" or any(i["severity"] in {"p0_blocker", "p1_critical", "p2_major"} for i in review["issues"]):
                reason = "Current review does not provide an unblocked pass"
            elif len(acceptance) != len(task["acceptance_criteria"]) or any(
                item.get("criterion") != criterion or item.get("passed") is not True or not item.get("evidence")
                for criterion, item in zip(task["acceptance_criteria"], acceptance)
            ):
                reason = "Required acceptance evidence is missing or failed"
            else:
                # ARCH-05: if stage_write was used, the latest apply_staged_write
                # must have succeeded and applied the exact reviewed artifact sha256.
                execs = self.list_tool_executions(task_id)
                stages = [t for t in execs if t["tool_name"] == "stage_write" and t["status"] == "success"]
                if stages:
                    applies = [t for t in execs if t["tool_name"] == "apply_staged_write" and t["status"] == "success"]
                    reviewed_shas = {
                        v.get("sha256") for v in artifacts.values() if isinstance(v, dict) and v.get("sha256")
                    }
                    if not applies:
                        reason = "Staged write has no confirmed post-review apply record"
                    else:
                        latest_apply_sd = (applies[-1].get("result") or {}).get("structured_data") or {}
                        if latest_apply_sd.get("applied_sha256") not in reviewed_shas:
                            reason = "Applied target hash does not match the current reviewed artifact hash"
        if control["pending_json"] or task["_runtime"]["has_unconfirmed_side_effect"] or task["_runtime"]["tokens_unknown_count"]:
            reason = "Unresolved approval, side effect, or unknown model usage"
        if reason:
            raise InvalidStateTransitionError(task["status"], "succeeded", reason)

    # ------------------------------------------------------------------
    # Artifact versions (Phase 3): control-owned version + SHA-256 lineage
    # ------------------------------------------------------------------

    @atomic
    def save_artifact_version(
        self,
        task_id: str,
        *,
        version: int,
        envelope: Dict[str, Any],
        sha256: str,
        generation_attempt_id: Optional[str],
    ) -> Dict[str, Any]:
        """Persist one artifact version. The caller (control layer) computes version and sha256; model values are not trusted."""
        if not self._conn.execute("SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)).fetchone():
            raise KeyError(f"Task '{task_id}' not found")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("Artifact version must be a positive integer")
        if not isinstance(envelope.get("content"), str) or not envelope["content"]:
            raise ValueError("Artifact content must be a non-empty string")
        if not isinstance(sha256, str) or len(sha256) != 64:
            raise ValueError("Artifact sha256 must be a 64-char hex digest")
        prior = self._conn.execute(
            "SELECT 1 FROM artifact_versions WHERE task_id = ? AND version = ?", (task_id, version)
        ).fetchone()
        if prior:
            raise ValueError(f"Artifact version {version} already saved for task '{task_id}'")
        now = _utc_now_iso()
        self._conn.execute(
            """
            INSERT INTO artifact_versions (
                task_id, version, artifact_id, artifact_type, content,
                sha256, envelope_json, generation_attempt_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                version,
                envelope["artifact_id"],
                envelope["artifact_type"],
                envelope["content"],
                sha256,
                json.dumps(envelope, ensure_ascii=False),
                generation_attempt_id,
                now,
            ),
        )
        self.record_event(
            task_id=task_id,
            event_type="artifact_version_saved",
            from_state=None,
            to_state=None,
            actor_role="orchestrator",
            tokens_used=None,
            details={"version": version, "artifact_id": envelope["artifact_id"], "sha256": sha256},
        )
        return {
            "task_id": task_id,
            "version": version,
            "artifact_id": envelope["artifact_id"],
            "artifact_type": envelope["artifact_type"],
            "content": envelope["content"],
            "sha256": sha256,
            "generation_attempt_id": generation_attempt_id,
            "created_at": now,
        }

    def get_latest_artifact(self, task_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM artifact_versions WHERE task_id = ? ORDER BY version DESC LIMIT 1", (task_id,)
        ).fetchone()
        if not row:
            return None
        return {
            "task_id": row["task_id"],
            "version": row["version"],
            "artifact_id": row["artifact_id"],
            "artifact_type": row["artifact_type"],
            "content": row["content"],
            "sha256": row["sha256"],
            "generation_attempt_id": row["generation_attempt_id"],
            "created_at": row["created_at"],
        }

    def list_artifact_versions(self, task_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM artifact_versions WHERE task_id = ? ORDER BY version ASC", (task_id,)
        ).fetchall()
        return [
            {
                "task_id": r["task_id"],
                "version": r["version"],
                "artifact_id": r["artifact_id"],
                "artifact_type": r["artifact_type"],
                "content": r["content"],
                "sha256": r["sha256"],
                "generation_attempt_id": r["generation_attempt_id"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    @atomic
    def attach_artifact(self, task_id: str, mem_path: str, evidence_ref: str) -> Dict[str, Any]:
        """Register an in-memory artifact as the current task artifact without a state transition."""
        task = self.get_task(task_id)
        task_dict = {k: v for k, v in task.items() if k != "_runtime"}
        refs = list(task_dict.get("artifact_refs") or [])
        if mem_path not in refs:
            refs.append(mem_path)
        task_dict["artifact_refs"] = refs
        evidence = list(task_dict.get("tool_execution_evidence") or [])
        if evidence_ref not in evidence:
            evidence.append(evidence_ref)
        task_dict["tool_execution_evidence"] = evidence
        task_dict["updated_at"] = _utc_now_iso()
        self.validate_task_dict(task_dict)
        self._conn.execute(
            "UPDATE tasks SET task_json = ?, updated_at = ? WHERE task_id = ?",
            (json.dumps(task_dict, ensure_ascii=False), task_dict["updated_at"], task_id),
        )
        return self.get_task(task_id)

    @atomic
    def attach_evidence(self, task_id: str, evidence_refs: List[str]) -> Dict[str, Any]:
        """Replace the task's tool_execution_evidence with the given ordered list."""
        task = self.get_task(task_id)
        task_dict = {k: v for k, v in task.items() if k != "_runtime"}
        task_dict["tool_execution_evidence"] = list(evidence_refs)
        task_dict["updated_at"] = _utc_now_iso()
        self.validate_task_dict(task_dict)
        self._conn.execute(
            "UPDATE tasks SET task_json = ?, updated_at = ? WHERE task_id = ?",
            (json.dumps(task_dict, ensure_ascii=False), task_dict["updated_at"], task_id),
        )
        return self.get_task(task_id)

    # ------------------------------------------------------------------
    @atomic
    def save_code_delivery(self, task_id: str, delivery: Dict[str, Any]) -> Dict[str, Any]:
        """Persist the control-owned code delivery record (waiting/applied state).

        Stored under task metadata `_delivery`, written only by the control
        layer (prepare/complete). Never merged from payloads or model output.
        """
        task = self.get_task(task_id)
        task_dict = {k: v for k, v in task.items() if k != "_runtime"}
        meta = dict(task_dict.get("metadata") or {})
        meta["_delivery"] = delivery
        task_dict["metadata"] = meta
        task_dict["updated_at"] = _utc_now_iso()
        self.validate_task_dict(task_dict)
        self._conn.execute(
            "UPDATE tasks SET task_json = ?, updated_at = ? WHERE task_id = ?",
            (json.dumps(task_dict, ensure_ascii=False), task_dict["updated_at"], task_id),
        )
        return self.get_task(task_id)

# Memory records (Phase 3 minimal logging per memory_record.v1.json)
    # ------------------------------------------------------------------

    @atomic
    def record_memory(self, record: Dict[str, Any]) -> str:
        from jsonschema import ValidationError
        _memory_schema = _load_json_schema("memory_record.v1.json")
        now = record.get("created_at") or _utc_now_iso()
        record = dict(record)
        record.setdefault("created_at", now)
        record.setdefault("updated_at", record["created_at"])
        Draft7Validator(_memory_schema, format_checker=FormatChecker()).validate(record)
        self._conn.execute(
            """
            INSERT INTO memory_records (
                memory_id, task_id, category, verification_status, record_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record["memory_id"],
                record["task_id"],
                record["category"],
                record["verification_status"],
                json.dumps(record, ensure_ascii=False),
                now,
            ),
        )
        self.record_event(
            task_id=record["task_id"],
            event_type="memory_record_saved",
            from_state=None,
            to_state=None,
            actor_role="orchestrator",
            tokens_used=None,
            details={"memory_id": record["memory_id"], "category": record["category"],
                     "verification_status": record["verification_status"]},
        )
        return record["memory_id"]

    def list_memory_records(self, task_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM memory_records WHERE task_id = ? ORDER BY rowid ASC", (task_id,)
        ).fetchall()
        return [
            {
                "memory_id": r["memory_id"],
                "task_id": r["task_id"],
                "category": r["category"],
                "verification_status": r["verification_status"],
                "record": json.loads(r["record_json"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    @atomic
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
            try:
                waiting = (json.loads(row["task_json"]).get("metadata") or {}).get("_delivery", {})
                waiting = isinstance(waiting, dict) and waiting.get("state") == "waiting_host"
            except ValueError:
                waiting = False
            if waiting:
                # A task waiting on host delivery rests here deliberately: leave
                # owner and state untouched (no preemption, no auto-replay).
                recovery_summary.append({
                    "task_id": task_id,
                    "previous_state": status,
                    "recovered_state": status,
                    "auto_replay_allowed": False,
                    "reason": "skipped_waiting_host",
                })
                continue
            held = self._conn.execute("SELECT owner_id FROM task_control WHERE task_id = ?", (task_id,)).fetchone()
            if held and held["owner_id"]:
                # An owner-held task may be live in another process. Recover must
                # not preempt it: leave owner and state untouched for explicit
                # operator resolution (stale-owner reclamation is N7 scope).
                recovery_summary.append({
                    "task_id": task_id,
                    "previous_state": status,
                    "recovered_state": status,
                    "auto_replay_allowed": False,
                    "reason": "skipped_owner_held",
                })
                continue
            has_unconfirmed = bool(row["has_unconfirmed_side_effect"])

            # Check in-flight tool executions
            t_cur = self._conn.execute(
                "SELECT * FROM tool_executions WHERE task_id = ? AND status = 'in_flight'",
                (task_id,),
            )
            in_flight_tools = t_cur.fetchall()
            any_side_effect_in_flight = any(bool(t["has_side_effect"]) for t in in_flight_tools)
            uncertain_attempts = self._conn.execute(
                "SELECT COUNT(*) FROM model_attempts WHERE task_id = ? AND status = 'in_flight'", (task_id,)
            ).fetchone()[0]
            # Recovery never adopts or terminates a process based on a stored PID.
            self._conn.execute("UPDATE task_control SET owner_id = NULL WHERE task_id = ?", (task_id,))

            if has_unconfirmed or any_side_effect_in_flight or uncertain_attempts or status in {"planning", "reviewing", "executing", "repairing"}:
                now = _utc_now_iso()
                with self.transaction():
                    self._conn.execute("UPDATE model_attempts SET status = 'interrupted_unconfirmed' WHERE task_id = ? AND status = 'in_flight'", (task_id,))
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
