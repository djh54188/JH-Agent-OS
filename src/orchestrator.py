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
from src.router_policy import RiskPolicyEngine, RuleBasedRouter, request_fingerprint
from src.control_guards import artifact_snapshot, call_limits, input_reserve, validate_acceptance, validate_review
from src.state_machine import (
    InvalidStateTransitionError,
    is_terminal_state,
    resolve_review_transition,
)
from src.storage import StaleAttemptError, TaskStorage

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
        acceptance_evaluator: Optional[Callable] = None,
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
        self._clock = clock or time.time
        self.acceptance_evaluator = acceptance_evaluator
        self.enforce_target_files_scope = enforce_target_files_scope
        self.task_deadline_seconds = task_deadline_seconds

        self._plan_schema = _load_schema("plan.v1.json")
        self._tool_req_schema = _load_schema("tool_request.v1.json")
        self._tool_res_schema = _load_schema("tool_result.v1.json")
        self._review_schema = _load_schema("review_result.v1.json")

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
        with self.storage.transaction():
            task = self.storage.create_task(task_payload)
            self.storage.set_deadline(task_id, self._clock() + min(
                task_payload["budget"]["max_execution_time_seconds"], self.task_deadline_seconds))
        return task

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
            "pending_approval_request": json.loads(self.storage.get_control(task_id)["pending_json"] or "null"),
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
        with self.storage.transaction():
            self.storage.approve_request(task_id, request_id, request_fingerprint)
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
        deadline = self.storage.get_control(task_id)["deadline"]
        # Legacy databases without a recorded deadline require explicit reconciliation.
        return 0.0 if deadline is None else max(0.0, deadline - self._clock())

    def _check_budget_or_fail(self, task_id: str, *, before_call: bool = True) -> Optional[Dict[str, Any]]:
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

        if runtime["tokens_unknown_count"]:
            return self.storage.transition_task_state(
                task_id, "needs_attention", reason="unknown_usage_blocks_further_calls",
                error_info="Model token usage is unknown; cannot assume zero cost or continue automatically")

        if remaining_sec <= 0:
            return self.storage.transition_task_state(
                task_id,
                "failed",
                actor_role="orchestrator",
                reason="task_timeout_budget_exhausted",
                error_info="Task execution time budget exhausted (timeout)",
            )
        if turns_used > max_turns or (before_call and turns_used == max_turns):
            return self.storage.transition_task_state(
                task_id,
                "failed",
                actor_role="orchestrator",
                reason="max_turns_budget_exhausted",
                error_info=f"Task turn budget exhausted ({turns_used}/{max_turns})",
            )
        if tokens_used is not None and (tokens_used > max_tokens or (before_call and tokens_used == max_tokens)):
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
        owner_id = uuid.uuid4().hex
        if not self.storage.claim_task(task_id, owner_id):
            return self.storage.get_task(task_id)
        try:
            return self._run_task(task_id)
        except StaleAttemptError as exc:
            task = self.storage.get_task(task_id)
            self.storage.record_event(task_id=task_id, event_type="stale_attempt_rejected",
                                      from_state=task["status"], to_state=task["status"],
                                      actor_role="orchestrator", details={"reason": str(exc)})
            return task
        finally:
            self.storage.release_task(task_id, owner_id)

    def _invoke_model(self, task_id, role, adapter, messages, schema, **kwargs):
        task = self.storage.get_task(task_id)
        reserved = input_reserve(messages, schema)
        limits = call_limits(adapter, task["budget"]["max_tokens"] - (task["_runtime"]["tokens_used"] or 0),
                             self.get_remaining_budget_seconds(task_id), reserved)
        attempt_id = self.storage.begin_attempt(task_id, role, {
            "input_reservation": reserved, "input_reservation_is_estimate": True,
            "deadline": self.storage.get_control(task_id)["deadline"], **limits, **kwargs})
        try:
            response = adapter.invoke_structured(messages, schema, task_id=task_id, **limits, **kwargs)
        except AdapterError:
            self.storage.complete_attempt(task_id, attempt_id, {"error": "adapter_failed"}, None)
            raise
        usage = response.total_tokens
        if usage is not None and (isinstance(usage, bool) or not isinstance(usage, int) or usage < 0):
            self.storage.complete_attempt(task_id, attempt_id, {"error": "invalid_usage"}, None)
            raise SchemaValidationError("Provider returned invalid token usage; budget cannot be trusted")
        self.storage.complete_attempt(task_id, attempt_id, response.parsed_json, response.total_tokens)
        return response, attempt_id

    def _run_task(self, task_id: str) -> Dict[str, Any]:
        """Drive task through its lifecycle until terminal state or approval/attention gate."""
        task = self.storage.get_task(task_id)

        if is_terminal_state(task["status"]) or task["status"] == "needs_attention":
            return task
        budget_failed = self._check_budget_or_fail(task_id)
        if budget_failed is not None:
            return budget_failed
        if self.storage.has_uncertain_attempt(task_id):
            return self.storage.transition_task_state(task_id, "needs_attention", reason="uncertain_attempt_no_auto_replay",
                                                       error_info="Previous model attempt has no confirmed result; reconcile before retry")
        if task["status"] == "awaiting_approval" and self.storage.get_control(task_id)["pending_json"]:
            return task

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
                plan_resp, _ = self._invoke_model(task_id, "worker", worker, plan_messages, self._plan_schema)
                Draft7Validator(self._plan_schema, format_checker=FormatChecker()).validate(plan_resp.parsed_json)
                if plan_resp.parsed_json["task_id"] != task_id:
                    raise SchemaValidationError("Plan task_id correlation mismatch")
            except CredentialMissingError as exc:
                return self.storage.transition_task_state(
                    task_id,
                    "failed",
                    actor_role="worker",
                    reason="worker_credential_missing",
                    error_info=str(exc),
                )
            except (AdapterError, SchemaValidationError, ValidationError) as exc:
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
                current = self.storage.get_task(task_id)
                if current["status"] != "executing":
                    self.storage.record_event(task_id=task_id, event_type="late_tool_request_rejected",
                                              from_state=current["status"], to_state=current["status"], actor_role="orchestrator")
                    return current
                budget_failed = self._check_budget_or_fail(task_id, before_call=False)
                if budget_failed is not None:
                    return budget_failed
                # Validate ToolRequest schema
                try:
                    Draft7Validator(self._tool_req_schema, format_checker=FormatChecker()).validate(tool_req)
                    if tool_req["task_id"] != task_id:
                        raise ValidationError("ToolRequest task_id correlation mismatch")
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
                    approved_fingerprints=json.loads(self.storage.get_control(task_id)["approvals_json"]),
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
                    with self.storage.transaction():
                        self.storage.hold_request(task_id, tool_req)
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
                try:
                    started = self.storage.record_tool_start(tool_req, has_side_effect=has_side_effect)
                except ValueError as exc:
                    return self.storage.transition_task_state(task_id, "needs_attention", error_info=str(exc))
                try:
                    if started:
                        tool_res = self.tool_executor.execute(tool_req)
                    else:
                        if self.storage.get_task(task_id)["status"] != "executing":
                            return self.storage.get_task(task_id)
                        tool_res = next(item["result"] for item in self.storage.list_tool_executions(task_id)
                                        if item["request_id"] == tool_req["request_id"])
                except SimulatedInterruptionError:
                    # Do NOT mark tool completed; re-raise to simulate unexpected process termination
                    raise
                except Exception as exc:
                    return self.storage.transition_task_state(
                        task_id, "needs_attention", reason="tool_result_uncertain",
                        error_info=f"Tool invocation failed before confirmation: {type(exc).__name__}")

                try:
                    Draft7Validator(self._tool_res_schema, format_checker=FormatChecker()).validate(tool_res)
                    is_clean = tool_res["status"] == "success"
                    self.storage.record_tool_completion(tool_res, confirmed_clean=is_clean)
                except (ValidationError, ValueError) as exc:
                    return self.storage.transition_task_state(task_id, "needs_attention", error_info=str(exc))

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

            if self.storage.get_task(task_id)["status"] != "executing":
                return self.storage.get_task(task_id)

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
                if reviewer is worker or reviewer.model_id == worker.model_id:
                    raise SchemaValidationError("Reviewer must be independent from Worker")
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
                snapshots = artifact_snapshot(self.tool_executor, task.get("artifact_refs", []))
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
                    "artifacts": snapshots,
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
                review_resp, review_attempt = self._invoke_model(
                    task_id, "reviewer", reviewer, review_messages, self._review_schema,
                    target_artifact_path=target_artifact,
                    review_metadata=review_metadata,
                    artifact_hashes={p: a["sha256"] for p, a in snapshots.items()},
                )
                review_json = review_resp.parsed_json
                Draft7Validator(self._review_schema, format_checker=FormatChecker()).validate(review_json)
                validate_review(review_json, review_metadata, snapshots,
                                artifact_snapshot(self.tool_executor, task.get("artifact_refs", [])))
                budget_failed = self._check_budget_or_fail(task_id, before_call=False)
                if budget_failed is not None:
                    return budget_failed
                acceptance = []
                if review_json["verdict"] == "pass":
                    if self.acceptance_evaluator is not None:
                        acceptance = self.acceptance_evaluator(task, self.storage.list_tool_executions(task_id))
                    validate_acceptance(task, acceptance, task["tool_execution_evidence"])
                    validate_review(review_json, review_metadata, snapshots,
                                    artifact_snapshot(self.tool_executor, task.get("artifact_refs", [])))
                self.storage.bind_review(review_json, review_attempt, snapshots, acceptance)
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
                try:
                    return self.storage.transition_task_state(
                        task_id,
                        "succeeded",
                        actor_role="reviewer",
                        reason=transition_reason,
                        review_verdict="pass",
                    )
                except InvalidStateTransitionError as exc:
                    current = self.storage.get_task(task_id)
                    if is_terminal_state(current["status"]):
                        return current
                    return self.storage.transition_task_state(
                        task_id, "needs_attention", reason="success_evidence_gate_rejected",
                        error_info=f"Success gate blocked: {exc.reason}")

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
