"""Hark-派单-08: the cleanup honours the refusals already made about the source.

Closes the 07 review's R2 tail: after the worker refused a settled, static
layout (root / ancestor link, or a link whose target already holds a synthetic
claim), the cleanup used to reopen the path, create probe.lock there and mark
the foreign claim finished. Now the claim is written back only to the same
plain directory and the same claim file this object wrote at acceptance;
otherwise nothing is created or changed and the refusal is reported (the
source stays blocked for manual handling).

Facts asserted: thread/start calls at the fake transport, the FILE SET and the
BYTES of every link target / swapped-in directory, the parked original claim,
and what status reports. Offline only; no model, no app-server. POSIX symlinks
stand in for Windows junctions (skipped on Windows; junctions unverified here).
"""
import json
import os
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_hark06_session_probe as fx  # noqa: E402
import src.session_probe as probe  # noqa: E402
from src.app_api import ApiError  # noqa: E402

NO_SYMLINKS = os.name == "nt"


def snapshot(root):
    root = Path(root)
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None)
            for p in sorted(root.rglob("*"))}


def attempt(api):
    try:
        prepared = api.prepare_session_probe({})
        started = api.start_session_probe({"probe_id": prepared["probe_id"], "confirm": True})
        return {"accepted": started.get("accepted") is True}
    except ApiError as exc:
        return {"refused": exc.code}


class _Case(fx._Case):
    def accepted_rig(self, base, probe_dir=None, **kw):
        rig = fx.Rig(base, probe_dir=probe_dir, runner="deferred", **kw)
        self.assertTrue(attempt(rig.api)["accepted"])
        return rig

    def claim_state(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))["state"]

    def run_worker(self, rig):
        for job in rig.deferred:
            job()
        return rig.api.session_probe_status()


@unittest.skipIf(NO_SYMLINKS, "symlink creation needs privileges on Windows; junctions untested")
class TestCleanupAfterRefusedLinkLayout(_Case):
    def _swap_for_link(self, original, target):
        parked = original.parent / (original.name + "-parked")
        original.rename(parked)
        os.symlink(target, original, target_is_directory=True)
        return parked

    def test_root_link_to_empty_target_gets_no_lock_and_no_write(self):
        base = Path(self.tmp)
        rig = self.accepted_rig(base)
        target = base / "outside"
        target.mkdir()
        parked = self._swap_for_link(rig.probe_dir, target)
        status = self.run_worker(rig)
        self.assertEqual(rig.thread_starts(), 0)
        self.assertEqual(snapshot(target), {})
        self.assertEqual(self.claim_state(parked / probe.CLAIM_NAME), "accepted")
        self.assertEqual(status["last_error"]["code"], "probe_path_is_link")
        self.assertEqual(status["last_error"]["cleanup_refused"]["code"], "probe_path_is_link")

    def test_root_link_to_a_target_with_a_synthetic_claim_is_not_marked_finished(self):
        base = Path(self.tmp)
        rig = self.accepted_rig(base)
        target = base / "outside"
        target.mkdir()
        shutil.copyfile(rig.probe_dir / probe.CLAIM_NAME, target / probe.CLAIM_NAME)
        shutil.copyfile(rig.probe_dir / probe.LOCK_NAME, target / probe.LOCK_NAME)
        before = snapshot(target)
        self.assertEqual(self.claim_state(target / probe.CLAIM_NAME), "accepted")
        parked = self._swap_for_link(rig.probe_dir, target)
        self.run_worker(rig)
        self.assertEqual(rig.thread_starts(), 0)
        self.assertEqual(snapshot(target), before)
        self.assertEqual(self.claim_state(parked / probe.CLAIM_NAME), "accepted")

    def test_ancestor_link_with_a_probe_subdirectory_gets_nothing(self):
        base = Path(self.tmp)
        rig = self.accepted_rig(base, probe_dir=base / "parent" / "probe")
        target = base / "outside"
        (target / "probe").mkdir(parents=True)
        before = snapshot(target)
        parked = self._swap_for_link(rig.probe_dir.parent, target)
        status = self.run_worker(rig)
        self.assertEqual(rig.thread_starts(), 0)
        self.assertEqual(snapshot(target), before)
        self.assertEqual(self.claim_state(parked / "probe" / probe.CLAIM_NAME), "accepted")
        self.assertIn("cleanup_refused", status["last_error"])

    def test_raw_or_ws_link_still_finishes_the_claim_in_the_real_source(self):
        for sub in ("raw", "ws"):
            with self.subTest(sub):
                base = Path(self.tmp) / sub
                rig = self.accepted_rig(base)
                target = base / "outside"
                target.mkdir()
                os.symlink(target, rig.probe_dir / sub, target_is_directory=True)
                status = self.run_worker(rig)
                self.assertEqual(rig.thread_starts(), 0)
                self.assertEqual(snapshot(target), {})
                self.assertEqual(status["claim"]["claim_state"], "finished")
                self.assertEqual(status["claim"]["outcome"], "refused_before_dispatch")
                self.assertNotIn("cleanup_refused", status["last_error"])


class TestCleanupAfterPlainReplacement(_Case):
    """No links at all: a plain directory or claim swapped in after acceptance.
    Runs on every platform."""

    def test_swapped_plain_directory_is_neither_dispatched_to_nor_cleaned_up(self):
        base = Path(self.tmp)
        rig = self.accepted_rig(base)
        parked = base / "parked"
        rig.probe_dir.rename(parked)
        rig.probe_dir.mkdir()
        shutil.copyfile(parked / probe.CLAIM_NAME, rig.probe_dir / probe.CLAIM_NAME)
        shutil.copyfile(parked / probe.LOCK_NAME, rig.probe_dir / probe.LOCK_NAME)
        before = snapshot(rig.probe_dir)
        status = self.run_worker(rig)
        self.assertEqual(rig.thread_starts(), 0)
        self.assertEqual(snapshot(rig.probe_dir), before)
        self.assertEqual(status["last_error"]["code"], "probe_source_replaced")
        self.assertEqual(status["last_error"]["cleanup_refused"]["code"], "probe_source_replaced")
        self.assertEqual(self.claim_state(parked / probe.CLAIM_NAME), "accepted")
        self.assertEqual(attempt(rig.make_api()), {"refused": "probe_claim_unresolved"})

    def test_replaced_claim_file_is_not_rewritten(self):
        base = Path(self.tmp)
        rig = self.accepted_rig(base)
        claim = rig.probe_dir / probe.CLAIM_NAME
        data = claim.read_bytes()
        claim.unlink()
        claim.write_bytes(data)                               # same bytes, another file
        status = self.run_worker(rig)
        self.assertEqual(rig.thread_starts(), 0)
        self.assertEqual(claim.read_bytes(), data)
        self.assertEqual(status["last_error"]["code"], "probe_claim_replaced")

    def test_missing_lock_is_not_recreated_and_the_claim_stays_blocking(self):
        base = Path(self.tmp)
        rig = self.accepted_rig(base)
        (rig.probe_dir / probe.LOCK_NAME).unlink()
        status = self.run_worker(rig)
        self.assertEqual(rig.thread_starts(), 1)              # nothing redirected: the dispatch ran
        self.assertFalse((rig.probe_dir / probe.LOCK_NAME).exists())
        self.assertEqual(self.claim_state(rig.probe_dir / probe.CLAIM_NAME), "accepted")
        self.assertEqual(status["last_error"]["cleanup_refused"]["code"], "probe_lock_missing")
        self.assertEqual(attempt(rig.make_api()), {"refused": "probe_claim_unresolved"})

    def test_normal_source_still_finishes_and_unknown_still_blocks(self):
        rig = fx.Rig(Path(self.tmp) / "ok")
        self.assertTrue(attempt(rig.api)["accepted"])
        status = rig.api.session_probe_status()
        self.assertEqual(status["claim"]["claim_state"], "finished")
        self.assertIsNone(status["last_error"])
        self.assertTrue(attempt(rig.make_api())["accepted"])
        unknown = fx.Rig(Path(self.tmp) / "unknown", transport_kw={"timeout_method": "thread/start"})
        self.assertTrue(attempt(unknown.api)["accepted"])
        self.assertEqual(unknown.api.session_probe_status()["claim"]["claim_state"], "finished")
        self.assertEqual(attempt(unknown.make_api()), {"refused": "probe_unresolved"})
        self.assertEqual(unknown.thread_starts(), 1)


if __name__ == "__main__":
    unittest.main()
