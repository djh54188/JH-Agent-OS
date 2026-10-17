"""Independent registration regressions; all source logs here are synthetic."""
import copy
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from test_jh006_capture_binding import _BindCase, THREAD, ITEM, CALL
from test_jh006_code_delivery import _baseline_bytes
import src.capture_binding as cb
from src.storage import TaskStorage


class TestBindingAudit(_BindCase):
    def _registered(self, file_db=False):
        orch, tid, delivery = self._prepared_production(use_file_db=file_db)
        self._fixture_apply(); store = self._live_store()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            row = self._register(orch, tid, delivery, archive)
        return orch, tid, delivery, store, archive, row

    def test_target_drift_invalidates_current(self):
        orch, tid, delivery, _, _, _ = self._registered()
        (self.ws / "target/widget.py").write_bytes(_baseline_bytes())
        result = cb.verify_binding_current(orch.storage, tid, delivery["delivery_id"])
        self.assertFalse(result["current"])
        self.assertIn("target-bytes-mismatch", result["reasons"])

    def test_review_payload_revoked_invalidates_current(self):
        orch, tid, delivery, _, _, _ = self._registered()
        attempt = orch.storage.list_model_attempts(tid)[-1]
        review = attempt["response"]; review["verdict"] = "revise"
        orch.storage._conn.execute("UPDATE model_attempts SET response_json=? WHERE attempt_id=?",
                                  (json.dumps(review), attempt["attempt_id"]))
        result = cb.verify_binding_current(orch.storage, tid, delivery["delivery_id"])
        self.assertFalse(result["current"])
        self.assertIn("review-changed", result["reasons"])

    def test_active_attempt_change_invalidates_current(self):
        orch, tid, delivery, _, _, _ = self._registered()
        orch.storage.begin_attempt(tid, "worker", {})
        self.assertFalse(cb.verify_binding_current(orch.storage, tid, delivery["delivery_id"])["current"])

    def test_workspace_change_invalidates_current(self):
        orch, tid, delivery, _, _, _ = self._registered()
        task = orch.storage.get_task(tid); task["context"]["workspace_root"] = str(self.case / "other")
        task.pop("_runtime", None)
        orch.storage._conn.execute("UPDATE tasks SET task_json=? WHERE task_id=?",
                                  (json.dumps(task), tid))
        self.assertFalse(cb.verify_binding_current(orch.storage, tid, delivery["delivery_id"])["current"])

    def test_echo_reassertion_is_not_a_fixed_check(self):
        orch, tid, delivery = self._prepared_production(); self._fixture_apply()
        store = self._live_store(); argv = ["pwsh.exe", "-Command", "Write-Output PASS"]
        script = "text(await tools.exec_command({cmd:'Write-Output PASS'}));"
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store,
                                            argv=argv, call_script=script)
        before = self._snapshot_state(orch, tid)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(cb.BindingRefused, "invocation-unverified"):
                self._register(orch, tid, delivery, archive, expected_argv=argv, expected_call_input=script)
        self._assert_state_kept(orch, tid, before)
        self.assertIsNone(cb.get_capture_binding(orch.storage, tid, delivery["delivery_id"]))

    def test_old_unregistered_call_cannot_be_relabelled(self):
        orch, tid_a, delivery_a = self._prepared_production(); self._fixture_apply()
        store = self._live_store(); old_plan = self._plan_for(tid_a, delivery_a)
        archive, _, _, _ = self._archive_for(tid_a, delivery_a, live_store=store)
        (self.ws / "target/widget.py").write_bytes(_baseline_bytes())
        payload = self._payload(); tid_b = payload["task_id"]
        self._assemble_reviewing(orch, payload, stamp_source="production", with_source_event=False)
        delivery_b = orch.prepare_code_delivery(tid_b, 1)["metadata"]["_delivery"]
        self._fixture_apply()
        manifest = json.loads((archive / "manifest.json").read_text(encoding="utf8"))
        manifest.update(task_claim=tid_b, delivery_claim=delivery_b["delivery_id"],
                        artifact_claim=delivery_b["artifact"], review_claim={"review_id":delivery_b["review_id"]})
        (archive / "manifest.json").write_text(json.dumps(manifest), encoding="utf8")
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(cb.BindingRefused, "invocation-unverified"):
                self._register(orch, tid_b, delivery_b, archive,
                    expected_argv=old_plan["argv"], expected_call_input=old_plan["call_input"])
        self.assertIsNone(cb.get_capture_binding(orch.storage, tid_b, delivery_b["delivery_id"]))

    def test_changed_expected_after_refused(self):
        orch, tid, delivery = self._prepared_production(); self._fixture_apply()
        delivery["expected_after"]["sha256"] = "0" * 64
        orch.storage.save_code_delivery(tid, delivery)
        with self.assertRaisesRegex(cb.BindingRefused, "delivery-malformed"):
            cb.capture_invocation_for_delivery(orch.storage, tid, delivery["delivery_id"])

    def test_unknown_usage_does_not_provide_current_binding(self):
        orch, tid, delivery, _, _, _ = self._registered()
        orch.storage.record_usage(tid, actor_role="worker", tokens_used=None,
                                  event_id="test-bind-unknown", details={})
        self.assertFalse(cb.verify_binding_current(orch.storage, tid, delivery["delivery_id"])["current"])

    def test_approval_pending_does_not_provide_current_binding(self):
        orch, tid, delivery, _, _, _ = self._registered()
        orch.storage._conn.execute("UPDATE task_control SET pending_json='{}' WHERE task_id=?",(tid,))
        self.assertFalse(cb.verify_binding_current(orch.storage, tid, delivery["delivery_id"])["current"])

    def test_registration_event_failure_rolls_back_row(self):
        orch, tid, delivery = self._prepared_production(); self._fixture_apply()
        store = self._live_store(); archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        before = self._snapshot_state(orch, tid)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store), patch.object(orch.storage, "record_event", side_effect=RuntimeError("injected-event-failure")):
            with self.assertRaisesRegex(RuntimeError,"injected-event-failure"):
                self._register(orch, tid, delivery, archive)
        self.assertIsNone(cb.get_capture_binding(orch.storage, tid, delivery["delivery_id"]))
        self._assert_state_kept(orch, tid, before)
        self.assertIsNone(orch.storage.get_control(tid)["owner_id"])

    def test_control_plan_runs_sealed_check_on_actual_target(self):
        orch, tid, delivery = self._prepared_production(); self._fixture_apply()
        plan = cb.capture_invocation_for_delivery(orch.storage, tid, delivery["delivery_id"])
        before = (self.ws / "target/widget.py").read_bytes()
        result = subprocess.run(plan["argv"], cwd=self.ws, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS", result.stdout)
        self.assertEqual((self.ws / "target/widget.py").read_bytes(), before)
        self.assertNotIn("succeeded", orch.storage.get_task(tid)["status"])

    def test_control_plan_baseline_bytes_fail_before_check(self):
        orch, tid, delivery = self._prepared_production()
        plan = cb.capture_invocation_for_delivery(orch.storage, tid, delivery["delivery_id"])
        result = subprocess.run(plan["argv"], cwd=self.ws, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertNotIn("PASS", result.stdout)

    def test_same_call_with_another_item_violates_database_unique(self):
        orch, _, _, _, _, row = self._registered()
        other = dict(row); other.update(binding_id="cb_other", delivery_id="other",item_id="other-item")
        with self.assertRaises(ValueError):
            orch.storage.save_capture_binding(other)
        self.assertEqual(orch.storage._conn.execute("SELECT count(*) FROM capture_bindings").fetchone()[0],1)

    def test_same_item_with_another_call_violates_database_unique(self):
        orch, _, _, _, _, row = self._registered()
        other = dict(row); other.update(binding_id="cb_other", delivery_id="other",call_id="other-call")
        with self.assertRaises(ValueError):
            orch.storage.save_capture_binding(other)
        self.assertEqual(orch.storage._conn.execute("SELECT count(*) FROM capture_bindings").fetchone()[0],1)

    def test_readonly_verification_preserves_database_bytes(self):
        orch, tid, delivery, _, _, _ = self._registered(file_db=True)
        orch.storage.close(); before = self.db.read_bytes()
        with_store = TaskStorage(self.db, read_only=True)
        try:
            self.assertTrue(cb.verify_binding_current(with_store, tid, delivery["delivery_id"])["current"])
        finally:
            with_store.close()
        self.assertEqual(self.db.read_bytes(), before)

    def test_boolean_artifact_claim_is_not_version_one(self):
        orch, tid, delivery = self._prepared_production(); self._fixture_apply()
        store = self._live_store()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store,
                                            claim_over={"artifact_claim":{"version":True}})
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(cb.BindingRefused, "claim-artifact-mismatch"):
                self._register(orch, tid, delivery, archive)
        self.assertIsNone(cb.get_capture_binding(orch.storage, tid, delivery["delivery_id"]))

    def test_target_drift_during_source_validation_refused(self):
        orch, tid, delivery = self._prepared_production(); self._fixture_apply()
        store = self._live_store(); archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        events = orch.storage.list_events(tid)
        original = cb._verify_liveness
        def drift(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.ws / "target/widget.py").write_bytes(_baseline_bytes())
            return result
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store), patch.object(cb, "_verify_liveness", side_effect=drift):
            with self.assertRaisesRegex(cb.BindingRefused,"target-bytes-mismatch"):
                self._register(orch, tid, delivery, archive)
        self.assertIsNone(cb.get_capture_binding(orch.storage, tid, delivery["delivery_id"]))
        self.assertEqual(orch.storage.list_events(tid), events)

    def test_wrong_apply_attempt_refused(self):
        orch, tid, delivery = self._prepared_production(); self._fixture_apply()
        store = self._live_store()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store,
                                            claim_over={"apply_attempt_id":"arbitrary"})
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(cb.BindingRefused,"apply-attempt-mismatch"):
                self._register(orch, tid, delivery, archive)
        self.assertIsNone(cb.get_capture_binding(orch.storage, tid, delivery["delivery_id"]))
