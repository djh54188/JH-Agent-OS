"""JH-020 C-2/C-3 regression tests: durable host unconfirmed-side-effect marker,
its reconciliation entry, and the complete-time sealed-output assertion.

Covers the fixed code_sortfix_v1 template only, in isolated workspaces, with the
offline fixture host. No models run, no host command is executed here, and no
production gate is opened. Assertions target external state (task status, DB
records, delivery record, workspace bytes), never implementation return values.

C-2: a host apply that was prepared but never confirmed must be persisted as an
unconfirmed side effect when the task leaves 'reviewing', and reconciled
explicitly afterwards (reads target bytes, never grants success).
C-3: 'complete' must reject a registered raw host output that does not carry the
sealed post-apply stdout assertion, instead of trusting a matching digest alone.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_jh006_code_delivery import (  # noqa: E402  (shared isolated-workspace harness)
    _Case,
    TARGET,
    TPL,
    _baseline_bytes,
    _reference_bytes,
    _sha,
)
from src.cli import build_task_summary, main as cli_main  # noqa: E402
from src.code_delivery import (  # noqa: E402
    CodeDeliveryRefused,
    reconcile_host_effect,
    record_fixture_host_evidence,
    registered_output_satisfies_sealing,
    sealed_post_apply_expectation,
)
from src.storage import TaskStorage  # noqa: E402

PASS_RAW = "fixture-offline-host: PASS JH-006"
FAIL_RAW = "case 0: FAIL\nFAIL"


class _ReconcileCase(_Case):
    def _delivery_scope(self, orch, tid):
        return build_task_summary(orch.storage, tid)["task_result"]["delivery_scope"]

    def _events(self, orch, tid, event_type):
        return [e for e in orch.storage.list_events(tid) if e["event_type"] == event_type]

    def _waiting(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        _, delivery = self._run_to_waiting(orch, payload)
        return orch, payload["task_id"], delivery

    def _bad_stdout_receipt(self, orch, tid, delivery):
        """Register a host output whose digest matches but whose text says FAIL."""
        receipt = self._receipt(tid, delivery, tests={"output_digest": _sha(FAIL_RAW.encode())})
        return record_fixture_host_evidence(orch.storage, tid, receipt, FAIL_RAW)


class TestSealedOutputAssertion(_ReconcileCase):
    """C-3: the registered raw output must carry the sealed stdout marker."""

    def test_sealed_expectation_comes_from_sealed_checks(self):
        expectation = sealed_post_apply_expectation(TPL)
        self.assertEqual(expectation["expect_exit"], 0)
        self.assertEqual(expectation["expect_stdout_contains"], "PASS")

    def test_sealed_expectation_refuses_without_a_template_entry(self):
        fake = {"fixture_dir": "tests/fixtures/n2/data_rollup", "checks_rel": "checks.json",
                "check_name": "post-apply-target-check"}
        with self.assertRaises(CodeDeliveryRefused):
            sealed_post_apply_expectation(fake)

    def test_satisfies_sealing_truth_table(self):
        exp = {"expect_exit": 0, "expect_stdout_contains": "PASS"}
        self.assertTrue(registered_output_satisfies_sealing("case 0: ok\nPASS", exp))
        self.assertFalse(registered_output_satisfies_sealing(FAIL_RAW, exp))
        self.assertFalse(registered_output_satisfies_sealing(None, exp))
        self.assertFalse(registered_output_satisfies_sealing("PASS", {"expect_stdout_contains": ""}))

    def test_matching_digest_with_failing_text_never_succeeds(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        receipt = self._bad_stdout_receipt(orch, tid, delivery)
        result = orch.complete_code_delivery(tid, receipt)
        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(orch.storage.list_review_bindings(tid), [])
        scope = self._delivery_scope(orch, tid)
        self.assertEqual(scope["delivery_state"], "waiting_host")
        self.assertFalse(scope["target_applied"])
        self.assertIn("sealed post-apply assertion", (result.get("error_info") or ""))

    def test_passing_registered_output_still_succeeds(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        result = orch.complete_code_delivery(tid, self._receipt(tid, delivery))
        self.assertEqual(result["status"], "succeeded")
        self.assertFalse(self._delivery_scope(orch, tid)["host_effect_unconfirmed"])


class TestUnconfirmedHostEffectPersistence(_ReconcileCase):
    """C-2: leaving 'reviewing' with an unconfirmed apply persists the marker."""

    def test_cancel_of_waiting_delivery_flags_unconfirmed(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        orch.cancel_task(tid, reason="jh020 cancel probe")
        task = orch.storage.get_task(tid)
        self.assertEqual(task["status"], "cancelled")
        self.assertTrue(task["_runtime"]["has_unconfirmed_side_effect"])
        self.assertTrue(task["metadata"]["_delivery"]["unconfirmed_host_effect"])
        self.assertEqual(self._events(orch, tid, "host_effect_unconfirmed")[0]["to_state"], "cancelled")
        self.assertIn("unconfirmed_side_effect", build_task_summary(orch.storage, tid)["task_result"]["blockers"])

    def test_deadline_exhausted_complete_flags_unconfirmed(self):
        now = [1700000000.0]
        orch, _, _ = self._orch(clock=lambda: now[0])
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery = self._run_to_waiting(orch, payload)
        self._fixture_apply()
        now[0] += 601
        result = orch.complete_code_delivery(tid, self._receipt(tid, delivery))
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["_runtime"]["has_unconfirmed_side_effect"])

    def test_uncertain_complete_flags_unconfirmed(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        result = orch.complete_code_delivery(tid, self._receipt(tid, delivery, tests={"exit_code": 1}))
        self.assertEqual(result["status"], "needs_attention")
        self.assertTrue(result["_runtime"]["has_unconfirmed_side_effect"])

    def test_failure_without_a_waiting_delivery_is_not_flagged(self):
        # No false positive: an ordinary task that never prepared a host apply.
        orch, _, _ = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        orch.submit_task(payload)
        orch.storage.transition_task_state(tid, "failed", actor_role="orchestrator",
                                           reason="jh020 plain failure probe")
        task = orch.storage.get_task(tid)
        self.assertEqual(task["status"], "failed")
        self.assertFalse(task["_runtime"]["has_unconfirmed_side_effect"])
        self.assertEqual(self._events(orch, tid, "host_effect_unconfirmed"), [])


class TestReconcileHostEffect(_ReconcileCase):
    """C-2: the post-terminal reconciliation entry records reality, never success."""

    def _cancelled_flagged(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        orch.cancel_task(tid, reason="jh020 reconcile probe")
        return orch, tid, delivery

    def test_reconcile_records_observation_clears_marker_stays_terminal(self):
        orch, tid, _ = self._cancelled_flagged()
        self.assertTrue(orch.storage.get_task(tid)["_runtime"]["has_unconfirmed_side_effect"])
        record = reconcile_host_effect(orch.storage, tid, note="jh020 reconcile")
        self.assertEqual(record["status"], "cancelled")  # never reopened or made succeeded
        self.assertFalse(record["_runtime"]["has_unconfirmed_side_effect"])
        reconciled = record["metadata"]["_delivery"]["reconciled"]
        self.assertEqual(reconciled["observation"]["state"], "matches_expected_after")
        self.assertEqual(reconciled["note"], "jh020 reconcile")
        events = self._events(orch, tid, "host_effect_reconciled")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["details"]["observation"]["state"], "matches_expected_after")
        scope = self._delivery_scope(orch, tid)
        self.assertFalse(scope["host_effect_unconfirmed"])
        self.assertEqual(scope["reconciled"]["observation"]["state"], "matches_expected_after")

    def test_reconcile_reports_baseline_left_intact(self):
        orch, tid, _ = self._cancelled_flagged()
        (self.ws / TARGET).write_bytes(_baseline_bytes())  # host never applied
        record = reconcile_host_effect(orch.storage, tid, note="restored baseline")
        state = record["metadata"]["_delivery"]["reconciled"]["observation"]["state"]
        self.assertEqual(state, "matches_baseline")

    def test_reconcile_reports_diverged_target(self):
        orch, tid, _ = self._cancelled_flagged()
        (self.ws / TARGET).write_bytes(_reference_bytes() + b"\n# diverged\n")
        record = reconcile_host_effect(orch.storage, tid)
        self.assertEqual(record["metadata"]["_delivery"]["reconciled"]["observation"]["state"], "diverged")
        self.assertFalse(record["_runtime"]["has_unconfirmed_side_effect"])

    def test_reconcile_refused_without_unconfirmed_effect(self):
        orch, tid, _ = self._waiting()
        # Still in reviewing with a waiting delivery but nothing flagged yet.
        with self.assertRaises(CodeDeliveryRefused):
            reconcile_host_effect(orch.storage, tid)

    def test_second_reconcile_refused(self):
        orch, tid, _ = self._cancelled_flagged()
        reconcile_host_effect(orch.storage, tid)
        with self.assertRaises(CodeDeliveryRefused):
            reconcile_host_effect(orch.storage, tid)

    def test_reconcile_refused_for_applied_delivery(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        self.assertEqual(orch.complete_code_delivery(tid, self._receipt(tid, delivery))["status"], "succeeded")
        with self.assertRaises(CodeDeliveryRefused):
            reconcile_host_effect(orch.storage, tid)

    def test_reconcile_does_not_reopen_success_gate(self):
        orch, tid, _ = self._cancelled_flagged()
        reconcile_host_effect(orch.storage, tid)
        # Reconciliation clears the marker but the task is terminal; the success
        # gate still refuses a cancelled task and never auto-approves it.
        from src.state_machine import InvalidStateTransitionError
        with self.assertRaises(InvalidStateTransitionError):
            orch.storage.transition_task_state(tid, "succeeded", review_verdict="pass")


class TestReconcileCli(_ReconcileCase):
    def test_cli_reconcile_delivery_roundtrip(self):
        orch, _, _ = self._orch(use_file_db=True)
        payload = self._payload()
        tid = payload["task_id"]
        self._run_to_waiting(orch, payload)
        self._fixture_apply()
        orch.cancel_task(tid, reason="jh020 cli reconcile probe")
        orch.storage.close()
        code, out, err = self._cli_subprocess("reconcile-delivery", tid, "--note", "cli probe")
        self.assertEqual(code, 0, err)
        body = json.loads(out)
        self.assertEqual(body["task_result"]["status"], "cancelled")
        self.assertEqual(body["reconciliation"]["observation"]["state"], "matches_expected_after")
        probe = TaskStorage(str(self.db), read_only=True)
        try:
            self.assertFalse(probe.get_task(tid)["_runtime"]["has_unconfirmed_side_effect"])
        finally:
            probe.close()

    def test_cli_reconcile_refuses_when_nothing_to_reconcile(self):
        orch, _, _ = self._orch(use_file_db=True)
        payload = self._payload()
        tid = payload["task_id"]
        self._run_to_waiting(orch, payload)  # reviewing, waiting, not flagged
        orch.storage.close()
        code, out, err = self._cli_subprocess("reconcile-delivery", tid)
        self.assertEqual(code, 1)
        self.assertIn("no unconfirmed host side effect", json.loads(out)["error"])


class TestCliSummarySurface(_ReconcileCase):
    def test_summary_surfaces_host_effect_facts(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        receipt = self._bad_stdout_receipt(orch, tid, delivery)
        self.assertEqual(orch.complete_code_delivery(tid, receipt)["status"], "needs_attention")
        scope = self._delivery_scope(orch, tid)
        self.assertTrue(scope["host_effect_unconfirmed"])
        self.assertTrue(scope["unconfirmed_reason"])
        self.assertIsNone(scope["reconciled"])


if __name__ == "__main__":
    unittest.main()
