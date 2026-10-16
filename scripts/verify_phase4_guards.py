"""Mutation guard verification for Phase 4 batch 1 file tools (JH016-04 remediation).

Each mutation breaks one file-tool security guarantee in memory (no files on
disk are modified). Two independent signals decide the verdict:

1. The mapped unit test, run with the mutant class installed: it must turn
   red when the safety invariant actually breaks.
2. A behavior oracle that replays a minimal attack scenario with the mutant
   installed and judges the SAFETY INVARIANT directly from disk bytes, task
   state, and call counts -- never from error wording.

Caliber rules (no more bool(failures or errors)):

- A test ERROR (unexpected exception, import/assembly failure) is a harness
  error, never a detection.
- Test red + invariant broken -> detected (true positive).
- Test red + invariant held -> the failure is wording-only or a redundant
  defense's different message; classified redundant, acceptable only when
  pre-declared in EXPECTED_REDUNDANT.
- Test green + invariant broken -> survived test gap (real failure).
- Test green + invariant held -> redundant-defense survival, acceptable only
  when pre-declared in EXPECTED_REDUNDANT.

Class/exception assembly is synchronized across every consumer: a file_tools
mutant replaces FileToolExecutor AND PathBoundaryError on the live file_tools
module, the live orchestrator module, and the test module, so no path silently
falls back to the unmutated class.
"""

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

spec = importlib.util.spec_from_file_location("phase4_tests", ROOT / "tests/test_phase4_file_tools.py")
phase4_tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase4_tests)

import src.orchestrator as live_orch
import src.file_tools as live_ft
import src.acceptance as live_acc

MUTATIONS = [
    # Workspace containment (drive/prefix) removed -> out-of-bounds write succeeds.
    ("workspace_containment_removed", "src/file_tools.py",
     '                raise PathBoundaryError(f"path escapes workspace: \'{raw}\'")',
     "                pass",
     "test_out_of_bounds_paths_refused"),
    # Ancestor reparse (junction) check removed -> junction escape succeeds.
    ("junction_ancestor_check_removed", "src/file_tools.py",
     '            if _is_reparse_point(step):\n                raise PathBoundaryError(f"ancestor component is a link/junction: \'{step}\' (raw: \'{raw}\')")',
     "            pass",
     "test_real_junction_escape_refused"),
    # NOTE: the post-open realpath containment re-check is a compensating control
    # for the sub-millisecond check-to-open race window; it is NOT mutation-tested
    # because the pre-open final-component reparse check already deterministically
    # refuses the planted-junction scenario (see test_race_planted_junction_refused).
    # Apply target-version confirmation removed -> concurrent edit overwritten.
    ("apply_target_version_check_removed", "src/file_tools.py",
     '        if current_sha != target_pre_sha256:\n            raise PathBoundaryError(\n                f"apply refused: target changed since review (current={current_sha}, expected={target_pre_sha256})")',
     "        if False:\n            pass",
     "test_target_changed_between_review_and_apply"),
    # Apply staged-vs-reviewed hash confirmation removed -> tampered content applied.
    ("apply_staged_hash_check_removed", "src/file_tools.py",
     '        if staged_sha != content_sha256:\n            raise PathBoundaryError(\n                f"apply refused: staged content sha {staged_sha[:12]}... != reviewed {content_sha256[:12]}...")',
     "        if False:\n            pass",
     "test_apply_refuses_tampered_staged_content"),
    # Orchestrator apply gate removed -> revise verdict also applies the write.
    ("apply_without_pass_verdict", "src/orchestrator.py",
     '                if review_json["verdict"] == "pass":',
     '                if True:',
     "test_review_rejection_blocks_apply"),
    # ARCH-01: staging directory reparse check removed -> outside victim truncated/modified.
    ("arch01_staging_dir_reparse_removed", "src/file_tools.py",
     '                if _is_reparse_point(step):\n                    raise PathBoundaryError(f"ancestor component is a link/junction: \'{step}\'")',
     "                pass",
     "test_arch01_staging_junction_does_not_truncate_outside_file"),
    # ARCH-03: guard construction-time version check removed -> concurrent edit overwritten.
    # (JH016 design: the initial path check, the guard baseline check, and the
    # pre-publish guard reverify triple-cover the version invariant; removing
    # any single one is backstopped. Expected redundant survival.)
    ("arch03_guard_baseline_version_check_removed", "src/file_tools.py",
     '        if expected_pre_sha256 is not None and self.content_sha != expected_pre_sha256:\n            self.close()\n            raise PathBoundaryError(\n                f"apply refused: target changed since review "\n                f"(current={self.content_sha}, expected={expected_pre_sha256})")',
     "        if False:\n            pass",
     "test_arch03_concurrent_target_edit_during_backed_up_preserved"),
    # ARCH-05: staged snapshot vs stage_write record consistency check removed.
    ("arch05_staged_snapshot_binding_removed", "src/orchestrator.py",
     '                self._verify_staged_snapshots_match_tool_records(task_id, snapshots)',
     '                pass',
     "test_arch05_reviewed_content_mismatch_blocked_at_snapshot_and_apply"),
    # R1: workspace constructor reparse rejection removed -> junction workspace accepted.
    ("workspace_ctor_reparse_removed", "src/file_tools.py",
     '        if _is_reparse_point(raw_ws):\n            raise PathBoundaryError(f"workspace root is a link/junction: \'{raw_ws}\'")',
     "        pass",
     "test_junction_workspace_root_rejected"),
    # H1: unique staged naming removed -> same-basename second stage overwrites first.
    ("staged_unique_name_removed", "src/file_tools.py",
     '        staged_path = staging_root / f"{uuid.uuid4().hex}_{Path(target_path).name}"\n        fd = self._open_verified(staged_path, mode="create_exclusive")',
     '        staged_path = staging_root / Path(target_path).name\n        fd = self._open_verified(staged_path, mode="write")',
     "test_same_basename_targets_get_distinct_staged_files"),
    # NOTE: the no-fallback snapshot-entry check in _apply_staged_writes is a
    # second-layer defence: the pre-review _verify_staged_snapshots_match check
    # already guarantees every reviewed ref has a matching snapshot, so no
    # reachable single-point mutation there can turn a test red. It is covered
    # by code review (G2-F2) rather than a mutation guard.
    # G2-F2b: recovered 'applied' hash vs reviewed snapshot comparison removed.
    ("recovered_applied_hash_check_removed", "src/orchestrator.py",
     '                    if recovery["target_sha256"] != reviewed_sha:\n                        raise ApplyBlockedError(\n                            f"recovered target hash mismatch (disk={recovery[\'target_sha256\']}, "\n                            f"reviewed={reviewed_sha})")',
     '                    if False:\n                        pass',
     "test_recovered_applied_hash_must_match_reviewed_snapshot"),
    # Acceptance must judge the CURRENT reviewed stage record, not the first in history.
    ("acceptance_current_turn_judgment_removed", "src/acceptance.py",
     '        for t in reversed(matching):\n            staged_path_rel = (t["result"]["structured_data"] or {}).get("staged_path")\n            if staged_path_rel in reviewed_refs:\n                judged = t\n                break',
     '        judged = matching[0]',
     "test_stale_turn_staged_write_does_not_block_current_apply"),
    # JH016-01: pre-commit temp hash check removed -> tampered temp published.
    ("temp_precommit_hash_check_removed", "src/file_tools.py",
     '            if _sha256_file(temp_path) != content_sha256:\n                raise PathBoundaryError("apply refused: temp file changed before commit")',
     "            if False:\n                pass",
     "test_temp_tamper_before_commit_refused"),
    # B2-02: parent directory share weakened -> rename-away + recreate at the
    # replace entry succeeds and the concurrent version is overwritten.
    ("dir_guard_share_weakened", "src/file_tools.py",
     '        handle = k32.CreateFileW(str(directory), GENERIC_READ, FILE_SHARE_READ,',
     '        handle = k32.CreateFileW(str(directory), GENERIC_READ, 0x00000007,',
     "test_replace_entry_rename_blocked"),
    # JH016-03: file guard share weakened -> an external write lands mid-span
    # and the guarded apply must refuse instead of succeeding.
    ("file_guard_share_weakened", "src/file_tools.py",
     '        FILE_SHARE_READ_ONLY = 0x00000001',
     '        FILE_SHARE_READ_ONLY = 0x00000007',
     "test_guarded_external_write_blocked"),
]


# Mutations whose survival is EXPECTED because an independent redundant defense
# still enforces the invariant. Survival (or wording-only red) here is honest
# evidence of defense in depth, not a test gap. Any mutation NOT listed here
# must be detected with the invariant actually broken.
EXPECTED_REDUNDANT = {
    "workspace_containment_removed":
        "Absolute-path precheck removed, but the component walk and the post-open "
        "realpath containment check still refuse; outside bytes never land.",
    "junction_ancestor_check_removed":
        "Walk-time ancestor check removed, but the pre-open reparse check and the "
        "realpath containment check still refuse with a different message.",
    "apply_target_version_check_removed":
        "Initial path-based version check removed, but the guard baseline check "
        "and the pre-publish guard reverify still refuse; concurrent bytes kept.",
    "apply_staged_hash_check_removed":
        "First staged-hash comparison removed, but the candidate re-read check "
        "still refuses with a different message; tampered bytes never applied.",
    "arch01_staging_dir_reparse_removed":
        "Staging-dir walk check removed, but verified open + realpath containment "
        "still refuse before any outside write.",
    "arch03_guard_baseline_version_check_removed":
        "Guard baseline check removed, but the initial path check and the "
        "pre-publish guard reverify still refuse; concurrent bytes kept.",
    "recovered_applied_hash_check_removed":
        "Recovered-hash comparison removed, but the success-evidence gate still "
        "blocks the task from succeeding with unreviewed bytes.",
}


def _make_junction(link: Path, target: Path) -> None:
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True)
    if proc.returncode != 0:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))


def _oracle_ws(name: str) -> Path:
    return phase4_tests.make_ws("mut-oracle-%s-%s" % (name, uuid.uuid4().hex[:8]))


def _cleanup_ws(ws: Path) -> None:
    shutil.rmtree(ws, ignore_errors=True)
    # make_ws nests under data/test-workspaces; remove sibling probe dirs too.
    for sibling in ws.parent.glob(ws.name + "-*"):
        shutil.rmtree(sibling, ignore_errors=True)


def _oracle_workspace_containment(name):
    """Invariant: no out-of-bounds bytes staged or written."""
    ws = _oracle_ws(name)
    try:
        sibling = ws.parent / (ws.name + "-oob")
        sibling.mkdir(exist_ok=True)
        ex = phase4_tests.FileToolExecutor(ws)
        tid = "task_oracle_oob_%s" % uuid.uuid4().hex[:8]
        result = ex.execute(phase4_tests.stage_request(tid, str(sibling / "x.txt"), "x"))
        outside_files = list(sibling.rglob("*"))
        unsafe = result["status"] == "success" or len(outside_files) > 0
        return unsafe, "status=%s outside_files=%d" % (result["status"], len(outside_files))
    finally:
        _cleanup_ws(ws)


def _oracle_junction_ancestor(name):
    """Invariant: bytes through a junction ancestor never land outside."""
    ws = _oracle_ws(name)
    try:
        outside = ws.parent / (ws.name + "-outside")
        outside.mkdir(exist_ok=True)
        _make_junction(ws / "link", outside)
        ex = phase4_tests.FileToolExecutor(ws)
        tid = "task_oracle_junc_%s" % uuid.uuid4().hex[:8]
        result = ex.execute(phase4_tests.stage_request(tid, "link/evil.txt", "x"))
        leftovers = list(outside.rglob("*"))
        unsafe = result["status"] == "success" or len(leftovers) > 0
        return unsafe, "status=%s outside_files=%d" % (result["status"], len(leftovers))
    finally:
        link = ws / "link"
        if link.exists() and os.path.islink(link):
            os.rmdir(link)
        _cleanup_ws(ws)


def _oracle_apply_target_version(name):
    """Invariant: a concurrent target edit is never overwritten by a stale apply."""
    ws = _oracle_ws(name)
    try:
        target = ws / "out" / "main.txt"
        target.write_text("ORIGINAL", encoding="utf-8")
        ex = phase4_tests.FileToolExecutor(ws)
        tid = "task_oracle_ver_%s" % uuid.uuid4().hex[:8]
        st = ex.stage_write(tid, target_path="out/main.txt", content="CANDIDATE")
        target.write_text("CONCURRENT_UPDATE", encoding="utf-8")
        try:
            ex.apply_staged(
                tid, target_path="out/main.txt", staged_path=st["staged_path"],
                content_sha256=st["content_sha256"], target_pre_sha256=st["target_pre_sha256"],
                authorized_files={"out/main.txt"})
            applied = True
        except Exception:
            applied = False
        after = target.read_text(encoding="utf-8")
        unsafe = applied or after != "CONCURRENT_UPDATE"
        return unsafe, "applied=%s target=%r" % (applied, after)
    finally:
        _cleanup_ws(ws)


def _oracle_apply_staged_hash(name):
    """Invariant: staged bytes tampered after review are never applied."""
    ws = _oracle_ws(name)
    try:
        target = ws / "out" / "main.txt"
        target.write_text("ORIGINAL", encoding="utf-8")
        ex = phase4_tests.FileToolExecutor(ws)
        tid = "task_oracle_tamper_%s" % uuid.uuid4().hex[:8]
        st = ex.stage_write(tid, target_path="out/main.txt", content="CANDIDATE")
        Path(st["staged_path"]).write_text("TAMPERED", encoding="utf-8")
        try:
            ex.apply_staged(
                tid, target_path="out/main.txt", staged_path=st["staged_path"],
                content_sha256=st["content_sha256"], target_pre_sha256=st["target_pre_sha256"],
                authorized_files={"out/main.txt"})
            applied = True
        except Exception:
            applied = False
        after = target.read_text(encoding="utf-8")
        unsafe = applied or after != "ORIGINAL"
        return unsafe, "applied=%s target=%r" % (applied, after)
    finally:
        _cleanup_ws(ws)


def _oracle_without_pass_verdict(name):
    """Invariant: no apply happens unless the verdict for this turn is pass."""
    import sys as _sys
    _sys.path.insert(0, str(ROOT))
    from src.storage import TaskStorage
    ws = _oracle_ws(name)
    storage = TaskStorage(":memory:")
    try:
        # Expected content mirrors the phase-4 file task fixture.
        expected = json.dumps([{"region": "a", "sales": 1}], ensure_ascii=False)
        tid = "task_oracle_gate_%s" % uuid.uuid4().hex[:8]
        worker = phase4_tests.ScriptedWorkerAdapter(tool_requests_by_turn=[
            [phase4_tests.stage_request(tid, "out/report.json", expected)]])
        reviewer = phase4_tests.ScriptedReviewerAdapter(mode="always_revise")
        orch = phase4_tests.Orchestrator(
            storage=storage, workspace_root=ws,
            tool_executor=phase4_tests.FileToolExecutor(ws),
            worker_adapter=worker, reviewer_adapter=reviewer,
            acceptance_evaluator=phase4_tests.file_write_acceptance_evaluator(
                expected, "out/report.json"),
            enforce_target_files_scope=True)
        orch.submit_task(phase4_tests.make_file_task(tid, ws))
        result = orch.run_task(tid)
        target = ws / "out" / "report.json"
        unsafe = target.exists()
        return unsafe, "status=%s target_exists=%s" % (result["status"], target.exists())
    finally:
        storage.close()
        _cleanup_ws(ws)


def _oracle_arch01(name):
    """Invariant: a junction _staging never lets stage content reach outside."""
    ws = _oracle_ws(name)
    try:
        outside = ws.parent / (ws.name + "-outside")
        outside.mkdir(exist_ok=True)
        victim = outside / "report.json"
        victim.write_text("KEEP_OUTSIDE", encoding="utf-8")
        _make_junction(ws / "_staging", outside)
        ex = phase4_tests.FileToolExecutor(ws)
        try:
            ex.stage_write("task_oracle_arch01", target_path="out/report.json",
                           content="OVERWRITTEN")
            refused = False
        except Exception:
            refused = True
        unsafe = victim.read_text(encoding="utf-8") != "KEEP_OUTSIDE"
        return unsafe, "refused=%s victim=%r" % (refused, victim.read_text(encoding="utf-8"))
    finally:
        link = ws / "_staging"
        if link.exists() or os.path.lexists(str(link)):
            try:
                os.rmdir(link)
            except OSError:
                pass
        _cleanup_ws(ws)


def _oracle_arch03(name):
    """Invariant: an edit landing during backed_up is never overwritten."""
    ws = _oracle_ws(name)
    try:
        target = ws / "out" / "main.txt"
        target.write_text("ORIGINAL", encoding="utf-8")
        ex = phase4_tests.FileToolExecutor(ws)
        tid = "task_oracle_arch03_%s" % uuid.uuid4().hex[:8]
        st = ex.stage_write(tid, target_path="out/main.txt", content="CANDIDATE_2")
        ex.fault_injector = lambda phase: (
            target.write_text("CONCURRENT_UPDATE", encoding="utf-8") if phase == "backed_up" else None)
        try:
            ex.apply_staged(
                tid, target_path="out/main.txt", staged_path=st["staged_path"],
                content_sha256=st["content_sha256"], target_pre_sha256=st["target_pre_sha256"],
                authorized_files={"out/main.txt"})
            applied = True
        except Exception:
            applied = False
        after = target.read_text(encoding="utf-8")
        unsafe = applied or after != "CONCURRENT_UPDATE"
        return unsafe, "applied=%s target=%r" % (applied, after)
    finally:
        _cleanup_ws(ws)


def _oracle_arch05(name):
    """Invariant: a staged file swapped between tool record and review is not admitted."""
    from src.storage import TaskStorage
    ws = _oracle_ws(name)
    storage = TaskStorage(":memory:")
    try:
        unreviewed = '[{"region":"a","sales":666}]'
        reviewed = '[{"region":"a","sales":1}]'
        ex = phase4_tests.FileToolExecutor(ws)
        orig_stage = ex.stage_write

        def swap_before_review(*a, **kw):
            r = orig_stage(*a, **kw)
            Path(r["staged_path"]).write_text(reviewed, encoding="utf-8")
            return r

        ex.stage_write = swap_before_review
        tid = "task_oracle_a05_%s" % uuid.uuid4().hex[:8]
        worker = phase4_tests.ScriptedWorkerAdapter(tool_requests_by_turn=[
            [phase4_tests.stage_request(tid, "out/report.json", unreviewed)]])
        reviewer = phase4_tests.ScriptedReviewerAdapter(mode="pass")
        orch = phase4_tests.Orchestrator(
            storage=storage, workspace_root=ws, tool_executor=ex,
            worker_adapter=worker, reviewer_adapter=reviewer,
            acceptance_evaluator=phase4_tests.file_write_acceptance_evaluator(
                reviewed, "out/report.json"),
            enforce_target_files_scope=True)
        orch.submit_task(phase4_tests.make_file_task(tid, ws))
        result = orch.run_task(tid)
        target = ws / "out" / "report.json"
        unsafe = reviewer.call_count > 0 or target.exists()
        return unsafe, "status=%s reviewer_calls=%d target_exists=%s" % (
            result["status"], reviewer.call_count, target.exists())
    finally:
        storage.close()
        _cleanup_ws(ws)


def _oracle_workspace_ctor(name):
    """Invariant: a junction workspace root is never accepted."""
    ws = _oracle_ws(name)
    try:
        real = ws.parent / (ws.name + "-real")
        real.mkdir(exist_ok=True)
        link = ws.parent / (ws.name + "-link")
        _make_junction(link, real)
        try:
            phase4_tests.FileToolExecutor(link)
            constructed = True
        except Exception:
            constructed = False
        return constructed, "constructed=%s" % constructed
    finally:
        link = ws.parent / (ws.name + "-link")
        if link.exists() or os.path.lexists(str(link)):
            try:
                os.rmdir(link)
            except OSError:
                pass
        _cleanup_ws(ws)


def _oracle_staged_unique_name(name):
    """Invariant: same-basename stages never share one staged file."""
    ws = _oracle_ws(name)
    try:
        (ws / "out" / "sub").mkdir(parents=True, exist_ok=True)
        ex = phase4_tests.FileToolExecutor(ws)
        tid = "task_oracle_h1_%s" % uuid.uuid4().hex[:8]
        first = ex.stage_write(tid, target_path="out/dup.txt", content="FIRST")
        second = ex.stage_write(tid, target_path="out/sub/dup.txt", content="SECOND")
        same_path = first["staged_path"] == second["staged_path"]
        first_lost = Path(first["staged_path"]).read_text(encoding="utf-8") != "FIRST"
        unsafe = same_path or first_lost
        return unsafe, "same_path=%s first_lost=%s" % (same_path, first_lost)
    finally:
        _cleanup_ws(ws)


def _oracle_recovered_hash(name):
    """Invariant: a recovered apply with unreviewed bytes never reports success."""
    from src.mock_components import SimulatedInterruptionError
    from src.storage import TaskStorage
    ws = _oracle_ws(name)
    storage = TaskStorage(":memory:")
    try:
        expected = json.dumps([{"region": "a", "sales": 1}], ensure_ascii=False)
        ex = phase4_tests.FileToolExecutor(ws)
        orig_recover = ex.recover_apply

        def lying_recover(*a, **kw):
            verdict = orig_recover(*a, **kw)
            if verdict["state"] == "not_applied":
                (ws / "out" / "report.json").write_text("OUTSIDER_BYTES", encoding="utf-8")
                import hashlib as _hl
                return {"state": "applied",
                        "target_sha256": _hl.sha256(b"OUTSIDER_BYTES").hexdigest()}
            return verdict

        def interrupt_once(phase):
            if phase == "temp_written" and not interrupt_once.fired:
                interrupt_once.fired = True
                raise SimulatedInterruptionError("drill: crash at temp_written")
        interrupt_once.fired = False
        ex.fault_injector = interrupt_once
        ex.recover_apply = lying_recover
        tid = "task_oracle_rec_%s" % uuid.uuid4().hex[:8]
        worker = phase4_tests.ScriptedWorkerAdapter(tool_requests_by_turn=[
            [phase4_tests.stage_request(tid, "out/report.json", expected)]])
        orch = phase4_tests.Orchestrator(
            storage=storage, workspace_root=ws, tool_executor=ex,
            worker_adapter=worker,
            reviewer_adapter=phase4_tests.ScriptedReviewerAdapter(mode="pass"),
            acceptance_evaluator=phase4_tests.file_write_acceptance_evaluator(
                expected, "out/report.json"),
            enforce_target_files_scope=True)
        orch.submit_task(phase4_tests.make_file_task(tid, ws))
        result = orch.run_task(tid)
        unsafe = result["status"] == "succeeded"
        return unsafe, "status=%s" % result["status"]
    finally:
        storage.close()
        _cleanup_ws(ws)


def _oracle_acceptance_current_turn(name):
    """Invariant: the current reviewed turn judges acceptance, history ignored."""
    from src.storage import TaskStorage
    ws = _oracle_ws(name)
    storage = TaskStorage(":memory:")
    try:
        expected = json.dumps([{"region": "a", "sales": 1}], ensure_ascii=False)
        stale = '{"stale":true}'
        tid = "task_oracle_acc_%s" % uuid.uuid4().hex[:8]
        worker = phase4_tests.ScriptedWorkerAdapter(tool_requests_by_turn=[
            [phase4_tests.stage_request(tid, "out/report.json", stale)],
            [phase4_tests.stage_request(tid, "out/report.json", expected)],
        ])
        reviewer = phase4_tests.ScriptedReviewerAdapter(mode="revise_then_pass")
        orch = phase4_tests.Orchestrator(
            storage=storage, workspace_root=ws,
            tool_executor=phase4_tests.FileToolExecutor(ws),
            worker_adapter=worker, reviewer_adapter=reviewer,
            acceptance_evaluator=phase4_tests.file_write_acceptance_evaluator(
                expected, "out/report.json"),
            enforce_target_files_scope=True)
        orch.submit_task(phase4_tests.make_file_task(tid, ws))
        result = orch.run_task(tid)
        target = ws / "out" / "report.json"
        applied_current = target.exists() and target.read_text(encoding="utf-8") == expected
        unsafe = result["status"] != "succeeded" or not applied_current
        return unsafe, "status=%s applied_current=%s" % (result["status"], applied_current)
    finally:
        storage.close()
        _cleanup_ws(ws)


def _oracle_temp_precommit_hash(name):
    """Invariant: temp bytes tampered after temp_written are never published."""
    ws = _oracle_ws(name)
    try:
        target = ws / "out" / "main.txt"
        target.write_text("ORIGINAL", encoding="utf-8")
        ex = phase4_tests.FileToolExecutor(ws)
        tid = "task_oracle_tmp_%s" % uuid.uuid4().hex[:8]
        st = ex.stage_write(tid, target_path="out/main.txt", content="CANDIDATE")

        def tamper_temp(phase):
            if phase == "temp_written":
                staging_task_dir = Path(st["staged_path"]).parent
                for tmp in staging_task_dir.glob(".tmp_*"):
                    tmp.write_bytes(b"TAMPERED_TEMP_BYTES")

        ex.fault_injector = tamper_temp
        try:
            ex.apply_staged(
                tid, target_path="out/main.txt", staged_path=st["staged_path"],
                content_sha256=st["content_sha256"], target_pre_sha256=st["target_pre_sha256"],
                authorized_files={"out/main.txt"})
            applied = True
        except Exception:
            applied = False
        after = target.read_bytes()
        unsafe = applied or after != b"ORIGINAL"
        return unsafe, "applied=%s target=%r" % (applied, after)
    finally:
        _cleanup_ws(ws)


def _oracle_dir_guard_weakened(name):
    """Invariant: a rename-away + recreate at the replace entry fails loudly;
    the concurrent version is never overwritten by a stale publish."""
    ws = _oracle_ws(name)
    try:
        target = ws / "out" / "main.txt"
        target.write_text("ORIGINAL", encoding="utf-8")
        ex = phase4_tests.FileToolExecutor(ws)
        tid = "task_oracle_dg_%s" % uuid.uuid4().hex[:8]
        st = ex.stage_write(tid, target_path="out/main.txt", content="CANDIDATE")
        probe = {}
        real_replace = os.replace

        def replace_boundary(src, dst):
            if Path(dst) == target and not probe:
                child_code = (
                    "from pathlib import Path; import sys; p = Path(sys.argv[1]); "
                    "p.rename(p.with_name('previous-object.json')); "
                    "p.write_text('CONCURRENT_NEW', encoding='utf-8')")
                child = subprocess.run(
                    [sys.executable, "-X", "utf8", "-B", "-c", child_code, str(target)],
                    capture_output=True, text=True, encoding="utf-8", timeout=30)
                probe["rc"] = child.returncode
            return real_replace(src, dst)

        with patch.object(os, "replace", replace_boundary):
            try:
                ex.apply_staged(
                    tid, target_path="out/main.txt", staged_path=st["staged_path"],
                    content_sha256=st["content_sha256"], target_pre_sha256=st["target_pre_sha256"],
                    authorized_files={"out/main.txt"})
                applied = True
            except Exception:
                applied = False
        after = target.read_bytes()
        unsafe = probe.get("rc") == 0 and after == b"CANDIDATE"
        return unsafe, "applied=%s child_rc=%s target=%r" % (applied, probe.get("rc"), after)
    finally:
        _cleanup_ws(ws)


def _oracle_file_guard_weakened(name):
    """Invariant: an external write opened mid-span fails loudly and the
    guarded apply does not report success with unreviewed interference."""
    ws = _oracle_ws(name)
    try:
        target = ws / "out" / "main.txt"
        target.write_text("ORIGINAL", encoding="utf-8")
        ex = phase4_tests.FileToolExecutor(ws)
        tid = "task_oracle_fg_%s" % uuid.uuid4().hex[:8]
        st = ex.stage_write(tid, target_path="out/main.txt", content="CANDIDATE")
        probe = {}

        def external_write(phase):
            if phase != "guarded":
                return
            child_code = (
                "import os, sys; fd = os.open(sys.argv[1], os.O_RDWR | getattr(os, 'O_BINARY', 0)); "
                "os.lseek(fd, 1, os.SEEK_SET); os.write(fd, b'Z'); os.fsync(fd); os.close(fd)")
            child = subprocess.run(
                [sys.executable, "-X", "utf8", "-B", "-c", child_code, str(target)],
                capture_output=True, text=True, encoding="utf-8", timeout=30)
            probe["rc"] = child.returncode

        ex.fault_injector = external_write
        try:
            ex.apply_staged(
                tid, target_path="out/main.txt", staged_path=st["staged_path"],
                content_sha256=st["content_sha256"], target_pre_sha256=st["target_pre_sha256"],
                authorized_files={"out/main.txt"})
            applied = True
        except Exception:
            applied = False
        unsafe = probe.get("rc") == 0
        return unsafe, "applied=%s child_rc=%s" % (applied, probe.get("rc"))
    finally:
        _cleanup_ws(ws)


ORACLES = {
    "workspace_containment_removed": _oracle_workspace_containment,
    "junction_ancestor_check_removed": _oracle_junction_ancestor,
    "apply_target_version_check_removed": _oracle_apply_target_version,
    "apply_staged_hash_check_removed": _oracle_apply_staged_hash,
    "apply_without_pass_verdict": _oracle_without_pass_verdict,
    "arch01_staging_dir_reparse_removed": _oracle_arch01,
    "arch03_guard_baseline_version_check_removed": _oracle_arch03,
    "arch05_staged_snapshot_binding_removed": _oracle_arch05,
    "workspace_ctor_reparse_removed": _oracle_workspace_ctor,
    "staged_unique_name_removed": _oracle_staged_unique_name,
    "recovered_applied_hash_check_removed": _oracle_recovered_hash,
    "acceptance_current_turn_judgment_removed": _oracle_acceptance_current_turn,
    "temp_precommit_hash_check_removed": _oracle_temp_precommit_hash,
    "dir_guard_share_weakened": _oracle_dir_guard_weakened,
    "file_guard_share_weakened": _oracle_file_guard_weakened,
}


def _find_test(test_name):
    for cls in (phase4_tests.TestPhase4FileLoop, phase4_tests.TestFileToolExecutor,
                getattr(phase4_tests, "TestJH016Integrity", ())):
        if cls != () and hasattr(cls, test_name):
            return cls(test_name)
    raise RuntimeError("test not found: " + test_name)


def _last_lines(traceback_text, count=5):
    lines = (traceback_text or "").strip().splitlines()
    return lines[-count:]


def main():
    records = []
    for name, rel_path, original, replacement, test_name in MUTATIONS:
        src_file = ROOT / rel_path
        source = src_file.read_text(encoding="utf-8")
        if source.count(original) != 1:
            raise RuntimeError(f"Mutation target ambiguous or missing: {name}")
        mutant_src = source.replace(original, replacement)

        mod_name = "src." + Path(rel_path).stem
        mutant = types.ModuleType(mod_name)
        mutant.__file__ = str(src_file)
        exec(compile(mutant_src, str(src_file), "exec"), mutant.__dict__)

        # Synchronized assembly: every consumer of the mutated class/exception
        # observes the SAME mutant object (JH016-04). A partially patched
        # consumer silently running unmutated code is an assembly bug.
        patches = []
        if mod_name == "src.file_tools":
            for consumer in (live_ft, live_orch, phase4_tests):
                for symbol in ("FileToolExecutor", "PathBoundaryError"):
                    patches.append(patch.object(consumer, symbol, getattr(mutant, symbol)))
            # PublishWindowDriftError crosses the file_tools -> orchestrator
            # boundary: consumers holding the live class would miss the
            # mutant's exception identity. (phase4_tests never references it.)
            for consumer in (live_ft, live_orch):
                patches.append(patch.object(consumer, "PublishWindowDriftError",
                                            getattr(mutant, "PublishWindowDriftError")))
        elif mod_name == "src.orchestrator":
            patches.append(patch.object(live_orch, "Orchestrator", mutant.Orchestrator, create=True))
            patches.append(patch.object(phase4_tests, "Orchestrator", mutant.Orchestrator))
        elif mod_name == "src.acceptance":
            patches.append(patch.object(live_acc, "file_write_acceptance_evaluator",
                                        mutant.file_write_acceptance_evaluator, create=True))
            patches.append(patch.object(phase4_tests, "file_write_acceptance_evaluator",
                                        mutant.file_write_acceptance_evaluator))

        suite = unittest.TestSuite([_find_test(test_name)])
        for patch_obj in patches:
            patch_obj.start()
        try:
            result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
            test_failures = [{"test": str(t), "last_lines": _last_lines(tb)}
                             for t, tb in result.failures]
            test_errors = [{"test": str(t), "last_lines": _last_lines(tb)}
                           for t, tb in result.errors]
            oracle_unsafe = None
            oracle_detail = ""
            oracle_error = ""
            try:
                oracle_unsafe, oracle_detail = ORACLES[name](name)
            except Exception as exc:
                oracle_error = "%s: %s" % (type(exc).__name__, exc)
        finally:
            for patch_obj in reversed(patches):
                patch_obj.stop()

        # Caliber: errors never count as detection; wording-only red without a
        # broken invariant is redundant survival, not a kill.
        has_red = bool(test_failures)
        has_error = bool(test_errors) or bool(oracle_error)
        if has_error:
            verdict = "harness_error"
            ok = False
        elif has_red and oracle_unsafe:
            verdict = "detected"
            ok = True
        elif has_red and not oracle_unsafe:
            verdict = "wording_only_redundant"
            ok = name in EXPECTED_REDUNDANT
        elif not has_red and oracle_unsafe:
            verdict = "survived_test_gap"
            ok = False
        else:
            verdict = "survived_redundant"
            ok = name in EXPECTED_REDUNDANT
        records.append({
            "guard": name, "module": rel_path, "test": test_name,
            "verdict": verdict, "ok": ok,
            "detected": verdict == "detected",
            "survived": verdict in ("survived_redundant", "survived_test_gap"),
            "expected_redundant": EXPECTED_REDUNDANT.get(name, ""),
            "test_failures": test_failures, "test_errors": test_errors,
            "oracle_error": oracle_error,
            "oracle_unsafe": oracle_unsafe, "oracle_detail": oracle_detail,
        })

    passed = all(r["ok"] for r in records)
    summary = {
        "mutations": len(records), "passed": passed,
        "detected": sum(1 for r in records if r["verdict"] == "detected"),
        "wording_only_redundant": sum(1 for r in records if r["verdict"] == "wording_only_redundant"),
        "survived_redundant": sum(1 for r in records if r["verdict"] == "survived_redundant"),
        "survived_test_gap": sum(1 for r in records if r["verdict"] == "survived_test_gap"),
        "harness_errors": sum(1 for r in records if r["verdict"] == "harness_error"),
        "results": records,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
