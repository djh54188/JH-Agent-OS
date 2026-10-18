"""Regression tests for the isolated mutation harness used by
scripts/verify_phase4_guards.py and scripts/verify_jh006_guards.py (V-1, V-2,
review rounds R1 and R2).

Everything runs on a synthetic throw-away repository in a temp directory: the
real checkout is only read (anchor uniqueness and test-id resolution). No APIs.
"""
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _load(script):
    spec = importlib.util.spec_from_file_location("verifier_" + script, ROOT / "scripts" / f"{script}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFIERS = {name: _load(name) for name in ("verify_phase4_guards", "verify_jh006_guards")}
ON_WINDOWS = sys.platform == "win32"

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


def check_code(value):
    if value > limit():
        raise Refused("over_limit")
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


def result(value):
    try:
        guard.check(value)
    except Refused as exc:
        return {"status": "blocked", "error": str(exc)}
    return {"status": "allowed", "error": None}


def allowed(value):
    return run(value) == "allowed"
'''
TESTS = '''\
import time
import unittest
from unittest.mock import patch
from src import consumer, guard


def _mentions_limit(text):
    return text.endswith("over limit")


def _limit_hits(text):
    return text.count("over limit")


class TestGuard(unittest.TestCase):
    def test_over_limit_blocked(self):
        self.assertEqual(consumer.run(11), "blocked: value over limit")

    def test_status_blocked(self):
        self.assertEqual(consumer.result(11), {"status": "blocked", "error": "value over limit"})

    def test_raises(self):
        with self.assertRaises(guard.Refused):
            guard.check(11)

    def test_patched_limit_reaches_guard(self):
        with patch("src.guard.limit", return_value=0):
            self.assertEqual(consumer.result(1)["status"], "blocked")

    def test_flaky(self):
        self.assertEqual(guard.flaky(1), 1)

    def test_system_bool(self):
        self.assertFalse(consumer.allowed(11))

    def test_system_bool_local(self):
        ok = consumer.allowed(11)
        self.assertFalse(ok)


class TestWording(unittest.TestCase):
    """Every assertion here also reads refusal wording; R1 counter-examples."""

    def test_equal_on_exception_text(self):
        with self.assertRaises(guard.Refused) as ctx:
            guard.check(11)
        self.assertEqual(
            str(ctx.exception),
            "value over limit")

    def test_multiline_assert_in(self):
        self.assertIn(
            "over limit",
            consumer.run(11),
        )

    def test_equal_token_message(self):
        with self.assertRaises(guard.Refused) as ctx:
            guard.check_code(11)
        self.assertEqual(str(ctx.exception), "over_limit")

    def test_raises_regex(self):
        with self.assertRaisesRegex(guard.Refused, "over limit"):
            guard.check(11)

    def test_bool_from_text(self):
        result = {"error_info": consumer.run(11)}
        good = result["error_info"].endswith("over limit")
        self.assertTrue(good)

    def test_bool_inline_from_text(self):
        result = {"error_info": consumer.run(11)}
        self.assertTrue(result["error_info"].endswith("over limit"))

    def test_bool_from_helper(self):
        self.assertTrue(_mentions_limit(consumer.run(11)))

    def test_bool_local_from_helper(self):
        flag = _mentions_limit(consumer.run(11))
        self.assertTrue(flag)

    def test_int_local_from_helper(self):
        hits = _limit_hits(consumer.run(11))
        self.assertEqual(hits, 1)

    def test_bool_local_chain(self):
        text = consumer.run(11)
        good = text.endswith("over limit")
        verdict = good and True
        self.assertTrue(verdict)

    def test_bool_rebound_local(self):
        good = consumer.allowed(1)
        good = consumer.run(11).endswith("over limit")
        self.assertTrue(good)

    def test_bool_in_test_built_dict(self):
        checks = {"ok": consumer.run(11).endswith("over limit")}
        self.assertTrue(checks["ok"])

    def test_bare_assert(self):
        assert consumer.run(11) == "blocked: value over limit"


class TestBaselines(unittest.TestCase):
    def test_red_baseline(self):
        self.assertEqual(1, 2)

    @unittest.skip("platform")
    def test_vague_skip(self):
        pass

    # On Windows these run and exercise the guard (11 > limit), so removing the
    # guard is observable there. Not executed on Windows by this harness author.
    @unittest.skipUnless(__import__("sys").platform == "win32", "requires Windows junctions")
    def test_windows_skip(self):
        with self.assertRaises(guard.Refused):
            guard.check(11)

    def test_windows_module(self):
        import _winapi  # noqa: F401
        with self.assertRaises(guard.Refused):
            guard.check(11)

    def test_slow(self):
        time.sleep(30)
'''
T = "test_guard.TestGuard."
W = "test_guard.TestWording."
B = "test_guard.TestBaselines."
CHECK = '    if value > limit():\n        raise Refused("value over limit")'
MSG = '"value over limit"'
MSG_CHANGED = '"value too high"'


def _case(name, original, replacement, test, path="src/guard.py"):
    return {"name": name, "path": path, "original": original, "replacement": replacement, "test": test}


GUARD_REMOVED = "    pass"
CASES = [
    _case("real_guard_removed", CHECK, GUARD_REMOVED, T + "test_over_limit_blocked"),
    _case("status_changed", CHECK, GUARD_REMOVED, T + "test_status_blocked"),
    _case("raise_removed", CHECK, GUARD_REMOVED, T + "test_raises"),
    _case("identity_only", CHECK, CHECK, T + "test_over_limit_blocked"),
    _case("patch_target_shared", CHECK, GUARD_REMOVED, T + "test_patched_limit_reaches_guard"),
    _case("system_bool", CHECK, GUARD_REMOVED, T + "test_system_bool"),
    _case("system_bool_local", CHECK, GUARD_REMOVED, T + "test_system_bool_local"),
    _case("error_not_detection", "def flaky(value):\n    return value\n",
          "def flaky(value):\n    return value.missing\n", T + "test_flaky"),
    _case("ambiguous_anchor", "    return value", "    return 0", T + "test_flaky"),
    _case("missing_anchor", "no such line", "x", T + "test_flaky"),
]
EXPECTED = {
    "real_guard_removed": "detected",
    "status_changed": "detected",
    "raise_removed": "detected",
    "identity_only": "survived",
    "patch_target_shared": "detected",
    "system_bool": "detected",
    "system_bool_local": "detected",
    "error_not_detection": "not_detected_error",
    "ambiguous_anchor": "harness_error",
    "missing_anchor": "harness_error",
}
# R1: the refusal still happens; only its wording changes.
WORDING_CASES = [
    _case("equal_whole_message", MSG, MSG_CHANGED, T + "test_over_limit_blocked"),
    _case("equal_dict_message", MSG, MSG_CHANGED, T + "test_status_blocked"),
    _case("equal_exception_text_multiline", MSG, MSG_CHANGED, W + "test_equal_on_exception_text"),
    _case("assert_in_multiline", MSG, MSG_CHANGED, W + "test_multiline_assert_in"),
    _case("equal_token_message", '"over_limit"', '"too_high"', W + "test_equal_token_message"),
    _case("raises_regex", MSG, MSG_CHANGED, W + "test_raises_regex"),
    _case("bool_local_from_text", MSG, MSG_CHANGED, W + "test_bool_from_text"),
    _case("bool_inline_from_text", MSG, MSG_CHANGED, W + "test_bool_inline_from_text"),
    _case("bool_from_helper", MSG, MSG_CHANGED, W + "test_bool_from_helper"),
    _case("bool_local_from_helper", MSG, MSG_CHANGED, W + "test_bool_local_from_helper"),
    _case("int_local_from_helper", MSG, MSG_CHANGED, W + "test_int_local_from_helper"),
    _case("bool_local_chain", MSG, MSG_CHANGED, W + "test_bool_local_chain"),
    _case("bool_in_test_built_dict", MSG, MSG_CHANGED, W + "test_bool_in_test_built_dict"),
    _case("bool_rebound_local", MSG, MSG_CHANGED, W + "test_bool_rebound_local"),
    _case("bare_assert", MSG, MSG_CHANGED, W + "test_bare_assert"),
]
WORDING_EXPECTED = {
    "equal_whole_message": "not_detected_message_only_failure",
    "equal_dict_message": "not_detected_message_only_failure",
    "equal_exception_text_multiline": "not_detected_message_only_failure",
    "assert_in_multiline": "not_detected_message_only_failure",
    "equal_token_message": "not_detected_message_only_failure",
    "raises_regex": "not_detected_message_only_failure",
    "bool_local_from_text": "not_detected_undetermined_failure",
    "bool_inline_from_text": "not_detected_undetermined_failure",
    "bool_from_helper": "not_detected_undetermined_failure",
    "bool_local_from_helper": "not_detected_undetermined_failure",
    "int_local_from_helper": "not_detected_undetermined_failure",
    "bool_local_chain": "not_detected_undetermined_failure",
    "bool_in_test_built_dict": "not_detected_undetermined_failure",
    "bool_rebound_local": "not_detected_undetermined_failure",
    "bare_assert": "not_detected_undetermined_failure",
}
# R2: red baselines.
BASELINE_CASES = [
    _case("red_baseline", CHECK, GUARD_REMOVED, B + "test_red_baseline"),
    _case("vague_skip", CHECK, GUARD_REMOVED, B + "test_vague_skip"),
    _case("load_error", CHECK, GUARD_REMOVED, "test_guard.TestBaselines.test_does_not_exist"),
    _case("windows_skip", CHECK, GUARD_REMOVED, B + "test_windows_skip"),
    _case("windows_module", CHECK, GUARD_REMOVED, B + "test_windows_module"),
]
BASELINE_EXPECTED = {
    "red_baseline": "baseline_not_green",
    "vague_skip": "baseline_not_green",
    "load_error": "baseline_not_green",
    # On Windows these tests run, call check(11) and so exercise the removed
    # guard: the expected verdict there is "detected". NOT MEASURED ON WINDOWS.
    "windows_skip": "detected" if ON_WINDOWS else "platform_blocked",
    "windows_module": "detected" if ON_WINDOWS else "platform_blocked",
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

    def verdicts(self, report):
        return {row["guard"]: row["verdict"] for row in report["rows"]}


class TestHarnessOnSyntheticRepo(SyntheticRepoMixin, unittest.TestCase):
    def test_each_verifier_classifies_every_shape(self):
        for script, module in VERIFIERS.items():
            with self.subTest(script=script):
                repo = self.make_repo()
                before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
                report = module.evaluate_mutations(repo, CASES)
                self.assertEqual(self.verdicts(report), EXPECTED)
                self.assertEqual(report["detected"], 6)
                self.assertEqual((report["verdict"], report["exit_code"]), ("fail", 1))
                after = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
                self.assertEqual(before, after, "harness must never touch the source checkout")

    def test_detections_carry_behavioural_evidence(self):
        for script, module in VERIFIERS.items():
            with self.subTest(script=script):
                report = module.evaluate_mutations(self.make_repo(), CASES[:3] + CASES[5:7])
                for row in report["rows"]:
                    self.assertEqual(row["verdict"], "detected", row)
                    self.assertEqual([ev["verdict"] for ev in row["mutant_evidence"]], ["behavioral"], row)


class TestWordingOnlyChangesNeverDetect(SyntheticRepoMixin, unittest.TestCase):
    """R1: wording-only mutations must not count, whatever the assertion shape."""

    def test_wording_only_mutations(self):
        for script, module in VERIFIERS.items():
            with self.subTest(script=script):
                report = module.evaluate_mutations(self.make_repo(), WORDING_CASES)
                self.assertEqual(self.verdicts(report), WORDING_EXPECTED)
                self.assertEqual(report["detected"], 0)
                self.assertEqual((report["verdict"], report["exit_code"]), ("fail", 1))
                for row in report["rows"]:
                    # original and identity controls stay green
                    self.assertEqual(row["outcomes"]["baseline"], "pass", row)
                    self.assertEqual(row["outcomes"]["identity"], "pass", row)

    def test_wording_change_beside_a_real_detection_still_fails(self):
        for script, module in VERIFIERS.items():
            with self.subTest(script=script):
                report = module.evaluate_mutations(self.make_repo(), [CASES[0], WORDING_CASES[0],
                                                                      WORDING_CASES[3]])
                self.assertEqual(report["detected"], 1)
                self.assertEqual(report["exit_code"], 1)


class TestRedBaselines(SyntheticRepoMixin, unittest.TestCase):
    """R2: only explicit platform evidence makes a red baseline platform_blocked."""

    def test_baseline_shapes(self):
        for script, module in VERIFIERS.items():
            with self.subTest(script=script):
                report = module.evaluate_mutations(self.make_repo(), BASELINE_CASES)
                self.assertEqual(self.verdicts(report), BASELINE_EXPECTED)
                rows = {row["guard"]: row for row in report["rows"]}
                for name in ("red_baseline", "vague_skip", "load_error"):
                    self.assertNotIn("platform_evidence", rows[name])
                if not ON_WINDOWS:
                    self.assertIn("requires Windows", rows["windows_skip"]["platform_evidence"])
                    self.assertIn("_winapi", rows["windows_module"]["platform_evidence"])

    def test_exit_codes(self):
        for script, module in VERIFIERS.items():
            with self.subTest(script=script):
                repo = self.make_repo()
                ok = module.evaluate_mutations(repo, [CASES[0], CASES[4], CASES[5], CASES[6]])
                self.assertEqual((ok["verdict"], ok["exit_code"]), ("pass", 0))
                red = module.evaluate_mutations(repo, [CASES[0], BASELINE_CASES[0]])
                self.assertEqual((red["verdict"], red["exit_code"]), ("fail", 1))
                load = module.evaluate_mutations(repo, [CASES[0], BASELINE_CASES[2]])
                self.assertEqual((load["verdict"], load["exit_code"]), ("fail", 1))
                survived = module.evaluate_mutations(repo, [CASES[0], CASES[3]])
                self.assertEqual((survived["verdict"], survived["exit_code"]), ("fail", 1))
                if not ON_WINDOWS:
                    blocked = module.evaluate_mutations(repo, [CASES[0], BASELINE_CASES[4]])
                    self.assertEqual((blocked["verdict"], blocked["exit_code"]), ("platform_blocked", 2))
                    mixed = module.evaluate_mutations(repo, [BASELINE_CASES[4], BASELINE_CASES[0]])
                    self.assertEqual((mixed["verdict"], mixed["exit_code"]), ("fail", 1))

    def test_timeout_is_a_failure(self):
        for script, module in VERIFIERS.items():
            with self.subTest(script=script):
                report = module.evaluate_mutations(self.make_repo(),
                                                   [CASES[0], _case("slow", CHECK, GUARD_REMOVED,
                                                                    B + "test_slow")], timeout=5)
                self.assertEqual(self.verdicts(report)["slow"], "harness_error")
                self.assertEqual((report["verdict"], report["exit_code"]), ("fail", 1))


class TestClassification(unittest.TestCase):
    BASE = {"tests_run": 1, "failures": [], "failure_evidence": [], "errors": [], "skipped": [],
            "expected_failures": 0, "unexpected_successes": 0}

    def test_outcomes(self):
        base = self.BASE
        for module in VERIFIERS.values():
            self.assertEqual(module.outcome_of(base), "pass")
            self.assertEqual(module.outcome_of({**base, "errors": ["Traceback"]}), "error")
            self.assertEqual(module.outcome_of({**base, "skipped": ["no cmd"]}), "skipped")
            self.assertEqual(module.outcome_of({**base, "tests_run": 0}), "not_exactly_one_test")
            self.assertEqual(module.outcome_of({"harness_error": "x"}), "harness_error")

            def failing(*verdicts):
                return {**base, "failures": ["t"] * len(verdicts),
                        "failure_evidence": [{"verdict": v} for v in verdicts]}
            self.assertEqual(module.outcome_of(failing("behavioral")), "behavioral_failure")
            self.assertEqual(module.outcome_of(failing("message_only")), "message_only_failure")
            self.assertEqual(module.outcome_of(failing("undetermined")), "undetermined_failure")
            self.assertEqual(module.outcome_of(failing("message_only", "behavioral")), "behavioral_failure")
            # A failure without evidence (traceback text only) is never a detection.
            self.assertEqual(module.outcome_of({**base, "failures": [
                'Traceback\n    self.assertEqual(result["status"], "x")\nAssertionError: \'y\' != \'x\''
            ]}), "undetermined_failure")

    def test_classify(self):
        for module in VERIFIERS.values():
            self.assertEqual(module.classify("pass", "pass", "behavioral_failure"), "detected")
            self.assertEqual(module.classify("pass", "pass", "pass"), "survived")
            self.assertEqual(module.classify("error", "error", "behavioral_failure"), "baseline_not_green")
            self.assertEqual(module.classify("behavioral_failure", "behavioral_failure", "pass"),
                             "baseline_not_green")
            self.assertEqual(module.classify("error", "error", "error", "ModuleNotFoundError: x"),
                             "platform_blocked")
            self.assertEqual(module.classify("pass", "error", "behavioral_failure"), "identity_not_green")
            self.assertEqual(module.classify("pass", "pass", "error"), "not_detected_error")
            self.assertEqual(module.classify("pass", "harness_error", "pass"), "harness_error")
            self.assertEqual(module.classify("pass", "pass", "message_only_failure"),
                             "not_detected_message_only_failure")
            self.assertEqual(module.classify("pass", "pass", "undetermined_failure"),
                             "not_detected_undetermined_failure")

    def test_platform_evidence(self):
        base = self.BASE
        winapi = {**base, "errors": ["Traceback\nModuleNotFoundError: No module named '_winapi'\n"]}
        cmd = {**base, "errors": ["Traceback\nFileNotFoundError: [Errno 2] No such file or directory: 'cmd'"]}
        other_mod = {**base, "errors": ["Traceback\nModuleNotFoundError: No module named 'jsonschema'"]}
        assertion = {**base, "failures": ["AssertionError: 1 != 2"],
                     "failure_evidence": [{"verdict": "behavioral"}]}
        skip_win = {**base, "skipped": ["requires Windows junctions"]}
        skip_vague = {**base, "skipped": ["platform"]}
        for module in VERIFIERS.values():
            ev = module.platform_evidence
            self.assertIn("_winapi", ev(winapi, host="linux"))
            self.assertIsNone(ev(winapi, host="win32"))
            self.assertIn("cmd", ev(cmd, host="linux"))
            self.assertIsNone(ev(other_mod, host="linux"))
            self.assertIsNone(ev(assertion, host="linux"))
            self.assertIn("requires Windows", ev(skip_win, host="linux"))
            self.assertIsNone(ev(skip_win, host="win32"))
            self.assertIsNone(ev(skip_vague, host="linux"))
            self.assertIsNone(ev({"harness_error": "timeout after 5s"}, host="linux"))
            self.assertIsNone(ev({**base, "tests_run": 0, "errors": winapi["errors"]}, host="linux"))

    def test_summarize(self):
        for module in VERIFIERS.values():
            def rows(*verdicts):
                return [{"verdict": v} for v in verdicts]
            self.assertEqual(module.summarize(rows("detected"))[2:], (0, "pass"))
            self.assertEqual(module.summarize(rows("detected", "platform_blocked"))[2:], (2, "platform_blocked"))
            self.assertEqual(module.summarize(rows("detected", "baseline_not_green"))[2:], (1, "fail"))
            self.assertEqual(module.summarize(rows("platform_blocked", "survived"))[2:], (1, "fail"))
            self.assertEqual(module.summarize(rows())[2:], (1, "fail"))


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

    def test_harness_blocks_identical_and_free_of_old_mechanisms(self):
        blocks = []
        for script in VERIFIERS:
            text = (ROOT / "scripts" / f"{script}.py").read_text(encoding="utf-8")
            self.assertNotIn("exec(compile(", text)
            self.assertNotIn("patch.object(", text)
            self.assertNotIn("_failing_source_line", text)
            start = text.index("# Isolated mutation harness")
            end = text.index("\ndef evaluate_mutations(")
            blocks.append(text[start:text.index("rows}", end)])
        self.assertEqual(blocks[0], blocks[1])


if __name__ == "__main__":
    unittest.main()
