"""Hark-派单-07 R3: the guard verifier's own classification, with counter-controls.

Drives `scripts/verify_hark06_session_probe_guards.py` with fixed outcomes so
every classification branch is pinned: only a clean control plus an assertion
is a detection; only a declared row with a clean control, a CLEAN mutated
formal target, an observed-active weakening and a valid diagnostic is
redundant; error, skip, zero tests, bad control, inactive weakening and invalid
diagnostics all fail the whole run. Also reproduces the review's frozen
five-outcome probe shape against `main()` itself.
"""
import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

_spec = importlib.util.spec_from_file_location(
    "hark06_guard_verifier", ROOT / "scripts" / "verify_hark06_session_probe_guards.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

GOOD_DIAG = {"thread_starts": 1, "first_start_accepted": True, "second_start": "probe_not_prepared"}
RED = "replay_check_removed"
PLAIN = "confirmation_ignored"


class TestClassifyPythonRow(unittest.TestCase):
    def classify(self, name=PLAIN, control="passed", mutated="assertion", active=None, diag=None):
        return guard.classify_python_row(name, control, mutated, active, diag)

    def test_detection_needs_clean_control_and_assertion(self):
        self.assertEqual(self.classify()[:2], ("detected", True))
        for control in ("assertion", "error", "skipped", "zero_tests"):
            self.assertEqual(self.classify(control=control)[:2], ("control_failed", False), control)

    def test_non_assertion_mutations_are_never_detections(self):
        for mutated in ("error", "skipped", "zero_tests"):
            verdict, counted, _ = self.classify(mutated=mutated)
            self.assertEqual((verdict, counted), (f"mutated_{mutated}", False), mutated)
        self.assertEqual(self.classify(mutated="passed")[:2], ("not_detected", False))

    def test_redundant_needs_clean_formal_sample_active_mutation_and_valid_diagnostic(self):
        self.assertEqual(self.classify(RED, mutated="passed", active=True, diag=GOOD_DIAG)[:2],
                         ("redundant", False))

    def test_redundant_row_with_error_skip_or_zero_tests_fails(self):
        for mutated in ("error", "skipped", "zero_tests"):
            verdict, counted, _ = self.classify(RED, mutated=mutated, active=True, diag=GOOD_DIAG)
            self.assertEqual((verdict, counted), (f"mutated_{mutated}", False), mutated)

    def test_redundant_row_with_bad_control_fails(self):
        self.assertEqual(self.classify(RED, control="error", mutated="passed", active=True, diag=GOOD_DIAG)[0],
                         "control_failed")

    def test_inactive_mutation_is_not_redundant(self):
        for active in (None, False):
            self.assertEqual(self.classify(RED, mutated="passed", active=active, diag=GOOD_DIAG)[0],
                             "mutation_inactive")

    def test_invalid_diagnostics_are_not_redundant(self):
        bad = [None, "1", {}, {"error": "boom"}, dict(GOOD_DIAG, thread_starts=2),
               dict(GOOD_DIAG, thread_starts=True), dict(GOOD_DIAG, thread_starts=0),
               dict(GOOD_DIAG, first_start_accepted=False), dict(GOOD_DIAG, second_start="accepted"),
               {"thread_starts": 1, "second_start": "probe_not_prepared"}]
        for diag in bad:
            verdict, _, extra = self.classify(RED, mutated="passed", active=True, diag=diag)
            self.assertEqual(verdict, "invalid_diagnostic", diag)
            self.assertTrue(extra.get("diagnostic_problem"))

    def test_assertion_on_a_declared_redundant_row_is_a_detection_not_redundancy(self):
        verdict, counted, extra = self.classify(RED, mutated="assertion", active=True, diag=GOOD_DIAG)
        self.assertEqual((verdict, counted), ("detected", True))
        self.assertTrue(extra["redundancy_contradicted"])

    def test_undeclared_clean_pass_is_never_redundant(self):
        self.assertEqual(self.classify(PLAIN, mutated="passed", active=True, diag=GOOD_DIAG)[0], "not_detected")


class TestClassifyJsRow(unittest.TestCase):
    def test_js_branches(self):
        self.assertEqual(guard.classify_js_row(True, 1, {"ok": False, "kind": "assertion"}), ("detected", True))
        self.assertEqual(guard.classify_js_row(True, 1, {"ok": False, "kind": "exception"})[0], "mutated_exception")
        self.assertEqual(guard.classify_js_row(True, 1, {"ok": True})[0], "not_detected")
        self.assertEqual(guard.classify_js_row(True, 1, None)[0], "no_report")
        self.assertEqual(guard.classify_js_row(False, 1, {"ok": False, "kind": "assertion"})[0], "control_failed")
        self.assertEqual(guard.classify_js_row(True, 2, {"ok": False, "kind": "assertion"})[0], "anchor_not_unique")


class TestRunCase(unittest.TestCase):
    def test_outcomes(self):
        base = "hark07_verifier_samples._Samples."
        self.assertEqual(guard.run_case(base + "test_pass"), "passed")
        self.assertEqual(guard.run_case(base + "test_assert"), "assertion")
        self.assertEqual(guard.run_case(base + "test_error"), "error")
        self.assertEqual(guard.run_case(base + "test_skip"), "skipped")
        self.assertEqual(guard.run_case(base + "test_does_not_exist"), "error")
        self.assertEqual(guard.run_case("hark07_verifier_samples._Empty"), "zero_tests")


def _main_with(state, *, patch_cm=contextlib.nullcontext, active=None, diag=None):
    """Run the verifier's main() on one declared-redundant row with fixed outcomes."""
    states = iter(["passed", state])
    buf = io.StringIO()
    checks = {} if active is None else {RED: lambda: active}
    with mock.patch.object(guard, "PY_MUTATIONS", [(RED, patch_cm, "synthetic-outcome")]), \
         mock.patch.object(guard, "JS_MUTATIONS", []), \
         mock.patch.object(guard, "NODE", "not-used"), \
         mock.patch.object(guard, "run_case", lambda name: next(states)), \
         mock.patch.object(guard, "run_js", lambda path: {"scenarios": {}}), \
         mock.patch.object(guard, "MUTATION_ACTIVE_CHECKS", checks), \
         mock.patch.object(guard, "replay_diagnostic", lambda: dict(diag or GOOD_DIAG)), \
         contextlib.redirect_stdout(buf):
        code = guard.main()
    return code, json.loads(buf.getvalue())


class TestMainRejectsTheReviewOutcomes(unittest.TestCase):
    """The frozen review probe's shape: control passed, fixed diagnostic, and
    the formal outcome varied over passed/assertion/error/skipped/zero_tests."""

    def test_error_skip_and_zero_tests_fail_the_run(self):
        for state in ("error", "skipped", "zero_tests"):
            code, summary = _main_with(state, active=True)
            self.assertEqual((code, summary["verdict"]), (1, "fail"), state)
            self.assertEqual(summary["rows"][0]["verdict"], f"mutated_{state}")
            self.assertEqual(summary["redundant"], 0)

    def test_clean_pass_with_inactive_mutation_fails(self):
        code, summary = _main_with("passed", active=False)
        self.assertEqual(code, 1)
        self.assertEqual(summary["rows"][0]["verdict"], "mutation_inactive")

    def test_clean_pass_with_invalid_diagnostic_fails(self):
        code, summary = _main_with("passed", active=True, diag={"thread_starts": 2})
        self.assertEqual(code, 1)
        self.assertEqual(summary["rows"][0]["verdict"], "invalid_diagnostic")

    def test_clean_pass_active_valid_is_the_only_redundant_pass(self):
        code, summary = _main_with("passed", active=True)
        self.assertEqual((code, summary["redundant"], summary["detected"]), (0, 1, 0))

    def test_assertion_is_counted_as_detection(self):
        code, summary = _main_with("assertion", active=True)
        self.assertEqual((code, summary["detected"], summary["redundant"]), (0, 1, 0))
        self.assertTrue(summary["rows"][0]["redundancy_contradicted"])

    def test_patch_failure_is_an_error_not_a_pass(self):
        def broken():
            raise RuntimeError("cannot patch")
        states = iter(["passed"])
        buf = io.StringIO()
        with mock.patch.object(guard, "PY_MUTATIONS", [(PLAIN, broken, "x")]), \
             mock.patch.object(guard, "JS_MUTATIONS", []), mock.patch.object(guard, "NODE", "not-used"), \
             mock.patch.object(guard, "run_case", lambda name: next(states)), \
             mock.patch.object(guard, "run_js", lambda path: {"scenarios": {}}), \
             contextlib.redirect_stdout(buf):
            code = guard.main()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(buf.getvalue())["rows"][0]["verdict"], "mutated_error")


if __name__ == "__main__":
    unittest.main()
