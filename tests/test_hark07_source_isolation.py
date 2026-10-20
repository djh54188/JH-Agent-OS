"""Hark-派单-07: one probe SOURCE, one unresolved dispatch; writes stay isolated.

Closes the review counterexamples R1 (exclusion was per service object) and R2
(identity / link layout could redirect writes). Offline only: the accepted fake
transport stands in for the app-server; no model, no real Codex process.

Facts asserted are external: thread/start calls that reached the transport (in
this process and, for the multi-process cases, summed over child processes),
the bytes of the history and of link targets, and the claim file on disk.

Windows junctions are NOT created here (Linux sandbox); POSIX symlinks are the
static-link analogue, and the junction branch of the check (reparse-point
attribute) is unverified until a Windows run.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_hark06_session_probe as fx  # noqa: E402
import src.session_probe as probe  # noqa: E402
from src.app_api import ApiError  # noqa: E402

CHILD = ROOT / "tests" / "hark07_probe_child.py"
NO_SYMLINKS = os.name == "nt"


def attempt(api, prepared=None):
    try:
        prepared = prepared or api.prepare_session_probe({})
        started = api.start_session_probe({"probe_id": prepared["probe_id"], "confirm": True})
        return {"accepted": started.get("accepted") is True, "replay": started.get("replay")}
    except ApiError as exc:
        return {"refused": exc.code}


def files_under(root):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*"))


class _Case(fx._Case):
    def child(self, *args, timeout=60):
        return subprocess.Popen([sys.executable, str(CHILD), *map(str, args)], cwd=str(ROOT),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


class TestSourceExclusion(_Case):
    def test_second_object_refused_after_accept_before_reserve(self):
        rig = fx.Rig(self.tmp, runner="deferred", transport_kw={"timeout_method": "thread/start"})
        other = rig.make_api()
        early = other.prepare_session_probe({})            # prepared before the first accept
        first = attempt(rig.api)
        self.assertTrue(first["accepted"])
        self.assertFalse(rig.registry().exists())           # accepted, nothing registered yet
        self.assertEqual(attempt(other, early), {"refused": "probe_claim_unresolved"})
        self.assertEqual(attempt(other), {"refused": "probe_claim_unresolved"})
        for job in rig.deferred:
            job()
        self.assertEqual(attempt(other)["refused"], "probe_unresolved")
        self.assertEqual(attempt(rig.make_api())["refused"], "probe_unresolved")
        self.assertEqual(rig.thread_starts(), 1)

    def test_lock_serialises_check_and_claim_across_objects(self):
        rig = fx.Rig(self.tmp, runner="deferred")
        other = rig.make_api()
        mine = rig.api.prepare_session_probe({})
        theirs = other.prepare_session_probe({})
        results = {}

        def race_inside_the_critical_section():
            worker = threading.Thread(target=lambda: results.__setitem__("other", attempt(other, theirs)))
            worker.start()
            worker.join(0.4)                                  # give it the window it would need
            results["worker"] = worker

        rig.api.session_probe._claim_hook = race_inside_the_critical_section
        results["mine"] = attempt(rig.api, mine)
        rig.api.session_probe._claim_hook = None
        results["worker"].join(10)
        accepted = [k for k in ("mine", "other") if results.get(k, {}).get("accepted")]
        self.assertEqual(accepted, ["mine"], results)
        self.assertEqual(results["other"], {"refused": "probe_claim_unresolved"})
        for job in list(rig.deferred):
            job()
        self.assertEqual(rig.thread_starts(), 1)

    def test_concurrent_processes_dispatch_at_most_once(self):
        go = Path(self.tmp) / "go"
        children = [self.child("race", self.tmp, go) for _ in range(4)]
        time.sleep(1.5)                                       # let every child import and wait
        go.write_text("go", encoding="utf-8")
        outputs = []
        for proc in children:
            out, err = proc.communicate(timeout=90)
            self.assertEqual(proc.returncode, 0, err)
            outputs.append(json.loads(out.strip().splitlines()[-1]))
        self.assertEqual(sum(o["thread_starts"] for o in outputs), 1, outputs)
        self.assertEqual(sum(1 for o in outputs if o.get("accepted")), 1, outputs)
        for o in outputs:
            if not o.get("accepted"):
                self.assertIn(o["refused"], {"probe_claim_unresolved", "probe_unresolved", "probe_source_busy"}, o)
        lines = (Path(self.tmp) / "probe" / "probe-runs.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len({json.loads(line)["run_id"] for line in lines}), 1)

    def test_process_exit_after_accept_blocks_the_source(self):
        proc = self.child("accept_and_exit", self.tmp)
        out, err = proc.communicate(timeout=60)
        report = json.loads(out.strip().splitlines()[-1])
        self.assertTrue(report["accepted"], err)
        self.assertEqual(report["thread_starts"], 0)
        rig = fx.Rig(self.tmp)                                # a restarted server
        status = rig.api.session_probe_status()
        self.assertEqual(status["claim"]["claim_state"], "accepted")
        self.assertIn("probe_claim_unresolved", [b["code"] for b in status["blockers"]])
        self.assertEqual(attempt(rig.api), {"refused": "probe_claim_unresolved"})
        self.assertEqual(rig.thread_starts(), 0)
        self.assertFalse(rig.registry().exists())

    def test_second_process_is_refused_while_the_source_is_held(self):
        ready = Path(self.tmp) / "ready"
        holder = self.child("hold_lock", self.tmp, ready, 6)
        deadline = time.time() + 30
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(ready.exists(), "holder never took the lock")
        rig = fx.Rig(self.tmp)
        with mock.patch.object(probe, "SOURCE_LOCK_TIMEOUT", 0.5):
            self.assertEqual(attempt(rig.api), {"refused": "probe_source_busy"})
        self.assertEqual(rig.thread_starts(), 0)
        self.assertFalse((rig.probe_dir / probe.CLAIM_NAME).exists())
        holder.communicate(timeout=60)

    def test_finished_probe_releases_the_source(self):
        rig = fx.Rig(self.tmp)
        self.assertTrue(attempt(rig.api)["accepted"])
        claim = json.loads((rig.probe_dir / probe.CLAIM_NAME).read_text(encoding="utf-8"))
        self.assertEqual(claim["state"], "finished")
        other = rig.make_api()
        self.assertTrue(attempt(other)["accepted"])
        self.assertEqual(rig.thread_starts(), 2)
        self.assertEqual(other.session_probe_status()["latest"]["outcome"], "round_trip_complete")

    def test_corrupt_claim_blocks_and_is_not_rewritten(self):
        rig = fx.Rig(self.tmp)
        rig.probe_dir.mkdir(parents=True)
        claim = rig.probe_dir / probe.CLAIM_NAME
        claim.write_bytes(b"{not json")
        self.assertEqual(attempt(rig.api), {"refused": "probe_claim_corrupt"})
        self.assertEqual(claim.read_bytes(), b"{not json")
        self.assertEqual(rig.thread_starts(), 0)

    def test_status_and_prepare_write_nothing(self):
        rig = fx.Rig(self.tmp)
        rig.api.session_probe_status()
        rig.api.prepare_session_probe({})
        self.assertFalse(rig.probe_dir.exists())


class TestSourceIsolation(_Case):
    def _assert_refused_clean(self, rig, code, untouched=()):
        before = {Path(p): Path(p).read_bytes() for p in untouched}
        self.assertEqual(attempt(rig.api), {"refused": code})
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data, path)
        self.assertEqual(rig.thread_starts(), 0)

    def test_history_hardlink_alias_is_refused_and_history_unchanged(self):
        for label, text in (("empty", ""), ("healthy", fx.fx.registry_line(event="reserved") + "\n")):
            with self.subTest(label):
                base = Path(self.tmp) / label
                rig = fx.Rig(base, history_text=text)
                rig.probe_dir.mkdir(parents=True)
                os.link(rig.history, rig.registry())
                self._assert_refused_clean(rig, "probe_registry_is_history_source", [rig.history])

    def test_registry_hardlinked_to_an_unrelated_file_is_refused(self):
        rig = fx.Rig(self.tmp)
        outside = Path(self.tmp) / "outside.jsonl"
        outside.write_bytes(b"")
        rig.probe_dir.mkdir(parents=True)
        os.link(outside, rig.registry())
        self._assert_refused_clean(rig, "probe_path_hardlinked", [outside])

    def test_claim_or_lock_hardlinked_is_refused(self):
        for name in (probe.CLAIM_NAME, probe.LOCK_NAME):
            with self.subTest(name):
                base = Path(self.tmp) / name
                rig = fx.Rig(base)
                outside = base / "outside"
                outside.write_bytes(b"keep")
                rig.probe_dir.mkdir(parents=True)
                os.link(outside, rig.probe_dir / name)
                self._assert_refused_clean(rig, "probe_path_hardlinked", [outside])

    def test_history_inside_the_probe_directory_is_refused(self):
        rig = fx.Rig(self.tmp)
        rig.probe_dir.mkdir(parents=True)
        inside = rig.probe_dir / "agent-runs.jsonl"
        inside.write_text("", encoding="utf-8")
        api = fx.AppInterface(db_path=rig.tmp / "no.sqlite3", runs_path=inside, session_probe=rig.config,
                              probe_adapter_factory=rig.factory, probe_runtime_check=lambda: None,
                              probe_runner=rig.runner)
        self.assertEqual(attempt(api), {"refused": "probe_registry_is_history_source"})
        self.assertEqual(rig.thread_starts(), 0)

    def _link(self, link, target):
        target.mkdir(parents=True, exist_ok=True)
        os.symlink(target, link, target_is_directory=True)

    @unittest.skipIf(NO_SYMLINKS, "symlink creation needs privileges on Windows; junctions untested")
    def test_linked_root_is_refused(self):
        target = Path(self.tmp) / "outside"
        link = Path(self.tmp) / "probe-link"
        self._link(link, target)
        rig = fx.Rig(self.tmp, probe_dir=link)
        self._assert_refused_clean(rig, "probe_path_is_link")
        self.assertEqual(files_under(target), [])

    @unittest.skipIf(NO_SYMLINKS, "symlink creation needs privileges on Windows; junctions untested")
    def test_linked_ancestor_is_refused_before_any_write(self):
        target = Path(self.tmp) / "outside"
        alias = Path(self.tmp) / "alias"
        self._link(alias, target)
        rig = fx.Rig(self.tmp, probe_dir=alias / "deeper" / "probe")
        self._assert_refused_clean(rig, "probe_path_is_link")
        self.assertEqual(files_under(target), [])

    @unittest.skipIf(NO_SYMLINKS, "symlink creation needs privileges on Windows; junctions untested")
    def test_linked_raw_and_ws_are_refused(self):
        for sub in ("raw", "ws"):
            with self.subTest(sub):
                base = Path(self.tmp) / sub
                target = base / "outside"
                rig = fx.Rig(base)
                rig.probe_dir.mkdir(parents=True)
                self._link(rig.probe_dir / sub, target)
                self._assert_refused_clean(rig, "probe_path_is_link")
                self.assertEqual(files_under(target), [])

    @unittest.skipIf(NO_SYMLINKS, "symlink creation needs privileges on Windows; junctions untested")
    def test_layout_changed_after_accept_is_refused_before_submit(self):
        rig = fx.Rig(self.tmp, runner="deferred")
        self.assertTrue(attempt(rig.api)["accepted"])
        target = Path(self.tmp) / "outside"
        self._link(rig.probe_dir / "raw", target)
        for job in rig.deferred:
            job()
        self.assertEqual(rig.thread_starts(), 0)
        self.assertEqual(files_under(target), [])
        self.assertFalse(rig.registry().exists())
        status = rig.api.session_probe_status()
        self.assertEqual(status["last_error"]["code"], "probe_path_is_link")
        self.assertEqual(status["claim"]["outcome"], "refused_before_dispatch")
        self.assertEqual(attempt(rig.make_api()), {"refused": "probe_path_is_link"})

    def test_legitimate_separate_directory_still_completes(self):
        rig = fx.Rig(self.tmp, history_text=fx.fx.registry_line() + "\n")
        before = rig.history.read_bytes()
        self.assertTrue(attempt(rig.api)["accepted"])
        self.assertEqual(rig.api.session_probe_status()["latest"]["outcome"], "round_trip_complete")
        self.assertEqual(rig.history.read_bytes(), before)
        self.assertEqual(rig.thread_starts(), 1)


class TestPredicates(unittest.TestCase):
    def test_file_identity_classes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertIsNone(probe.file_identity_problem(base / "absent"))
            plain = base / "plain"
            plain.write_bytes(b"x")
            self.assertIsNone(probe.file_identity_problem(plain))
            os.link(plain, base / "second")
            self.assertEqual(probe.file_identity_problem(plain)[0], "probe_path_hardlinked")
            self.assertEqual(probe.file_identity_problem(base)[0], "probe_path_invalid")
            if not NO_SYMLINKS:
                os.symlink(base / "absent", base / "dangling")
                self.assertEqual(probe.file_identity_problem(base / "dangling")[0], "probe_path_is_link")

    def test_reparse_attribute_counts_as_a_link(self):
        """The Windows junction branch, driven through its attribute only."""
        class Entry:
            st_mode = 0o040755
            st_file_attributes = probe._REPARSE_FLAG
        self.assertTrue(probe._is_link_entry(Entry()))
        Entry.st_file_attributes = 0
        self.assertFalse(probe._is_link_entry(Entry()))

    def test_claim_reader_never_folds_corrupt_into_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            self.assertEqual(probe.read_claim(path)[0], "absent")
            for raw in (b"", b"[]", b'{"schema":"x"}', json.dumps({"schema": probe.CLAIM_SCHEMA,
                        "probe_id": "../x", "state": "accepted"}).encode()):
                path.write_bytes(raw)
                self.assertEqual(probe.read_claim(path)[0], "corrupt", raw)
                self.assertTrue(probe.claim_blocks(*probe.read_claim(path)[:2]))


if __name__ == "__main__":
    unittest.main()
