"""Hark-派单-12: admission counterexamples for the Gemini CLI bridge (offline).

R1 registry / idempotency, R2 observation / evidence shapes, R3 paths / fixture
encoding. The CLI process is the stand-in tests/fixtures/gemini_cli/fake_cli.py
replaying native bytes captured from the real Gemini CLI 0.65.0 (synthetic
endpoint, no model). No test starts the real CLI or calls a model. Windows path
handling is exercised by the classifier and by synthetic Windows strings on
Linux only: that is NOT a Windows run.
"""
import contextlib
import copy
import hashlib
import json
import multiprocessing
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "gemini_cli"))

from src import agent_session_gemini as gem  # noqa: E402
from src.agent_session import (  # noqa: E402
    IdempotencyConflict, RunState, SessionAddress, SubmitRequest)

FAKE_CLI = ROOT / "tests" / "fixtures" / "gemini_cli" / "fake_cli.py"
ADDRESS = SessionAddress(app_kind="gemini-cli", instance_id="gemini-cli/local", session_id="new")
H11_EVIDENCE = ROOT / "reports" / "hark-gemini-bridge-20261021"
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def _source_file() -> str:
    lines = [f"# line {i}" for i in range(1, 140)]
    lines[93] = "class RunState:"
    return "\n".join(lines) + "\n"


class _Harness:
    def __init__(self, scenario=None):
        self.tmp = tempfile.mkdtemp(prefix="hark12-")
        base = os.path.realpath(self.tmp)
        self.base = base
        self.workspace = os.path.join(base, "ws")
        os.makedirs(os.path.join(self.workspace, "src"))
        with open(os.path.join(self.workspace, "src", "agent_session.py"), "w", encoding="utf-8") as fh:
            fh.write(_source_file())
        self.evidence = os.path.join(base, "evidence")
        self.home = os.path.join(base, "home")
        os.makedirs(self.home)
        self.scenario_dir = os.path.join(base, "scenario")
        os.makedirs(self.scenario_dir)
        self.scenario_path = os.path.join(self.scenario_dir, "scenario.json")
        self.set_scenario(scenario or {})
        self.launches = []
        self._lock = threading.Lock()

    @property
    def registry(self):
        return os.path.join(self.evidence, "registry.jsonl")

    def set_scenario(self, scenario):
        with open(self.scenario_path, "w", encoding="utf-8") as fh:
            json.dump(scenario, fh)

    def config(self):
        return gem.GeminiCliConfig(argv_prefix=(sys.executable, str(FAKE_CLI)), cli_home=self.home,
                                   evidence_root=self.evidence, registry_lock_timeout=10.0,
                                   extra_env=(("FAKE_GEMINI_SCENARIO", self.scenario_path),))

    def adapter(self, **kwargs):
        def launcher(argv, **kw):
            with self._lock:
                self.launches.append(argv)
            return gem.default_launcher(argv, **kw)
        return gem.GeminiCliAgentSessionAdapter(self.config(), launcher=launcher, **kwargs)

    def request(self, **overrides):
        values = dict(task_id="t-locate", attempt=1, session=ADDRESS,
                      instructions="Locate class RunState.", workspace=self.workspace,
                      idempotency_key="k1")
        values.update(overrides)
        return SubmitRequest(**values)

    def records(self):
        with open(self.registry, "rb") as fh:
            return [json.loads(l) for l in fh.read().decode("utf-8").splitlines() if l.strip()]

    def write_records(self, records):
        with open(self.registry, "wb") as fh:
            for r in records:
                fh.write(json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")

    def close(self):
        for dirpath, dirnames, _ in os.walk(self.tmp):
            for d in dirnames:
                try:
                    os.chmod(os.path.join(dirpath, d), 0o700)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)


class _Base(unittest.TestCase):
    def setUp(self):
        self.h = _Harness()
        self.addCleanup(self.h.close)

    def wait_all(self, adapter):
        for run_id in list(adapter._owned):
            run = adapter.inspect(run_id)
            if run is not None and run.process is not None:
                run.process.wait(timeout=60)

    def dispatched_count(self):
        if not os.path.exists(self.h.registry):
            return 0
        return sum(1 for r in self.h.records() if r.get("event") == "dispatched")


# --------------------------------------------------------------------------
# R1 registry / idempotency
# --------------------------------------------------------------------------

class TestR1SharedRegistry(_Base):
    def test_two_adapters_same_root_dispatch_once(self):
        a = self.h.adapter()
        b = self.h.adapter()
        first = a.submit(self.h.request())
        second = b.submit(self.h.request())
        self.wait_all(a)
        self.assertEqual(second.run_id, first.run_id)
        self.assertEqual(len(self.h.launches), 1)
        self.assertEqual(self.dispatched_count(), 1)
        self.assertEqual(second.state, RunState.UNKNOWN)   # B has no process for A's run
        with self.assertRaises(IdempotencyConflict):
            b.submit(self.h.request(instructions="different"))
        self.assertEqual(len(self.h.launches), 1)

    def test_threads_synchronised_in_version_probe_dispatch_once(self):
        probe_barrier = threading.Barrier(2, timeout=10)
        manifest_barrier = threading.Barrier(2, timeout=1.0)
        real_manifest = gem.tree_manifest

        def probe(config):
            probe_barrier.wait()
            return "0.65.0"

        def manifest(root):
            # Both threads meet here only if both got past admission (no lock).
            with contextlib.suppress(threading.BrokenBarrierError):
                manifest_barrier.wait()
            return real_manifest(root)

        adapter = self.h.adapter(version_probe=probe)
        out, errors = [], []

        def go():
            try:
                out.append(adapter.submit(self.h.request()))
            except Exception as exc:  # recorded and asserted below
                errors.append(exc)

        with mock.patch.object(gem, "tree_manifest", manifest):
            threads = [threading.Thread(target=go) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
        self.wait_all(adapter)
        self.assertEqual(errors, [])
        self.assertEqual(len({s.run_id for s in out}), 1)
        self.assertEqual(len(self.h.launches), 1)
        self.assertEqual(self.dispatched_count(), 1)

    def test_processes_dispatch_once(self):
        ctx = multiprocessing.get_context("spawn")
        import mp_submit
        paths = {"home": self.h.home, "evidence": self.h.evidence, "scenario": self.h.scenario_path,
                 "workspace": self.h.workspace, "launches": os.path.join(self.h.base, "launches.txt")}
        barrier = ctx.Barrier(2)
        queue = ctx.Queue()
        procs = [ctx.Process(target=mp_submit.worker, args=(paths, barrier, queue)) for _ in range(2)]
        for p in procs:
            p.start()
        results = [queue.get(timeout=120) for _ in procs]
        for p in procs:
            p.join(timeout=60)
        with open(paths["launches"], encoding="ascii") as fh:
            launches = [l for l in fh.read().splitlines() if l]
        self.assertEqual(len(launches), 1, results)
        self.assertEqual(self.dispatched_count(), 1)
        oks = [r for r in results if r[0] == "ok"]
        self.assertTrue(oks)
        self.assertEqual(len({r[1] for r in oks}), 1)
        for r in results:
            if r[0] == "refused":
                self.assertEqual(r[1], "RegistryBusy")


class TestR1RegistryState(_Base):
    def _legal_history(self, collected=False):
        adapter = self.h.adapter()
        status = adapter.submit(self.h.request())
        if collected:
            adapter.collect(ADDRESS, status.run_id, timeout=60)
        self.wait_all(adapter)
        records = self.h.records()
        if not collected:
            self.assertEqual([r["event"] for r in records], ["reserved", "dispatched"])
        return status.run_id, records

    def test_corrupted_after_construction_refuses(self):
        adapter = self.h.adapter()                      # no registry yet
        os.makedirs(self.h.evidence)
        with open(self.h.registry, "wb") as fh:
            fh.write(b"{not json\n")
        with self.assertRaises(gem.RegistryUnavailable):
            adapter.submit(self.h.request())
        self.assertEqual(self.h.launches, [])

    def test_corrupted_line_appended_after_construction_refuses(self):
        run_id, _ = self._legal_history(collected=True)
        adapter = self.h.adapter()                      # constructed on a legal registry
        with open(self.h.registry, "ab") as fh:
            fh.write(b'{"event": "reserved"\n')
        with self.assertRaises(gem.RegistryUnavailable):
            adapter.submit(self.h.request(idempotency_key="k2", task_id="t-other"))
        self.assertEqual(len(self.h.launches), 1)

    @unittest.skipIf(IS_ROOT or os.name == "nt", "needs POSIX permissions as non-root")
    def test_unreadable_registry_refuses(self):
        self._legal_history()
        os.chmod(self.h.registry, 0)
        self.addCleanup(os.chmod, self.h.registry, 0o600)
        with self.assertRaises(gem.RegistryUnavailable):
            self.h.adapter().submit(self.h.request(idempotency_key="k2", task_id="t-other"))
        self.assertEqual(len(self.h.launches), 1)

    def test_truncated_registry_refuses(self):
        self._legal_history()
        reader = self.h.adapter()                       # has seen the reservation
        with open(self.h.registry, "wb"):
            pass                                        # truncated to empty (still parseable)
        with self.assertRaises(gem.RegistryUnavailable):
            reader.submit(self.h.request())             # same key: must not be re-sent
        self.assertEqual(len(self.h.launches), 1)

    def test_deleted_registry_refuses_owner(self):
        owner = self.h.adapter()
        owner.submit(self.h.request())
        self.wait_all(owner)
        os.remove(self.h.registry)
        with self.assertRaises(gem.RegistryUnavailable):
            owner.submit(self.h.request(idempotency_key="k2", task_id="t-other"))
        self.assertEqual(len(self.h.launches), 1)

    def test_legal_prefix_is_kept(self):
        run_id, _ = self._legal_history()
        reopened = self.h.adapter()
        self.assertEqual(reopened.status(ADDRESS, run_id).state, RunState.UNKNOWN)
        replay = reopened.submit(self.h.request())
        self.assertEqual(replay.run_id, run_id)
        self.assertEqual(len(self.h.launches), 1)
        with self.assertRaises(IdempotencyConflict):
            reopened.submit(self.h.request(instructions="different"))
        fresh = reopened.submit(self.h.request(idempotency_key="k2", task_id="t-other"))
        self.assertNotEqual(fresh.run_id, run_id)
        self.assertEqual(len(self.h.launches), 2)
        self.wait_all(reopened)

    def test_reserved_only_prefix_is_unknown_not_resent(self):
        run_id, records = self._legal_history()
        self.h.write_records(records[:1])
        reopened = self.h.adapter()
        self.assertEqual(reopened.status(ADDRESS, run_id).state, RunState.UNKNOWN)
        self.assertEqual(reopened.submit(self.h.request()).run_id, run_id)
        self.assertEqual(len(self.h.launches), 1)

    def test_derived_illegal_histories_refuse_new_dispatch(self):
        run_id, legal = self._legal_history()
        snapshot = legal[0]["request_snapshot"]

        def drop(name, index=None):
            def f(rs):
                for i, r in enumerate(rs):
                    if index is None or i == index:
                        r.pop(name, None)
            return f

        def set_(name, value, index=None):
            def f(rs):
                for i, r in enumerate(rs):
                    if index is None or i == index:
                        r[name] = value
            return f

        other_snapshot = snapshot.replace("Locate class RunState.", "Locate something else.")
        cases = {
            "missing instance_id": drop("instance_id"),
            "missing session_id": drop("session_id"),
            "missing app_kind": drop("app_kind"),
            "missing task_id": drop("task_id"),
            "missing attempt": drop("attempt"),
            "missing native_session_id": drop("native_session_id"),
            "missing request_snapshot": drop("request_snapshot"),
            "missing request_fingerprint": drop("request_fingerprint"),
            "run_id int": set_("run_id", 123),
            "run_id empty": set_("run_id", ""),
            "run_id null": set_("run_id", None),
            "run_id list": set_("run_id", [run_id]),
            "attempt string": set_("attempt", "1"),
            "attempt bool": set_("attempt", True),
            "unknown event": set_("event", "launched", 1),
            "illegal state": set_("state", "succeeded", 1),
            "fingerprint mismatch": set_("request_fingerprint", hashlib.sha256(b"x").hexdigest()),
            "snapshot mismatch": set_("request_snapshot", other_snapshot),
            "snapshot binding mismatch": lambda rs: [r.update(
                request_snapshot=snapshot.replace('"t-locate"', '"t-forged"'),
                request_fingerprint=hashlib.sha256(snapshot.replace('"t-locate"', '"t-forged"').encode()).hexdigest())
                for r in rs],
            "cross-record native mismatch": set_("native_session_id", "00000000-0000-4000-8000-000000000000", 1),
            "cross-record task mismatch": lambda rs: rs[1].update(task_id="t-forged"),
            "events reversed": lambda rs: rs.reverse(),
            "duplicate reservation": lambda rs: rs.insert(1, copy.deepcopy(rs[0])),
            "record not an object": lambda rs: rs.append([1, 2]),
            "unexpected field": set_("note", "x", 0),
            "relative workspace": lambda rs: [r.update(workspace="ws") for r in rs],
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                records = copy.deepcopy(legal)
                mutate(records)
                self.h.write_records(records)
                before = len(self.h.launches)
                adapter = self.h.adapter()
                with self.assertRaises(gem.RegistryUnavailable):
                    adapter.submit(self.h.request(idempotency_key=f"k-{len(name)}-{abs(hash(name))}",
                                                  task_id="t-new"))
                with self.assertRaises(gem.RegistryUnavailable):
                    adapter.submit(self.h.request())
                self.assertEqual(len(self.h.launches), before)

    def test_committed_h11_registries_are_legal(self):
        for path in sorted(H11_EVIDENCE.rglob("registry.jsonl")):
            with self.subTest(path=str(path.relative_to(ROOT))):
                with open(path, "rb") as fh:
                    records = [json.loads(l) for l in fh.read().decode("utf-8").splitlines() if l.strip()]
                self.assertEqual(gem.validate_registry_records(records), [])


# --------------------------------------------------------------------------
# R2 observation / evidence shapes
# --------------------------------------------------------------------------

def _failing_scandir(target):
    real = os.scandir

    def scandir(path="."):
        if os.path.realpath(os.fspath(path)) == os.path.realpath(target):
            raise PermissionError(13, "Permission denied (injected)", os.fspath(path))
        return real(path)
    return scandir


class TestR2Observation(_Base):
    def _assert_refused_before_launch(self, target):
        adapter = self.h.adapter()
        with mock.patch("os.scandir", _failing_scandir(target)):
            with self.assertRaises(gem.WorkspaceUnobservable):
                adapter.submit(self.h.request())
        self.assertEqual(self.h.launches, [])
        self.assertEqual(self.dispatched_count(), 0)
        self.assertFalse(os.path.exists(self.h.registry))

    def test_root_unlistable_before_launch_refuses(self):
        self._assert_refused_before_launch(self.h.workspace)

    def test_subdir_unlistable_before_launch_refuses(self):
        self._assert_refused_before_launch(os.path.join(self.h.workspace, "src"))

    def test_unlistable_before_and_at_collect(self):
        # The architect's case: listing fails both before launch and at collect.
        adapter = self.h.adapter()
        with mock.patch("os.scandir", _failing_scandir(os.path.join(self.h.workspace, "src"))):
            with self.assertRaises(gem.WorkspaceUnobservable):
                adapter.submit(self.h.request())
        self.assertEqual(self.h.launches, [])

    def test_unlistable_at_collect_only_is_not_unchanged(self):
        adapter = self.h.adapter()
        submitted = adapter.submit(self.h.request())
        adapter.inspect(submitted.run_id).process.wait(timeout=60)
        with mock.patch("os.scandir", _failing_scandir(os.path.join(self.h.workspace, "src"))):
            result = adapter.collect(ADDRESS, submitted.run_id, timeout=60)
        self.assertEqual(adapter.status(ADDRESS, submitted.run_id).state, RunState.FAILED)
        self.assertIsNone(result.artifacts[0]["workspace_unchanged"])
        self.assertTrue(any("could not be fully observed at collect" in u for u in result.unverified))

    @unittest.skipIf(IS_ROOT or os.name == "nt", "needs POSIX permissions as non-root")
    def test_real_permission_denied_subdir_refuses(self):
        sub = os.path.join(self.h.workspace, "src")
        os.chmod(sub, 0)
        self.addCleanup(os.chmod, sub, 0o700)
        with self.assertRaises(gem.WorkspaceUnobservable):
            self.h.adapter().submit(self.h.request())
        self.assertEqual(self.h.launches, [])

    @unittest.skipIf(not hasattr(os, "mkfifo"), "no FIFOs on this platform")
    def test_fifo_in_workspace_is_recorded_not_opened(self):
        os.mkfifo(os.path.join(self.h.workspace, "pipe"))
        manifest = gem.tree_manifest(self.h.workspace)
        self.assertEqual(manifest["pipe"]["kind"], "special")

    def _run(self, scenario):
        # A fresh key per call: every case is its own dispatch, never a replay.
        self._runs = getattr(self, "_runs", 0) + 1
        self.h.set_scenario(scenario)
        adapter = self.h.adapter()
        submitted = adapter.submit(self.h.request(idempotency_key=f"k-run-{self._runs}",
                                                  task_id=f"t-run-{self._runs}"))
        self.assertEqual(len(self.h.launches), self._runs)
        result = adapter.collect(ADDRESS, submitted.run_id, timeout=60)
        return adapter.status(ADDRESS, submitted.run_id).state, result

    def _write_result_edit(self, **edit):
        # tool_result index 2 is write_file's (template order: grep, read, write, shell).
        return {"edit_events": [dict(type="tool_result", index=2, **edit)]}

    def test_tool_status_shapes_do_not_confirm_denial(self):
        cases = {"missing": dict(delete=["status"]), "null": dict(set={"status": None}),
                 "unknown string": dict(set={"status": "maybe"}),
                 "array": dict(set={"status": ["error"]})}
        for name, edit in cases.items():
            with self.subTest(case=name):
                state, result = self._run(self._write_result_edit(**edit))
                self.assertEqual(state, RunState.FAILED)
                self.assertNotIn("write_file", result.artifacts[0]["denied_tool_requests"])
                self.assertEqual(result.artifacts[0]["denied_tool_requests"], ["run_shell_command"])

    def test_unknown_tool_status_is_unconfirmed(self):
        state, result = self._run(self._write_result_edit(set={"status": "maybe"}))
        self.assertEqual(state, RunState.FAILED)
        self.assertEqual(result.artifacts[0]["unconfirmed_tool_requests"], ["write_file"])
        self.assertTrue(any("does not confirm a refusal" in u for u in result.unverified))

    def test_bad_shapes_are_diagnosed_not_raised(self):
        cases = {
            "tool_id array": ({"edit_events": [{"type": "tool_result", "index": 0,
                                                "set": {"tool_id": ["x"]}}]}, "field 'tool_id' is list"),
            "type array": ({"edit_events": [{"type": "message", "index": 1,
                                             "set": {"type": ["message"]}}]}, "type list"),
            "tool_use id array": ({"edit_events": [{"type": "tool_use", "index": 0,
                                                    "set": {"tool_id": ["x"]}}]}, "field 'tool_id' is list"),
            "init session object": ({"edit_events": [{"type": "init", "index": 0,
                                                      "set": {"session_id": {"a": 1}}}]}, "field 'session_id' is dict"),
            "result stats array": ({"edit_events": [{"type": "result", "index": 0,
                                                     "set": {"stats": [1]}}]}, "field 'stats' is list"),
        }
        for name, (scenario, fragment) in cases.items():
            with self.subTest(case=name):
                state, result = self._run(scenario)
                self.assertEqual(state, RunState.FAILED)
                self.assertTrue(any(fragment in u for u in result.unverified), result.unverified)

    def test_tool_id_array_is_diagnosed(self):
        state, result = self._run({"edit_events": [{"type": "tool_result", "index": 0,
                                                    "set": {"tool_id": ["x"]}}]})
        self.assertEqual(state, RunState.FAILED)
        self.assertTrue(any("field 'tool_id' is list" in u for u in result.unverified))

    def test_chat_record_first_line_shapes(self):
        for head in ([], None, "session", 3, True):
            with self.subTest(head=head):
                state, result = self._run({"chat_head": head})
                self.assertTrue(any("not a JSON object" in u for u in result.unverified), result.unverified)
                self.assertIn("host chat record for this session not found", result.unverified)
                shutil.rmtree(os.path.join(self.h.home, ".gemini", "tmp"), ignore_errors=True)

    def test_valid_delivery_keeps_raw_bytes(self):
        state, result = self._run({})
        self.assertEqual(state, RunState.DECLARED_COMPLETE)
        art = result.artifacts[0]
        self.assertTrue(art["workspace_unchanged"])
        self.assertEqual(art["unconfirmed_tool_requests"], [])
        stdout = [p for p in result.raw_refs if p.endswith("stdout.jsonl")][0]
        collected = [r for r in self.h.records() if r["event"] == "collected"][0]
        with open(stdout, "rb") as fh:
            data = fh.read()
        self.assertEqual(hashlib.sha256(data).hexdigest(), collected["evidence"]["stdout.jsonl"]["sha256"])


# --------------------------------------------------------------------------
# R3 paths / fixture encoding
# --------------------------------------------------------------------------

class TestR3Paths(_Base):
    def _refused(self, workspace):
        adapter = self.h.adapter()
        with self.assertRaises(gem.HostRestrictionUnavailable):
            adapter.submit(self.h.request(workspace=workspace))
        self.assertEqual(self.h.launches, [])
        self.assertFalse(os.path.exists(self.h.registry))

    def test_existing_relative_directory_refused(self):
        cwd = os.getcwd()
        os.chdir(self.h.base)
        self.addCleanup(os.chdir, cwd)
        self.assertTrue(os.path.isdir("ws"))
        self._refused("ws")

    def test_relative_inputs_refused(self):
        cwd = os.getcwd()
        os.chdir(self.h.base)
        self.addCleanup(os.chdir, cwd)
        for value in ("ws", "./ws", "ws/src", "missing/ws", "\\ws", "C:ws", "C:\\ws",
                      self.h.workspace + "/../ws", ""):
            with self.subTest(workspace=value):
                self._refused(value)

    def test_absolute_workspace_positive_control(self):
        adapter = self.h.adapter()
        submitted = adapter.submit(self.h.request())
        result = adapter.collect(ADDRESS, submitted.run_id, timeout=60)
        self.assertEqual(result.declared_status, "success")
        self.assertEqual(adapter.inspect(submitted.run_id).workspace, self.h.workspace)

    def test_classifier_windows_and_posix_forms(self):
        p = gem.workspace_path_problem
        for value in ("\\ws", "/ws"):
            self.assertIn("root-relative", p(value, "nt"), value)
        for value in ("C:ws", "c:ws\\sub", "C:"):
            self.assertIn("drive-relative", p(value, "nt"), value)
        for value in ("ws", ".\\ws", "ws\\sub", ""):
            self.assertIsNotNone(p(value, "nt"), value)
        for value in ("\\\\?\\C:\\ws", "\\\\.\\C:\\ws", "C:\\a\\..\\b"):
            self.assertIsNotNone(p(value, "nt"), value)
        for value in ("C:\\Users\\jh\\JH-Agent-OS", "C:/Users/jh/ws", "\\\\server\\share\\ws", "D:\\"):
            self.assertIsNone(p(value, "nt"), value)
        for value in ("ws", "./ws", "\\ws", "C:\\ws", "C:ws", "/a/../b", "", None, 3):
            self.assertIsNotNone(p(value, "posix"), value)
        for value in ("/ws", "/home/a b/ws", "/"):
            self.assertIsNone(p(value, "posix"), value)


class TestR3Fixture(_Base):
    WINDOWS = "C:\\Users\\jh \"q\"\\JH-Agent-OS"

    def test_windows_workspace_string_replays_as_valid_json(self):
        self.h.set_scenario({"workspace_display": self.WINDOWS})
        adapter = self.h.adapter()
        submitted = adapter.submit(self.h.request())
        result = adapter.collect(ADDRESS, submitted.run_id, timeout=60)
        self.assertEqual(adapter.status(ADDRESS, submitted.run_id).state, RunState.DECLARED_COMPLETE)
        read = [c for c in result.artifacts[0]["tool_calls"] if c["tool_name"] == "read_file"][0]
        self.assertEqual(read["parameters"]["file_path"], self.WINDOWS + "/src/agent_session.py")

    def test_old_text_substitution_would_be_invalid(self):
        with open(FAKE_CLI.parent / "host_probe_stream.template.jsonl", encoding="utf-8") as fh:
            template = fh.read()
        broken = 0
        for line in template.replace("{WORKSPACE}", self.WINDOWS).splitlines():
            try:
                json.loads(line)
            except json.JSONDecodeError:
                broken += 1
        self.assertGreater(broken, 0)   # the R3 counterexample, reproduced

    def test_reencoding_reproduces_captured_native_bytes(self):
        import fake_cli
        bridge = H11_EVIDENCE / "host-probe" / "bridge"
        with open(bridge / "registry.jsonl", "rb") as fh:
            reserved = json.loads(fh.readline())
        with open(bridge / "runs" / reserved["run_id"] / "stdout.jsonl", "rb") as fh:
            raw = fh.read()
        with open(FAKE_CLI.parent / "host_probe_stream.template.jsonl", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        values = {"{SESSION_ID}": reserved["native_session_id"], "{WORKSPACE}": reserved["workspace"]}
        rebuilt = "".join(fake_cli.encode(fake_cli.substitute(json.loads(l), values)) + "\n" for l in lines)
        self.assertEqual(rebuilt.encode("utf-8"), raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "be3ed400936c425654f1407371e60447c624d937967716e8f337f210fd899b97")


# --------------------------------------------------------------------------
# Targeted weakening of the guards this batch adds
# --------------------------------------------------------------------------

WEAKENINGS = {
    "registry_lock": (lambda *a, **k: contextlib.nullcontext(),
                      "TestR1SharedRegistry.test_threads_synchronised_in_version_probe_dispatch_once"),
    "validate_registry_records": (lambda *a, **k: [],
                                  "TestR1RegistryState.test_derived_illegal_histories_refuse_new_dispatch"),
    "registry_prefix_intact": (lambda prefix, data: True, "TestR1RegistryState.test_truncated_registry_refuses"),
    "on_walk_error": (lambda exc: None, "TestR2Observation.test_subdir_unlistable_before_launch_refuses"),
    "tool_status_confirms_denial": (lambda status: True, "TestR2Observation.test_unknown_tool_status_is_unconfirmed"),
    "event_shape_problems": (lambda event: [], "TestR2Observation.test_tool_id_array_is_diagnosed"),
    "workspace_path_problem": (lambda *a, **k: None, "TestR3Paths.test_existing_relative_directory_refused"),
}


class TestGuardWeakening(unittest.TestCase):
    def test_each_weakened_guard_fails_its_test(self):
        module = sys.modules[__name__]
        for name, (weak, target) in WEAKENINGS.items():
            with self.subTest(guard=name):
                cls_name, method = target.split(".")
                control = unittest.TestResult()
                getattr(module, cls_name)(method).run(control)
                self.assertTrue(control.wasSuccessful(), f"{target} must pass unweakened: "
                                f"{control.failures + control.errors}")
                with mock.patch.object(gem, name, weak):
                    weakened = unittest.TestResult()
                    getattr(module, cls_name)(method).run(weakened)
                self.assertFalse(weakened.wasSuccessful(), f"weakening {name} went undetected")


if __name__ == "__main__":
    unittest.main()
