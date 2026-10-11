"""
JH-Agent OS Configurable Mock Provider & Mock Tool Executor v1.0.0
Supports offline deterministic verification of all Phase 2 lifecycle, policy,
budget, review, timeout, and crash-interruption scenarios.
"""

from datetime import datetime, timezone
import copy
import json
from typing import Any, Callable, Dict, List, Optional
import uuid

from jsonschema import Draft7Validator, FormatChecker
from src.adapters import (
    AdapterError,
    AdapterResponse,
    CredentialMissingError,
    ModelAdapter,
    SchemaValidationError,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SimulatedInterruptionError(Exception):
    """Raised when a tool simulates a mid-execution crash or power loss before completion."""
    pass


def mock_acceptance(task, executions, artifact_store=None):
    """Explicit test fixture; never a production validator for arbitrary user criteria."""
    evidence = [f"{item['request_id']}:{item['tool_name']}:success" for item in executions if item["status"] == "success"]
    return [{"criterion": criterion, "passed": bool(evidence), "evidence": evidence, "is_mock": True}
            for criterion in task["acceptance_criteria"]]


class ScriptedWorkerAdapter(ModelAdapter):
    """Configurable Mock Worker that produces schema-valid Plans and ToolRequests or simulates faults."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        mode: str = "normal",
        tool_requests_by_turn: Optional[List[List[Dict[str, Any]]]] = None,
        artifact_content_by_turn: Optional[List[str]] = None,
        artifact_type: str = "text_transformation",
        tokens_per_call: Optional[int] = 120,
        simulated_latency_ms: int = 10,
    ):
        super().__init__(config or {"role": "worker", "model_id": "mock-gemini-worker"})
        self.mode = mode
        self.tool_requests_by_turn = tool_requests_by_turn or []
        self.artifact_content_by_turn = artifact_content_by_turn
        self.artifact_type = artifact_type
        self.tokens_per_call = tokens_per_call  # Can be None to test unknown token handling
        self.simulated_latency_ms = simulated_latency_ms
        self.call_count = 0
        self.calls = []
        self.generation_contexts = []
        self._generation_calls = 0

    def get_role(self) -> str:
        return "worker"

    def check_health(self) -> Dict[str, Any]:
        if self.mode == "missing_credential":
            return {"healthy": False, "mock": True, "error": "Missing GEMINI_API_KEY"}
        return {"healthy": True, "mock": True, "error": None}

    def invoke(self, messages: List[Dict[str, str]], **kwargs) -> AdapterResponse:
        self.call_count += 1
        if self.mode == "missing_credential":
            raise CredentialMissingError("Missing environment variable 'GEMINI_API_KEY'")
        if self.mode == "timeout":
            raise AdapterError("Worker model invocation timed out")
        if self.mode == "truncated":
            raise AdapterError("Provider truncated the output (finishReason=MAX_TOKENS); result is incomplete")
        if self.mode == "refused":
            raise AdapterError("Provider refused the request (finishReason=SAFETY)")
        if self.mode == "provider_error":
            raise AdapterError("Worker provider HTTP 503 Service Unavailable")

        payload = kwargs.get("_canned_payload") or {"status": "ok", "turn": self.call_count}
        content = json.dumps(payload, ensure_ascii=False)
        if self.mode == "invalid_json":
            content = "{broken json output"

        p_tok = None if self.tokens_per_call is None else self.tokens_per_call // 2
        c_tok = None if self.tokens_per_call is None else self.tokens_per_call - (self.tokens_per_call // 2)
        return AdapterResponse(
            content=content,
            parsed_json=payload if self.mode != "invalid_json" else None,
            model_id=self.model_id,
            tokens_prompt=p_tok,
            tokens_completion=c_tok,
            total_tokens=self.tokens_per_call,
            latency_ms=self.simulated_latency_ms,
            is_mock=True,
            raw_response={"mock": True, "call_count": self.call_count},
        )

    def invoke_structured(
        self,
        messages: List[Dict[str, str]],
        schema: Dict[str, Any],
        **kwargs
    ) -> AdapterResponse:
        if self.mode == "invalid_json":
            self.call_count += 1
            raise SchemaValidationError("Worker output is not valid JSON or failed schema validation")

        # Phase 3 Worker contract: artifact generation is detected by schema title.
        if schema.get("title") == "ArtifactSchemaV1":
            self.calls.append(dict(kwargs))
            user_payload = None
            for message in messages:
                if message.get("role") == "user":
                    try:
                        user_payload = json.loads(message["content"])
                    except ValueError:
                        user_payload = None
            self.generation_contexts.append(user_payload)
            if self.mode == "missing_credential":
                raise CredentialMissingError("Missing environment variable 'GEMINI_API_KEY'")
            if self.mode == "timeout":
                raise AdapterError("Worker model invocation timed out")
            if self.mode == "truncated":
                raise AdapterError("Provider truncated the output (finishReason=MAX_TOKENS); result is incomplete")
            if self.mode == "refused":
                raise AdapterError("Provider refused the request (finishReason=SAFETY)")
            self.call_count += 1
            gen_index = self._generation_calls
            self._generation_calls += 1
            task_id = kwargs.get("task_id", "task_default12345")
            if self.artifact_content_by_turn is not None:
                if gen_index >= len(self.artifact_content_by_turn):
                    content = self.artifact_content_by_turn[-1]
                else:
                    content = self.artifact_content_by_turn[gen_index]
            else:
                content = f"mock artifact for {task_id}\n"
            artifact_payload = {
                "schema_version": "1.0.0",
                "artifact_id": "art_" + uuid.uuid4().hex[:12],
                "task_id": task_id,
                "artifact_type": self.artifact_type,
                "content": content,
                "summary": "Generated artifact revision " + str(gen_index + 1) + " for " + task_id,
            }
            Draft7Validator(schema, format_checker=FormatChecker()).validate(artifact_payload)
            resp = self.invoke(messages, _canned_payload=artifact_payload, **kwargs)
            resp.parsed_json = artifact_payload
            return resp

        task_id = kwargs.get("task_id", "task_default12345")
        self.calls.append(dict(kwargs))
        now = _utc_now_iso()
        plan_payload = {
            "schema_version": "1.0.0",
            "plan_id": f"plan_{uuid.uuid4().hex[:12]}",
            "task_id": task_id,
            "goal_summary": f"Execute plan for {task_id} (revision {self.call_count + 1})",
            "revision": self.call_count + 1,
            "steps": [
                {
                    "step_id": "step_01",
                    "title": "Inspect and execute task",
                    "description": "Perform controlled workspace actions according to task specification",
                    "role": "worker",
                    "tool_dependencies": ["read_file", "write_file"],
                    "risk_level": kwargs.get("step_risk_level", "workspace_write"),
                    "complexity": kwargs.get("step_complexity", "low"),
                    "uncertainty": kwargs.get("step_uncertainty", "low"),
                    "rationale": "Standard controlled worker execution step",
                    "expected_outcome": "Artifact generated and ready for independent review",
                    "status": "pending",
                }
            ],
            "fallback_strategy": "Halt and escalate to orchestrator on error",
            "created_at": now,
        }
        Draft7Validator(schema, format_checker=FormatChecker()).validate(plan_payload)
        resp = self.invoke(messages, _canned_payload=plan_payload, **kwargs)
        resp.parsed_json = plan_payload
        return resp

    def propose_tool_requests(self, task: Dict[str, Any], turn_index: int = 0) -> List[Dict[str, Any]]:
        """Return tool requests for the given execution/repair turn."""
        if turn_index < len(self.tool_requests_by_turn):
            return self.tool_requests_by_turn[turn_index]
        if self.tool_requests_by_turn:
            return self.tool_requests_by_turn[-1]
        now = _utc_now_iso()
        target_files = (task.get("context") or {}).get("target_files") or ["docs/architecture-roadmap-v1.1.md"]
        return [
            {
                "schema_version": "1.0.0",
                "request_id": f"treq_{uuid.uuid4().hex[:12]}",
                "task_id": task["task_id"],
                "step_id": "step_01",
                "caller_role": "worker",
                "tool_name": "read_file",
                "parameters": {"path": target_files[0]},
                "timeout_ms": 5000,
                "risk_level": "read_only",
                "requires_human_approval": False,
                "created_at": now,
            }
        ]


class ScriptedReviewerAdapter(ModelAdapter):
    """Configurable Mock Reviewer supporting pass, revise_then_pass, always_revise, reject, needs_human, and faults."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        mode: str = "pass",
        tokens_per_call: Optional[int] = 100,
        simulated_latency_ms: int = 10,
    ):
        super().__init__(config or {"role": "reviewer", "model_id": "mock-muse-reviewer"})
        self.mode = mode
        self.tokens_per_call = tokens_per_call
        self.simulated_latency_ms = simulated_latency_ms
        self.call_count = 0
        self.calls = []

    def get_role(self) -> str:
        return "reviewer"

    def check_health(self) -> Dict[str, Any]:
        if self.mode in {"unavailable", "missing_credential"}:
            return {"healthy": False, "mock": True, "error": "Reviewer unavailable"}
        return {"healthy": True, "mock": True, "error": None}

    def invoke(self, messages: List[Dict[str, str]], **kwargs) -> AdapterResponse:
        self.call_count += 1
        if self.mode == "missing_credential":
            raise CredentialMissingError("Missing environment variable 'MUSE_API_KEY'")
        if self.mode == "timeout":
            raise AdapterError("Reviewer invocation timed out")
        if self.mode == "unavailable":
            raise AdapterError("Reviewer endpoint unavailable (HTTP 502)")

        payload = kwargs.get("_canned_payload") or {"verdict": "pass"}
        content = json.dumps(payload, ensure_ascii=False)
        p_tok = None if self.tokens_per_call is None else self.tokens_per_call // 2
        c_tok = None if self.tokens_per_call is None else self.tokens_per_call - (self.tokens_per_call // 2)
        return AdapterResponse(
            content=content,
            parsed_json=payload,
            model_id=self.model_id,
            tokens_prompt=p_tok,
            tokens_completion=c_tok,
            total_tokens=self.tokens_per_call,
            latency_ms=self.simulated_latency_ms,
            is_mock=True,
            raw_response={"mock": True, "call_count": self.call_count},
        )

    def invoke_structured(
        self,
        messages: List[Dict[str, str]],
        schema: Dict[str, Any],
        **kwargs
    ) -> AdapterResponse:
        self.calls.append(dict(kwargs))
        if self.mode == "invalid_schema":
            self.call_count += 1
            raise SchemaValidationError("Reviewer output does not match ReviewResult Schema")

        next_call_idx = self.call_count + 1
        if self.mode == "pass":
            verdict = "pass"
        elif self.mode == "revise_then_pass":
            verdict = "revise" if next_call_idx == 1 else "pass"
        elif self.mode == "always_revise":
            verdict = "revise"
        elif self.mode == "reject":
            verdict = "reject"
        elif self.mode == "needs_human":
            verdict = "needs_human"
        else:
            verdict = "pass"

        task_id = kwargs.get("task_id", "task_default12345")
        target_path = kwargs.get("target_artifact_path", "src/adapters.py")
        issues = []
        score = 95.0
        summary = "All acceptance criteria and safety constraints verified."

        if verdict != "pass":
            score = 55.0 if verdict == "revise" else 20.0
            summary = f"Review concluded with '{verdict}' due to identified defects."
            issues.append({
                "issue_id": f"iss_{next_call_idx:02d}",
                "severity": "p1_critical" if verdict == "reject" else "p2_major",
                "category": "correctness",
                "description": "Missing boundary validation in generated artifact",
                "evidence": f"{target_path}:10-25 lacks null/empty check",
                "file_path": target_path,
                "line_start": 1,
                "line_end": 1,
                "recommendation": "Add explicit guard clause and unit test",
            })

        review_payload = {
            "schema_version": "1.0.0",
            "review_id": f"rev_{uuid.uuid4().hex[:12]}",
            "task_id": task_id,
            "target_artifact_type": "code",
            "target_artifact_path": target_path,
            "verdict": verdict,
            "overall_score": score,
            "issues": issues,
            "summary": summary,
            "reviewer_role": self.model_id,
            "created_at": _utc_now_iso(),
        }
        review_payload.update(kwargs.get("review_metadata", {}))
        Draft7Validator(schema, format_checker=FormatChecker()).validate(review_payload)
        resp = self.invoke(messages, _canned_payload=review_payload, **kwargs)
        resp.parsed_json = review_payload
        return resp


class MockToolExecutor:
    """
    Controlled Mock Tool Executor.
    Executes schema-valid ToolRequests after RiskPolicyEngine approval,
    and supports fault injection (timeout, failure, mid-write interruption).
    """

    def __init__(self, mode: str = "normal"):
        self.mode = mode
        self.executed_requests: List[Dict[str, Any]] = []
        self.artifacts: Dict[str, str] = {}

    def snapshot_artifacts(self, paths):
        return {path: self.artifacts[path] for path in paths if path in self.artifacts}

    def execute(self, tool_request: Dict[str, Any]) -> Dict[str, Any]:
        req_id = tool_request["request_id"]
        task_id = tool_request["task_id"]
        tool_name = tool_request["tool_name"]
        now = _utc_now_iso()

        if self.mode == "interrupt_mid_write":
            raise SimulatedInterruptionError(
                f"Process crashed mid-execution of tool '{tool_name}' ({req_id}); side effect unconfirmed"
            )

        self.executed_requests.append(copy.deepcopy(tool_request))

        if self.mode == "timeout":
            return {
                "schema_version": "1.0.0",
                "result_id": f"tres_{uuid.uuid4().hex[:12]}",
                "request_id": req_id,
                "task_id": task_id,
                "tool_name": tool_name,
                "status": "timeout",
                "stdout": None,
                "stderr": "Tool execution exceeded timeout_ms",
                "structured_data": None,
                "execution_time_ms": tool_request.get("timeout_ms", 1000),
                "error_message": "Tool timed out",
                "created_at": now,
            }

        if self.mode == "fail":
            return {
                "schema_version": "1.0.0",
                "result_id": f"tres_{uuid.uuid4().hex[:12]}",
                "request_id": req_id,
                "task_id": task_id,
                "tool_name": tool_name,
                "status": "failed",
                "stdout": "",
                "stderr": "Simulated non-zero exit code 1",
                "structured_data": None,
                "execution_time_ms": 15,
                "error_message": "Tool execution failed",
                "created_at": now,
            }

        path = tool_request.get("parameters", {}).get("path")
        if path:
            if tool_name in {"write_file", "edit_file", "create_file", "apply_patch"}:
                self.artifacts[path] = tool_request.get("parameters", {}).get("content", "Mock artifact fixture\n")
            else:
                self.artifacts.setdefault(path, "Mock artifact fixture\n")
        return {
            "schema_version": "1.0.0",
            "result_id": f"tres_{uuid.uuid4().hex[:12]}",
            "request_id": req_id,
            "task_id": task_id,
            "tool_name": tool_name,
            "status": "success",
            "stdout": f"Mock executed {tool_name} successfully",
            "stderr": None,
            "structured_data": {"executed_tool": tool_name, "parameters": tool_request.get("parameters")},
            "execution_time_ms": 8,
            "error_message": None,
            "created_at": now,
        }
