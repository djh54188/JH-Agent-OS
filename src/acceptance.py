"""
JH-Agent OS Control-Owned Acceptance Verifiers v1.0.0 (Phase 3)
Acceptance is judged by the control layer against the ACTUAL artifact content,
never by Worker self-scores. Verifiers are deterministic and task-type specific.
"""

import json
from typing import Any, Callable, Dict, List, Optional


def _artifact_content(artifact_store, task_id: str) -> Optional[str]:
    latest = artifact_store.get_latest(task_id) if artifact_store else None
    return latest["content"] if latest else None


def transform_acceptance_evaluator(expected_output: Any, *, input_label: str = "input_data") -> Callable:
    """Build a control-owned evaluator for fixed-input data transformation tasks.

    The expected output is computed independently by the control layer from the
    declared input; the Worker's summary is never consulted. Each returned item
    cites the artifact evidence reference recorded by the orchestrator, so the
    evidence is traceable through task.tool_execution_evidence.

    Capability limit (declared per Phase 3 review finding): this evaluator judges
    exactly TWO criteria — acceptance_criteria[0] is "trusted input_data was
    provided" and acceptance_criteria[1] is "artifact content equals the expected
    output". Any criteria beyond the second are returned fail-closed
    (passed=False, evidence=[]) BY DESIGN; tasks that need three or more
    verifiable conditions must supply a different control-owned evaluator rather
    than reuse this one.
    """
    expected_text = expected_output if isinstance(expected_output, str) else json.dumps(expected_output, ensure_ascii=False, sort_keys=True)

    def evaluate(task: Dict[str, Any], tool_executions: List[Dict[str, Any]], artifact_store=None) -> List[Dict[str, Any]]:
        content = _artifact_content(artifact_store, task["task_id"])
        latest = artifact_store.get_latest(task["task_id"]) if artifact_store else None
        artifact_ref = (
            f"artifact:{latest['artifact_id']}@v{latest['version']}:sha256={latest['sha256']}"
            if latest else None
        )
        results = []
        input_present = bool((task.get("metadata") or {}).get(input_label) is not None)
        results.append({
            "criterion": task["acceptance_criteria"][0],
            "passed": input_present,
            "evidence": ["task_metadata:input_data"] if input_present else [],
        })
        if len(task["acceptance_criteria"]) > 1:
            criterion = task["acceptance_criteria"][1]
            if content is None:
                results.append({"criterion": criterion, "passed": False, "evidence": []})
            else:
                try:
                    produced = json.loads(content)
                    normalized = json.dumps(produced, ensure_ascii=False, sort_keys=True)
                    matches = normalized == expected_text
                except ValueError:
                    normalized = None
                    matches = False
                # Evidence cites the control-hashed artifact version recorded in
                # task.tool_execution_evidence; the normalized comparison is kept
                # as an unvalidated note for the audit record.
                results.append({
                    "criterion": criterion,
                    "passed": matches,
                    "evidence": [artifact_ref] if (matches and artifact_ref) else [],
                    "note": normalized[:256] if normalized is not None else f"unparseable:{content[:128]}",
                })
        # Any remaining criteria: fail-closed by declared capability limit (see
        # docstring) — a third+ condition is never silently judged "passed".
        for criterion in task["acceptance_criteria"][2:]:
            results.append({"criterion": criterion, "passed": False, "evidence": []})
        return results

    return evaluate
