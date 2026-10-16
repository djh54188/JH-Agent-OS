"""Independent variant probes for ARCH-01..05. No API calls, no shared-source writes.

Each variant attacks the SAME invariant as the architect probe but through a
different window/level, plus two association observations (staging-name
collision, repair-with-different-content). All file objects live under one
unique temp root on D:; 'outside' means a sibling dir of the mock workspace.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

REPO = r"D:\JH-Agent OS"
sys.path.insert(0, REPO)
sys.path.insert(0, str(Path(REPO) / "tests"))
from src.file_tools import FileToolExecutor, PathBoundaryError  # noqa: E402

BASE = Path(REPO) / ".test-tmp"
BASE.mkdir(parents=True, exist_ok=True)
ROOT = BASE / ("review-variant-" + uuid.uuid4().hex)
ROOT.mkdir()
LINKS = []
OUT = []


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def junction(link, target):
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True, text=True, encoding="gbk", errors="replace")
    if proc.returncode:
        raise RuntimeError("junction setup failed: " + proc.stderr)
    LINKS.append(link)


def ws_new(label):
    ws = ROOT / label
    (ws / "out").mkdir(parents=True)
    return ws


def rec(name, **kw):
    OUT.append({"variant": name, **kw})


try:
    # ---- V1: junction one level deeper (task staging dir), plus dir-creation side effect
    ws = ws_new("v1-taskdir-junction")
    outside = ROOT / "v1-outside"
    (outside / "task_v1").mkdir(parents=True)
    victim = outside / "task_v1" / "note.txt"
    victim.write_text("KEEP_V1", encoding="utf-8")
    (ws / "_staging").mkdir()
    junction(ws / "_staging" / "task_v1", outside / "task_v1")
    ex = FileToolExecutor(ws)
    try:
        ex.stage_write("task_v1", target_path="out/note.txt", content="CAND")
        outcome = "success"
    except Exception as e:
        outcome = type(e).__name__
    rec("V1-taskdir-junction-truncates-outside", outcome=outcome,
        outside_after=victim.read_text(encoding="utf-8"),
        outside_bytes=victim.stat().st_size,
        invariant_holds=(victim.read_text(encoding="utf-8") == "KEEP_V1"))

    # V1b: directory creation itself follows a junction (mkdir side effect)
    ws = ws_new("v1b-mkdir-follows-junction")
    outside = ROOT / "v1b-outside"
    outside.mkdir()
    junction(ws / "_staging", outside)
    ex = FileToolExecutor(ws)
    try:
        ex.stage_write("task_newdir", target_path="out/n.txt", content="x")
        outcome = "success"
    except Exception as e:
        outcome = type(e).__name__
    rec("V1b-mkdir-creates-outside-dir", outcome=outcome,
        outside_taskdir_created=(outside / "task_newdir").exists(),
        invariant_holds=not (outside / "task_newdir").exists())

    # ---- V2: backup file silently overwritten (shutil.copy2, no exclusivity check)
    ws = ws_new("v2-backup-collision")
    target = ws / "out" / "doc.txt"
    target.write_text("ORIG_DOC", encoding="utf-8")
    ex = FileToolExecutor(ws)
    stage = ex.stage_write("task_bk", target_path="out/doc.txt", content="NEWDOC")
    planted = Path(stage["staged_path"]).with_name("backup_doc.txt")
    planted.write_text("SENTINEL_BACKUP", encoding="utf-8")
    applied = ex.apply_staged("task_bk", target_path="out/doc.txt",
        staged_path=stage["staged_path"], content_sha256=stage["content_sha256"],
        target_pre_sha256=stage["target_pre_sha256"], authorized_files={"out/doc.txt"})
    rec("V2-preexisting-backup-overwritten", applied=applied["applied"],
        backup_after=planted.read_text(encoding="utf-8"),
        invariant_holds=(planted.read_text(encoding="utf-8") == "SENTINEL_BACKUP"))

    # ---- V3a: concurrent edit between temp-write and replace (later window)
    ws = ws_new("v3-late-window")
    target = ws / "out" / "m.txt"
    target.write_text("ORIG3", encoding="utf-8")
    ex = FileToolExecutor(ws)
    stage = ex.stage_write("task_w", target_path="out/m.txt", content="CAND3")
    def late_edit(phase):
        if phase == "temp_written":
            target.write_text("LATE_UPDATE", encoding="utf-8")
    ex.fault_injector = late_edit
    applied = ex.apply_staged("task_w", target_path="out/m.txt",
        staged_path=stage["staged_path"], content_sha256=stage["content_sha256"],
        target_pre_sha256=stage["target_pre_sha256"], authorized_files={"out/m.txt"})
    rec("V3a-concurrent-edit-after-temp-write", applied=applied["applied"],
        target_after=target.read_text(encoding="utf-8"),
        invariant_holds=(target.read_text(encoding="utf-8") == "LATE_UPDATE"))

    # ---- V3b: replayed apply with stale pre-state must refuse, not re-apply
    ws = ws_new("v3b-replay")
    target = ws / "out" / "r.txt"
    target.write_text("ORIG_R", encoding="utf-8")
    ex = FileToolExecutor(ws)
    stage = ex.stage_write("task_r", target_path="out/r.txt", content="CAND_R")
    first = ex.apply_staged("task_r", target_path="out/r.txt",
        staged_path=stage["staged_path"], content_sha256=stage["content_sha256"],
        target_pre_sha256=stage["target_pre_sha256"], authorized_files={"out/r.txt"})
    try:
        ex.apply_staged("task_r", target_path="out/r.txt",
            staged_path=stage["staged_path"], content_sha256=stage["content_sha256"],
            target_pre_sha256=stage["target_pre_sha256"], authorized_files={"out/r.txt"})
        replay = "applied-again"
    except Exception as e:
        replay = type(e).__name__
    rec("V3b-replayed-apply-refused", first_applied=first["applied"], replay_outcome=replay,
        target_after=target.read_text(encoding="utf-8"),
        invariant_holds=(replay != "applied-again"))

    # ---- V4: parent swapped at apply_start (earliest post-check window)
    ws = ws_new("v4-early-window")
    target = ws / "out" / "m.txt"
    target.write_text("INSIDE_V4", encoding="utf-8")
    outside = ROOT / "v4-outside"
    outside.mkdir()
    o_target = outside / "m.txt"
    o_target.write_text("OUTSIDE_V4", encoding="utf-8")
    ex = FileToolExecutor(ws)
    stage = ex.stage_write("task_e", target_path="out/m.txt", content="CAND_V4")
    def early_swap(phase):
        if phase == "apply_start":
            os.rename(ws / "out", ws / "out_saved")
            junction(ws / "out", outside)
    ex.fault_injector = early_swap
    try:
        applied = ex.apply_staged("task_e", target_path="out/m.txt",
            staged_path=stage["staged_path"], content_sha256=stage["content_sha256"],
            target_pre_sha256=stage["target_pre_sha256"], authorized_files={"out/m.txt"})
        v4_outcome = "applied"
    except Exception as e:
        v4_outcome = type(e).__name__ + ": " + str(e)[:80]
    rec("V4-parent-swap-at-apply-start", outcome=v4_outcome,
        outside_after=o_target.read_text(encoding="utf-8"),
        inside_after=(ws / "out_saved" / "m.txt").read_text(encoding="utf-8"),
        invariant_holds=(o_target.read_text(encoding="utf-8") == "OUTSIDE_V4"))

    # ---- V5: repair that re-stages DIFFERENT content (binding vs availability)
    from test_phase4_file_tools import make_file_task, stage_request  # noqa: E402
    from src.orchestrator import Orchestrator  # noqa: E402
    from src.acceptance import file_write_acceptance_evaluator  # noqa: E402
    from src.mock_components import ScriptedWorkerAdapter, ScriptedReviewerAdapter  # noqa: E402
    ws = ws_new("v5-repair-differs")
    tid = "task_v5repair0001"
    content_a = '[{"region":"a","sales":100}]'
    content_b = '[{"region":"a","sales":1}]'
    worker = ScriptedWorkerAdapter(tool_requests_by_turn=[
        [stage_request(tid, "out/report.json", content_a)],
        [stage_request(tid, "out/report.json", content_b)]])
    orch = Orchestrator(workspace_root=ws, tool_executor=FileToolExecutor(ws),
        worker_adapter=worker, reviewer_adapter=ScriptedReviewerAdapter(mode="revise_then_pass"),
        acceptance_evaluator=file_write_acceptance_evaluator(content_b, "out/report.json"),
        enforce_target_files_scope=True)
    try:
        orch.submit_task(make_file_task(tid, ws, expected_content=content_b))
        result = orch.run_task(tid)
        bindings = orch.storage.list_review_bindings(tid)
        disk = (ws / "out" / "report.json").read_text(encoding="utf-8") \
            if (ws / "out" / "report.json").exists() else None
        last = bindings[-1] if bindings else None
        snap = list(last["artifacts"].values())[0] if last else None
        rec("V5-repair-restage-different-content", status=result["status"],
            error=(result.get("error_info") or "")[:120],
            disk_content=disk, reviewed_content=snap.get("content") if snap else None,
            disk_eq_reviewed=(disk == snap.get("content")) if (disk and snap) else None,
            invariant_holds=(result["status"] == "succeeded" and disk == content_b
                             and snap.get("content") == content_b) if snap else False)
    finally:
        orch.storage.close()

    # ---- V6 (observation): two targets sharing one staged basename collide
    ws = ws_new("v6-staged-basename")
    (ws / "out" / "a").mkdir(parents=True)
    (ws / "out" / "b").mkdir(parents=True)
    ex = FileToolExecutor(ws)
    s1 = ex.stage_write("task_c", target_path="out/a/r.json", content='{"which":"A"}')
    s2 = ex.stage_write("task_c", target_path="out/b/r.json", content='{"which":"B"}')
    rec("V6-same-basename-staged-path-collision",
        staged1=s1["staged_path"], staged2=s2["staged_path"],
        same_path=(s1["staged_path"] == s2["staged_path"]),
        disk_content=Path(s2["staged_path"]).read_text(encoding="utf-8"),
        invariant_holds=(s1["staged_path"] != s2["staged_path"]))
finally:
    for link in reversed(LINKS):
        if os.path.lexists(link):
            os.rmdir(link)
    if ROOT.parent.resolve() != BASE or not ROOT.name.startswith("review-variant-"):
        raise RuntimeError("unsafe cleanup target")
    shutil.rmtree(ROOT)

print(json.dumps({"shared_source_modified": False, "api_calls": 0,
                  "temp_root_cleaned": True, "variants": OUT},
                 ensure_ascii=False, indent=2))
