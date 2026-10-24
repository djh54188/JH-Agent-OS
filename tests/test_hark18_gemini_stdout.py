"""Hark-派单-18: the known Gemini CLI 0.65.0 ProjectRegistry.save retry line before init.

All streams here are CONSTRUCTED inputs: the captured 0.65.0 template (fake_cli.py)
with raw bytes added by fake_cli_h18.py. The ORIGINAL node22-run1 stdout is replayed
only when HARK18_ORIGINAL_STDOUT names it (Windows cell); otherwise that test SKIPS
and is counted as a skip, never as a pass. No real CLI, no model.
"""
import base64
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from src import agent_session_gemini as gem  # noqa: E402
from src.agent_session import RunState  # noqa: E402
import test_hark11_gemini_session as h11  # noqa: E402

FAKE_H18 = ROOT / "tests" / "fixtures" / "gemini_cli" / "fake_cli_h18.py"
KNOWN = b"Rename failed with EPERM, retrying in 50ms (attempt 1/5)...\n"
ORIGINAL_SHA256 = "4f8effca4110d539f64bf06a90cb830b593f706ec82a4e3a65a28c8a67ffa4aa"
ORIGINAL_BYTES = 3212
ORIGINAL_SESSION = "8911e128-a644-4360-8412-de5b2606d832"
TEMPLATE = (ROOT / "tests" / "fixtures" / "gemini_cli" / "host_probe_stream.template.jsonl").read_bytes()
STREAM = TEMPLATE.replace(b"{SESSION_ID}", b"s-1").replace(b"{WORKSPACE}", b"/w")


def retry(code, attempt):
    return f"Rename failed with {code}, retrying in {50 * 2 ** (attempt - 1)}ms (attempt {attempt}/5)...\n".encode()


def b64(data):
    return base64.b64encode(data).decode("ascii")


class TestParser(unittest.TestCase):
    """Pure parser: accepted preambles vs. everything that must keep the strict result."""

    def assert_strict(self, data):
        events, problems, pre = gem.parse_stream_with_host_preamble(data)
        self.assertEqual(pre, [])
        self.assertEqual((events, problems), gem.parse_stream(data))
        self.assertTrue(problems)

    def test_baseline_reproduces_known_line_failure(self):
        events, problems = gem.parse_stream(KNOWN + STREAM)
        self.assertEqual(problems, ["line 1: not JSON"])

    def test_known_line_kept_as_diagnostic(self):
        events, problems, pre = gem.parse_stream_with_host_preamble(KNOWN + STREAM)
        self.assertEqual(problems, [])
        self.assertEqual(events, gem.parse_stream(STREAM)[0])
        self.assertEqual(pre, [{"line": 1, "text": KNOWN[:-1].decode(), "bytes": len(KNOWN),
                                "sha256": hashlib.sha256(KNOWN).hexdigest(), "code": "EPERM",
                                "delay_ms": 50, "attempt": 1, "max_attempts": 5}])

    def test_no_preamble_control_unchanged(self):
        self.assertEqual(gem.parse_stream_with_host_preamble(STREAM), gem.parse_stream(STREAM) + ([],))

    def test_accepted_bounded_forms(self):
        forms = {
            "ebusy": retry("EBUSY", 1),
            "full_group": b"".join(retry("EPERM", a) for a in (1, 2, 3, 4)),
            "mixed_codes": retry("EPERM", 1) + retry("EBUSY", 2),
            "two_saves": retry("EPERM", 1) + retry("EPERM", 2) + retry("EBUSY", 1),
            "max_lines": b"".join(retry("EPERM", a) for a in (1, 2, 3, 4)) * 2,
        }
        for name, pre in forms.items():
            with self.subTest(name):
                events, problems, kept = gem.parse_stream_with_host_preamble(pre + STREAM)
                self.assertEqual(problems, [])
                self.assertEqual(sum(k["bytes"] for k in kept), len(pre))

    def test_rejected_forms_keep_strict_result(self):
        bad = {
            "unknown": b"Unknown host message\n",
            "other_code": b"Rename failed with EACCES, retrying in 50ms (attempt 1/5)...\n",
            "wrong_delay_for_attempt": b"Rename failed with EPERM, retrying in 100ms (attempt 1/5)...\n",
            "unknown_delay": b"Rename failed with EPERM, retrying in 60ms (attempt 1/5)...\n",
            "attempt_5": b"Rename failed with EPERM, retrying in 800ms (attempt 5/5)...\n",
            "attempt_0": b"Rename failed with EPERM, retrying in 25ms (attempt 0/5)...\n",
            "other_max": b"Rename failed with EPERM, retrying in 50ms (attempt 1/6)...\n",
            "truncated": b"Rename failed with EPERM, retrying in 50ms (attempt 1/5)..\n",
            "trailing_text": b"Rename failed with EPERM, retrying in 50ms (attempt 1/5)... x\n",
            "leading_space": b" " + KNOWN,
            "crlf": KNOWN[:-1] + b"\r\n",
            "bom": b"\xef\xbb\xbf" + KNOWN,
            "starts_at_2": retry("EPERM", 2),
            "gap": retry("EPERM", 1) + retry("EPERM", 3),
            "three_saves": retry("EPERM", 1) * 3,
            "too_many_lines": b"".join(retry("EPERM", a) for a in (1, 2, 3, 4)) * 2 + retry("EPERM", 1),
            "blank_line_between": KNOWN + b"\n",
            "json_string": json.dumps(KNOWN[:-1].decode()).encode() + b"\n",
        }
        for name, pre in bad.items():
            with self.subTest(name):
                self.assert_strict(pre + STREAM)

    def test_known_line_not_followed_by_init(self):
        lines = STREAM.splitlines(keepends=True)
        for name, data in {"message_first": KNOWN + b"".join(lines[1:]),
                           "bad_json_next": KNOWN + b"{broken\n" + STREAM,
                           "non_object_next": KNOWN + b"[1]\n" + STREAM,
                           "eof_after_line": KNOWN,
                           "eof_mid_line": KNOWN[:20]}.items():
            with self.subTest(name):
                self.assert_strict(data)

    def test_same_text_elsewhere_is_still_a_problem(self):
        lines = STREAM.splitlines(keepends=True)
        for name, data in {"after_init": lines[0] + KNOWN + b"".join(lines[1:]),
                           "after_result": STREAM + KNOWN,
                           "after_accepted_preamble_and_init": KNOWN + lines[0] + KNOWN + b"".join(lines[1:])}.items():
            with self.subTest(name):
                events, problems, pre = gem.parse_stream_with_host_preamble(data)
                self.assertTrue(problems, name)

    def test_line_numbers_are_original(self):
        lines = STREAM.splitlines(keepends=True)
        data = KNOWN + lines[0] + b"{broken\n" + b"".join(lines[1:])
        self.assertEqual(gem.parse_stream_with_host_preamble(data)[1], ["line 3: not JSON"])

    def test_bad_utf8_after_preamble(self):
        self.assert_strict(KNOWN + STREAM + b"\xff\n")

    def test_same_text_inside_json_field_is_not_stripped(self):
        event = {"type": "message", "role": "assistant", "content": KNOWN.decode(), "timestamp": "t"}
        lines = STREAM.splitlines(keepends=True)
        data = KNOWN + lines[0] + json.dumps(event).encode() + b"\n" + b"".join(lines[1:])
        events, problems, pre = gem.parse_stream_with_host_preamble(data)
        self.assertEqual(problems, [])
        self.assertEqual(events[1]["content"], KNOWN.decode())


class _H18Harness(h11._Harness):
    def config(self):
        return gem.GeminiCliConfig(argv_prefix=(sys.executable, str(FAKE_H18)), cli_home=self.home,
                                   evidence_root=self.evidence,
                                   extra_env=(("FAKE_GEMINI_SCENARIO", self.scenario_path),))


class TestAdapter(unittest.TestCase):
    def run_case(self, scenario):
        scenario = dict(scenario)
        model = scenario.pop("model", None)
        address = h11.SessionAddress(app_kind="gemini-cli", instance_id="gemini-cli/local",
                                     session_id="new", model_id=model) if model else h11.ADDRESS
        self.h = _H18Harness(scenario)
        self.addCleanup(self.h.close)
        adapter = self.h.adapter()
        submitted = adapter.submit(self.h.request(session=address))
        result = adapter.collect(address, submitted.run_id, timeout=60)
        status = adapter.status(address, submitted.run_id)
        return adapter, submitted, result, status

    def stdout_bytes(self, result):
        path = next(p for p in result.raw_refs if str(p).endswith("stdout.jsonl"))
        return Path(path).read_bytes()

    def test_control_without_preamble(self):
        _, _, result, status = self.run_case({})
        self.assertEqual(status.state, RunState.DECLARED_COMPLETE)
        self.assertNotIn("host_diagnostics", result.artifacts[0])
        self.assertFalse(any("preamble" in u for u in result.unverified))

    def test_known_preamble_collects_and_keeps_bytes(self):
        _, _, result, status = self.run_case({"h18_prefix_b64": b64(KNOWN)})
        self.assertEqual(status.state, RunState.DECLARED_COMPLETE, result.unverified)
        self.assertEqual(result.declared_status, "success")
        raw = self.stdout_bytes(result)
        self.assertTrue(raw.startswith(KNOWN))
        diag = result.artifacts[0]["host_diagnostics"]
        self.assertEqual(diag["stdout_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(diag["preamble_sha256"], hashlib.sha256(KNOWN).hexdigest())
        self.assertEqual([l["text"] for l in diag["lines"]], [KNOWN[:-1].decode()])
        self.assertIn("independent review decides success", result.unverified[0])
        self.assertTrue(any("host stdout preamble: 1 known" in u for u in result.unverified))
        self.assertTrue(result.artifacts[0]["workspace_unchanged"])

    def assert_failed(self, scenario, expect=None):
        scenario = dict(scenario, h18_prefix_b64=b64(KNOWN))
        _, _, result, status = self.run_case(scenario)
        self.assertEqual(status.state, RunState.FAILED, result.unverified)
        if expect:
            self.assertTrue(any(expect in u for u in result.unverified), result.unverified)
        return result

    def test_final_save_failure_on_stderr_fails(self):
        self.assert_failed({"h18_stderr_b64": b64(b"Failed to save project registry to /h/projects.json: Error: EPERM\n")},
                           "host project registry save failed")

    def test_stderr_unavailable_fails(self):
        real = os.path.isfile
        with mock.patch.object(gem.os.path, "isfile", lambda p: False if str(p).endswith("stderr.txt") else real(p)):
            self.assert_failed({}, "stderr evidence unavailable")

    def test_combined_faults_still_fail(self):
        cases = {
            "nonzero_exit": {"exit_code": 1},
            "session_mismatch": {"session_override": "00000000-0000-4000-8000-000000000000"},
            "model_mismatch": {"model": "gemini-2.5-flash",
                               "edit_events": [{"type": "init", "index": 0, "set": {"model": "other-model"}}]},
            "tool_unpaired": {"drop_types": ["tool_result"]},
            "denial_not_confirmed": {"tool_success_override": "write_file"},
            "workspace_modified": {"mutate": "new_file.txt"},
            "host_result_error": {"edit_events": [{"type": "result", "index": 0, "set": {"status": "error"}}]},
            "no_result": {"drop_types": ["result"]},
            "unknown_after_init": {"h18_insert": [{"after_line": 1, "b64": b64(b"Unknown host message\n")}]},
            "same_text_after_result": {"h18_insert": [{"after_line": 12, "b64": b64(KNOWN)}]},
        }
        for name, scenario in cases.items():
            with self.subTest(name):
                self.assert_failed(scenario)

    def test_requested_model_honored_with_preamble(self):
        _, _, result, status = self.run_case({"h18_prefix_b64": b64(KNOWN), "model": "gemini-2.5-flash"})
        self.assertEqual(status.state, RunState.DECLARED_COMPLETE, result.unverified)

    def test_unknown_preamble_still_fails(self):
        _, _, result, status = self.run_case({"h18_prefix_b64": b64(b"Unknown host message\n")})
        self.assertEqual(status.state, RunState.FAILED)
        self.assertIn("line 1: not JSON", result.unverified)

    def test_collect_again_does_not_redispatch(self):
        adapter, submitted, first, _ = self.run_case({"h18_prefix_b64": b64(KNOWN), "exit_code": 1})
        again = adapter.collect(h11.ADDRESS, submitted.run_id, timeout=5)
        self.assertEqual(len(self.h.launches), 1)
        self.assertEqual(again.declared_status, first.declared_status)


@unittest.skipUnless(os.environ.get("HARK18_ORIGINAL_STDOUT"), "original node22-run1 stdout not provided (Windows cell)")
class TestOriginalRun1Replay(unittest.TestCase):
    def test_original_bytes(self):
        data = Path(os.environ["HARK18_ORIGINAL_STDOUT"]).read_bytes()
        self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (ORIGINAL_BYTES, ORIGINAL_SHA256))
        self.assertEqual(gem.parse_stream(data)[1], ["line 1: not JSON"])            # baseline reproduced
        events, problems, pre = gem.parse_stream_with_host_preamble(data)
        self.assertEqual(problems, [])
        self.assertEqual([p["text"] for p in pre], [KNOWN[:-1].decode()])
        assoc = gem.associate(events, ORIGINAL_SESSION)
        self.assertEqual(assoc["problems"], [])
        self.assertEqual(assoc["result"]["status"], "success")


if __name__ == "__main__":
    unittest.main()


# Guard -> (exact source text, weakened text, target test). Each target first passes on an
# unmodified copy (control), then must fail on the weakened copy.
GUARDS = {
    "exact_line": ("HOST_PREAMBLE_LINE.fullmatch(data, pos, end)", "HOST_PREAMBLE_LINE.match(data, pos, end)",
                   "TestParser.test_rejected_forms_keep_strict_result"),
    "delay_matches_attempt": ("if delay != 50 * 2 ** (attempt - 1):", "if False:",
                              "TestParser.test_rejected_forms_keep_strict_result"),
    "attempt_sequence": ("elif attempt != previous + 1:", "elif False:",
                         "TestParser.test_rejected_forms_keep_strict_result"),
    "bounds": ("if saves > HOST_PREAMBLE_MAX_SAVES or len(lines) >= HOST_PREAMBLE_MAX_LINES:", "if False:",
               "TestParser.test_rejected_forms_keep_strict_result"),
    "init_follows": ('if not (isinstance(value, dict) and value.get("type") == "init"):', "if False:",
                     "TestParser.test_known_line_not_followed_by_init"),
    "leading_only": ("        if not line.strip():\n            continue\n        try:",
                     "        if not line.strip() or HOST_PREAMBLE_LINE.fullmatch(line.encode()):\n            continue\n        try:",
                     "TestParser.test_same_text_elsewhere_is_still_a_problem"),
    "final_save_failure": ("elif HOST_SAVE_FINAL_FAILURE in stderr_bytes:", "elif False:",
                           "TestAdapter.test_final_save_failure_on_stderr_fails"),
    "stderr_required": ("            if stderr_bytes is None:\n                parse_problems.append(",
                        "            if False:\n                parse_problems.append(",
                        "TestAdapter.test_stderr_unavailable_fails"),
}


class TestGuardWeakening(unittest.TestCase):
    def _copy(self, tmp, name, text):
        import shutil
        dest = Path(tmp) / name
        shutil.copytree(ROOT / "src", dest / "src")
        shutil.copytree(ROOT / "tests", dest / "tests", ignore=shutil.ignore_patterns("__pycache__"))
        (dest / "src" / "agent_session_gemini.py").write_text(text, encoding="utf-8")
        return dest

    def _run(self, root, target):
        import subprocess
        done = subprocess.run([sys.executable, "-m", "unittest", "tests.test_hark18_gemini_stdout." + target],
                              cwd=root, capture_output=True, text=True, timeout=300)
        tail = done.stderr.strip().splitlines()[-1] if done.stderr.strip() else ""
        if done.returncode == 0 and "skipped" in tail:
            return "skip"
        return "pass" if done.returncode == 0 else "fail"

    def test_each_guard_is_effective_then_detected(self):
        import tempfile
        original = (ROOT / "src" / "agent_session_gemini.py").read_text(encoding="utf-8")
        rows = []
        with tempfile.TemporaryDirectory(prefix="hark18-weak-") as tmp:
            control_root = self._copy(tmp, "control", original)
            for name, (exact, weak, target) in GUARDS.items():
                with self.subTest(guard=name):
                    self.assertEqual(original.count(exact), 1, f"guard text for {name} not unique")
                    control = self._run(control_root, target)
                    weak_root = self._copy(tmp, "weak-" + name, original.replace(exact, weak, 1))
                    weakened = self._run(weak_root, target)
                    rows.append(f"{name}\tcontrol={control}\tweakened={'detected' if weakened == 'fail' else 'MISSED'}\t{target}")
                    self.assertEqual(control, "pass")
                    self.assertEqual(weakened, "fail")
        log = os.environ.get("HARK18_WEAKENING_LOG")
        if log:
            Path(log).write_text("\n".join(rows) + "\n", encoding="utf-8")
