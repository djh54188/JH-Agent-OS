"""Hark-派单-04 C: classification regression for verify_jh020_run_history_guards.

Only the one proven-masked row may be classified redundant, and only when its
control is green, its mutant passes cleanly and its isolated diagnostic (the
covering layer also off) fails on an assertion. Every other outcome - control
not green, error, skip, zero tests, unknown survivor - must stay a reported
failure. Pure: summaries are fabricated, no mutation is executed here.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

_spec = importlib.util.spec_from_file_location(
    "verify_jh020_run_history_guards", ROOT / "scripts" / "verify_jh020_run_history_guards.py")
guards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guards)

DECLARED = "registry_field_validation_skipped"
OTHER = "evidence_state_ignored"


def s(run=1, failures=0, errors=0, skipped=0):
    return {"run": run, "failures": failures, "errors": errors, "skipped": skipped}


GREEN = s()
FAILED = s(failures=1)


class TestClassification(unittest.TestCase):
    def test_the_nine_rows_and_their_targets_are_kept(self):
        names = [row[0] for row in guards.MUTATIONS]
        self.assertEqual(len(names), 9)
        self.assertEqual(len(set(names)), 9)
        self.assertIn(DECLARED, names)
        target = dict((row[0], row[-1]) for row in guards.MUTATIONS)[DECLARED]
        self.assertTrue(target.endswith("test_corrupt_registry_is_reported_and_left_untouched"))

    def test_only_the_proven_row_is_declared_redundant(self):
        self.assertEqual(set(guards.DECLARED_REDUNDANT), {DECLARED})

    def test_detected(self):
        self.assertEqual(guards.classify(OTHER, GREEN, FAILED), "detected")
        self.assertEqual(guards.classify(DECLARED, GREEN, FAILED, FAILED), "detected")

    def test_declared_row_masked_and_confirmed_is_redundant(self):
        self.assertEqual(guards.classify(DECLARED, GREEN, GREEN, FAILED), "redundant")

    def test_declared_row_without_confirming_diagnostic_is_a_survivor(self):
        for diagnostic in (None, GREEN, s(errors=1), s(run=0), s(failures=1, skipped=1)):
            with self.subTest(diagnostic=diagnostic):
                self.assertEqual(guards.classify(DECLARED, GREEN, GREEN, diagnostic), "survived")

    def test_undeclared_survivor_is_never_redundant(self):
        self.assertEqual(guards.classify(OTHER, GREEN, GREEN, FAILED), "survived")

    def test_control_not_green_is_never_redundant(self):
        for control in (FAILED, s(errors=1), s(skipped=1), s(run=0), None):
            with self.subTest(control=control):
                self.assertEqual(guards.classify(DECLARED, control, GREEN, FAILED), "control_failed")

    def test_errors_skips_and_zero_tests_are_reported(self):
        self.assertEqual(guards.classify(DECLARED, GREEN, s(errors=1), FAILED), "error")
        self.assertEqual(guards.classify(DECLARED, GREEN, s(failures=1, errors=1), FAILED), "error")
        self.assertEqual(guards.classify(DECLARED, GREEN, s(skipped=1), FAILED), "skipped")
        self.assertEqual(guards.classify(DECLARED, GREEN, s(run=0), FAILED), "no_tests")
        self.assertEqual(guards.classify(DECLARED, GREEN, None, FAILED), "no_tests")

    def test_verdict(self):
        row = lambda c: {"classification": c}
        self.assertEqual(guards.verdict([row("detected")] * 8 + [row("redundant")]), "pass")
        for bad in ("control_failed", "error", "skipped", "no_tests", "survived"):
            with self.subTest(bad=bad):
                self.assertEqual(guards.verdict([row("detected")] * 7 + [row("redundant"), row(bad)]),
                                 "fail")
        self.assertEqual(guards.verdict([]), "fail")


if __name__ == "__main__":
    unittest.main()
