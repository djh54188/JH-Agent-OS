"""Hark-派单-13: a NEW adapter must not mistake a lost registry for a fresh store.

A first adapter A submits key K (in-memory launcher: no process, no model; the
outcome stays undetermined), which leaves `reserved`/`dispatched` records and
`runs/<run_id>/`. The registry is then deleted, emptied, blanked or cut back and
a new adapter B (a restart) submits K again. B must refuse rather than dispatch.
Controls keep a truly fresh store, a complete history and legal reserved-only
prefixes working. Offline; no real CLI, no model.
"""
import contextlib
import hashlib
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "gemini_cli"))

from src import agent_session_gemini as gem  # noqa: E402
from src.agent_session import IdempotencyConflict, RunState, SessionAddress, SubmitRequest  # noqa: E402

ADDRESS = SessionAddress(app_kind="gemini-cli", instance_id="gemini-cli/local", session_id="new")
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


class PendingProcess:
    """A launched CLI whose outcome is never determined."""
    returncode = None

    def poll(self):
        return None

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired("pending", timeout)


class Store:
    def __init__(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="hark13-"))
        self.ws = os.path.join(self.tmp, "ws")
        os.makedirs(os.path.join(self.ws, "src"))
        with open(os.path.join(self.ws, "src", "a.py"), "w") as fh:
            fh.write("x = 1\n")
        self.ev = os.path.join(self.tmp, "evidence")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.launches = 0

    @property
    def registry(self):
        return os.path.join(self.ev, "registry.jsonl")

    @property
    def runs_dir(self):
        return os.path.join(self.ev, "runs")

    def config(self):
        return gem.GeminiCliConfig(argv_prefix=("gemini-not-started",), cli_home=self.home,
                                   evidence_root=self.ev, registry_lock_timeout=10.0)

    def adapter(self):
        def launcher(argv, **kw):
            self.launches += 1
            return PendingProcess()
        return gem.GeminiCliAgentSessionAdapter(self.config(), launcher=launcher,
                                                version_probe=lambda c: "0.65.0")

    def req(self, key="K", task="t", instructions="Locate x."):
        return SubmitRequest(task_id=task, attempt=1, session=ADDRESS, instructions=instructions,
                             workspace=self.ws, idempotency_key=key)

    def run_ids(self):
        return sorted(os.listdir(self.runs_dir)) if os.path.isdir(self.runs_dir) else []

    def evidence_digest(self):
        """Every byte under the evidence root except the lock file."""
        h = hashlib.sha256()
        for dirpath, dirnames, filenames in os.walk(self.ev):
            dirnames.sort()
            for name in sorted(filenames):
                if name == "registry.lock":
                    continue
                path = os.path.join(dirpath, name)
                h.update(os.path.relpath(path, self.ev).encode() + b"\0")
                with open(path, "rb") as fh:
                    h.update(fh.read())
        return h.hexdigest()

    def lines(self):
        with open(self.registry, "rb") as fh:
            return fh.read().splitlines(keepends=True)

    def close(self):
        if os.path.isdir(self.runs_dir):
            os.chmod(self.runs_dir, 0o700)
        shutil.rmtree(self.tmp, ignore_errors=True)


def lose(store, how, keep=None):
    if how == "delete":
        os.remove(store.registry)
    elif how == "zero":
        open(store.registry, "wb").close()
    elif how == "blank":
        with open(store.registry, "wb") as fh:
            fh.write(b" \n\t\n\n")
    elif how == "keep":
        lines = store.lines()
        with open(store.registry, "wb") as fh:
            fh.write(b"".join(lines[:keep]))
    else:
        raise ValueError(how)


class _Base(unittest.TestCase):
    def store(self):
        s = Store()
        self.addCleanup(s.close)
        return s

    def assert_refused(self, store, adapter, request):
        before_launches = store.launches
        before_runs = store.run_ids()
        before_bytes = store.evidence_digest()
        with self.assertRaises(gem.RegistryUnavailable):
            adapter.submit(request)
        self.assertEqual(store.launches, before_launches)
        self.assertEqual(store.run_ids(), before_runs)          # no new run reserved
        self.assertEqual(store.evidence_digest(), before_bytes)  # source bytes untouched


class TestLostRegistryNewObject(_Base):
    def _one_layout(self, how):
        s = self.store()
        a = s.adapter()
        first = a.submit(s.req())
        self.assertEqual(first.state, RunState.RUNNING)
        lose(s, how)
        b = s.adapter()                                      # restart: new object
        self.assert_refused(s, b, s.req())
        self.assert_refused(s, b, s.req(key="K-other", task="t-other"))
        self.assertEqual(s.run_ids(), [first.run_id])
        return s

    def test_registry_deleted(self):
        for repeat in (1, 2):
            with self.subTest(root=repeat):
                self._one_layout("delete")

    def test_registry_zero_bytes(self):
        for repeat in (1, 2):
            with self.subTest(root=repeat):
                self._one_layout("zero")

    def test_registry_whitespace_only(self):
        for repeat in (1, 2):
            with self.subTest(root=repeat):
                self._one_layout("blank")

    def test_non_empty_tail_lost(self):
        s = self.store()
        a = s.adapter()
        k1 = a.submit(s.req(key="K1", task="t1"))
        k2 = a.submit(s.req(key="K2", task="t2"))
        self.assertEqual(len(s.lines()), 4)
        lose(s, "keep", keep=2)                              # only K1's complete records
        b = s.adapter()
        self.assert_refused(s, b, s.req(key="K2", task="t2"))
        self.assert_refused(s, b, s.req(key="K1", task="t1"))   # even K1: store is incomplete
        self.assertEqual(s.run_ids(), sorted([k1.run_id, k2.run_id]))

    def test_restart_in_a_new_process(self):
        s = self.store()
        a = s.adapter()
        a.submit(s.req())
        lose(s, "delete")
        ctx = multiprocessing.get_context("spawn")
        import h13_restart
        queue = ctx.Queue()
        proc = ctx.Process(target=h13_restart.worker,
                           args=({"home": s.home, "evidence": s.ev, "workspace": s.ws}, queue))
        proc.start()
        outcome = queue.get(timeout=120)
        proc.join(timeout=60)
        self.assertEqual(outcome[0], "refused", outcome)
        self.assertEqual(outcome[1], "RegistryUnavailable")
        self.assertEqual(outcome[3], 0)                      # launches inside that process

    def test_constructed_before_loss_also_refuses(self):
        s = self.store()
        a = s.adapter()
        a.submit(s.req())
        b = s.adapter()                                      # saw the complete registry
        lose(s, "zero")
        self.assert_refused(s, b, s.req())


class TestUnjudgeableRunEvidence(_Base):
    def _seed(self):
        s = self.store()
        s.adapter().submit(s.req())
        return s

    @unittest.skipIf(IS_ROOT or os.name == "nt", "needs POSIX permissions as non-root")
    def test_runs_dir_unreadable_with_lost_registry(self):
        s = self._seed()
        lose(s, "delete")
        os.chmod(s.runs_dir, 0)
        before = s.launches
        with self.assertRaises(gem.RegistryUnavailable):
            s.adapter().submit(s.req())
        self.assertEqual(s.launches, before)

    @unittest.skipIf(IS_ROOT or os.name == "nt", "needs POSIX permissions as non-root")
    def test_runs_dir_unreadable_with_intact_registry(self):
        s = self._seed()
        os.chmod(s.runs_dir, 0)
        before = s.launches
        with self.assertRaises(gem.RegistryUnavailable):
            s.adapter().submit(s.req(key="K2", task="t2"))
        self.assertEqual(s.launches, before)

    def test_runs_dir_listing_error_injected(self):
        s = self._seed()
        lose(s, "delete")
        real = os.scandir

        def scandir(path="."):
            if os.path.realpath(os.fspath(path)) == os.path.realpath(s.runs_dir):
                raise PermissionError(13, "injected", os.fspath(path))
            return real(path)
        b = s.adapter()
        with mock.patch("os.scandir", scandir):
            self.assert_refused(s, b, s.req())

    def test_stray_entries_refused(self):
        for kind in ("file", "badname-dir", "symlink"):
            with self.subTest(kind=kind):
                s = self.store()
                os.makedirs(s.runs_dir)
                if kind == "file":
                    with open(os.path.join(s.runs_dir, "notes.txt"), "w") as fh:
                        fh.write("x")
                elif kind == "badname-dir":
                    os.makedirs(os.path.join(s.runs_dir, "run-1"))
                else:
                    target = os.path.join(s.tmp, "elsewhere")
                    os.makedirs(target)
                    os.symlink(target, os.path.join(s.runs_dir, "gemini-run-0123456789ab"))
                self.assert_refused(s, s.adapter(), s.req())

    def test_runs_path_not_a_directory(self):
        s = self.store()
        os.makedirs(s.ev)
        with open(s.runs_dir, "w") as fh:
            fh.write("x")
        self.assert_refused(s, s.adapter(), s.req())

    def test_whitespace_registry_without_runs_refused(self):
        s = self.store()
        os.makedirs(s.ev)
        with open(s.registry, "wb") as fh:
            fh.write(b"\n")
        self.assert_refused(s, s.adapter(), s.req())


class TestControls(_Base):
    def test_truly_fresh_store_dispatches(self):
        s = self.store()
        status = s.adapter().submit(s.req())
        self.assertEqual(status.state, RunState.RUNNING)
        self.assertEqual(s.launches, 1)
        self.assertEqual(s.run_ids(), [status.run_id])

    def test_zero_byte_registry_without_runs_is_a_fresh_store(self):
        s = self.store()
        os.makedirs(s.ev)
        open(s.registry, "wb").close()
        s.adapter().submit(s.req())
        self.assertEqual(s.launches, 1)

    def test_complete_registry_reopened(self):
        s = self.store()
        first = s.adapter().submit(s.req())
        b = s.adapter()
        replay = b.submit(s.req())
        self.assertEqual(replay.run_id, first.run_id)
        self.assertEqual(replay.state, RunState.UNKNOWN)
        self.assertEqual(s.launches, 1)
        with self.assertRaises(IdempotencyConflict):
            b.submit(s.req(instructions="different"))
        fresh = b.submit(s.req(key="K2", task="t2"))
        self.assertNotEqual(fresh.run_id, first.run_id)
        self.assertEqual(s.launches, 2)

    def test_reserved_only_prefix(self):
        s = self.store()
        first = s.adapter().submit(s.req())
        lose(s, "keep", keep=1)
        replay = s.adapter().submit(s.req())
        self.assertEqual((replay.run_id, replay.state), (first.run_id, RunState.UNKNOWN))
        self.assertEqual(s.launches, 1)

    def test_two_runs_each_reserved_only(self):
        s = self.store()
        a = s.adapter()
        r1 = a.submit(s.req(key="K1", task="t1"))
        r2 = a.submit(s.req(key="K2", task="t2"))
        kept = [l for l in s.lines() if json.loads(l)["event"] == "reserved"]
        with open(s.registry, "wb") as fh:
            fh.write(b"".join(kept))
        b = s.adapter()
        o1 = b.submit(s.req(key="K1", task="t1"))
        o2 = b.submit(s.req(key="K2", task="t2"))
        self.assertEqual([o1.run_id, o2.run_id], [r1.run_id, r2.run_id])
        self.assertEqual([o1.state, o2.state], [RunState.UNKNOWN, RunState.UNKNOWN])
        self.assertEqual(s.launches, 2)


WEAKENINGS = {
    "unaccounted_run_traces": (lambda *a, **k: [], "TestLostRegistryNewObject.test_registry_deleted"),
}


class TestGuardWeakening(unittest.TestCase):
    def test_weakened_guard_fails_its_test(self):
        module = sys.modules[__name__]
        for name, (weak, target) in WEAKENINGS.items():
            with self.subTest(guard=name):
                cls_name, method = target.split(".")
                control = unittest.TestResult()
                getattr(module, cls_name)(method).run(control)
                self.assertTrue(control.wasSuccessful(), f"{target} must pass unweakened")
                with mock.patch.object(gem, name, weak):
                    weakened = unittest.TestResult()
                    getattr(module, cls_name)(method).run(weakened)
                self.assertFalse(weakened.wasSuccessful(), f"weakening {name} went undetected")


if __name__ == "__main__":
    unittest.main()
