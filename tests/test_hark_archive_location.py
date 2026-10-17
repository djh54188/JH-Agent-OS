"""Synthetic regressions for the Hark intake review; never production evidence."""
import contextlib
import json
import os
from pathlib import Path
from unittest.mock import patch

from test_jh006_capture_binding import _BindCase
import src.capture_binding as cb

ROOT = Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def _cwd(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class TestHarkArchiveLocation(_BindCase):
    def _registered_archive(self, relative):
        with _cwd(ROOT):
            orch, tid, delivery = self._prepared_production()
            self._fixture_apply()
            store = self._live_store()
            archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
            arg = Path(os.path.relpath(archive, ROOT)) if relative else archive
            with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
                row = self._register(orch, tid, delivery, arg)
        return orch, tid, delivery, archive, row

    def _assert_survives_cwd_change(self, relative):
        orch, tid, delivery, archive, row = self._registered_archive(relative)
        with _cwd(ROOT):
            self.assertTrue(cb.verify_binding_current(orch.storage, tid, delivery["delivery_id"])["current"])
        before_changes = orch.storage._conn.total_changes
        with _cwd(self.case):
            actual = cb.verify_binding_current(orch.storage, tid, delivery["delivery_id"])
        self.assertTrue(actual["current"], actual["reasons"])
        self.assertEqual(Path(row["archive_dir"]), archive.resolve(strict=True))
        self.assertTrue(Path(row["archive_dir"]).is_absolute())
        self.assertEqual(orch.storage._conn.total_changes, before_changes)
        self.assertEqual(orch.storage.get_task(tid)["status"], "reviewing")

    def test_relative_archive_survives_changed_cwd(self):
        self._assert_survives_cwd_change(True)

    def test_absolute_archive_survives_changed_cwd(self):
        self._assert_survives_cwd_change(False)

    def test_persisted_unverified_review_items_are_rejected(self):
        orch, tid, delivery, _, row = self._registered_archive(False)
        orch.storage._conn.execute(
            "UPDATE capture_bindings SET review_items_json=? WHERE binding_id=?",
            (json.dumps(["synthetic-unverified"]), row["binding_id"]))
        orch.storage._conn.commit()
        actual = cb.verify_binding_current(orch.storage, tid, delivery["delivery_id"])
        self.assertFalse(actual["current"])
        self.assertIn("invocation-unverified", actual["reasons"])
