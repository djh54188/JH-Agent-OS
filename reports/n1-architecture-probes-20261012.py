"""Offline reproduction of JH-002 file-tool defects. No API calls or shared-source writes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

parser = argparse.ArgumentParser()
parser.add_argument("--repo", default=r"D:\JH-Agent OS")
parser.add_argument("--temp-base", default=str(Path(__file__).parent))
parser.add_argument("--report")
args = parser.parse_args()
sys.path.insert(0, args.repo)
from src.file_tools import FileToolExecutor

base = Path(args.temp_base).resolve()
base.mkdir(parents=True, exist_ok=True)
root = base / ("jh002-probe-" + uuid.uuid4().hex)
root.mkdir()
results = []
links = []

def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def junction(link, target):
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError("junction setup failed: " + proc.stderr)
    links.append(link)

def new_workspace(label):
    ws = root / label
    (ws / "out").mkdir(parents=True)
    return ws

try:
    ws = new_workspace("staging")
    outside = root / "outside-staging"
    (outside / "task_probe").mkdir(parents=True)
    victim = outside / "task_probe" / "notice.txt"
    before = "KEEP_OUTSIDE"
    victim.write_text(before, encoding="utf-8")
    junction(ws / "_staging", outside)
    executor = FileToolExecutor(ws)
    try:
        executor.stage_write("task_probe", target_path="out/notice.txt", content="APPROVED_LATER")
        outcome = "success"
    except Exception as exc:
        outcome = type(exc).__name__
    after = victim.read_text(encoding="utf-8")
    results.append({"probe": "staging_junction", "result": outcome, "outside_before": before,
                    "outside_after": after, "outside_bytes_after": victim.stat().st_size,
                    "defect_reproduced": outcome == "PathBoundaryError" and after == ""})

    ws = new_workspace("temp-collision")
    target = ws / "out" / "main.txt"
    target.write_text("ORIGINAL", encoding="utf-8")
    neighbor = ws / "out" / "main.txt.jh_tmp"
    before = "UNAUTHORIZED_NEIGHBOR"
    neighbor.write_text(before, encoding="utf-8")
    executor = FileToolExecutor(ws)
    stage = executor.stage_write("task_temp", target_path="out/main.txt", content="CANDIDATE")
    applied = executor.apply_staged("task_temp", target_path="out/main.txt",
        staged_path=stage["staged_path"], content_sha256=stage["content_sha256"],
        target_pre_sha256=stage["target_pre_sha256"], authorized_files={"out/main.txt"})
    results.append({"probe": "fixed_temp_collision", "neighbor_before": before,
                    "neighbor_exists_after": neighbor.exists(),
                    "target_after": target.read_text(encoding="utf-8"), "applied": applied["applied"],
                    "defect_reproduced": applied["applied"] and not neighbor.exists()})

    ws = new_workspace("target-race")
    target = ws / "out" / "main.txt"
    target.write_text("ORIGINAL_2", encoding="utf-8")
    executor = FileToolExecutor(ws)
    stage = executor.stage_write("task_race", target_path="out/main.txt", content="CANDIDATE_2")
    def concurrent_edit(phase):
        if phase == "backed_up":
            target.write_text("CONCURRENT_UPDATE", encoding="utf-8")
    executor.fault_injector = concurrent_edit
    applied = executor.apply_staged("task_race", target_path="out/main.txt",
        staged_path=stage["staged_path"], content_sha256=stage["content_sha256"],
        target_pre_sha256=stage["target_pre_sha256"], authorized_files={"out/main.txt"})
    after = target.read_text(encoding="utf-8")
    results.append({"probe": "target_changed_after_check", "concurrent_content": "CONCURRENT_UPDATE",
                    "target_after": after, "applied": applied["applied"],
                    "defect_reproduced": applied["applied"] and after == "CANDIDATE_2"})

    ws = new_workspace("replace-junction")
    target = ws / "out" / "main.txt"
    target.write_text("INSIDE_ORIGINAL", encoding="utf-8")
    outside = root / "outside-replace"
    outside.mkdir()
    outside_target = outside / "main.txt"
    outside_target.write_text("OUTSIDE_ORIGINAL", encoding="utf-8")
    (outside / "main.txt.jh_tmp").write_text("CANDIDATE_REPLACE", encoding="utf-8")
    executor = FileToolExecutor(ws)
    stage = executor.stage_write("task_replace", target_path="out/main.txt", content="CANDIDATE_REPLACE")
    def replace_parent(phase):
        if phase == "temp_written":
            os.rename(ws / "out", ws / "out_original")
            junction(ws / "out", outside)
    executor.fault_injector = replace_parent
    applied = executor.apply_staged("task_replace", target_path="out/main.txt",
        staged_path=stage["staged_path"], content_sha256=stage["content_sha256"],
        target_pre_sha256=stage["target_pre_sha256"], authorized_files={"out/main.txt"})
    after = outside_target.read_text(encoding="utf-8")
    results.append({"probe": "junction_after_temp_before_replace", "outside_before": "OUTSIDE_ORIGINAL",
                    "outside_after": after, "inside_original_after": (ws / "out_original" / "main.txt").read_text(encoding="utf-8"),
                    "applied": applied["applied"], "defect_reproduced": applied["applied"] and after == "CANDIDATE_REPLACE"})

    # No policy modifications: run complete orchestration binding probe only on D.
    if root.drive.lower() == "d:":
        sys.path.insert(0, str(Path(args.repo) / "tests"))
        from test_phase4_file_tools import make_file_task, stage_request
        from src.orchestrator import Orchestrator
        from src.acceptance import file_write_acceptance_evaluator
        from src.mock_components import ScriptedWorkerAdapter, ScriptedReviewerAdapter
        ws = new_workspace("review-binding")
        tid = "task_binding_probe01"
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
        worker = ScriptedWorkerAdapter(tool_requests_by_turn=[[stage_request(tid, "out/report.json", unreviewed)]])
        orch = Orchestrator(workspace_root=ws, tool_executor=executor, worker_adapter=worker,
            reviewer_adapter=ScriptedReviewerAdapter(mode="pass"),
            acceptance_evaluator=file_write_acceptance_evaluator(reviewed, "out/report.json"),
            enforce_target_files_scope=True)
        try:
            orch.submit_task(make_file_task(tid, ws, expected_content=reviewed))
            result = orch.run_task(tid)
            bindings = orch.storage.list_review_bindings(tid)
            target = ws / "out" / "report.json"
            actual = target.read_text(encoding="utf-8") if target.exists() else None
            artifact = list(bindings[-1]["artifacts"].values())[0] if bindings else None
            results.append({"probe": "reviewed_B_applied_A", "status": result["status"],
                "error": result.get("error_info"), "target_content": actual,
                "reviewed_content": artifact.get("content") if artifact else None,
                "review_sha": artifact.get("sha256") if artifact else None,
                "target_sha": sha(actual) if actual else None, "policy_modified": False,
                "defect_reproduced": result["status"] == "succeeded" and actual == unreviewed
                    and artifact and artifact.get("content") == reviewed})
        finally:
            orch.storage.close()
    else:
        results.append({"probe": "reviewed_B_applied_A", "status": "not_run",
            "reason": "Production policy forbids C writes. Re-run with --temp-base D:\\JH-Agent OS\\.test-tmp; no policy shim in this script."})
finally:
    # Only remove junction entries we created, then the checked unique temp root.
    for link in reversed(links):
        if os.path.lexists(link):
            os.rmdir(link)
    if root.parent.resolve() != base or not root.name.startswith("jh002-probe-"):
        raise RuntimeError("unsafe cleanup target")
    shutil.rmtree(root)

report = {"repo": args.repo, "shared_source_modified": False, "api_calls": 0,
          "temp_root_cleaned": True, "results": results}
if args.report:
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))

