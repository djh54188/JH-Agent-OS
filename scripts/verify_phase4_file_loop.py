"""Phase 4 batch 1: REAL Gemini worker + Muse reviewer + file tools closed loop.

Chain (all model calls real, no retries, original budget):
  real Gemini plans via ToolPlanSchemaV1 (proposes stage_write with content)
  -> control layer validates the plan and each request (schema, task
     correlation, per-call risk policy on actual parameters)
  -> FileToolExecutor stages the write inside the isolated test workspace
  -> real Muse independently reviews the STAGED file content
  -> control-owned file acceptance verifies authorization + content
  -> post-review apply re-confirms (content == reviewed, target version ==
     pre-state, path ownership, authorization) and atomically writes the
     final target inside the workspace
  -> succeeded, with SQLite-persisted versions/reviews/evidence.

Evidence: reports/phase4-file-loop-verification.json (full contents, hashes,
per-attempt usage, apply phases). Exit code non-zero on any failure.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jsonschema import Draft7Validator, FormatChecker
from src.acceptance import file_write_acceptance_evaluator
from src.control_guards import verify_closed_loop_success
from src.file_tools import FileToolExecutor
from src.model_config import load_model_adapter
from src.orchestrator import Orchestrator
from src.storage import TaskStorage


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    worker = load_model_adapter("worker")
    reviewer = load_model_adapter("reviewer")
    worker_health = worker.check_health()
    reviewer_health = reviewer.check_health()

    report = {
        "timestamp": utc_now(),
        "phase": 4,
        "batch": 1,
        "is_mock": False,
        "counts_as_acceptance_evidence": True,
        "worker": {"provider": worker.config.get("provider"), "api_protocol": worker.config.get("api_protocol"),
                   "model_id": worker.model_id, "healthy": worker_health.get("healthy")},
        "reviewer": {"provider": reviewer.config.get("provider"), "api_protocol": "responses",
                     "model_id": reviewer.model_id, "healthy": reviewer_health.get("healthy")},
    }
    if not (worker_health.get("healthy") and reviewer_health.get("healthy")):
        report.update({"status": "BLOCKED_ENDPOINT_UNAVAILABLE", "passed": False})
        out = ROOT / "reports" / "phase4-file-loop-verification.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": report["status"], "report": str(out)}))
        return 1

    # Isolated in-project test workspace (never project sources, never user files).
    ws = ROOT / "data" / "test-workspaces" / ("phase4-live-" + utc_now().replace(":", ""))
    (ws / "out").mkdir(parents=True, exist_ok=True)
    target = "out/notice.json"
    task_id = "task_p4_file_loop01"
    # Control-owned expectation for the acceptance evaluator: the exact object
    # the task instructs the worker to write (values verbatim from input_data).
    expected_object = {
        "notice": "JH-Agent OS phase 4 file tool loop",
        "generated_by": "gemini-3.8-flash-high via local proxy",
        "items": [1, 2, 3],
    }
    expected_content = json.dumps(expected_object, ensure_ascii=False, sort_keys=True)

    task = {
        "schema_version": "1.0.0",
        "task_id": task_id,
        "title": "Stage and apply a workspace notice file",
        "description": ("Propose a stage_write tool request whose content is EXACTLY the JSON "
                        "object given in input_data.notice_object (same keys, same values, same "
                        "array order); the control layer stages, reviews and applies it to the "
                        "authorized target."),
        "acceptance_criteria": [
            "Worker 通过 stage_write 提交目标文件的写入请求且执行成功",
            "暂存内容 JSON 解析后与期望结构一致（notice/generated_by/items 键值完全相同）",
            "目标路径位于工作区内且在授权文件清单中",
        ],
        "priority": "high",
        "status": "created",
        "risk_assessment": {
            "risk_level": "workspace_write", "complexity": "medium", "uncertainty": "low",
            "risk_basis": "Workspace-bounded staged file write; final apply after review",
            "routing_basis": "Phase 4 batch 1: real Gemini worker + real Muse reviewer + file tools",
        },
        "budget": {"max_turns": 8, "max_tokens": 120000, "max_execution_time_seconds": 300},
        "context": {"workspace_root": str(ws), "target_files": [target]},
        "artifact_refs": [],
        "tool_execution_evidence": [],
        "review_verdict": None,
        "error_info": None,
        "assigned_worker": "gemini_worker",
        "assigned_reviewer": "muse_reviewer",
        "metadata": {"execution_mode": "file_tools",
                     "input_data": {"notice_object": expected_object}},
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }

    db_path = ROOT / "data" / "phase4-file-loop.sqlite3"
    if db_path.exists():
        db_path.unlink()
    orchestrator = Orchestrator(
        storage=TaskStorage(db_path), workspace_root=ws,
        tool_executor=FileToolExecutor(ws),
        worker_adapter=worker, reviewer_adapter=reviewer,
        acceptance_evaluator=file_write_acceptance_evaluator(expected_content, target),
        enforce_target_files_scope=True,
    )
    orchestrator.submit_task(task)
    result = orchestrator.run_task(task_id)
    status = orchestrator.get_task_status(task_id)
    versions = orchestrator.storage.list_artifact_versions(task_id)
    bindings = orchestrator.storage.list_review_bindings(task_id)
    attempts = orchestrator.storage.list_model_attempts(task_id)
    gate_error = None
    try:
        # File tasks keep their artifact lineage in tool_executions (the staged
        # content hash recorded by the control layer), not in the mem lineage
        # table; construct the current artifact state from that record.
        stage_execs = [t for t in orchestrator.storage.list_tool_executions(task_id)
                       if t["tool_name"] == "stage_write" and t["status"] == "success"]
        current_artifact = None
        if stage_execs:
            latest_stage = stage_execs[-1]["result"]["structured_data"]
            current_artifact = {"sha256": latest_stage["content_sha256"], "version": len(stage_execs)}
        verify_closed_loop_success(result, current_artifact, bindings, status)
    except Exception as exc:
        gate_error = str(exc)
    orchestrator.storage.close()

    target_file = ws / "out" / "notice.json"
    target_content = target_file.read_text(encoding="utf-8") if target_file.exists() else None
    report.update({
        "status": "completed",
        "result": {
            "final_status": result["status"],
            "review_verdict": result.get("review_verdict"),
            "repair_count": status["repair_count"],
            "turns_used": status["turns_used"],
            "tokens_used": status["tokens_used"],
            "tokens_unknown_count": status["tokens_unknown_count"],
            "error_info": result.get("error_info"),
        },
        "workspace": str(ws),
        "target_file": {"path": target, "exists": target_file.exists(),
                        "content": target_content,
                        "sha256": __import__("hashlib").sha256(target_content.encode()).hexdigest() if target_content else None},
        "review_bindings": [
            {"review_id": b["review_id"], "verdict": b["review"]["verdict"],
             "issues": b["review"].get("issues", []), "acceptance": b["acceptance"]}
            for b in bindings
        ],
        "model_attempts": [
            {"attempt_id": a["attempt_id"], "actor_role": a["actor_role"], "status": a["status"]}
            for a in attempts
        ],
        "closed_loop_gate": gate_error or "passed",
        "checks": {
            "final_succeeded": result["status"] == "succeeded",
            "review_pass_bound": bool(bindings) and bindings[-1]["review"]["verdict"] == "pass",
            "target_file_created_with_reviewed_content": target_content is not None
                and __import__("hashlib").sha256(target_content.encode()).hexdigest() == (
                    list(bindings[-1]["artifacts"].values())[0]["sha256"] if bindings else None),
            "strict_closed_loop_gate": gate_error is None,
        },
    })
    report["passed"] = all(report["checks"].values())

    out = ROOT / "reports" / "phase4-file-loop-verification.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "passed": report["passed"],
        "final_status": result["status"],
        "review_verdict": result.get("review_verdict"),
        "tokens_used": status["tokens_used"],
        "target_exists": target_file.exists(),
        "checks": report["checks"],
        "report": str(out),
    }, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
