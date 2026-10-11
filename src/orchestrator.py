"""
JH-Agent OS Phase 2 Task Orchestrator v1.0.0
Coordinates the 11-state task lifecycle, RuleBasedRouter, RiskPolicyEngine,
SQLite TaskStorage, Worker/Reviewer ModelAdapters, and Controlled Tool Execution.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Callable, Dict, List, Optional, Set
import uuid

from jsonschema import Draft7Validator, FormatChecker, ValidationError
from src.adapters import (
    AdapterError,
    CredentialMissingError,
    ModelAdapter,
    SchemaValidationError,
)
from src.codex_managed import ManagedCodexAdapter, ManagedCodexConfig
from src.mock_components import (
    MockToolExecutor,
    ScriptedReviewerAdapter,
    ScriptedWorkerAdapter,
    SimulatedInterruptionError,
)
from src.model_config import has_credential, load_model_adapter
from src.router_policy import RiskPolicyEngine, RuleBasedRouter
from src.state_machine import (
    InvalidStateTransitionError,
    is_terminal_state,
    resolve_review_transition,
)
from src.storage import TaskStorage

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = PROJECT_ROOT / "docs" / "schemas"

# Approved Phase 2 Operational Constants
DEFAULT_CODEX_CALL_TIMEOUT_SECONDS = 300.0
DEFAULT_TASK_DEADLINE_SECONDS = 600.0
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_NO_EVENT_WARNING_SECONDS = 180.0
DEFAULT_CODEX_CONCURRENCY = 1
DEFAULT_AUTO_RESTART_RETRIES = 0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_schema(name: str) -> Dict[str, Any]:
    with (SCHEMAS_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


class Orchestrator:
    """
    Central control layer for JH-Agent OS.
    Separates intelligence suggestions (Worker/Reviewer) from execution authority (Orchestrator/Policy).
    """

    def __init__(
        self,
        *,
        storage: Optional[TaskStorage] = None,
        workspace_root: str | Path = PROJECT_ROOT,
        worker_adapter: Optional[ModelAdapter] = None,
        reviewer_adapter: Optional[ModelAdapter] = None,
        tool_executor: Optional[Any] = None,
        codex_adapter: Optional[ManagedCodexAdapter] = None,
        clock: Optional[Callable[[], float]] = None,
        enforce_target_files_scope: bool = False,
        task_deadline_seconds: float = DEFAULT_TASK_DEADLINE_SECONDS,
    ):
        self.workspace_root = str(Path(workspace_root).resolve())
        self.storage = storage or TaskStorage(":memory:")
        self.policy_engine = RiskPolicyEngine(self.workspace_root)
        self.router = RuleBasedRouter(
            self.policy_engine,
            has_gemini_credential_fn=lambda: has_credential("GEMINI_API_KEY"),
        )
        self._worker_adapter = worker_adapter
        self._reviewer_adapter = reviewer_adapter
        self.tool_executor = tool_executor or MockToolExecutor()
        self.codex_adapter = codex_adapter or ManagedCodexAdapter(
            ManagedCodexConfig(
                workspace_root=self.workspace_root,
                call_timeout_seconds=DEFAULT_CODEX_CALL_TIMEOUT_SECONDS,
                task_deadline_seconds=task_deadline_seconds,
                poll_interval_seconds=DEFAULT_POLL_INTERVAL_SECONDS,
                no_event_warning_seconds=DEFAULT_NO_EVENT_WARNING_SECONDS,
                max_concurrency=DEFAULT_CODEX_CONCURRENCY,
                auto_restart_retries=DEFAULT_AUTO_RESTART_RETRIES,
            )
        )
        self._clock = clock or time.monotonic
        self.enforce_target_files_scope = enforce_target_files_scope
        self.task_deadline_seconds = task_deadline_seconds

        self._plan_schema = _load_schema("plan.v1.json")
        self._tool_req_schema = _load_schema("tool_request.v1.json")
        self._tool_res_schema = _load_schema("tool_result.v1.json")
        self._review_schema = _load_schema("review_result.v1.json")

        # Track approved Level 2 request IDs and task start clocks
        self._approved_requests: Dict[str, Set[str]] = {}
        self._pending_approval_requests: Dict[str, Dict[str, Any]] = {}
        self._task_start_clocks: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Entrypoints: Submit, Status Query, Cancel, Approve, Recover
    # ------------------------------------------------------------------

    def submit_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and register a new task in 'created' state."""
        try:
            self.storage.validate_task_dict(task_payload)
        except ValidationError as exc:
            raise SchemaValidationError(f"Invalid Task payload rejected: {exc.message}") from None

        task_id = task_payload["task_id"]
        self._approved_requests.setdefault(task_id, set())
        self._task_start_clocks[task_id] = self._clock()
        return self.storage.create_task(task_payload)

    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """Query current task snapshot, runtime counters, and remaining budget."""
        task = self.storage.get_task(task_id)
        remaining_sec = self.get_remaining_budget_seconds(task_id, task)
        runtime = task.get("_runtime") or {}
        return {
            "task_id": task_id,
            "status": task["status"],
            "title": task["title"],
            "priority": task["priority"],
            "assigned_worker": task.get("assigned_worker"),
            "assigned_reviewer": task.get("assigned_reviewer"),
            "review_verdict": task.get("review_verdict"),
            "error_info": task.get("error_info"),
            "turns_used": runtime.get("turns_used", 0),
            "tokens_used": runtime.get("tokens_used"),  # None if unknown
            "tokens_unknown_count": runtime.get("tokens_unknown_count", 0),
            "repair_count": runtime.get("repair_count", 0),
            "has_unconfirmed_side_effect": runtime.get("has_unconfirmed_side_effect", False),
            "remaining_budget_seconds": remaining_sec,
            "pending_approval_request": self._pending_approval_requests.get(task_id),
            "artifact_refs": task.get("artifact_refs", []),
            "tool_execution_evidence": task.get("tool_execution_evidence", []),
            "updated_at": task.get("updated_at"),
        }

    def list_task_events(self, task_id: str) -> List[Dict[str, Any]]:
        return self.storage.list_events(task_id)

    def cancel_task(self, task_id: str, reason: str = "Cancelled by user") -> Dict[str, Any]:
        """Cancel a non-terminal task."""
        return self.storage.transition_task_state(
            task_id,
            "cancelled",
            actor_role="user",
            reason=reason,
            error_info=reason,
        )

    def approve_task_request(self, task_id: str, request_id: str, approver: str = "meta_planner") -> Dict[str, Any]:
        """Grant human approval for a held Level 2 request and resume execution."""
        task = self.storage.get_task(task_id)
        if task["status"] != "awaiting_approval":
            raise InvalidStateTransitionError(
                task["status"],
                "executing",
                "Task is not currently in 'awaiting_approval' state",
            )
        self._approved_requests.setdefault(task_id, set()).add(request_id)
        self._pending_approval_requests.pop(task_id, None)
        self.storage.record_event(
            task_id=task_id,
            event_type="human_approval_granted",
            from_state="awaiting_approval",
            to_state="awaiting_approval",
            actor_role=approver,
            tokens_used=None,
            details={"approved_request_id": request_id},
        )
        return self.run_task(task_id)

    def reject_task_request(self, task_id: str, request_id: str, reason: str = "Rejected by human") -> Dict[str, Any]:
        """Reject a held Level 2 request and transition task to 'failed'."""
        task = self.storage.get_task(task_id)
        if task["status"] != "awaiting_approval":
            raise InvalidStateTransitionError(
                task["status"],
                "failed",
                "Task is not currently in 'awaiting_approval' state",
            )
        self._pending_approval_requests.pop(task_id, None)
        return self.storage.transition_task_state(
            task_id,
            "failed",
            actor_role="meta_planner",
            reason=f"Approval rejected for {request_id}: {reason}",
            error_info=f"Human rejected approval for {request_id}: {reason}",
        )

    def recover_interrupted_tasks(self) -> List[Dict[str, Any]]:
        """Recover tasks from SQLite after restart without blindly replaying unconfirmed side effects."""
        return self.storage.recover_interrupted_tasks()

    # ------------------------------------------------------------------
    # Budget & Adapter Resolution Helpers
    # ------------------------------------------------------------------

    def get_remaining_budget_seconds(self, task_id: str, task: Optional[Dict[str, Any]] = None) -> float:
        task_obj = task or self.storage.get_task(task_id)
        budget = task_obj.get("budget") or {}
        task_max_sec = float(budget.get("max_execution_time_seconds", self.task_deadline_seconds))
        effective_deadline = min(task_max_sec, float(self.task_deadline_seconds))
        start_clock = self._task_start_clocks.get(task_id)
        if start_clock is None:
            return effective_deadline
        elapsed = max(0.0, self._clock() - start_clock)
        return max(0.0, effective_deadline - elapsed)

    def _check_budget_or_fail(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Check turns, tokens, and time budget. If exhausted, transition task to 'failed'."""
        task = self.storage.get_task(task_id)
        if is_terminal_state(task["status"]):
            return task

        budget = task["budget"]
        runtime = task["_runtime"]
        max_turns = budget["max_turns"]
        max_tokens = budget["max_tokens"]
        turns_used = runtime["turns_used"]
        tokens_used = runtime["tokens_used"]
        remaining_sec = self.get_remaining_budget_seconds(task_id, task)

        if remaining_sec <= 0:
            return self.storage.transition_task_state(
                task_id,
                "failed",
                actor_role="orchestrator",
                reason="task_timeout_budget_exhausted",
                error_info="Task execution time budget exhausted (timeout)",
            )
        if turns_used >= max_turns:
            return self.storage.transition_task_state(
                task_id,
                "failed",
                actor_role="orchestrator",
                reason="max_turns_budget_exhausted",
                error_info=f"Task turn budget exhausted ({turns_used}/{max_turns})",
            )
        if tokens_used is not None and tokens_used >= max_tokens:
            return self.storage.transition_task_state(
                task_id,
                "failed",
                actor_role="orchestrator",
                reason="max_tokens_budget_exhausted",
                error_info=f"Task token budget exhausted ({tokens_used}/{max_tokens})",
            )
        return None

    def _resolve_worker(self, routing_decision: Any) -> ModelAdapter:
        if self._worker_adapter is not None:
            return self._worker_adapter
        if routing_decision.use_mock_worker:
            return ScriptedWorkerAdapter()
        return load_model_adapter("worker", profile_override=routing_decision.worker_profile)

    def _resolve_reviewer(self, routing_decision: Any) -> ModelAdapter:
        if self._reviewer_adapter is not None:
            return self._reviewer_adapter
        return load_model_adapter("reviewer", profile_override=routing_decision.reviewer_profile)

    # ------------------------------------------------------------------
    # Core Task Execution Loop
    # ------------------------------------------------------------------

    def run_task(self, task_id: str) -> Dict[str, Any]:
        """Drive task through its lifecycle until terminal state or approval/attention gate."""
        self._task_start_clocks.setdefault(task_id, self._clock())
        task = self.storage.get_task(task_id)

        if is_terminal_state(task["status"]) or task["status"] == "needs_attention":
            return task

        budget_failed = self._check_budget_or_fail(task_id)
        if budget_failed is not None:
            return budget_failed

        # 1. Classification & Routing
        routing = self.router.route_task(task)
        if task["status"] == "created":
            task = self.storage.transition_task_state(
                task_id,
                "classified",
                actor_role="orchestrator",
                reason=routing.routing_basis,
                assigned_worker=routing.worker_profile,
                assigned_reviewer=routing.reviewer_profile,
                details={
                    "planning_depth": routing.planning_depth,
                    "max_repairs": routing.max_repairs,
                    "use_mock_worker": routing.use_mock_worker,
                    "blocked_by_level3": routing.blocked_by_level3,
                },
            )

        if routing.blocked_by_level3:
            return self.storage.transition_task_state(
                task_id,
                "failed",
                actor_role="orchestrator",
                reason="level3_danger_override",
                error_info=f"Blocked by Level 3 Risk Policy: {routing.risk_basis}",
                details={"violated_level": 3, "routing_basis": routing.routing_basis},
            )

        # 2. Planning Phase
        worker = self._resolve_worker(routing)
        if task["status"] == "classified":
            task = self.storage.transition_task_state(
                task_id,
                "planning",
                actor_role="orchestrator",
                reason="Starting worker planning phase",
            )

        if task["status"] == "planning":
            budget_failed = self._check_budget_or_fail(task_id)
            if budget_failed is not None:
                return budget_failed

            try:
                remaining_sec = self.get_remaining_budget_seconds(task_id, task)
                plan_messages = [
                    {"role": "system", "content": "Generate a valid execution Plan for the task."},
                    {"role": "user", "content": json.dumps({"title": task["title"], "description": task["description"]})},
                ]
                plan_resp = worker.invoke_structured(
                    plan_messages,
                    self._plan_schema,
                    task_id=task_id,
                    timeout=remaining_sec,
                )
                self.storage.record_usage(
                    task_id,
                    actor_role="worker",
                    tokens_used=plan_resp.total_tokens,
                    increment_turn=True,
                    details={"phase": "planning", "model_id": plan_resp.model_id, "is_mock": plan_resp.is_mock},
                )
            except CredentialMissingError as exc:
                return self.storage.transition_task_state(
                    task_id,
                    "failed",
                    actor_role="worker",
                    reason="worker_credential_missing",
                    error_info=str(exc),
                )
            except (AdapterError, SchemaValidationError) as exc:
                err_str = str(exc)
                reason_code = "worker_timeout" if "timed out" in err_str.lower() else "worker_planning_failed"
                return self.storage.transition_task_state(
                    task_id,
                    "failed",
                    actor_role="worker",
                    reason=reason_code,
                    error_info=err_str,
                )

            task = self.storage.transition_task_state(
                task_id,
                "executing",
                actor_role="orchestrator",
                reason="Plan validated; entering controlled execution",
            )

        # If resuming from awaiting_approval, transition to executing
        if task["status"] == "awaiting_approval":
            task = self.storage.transition_task_state(
                task_id,
                "executing",
                actor_role="orchestrator",
                reason="Resuming execution after human approval",
            )

        # 3. Execution & Bounded Repair Loop
        max_repairs = routing.max_repairs
        while task["status"] == "executing":
            budget_failed = self._check_budget_or_fail(task_id)
            if budget_failed is not None:
                return budget_failed

            repair_idx = task["_runtime"]["repair_count"]
            if hasattr(worker, "propose_tool_requests"):
                proposed_requests = worker.propose_tool_requests(task, turn_index=repair_idx)
            else:
                proposed_requests = []

            authorized_files: Optional[Set[str]] = None
            if self.enforce_target_files_scope:
                t_files = (task.get("context") or {}).get("target_files") or []
                authorized_files = set(t_files)

            produced_artifacts: List[str] = []
            execution_evidence: List[str] = []

            for tool_req in proposed_requests:
                # Validate ToolRequest schema
                try:
                    Draft7Validator(self._tool_req_schema, format_checker=FormatChecker()).validate(tool_req)
                except ValidationError as exc:
                    return self.storage.transition_task_state(
                        task_id,
                        "failed",
                        actor_role="orchestrator",
                        reason="invalid_tool_request_schema",
                        error_info=f"Invalid ToolRequest rejected: {exc.message}",
                    )

                # Per-call dynamic parameter & permission check
                decision = self.policy_engine.evaluate_tool_request(
                    tool_req,
                    approved_request_ids=self._approved_requests.get(task_id, set()),
                    authorized_files=authorized_files,
                )

                if decision.action == "deny_and_fail":
                    denied_res = {
                        "schema_version": "1.0.0",
                        "result_id": f"tres_{uuid.uuid4().hex[:12]}",
                        "request_id": tool_req["request_id"],
                        "task_id": task_id,
                        "tool_name": tool_req["tool_name"],
                        "status": "denied_by_policy",
                        "stdout": None,
                        "stderr": decision.reason,
                        "structured_data": {"violated_rule_id": decision.violated_rule_id, "level": decision.level},
                        "execution_time_ms": 0,
                        "error_message": decision.reason,
                        "created_at": _utc_now_iso(),
                    }
                    self.storage.record_tool_start(tool_req, has_side_effect=False)
                    self.storage.record_tool_completion(denied_res, confirmed_clean=True)
                    self.storage.record_event(
                        task_id=task_id,
                        event_type="security_policy_blocked_level3",
                        from_state="executing",
                        to_state="failed",
                        actor_role="orchestrator",
                        tokens_used=None,
                        details={
                            "request_id": tool_req["request_id"],
                            "tool_name": tool_req["tool_name"],
                            "violated_rule_id": decision.violated_rule_id,
                            "reason": decision.reason,
                        },
                    )
                    return self.storage.transition_task_state(
                        task_id,
                        "failed",
                        actor_role="orchestrator",
                        reason=f"Denied by Level 3 policy ({decision.violated_rule_id})",
                        error_info=decision.reason,
                    )

                if decision.action == "hold_for_approval":
                    self._pending_approval_requests[task_id] = {
                        "request_id": tool_req["request_id"],
                        "tool_name": tool_req["tool_name"],
                        "parameters": tool_req.get("parameters"),
                        "violated_rule_id": decision.violated_rule_id,
                        "reason": decision.reason,
                    }
                    return self.storage.transition_task_state(
                        task_id,
                        "awaiting_approval",
                        actor_role="orchestrator",
                        reason=decision.reason,
                        details={
                            "pending_request_id": tool_req["request_id"],
                            "violated_rule_id": decision.violated_rule_id,
                        },
                    )

                # Execute allowed Level 0 / Level 1 / Approved Level 2 tool
                has_side_effect = decision.level >= 1
                self.storage.record_tool_start(tool_req, has_side_effect=has_side_effect)
                try:
                    tool_res = self.tool_executor.execute(tool_req)
                except SimulatedInterruptionError:
                    # Do NOT mark tool completed; re-raise to simulate unexpected process termination
                    raise

                Draft7Validator(self._tool_res_schema, format_checker=FormatChecker()).validate(tool_res)
                is_clean = tool_res["status"] == "success"
                self.storage.record_tool_completion(tool_res, confirmed_clean=is_clean)

                if tool_res["status"] != "success":
                    return self.storage.transition_task_state(
                        task_id,
                        "failed",
                        actor_role="orchestrator",
                        reason=f"tool_execution_{tool_res['status']}",
                        error_info=tool_res.get("error_message") or f"Tool {tool_req['tool_name']} {tool_res['status']}",
                    )

                path_param = (tool_req.get("parameters") or {}).get("path")
                if isinstance(path_param, str) and path_param:
                    produced_artifacts.append(path_param)
                execution_evidence.append(f"{tool_req['request_id']}:{tool_req['tool_name']}:success")

            # 4. Transition to Independent Review (Worker completion != Task success)
            task = self.storage.transition_task_state(
                task_id,
                "reviewing",
                actor_role="orchestrator",
                reason="Worker execution finished; entering independent Reviewer verification",
                artifact_refs=produced_artifacts,
                tool_evidence=execution_evidence,
            )

            budget_failed = self._check_budget_or_fail(task_id)
            if budget_failed is not None:
                return budget_failed

            try:
                reviewer = self._resolve_reviewer(routing)
                remaining_sec = self.get_remaining_budget_seconds(task_id, task)
                if remaining_sec <= 0:
                    return self.storage.transition_task_state(
                        task_id,
                        "failed",
                        actor_role="orchestrator",
                        reason="task_timeout_before_review",
                        error_info="Task deadline exhausted before Reviewer invocation",
                    )

                target_artifact = (task.get("artifact_refs") or ["workspace"])[0]
                review_metadata = {
                    "schema_version": "1.0.0",
                    "review_id": f"rev_{uuid.uuid4().hex[:12]}",
                    "task_id": task_id,
                    "target_artifact_type": "code",
                    "target_artifact_path": target_artifact,
                    "reviewer_role": getattr(reviewer, "model_id", "reviewer"),
                    "created_at": _utc_now_iso(),
                }
                independent_review_context = {
                    "metadata": review_metadata,
                    "task_id": task_id,
                    "title": task["title"],
                    "description": task["description"],
                    "acceptance_criteria": task["acceptance_criteria"],
                    "artifact_refs": task.get("artifact_refs", []),
                    "tool_execution_evidence": task.get("tool_execution_evidence", []),
                    "note": "Independent review context; worker summary is excluded from primary evidence.",
                }
                review_messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are an independent code reviewer for JH-Agent OS. "
                            "Review only the supplied artifact and tool_execution_evidence against acceptance_criteria. "
                            "Return only the raw ReviewResult JSON object without markdown fences or backticks. "
                            "Use verdict pass with empty issues if all acceptance_criteria are satisfied; "
                            "otherwise use revise or reject and cite concrete evidence in issue evidence. "
                            "overall_score is 0-100. Preserve the supplied metadata fields exactly. "
                            "Do not use tools or invent executed test results."
                        ),
                    },
                    {"role": "user", "content": json.dumps(independent_review_context, ensure_ascii=False)},
                ]
                review_resp = reviewer.invoke_structured(
                    review_messages,
                    self._review_schema,
                    task_id=task_id,
                    target_artifact_path=target_artifact,
                    timeout=remaining_sec,
                    max_output_tokens=8192,
                )
                self.storage.record_usage(
                    task_id,
                    actor_role="reviewer",
                    tokens_used=review_resp.total_tokens,
                    increment_turn=True,
                    details={"phase": "reviewing", "model_id": review_resp.model_id, "is_mock": review_resp.is_mock},
                )
                review_json = review_resp.parsed_json
                Draft7Validator(self._review_schema, format_checker=FormatChecker()).validate(review_json)
                self.storage.record_review(review_json)
            except (CredentialMissingError, AdapterError, SchemaValidationError, ValidationError) as exc:
                # Iron Rule: Reviewer unavailability, timeout, or invalid JSON MUST NEVER auto-pass!
                return self.storage.transition_task_state(
                    task_id,
                    "needs_attention",
                    actor_role="reviewer",
                    reason="reviewer_unavailable_or_invalid_fail_closed",
                    error_info=f"Reviewer check failed (fail-closed, never auto-pass): {exc}",
                )

            verdict = review_json.get("verdict")
            current_repairs = self.storage.get_task(task_id)["_runtime"]["repair_count"]
            next_state, transition_reason = resolve_review_transition(
                verdict,
                repair_count=current_repairs,
                max_repairs=max_repairs,
            )

            if next_state == "succeeded":
                return self.storage.transition_task_state(
                    task_id,
                    "succeeded",
                    actor_role="reviewer",
                    reason=transition_reason,
                    review_verdict="pass",
                )

            if next_state == "repairing":
                task = self.storage.transition_task_state(
                    task_id,
                    "repairing",
                    actor_role="reviewer",
                    reason=transition_reason,
                    review_verdict="revise",
                    increment_repair=True,
                    details={"issues": review_json.get("issues", [])},
                )
                budget_failed = self._check_budget_or_fail(task_id)
                if budget_failed is not None:
                    return budget_failed
                task = self.storage.transition_task_state(
                    task_id,
                    "executing",
                    actor_role="orchestrator",
                    reason="Re-entering execution from bounded repair loop",
                )
                continue

            # 'failed' or 'needs_attention'
            return self.storage.transition_task_state(
                task_id,
                next_state,
                actor_role="reviewer",
                reason=transition_reason,
                review_verdict=verdict,
                error_info=review_json.get("summary") or transition_reason,
            )

        return self.storage.get_task(task_id)
