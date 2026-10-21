"""Hark-派单-11: Gemini CLI AgentSessionAdapter, offline.

The CLI process is the stand-in tests/fixtures/gemini_cli/fake_cli.py, which
replays a native stream captured from the real Gemini CLI 0.65.0 (driven by the
synthetic endpoint, no model). No test here starts the real CLI or calls a model.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import agent_session_gemini as gem  # noqa: E402
from src.agent_session import (  # noqa: E402
    AddressMismatch, AgentSessionAdapter, IdempotencyConflict, RunState, SessionAddress,
    SubmitRequest, UnsupportedCapability)

FAKE_CLI = ROOT / "tests" / "fixtures" / "gemini_cli" / "fake_cli.py"
ADDRESS = SessionAddress(app_kind="gemini-cli", instance_id="gemini-cli/local", session_id="new")


def _source_file() -> str:
    lines = [f"# line {i}" for i in range(1, 140)]
    lines[93] = "class RunState:"
    return "\n".join(lines) + "\n"


class _Harness:
    def __init__(self, scenario=None):
        self.tmp = tempfile.mkdtemp(prefix="hark11-")
        base = os.path.realpath(self.tmp)
        self.workspace = os.path.join(base, "ws")
        os.makedirs(os.path.join(self.workspace, "src"))
        with open(os.path.join(self.workspace, "src", "agent_session.py"), "w", encoding="utf-8") as fh:
            fh.write(_source_file())
        self.evidence = os.path.join(base, "evidence")
        self.home = os.path.join(base, "home")
        os.makedirs(self.home)
        self.scenario_dir = os.path.join(base, "scenario")
        os.makedirs(self.scenario_dir)
        self.set_scenario(scenario or {})
        self.launches = []

    def set_scenario(self, scenario):
        self.scenario_path = os.path.join(self.scenario_dir, "scenario.json")
        with open(self.scenario_path, "w", encoding="utf-8") as fh:
            json.dump(scenario, fh)

    def config(self):
        return gem.GeminiCliConfig(argv_prefix=(sys.executable, str(FAKE_CLI)), cli_home=self.home,
                                   evidence_root=self.evidence,
                                   extra_env=(("FAKE_GEMINI_SCENARIO", self.scenario_path),))

    def adapter(self):
        def launcher(argv, **kwargs):
            self.launches.append(argv)
            return gem.default_launcher(argv, **kwargs)
        return gem.GeminiCliAgentSessionAdapter(self.config(), launcher=launcher)

    def request(self, **overrides):
        values = dict(task_id="t-locate", attempt=1, session=ADDRESS,
                      instructions="Locate class RunState.", workspace=self.workspace,
                      idempotency_key="k1")
        values.update(overrides)
        return SubmitRequest(**values)

    def argv_seen(self):
        with open(os.path.join(self.scenario_dir, "argv.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class _Base(unittest.TestCase):
    scenario = None

    def setUp(self):
        self.h = _Harness(self.scenario)
        self.addCleanup(self.h.close)

    def run_once(self, **overrides):
        adapter = self.h.adapter()
        submitted = adapter.submit(self.h.request(**overrides))
        result = adapter.collect(ADDRESS, submitted.run_id, timeout=60)
        return adapter, submitted, result


class TestValidDelivery(_Base):
    def test_valid_delivery_is_declared_not_succeeded(self):
        adapter, submitted, result = self.run_once()
        status = adapter.status(ADDRESS, submitted.run_id)
        self.assertEqual(status.state, RunState.DECLARED_COMPLETE)
        self.assertEqual(result.declared_status, "success")
        self.assertNotEqual(status.state, "succeeded")
        art = result.artifacts[0]
        self.assertEqual(art["native_session_id"], adapter.inspect(submitted.run_id).native_session_id)
        self.assertTrue(art["workspace_unchanged"])
        names = [c["tool_name"] for c in art["tool_calls"]]
        self.assertEqual(names, ["grep_search", "read_file", "write_file", "run_shell_command"])
        self.assertTrue(all(c["result"] is not None for c in art["tool_calls"]))
        self.assertEqual(sorted(art["denied_tool_requests"]), ["run_shell_command", "write_file"])
        self.assertEqual([r["verified"] for r in art["source_refs"]], [True])
        self.assertEqual(art["host_reported_model"], "gemini-2.5-flash")
        self.assertIn("synthetic-endpoint", art["backend_models"])
        self.assertIn("native turn ids: not exposed by stream-json; turn count unknown", result.unverified)
        self.assertEqual(result.usage["tool_calls"], 4)

    def test_host_restriction_flags_are_passed(self):
        adapter, submitted, _ = self.run_once()
        argv = self.h.argv_seen()
        self.assertEqual(argv[argv.index("--approval-mode") + 1], "default")
        policy = argv[argv.index("--admin-policy") + 1]
        with open(policy, "rb") as fh:
            text = fh.read().decode("utf-8")
        self.assertIn('toolName = "*"\ndecision = "deny"', text)
        for tool in gem.READ_ONLY_TOOLS:
            self.assertIn(f'"{tool}"', text)
        self.assertNotIn("--yolo", argv)
        self.assertNotIn("yolo", argv)
        self.assertEqual(argv[argv.index("-o") + 1], "stream-json")
        self.assertEqual(argv[argv.index("--session-id") + 1],
                         adapter.inspect(submitted.run_id).native_session_id)
        self.assertFalse(policy.startswith(self.h.workspace))

    def test_evidence_bytes_are_verified_on_disk(self):
        adapter, submitted, result = self.run_once()
        registry = os.path.join(self.h.evidence, "registry.jsonl")
        report = gem.verify_run_evidence(registry, submitted.run_id)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["files"]["stdout.jsonl"], "match")
        stdout = adapter.inspect(submitted.run_id).run_dir + os.sep + "stdout.jsonl"
        with open(stdout, "r+b") as fh:          # same size, one byte flipped
            first = fh.read(1)
            fh.seek(0)
            fh.write(b"[" if first != b"[" else b"{")
        self.assertFalse(gem.verify_run_evidence(registry, submitted.run_id)["ok"])

    def test_capabilities_and_unsupported_operations(self):
        adapter = self.h.adapter()
        self.assertTrue(issubclass(gem.GeminiCliAgentSessionAdapter, AgentSessionAdapter))
        caps = adapter.capabilities()
        for name in ("capabilities", "submit", "status", "collect"):
            self.assertTrue(caps[name]["supported"], name)
        for name in ("continue", "cancel"):
            self.assertFalse(caps[name]["supported"], name)
        with self.assertRaises(UnsupportedCapability):
            adapter.continue_run(ADDRESS, "x", "more")
        with self.assertRaises(UnsupportedCapability):
            adapter.cancel(ADDRESS, "x")


class TestAssociationErrors(_Base):
    def _expect_failed(self, scenario, fragment):
        self.h.set_scenario(scenario)
        adapter, submitted, result = self.run_once()
        self.assertEqual(adapter.status(ADDRESS, submitted.run_id).state, RunState.FAILED)
        self.assertTrue(any(fragment in u for u in result.unverified), result.unverified)
        return result

    def test_foreign_session_is_not_adopted(self):
        result = self._expect_failed({"session_override": "00000000-0000-4000-8000-000000000000"},
                                     "init session")
        self.assertEqual(result.declared_status, "unattributed")

    def test_tool_result_without_tool_use(self):
        self._expect_failed({"drop_types": ["tool_use"]}, "has no preceding tool_use")

    def test_tool_use_without_result(self):
        self._expect_failed({"drop_types": ["tool_result"]}, "has no tool_result")

    def test_missing_result_event(self):
        self._expect_failed({"drop_types": ["result"]}, "expected exactly one result event")

    def test_duplicate_init(self):
        extra = json.dumps({"type": "init", "session_id": "other", "model": "x"})
        self._expect_failed({"extra_lines": [extra]}, "expected exactly one init event")

    def test_non_native_line(self):
        self._expect_failed({"extra_lines": ['{"type":"made_up"}']}, "not a native stream-json event")

    def test_nonzero_exit(self):
        self.h.set_scenario({"exit_code": 3})
        adapter, submitted, result = self.run_once()
        self.assertEqual(adapter.status(ADDRESS, submitted.run_id).state, RunState.FAILED)

    def test_requested_model_not_honored(self):
        address = SessionAddress(app_kind="gemini-cli", instance_id="gemini-cli/local",
                                 session_id="new", model_id="gemini-2.5-pro")
        adapter = self.h.adapter()
        submitted = adapter.submit(self.h.request(session=address))
        result = adapter.collect(address, submitted.run_id, timeout=60)
        self.assertEqual(adapter.status(address, submitted.run_id).state, RunState.FAILED)
        self.assertTrue(any("not honored" in u for u in result.unverified))
        argv = self.h.argv_seen()
        self.assertEqual(argv[argv.index("-m") + 1], "gemini-2.5-pro")

    def test_address_mismatch(self):
        adapter, submitted, _ = self.run_once()
        other = SessionAddress(app_kind="gemini-cli", instance_id="gemini-cli/other", session_id="new")
        with self.assertRaises(AddressMismatch):
            adapter.status(other, submitted.run_id)


class TestEvidenceMissing(_Base):
    def test_stdout_missing_fails(self):
        adapter = self.h.adapter()
        submitted = adapter.submit(self.h.request())
        run = adapter.inspect(submitted.run_id)
        run.process.wait(timeout=60)
        os.remove(os.path.join(run.run_dir, "stdout.jsonl"))
        result = adapter.collect(ADDRESS, submitted.run_id, timeout=60)
        self.assertEqual(adapter.status(ADDRESS, submitted.run_id).state, RunState.FAILED)
        self.assertIn("evidence missing: stdout.jsonl", result.unverified)

    def test_chat_record_missing_is_listed(self):
        self.h.set_scenario({"chat_record": False})
        _, _, result = self.run_once()
        self.assertIn("host chat record for this session not found", result.unverified)

    def test_tampered_before_manifest_fails(self):
        adapter = self.h.adapter()
        submitted = adapter.submit(self.h.request())
        run = adapter.inspect(submitted.run_id)
        run.process.wait(timeout=60)
        with open(os.path.join(run.run_dir, "workspace-before.json"), "ab") as fh:
            fh.write(b" ")
        result = adapter.collect(ADDRESS, submitted.run_id, timeout=60)
        self.assertEqual(adapter.status(ADDRESS, submitted.run_id).state, RunState.FAILED)
        self.assertTrue(any("does not match the registered digest" in u for u in result.unverified))


class TestReadOnly(_Base):
    def test_workspace_modification_is_detected(self):
        self.h.set_scenario({"mutate": "new_file.txt"})
        adapter, submitted, result = self.run_once()
        self.assertEqual(adapter.status(ADDRESS, submitted.run_id).state, RunState.FAILED)
        self.assertFalse(result.artifacts[0]["workspace_unchanged"])

    def test_successful_write_tool_is_a_breach(self):
        self.h.set_scenario({"tool_success_override": "write_file"})
        adapter, submitted, result = self.run_once()
        self.assertEqual(adapter.status(ADDRESS, submitted.run_id).state, RunState.FAILED)
        self.assertTrue(any("host restriction breached" in u for u in result.unverified))

    def _refused(self, exc_type, **overrides):
        adapter = self.h.adapter()
        with self.assertRaises(exc_type):
            adapter.submit(self.h.request(**overrides))
        self.assertEqual(self.h.launches, [])
        self.assertFalse(os.path.exists(os.path.join(self.h.evidence, "registry.jsonl")))

    def test_workspace_host_config_refused(self):
        os.makedirs(os.path.join(self.h.workspace, ".gemini"))
        self._refused(gem.HostRestrictionUnavailable)

    def test_workspace_env_file_refused(self):
        with open(os.path.join(self.h.workspace, ".env"), "w") as fh:
            fh.write("X=1\n")
        self._refused(gem.HostRestrictionUnavailable)

    def test_ancestor_env_file_refused(self):
        with open(os.path.join(os.path.dirname(self.h.workspace), ".env"), "w") as fh:
            fh.write("X=1\n")
        self._refused(gem.HostRestrictionUnavailable)

    def test_overlapping_evidence_refused(self):
        h = self.h
        bad = gem.GeminiCliConfig(argv_prefix=(sys.executable, str(FAKE_CLI)), cli_home=h.home,
                                  evidence_root=os.path.join(h.workspace, "evidence"),
                                  extra_env=(("FAKE_GEMINI_SCENARIO", h.scenario_path),))
        with self.assertRaises(gem.HostRestrictionUnavailable):
            gem.GeminiCliAgentSessionAdapter(bad).submit(h.request())

    def test_unpinned_version_refused(self):
        self.h.set_scenario({"version": "0.66.0"})
        self._refused(gem.HostRestrictionUnavailable)

    def test_path_scope_and_budget_refused(self):
        self._refused(gem.UnsupportedRequest, allowed_scope=("src/a.py",))
        self._refused(gem.UnsupportedRequest, budget_tokens=1000)

    def test_existing_session_refused(self):
        address = SessionAddress(app_kind="gemini-cli", instance_id="gemini-cli/local",
                                 session_id="11111111-2222-4333-8444-555555555555")
        self._refused(gem.UnsupportedAddress, session=address)

    def test_relative_workspace_refused(self):
        self._refused(gem.HostRestrictionUnavailable, workspace="relative/ws")


class TestUnknownNotResent(_Base):
    def test_bounded_wait_and_no_redispatch(self):
        self.h.set_scenario({"sleep": 3})
        adapter = self.h.adapter()
        request = self.h.request()
        submitted = adapter.submit(request)
        started = time.monotonic()
        result = adapter.collect(ADDRESS, submitted.run_id, timeout=0.3)
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertTrue(any("bounded wait" in u for u in result.unverified))
        again = adapter.submit(request)
        self.assertEqual(again.run_id, submitted.run_id)
        self.assertEqual(len(self.h.launches), 1)
        # Reopen: no live process, outcome unknown, never re-dispatched.
        reopened = self.h.adapter()
        self.assertEqual(reopened.status(ADDRESS, submitted.run_id).state, RunState.UNKNOWN)
        replay = reopened.submit(request)
        self.assertEqual(replay.run_id, submitted.run_id)
        self.assertEqual(len(self.h.launches), 1)
        unknown = reopened.collect(ADDRESS, submitted.run_id)
        self.assertEqual(unknown.declared_status, "unreported")
        adapter.inspect(submitted.run_id).process.wait(timeout=30)

    def test_same_key_different_request_conflicts(self):
        adapter = self.h.adapter()
        adapter.submit(self.h.request())
        with self.assertRaises(IdempotencyConflict):
            adapter.submit(self.h.request(instructions="something else"))
        self.assertEqual(len(self.h.launches), 1)

    def test_unreadable_registry_refuses(self):
        os.makedirs(self.h.evidence)
        with open(os.path.join(self.h.evidence, "registry.jsonl"), "wb") as fh:
            fh.write(b"{not json\n")
        with self.assertRaises(gem.RegistryUnavailable):
            self.h.adapter().submit(self.h.request())
        self.assertEqual(self.h.launches, [])

    def test_collected_result_survives_reopen(self):
        adapter, submitted, result = self.run_once()
        reopened = self.h.adapter()
        again = reopened.collect(ADDRESS, submitted.run_id)
        self.assertEqual(again.declared_status, result.declared_status)
        self.assertEqual(reopened.status(ADDRESS, submitted.run_id).state, RunState.DECLARED_COMPLETE)
        self.assertEqual(len(self.h.launches), 1)


class TestSourceRefs(unittest.TestCase):
    def test_refs_resolve_only_inside_workspace(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "a.py"), "w") as fh:
            fh.write("x\ny\n")
        refs = gem.extract_source_refs("see a.py:2, ../etc/passwd:1, a.py:9 and b.py:1", tmp)
        by = {(r["path"], r["line"]): r["verified"] for r in refs}
        self.assertTrue(by[("a.py", 2)])
        self.assertFalse(by[("a.py", 9)])
        self.assertFalse(by[("b.py", 1)])
        self.assertNotIn(("passwd", 1), by)


# Targeted weakening: disabling one guard predicate must make its own test fail.
WEAKENINGS = {
    "event_session_matches": (lambda event, sid: True,
                              "TestAssociationErrors.test_foreign_session_is_not_adopted"),
    "tool_name_is_permitted": (lambda name: True,
                               "TestReadOnly.test_successful_write_tool_is_a_breach"),
    "manifests_equal": (lambda a, b: True, "TestReadOnly.test_workspace_modification_is_detected"),
    "digest_matches": (lambda a, b: True, "TestValidDelivery.test_evidence_bytes_are_verified_on_disk"),
    "version_is_pinned": (lambda v: True, "TestReadOnly.test_unpinned_version_refused"),
    "workspace_preflight": (lambda *a: [], "TestReadOnly.test_workspace_host_config_refused"),
    "model_is_honored": (lambda a, b: True, "TestAssociationErrors.test_requested_model_not_honored"),
    "is_new_session_request": (lambda s: True, "TestReadOnly.test_existing_session_refused"),
}


class TestGuardWeakening(unittest.TestCase):
    def test_each_weakened_guard_fails_its_test(self):
        module = sys.modules[__name__]
        for name, (weak, target) in WEAKENINGS.items():
            with self.subTest(guard=name):
                cls_name, method = target.split(".")
                case = getattr(module, cls_name)(method)
                control = unittest.TestResult()
                case.run(control)
                self.assertTrue(control.wasSuccessful(), f"{target} must pass unweakened")
                with mock.patch.object(gem, name, weak):
                    weakened = unittest.TestResult()
                    getattr(module, cls_name)(method).run(weakened)
                self.assertFalse(weakened.wasSuccessful(), f"weakening {name} went undetected")


if __name__ == "__main__":
    unittest.main()
