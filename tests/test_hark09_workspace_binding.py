"""Hark-派单-09: one workspace for plan, dispatch and native session; honest observation.

Closes the 21 Oct review's R4. A relative probe directory used to be handed to
the adapter as a relative string, used as the app-server PROCESS cwd and again
as the thread/turn cwd, so the native side anchored it twice; the status then
listed the PLANNED directory as "workspace_files=[]". Now:

* the probe directory is resolved once, at construction, to an absolute path
  (relative spellings are anchored to the server cwd; `.` dropped); a `..`
  segment is refused before any external call, never collapsed;
* the status reports `workspace.state`; only `verified` (plan == dispatch ==
  every same-thread native statement) yields a file listing. Missing, foreign,
  mismatching or contradicting native facts give `files=None` and
  `verified_empty=None`, while the fixed-token echo keeps its own outcome.

The end-to-end cases drive the REAL default adapter factory; only the stdio
transport class is replaced in-process by a resolver that anchors a supplied cwd
to the PROCESS cwd it was started with (what the native side did). No process,
model or app-server is started. POSIX symlink cases stand in for junctions and
are skipped on Windows; Windows path semantics are not verified here.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_hark06_session_probe as fx  # noqa: E402
import src.agent_session_codex as codex  # noqa: E402
import src.session_probe as probe  # noqa: E402
from src.app_api import ApiError, AppInterface  # noqa: E402
from src.session_probe import ProbeConfig, classify_workspace, resolve_probe_dir  # noqa: E402

NO_SYMLINKS = os.name == "nt"
SPACE_CJK = "with space 空格 中文"


class World:
    """Real default adapter factory; resolver transport; synchronous runner."""

    def __init__(self, tmp, spelling, *, native="resolve", response_cwd=True, notification=True,
                 notification_thread=None, timeout_method=None):
        self.tmp = Path(tmp)
        self.spelling = spelling
        self.native = native
        self.response_cwd = response_cwd
        self.notification = notification
        self.notification_thread = notification_thread
        self.timeout_method = timeout_method
        self.transports = []
        self.calls = []
        self.api = self.make_api()

    def make_api(self):
        return AppInterface(db_path=self.tmp / "absent.sqlite3", runs_path=self.tmp / "history.jsonl",
                            session_probe=ProbeConfig(enabled=True, probe_dir=self.spelling),
                            probe_runtime_check=lambda: None, probe_runner=lambda job: job())

    def transport_class(self):
        world = self

        class Resolver(fx.EchoTransport):
            def __init__(self, argv, cwd):
                super().__init__(timeout_method=world.timeout_method)
                self.process_cwd = os.path.abspath(cwd)
                self.effective = None
                world.transports.append(self)

            def call(self, method, params, timeout=60):
                result = super().call(method, params, timeout)
                if method in ("thread/start", "turn/start"):
                    supplied = params["cwd"]
                    effective = os.path.abspath(os.path.join(self.process_cwd, supplied))
                    if world.native == "elsewhere":
                        effective = str(world.tmp / "different-native")
                    self.effective = effective
                    world.calls.append({"method": method, "process_cwd": self.process_cwd,
                                        "supplied": supplied, "effective": effective})
                    if method == "thread/start" and isinstance(result.get("result"), dict):
                        result["result"].pop("cwd", None)
                        if world.response_cwd:
                            result["result"]["cwd"] = effective
                            result["result"]["thread"]["cwd"] = effective
                return result

            def notifications(self):
                notes = super().notifications()
                if world.notification:
                    thread = world.notification_thread or fx.fx.THREAD
                    cwd = self.effective if world.native != "conflict" else str(world.tmp / "conflicting")
                    if world.notification_thread:
                        cwd = str(world.tmp / "foreign-native")
                    notes.insert(0, {"method": "thread/started", "params": {"thread": {"id": thread, "cwd": cwd}}})
                return notes
        return Resolver

    def attempt(self, api=None):
        api = api or self.api
        with mock.patch.object(codex, "CodexStdioTransport", self.transport_class()):
            try:
                prepared = api.prepare_session_probe({})
                api.start_session_probe({"probe_id": prepared["probe_id"], "confirm": True})
            except ApiError as exc:
                return {"refused": exc.code}
        return {"status": api.session_probe_status(), "probe_id": prepared["probe_id"]}

    def thread_starts(self):
        return sum(1 for t in self.transports for c in t.calls if c["method"] == "thread/start")


class _Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self._cwd = os.getcwd()
        os.chdir(self.tmp)                     # relative spellings are anchored HERE
        if os.name != "nt":
            patcher = mock.patch.object(codex, "is_special_path", fx._posix_evidence_seam)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def assert_verified(self, world, result, expected_probe_dir):
        latest = result["status"]["latest"]
        planned = str(Path(expected_probe_dir) / "ws" / result["probe_id"])
        ws = latest["workspace"]
        self.assertEqual(latest["outcome"], "round_trip_complete")
        self.assertEqual(world.thread_starts(), 1)
        for call in world.calls:                        # one absolute string everywhere
            self.assertEqual(call["supplied"], planned)
            self.assertEqual(call["process_cwd"], planned)
            self.assertEqual(call["effective"], planned)
        self.assertEqual((ws["state"], ws["verified"], ws["planned"], ws["dispatched"]),
                         ("verified", True, planned, planned))
        self.assertEqual((latest["workspace_files"], ws["verified_empty"]), ([], True))
        same = [s for s in ws["native_sources"] if s["same_thread"]]
        self.assertTrue(same)
        return latest

    def assert_unverified(self, result, state):
        latest = result["status"]["latest"]
        ws = latest["workspace"]
        self.assertEqual(latest["echo"], "matched")             # echo reported separately
        self.assertEqual((ws["state"], ws["verified"]), (state, False))
        self.assertIsNone(latest["workspace_files"])
        self.assertIsNone(ws["verified_empty"])
        self.assertIsNone(ws["files"])
        return latest


class TestResolution(unittest.TestCase):
    def test_spellings(self):
        cwd = os.path.abspath(os.sep + os.path.join("srv", "jh"))
        default = Path(cwd) / "default"
        self.assertEqual(resolve_probe_dir(None, cwd=cwd, default=default), (default, "default", None))
        absolute = os.path.join(cwd, "abs", "probe")
        self.assertEqual(resolve_probe_dir(absolute, cwd="/elsewhere"),
                         (Path(absolute), "configured_absolute", None))
        rel = os.path.join("data", SPACE_CJK, "probe")
        self.assertEqual(resolve_probe_dir(rel, cwd=cwd), (Path(cwd, rel), "relative_to_server_cwd", None))
        self.assertEqual(resolve_probe_dir("." + os.sep + rel, cwd=cwd)[0], Path(cwd, rel))
        self.assertEqual(resolve_probe_dir(os.path.join("data", ".", "probe"), cwd=cwd)[0],
                         Path(cwd, "data", "probe"))
        for bad in (os.path.join("data", "x", "..", "probe"), os.path.join(cwd, "a", "..", "probe"),
                    os.path.join("..", "probe")):
            self.assertEqual(resolve_probe_dir(bad, cwd=cwd)[2][0], "probe_dir_parent_segment", bad)
        self.assertEqual(resolve_probe_dir("a\0b", cwd=cwd)[2][0], "probe_dir_invalid")

    def test_classify_real_windows_shape_is_not_verified(self):
        planned = os.path.join(os.sep + "srv", "data", "trial-01", "ws", "536e")
        rel = os.path.join("data", "trial-01", "ws", "536e")
        doubled = os.path.join(planned, rel)
        native = [{"source": "thread/started.notification", "thread_id": "T", "cwd": doubled}]
        self.assertEqual(classify_workspace(planned, rel, "T", native, "absent", "verified")[0],
                         "dispatch_not_absolute")
        self.assertEqual(classify_workspace(planned, planned, "T", native, "absent", "verified")[0],
                         "native_mismatch")
        self.assertEqual(classify_workspace(planned, planned + "x", "T", native, "absent", "verified")[0],
                         "dispatch_mismatch")
        self.assertEqual(classify_workspace(planned, planned, None, native, "absent", "verified")[0],
                         "native_thread_unknown")
        self.assertEqual(classify_workspace(planned, planned, "T", native, "absent", "mismatch")[0],
                         "native_evidence_unverified")
        agreeing = [{"source": "thread/start.response", "thread_id": "T", "cwd": planned}]
        for evidence in ("mismatch", "missing", None):            # capture alone never verifies
            self.assertEqual(classify_workspace(planned, planned, "T", agreeing, "present", evidence)[0],
                             "native_evidence_unverified")
        dotted = [{"thread_id": "T", "cwd": os.path.join(planned, "x", "..")}]
        self.assertEqual(classify_workspace(planned, planned, "T", dotted, "absent", "verified")[0],
                         "native_mismatch")                   # no lexical collapsing
        bad_type = [{"thread_id": "T", "cwd": planned}, {"thread_id": "T", "cwd": 7}]
        self.assertEqual(classify_workspace(planned, planned, "T", bad_type, "absent", "verified")[0],
                         "native_conflict")


class TestConsistentBinding(_Case):
    def test_absolute(self):
        target = self.tmp / "abs" / "probe"
        world = World(self.tmp, str(target))
        self.assert_verified(world, world.attempt(), target)

    def test_absolute_with_space_and_cjk(self):
        target = self.tmp / SPACE_CJK / "probe"
        world = World(self.tmp, str(target))
        self.assert_verified(world, world.attempt(), target)

    def test_relative_is_anchored_once(self):
        world = World(self.tmp, os.path.join("data", "rel", "probe"))
        self.assert_verified(world, world.attempt(), self.tmp / "data" / "rel" / "probe")
        ws_root = self.tmp / "data" / "rel" / "probe" / "ws"
        for run_dir in ws_root.iterdir():
            self.assertEqual(list(run_dir.iterdir()), [])      # no doubled data/rel/... inside

    def test_relative_with_leading_dot(self):
        world = World(self.tmp, "." + os.sep + os.path.join("data", "dot", "probe"))
        self.assert_verified(world, world.attempt(), self.tmp / "data" / "dot" / "probe")

    def test_relative_with_space_and_cjk(self):
        world = World(self.tmp, os.path.join("data", SPACE_CJK, "probe"))
        self.assert_verified(world, world.attempt(), self.tmp / "data" / SPACE_CJK / "probe")

    def test_relative_survives_a_later_cwd_change(self):
        world = World(self.tmp, os.path.join("data", "moved", "probe"))
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        os.chdir(elsewhere)
        self.assert_verified(world, world.attempt(), self.tmp / "data" / "moved" / "probe")
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_parent_segment_is_refused_before_anything(self):
        (self.tmp / "data" / "middle").mkdir(parents=True)
        world = World(self.tmp, os.path.join("data", "middle", "..", "probe"))
        self.assertEqual(world.attempt(), {"refused": "probe_dir_parent_segment"})
        self.assertEqual((len(world.transports), world.thread_starts()), (0, 0))
        self.assertFalse((self.tmp / "data" / "probe").exists())
        status = world.api.session_probe_status()
        self.assertEqual(status["blockers"][0]["code"], "probe_dir_parent_segment")
        self.assertEqual(status["plan"]["workspace_binding"]["resolution"], "relative_to_server_cwd")

    def test_absolute_parent_segment_is_refused(self):
        world = World(self.tmp, str(self.tmp) + os.sep + os.path.join("a", "..", "probe"))
        self.assertEqual(world.attempt(), {"refused": "probe_dir_parent_segment"})
        self.assertEqual(len(world.transports), 0)


class TestNativeFacts(_Case):
    def world(self, **kw):
        return World(self.tmp, str(self.tmp / "abs" / "probe"), **kw)

    def test_native_mismatch_is_not_verified(self):
        self.assert_unverified(self.world(native="elsewhere").attempt(), "native_mismatch")

    def test_native_missing_is_not_verified(self):
        self.assert_unverified(self.world(response_cwd=False, notification=False).attempt(), "native_missing")

    def test_foreign_thread_only_is_not_verified(self):
        result = self.world(response_cwd=False, notification_thread=fx.fx.OTHER_THREAD).attempt()
        latest = self.assert_unverified(result, "native_foreign_only")
        sources = latest["workspace"]["native_sources"]
        self.assertFalse(any(s["same_thread"] and (s["cwd"] or s["thread_cwd"]) for s in sources))
        self.assertTrue(any(not s["same_thread"] and s["cwd"] for s in sources))

    def test_contradicting_same_thread_facts_are_not_verified(self):
        self.assert_unverified(self.world(native="conflict").attempt(), "native_conflict")

    def test_response_only_and_notification_only_are_each_enough(self):
        world = self.world(notification=False)
        self.assertEqual(world.attempt()["status"]["latest"]["workspace"]["state"], "verified")
        world = World(self.tmp, str(self.tmp / "n" / "probe"), response_cwd=False)
        self.assertEqual(world.attempt()["status"]["latest"]["workspace"]["state"], "verified")

    def test_corrupt_capture_is_not_verified(self):
        world = self.world(notification=False)
        result = world.attempt()
        capture = self.tmp / "abs" / "probe" / "raw" / f"probe-native-{result['probe_id']}.json"
        self.assertTrue(capture.is_file())
        capture.write_text("{not json", encoding="utf-8")
        latest = world.api.session_probe_status()["latest"]
        self.assertEqual(latest["workspace"]["state"], "native_evidence_invalid")
        self.assertIsNone(latest["workspace_files"])

    def test_verified_listing_reports_real_content(self):
        world = self.world()
        result = world.attempt()
        planned = Path(result["status"]["latest"]["workspace"]["planned"])
        (planned / "note.txt").write_text("x", encoding="utf-8")
        latest = world.api.session_probe_status()["latest"]
        self.assertEqual((latest["workspace_files"], latest["workspace"]["verified_empty"]),
                         (["note.txt"], False))


class TestGuardsKept(_Case):
    def test_unknown_result_with_relative_config_is_not_redispatched(self):
        world = World(self.tmp, os.path.join("data", "unk", "probe"), timeout_method="thread/start")
        world.attempt()
        self.assertEqual(world.attempt(world.make_api()), {"refused": "probe_unresolved"})
        self.assertEqual(world.thread_starts(), 1)

    def test_relative_config_still_claims_and_finishes(self):
        world = World(self.tmp, os.path.join("data", "claim", "probe"))
        world.attempt()
        claim = json.loads((self.tmp / "data" / "claim" / "probe" / probe.CLAIM_NAME).read_text("utf-8"))
        self.assertEqual(claim["state"], "finished")
        self.assertIn("round_trip_complete", [world.api.session_probe_status()["latest"]["outcome"]])

    @unittest.skipIf(NO_SYMLINKS, "symlink creation needs privileges on Windows; junctions untested")
    def test_relative_path_through_a_link_is_still_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        os.symlink(outside, self.tmp / "linked", target_is_directory=True)
        world = World(self.tmp, os.path.join("linked", "probe"))
        self.assertEqual(world.attempt(), {"refused": "probe_path_is_link"})
        self.assertEqual((len(world.transports), sorted(outside.iterdir())), (0, []))

    @unittest.skipIf(NO_SYMLINKS, "symlink creation needs privileges on Windows; junctions untested")
    def test_parent_segment_behind_a_link_is_refused_not_collapsed(self):
        outside = self.tmp / "outside"
        (outside / "deep").mkdir(parents=True)
        os.symlink(outside / "deep", self.tmp / "hop", target_is_directory=True)
        world = World(self.tmp, os.path.join("hop", "..", "probe"))
        self.assertEqual(world.attempt(), {"refused": "probe_dir_parent_segment"})
        self.assertEqual(len(world.transports), 0)
        self.assertFalse((self.tmp / "probe").exists() or (outside / "probe").exists())


if __name__ == "__main__":
    unittest.main()
