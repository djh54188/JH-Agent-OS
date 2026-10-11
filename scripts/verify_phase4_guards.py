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
