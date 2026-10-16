"""Control-owned review correlation and per-call budget calculations."""

import hashlib
import json

from src.adapters import AdapterError, SchemaValidationError


def input_reserve(messages, schema):
    # An explicit estimate, not a provider token measurement. Include schema injection
    # and formatting overhead. Unknown actual usage stops subsequent invocations.
    serialized = json.dumps(messages, ensure_ascii=False) + 2 * json.dumps(schema, ensure_ascii=False)
    return (len(serialized.encode("utf-8")) + 2) // 3 + 256


def call_limits(adapter, remaining_tokens, remaining_seconds, reserved_input):
    output_limit = min(
        int(getattr(adapter, "max_output_tokens", getattr(adapter, "max_tokens", 8192))),
        remaining_tokens - reserved_input,
    )
    timeout = min(float(getattr(adapter, "timeout_seconds", 300)), remaining_seconds)
    if output_limit <= 0 or timeout <= 0:
        raise AdapterError("Insufficient task budget for input reservation and output or timeout")
    return {"max_output_tokens": output_limit, "timeout": timeout}


def artifact_snapshot(executor, paths):
    if not paths or not hasattr(executor, "snapshot_artifacts"):
        raise SchemaValidationError("Artifact snapshot unavailable; review cannot prove current content")
    contents = executor.snapshot_artifacts(paths)
    if set(contents) != set(paths) or not all(isinstance(value, str) for value in contents.values()):
        raise SchemaValidationError("Artifact snapshot is incomplete")
    return {path: {"content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}
            for path, content in sorted(contents.items())}


def validate_review(review, metadata, before, after):
    for field in ("task_id", "review_id", "target_artifact_path", "reviewer_role"):
        if review.get(field) != metadata[field]:
            raise SchemaValidationError(f"Review correlation mismatch: {field}")
    if before != after:
        raise SchemaValidationError("Artifact changed during review; old SHA-256 cannot authorize success")
    if review["target_artifact_path"] not in before:
        raise SchemaValidationError("Review refers to a missing artifact")
    for issue in review["issues"]:
        start, end = issue.get("line_start"), issue.get("line_end")
        path = issue.get("file_path")
        if path is not None and path not in before:
            raise SchemaValidationError("Issue refers to a missing artifact")
        if start is not None or end is not None:
            if path not in before or start is None or end is None or start > end:
                raise SchemaValidationError("Invalid issue evidence line range")
            if end > len(before[path]["content"].splitlines()):
                raise SchemaValidationError("Issue line range exceeds artifact length")
        if review["verdict"] == "pass" and issue["severity"] in {"p0_blocker", "p1_critical", "p2_major"}:
            raise SchemaValidationError("Review pass contradicts unresolved blocking issue")


def validate_acceptance(task, results, evidence):
    if not isinstance(results, list) or len(results) != len(task["acceptance_criteria"]):
        raise SchemaValidationError("Required acceptance evidence is missing")
    for criterion, result in zip(task["acceptance_criteria"], results):
        if (not isinstance(result, dict) or result.get("criterion") != criterion
                or result.get("passed") is not True or not result.get("evidence")
                or any(ref not in evidence for ref in result["evidence"])):
            raise SchemaValidationError(f"Required acceptance condition failed or lacks evidence: {criterion}")


def verify_closed_loop_success(task, latest_artifact, bindings, runtime):
    """Independent re-verification, from persisted records only, that a task is a
    NORMAL closed-loop success. Raises SchemaValidationError otherwise.

    Rectification #1: evidence scripts must use this gate so a failed, blocked,
    or revise-ended run can never be reported as a passed closed loop. A normal
    success requires ALL of:
    - final status == 'succeeded' and current verdict == 'pass';
    - the latest review binding is a pass with no unresolved blocking issues;
    - the reviewed artifact snapshot matches the CURRENT artifact SHA-256;
    - acceptance evidence complete (every criterion passed with traceable refs);
    - no unknown token usage and no unconfirmed side effects.
    Fault scenarios (failed / needs_attention / revise / missing evidence) are
    reported separately and never satisfy this gate.
    """
    if task.get("status") != "succeeded":
        raise SchemaValidationError(
            f"closed-loop gate: final status is '{task.get('status')}', not 'succeeded'")
    if task.get("review_verdict") != "pass":
        raise SchemaValidationError("closed-loop gate: no current 'pass' review verdict")
    if runtime.get("tokens_unknown_count"):
        raise SchemaValidationError("closed-loop gate: unknown token usage unresolved")
    if runtime.get("has_unconfirmed_side_effect"):
        raise SchemaValidationError("closed-loop gate: unconfirmed side effect unresolved")
    if not bindings:
        raise SchemaValidationError("closed-loop gate: no review binding recorded")
    latest_binding = bindings[-1]
    review = latest_binding.get("review") or {}
    if review.get("verdict") != "pass":
        raise SchemaValidationError(
            f"closed-loop gate: latest review verdict is '{review.get('verdict')}', not 'pass'")
    for issue in review.get("issues") or []:
        if issue.get("severity") in {"p0_blocker", "p1_critical", "p2_major"}:
            raise SchemaValidationError("closed-loop gate: latest pass review carries unresolved blocking issues")
    acceptance = latest_binding.get("acceptance") or []
    criteria = task.get("acceptance_criteria") or []
    if len(acceptance) != len(criteria) or any(
            item.get("criterion") != criterion or item.get("passed") is not True or not item.get("evidence")
            for criterion, item in zip(criteria, acceptance)):
        raise SchemaValidationError("closed-loop gate: acceptance evidence missing or failed")
    if not latest_artifact:
        raise SchemaValidationError("closed-loop gate: no artifact version persisted")
    snapshot = latest_binding.get("artifacts") or {}
    if not snapshot or not any(
            isinstance(value, dict) and value.get("sha256") == latest_artifact.get("sha256")
            for value in snapshot.values()):
        raise SchemaValidationError(
            "closed-loop gate: latest review does not cover the current artifact version (stale SHA-256)")
