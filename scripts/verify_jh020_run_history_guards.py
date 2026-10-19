"""JH-020 run-history guard verification by targeted mutation (offline).

Each guard the run-history surface relies on is disabled alone with a targeted
patch, and its regression test must then FAIL on an assertion (an error never
counts). A baseline run of every test (unpatched) must pass first, so a green
result cannot come from a broken harness.

Patches replace single callables by name, so a mutant never fails merely because
an in-memory copy changed identity. No process, no model call, no session send,
and no source file is touched.
"""
import io
import json
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import src.agent_session_codex as codex_mod
import src.app_api as app_api
import test_jh018_agent_session_contract as contract_tests

T = "test_jh020_run_history_api."
BOUNDARY = T + "TestEvidenceBoundary."
CONTRACT = "test_jh018_agent_session_contract.TestCoreHoldsNoConcreteAdapter."
STATES = T + "TestRunHistoryStates."
EVIDENCE = T + "TestEvidenceClassification."
EFFECTS = T + "TestNoSideEffects."


def run_case(name):
    return unittest.TextTestRunner(stream=io.StringIO()).run(
        unittest.defaultTestLoader.loadTestsFromName(name))


def _evidence_always_verified(raw_evidence, **kwargs):
    # A regression that stops reading the bytes and trusts the recorded digest.
    return "verified", "assumed verified"


def _lenient_parse(text):
    # The pre-rectification parser: parse each line, validate nothing.
    records = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


def _absent_reads_as_empty(path):
    # A regression that folds "no file" into "empty history".
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


_real_evidence_state = codex_mod.raw_evidence_state


def _uncontained_evidence_state(raw_evidence, **kwargs):
    # Drops the server-owned roots, i.e. the pre-rectification uncontained read.
    return _real_evidence_state(raw_evidence)


def _no_special_path(value):
    return None


def _no_reparse(candidate, root):
    return None


def _exact_match_only(self, path):
    # The pre-rectification predicate: only the exact absolute form.
    import ast as _ast
    tree = _ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in _ast.walk(tree):
        if isinstance(node, _ast.ImportFrom) and node.module == "src.agent_session" and node.level == 0:
            return True
        if isinstance(node, _ast.Import) and any(a.name == "src.agent_session" for a in node.names):
            return True
    return False


def _no_literal_target(cls, call):
    return None


def _link_always_true(self, task_id):
    # A regression that reports an unlinked run as linked.
    return {"checked": True, "linked": True, "note": "assumed linked"}


MUTATIONS = [
    ("evidence_root_containment_ignored",
     [patch.object(app_api, "raw_evidence_state", new=_uncontained_evidence_state)],
     BOUNDARY + "test_outside_absolute_is_refused_before_read"),
    ("special_path_check_ignored",
     [patch.object(codex_mod, "is_special_path", new=_no_special_path)],
     BOUNDARY + "test_special_path_forms_are_never_touched"),
    ("reparse_check_ignored",
     [patch.object(codex_mod, "_reparse_component", new=_no_reparse)],
     BOUNDARY + "test_junction_under_the_root_is_refused_before_read"),
    ("equivalent_import_forms_ignored",
     [patch.object(contract_tests.TestCoreHoldsNoConcreteAdapter, "_imports_the_contract",
                   new=_exact_match_only)],
     CONTRACT + "test_every_equivalent_static_import_form_is_flagged"),
    ("dynamic_literal_import_ignored",
     [patch.object(contract_tests.TestCoreHoldsNoConcreteAdapter, "_literal_import_target",
                   new=classmethod(_no_literal_target))],
     CONTRACT + "test_every_equivalent_static_import_form_is_flagged"),
    ("evidence_state_ignored",
     [patch.object(app_api, "raw_evidence_state", new=_evidence_always_verified)],
     EVIDENCE + "test_mismatch_on_altered_bytes"),
    ("registry_field_validation_skipped",
     [patch.object(app_api, "parse_registry_text", new=_lenient_parse)],
     STATES + "test_corrupt_registry_is_reported_and_left_untouched"),
    ("absent_registry_folded_into_empty",
     [patch.object(app_api, "read_registry_text", new=_absent_reads_as_empty)],
     STATES + "test_absent_registry_is_its_own_state"),
    ("unlinked_run_reported_as_linked",
     [patch.object(app_api.AppInterface, "_task_link", new=_link_always_true)],
     EFFECTS + "test_unlinked_run_does_not_create_a_task_record"),
]


def main():
    controls = [run_case(row[-1]) for row in MUTATIONS]
    if not all(r.wasSuccessful() and r.testsRun == 1 and not r.skipped for r in controls):
        failing = [row[0] for row, r in zip(MUTATIONS, controls) if not r.wasSuccessful()]
        print(json.dumps({"verdict": "fail", "reason": "baseline control failed", "cases": failing}))
        return 1
    rows = []
    for name, patchers, test_name in MUTATIONS:
        with ExitStack() as stack:
            for patcher in patchers:
                stack.enter_context(patcher)
            result = run_case(test_name)
        detected = bool(result.failures) and not result.errors
        rows.append({"case": name, "detected": detected,
                     "failures": len(result.failures), "errors": len(result.errors)})
    passed = all(row["detected"] for row in rows)
    print(json.dumps({"scope": "jh020_run_history_offline_only", "model_calls": 0,
                      "processes": 0, "controls": len(controls), "mutations": len(rows),
                      "detected": sum(row["detected"] for row in rows),
                      "rows": rows, "verdict": "pass" if passed else "fail"}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
