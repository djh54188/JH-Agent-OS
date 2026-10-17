"""JH-Agent OS production tool executor for the N1 artifact-entry path.

JH-004 / ADR-0004: the production generation path has NO tools. Every
model-proposed tool request is denied, nothing executes, and no tool success
can ever contribute to task success.

This is deliberately NOT the MockToolExecutor: mock executors return simulated
successes that must never appear on the production path.
"""

from datetime import datetime, timezone
import uuid
from typing import Any, Dict, List


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class NoToolExecutor:
    """Production executor that denies every tool request.

    Denied requests are recorded in `denied_requests` (never in an
    `executed_requests` list: nothing here ever executes). Artifact snapshots
    are always empty: production generate-mode artifacts live in the
    control-owned ArtifactStore (mem://), never in tool-produced files.
    """

    def __init__(self) -> None:
        self.denied_requests: List[Dict[str, Any]] = []

    def snapshot_artifacts(self, paths) -> Dict[str, str]:
        return {}

    def execute(self, tool_request: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = tool_request["tool_name"]
        self.denied_requests.append({
            "request_id": tool_request["request_id"],
            "task_id": tool_request["task_id"],
            "tool_name": tool_name,
        })
        message = (
            f"production generate path has no tools: '{tool_name}' denied "
            f"(request {tool_request['request_id']})"
        )
        return {
            "schema_version": "1.0.0",
            "result_id": f"tres_{uuid.uuid4().hex[:12]}",
            "request_id": tool_request["request_id"],
            "task_id": tool_request["task_id"],
            "tool_name": tool_name,
            "status": "denied_by_policy",
            "stdout": None,
            "stderr": message,
            "structured_data": {"production_no_tool": True},
            "execution_time_ms": 0,
            "error_message": message,
            "created_at": _utc_now_iso(),
        }
