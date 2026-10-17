"""Mutation guard verification for Phase 4 batch 1 file tools.

Each mutation breaks one file-tool security guarantee in memory (no files on
disk are modified); the matching test in tests/test_phase4_file_tools.py must
turn red.
"""

import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

spec = importlib.util.spec_from_file_location("phase4_tests", ROOT / "tests/test_phase4_file_tools.py")
phase4_tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase4_tests)

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
    # ARCH-03: locked-handle pre_sha re-verification removed -> concurrent edit overwritten.
    ("arch03_locked_handle_version_check_removed", "src/file_tools.py",
     '                        if live_sha != target_pre_sha256:\n                            raise PathBoundaryError(\n                                f"apply refused: target changed since review "\n                                f"(current={live_sha}, expected={target_pre_sha256})")',
     "                        if False:\n                            pass",
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
]


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

        patches = []
        if mod_name == "src.file_tools":
            # The test module references FileToolExecutor directly.
            patches = [patch.object(phase4_tests, "FileToolExecutor", mutant.FileToolExecutor)]
        elif mod_name == "src.orchestrator":
            import src.orchestrator as live
            patches.append(patch.object(live, "Orchestrator", mutant.Orchestrator, create=True))
            patches.append(patch.object(phase4_tests, "Orchestrator", mutant.Orchestrator))
        elif mod_name == "src.acceptance":
            import src.acceptance as live_acc
            patches.append(patch.object(live_acc, "file_write_acceptance_evaluator",
                                        mutant.file_write_acceptance_evaluator, create=True))
            patches.append(patch.object(phase4_tests, "file_write_acceptance_evaluator",
                                        mutant.file_write_acceptance_evaluator))

        def _find_test(test_name):
            for cls in (phase4_tests.TestPhase4FileLoop, phase4_tests.TestFileToolExecutor):
                if hasattr(cls, test_name):
                    return cls(test_name)
            raise RuntimeError("test not found: " + test_name)

        suite = unittest.TestSuite([_find_test(test_name)])
        for patch_obj in patches:
            patch_obj.start()
        try:
            result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        finally:
            for patch_obj in reversed(patches):
                patch_obj.stop()

        detected = bool(result.failures or result.errors)
        records.append({"guard": name, "module": rel_path, "detected": detected})

    passed = all(r["detected"] for r in records)
    print(json.dumps({"mutations": len(records), "passed": passed, "results": records}))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
