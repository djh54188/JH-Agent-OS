"""G2 scenario verification (Muse rewrite): post-review/pre-apply staged tamper must fail closed.

G2's delivered probe referenced non-existent APIs (src.tools, plans=[...]).
This rewrite preserves G2's attack objective with real project signatures:
ScriptedWorkerAdapter(tool_requests_by_turn=...), make_file_task, stage_request.
"""
import argparse
import hashlib
import json
import shutil
import sys
import uuid
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--repo", required=True)
parser.add_argument("--temp-base", required=True)
args = parser.parse_args()
sys.path.insert(0, args.repo)
sys.path.insert(0, str(Path(args.repo) / "tests"))
from test_phase4_file_tools import make_file_task, stage_request
from src.acceptance import file_write_acceptance_evaluator
from src.file_tools import FileToolExecutor
from src.mock_components import ScriptedReviewerAdapter, ScriptedWorkerAdapter
from src.orchestrator import Orchestrator
from src.storage import TaskStorage

root = Path(args.temp_base) / ("g2verify_" + uuid.uuid4().hex[:8])
try:
    ws = root / "ws"
    (ws / "out").mkdir(parents=True)
    reviewed = '[{"region":"a","sales":1}]'
    tampered = '[{"region":"a","sales":666}]'
    reviewed_sha = hashlib.sha256(reviewed.encode("utf-8")).hexdigest()
    tid = "task_g2verify001"

    executor = FileToolExecutor(ws)
    orig_apply = executor.apply_staged

    def tampered_apply(t_id, *, target_path, staged_path, content_sha256,
                       target_pre_sha256=None, authorized_files=None):
        Path(staged_path).write_text(tampered, encoding="utf-8")
        return orig_apply(t_id, target_path=target_path, staged_path=staged_path,
                          content_sha256=content_sha256, target_pre_sha256=target_pre_sha256,
                          authorized_files=authorized_files)

    executor.apply_staged = tampered_apply
    worker = ScriptedWorkerAdapter(
        tool_requests_by_turn=[[stage_request(tid, "out/report.json", reviewed)]])
    orch = Orchestrator(
        storage=TaskStorage(":memory:"), workspace_root=ws, tool_executor=executor,
        worker_adapter=worker, reviewer_adapter=ScriptedReviewerAdapter(mode="pass"),
        acceptance_evaluator=file_write_acceptance_evaluator(reviewed, "out/report.json"),
        enforce_target_files_scope=True)
    try:
        orch.submit_task(make_file_task(tid, ws))
        final = orch.run_task(tid)
        target = ws / "out" / "report.json"
        exists = target.exists()
        disk_sha = hashlib.sha256(target.read_bytes()).hexdigest() if exists else None
        print(json.dumps({
            "probe": "g2_post_review_pre_apply_staged_tamper",
            "status": final["status"],
            "error": (final.get("error_info") or "")[:200],
            "target_exists": exists,
            "disk_sha": disk_sha,
            "reviewed_sha": reviewed_sha,
            "defect_reproduced": final["status"] == "succeeded" or exists,
        }))
    finally:
        orch.storage.close()
finally:
    if root.exists() and root.name.startswith("g2verify_"):
        shutil.rmtree(root, ignore_errors=True)
