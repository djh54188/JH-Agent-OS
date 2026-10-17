"""Mutation guard verification for Phase 4 batch 1 file tools.

Each mutation breaks one file-tool security guarantee. The repository itself is
never modified: every run happens in a disposable copy of the checkout, in its
own Python process, so the mutated module, its consumers (orchestrator,
acceptance) and the test module share one import graph -- one class identity
for every exception and one set of module attributes for every patch target.

Per mutation three runs of the matching test in tests/test_phase4_file_tools.py:
  1. baseline  -- unmodified copy; must pass (else ``baseline_not_green``)
  2. identity  -- anchor replaced by itself through the same write path; must pass
  3. mutant    -- real replacement; must fail by assertion only
Only an assertion failure in run 3 counts as ``detected``. Errors, skips,
load failures, a red baseline/identity run, or an assertion that only differs
in an exception-message regex are never counted.

Exit codes: 0 every guard detected; 1 any guard survived or a harness/
classification problem; 2 no survivors but at least one baseline is not green
on this host (platform-blocked, e.g. Windows-only junction tests on POSIX).
"""

import ast
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Isolated mutation harness (identical copy in verify_phase4_guards.py and
# verify_jh006_guards.py; kept self-contained per script on purpose).
# ---------------------------------------------------------------------------
import json as _json
import os as _os
import re as _re
import shutil as _shutil
import subprocess as _subprocess
import tempfile as _tempfile

_COPY_IGNORE = _shutil.ignore_patterns(".git", "data", ".test-tmp", "__pycache__", "*.pyc",
                                       ".pytest_cache")
_RESULT_MARK = "@@MUTATION_RESULT@@"
_CHILD = r'''
import io, json, sys, unittest
root, name = sys.argv[1], sys.argv[2]
sys.path[:0] = [root, root + "/tests"]
suite = unittest.defaultTestLoader.loadTestsFromName(name)
result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
print("@@MUTATION_RESULT@@" + json.dumps({
    "tests_run": result.testsRun,
    "failures": [text for _, text in result.failures],
    "errors": [text for _, text in result.errors],
    "skipped": [reason for _, reason in result.skipped],
    "expected_failures": len(result.expectedFailures),
    "unexpected_successes": len(result.unexpectedSuccesses),
}))
'''
# A failure that only proves an error/refusal *message* moved is not a detected
# behaviour change: an assertRaisesRegex/assertRegex mismatch, or an
# assertIn/assertNotIn/assertRegex whose failing source line reads an error text
# (error_info, exception, reason, message). The control decision itself (status,
# raised type, side effects) is asserted elsewhere and must be what turns red.
_REGEX_MISMATCH = _re.compile(r'AssertionError: (?:"[^\n]*" does not match "|Regex didn\'t match)')
_TEXT_ASSERT = _re.compile(r'assert(?:Not)?In\(|assert(?:Not)?Regex\(')
_ERROR_TEXT = _re.compile(r'error_info|exception|\breason\b|message|str\(\s*(?:exc|err|e|ctx)')


def _failing_source_line(text):
    lines = text.rstrip().splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].lstrip().startswith('File "') and i + 1 < len(lines):
            return lines[i + 1].strip()
    return ""


def _is_message_only(text):
    if _REGEX_MISMATCH.search(text):
        return True
    source = _failing_source_line(text)
    return bool(_TEXT_ASSERT.search(source) and _ERROR_TEXT.search(source))
CHILD_TIMEOUT_SECONDS = 600


def _copy_checkout(root, dest):
    _shutil.copytree(str(root), str(dest), ignore=_COPY_IGNORE)


def _apply_anchor(repo, rel_path, original, replacement):
    """Rewrite one file in the disposable copy. Returns an error string or None."""
    target = repo / rel_path
    raw = target.read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    anchor = original.replace("\n", "\r\n") if crlf else original
    repl = replacement.replace("\n", "\r\n") if crlf else replacement
    count = raw.count(anchor)
    if count != 1:
        return f"anchor occurs {count} times in {rel_path}"
    target.write_bytes(raw.replace(anchor, repl, 1).encode("utf-8"))
    return None


def run_test_in_copy(repo, test_id, timeout=CHILD_TIMEOUT_SECONDS):
    env = dict(_os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    try:
        proc = _subprocess.run([sys.executable, "-B", "-c", _CHILD, str(repo), test_id],
                               cwd=str(repo), env=env, capture_output=True, text=True,
                               timeout=timeout)
    except _subprocess.TimeoutExpired:
        return {"harness_error": f"timeout after {timeout}s"}
    for line in proc.stdout.splitlines()[::-1]:
        if line.startswith(_RESULT_MARK):
            return _json.loads(line[len(_RESULT_MARK):])
    return {"harness_error": f"no result (exit {proc.returncode}): {proc.stderr[-2000:]}"}


def outcome_of(run):
    """Collapse one child result into a single outcome word."""
    if "harness_error" in run:
        return "harness_error"
    if run["tests_run"] != 1:
        return "not_exactly_one_test"
    if run["skipped"]:
        return "skipped"
    if run["errors"]:
        return "error"
    if run["expected_failures"] or run["unexpected_successes"]:
        return "expected_failure_marker"
    if run["failures"]:
        if all(_is_message_only(text) for text in run["failures"]):
            return "message_only_failure"
        return "assertion_failure"
    return "pass"


def classify(baseline, identity, mutant):
    """Verdict for one mutation from its three outcome words."""
    if baseline != "pass":
        return "baseline_not_green"
    if identity != "pass":
        return "identity_not_green"
    if mutant == "assertion_failure":
        return "detected"
    if mutant == "pass":
        return "survived"
    return "not_detected_" + mutant


def evaluate_mutations(root, cases):
    rows = []
    with _tempfile.TemporaryDirectory(prefix="jh-mutation-") as tmp:
        tmp = Path(tmp)
        base_repo = tmp / "baseline"
        _copy_checkout(root, base_repo)
        baseline_cache = {}
        for index, case in enumerate(cases):
            row = {"guard": case["name"], "module": case["path"], "test": case["test"]}
            check = (base_repo / case["path"]).read_bytes().decode("utf-8")
            occurrences = check.count(case["original"].replace("\n", "\r\n")
                                      if "\r\n" in check else case["original"])
            if occurrences != 1:
                row.update(verdict="harness_error",
                           reason=f"anchor occurs {occurrences} times in {case['path']}")
                rows.append(row)
                continue
            if case["test"] not in baseline_cache:
                baseline_cache[case["test"]] = run_test_in_copy(base_repo, case["test"])
            runs = {"baseline": baseline_cache[case["test"]]}
            for kind, replacement in (("identity", case["original"]), ("mutant", case["replacement"])):
                repo = tmp / f"{kind}-{index}"
                _copy_checkout(root, repo)
                err = _apply_anchor(repo, case["path"], case["original"], replacement)
                runs[kind] = {"harness_error": err} if err else run_test_in_copy(repo, case["test"])
                _shutil.rmtree(str(repo), ignore_errors=True)
            outcomes = {kind: outcome_of(run) for kind, run in runs.items()}
            row.update(outcomes=outcomes, verdict=classify(outcomes["baseline"],
                                                           outcomes["identity"],
                                                           outcomes["mutant"]))
            if row["verdict"] == "detected":
                text = runs["mutant"]["failures"][0]
                row["mutant_assertion"] = {"source": _failing_source_line(text)[-300:],
                                           "message": text.strip().splitlines()[-1][-300:]}
            else:
                row["evidence"] = {kind: (run.get("harness_error")
                                          or (run.get("errors") or run.get("failures")
                                              or run.get("skipped") or [""])[0][-1200:])
                                   for kind, run in runs.items()}
            rows.append(row)
    counts = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    detected = counts.get("detected", 0)
    blocked = counts.get("baseline_not_green", 0)
    if detected == len(rows):
        exit_code, verdict = 0, "pass"
    elif detected + blocked == len(rows):
        exit_code, verdict = 2, "platform_blocked"
    else:
        exit_code, verdict = 1, "fail"
    return {"scope": "offline_isolated_copy", "api_calls": 0, "python": sys.version.split()[0],
            "platform": sys.platform, "mutations": len(rows), "detected": detected,
            "counts": counts, "verdict": verdict, "exit_code": exit_code, "rows": rows}

TEST_FILE = "tests/test_phase4_file_tools.py"

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


def _test_ids():
    """Map each test method name to its unittest id via the test module's AST."""
    tree = ast.parse((ROOT / TEST_FILE).read_text(encoding="utf-8"))
    ids = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name.startswith("test_"):
                    ids.setdefault(item.name, f"test_phase4_file_tools.{node.name}.{item.name}")
    return ids


def build_cases():
    ids = _test_ids()
    cases = []
    for name, rel_path, original, replacement, test_name in MUTATIONS:
        cases.append({"name": name, "path": rel_path, "original": original,
                      "replacement": replacement, "test": ids.get(test_name, test_name)})
    return cases


def main(argv=None):
    report = evaluate_mutations(ROOT, build_cases())
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
