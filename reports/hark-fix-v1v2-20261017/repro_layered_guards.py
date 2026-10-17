"""Reproduction: why three Phase 4 mutations are not detected (V-1 analysis).

Read-only with respect to the checkout: every run uses a disposable copy.
For each case it runs, in a fresh process:
  A. single mutant + the regression with its error-text assertion lines removed
     (proves the control decision and side-effect assertions are unchanged);
  B. two layers removed + the unmodified regression;
  C. (where a third refusal exists) three layers removed + the unmodified regression.
B/C show which further layer still refuses; nothing here is counted as detection.
Usage: python reports/hark-fix-v1v2-20261017/repro_layered_guards.py
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_phase4_guards as v4  # noqa: E402

BY_NAME = {m[0]: m for m in v4.MUTATIONS}
TEST_FILE = "tests/test_phase4_file_tools.py"

CASES = [
    {
        "guard": "apply_target_version_check_removed",
        "test": "test_target_changed_between_review_and_apply",
        "text_assert": '        self.assertIn("target changed since review", result["error_info"])\n',
        "second_layer": ("src/file_tools.py",) + tuple(BY_NAME["arch03_locked_handle_version_check_removed"][2:4]),
        "third_layer": ("src/file_tools.py",
                        "                if os.path.lexists(str(target_now)):\n"
                        "                    raise PathBoundaryError(\n"
                        "                        f\"apply refused: target changed since review (file appeared concurrently: '{target_now}')\")",
                        "                if False:\n                    pass"),
    },
    {
        "guard": "apply_staged_hash_check_removed",
        "test": "test_apply_refuses_tampered_staged_content",
        "text_assert": '        self.assertIn("staged content sha", result["error_info"])\n',
        "second_layer": ("src/file_tools.py",
                         "        if _sha256_text(candidate) != content_sha256:\n"
                         '            raise PathBoundaryError("apply refused: staged content does not match reviewed hash")',
                         "        if False:\n            pass"),
        "third_layer": ("src/file_tools.py",
                        "            if _sha256_file(temp_path) != content_sha256:\n"
                        '                raise PathBoundaryError("apply refused: temp file hash mismatch before commit")',
                        "            if False:\n                pass"),
    },
    {
        "guard": "recovered_applied_hash_check_removed",
        "test": "test_recovered_applied_hash_must_match_reviewed_snapshot",
        "text_assert": '        self.assertIn("hash mismatch", (result["error_info"] or "").lower())\n',
        "second_layer": ("src/storage.py",
                         '                        if latest_apply_sd.get("applied_sha256") not in reviewed_shas:\n'
                         '                            reason = "Applied target hash does not match the current reviewed artifact hash"',
                         "                        if False:\n                            pass"),
    },
]


def main():
    import tempfile
    ids = v4._test_ids()
    out = []
    with tempfile.TemporaryDirectory(prefix="jh-layered-") as tmp:
        tmp = Path(tmp)
        for i, case in enumerate(CASES):
            _, rel, original, replacement, _ = BY_NAME[case["guard"]]
            test_id = ids[case["test"]]
            row = {"guard": case["guard"], "test": test_id}
            # A: single mutant, error-text assertion removed.
            repo = tmp / f"a{i}"
            v4._copy_checkout(ROOT, repo)
            errs = [v4._apply_anchor(repo, rel, original, replacement),
                    v4._apply_anchor(repo, TEST_FILE, case["text_assert"], "")]
            row["A_single_mutant_text_assert_removed"] = (
                {"harness_error": errs} if any(errs) else v4.outcome_of(v4.run_test_in_copy(repo, test_id)))
            # B: both layers removed, regression unchanged.
            repo = tmp / f"b{i}"
            v4._copy_checkout(ROOT, repo)
            rel2, orig2, repl2 = case["second_layer"]
            errs = [v4._apply_anchor(repo, rel, original, replacement),
                    v4._apply_anchor(repo, rel2, orig2, repl2)]
            if any(errs):
                row["B_both_layers_removed"] = {"harness_error": errs}
            else:
                run = v4.run_test_in_copy(repo, test_id)
                row["B_both_layers_removed"] = v4.outcome_of(run)
                if run.get("failures"):
                    row["B_failing_source"] = v4._failing_source_line(run["failures"][0])
                    row["B_message"] = run["failures"][0].strip().splitlines()[-1][-300:]
            if "third_layer" in case:
                repo = tmp / f"c{i}"
                v4._copy_checkout(ROOT, repo)
                rel3, orig3, repl3 = case["third_layer"]
                errs = [v4._apply_anchor(repo, rel, original, replacement),
                        v4._apply_anchor(repo, rel2, orig2, repl2),
                        v4._apply_anchor(repo, rel3, orig3, repl3)]
                if any(errs):
                    row["C_three_layers_removed"] = {"harness_error": errs}
                else:
                    run = v4.run_test_in_copy(repo, test_id)
                    row["C_three_layers_removed"] = v4.outcome_of(run)
                    if run.get("failures") or run.get("errors"):
                        text = (run.get("failures") or run.get("errors"))[0]
                        row["C_failing_source"] = v4._failing_source_line(text)
                        row["C_message"] = text.strip().splitlines()[-1][-300:]
            out.append(row)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
