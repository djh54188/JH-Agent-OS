"""Regression tests for the isolated mutation harness used by
scripts/verify_phase4_guards.py and scripts/verify_jh006_guards.py (V-1, V-2).

Everything runs on a synthetic throw-away repository in a temp directory: the
real checkout is only read (anchor uniqueness and test-id resolution). No APIs.
"""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _load(script):
    spec = importlib.util.spec_from_file_location("verifier_" + script, ROOT / "scripts" / f"{script}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFIERS = {name: _load(name) for name in ("verify_phase4_guards", "verify_jh006_guards")}

# Synthetic project: the mutated module (guard.py) defines an exception and a
# helper; a separate consumer module catches that exception and calls the
# helper through its module attribute; the test patches the helper on the
# guard module. The old in-memory exec harnesses broke exactly this shape:
# a re-executed module minted a second exception class (V-1) and test patches
# never reached the mutant namespace (V-2).
GUARD = '''\
class Refused(Exception):
    pass


def limit():
    return 10


def check(value):
    if value > limit():
        raise Refused("value over limit")
    return value


def flaky(value):
    return value
'''
CONSUMER = '''\
from src import guard
from src.guard import Refused


def run(value):
    try:
        guard.check(value)
    except Refused as exc:
        return "blocked: " + str(exc)
    return "allowed"
'''
TESTS = '''\
import unittest
from unittest.mock import patch
from src import consumer, guard


class TestGuard(unittest.TestCase):
    def test_over_limit_blocked(self):
        self.assertEqual(consumer.run(11), "blocked: value over limit")

    def test_patched_limit_reaches_guard(self):
        with patch("src.guard.limit", return_value=0):
            self.assertTrue(consumer.run(1).startswith("blocked"))

    def test_message(self):
        result = {"error_info": consumer.run(11)}
        self.assertTrue(result["error_info"].startswith("blocked"))
        self.assertIn("over limit", result["error_info"])

    def test_flaky(self):
        self.assertEqual(guard.flaky(1), 1)

    def test_red_baseline(self):
        self.assertEqual(1, 2)

    @unittest.skip("platform")
    def test_skipped(self):
        pass
'''
T = "test_guard.TestGuard."
CHECK = '    if value > limit():\n        raise Refused("value over limit")'


def _case(name, original, replacement, test, path="src/guard.py"):
    return {"name": name, "path": path, "original": original, "replacement": replacement, "test": test}


CASES = [
    _case("real_guard_removed", CHECK, "    pass", T + "test_over_limit_blocked"),
    _case("identity_only", CHECK, CHECK, T + "test_over_limit_blocked"),
    _case("patch_target_shared", CHECK, "    pass", T + "test_patched_limit_reaches_guard"),
    _case("error_not_detection", "def flaky(value):\n    return value\n",
          "def flaky(value):\n    return value.missing\n", T + "test_flaky"),
    _case("message_only", '"value over limit"', '"value too high"', T + "test_message"),
    _case("red_baseline", CHECK, "    pass", T + "test_red_baseline"),
    _case("skip_not_detection", CHECK, "    pass", T + "test_skipped"),
    _case("ambiguous_anchor", "    return value", "    return 0", T + "test_flaky"),
    _case("missing_anchor", "no such line", "x", T + "test_flaky"),
]
EXPECTED = {
    "real_guard_removed": "detected",
    "identity_only": "survived",
    "patch_target_shared": "detected",
    "error_not_detection": "not_detected_error",
    "message_only": "not_detected_message_only_failure",
    "red_baseline": "baseline_not_green",
    "skip_not_detection": "baseline_not_green",
    "ambiguous_anchor": "harness_error",
    "missing_anchor": "harness_error",
}


class SyntheticRepoMixin:
    def make_repo(self):
        tmp = Path(tempfile.mkdtemp(prefix="jh-harness-test-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "src").mkdir()
        (tmp / "tests").mkdir()
        (tmp / "src" / "__init__.py").write_text("", encoding="utf-8")
        (tmp / "src" / "guard.py").write_text(GUARD, encoding="utf-8")
        (tmp / "src" / "consumer.py").write_text(CONSUMER, encoding="utf-8")
        (tmp / "tests" / "test_guard.py").write_text(TESTS, encoding="utf-8")
        return tmp


class TestHarnessOnSyntheticRepo(SyntheticRepoMixin, unittest.TestCase):
    def test_each_verifier_classifies_every_shape(self):
        for script, module in VERIFIERS.items():
            with self.subTest(script=script):
                repo = self.make_repo()
                before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
                report = module.evaluate_mutations(repo, CASES)
                verdicts = {row["guard"]: row["verdict"] for row in report["rows"]}
                self.assertEqual(verdicts, EXPECTED)
                self.assertEqual(report["detected"], 2)
                self.assertEqual((report["verdict"], report["exit_code"]), ("fail", 1))
                after = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
                self.assertEqual(before, after, "harness must never touch the source checkout")

    def test_identity_runs_green_where_baseline_is_green(self):
        for script, module in VERIFIERS.items():
            with self.subTest(script=script):
                report = module.evaluate_mutations(self.make_repo(), CASES[:5])
                for row in report["rows"]:
                    self.assertEqual(row["outcomes"]["baseline"], "pass", row)
                    self.assertEqual(row["outcomes"]["identity"], "pass", row)

    def test_exit_codes(self):
        for script, module in VERIFIERS.items():
            with self.subTest(script=script):
                ok = module.evaluate_mutations(self.make_repo(), [CASES[0], CASES[2]])
                self.assertEqual((ok["verdict"], ok["exit_code"]), ("pass", 0))
                blocked = module.evaluate_mutations(self.make_repo(), [CASES[0], CASES[5]])
                self.assertEqual((blocked["verdict"], blocked["exit_code"]), ("platform_blocked", 2))
                survived = module.evaluate_mutations(self.make_repo(), [CASES[0], CASES[1]])
                self.assertEqual((survived["verdict"], survived["exit_code"]), ("fail", 1))


class TestClassification(unittest.TestCase):
    def test_outcomes(self):
        for module in VERIFIERS.values():
            base = {"tests_run": 1, "failures": [], "errors": [], "skipped": [],
                    "expected_failures": 0, "unexpected_successes": 0}
            self.assertEqual(module.outcome_of(base), "pass")
            self.assertEqual(module.outcome_of({**base, "errors": ["Traceback"]}), "error")
            self.assertEqual(module.outcome_of({**base, "skipped": ["no cmd"]}), "skipped")
            self.assertEqual(module.outcome_of({**base, "tests_run": 0}), "not_exactly_one_test")
            self.assertEqual(module.outcome_of({"harness_error": "x"}), "harness_error")
            regex = 'Traceback\nAssertionError: "staged" does not match "other text"'
            self.assertEqual(module.outcome_of({**base, "failures": [regex]}), "message_only_failure")
            text_line = textwrap.dedent('''\
                Traceback (most recent call last):
                  File "t.py", line 5, in test_x
                    self.assertIn("hash mismatch", result["error_info"])
                AssertionError: 'hash mismatch' not found in 'other'
                ''')
            self.assertEqual(module.outcome_of({**base, "failures": [text_line]}), "message_only_failure")
            status_line = textwrap.dedent('''\
                Traceback (most recent call last):
                  File "t.py", line 5, in test_x
                    self.assertEqual(result["status"], "needs_attention")
                AssertionError: 'succeeded' != 'needs_attention'
                ''')
            self.assertEqual(module.outcome_of({**base, "failures": [status_line]}), "assertion_failure")

    def test_classify(self):
        for module in VERIFIERS.values():
            self.assertEqual(module.classify("pass", "pass", "assertion_failure"), "detected")
            self.assertEqual(module.classify("pass", "pass", "pass"), "survived")
            self.assertEqual(module.classify("error", "error", "assertion_failure"), "baseline_not_green")
            self.assertEqual(module.classify("pass", "error", "assertion_failure"), "identity_not_green")
            self.assertEqual(module.classify("pass", "pass", "error"), "not_detected_error")
            self.assertEqual(module.classify("pass", "pass", "message_only_failure"),
                             "not_detected_message_only_failure")


class TestRealCatalogues(unittest.TestCase):
    """Read-only checks against this checkout: anchors unique, tests resolvable."""

    def test_anchors_unique_and_tests_resolve(self):
        for script, module in VERIFIERS.items():
            for case in module.build_cases():
                with self.subTest(script=script, guard=case["name"]):
                    source = (ROOT / case["path"]).read_text(encoding="utf-8")
                    self.assertEqual(source.count(case["original"]), 1)
                    self.assertNotEqual(case["original"], case["replacement"])
                    module_name, cls, method = case["test"].rsplit(".", 2)
                    test_src = (ROOT / "tests" / (module_name + ".py")).read_text(encoding="utf-8")
                    self.assertIn(f"class {cls}", test_src)
                    self.assertIn(f"def {method}(", test_src)

    def test_no_in_memory_exec_or_live_patch_remains(self):
        for script in VERIFIERS:
            text = (ROOT / "scripts" / f"{script}.py").read_text(encoding="utf-8")
            self.assertNotIn("exec(compile(", text)
            self.assertNotIn("patch.object(", text)


if __name__ == "__main__":
    unittest.main()
