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
from src.artifacts import ArtifactStore, content_sha256, is_mem_artifact_path, mem_artifact_path
from src.code_delivery import (
    CodeDeliveryRefused,
    delivery_state,
    is_code_delivery_task,
    materialized_post_apply_command,
    parse_fixture_patch,
    resolve_target_path,
    sealed_fixture_bytes,
    template_for_task,
    validate_apply_receipt,
    verify_production_history_clean,
    is_clean_pass_review,
    sha256_hex,
    fixture_host_evidence_matches,
    workspace_hashes,
)
from src.codex_managed import ManagedCodexAdapter, ManagedCodexConfig
from src.file_tools import FileToolExecutor, PathBoundaryError
from src.memory import record_task_outcome_memory
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


class ApplyBlockedError(Exception):
    """A post-review staged-write apply was refused or left uncertain."""


class _DispatchSnapshotView:
    """Snapshot view that dispatches mem:// artifacts and real tool-produced
    files, so control-owned acceptance evaluators can read either kind."""

    def __init__(self, snapshot_fn):
        self._snapshot_fn = snapshot_fn

    def snapshot_artifacts(self, paths):
        return self._snapshot_fn(paths)

    def get_latest(self, task_id):
        return None  # file tasks have no mem lineage; evaluators use snapshot_artifacts


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
        artifact_store: Optional[ArtifactStore] = None,
        require_real_responses: bool = False,
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
        self._require_real_responses = require_real_responses

        self._plan_schema = _load_schema("plan.v1.json")
        self._tool_req_schema = _load_schema("tool_request.v1.json")
        self._tool_res_schema = _load_schema("tool_result.v1.json")
        self._review_schema = _load_schema("review_result.v1.json")
        self._artifact_schema = _load_schema("artifact.v1.json")
        self._tool_plan_schema = _load_schema("tool_plan.v1.json")
        self.artifact_store = artifact_store or ArtifactStore(self.storage)

    # ------------------------------------------------------------------
    # Entrypoints: Submit, Status Query, Cancel, Approve, Recover
    # ------------------------------------------------------------------

    def submit_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and register a new task in 'created' state."""
        try:
            self.storage.validate_task_dict(task_payload)
        except ValidationError as exc:
            raise SchemaValidationError(f"Invalid Task payload rejected: {exc.message}") from None

        if "_delivery" in (task_payload.get("metadata") or {}):
            raise SchemaValidationError("Task payload cannot supply control-owned _delivery metadata")
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
        task = self.storage.transition_task_state(
            task_id,
            "failed",
            actor_role="meta_planner",
            reason=f"Approval rejected for {request_id}: {reason}",
            error_info=f"Human rejected approval for {request_id}: {reason}",
        )
        record_task_outcome_memory(self.storage, task)
        return task

    # ------------------------------------------------------------------
    # JH-006 two-step code delivery (fixed fixture template only)
    # ------------------------------------------------------------------

    def _reject_delivery_blockers(self, task_id):
        task = self.storage.get_task(task_id)
        if self.storage.get_control(task_id).get("pending_json"):
            raise CodeDeliveryRefused("task has a pending approval; resolve it first")
        uncertain = {"in_flight", "interrupted_unconfirmed", "uncertain"}
        if any(a.get("status") in uncertain for a in self.storage.list_model_attempts(task_id)):
            raise CodeDeliveryRefused("unconfirmed model attempts outstanding")
        if any(t.get("status") in uncertain for t in self.storage.list_tool_executions(task_id)):
            raise CodeDeliveryRefused("unconfirmed tool executions outstanding")
        if task["_runtime"].get("has_unconfirmed_side_effect"):
            raise CodeDeliveryRefused("unconfirmed side effect outstanding")

    def prepare_code_delivery(self, task_id: str, artifact_version: int) -> Dict[str, Any]:
        owner_id = uuid.uuid4().hex
        if not self.storage.claim_task(task_id, owner_id):
            raise CodeDeliveryRefused("task owner is held by another execution")
        try:
            with self.storage.transaction():
                return self._prepare_code_delivery_locked(task_id, artifact_version)
        finally:
            self.storage.release_task(task_id, owner_id)

    def _prepare_code_delivery_locked(self, task_id: str, artifact_version: int) -> Dict[str, Any]:
        """Validate the current reviewed patch and mark the task waiting-host.

        Never transitions to succeeded. Refusals raise CodeDeliveryRefused with
        no state change. Idempotent for the same artifact version.
        """
        task = self.storage.get_task(task_id)
        if task["status"] != "reviewing":
            raise CodeDeliveryRefused(f"prepare requires a reviewing task, got '{task['status']}'")
        stopped = self._check_budget_or_fail(task_id, before_call=False)
        if stopped is not None:
            return stopped
        self._reject_delivery_blockers(task_id)
        tpl = template_for_task(task)
        stamped = (task.get("metadata") or {}).get("_trusted") or {}
        if stamped.get("template_id") != tpl["template_id"]:
            raise CodeDeliveryRefused("task lacks a matching trusted template stamp")
        if stamped.get("source") == "production":
            taint = verify_production_history_clean(self.storage, task_id)
            if taint is not None:
                raise CodeDeliveryRefused(f"tainted production history: {taint}")
        elif stamped.get("source") != "fixture":
            raise CodeDeliveryRefused(f"unknown task source {stamped.get('source')!r}")
        if list(task.get("acceptance_criteria") or []) != list(tpl["criteria"]):
            raise CodeDeliveryRefused("task acceptance criteria do not match the fixed code template")
        if list(((task.get("context") or {}).get("target_files") or [])) != [tpl["authorized_target"]]:
            raise CodeDeliveryRefused("task target_files do not match the single authorized code target")
        if isinstance(artifact_version, bool) or not isinstance(artifact_version, int):
            raise CodeDeliveryRefused(f"artifact version must be an integer, got {artifact_version!r}")
        existing = (task.get("metadata") or {}).get("_delivery")
        latest = self.artifact_store.get_latest(task_id)
        if latest is None or latest["version"] != artifact_version:
            raise CodeDeliveryRefused(f"artifact version {artifact_version} is not current")
        attempts = [a for a in self.storage.list_model_attempts(task_id)
                    if a.get("actor_role") == "reviewer" and a.get("status") == "completed"]
        if not attempts:
            raise CodeDeliveryRefused("no completed review attempt to prepare from")
        review_attempt = attempts[-1]
        review = review_attempt.get("response") or {}
        if not is_clean_pass_review(review):
            raise CodeDeliveryRefused("no passing review without blocking issues for the current artifact")
        context = review_attempt.get("context")
        hashes = context.get("artifact_hashes") if isinstance(context, dict) else None
        if not isinstance(hashes, dict) or latest["sha256"] not in hashes.values():
            raise CodeDeliveryRefused("review did not cover the current artifact bytes")
        if self.storage.get_control(task_id).get("active_attempt_id") != review_attempt["attempt_id"]:
            raise CodeDeliveryRefused("review attempt is no longer the active attempt")
        if latest.get("artifact_type") != "patch_text":
            raise CodeDeliveryRefused(
                f"artifact type {latest.get('artifact_type')!r} is not patch_text")
        target_ref = mem_artifact_path(task_id, latest["artifact_id"])
        if task.get("artifact_refs") != [target_ref]:
            raise CodeDeliveryRefused("task artifact references differ from the current patch")
        snapshots = self._snapshot_artifacts(task.get("artifact_refs", []))
        metadata = {"task_id": task_id, "review_id": review.get("review_id"),
                    "target_artifact_path": target_ref, "reviewer_role": review.get("reviewer_role")}
        try:
            validate_review(review, metadata, snapshots, snapshots)
        except (SchemaValidationError, KeyError, TypeError) as exc:
            raise CodeDeliveryRefused(f"invalid current review binding: {exc}") from None
        if hashes != {p: a["sha256"] for p, a in snapshots.items()}:
            raise CodeDeliveryRefused("review artifact coverage differs from the current snapshot")
        if isinstance(existing, dict) and existing.get("state") == "waiting_host":
            pinned = existing.get("artifact") or {}
            if (pinned.get("version") == artifact_version
                    and pinned.get("artifact_id") == latest["artifact_id"]
                    and pinned.get("sha256") == latest["sha256"]
                    and existing.get("generation_attempt_id") == latest.get("generation_attempt_id")
                    and existing.get("review_attempt_id") == review_attempt["attempt_id"]
                    and existing.get("review_sha256") == content_sha256(json.dumps(review, sort_keys=True))):
                return task
            raise CodeDeliveryRefused("waiting delivery binding is stale; cancel or reconcile first")
        parsed = parse_fixture_patch(latest["content"], tpl["authorized_target"])
        ws = (task.get("context") or {}).get("workspace_root") or self.workspace_root
        target_abs = resolve_target_path(ws, parsed["path"])
        try:
            before_bytes = Path(target_abs).read_bytes()
        except OSError as exc:
            raise CodeDeliveryRefused(f"cannot read target baseline bytes: {exc}") from None
        before_sha = sha256_hex(before_bytes)
        reference_bytes = sealed_fixture_bytes(tpl, "reference_rel")
        if parsed["content_bytes"] != reference_bytes:
            raise CodeDeliveryRefused("patch result differs from the sealed reference bytes")
        sealed_before = sealed_fixture_bytes(tpl, "baseline_rel")
        if before_bytes != sealed_before:
            raise CodeDeliveryRefused("workspace target does not match the sealed baseline")
        expected_after = sha256_hex(parsed["content_bytes"])
        scope_before = workspace_hashes(ws)
        if scope_before.get(parsed["path"]) != before_sha:
            raise CodeDeliveryRefused("target changed while capturing the baseline scope")
        delivery = {
            "delivery_id": f"dlv_{task_id}_v{latest['version']}",
            "state": "waiting_host",
            "artifact": {"version": latest["version"], "artifact_id": latest["artifact_id"],
                         "sha256": latest["sha256"]},
            "generation_attempt_id": latest.get("generation_attempt_id"),
            "review_attempt_id": review_attempt["attempt_id"],
            "review_id": review.get("review_id"),
            "review_sha256": content_sha256(json.dumps(review, sort_keys=True)),
            "baseline": {"path": parsed["path"], "sha256_before": before_sha},
            "expected_after": {"path": parsed["path"], "sha256": expected_after},
            "authorized_files": [tpl["authorized_target"]],
            "scope_before": scope_before,
            "apply_receipt": None,
            "created_at": _utc_now_iso(),
            "completed_at": None,
        }
        stopped = self._check_budget_or_fail(task_id, before_call=False)
        if stopped is not None:
            return stopped
        self.storage.save_code_delivery(task_id, delivery)
        self.storage.record_event(task_id=task_id, event_type="code_delivery_prepared",
                                  from_state="reviewing", to_state="reviewing",
                                  actor_role="orchestrator",
                                  details={"delivery_id": delivery["delivery_id"],
                                           "artifact_version": latest["version"],
                                           "expected_after": expected_after})
        return self.storage.get_task(task_id)

    def _prepare_code_delivery(self, task_id: str) -> Dict[str, Any]:
        """Auto-prepare the latest reviewed artifact after a pass verdict (code tasks only)."""
        latest = self.artifact_store.get_latest(task_id)
        if latest is None:
            raise CodeDeliveryRefused("no artifact version to prepare")
        with self.storage.transaction():
            return self._prepare_code_delivery_locked(task_id, latest["version"])

    def complete_code_delivery(self, task_id: str, receipt: Dict[str, Any]) -> Dict[str, Any]:
        owner_id = uuid.uuid4().hex
        if not self.storage.claim_task(task_id, owner_id):
            raise CodeDeliveryRefused("task owner is held by another execution")
        try:
            with self.storage.transaction():
                return self._check_and_complete_code_delivery(task_id, receipt)
        finally:
            self.storage.release_task(task_id, owner_id)

    def _check_and_complete_code_delivery(self, task_id: str, receipt: Dict[str, Any]) -> Dict[str, Any]:
        """Verify a host apply receipt and finalize the code delivery.

        Returns the task in all non-crash paths except refusals: succeeded on
        full verification, failed on exhausted budget, needs_attention when the
        outcome cannot be established. Raises CodeDeliveryRefused (no state
        change) on validation failures.
        """
        task = self.storage.get_task(task_id)
        if task["status"] != "reviewing":
            raise CodeDeliveryRefused(f"complete requires reviewing, got {task['status']}")
        delivery = (task.get("metadata") or {}).get("_delivery")
        if not isinstance(delivery, dict) or delivery.get("state") != "waiting_host":
            raise CodeDeliveryRefused("no waiting_host delivery to complete")
        shape_ok = (
            isinstance(delivery.get("artifact"), dict)
            and isinstance(delivery.get("baseline"), dict)
            and isinstance(delivery.get("expected_after"), dict)
            and isinstance(delivery.get("review_attempt_id"), str)
            and isinstance(delivery.get("review_id"), str)
            and isinstance(delivery.get("review_sha256"), str)
            and isinstance(delivery.get("delivery_id"), str)
        )
        if not shape_ok:
            raise CodeDeliveryRefused("waiting delivery record is malformed")
        tpl = template_for_task(task)
        stamped = (task.get("metadata") or {}).get("_trusted") or {}
        if stamped.get("template_id") != tpl["template_id"]:
            raise CodeDeliveryRefused("task lacks a matching trusted template stamp")
        if stamped.get("source") == "production":
            raise CodeDeliveryRefused(
                "production host assembly is out of scope for the JH-006 offline batch; "
                "only fixture tasks with fixture-assembled receipts can complete")
        elif stamped.get("source") != "fixture":
            raise CodeDeliveryRefused(f"unknown task source {stamped.get('source')!r}")
        return self._complete_code_delivery_locked(task_id, receipt, tpl, delivery)

    def _complete_code_delivery_locked(self, task_id, receipt, tpl, delivery):
        budget_failed = self._check_budget_or_fail(task_id, before_call=False)
        if budget_failed is not None:
            return budget_failed
        task = self.storage.get_task(task_id)
        self._reject_delivery_blockers(task_id)
        ws = (task.get("context") or {}).get("workspace_root") or self.workspace_root
        expected_command = materialized_post_apply_command(tpl, ws)
        norm = validate_apply_receipt(
            receipt, task_id=task_id, delivery_id=delivery["delivery_id"],
            authorized_target=tpl["authorized_target"], expected_command=expected_command)
        latest = self.artifact_store.get_latest(task_id)
        pinned_art = delivery.get("artifact") or {}
        if (latest is None or latest["version"] != pinned_art.get("version")
                or latest["artifact_id"] != pinned_art.get("artifact_id")
                or latest["sha256"] != pinned_art.get("sha256")
                or latest.get("generation_attempt_id") != delivery.get("generation_attempt_id")):
            raise CodeDeliveryRefused("current artifact differs from the prepared version")
        attempts = [a for a in self.storage.list_model_attempts(task_id)
                    if a.get("actor_role") == "reviewer" and a.get("status") == "completed"]
        if not attempts or attempts[-1]["attempt_id"] != delivery.get("review_attempt_id"):
            raise CodeDeliveryRefused("pinned review attempt is no longer the latest completed review")
        review = attempts[-1].get("response") or {}
        if review.get("review_id") != delivery.get("review_id"):
            raise CodeDeliveryRefused("review identity drifted since prepare")
        if not is_clean_pass_review(review):
            raise CodeDeliveryRefused("pinned review is not a clean pass")
        if content_sha256(json.dumps(review, sort_keys=True)) != delivery.get("review_sha256"):
            raise CodeDeliveryRefused("pinned review content changed since prepare")
        if self.storage.get_control(task_id).get("active_attempt_id") != delivery.get("review_attempt_id"):
            raise CodeDeliveryRefused("review attempt is no longer active")
        if not fixture_host_evidence_matches(self.storage, task_id, norm):
            return self._complete_uncertain(task_id, delivery, "host record missing or differs from the receipt/raw output")
        expected_script = f"{tpl['fixture_dir']}/{tpl['check_rel']}"
        if norm["tests"]["check_script"] != expected_script:
            raise CodeDeliveryRefused("receipt check script path differs from the fixed check")
        target_abs = resolve_target_path(ws, tpl["authorized_target"])
        try:
            actual_bytes = Path(target_abs).read_bytes()
        except OSError:
            return self._complete_uncertain(task_id, delivery, "authorized target unreadable at complete time")
        actual_sha = sha256_hex(actual_bytes)
        if norm["baseline_seen"] != delivery["baseline"]["sha256_before"]:
            if actual_sha == delivery["baseline"]["sha256_before"]:
                raise CodeDeliveryRefused(
                    "target version changed before apply; restore the baseline and retry, or cancel and re-submit")
            return self._complete_uncertain(
                task_id, delivery, "baseline changed and applied bytes differ from the recorded baseline")
        if actual_sha != delivery["expected_after"]["sha256"]:
            if actual_sha == delivery["baseline"]["sha256_before"]:
                raise CodeDeliveryRefused("target is still the baseline; no verified apply")
            return self._complete_uncertain(task_id, delivery,
                "applied bytes differ from the reviewed expected_after")
        try:
            scope_after = workspace_hashes(ws)
        except CodeDeliveryRefused as exc:
            return self._complete_uncertain(task_id, delivery, str(exc))
        scope_before = delivery.get("scope_before")
        if not isinstance(scope_before, dict):
            return self._complete_uncertain(task_id, delivery, "baseline scope evidence missing")
        unauthorized = [p for p in set(scope_before) | set(scope_after)
                        if p != tpl["authorized_target"] and scope_before.get(p) != scope_after.get(p)]
        if unauthorized or scope_after.get(tpl["authorized_target"]) != actual_sha:
            return self._complete_uncertain(task_id, delivery,
                "unauthorized workspace changes or target drift during verification")
        if norm["target"]["sha256"] != actual_sha:
            return self._complete_uncertain(
                task_id, delivery, "receipt target hash differs from freshly read bytes")
        tests = norm["tests"]
        if tests["target_sha256"] != actual_sha:
            return self._complete_uncertain(
                task_id, delivery, "receipt test target hash differs from freshly read bytes")
        sealed_check = sealed_fixture_bytes(tpl, "check_rel")
        if sha256_hex(sealed_check) != tests["check_script_sha256"]:
            return self._complete_uncertain(
                task_id, delivery, "receipt check script differs from the sealed check")
        if tests["exit_code"] != 0:
            return self._complete_uncertain(
                task_id, delivery, f"post-apply checks exited {tests['exit_code']}")
        if norm["uncertain"] is True:
            return self._complete_uncertain(task_id, delivery, "host receipt declares uncertainty")
        stopped = self._check_budget_or_fail(task_id, before_call=False)
        if stopped is not None:
            return stopped
        criteria = task["acceptance_criteria"]
        acceptance = [
            {"criterion": criteria[0], "passed": True,
             "evidence": [f"review:{delivery['review_id']}:pass"]},
            {"criterion": criteria[1], "passed": True,
             "evidence": [f"delivery:{delivery['delivery_id']}:post-apply-checks:exit0"]},
            {"criterion": criteria[2], "passed": True,
             "evidence": [f"delivery:{delivery['delivery_id']}:scope-ok"]},
        ]
        snapshots = self._snapshot_artifacts(task.get("artifact_refs", []))
        try:
            with self.storage.transaction():
                evidence_refs = list(task.get("tool_execution_evidence") or []) + [
                    f"review:{delivery['review_id']}:pass",
                    f"delivery:{delivery['delivery_id']}:post-apply-checks:exit0",
                    f"delivery:{delivery['delivery_id']}:scope-ok",
                ]
                task = self.storage.attach_evidence(task_id, evidence_refs)
                self.storage.bind_review(review, delivery["review_attempt_id"], snapshots, acceptance)
                validate_acceptance(task, acceptance, task["tool_execution_evidence"])
                done = dict(delivery)
                done["state"] = "applied"
                done["apply_receipt"] = norm
                done["completed_at"] = _utc_now_iso()
                self.storage.save_code_delivery(task_id, done)
                result = self.storage.transition_task_state(
                    task_id, "succeeded", actor_role="orchestrator",
                    reason="code_delivery_verified_complete", review_verdict="pass",
                    details={"delivery_id": delivery["delivery_id"]})
        except (StaleAttemptError, SchemaValidationError, InvalidStateTransitionError, ValueError) as exc:
            return self._complete_uncertain(
                task_id, delivery, f"finalize failed safely: {type(exc).__name__}: {exc}")
        record_task_outcome_memory(self.storage, result)
        return result

    def _complete_uncertain(self, task_id, delivery, reason):
        try:
            task = self.storage.transition_task_state(
                task_id, "needs_attention", actor_role="orchestrator",
                reason="code_delivery_uncertain", error_info=reason,
                details={"delivery_id": (delivery or {}).get("delivery_id")})
        except InvalidStateTransitionError:
            task = self.storage.get_task(task_id)
        self.storage.record_event(task_id=task_id, event_type="code_delivery_uncertain",
                                  from_state=task["status"], to_state=task["status"],
                                  actor_role="orchestrator",
                                  details={"delivery_id": (delivery or {}).get("delivery_id"),
                                           "reason": reason})
        return task

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
        if delivery_state(self.storage.get_task(task_id)) == "waiting_host":
            return self.storage.get_task(task_id)
        owner_id = uuid.uuid4().hex
        if not self.storage.claim_task(task_id, owner_id):
            return self.storage.get_task(task_id)
        try:
            pre_status = self.storage.get_task(task_id)["status"]
            result = self._run_task(task_id)
            # Minimal Phase 3 memory logging: only when THIS run drove the task
            # into a terminal succeeded/failed state (never on repeat queries).
            if result["status"] in {"succeeded", "failed"} and pre_status not in {"succeeded", "failed"}:
                record_task_outcome_memory(self.storage, result)
            return result
        except StaleAttemptError as exc:
            task = self.storage.get_task(task_id)
            self.storage.record_event(task_id=task_id, event_type="stale_attempt_rejected",
                                      from_state=task["status"], to_state=task["status"],
                                      actor_role="orchestrator", details={"reason": str(exc)})
            return task
        finally:
            self.storage.release_task(task_id, owner_id)

    # ------------------------------------------------------------------
    # Phase 3 Worker contract: trusted planning / generation / repair context
    # ------------------------------------------------------------------

    def _worker_task_context(self, task: Dict[str, Any]) -> Dict[str, Any]:
        budget = task.get("budget") or {}
        context = task.get("context") or {}
        return {
            "task_id": task["task_id"],
            "title": task["title"],
            "description": task["description"],
            "acceptance_criteria": task["acceptance_criteria"],
            "constraints": {
                "max_turns": budget.get("max_turns"),
                "max_tokens": budget.get("max_tokens"),
                "max_execution_time_seconds": budget.get("max_execution_time_seconds"),
                "workspace_root": context.get("workspace_root"),
                "target_files": context.get("target_files") or [],
                "risk_level": (task.get("risk_assessment") or {}).get("risk_level"),
            },
            "input_data": (task.get("metadata") or {}).get("input_data"),
        }

    def _build_planning_messages(self, task: Dict[str, Any]) -> List[Dict[str, str]]:
        # Phase 3 gap fix: planning receives the trusted task identity, goals,
        # acceptance criteria and constraints (previously only title+description).
        return [
            {"role": "system", "content": (
                "Generate a valid execution Plan JSON for the trusted task context provided. "
                "Return only the raw Plan JSON object without markdown fences."
            )},
            {"role": "user", "content": json.dumps(self._worker_task_context(task), ensure_ascii=False)},
        ]

    def _build_tool_plan_messages(self, task: Dict[str, Any], repair_count: int) -> List[Dict[str, str]]:
        """Phase 4 batch 1: structured tool-request proposal for file-tools tasks.

        The Worker receives the trusted task context (task_id, goal, acceptance
        criteria, constraints, authorized target files) and returns a ToolPlan
        whose tool_requests the control layer validates and executes. The Worker
        never touches the filesystem itself. Repair turns receive the reviewer
        issues and the current staged content, mirroring the artifact contract.
        """
        context = self._worker_task_context(task)
        context["attempt_kind"] = "initial" if repair_count == 0 else "repair"
        context["available_tools"] = ["read_file", "stage_write"]
        if repair_count > 0:
            issues = []
            for binding in self.storage.list_review_bindings(task_id=task["task_id"]):
                issues.extend(binding["review"].get("issues") or [])
            context["reviewer_issues"] = issues
            context["note"] = ("Previous staged write was rejected in review; "
                               "re-propose the corrected stage_write request.")
        return [
            {"role": "system", "content": (
                "Propose the controlled file tool requests for the trusted task context. "
                "Return only the raw ToolPlan JSON object matching ToolPlanSchemaV1 without "
                "markdown fences. Use tool_name 'stage_write' with parameters "
                "{'path': <authorized relative target>, 'content': <full file content>}. "
                "The control layer executes tools; you must never claim file access."
            )},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]

    def _build_generation_messages(self, task: Dict[str, Any], repair_count: int) -> List[Dict[str, str]]:
        context = self._worker_task_context(task)
        if is_code_delivery_task(task):
            tpl = template_for_task(task)
            context["code_template"] = {
                "template_id": tpl["template_id"], "authorized_target": tpl["authorized_target"],
                "baseline": sealed_fixture_bytes(tpl, "baseline_rel").decode("utf-8"),
                "artifact_type": "patch_text", "patch_format": {"path": tpl["authorized_target"], "content": "complete corrected UTF-8 file"},
                "note": "Generate a patch only. Host applies and tests after independent patch review."}
        if repair_count > 0:
            issues = []
            for binding in self.storage.list_review_bindings(task_id=task["task_id"]):
                issues.extend(binding["review"].get("issues") or [])
            acceptance_failures = [
                item for item in (self._last_acceptance_results(task["task_id"]) or [])
                if item.get("passed") is not True
            ]
            latest = self.artifact_store.get_latest(task["task_id"])
            context.update({
                "attempt_kind": "repair",
                "repair_round": repair_count,
                "reviewer_issues": issues,
                "acceptance_failures": acceptance_failures,
                "current_artifact": (
                    {"version": latest["version"], "sha256": latest["sha256"], "content": latest["content"]}
                    if latest else None
                ),
            })
        else:
            context["attempt_kind"] = "initial"
        return [
            {"role": "system", "content": (
                "Produce the actual work artifact for the trusted task context. "
                "Return only the raw Artifact JSON object matching ArtifactSchemaV1; "
                "the content field must contain the complete artifact, not a plan or a completion summary. "
                "The control layer computes the authoritative version and SHA-256; do not include hash fields."
            )},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]

    def _last_acceptance_results(self, task_id: str) -> Optional[List[Dict[str, Any]]]:
        bindings = self.storage.list_review_bindings(task_id=task_id)
        return bindings[-1]["acceptance"] if bindings else None

    def _snapshot_artifacts(self, refs: List[str]) -> Dict[str, Any]:
        if refs and all(is_mem_artifact_path(ref) for ref in refs):
            return artifact_snapshot(self.artifact_store, refs)
        return artifact_snapshot(self.tool_executor, refs)

    def _verify_staged_snapshots_match_tool_records(
        self, task_id: str, snapshots: Dict[str, Any]
    ) -> None:
        """ARCH-05 defence: every staged file entering review must still match
        the exact content_sha256 recorded by the latest stage_write tool execution."""
        if not isinstance(self.tool_executor, FileToolExecutor):
            return
        stage_execs = [t for t in self.storage.list_tool_executions(task_id)
                       if t["tool_name"] == "stage_write" and t["status"] == "success"]
        if not stage_execs:
            return
        latest_by_staged: Dict[str, Dict[str, Any]] = {}
        for t in stage_execs:
            sd = (t.get("result") or {}).get("structured_data") or {}
            sp = sd.get("staged_path")
            if sp:
                latest_by_staged[sp] = sd
        for ref, snap in snapshots.items():
            sd = latest_by_staged.get(ref)
            if sd is not None and snap.get("sha256") != sd.get("content_sha256"):
                raise SchemaValidationError(
                    f"Staged artifact '{ref}' changed between stage_write and review snapshot "
                    f"(snapshot={snap.get('sha256')}, recorded={sd.get('content_sha256')})")

    def _apply_staged_writes(self, task_id: str, task: Dict[str, Any],
                             review_json: Dict[str, Any],
                             snapshots: Optional[Dict[str, Any]] = None) -> Optional[List[str]]:
        """Phase 4 batch 1: post-review apply of staged writes (controlled action).

        Re-confirms for each staged write: applied content == reviewed content,
        target version == pre-review state, path ownership and authorization
        still valid. Refusals and uncertain outcomes raise ApplyBlockedError ->
        needs_attention (target never overwritten on refusal). Returns the
        apply evidence refs appended to tool_execution_evidence, or None when
        there is nothing to apply.
        """
        executor = self.tool_executor
        if not isinstance(executor, FileToolExecutor):
            return None
        stage_execs = [t for t in self.storage.list_tool_executions(task_id)
                       if t["tool_name"] == "stage_write" and t["status"] == "success"]
        if not stage_execs:
            return None
        # Only the latest stage_write per target_path from the CURRENT reviewed
        # set is eligible. Historical stage_write records from earlier repair
        # turns are ignored (not applied, not blocking): the Reviewed set for
        # THIS turn is authoritative.
        if snapshots is None:
            raise ApplyBlockedError("apply refused: reviewed snapshots are missing")
        reviewed_refs = set(task.get("artifact_refs") or [])
        latest_by_target: Dict[str, Dict[str, Any]] = {}
        for t in stage_execs:
            sd = (t.get("result") or {}).get("structured_data") or {}
            if sd.get("staged_path") not in reviewed_refs:
                continue
            latest_by_target[sd["target_path"]] = t
        if not latest_by_target:
            raise ApplyBlockedError(
                "apply refused: no reviewed staged write is eligible for apply")

        authorized_files = None
        if self.enforce_target_files_scope:
            authorized_files = set((task.get("context") or {}).get("target_files") or [])

        evidence: List[str] = []
        for t in latest_by_target.values():
            sd = t["result"]["structured_data"] or {}
            staged_path = sd["staged_path"]
            # No fallback: a missing snapshot entry fails closed instead of
            # comparing the tool record against itself.
            reviewed_entry = snapshots.get(staged_path)
            if not isinstance(reviewed_entry, dict) or not reviewed_entry.get("sha256"):
                raise ApplyBlockedError(
                    f"apply refused: staged write '{staged_path}' has no reviewed snapshot entry")
            reviewed_sha = reviewed_entry["sha256"]
            if reviewed_sha != sd.get("content_sha256"):
                raise ApplyBlockedError(
                    f"apply refused: stage_write content_sha256 ({sd.get('content_sha256')}) "
                    f"does not match reviewed snapshot sha256 ({reviewed_sha})")
            apply_req = {
                "schema_version": "1.0.0",
                "request_id": f"treq_{uuid.uuid4().hex[:12]}",
                "task_id": task_id,
                "step_id": "step_apply",
                "caller_role": "orchestrator",
                "tool_name": "apply_staged_write",
                "parameters": {
                    "target_path": sd["target_path"], "staged_path": staged_path,
                    "content_sha256": reviewed_sha,
                    "target_pre_sha256": sd.get("target_pre_sha256"),
                },
                "timeout_ms": 30000,
                "risk_level": "workspace_write",
                "requires_human_approval": False,
                "created_at": _utc_now_iso(),
            }
            # Per-call policy re-check on the actual apply parameters.
            decision = self.policy_engine.evaluate_tool_request(
                apply_req, approved_fingerprints=json.loads(self.storage.get_control(task_id)["approvals_json"]),
                authorized_files=authorized_files)
            if decision.action != "allow":
                raise ApplyBlockedError(
                    f"apply refused by policy ({decision.violated_rule_id}): {decision.reason}")

            try:
                started = self.storage.record_tool_start(apply_req, has_side_effect=True,
                                                         allowed_states=("executing", "reviewing"))
            except ValueError as exc:
                raise ApplyBlockedError(f"apply record conflict: {exc}")
            if not started:
                raise ApplyBlockedError("apply request is stale (task left executing/reviewing)")
            try:
                outcome = executor.apply_staged(
                    task_id,
                    target_path=sd["target_path"], staged_path=staged_path,
                    content_sha256=reviewed_sha,
                    target_pre_sha256=sd.get("target_pre_sha256"),
                    authorized_files=authorized_files)
            except SimulatedInterruptionError:
                # Interruption drill / real crash: classify without blind replay.
                recovery = executor.recover_apply(
                    task_id,
                    target_path=sd["target_path"], staged_path=staged_path,
                    content_sha256=reviewed_sha,
                    target_pre_sha256=sd.get("target_pre_sha256"))
                if recovery["state"] == "not_applied":
                    outcome = executor.apply_staged(
                        task_id,
                        target_path=sd["target_path"], staged_path=staged_path,
                        content_sha256=reviewed_sha,
                        target_pre_sha256=sd.get("target_pre_sha256"),
                        authorized_files=authorized_files)
                    self.storage.record_event(
                        task_id=task_id, event_type="apply_recovered_not_applied",
                        from_state="reviewing", to_state="reviewing", actor_role="orchestrator",
                        details={"target_path": sd["target_path"], "recovered_state": "not_applied"})
                elif recovery["state"] == "applied":
                    # The recovered 'applied' bytes must still equal the CURRENT
                    # reviewed snapshot; a concurrent rewrite after the crash
                    # must not be recorded as a reviewed apply.
                    if recovery["target_sha256"] != reviewed_sha:
                        raise ApplyBlockedError(
                            f"recovered target hash mismatch (disk={recovery['target_sha256']}, "
                            f"reviewed={reviewed_sha})")
                    self.storage.record_tool_completion({
                        "schema_version": "1.0.0",
                        "result_id": f"tres_{uuid.uuid4().hex[:12]}",
                        "request_id": apply_req["request_id"], "task_id": task_id,
                        "tool_name": "apply_staged_write", "status": "success",
                        "stdout": None, "stderr": None,
                        "structured_data": {"target_path": sd["target_path"],
                                            "recovered_state": "applied",
                                            "applied_sha256": recovery["target_sha256"]},
                        "execution_time_ms": 0, "error_message": None,
                        "created_at": _utc_now_iso(),
                    }, confirmed_clean=True)
                    evidence.append(f"{apply_req['request_id']}:apply_staged_write:applied_confirmed")
                    continue
                else:
                    raise ApplyBlockedError(
                        f"apply outcome uncertain (target sha {recovery['target_sha256']}); "
                        "needs_attention, no blind replay")
            except (PathBoundaryError, FileNotFoundError, OSError) as exc:
                self.storage.record_tool_completion(
                    self._apply_refused_result(apply_req, str(exc)), confirmed_clean=False)
                raise ApplyBlockedError(f"apply refused: {exc}")

            # Verify on disk that the applied target bytes equal the reviewed snapshot hash.
            verified_target = executor.resolve_workspace_path(sd["target_path"])
            import hashlib as _hl
            disk_sha = _hl.sha256(verified_target.read_bytes()).hexdigest()
            if disk_sha != reviewed_sha or outcome.get("applied_sha256") != reviewed_sha:
                raise ApplyBlockedError(
                    f"post-apply target hash mismatch (disk={disk_sha}, reviewed={reviewed_sha})")

            apply_res = {
                "schema_version": "1.0.0",
                "result_id": f"tres_{uuid.uuid4().hex[:12]}",
                "request_id": apply_req["request_id"], "task_id": task_id,
                "tool_name": "apply_staged_write", "status": "success",
                "stdout": None, "stderr": None,
                "structured_data": {"target_path": sd["target_path"],
                                    "staged_path": staged_path,
                                    "applied_sha256": outcome["applied_sha256"],
                                    "replaced_existing": outcome["replaced_existing"]},
                "execution_time_ms": 0, "error_message": None,
                "created_at": _utc_now_iso(),
            }
            self.storage.record_tool_completion(apply_res, confirmed_clean=True)
            evidence.append(f"{apply_req['request_id']}:apply_staged_write:applied")
        return evidence

    def _apply_refused_result(self, apply_req: Dict[str, Any], error_text: str) -> Dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "result_id": f"tres_{uuid.uuid4().hex[:12]}",
            "request_id": apply_req["request_id"], "task_id": apply_req["task_id"],
            "tool_name": "apply_staged_write", "status": "denied_by_policy",
            "stdout": None, "stderr": error_text, "structured_data": None,
            "execution_time_ms": 0, "error_message": error_text,
            "created_at": _utc_now_iso(),
        }

    def _generate_artifact(self, task_id: str, worker: ModelAdapter, task: Dict[str, Any], repair_count: int) -> Dict[str, Any]:
        """Run one structured generation (or repair) turn and persist the control-hashed artifact."""
        try:
            gen_messages = self._build_generation_messages(task, repair_count)
            resp, attempt_id = self._invoke_model(task_id, "worker", worker, gen_messages, self._artifact_schema)
            envelope = resp.parsed_json
            Draft7Validator(self._artifact_schema, format_checker=FormatChecker()).validate(envelope)
            if envelope["task_id"] != task_id:
                raise SchemaValidationError("Artifact task_id correlation mismatch")
            version = repair_count + 1
            record = self.artifact_store.save_version(
                task_id, envelope=envelope, version=version, generation_attempt_id=attempt_id)
            mem_path = mem_artifact_path(task_id, envelope["artifact_id"])
            evidence_ref = f"artifact:{envelope['artifact_id']}@v{version}:sha256={record['sha256']}"
            task = self.storage.attach_artifact(task_id, mem_path, evidence_ref)
            if (task.get("metadata") or {}).get("input_data") is not None:
                task = self.storage.attach_artifact(task_id, mem_path, "task_metadata:input_data")
            return task
        except CredentialMissingError as exc:
            return self.storage.transition_task_state(
                task_id, "failed", actor_role="worker",
                reason="worker_credential_missing", error_info=str(exc))
        except (AdapterError, SchemaValidationError, ValidationError) as exc:
            err_str = str(exc)
            lowered = err_str.lower()
            if "timed out" in lowered:
                reason_code = "worker_timeout"
            elif "truncated" in lowered:
                reason_code = "worker_truncated"
            elif "refused" in lowered:
                reason_code = "worker_refused"
            else:
                reason_code = "worker_generation_failed"
            return self.storage.transition_task_state(
                task_id, "failed", actor_role="worker",
                reason=reason_code, error_info=err_str)

    def _record_response_source(self, task_id, role, adapter, attempt_id, response) -> None:
        """Persist the actually-used call source for production audit and resume checks."""
        status = self.storage.get_task(task_id)["status"]
        self.storage.record_event(
            task_id=task_id, event_type="model_response_source",
            from_state=status, to_state=status, actor_role="orchestrator",
            details={
                "attempt_id": attempt_id, "role": role,
                "adapter_class": type(adapter).__name__,
                "model_id": getattr(adapter, "model_id", None),
                "is_mock": getattr(response, "is_mock", None),
            },
        )

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
            if self._require_real_responses:
                self._record_response_source(task_id, role, adapter, attempt_id, response)
                source_flag = getattr(response, "is_mock", None)
                if source_flag is not False:
                    reason = "mock_response_rejected" if source_flag is True else "unconfirmed_response_source"
                    self.storage.complete_attempt(task_id, attempt_id, {"error": reason}, None)
                    raise AdapterError(
                        f"Production refused {role} response with mock/unconfirmed model source "
                        f"(is_mock={source_flag!r}, adapter={type(adapter).__name__})"
                    )
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
                plan_resp, _ = self._invoke_model(task_id, "worker", worker, self._build_planning_messages(task), self._plan_schema)
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
            execution_mode = (task.get("metadata") or {}).get("execution_mode", "generate")
            if execution_mode == "file_tools":
                # Phase 4 batch 1: the real Worker proposes a structured tool plan
                # (Worker proposes -> control layer validates -> tool executes ->
                # result returns). Same unified attempt/budget/usage chain.
                try:
                    plan_resp, _ = self._invoke_model(
                        task_id, "worker", worker, self._build_tool_plan_messages(task, repair_idx),
                        self._tool_plan_schema)
                    tool_plan = plan_resp.parsed_json
                    Draft7Validator(self._tool_plan_schema, format_checker=FormatChecker()).validate(tool_plan)
                    if tool_plan["task_id"] != task_id:
                        raise SchemaValidationError("ToolPlan task_id correlation mismatch")
                    proposed_requests = tool_plan["tool_requests"]
                except CredentialMissingError as exc:
                    return self.storage.transition_task_state(
                        task_id, "failed", actor_role="worker",
                        reason="worker_credential_missing", error_info=str(exc))
                except (AdapterError, SchemaValidationError, ValidationError) as exc:
                    err_str = str(exc)
                    lowered = err_str.lower()
                    reason_code = ("worker_timeout" if "timed out" in lowered
                                   else "worker_tool_plan_failed")
                    return self.storage.transition_task_state(
                        task_id, "failed", actor_role="worker",
                        reason=reason_code, error_info=err_str)
            elif hasattr(worker, "propose_tool_requests"):
                proposed_requests = worker.propose_tool_requests(task, turn_index=repair_idx)
            else:
                proposed_requests = []

            authorized_files: Optional[Set[str]] = None
            if self.enforce_target_files_scope:
                t_files = (task.get("context") or {}).get("target_files") or []
                authorized_files = set(t_files)

            produced_artifacts: List[str] = []
            execution_evidence: List[str] = []

            if proposed_requests and is_code_delivery_task(task):
                return self.storage.transition_task_state(
                    task_id, "needs_attention", reason="code_template_tools_refused",
                    error_info="Fixed code template accepts patch artifacts only, never model tools")
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
                staged_rel = (tool_res.get("structured_data") or {}).get("staged_path")
                if isinstance(path_param, str) and path_param:
                    # Phase 4 file tools: the reviewed artifact is the STAGED file
                    # (never the final target, which is only written at apply).
                    # With a real FileToolExecutor, read_file results have no
                    # staged_path and must not enter the reviewed artifact set.
                    # Other executors (Mock/Phase 2-3 flows) keep legacy behavior.
                    if isinstance(self.tool_executor, FileToolExecutor):
                        if tool_req["tool_name"] == "stage_write":
                            produced_artifacts.append(staged_rel or path_param)
                        elif staged_rel:
                            produced_artifacts.append(staged_rel)
                        # else: read_file or other non-staging tool -> not an artifact.
                    else:
                        produced_artifacts.append(staged_rel or path_param)
                execution_evidence.append(f"{tool_req['request_id']}:{tool_req['tool_name']}:success")
                if tool_req["tool_name"] == "stage_write" and isinstance(path_param, str) and path_param:
                    # The resolved target path is a recorded fact the acceptance
                    # evaluator cites as evidence for the authorization criterion.
                    execution_evidence.append(f"target_path:{path_param}")

            if self.storage.get_task(task_id)["status"] != "executing":
                return self.storage.get_task(task_id)

            # Phase 3: no-tool generation tasks produce the artifact through the
            # structured Worker contract (memory/SQLite stored, never a file write).
            # Phase 4 file_tools mode never enters the generation path: its
            # artifacts are the real staged files produced by the tool executor.
            if not proposed_requests and execution_mode != "file_tools":
                task = self._generate_artifact(task_id, worker, task, repair_idx)
                if task["status"] != "executing":
                    return task

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
                snapshots = self._snapshot_artifacts(task.get("artifact_refs", []))
                self._verify_staged_snapshots_match_tool_records(task_id, snapshots)
                review_metadata = {
                    "schema_version": "1.0.0",
                    "review_id": f"rev_{uuid.uuid4().hex[:12]}",
                    "task_id": task_id,
                    "target_artifact_type": "patch" if is_code_delivery_task(task) else "code",
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
                if is_code_delivery_task(task):
                    tpl = template_for_task(task)
                    independent_review_context["code_review_scope"] = {
                        "template_id": tpl["template_id"],
                        "baseline": sealed_fixture_bytes(tpl, "baseline_rel").decode("utf-8"),
                        "pending_host_criteria": tpl["criteria"][1:],
                        "note": "Assess patch correctness only; host application/tests remain pending and cannot be claimed."}
                review_messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are an independent code reviewer for JH-Agent OS. "
                            "Review only the supplied artifact and tool_execution_evidence against acceptance_criteria. "
                            "Return only the raw ReviewResult JSON object without markdown fences or backticks. "
                            + ("For code_review_scope, pass means the patch is correct with no blocking issues; "
                             "post-apply criteria remain pending control verification. "
                             if is_code_delivery_task(task) else
                             "Use verdict pass with empty issues if all acceptance_criteria are satisfied; ")
                            + "Otherwise use revise or reject and cite concrete evidence in issue evidence. "
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
                                self._snapshot_artifacts(task.get("artifact_refs", [])))
                budget_failed = self._check_budget_or_fail(task_id, before_call=False)
                if budget_failed is not None:
                    return budget_failed
                acceptance = []
                if review_json["verdict"] == "pass" and is_code_delivery_task(task):
                    # Code tasks never judge post-apply criteria pre-apply: prepare
                    # the delivery (pin baseline + expected bytes, mark waiting)
                    # instead of running acceptance/apply/success here.
                    try:
                        return self._prepare_code_delivery(task_id)
                    except CodeDeliveryRefused as exc:
                        return self.storage.transition_task_state(
                            task_id, "needs_attention", actor_role="orchestrator",
                            reason="code_delivery_prepare_refused",
                            error_info=str(exc))
                if review_json["verdict"] == "pass":
                    if self.acceptance_evaluator is not None:
                        refs = task.get("artifact_refs", [])
                        evidence_view = (self.artifact_store
                                         if refs and all(is_mem_artifact_path(r) for r in refs)
                                         else _DispatchSnapshotView(self._snapshot_artifacts))
                        acceptance = self.acceptance_evaluator(
                            task, self.storage.list_tool_executions(task_id), evidence_view)
                    validate_acceptance(task, acceptance, task["tool_execution_evidence"])
                    validate_review(review_json, review_metadata, snapshots,
                                    self._snapshot_artifacts(task.get("artifact_refs", [])))
                    # Phase 4 batch 1: post-review apply of staged writes (controlled
                    # action, ONLY after a pass verdict). Re-confirms content hash ==
                    # reviewed hash, target version == pre-review state, path
                    # ownership and authorization; any mismatch refuses WITHOUT
                    # touching the target. revise/reject/needs_human never apply.
                    try:
                        apply_evidence = self._apply_staged_writes(
                            task_id, task, review_json, snapshots=snapshots)
                    except ApplyBlockedError as exc:
                        return self.storage.transition_task_state(
                            task_id, "needs_attention", actor_role="orchestrator",
                            reason="staged_write_apply_refused",
                            error_info=f"Staged write apply blocked: {exc}")
                    if apply_evidence:
                        execution_evidence = list(task.get("tool_execution_evidence") or []) + apply_evidence
                        task = self.storage.attach_evidence(task_id, execution_evidence)
                else:
                    acceptance = []
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
