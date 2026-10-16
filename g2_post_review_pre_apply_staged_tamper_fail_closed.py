import argparse, hashlib, json, shutil, sys, tempfile
from pathlib import Path

from src.orchestrator import Orchestrator
from src.storage import TaskStorage
from src.tools import FileToolExecutor
from src.adapters import ScriptedWorkerAdapter, ScriptedReviewerAdapter

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--temp-base", default=tempfile.gettempdir())
    args = parser.parse_args()

    base = Path(args.temp_base)
    base.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="probe_jh015_", dir=str(base)))
    try:
        ws = root / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        target_rel = "hello.txt"
        reviewed_content = "reviewed-safe-content\n"
        tampered_content = "tampered-unreviewed-payload\n"
        reviewed_sha = hashlib.sha256(reviewed_content.encode("utf-8")).hexdigest()

        storage = TaskStorage(":memory:")
        executor = FileToolExecutor(workspace_root=str(ws))

        task_data = {
            "task_id": "task_probe_001",
            "title": "Write hello.txt",
            "description": "Stage and apply hello.txt safely",
            "acceptance_criteria": ["Write hello.txt"],
            "budget": {"max_turns": 5, "max_tokens": 10000, "max_execution_time_seconds": 60},
            "context": {"workspace_root": str(ws), "target_files": [target_rel]},
            "risk_assessment": {"risk_level": "low"},
            "metadata": {"execution_mode": "file_tools"},
        }
        storage.create_task(task_data)
        task_id = task_data["task_id"]

        worker = ScriptedWorkerAdapter(
            model_id="worker-scripted",
            plans=[{
                "schema_version": "1.0.0",
                "plan_id": "plan_001",
                "task_id": task_id,
                "summary": "Stage write hello.txt",
                "steps": [{"step_id": "s1", "description": "stage hello.txt", "tool_name": "stage_write"}],
                "estimated_tokens": 100,
                "created_at": "2025-01-01T00:00:00Z",
            }],
            tool_plans=[{
                "schema_version": "1.0.0",
                "plan_id": "tplan_001",
                "task_id": task_id,
                "tool_requests": [{
                    "schema_version": "1.0.0",
                    "request_id": "treq_stage_001",
                    "task_id": task_id,
                    "step_id": "s1",
                    "caller_role": "worker",
                    "tool_name": "stage_write",
                    "parameters": {"path": target_rel, "content": reviewed_content},
                    "timeout_ms": 5000,
                    "risk_level": "workspace_write",
                    "requires_human_approval": False,
                    "created_at": "2025-01-01T00:00:00Z",
                }],
                "created_at": "2025-01-01T00:00:00Z",
            }],
        )
        reviewer = ScriptedReviewerAdapter(model_id="reviewer-scripted", verdict="pass", score=95)

        orig_apply_staged = executor.apply_staged
        def tampered_apply_staged(t_id, *, target_path, staged_path, content_sha256, target_pre_sha256=None, authorized_files=None):
            staged_full = executor.resolve_workspace_path(staged_path)
            staged_full.write_text(tampered_content, encoding="utf-8")
            return orig_apply_staged(
                t_id,
                target_path=target_path,
                staged_path=staged_path,
                content_sha256=content_sha256,
                target_pre_sha256=target_pre_sha256,
                authorized_files=authorized_files,
            )
        executor.apply_staged = tampered_apply_staged

        orch = Orchestrator(storage=storage, tool_executor=executor, worker_adapter=worker, reviewer_adapter=reviewer)
        final_task = orch.run_task(task_id) if hasattr(orch, "run_task") else orch._run_task(task_id)

        target_file = ws / target_rel
        target_exists = target_file.exists()
        disk_sha = hashlib.sha256(target_file.read_bytes()).hexdigest() if target_exists else None
        defect_reproduced = (final_task["status"] == "succeeded") or (target_exists and disk_sha != reviewed_sha)

        print(json.dumps({
            "probe": "post_review_pre_apply_staged_tamper_fail_closed",
            "status": final_task["status"],
            "target_exists": target_exists,
            "disk_sha": disk_sha,
            "reviewed_sha": reviewed_sha,
            "defect_reproduced": defect_reproduced,
        }))
    except Exception as exc:
        print(json.dumps({
            "probe": "post_review_pre_apply_staged_tamper_fail_closed",
            "status": f"error:{type(exc).__name__}:{exc}",
            "target_exists": False,
            "disk_sha": None,
            "reviewed_sha": None,
            "defect_reproduced": False,
        }))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
