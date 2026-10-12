"""
JH-Agent OS Control-Owned Acceptance Verifiers v1.1.0 (Phase 3 + Phase 4 batch 1)
Acceptance is judged by the control layer against the ACTUAL artifact content,
never by Worker self-scores. Verifiers are deterministic and task-type specific.
"""

import hashlib
import json
import os
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


def file_write_acceptance_evaluator(expected_content: str, expected_target: str) -> Callable:
    """Control-owned acceptance evaluator for Phase 4 file-write tasks.

    Unlike the Phase 3 data-transform evaluator, this judges the real staged
    write chain through the control-owned tool execution records. Declared
    canonical criteria (a task using this evaluator must declare exactly these
    three, in order):
      1. A successful stage_write for the expected target exists (evidence ref
         cites the recorded tool execution in tool_execution_evidence).
      2. The staged content decodes to exactly the JSON structure the control
         layer computes independently from its own expected content (semantic
         equality; byte formatting may vary between models). The review binding
         additionally snapshots the staged file, and validate_review proves the
         content unchanged through review.
      3. The target path is relative (inside the workspace by contract) and
         equals an authorized target_files entry.
    Any other criterion count/order fail-closes.
    """

    def evaluate(task: Dict[str, Any], tool_executions: List[Dict[str, Any]], artifact_store=None) -> List[Dict[str, Any]]:
        criteria = task["acceptance_criteria"]
        if len(criteria) != 3:
            return [{"criterion": c, "passed": False, "evidence": []} for c in criteria]

        expected_sha = hashlib.sha256(expected_content.encode("utf-8")).hexdigest()
        stage_results = [t for t in tool_executions
                         if t["tool_name"] == "stage_write" and t["status"] == "success"]
        target_norm = os.path.normcase(expected_target).replace("\\", "/")
        matching = [t for t in stage_results
                    if os.path.normcase((t["result"]["structured_data"] or {}).get("target_path", ""))
                    .replace("\\", "/").endswith(target_norm)]
        stage_evidence = [f"{t['request_id']}:{t['tool_name']}:success" for t in matching]

        # Semantic equality: the CURRENT reviewed staged content must decode to
        # exactly the expected JSON structure. The reviewed set for this turn
        # (task artifact_refs) selects which stage record is judged; history
        # from earlier repair turns must not shadow it.
        content_ok = False
        judged = None
        reviewed_refs = set(task.get("artifact_refs") or [])
        for t in reversed(matching):
            staged_path_rel = (t["result"]["structured_data"] or {}).get("staged_path")
            if staged_path_rel in reviewed_refs:
                judged = t
                break
        if judged is None and matching and not reviewed_refs:
            judged = matching[-1]
        if judged is not None:
            staged_path_rel = judged["result"]["structured_data"].get("staged_path")
            if artifact_store is not None and staged_path_rel:
                try:
                    snap_value = artifact_store.snapshot_artifacts([staged_path_rel]).get(staged_path_rel)
                except (KeyError, ValueError):
                    snap_value = None
                # Control-guards snapshots wrap {content, sha256}; tool executors
                # return plain strings. Accept both shapes.
                if isinstance(snap_value, dict):
                    staged_text = snap_value.get("content")
                else:
                    staged_text = snap_value
                if staged_text is not None:
                    try:
                        produced_norm = json.dumps(json.loads(staged_text),
                                                   ensure_ascii=False, sort_keys=True)
                        expected_norm = json.dumps(json.loads(expected_content),
                                                   ensure_ascii=False, sort_keys=True)
                        content_ok = produced_norm == expected_norm
                    except ValueError:
                        content_ok = False

        target_files = (task.get("context") or {}).get("target_files") or []
        authorized = any(
            os.path.normcase(expected_target).replace("\\", "/") == os.path.normcase(a).replace("\\", "/")
            for a in target_files)
        within_scope = not os.path.isabs(expected_target)

        return [
            {"criterion": criteria[0], "passed": bool(stage_evidence), "evidence": stage_evidence},
            {"criterion": criteria[1], "passed": content_ok,
             "evidence": stage_evidence if content_ok else [],
             "note": "semantic JSON equality against the control-owned expected structure"},
            {"criterion": criteria[2], "passed": authorized and within_scope,
             "evidence": [f"target_path:{expected_target}"] if (authorized and within_scope) else []},
        ]

    return evaluate
