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
