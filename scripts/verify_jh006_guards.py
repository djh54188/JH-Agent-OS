"""JH-006 offline source mutations; assertion failures detect, errors never do.

The repository is never modified. Every run happens in a disposable copy of the
checkout in its own Python process, so the mutated module, every consumer
(orchestrator, cli, code_delivery helpers) and the test modules share a single
import graph: one class identity for each exception and one module attribute
for each ``unittest.mock.patch`` target. The previous in-memory ``exec`` into a
dict namespace left test patches on the live module, so some mutants were
judged against unpatched helpers (V-2).

Per mutation three runs of the named regression:
  1. baseline  -- unmodified copy; must pass (else ``baseline_not_green``)
  2. identity  -- anchor replaced by itself through the same write path; must pass
  3. mutant    -- real replacement; must fail by assertion only
Errors, skips, load failures and message-regex-only mismatches never count.
Exit codes: 0 all detected; 1 a survivor or harness/classification problem;
2 no survivors but at least one baseline is not green on this host.
Each test creates its own isolated workspace and SQLite records. No APIs.
"""
import json
import sys
from pathlib import Path

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

R = "test_jh006_review_regressions.TestReviewedDeliveryBoundaries."
F = "test_jh006_code_delivery."
# name, module, unique source anchor, replacement, observable regression
MUTATIONS = [
    ("payload_delivery_marker", "src.orchestrator",
     '        if "_delivery" in (task_payload.get("metadata") or {}):',
     '        if False:', R + "test_input_cannot_claim_applied_or_waiting_delivery"),
    ("cli_delivery_marker", "src.cli", '    if "_delivery" in meta:',
     '    if False:', R + "test_cli_rejects_delivery_marker_before_assembly"),
    ("unregistered_host_record", "src.orchestrator",
     '        if not fixture_host_evidence_matches(self.storage, task_id, norm):',
     '        if False:', R + "test_unregistered_receipt_cannot_succeed"),
    ("raw_output_digest", "src.code_delivery",
     '            and sha256_hex(output.encode("utf-8")) == receipt["tests"]["output_digest"])',
     '            and True)', R + "test_host_output_digest_must_match_persisted_raw_output"),
    ("strict_uncertainty_bool", "src.code_delivery",
     '    if type(receipt["uncertain"]) is not bool:',
     '    if receipt["uncertain"] not in (True, False):', R + "test_uncertainty_requires_bool_not_zero_or_one"),
    ("fixed_check_script", "src.orchestrator",
     '        if norm["tests"]["check_script"] != expected_script:',
     '        if False:', R + "test_wrong_script_path_is_not_a_verified_check"),
    ("sealed_check_command", "src.code_delivery",
     '        checks = json.loads(sealed_fixture_bytes(template, "checks_rel"))',
     '        checks = json.loads((_repo_root() / template["fixture_dir"] / "checks.json").read_text(encoding="utf-8"))',
     R + "test_unsealed_check_command_is_refused"),
    ("prepare_owner", "src.orchestrator",
     '    def prepare_code_delivery(self, task_id: str, artifact_version: int) -> Dict[str, Any]:\n        owner_id = uuid.uuid4().hex\n        if not self.storage.claim_task(task_id, owner_id):',
     '    def prepare_code_delivery(self, task_id: str, artifact_version: int) -> Dict[str, Any]:\n        owner_id = uuid.uuid4().hex\n        if False:',
     R + "test_prepare_respects_existing_owner"),
    ("pending_approval", "src.orchestrator",
     '        if self.storage.get_control(task_id).get("pending_json"):',
     '        if False:', R + "test_prepare_rechecks_pending_approval"),
    ("interrupted_tool", "src.orchestrator",
     '        if any(t.get("status") in uncertain for t in self.storage.list_tool_executions(task_id)):',
     '        if False:', R + "test_prepare_rechecks_interrupted_tool"),
    ("prepare_transaction", "src.orchestrator",
     '            with self.storage.transaction():\n                return self._prepare_code_delivery_locked(task_id, artifact_version)',
     '            with __import__("contextlib").nullcontext():\n                return self._prepare_code_delivery_locked(task_id, artifact_version)',
     R + "test_prepare_and_event_rollback_together"),
    ("authorized_workspace_scope", "src.orchestrator",
     '        if unauthorized or scope_after.get(tpl["authorized_target"]) != actual_sha:',
     '        if scope_after.get(tpl["authorized_target"]) != actual_sha:',
     R + "test_extra_file_change_never_satisfies_authorized_scope"),
    ("no_model_tools", "src.orchestrator",
     '            if proposed_requests and is_code_delivery_task(task):',
     '            if False:', R + "test_fixture_model_tools_never_execute"),
    ("generation_baseline_context", "src.orchestrator",
     '        if is_code_delivery_task(task):\n            tpl = template_for_task(task)\n            context["code_template"] = {',
     '        if False:\n            tpl = template_for_task(task)\n            context["code_template"] = {',
     R + "test_generation_receives_template_and_sealed_baseline"),
    ("review_deferred_scope", "src.orchestrator",
     '                if is_code_delivery_task(task):\n                    tpl = template_for_task(task)\n                    independent_review_context["code_review_scope"] = {',
     '                if False:\n                    tpl = template_for_task(task)\n                    independent_review_context["code_review_scope"] = {',
     R + "test_review_scope_defers_unexecuted_host_checks"),
    ("fresh_cancel_state", "src.orchestrator",
     '        if task["status"] != "reviewing":\n            raise CodeDeliveryRefused(f"complete requires reviewing, got',
     '        if False:\n            raise CodeDeliveryRefused(f"complete requires reviewing, got',
     R + "test_complete_reloads_cancel_after_owner_acquisition"),
    ("prepared_version_field", "src.orchestrator",
     '            if (pinned.get("version") == artifact_version',
     '            if (pinned.get("artifact_version") == artifact_version',
     F + "TestCliDeliveryCommands.test_cli_prepare_and_complete_roundtrip"),
    ("no_extra_model_turn", "src.orchestrator",
     '    def _complete_code_delivery_locked(self, task_id, receipt, tpl, delivery):\n        budget_failed = self._check_budget_or_fail(task_id, before_call=False)',
     '    def _complete_code_delivery_locked(self, task_id, receipt, tpl, delivery):\n        budget_failed = self._check_budget_or_fail(task_id)',
     R + "test_control_completion_does_not_reserve_an_extra_model_turn"),
    ("final_deadline_gate", "src.orchestrator",
     '        stopped = self._check_budget_or_fail(task_id, before_call=False)\n        if stopped is not None:\n            return stopped\n        criteria = task["acceptance_criteria"]',
     '        stopped = None\n        if stopped is not None:\n            return stopped\n        criteria = task["acceptance_criteria"]',
     R + "test_deadline_is_rechecked_after_verification_reads"),
]


def build_cases():
    cases = []
    for name, module_name, original, replacement, test_name in MUTATIONS:
        cases.append({"name": name, "path": module_name.replace(".", "/") + ".py",
                      "original": original, "replacement": replacement, "test": test_name})
    return cases


def main(argv=None):
    report = evaluate_mutations(ROOT, build_cases())
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
