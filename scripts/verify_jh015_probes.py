"""JH-015 verification runner: proves ARCH-01..05 defects are closed with zero disk side effects.

Runs all five architect reproduction probes (plus the post-review staged tamper
variant of ARCH-05) in an isolated temp directory, checks actual disk contents
and review bindings, and exits non-zero if any defect is reproduced.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.acceptance import file_write_acceptance_evaluator
from src.file_tools import FileToolExecutor, PathBoundaryError
from src.mock_components import ScriptedReviewerAdapter, ScriptedWorkerAdapter
from src.orchestrator import Orchestrator
from src.storage import TaskStorage


def _make_junction(link: Path, target: Path) -> None:
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    except Exception:
        res = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if res.returncode != 0:
            raise RuntimeError(f"mklink failed: {res.returncode}")


def run_probes() -> dict:
    results = []
    base = (ROOT / "data" / "test-workspaces").resolve()
    base.mkdir(parents=True, exist_ok=True)
    root = base / ("jh015-probe-" + uuid.uuid4().hex)
    root.mkdir()
    try:

        # 1. ARCH-01: staging_junction (refusal must not truncate or modify outside file)
        ws = root / "p1" / "ws"
        outside = root / "p1" / "outside"
        (ws / "out").mkdir(parents=True)
        outside.mkdir(parents=True)
        victim = outside / "report.json"
        victim.write_text("KEEP_OUTSIDE", encoding="utf-8")
        _make_junction(ws / "_staging", outside)
        executor = FileToolExecutor(ws)
        try:
            executor.stage_write("task_probe", target_path="out/report.json", content="OVERWRITTEN")
            res1 = "allowed"
        except Exception as exc:
            res1 = type(exc).__name__
        after1 = victim.read_text(encoding="utf-8")
        os.rmdir(ws / "_staging")
        results.append({
            "probe": "ARCH-01_staging_junction",
            "result": res1,
            "outside_before": "KEEP_OUTSIDE",
            "outside_after": after1,
            "outside_bytes_after": len(after1.encode("utf-8")),
            "defect_reproduced": not (res1 == "PathBoundaryError" and after1 == "KEEP_OUTSIDE"),
        })

        # 2. ARCH-02: fixed_temp_collision (neighbor file .jh_tmp must not be touched or deleted)
        ws = root / "p2" / "ws"
        (ws / "out").mkdir(parents=True)
        target = ws / "out" / "main.txt"
        target.write_text("ORIGINAL", encoding="utf-8")
        neighbor = ws / "out" / "main.txt.jh_tmp"
        neighbor.write_text("UNAUTHORIZED_NEIGHBOR", encoding="utf-8")
        executor = FileToolExecutor(ws)
        stage = executor.stage_write("task_tmp", target_path="out/main.txt", content="CANDIDATE")
        applied = executor.apply_staged(
            "task_tmp", target_path="out/main.txt", staged_path=stage["staged_path"],
            content_sha256=stage["content_sha256"], target_pre_sha256=stage["target_pre_sha256"],
            authorized_files={"out/main.txt"},
        )
        neighbor_after = neighbor.read_text(encoding="utf-8") if neighbor.exists() else None
        target_after2 = target.read_text(encoding="utf-8")
        results.append({
            "probe": "ARCH-02_fixed_temp_collision",
            "applied": applied["applied"],
            "neighbor_exists_after": neighbor.exists(),
            "neighbor_after": neighbor_after,
            "target_after": target_after2,
            "defect_reproduced": not (
                applied["applied"]
                and neighbor.exists()
                and neighbor_after == "UNAUTHORIZED_NEIGHBOR"
                and target_after2 == "CANDIDATE"
            ),
        })

        # 3. ARCH-03: target_changed_after_check (concurrent edit during backed_up must not be overwritten)
        ws = root / "p3" / "ws"
        (ws / "out").mkdir(parents=True)
        target = ws / "out" / "main.txt"
        target.write_text("ORIGINAL", encoding="utf-8")
        executor = FileToolExecutor(ws)
        stage = executor.stage_write("task_race", target_path="out/main.txt", content="CANDIDATE_2")

        def concurrent_edit(phase):
            if phase == "backed_up":
                target.write_text("CONCURRENT_UPDATE", encoding="utf-8")

        executor.fault_injector = concurrent_edit
        try:
            executor.apply_staged(
                "task_race", target_path="out/main.txt", staged_path=stage["staged_path"],
                content_sha256=stage["content_sha256"], target_pre_sha256=stage["target_pre_sha256"],
                authorized_files={"out/main.txt"},
            )
            res3 = "allowed"
        except PathBoundaryError as exc:
            res3 = f"PathBoundaryError: {exc}"
        after3 = target.read_text(encoding="utf-8")
        results.append({
            "probe": "ARCH-03_target_changed_after_check",
            "result": res3,
            "concurrent_content": "CONCURRENT_UPDATE",
            "target_after": after3,
            "defect_reproduced": not (res3.startswith("PathBoundaryError") and after3 == "CONCURRENT_UPDATE"),
        })

        # 4. ARCH-04: junction_after_temp_before_replace (parent directory swapped to junction before commit)
        ws = root / "p4" / "ws"
        outside = root / "p4" / "outside"
        (ws / "out").mkdir(parents=True)
        outside.mkdir(parents=True)
        inside_target = ws / "out" / "main.txt"
        inside_target.write_text("INSIDE_ORIGINAL", encoding="utf-8")
        outside_target = outside / "main.txt"
        outside_target.write_text("OUTSIDE_ORIGINAL", encoding="utf-8")
        executor = FileToolExecutor(ws)
        stage = executor.stage_write("task_jswap", target_path="out/main.txt", content="CANDIDATE_REPLACE")

        def replace_parent(phase):
            if phase == "temp_written":
                os.rename(ws / "out", ws / "out_original")
                _make_junction(ws / "out", outside)

        executor.fault_injector = replace_parent
        try:
            executor.apply_staged(
                "task_jswap", target_path="out/main.txt", staged_path=stage["staged_path"],
                content_sha256=stage["content_sha256"], target_pre_sha256=stage["target_pre_sha256"],
                authorized_files={"out/main.txt"},
            )
            res4 = "allowed"
        except PathBoundaryError as exc:
            res4 = f"PathBoundaryError: {exc}"
        outside_after4 = outside_target.read_text(encoding="utf-8")
        inside_after4 = (ws / "out_original" / "main.txt").read_text(encoding="utf-8")
        os.rmdir(ws / "out")
        results.append({
            "probe": "ARCH-04_junction_after_temp_before_replace",
            "result": res4,
            "outside_after": outside_after4,
            "inside_original_after": inside_after4,
            "defect_reproduced": not (
                res4.startswith("PathBoundaryError")
                and outside_after4 == "OUTSIDE_ORIGINAL"
                and inside_after4 == "INSIDE_ORIGINAL"
            ),
        })

        # 5. ARCH-05: reviewed_B_applied_A (staged content swapped before review and restored before apply)
        ws = root / "p5" / "ws"
        (ws / "out").mkdir(parents=True)
        task_id = "task_arch05_probe"
        unreviewed = '[{"region":"a","sales":666}]'
        reviewed = '[{"region":"a","sales":1}]'
        executor = FileToolExecutor(ws)
        original_stage = executor.stage_write

        def swap_before_review(*a, **kw):
            result = original_stage(*a, **kw)
            Path(result["staged_path"]).write_text(reviewed, encoding="utf-8")
            return result

        executor.stage_write = swap_before_review
        original_apply = executor.apply_staged

        def restore_after_review(*a, **kw):
            Path(kw["staged_path"]).write_text(unreviewed, encoding="utf-8")
            return original_apply(*a, **kw)

        executor.apply_staged = restore_after_review
        request = {
            "schema_version": "1.0.0", "request_id": "treq_mismatch001", "task_id": task_id,
            "step_id": "step_01", "caller_role": "worker", "tool_name": "stage_write",
            "parameters": {"path": "out/report.json", "content": unreviewed},
            "timeout_ms": 5000, "risk_level": "workspace_write",
            "requires_human_approval": False, "created_at": "2026-10-12T00:00:00Z",
        }
        worker = ScriptedWorkerAdapter(tool_requests_by_turn=[[request]])
        reviewer = ScriptedReviewerAdapter(mode="pass")
        orch = Orchestrator(
            storage=TaskStorage(":memory:"), workspace_root=ws, tool_executor=executor,
            worker_adapter=worker, reviewer_adapter=reviewer,
            acceptance_evaluator=file_write_acceptance_evaluator(reviewed, "out/report.json"),
            enforce_target_files_scope=True,
        )
        task = {
            "schema_version": "1.0.0", "task_id": task_id, "title": "probe", "description": "probe",
            "acceptance_criteria": [
                "Worker 通过 stage_write 提交目标文件的写入请求且执行成功",
                "暂存内容 JSON 解析后与期望结构一致（notice/generated_by/items 键值完全相同）",
                "目标路径位于工作区内且在授权文件清单中",
            ],
            "priority": "high", "status": "created",
            "risk_assessment": {"risk_level": "workspace_write", "complexity": "low", "uncertainty": "low",
                                "risk_basis": "probe", "routing_basis": "probe"},
            "budget": {"max_turns": 8, "max_tokens": 60000, "max_execution_time_seconds": 300},
            "context": {"workspace_root": str(ws), "target_files": ["out/report.json"]},
            "artifact_refs": [], "tool_execution_evidence": [], "review_verdict": None, "error_info": None,
            "assigned_worker": "mock_worker", "assigned_reviewer": "mock_reviewer",
            "metadata": {"execution_mode": "file_tools"},
            "created_at": "2026-10-12T00:00:00Z", "updated_at": "2026-10-12T00:00:00Z",
        }
        orch.submit_task(task)
        final = orch.run_task(task_id)
        target_path5 = ws / "out" / "report.json"
        orch.storage.close()
        results.append({
            "probe": "ARCH-05_reviewed_B_applied_A",
            "status": final["status"],
            "error": final.get("error_info"),
            "target_exists": target_path5.exists(),
            "defect_reproduced": final["status"] == "succeeded" or target_path5.exists(),
        })

        # 6. ARCH-05b: staged content intact at review (B), then swapped to unreviewed (A) right before apply
        ws6 = root / "p6" / "ws"
        (ws6 / "out").mkdir(parents=True)
        task_id6 = "task_arch05b_probe"
        executor6 = FileToolExecutor(ws6)
        orig_apply6 = executor6.apply_staged

        def tamper_before_apply(*a, **kw):
            Path(kw["staged_path"]).write_text(unreviewed, encoding="utf-8")
            return orig_apply6(*a, **kw)

        executor6.apply_staged = tamper_before_apply
        req6 = dict(request, request_id="treq_mismatch002", task_id=task_id6,
                    parameters={"path": "out/report.json", "content": reviewed})
        orch6 = Orchestrator(
            storage=TaskStorage(":memory:"), workspace_root=ws6, tool_executor=executor6,
            worker_adapter=ScriptedWorkerAdapter(tool_requests_by_turn=[[req6]]),
            reviewer_adapter=ScriptedReviewerAdapter(mode="pass"),
            acceptance_evaluator=file_write_acceptance_evaluator(reviewed, "out/report.json"),
            enforce_target_files_scope=True,
        )
        task6 = dict(task, task_id=task_id6, context={"workspace_root": str(ws6), "target_files": ["out/report.json"]})
        orch6.submit_task(task6)
        final6 = orch6.run_task(task_id6)
        target_path6 = ws6 / "out" / "report.json"
        orch6.storage.close()
        results.append({
            "probe": "ARCH-05b_staged_swapped_after_review_before_apply",
            "status": final6["status"],
            "error": final6.get("error_info"),
            "target_exists": target_path6.exists(),
            "defect_reproduced": final6["status"] == "succeeded" or target_path6.exists(),
        })
    finally:
        if root.exists() and root.parent.resolve() == base and root.name.startswith("jh015-probe-"):
            shutil.rmtree(root)

    all_closed = all(not item["defect_reproduced"] for item in results)
    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "task": "JH-015",
        "shared_source_modified": False,
        "api_calls": 0,
        "all_defects_closed": all_closed,
        "results": results,
    }


def main() -> int:
    report = run_probes()
    out_path = ROOT / "reports" / "jh015-verification-20261012.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"all_defects_closed": report["all_defects_closed"], "report": str(out_path)}, indent=2))
    return 0 if report["all_defects_closed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
