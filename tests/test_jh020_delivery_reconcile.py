"""JH-020 C-2/C-3 regression tests: durable host unconfirmed-side-effect marker,
the observation-only reconciliation entry, pending-reconciliation discovery, and
the complete-time sealed-output assertion.

Covers the fixed code_sortfix_v1 template only, in isolated workspaces, with the
offline fixture host. No models run, no host command is executed here, and no
production gate is opened. Assertions target external state (task status, DB
records, delivery record, workspace bytes), never implementation return values.

C-2: a host apply that was prepared but never confirmed is persisted as an
unconfirmed side effect when the task leaves 'reviewing'. The reconciliation
entry only RECORDS an observation: a single byte read never releases the marker,
unreadable/diverged stay unresolved, repeat observation is allowed, and an owner
or other in-flight side effects are never preempted or overwritten. Unresolved
effects on terminal tasks stay discoverable.
C-3: 'complete' must reject a registered raw host output that does not carry the
sealed post-apply stdout assertion, instead of trusting a matching digest alone.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_digest_mismatch_with_passing_text_never_succeeds(self):
        # Registered raw output differs from the receipt's declared output_digest.
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        receipt = self._receipt(tid, delivery, tests={"output_digest": _sha(b"invented output")})
        result = orch.complete_code_delivery(tid, receipt)
        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(orch.storage.list_review_bindings(tid), [])

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

    def test_marker_and_audit_event_are_atomic(self):
        # The marker write and the host_effect_unconfirmed audit event commit as
        # one unit: an injected audit failure must roll both back.
        orch, tid, _ = self._waiting()
        self._fixture_apply()
        real = orch.storage.record_event

        def boom(**kw):
            if kw.get("event_type") == "host_effect_unconfirmed":
                raise RuntimeError("injected audit failure")
            return real(**kw)

        with patch.object(orch.storage, "record_event", boom):
            with self.assertRaises(RuntimeError):
                orch.cancel_task(tid, reason="jh020 atomicity probe")
        task = orch.storage.get_task(tid)
        self.assertEqual(task["status"], "reviewing")
        self.assertFalse(task["_runtime"]["has_unconfirmed_side_effect"])
        self.assertEqual(self._events(orch, tid, "host_effect_unconfirmed"), [])


class TestReconcileHostEffectObservationOnly(_ReconcileCase):
    """R1: reconciliation records the current observation and releases nothing."""

    def _cancelled_flagged(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        orch.cancel_task(tid, reason="jh020 observation probe")
        return orch, tid, delivery

    def test_observation_records_state_and_keeps_marker(self):
        orch, tid, _ = self._cancelled_flagged()
        record = reconcile_host_effect(orch.storage, tid, note="jh020 observation")
        self.assertEqual(record["status"], "cancelled")            # state unchanged
        self.assertTrue(record["_runtime"]["has_unconfirmed_side_effect"])  # marker NOT released
        delivery = record["metadata"]["_delivery"]
        self.assertEqual(delivery["reconciled"]["observation"]["state"], "matches_expected_after")
        self.assertEqual(delivery["reconciled"]["note"], "jh020 observation")
        self.assertEqual(len(delivery["observations"]), 1)
        events = self._events(orch, tid, "host_effect_observed")
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["details"]["marker_released"])
        scope = self._delivery_scope(orch, tid)
        self.assertTrue(scope["host_effect_unconfirmed"])         # still unresolved to callers
        self.assertIn("unconfirmed_side_effect", build_task_summary(orch.storage, tid)["task_result"]["blockers"])

    def test_repeat_observation_is_allowed(self):
        orch, tid, _ = self._cancelled_flagged()
        reconcile_host_effect(orch.storage, tid, note="first")
        record = reconcile_host_effect(orch.storage, tid, note="second")
        self.assertEqual(len(record["metadata"]["_delivery"]["observations"]), 2)
        self.assertTrue(record["_runtime"]["has_unconfirmed_side_effect"])
        self.assertEqual(len(self._events(orch, tid, "host_effect_observed")), 2)

    def test_unreadable_target_stays_unresolved_and_reobservable(self):
        orch, tid, _ = self._cancelled_flagged()
        (self.ws / TARGET).unlink()
        first = reconcile_host_effect(orch.storage, tid, note="unreadable")
        state = first["metadata"]["_delivery"]["reconciled"]["observation"]["state"]
        self.assertEqual(state, "unreadable")
        self.assertTrue(first["_runtime"]["has_unconfirmed_side_effect"])
        second = reconcile_host_effect(orch.storage, tid, note="observed again")
        self.assertEqual(len(second["metadata"]["_delivery"]["observations"]), 2)

    def test_diverged_target_keeps_marker(self):
        orch, tid, _ = self._cancelled_flagged()
        (self.ws / TARGET).write_bytes(_reference_bytes() + b"\n# diverged\n")
        record = reconcile_host_effect(orch.storage, tid, note="diverged")
        self.assertEqual(record["metadata"]["_delivery"]["reconciled"]["observation"]["state"], "diverged")
        self.assertTrue(record["_runtime"]["has_unconfirmed_side_effect"])

    def test_baseline_target_keeps_marker(self):
        orch, tid, _ = self._cancelled_flagged()
        (self.ws / TARGET).write_bytes(_baseline_bytes())
        record = reconcile_host_effect(orch.storage, tid, note="baseline")
        self.assertEqual(record["metadata"]["_delivery"]["reconciled"]["observation"]["state"], "matches_baseline")
        self.assertTrue(record["_runtime"]["has_unconfirmed_side_effect"])

    def test_owner_held_task_is_observed_without_preemption(self):
        orch, tid, _ = self._cancelled_flagged()
        self.assertTrue(orch.storage.claim_task(tid, "independent_active_owner"))
        record = reconcile_host_effect(orch.storage, tid, note="observe under foreign owner")
        self.assertEqual(orch.storage.get_control(tid)["owner_id"], "independent_active_owner")
        self.assertTrue(record["_runtime"]["has_unconfirmed_side_effect"])
        self.assertEqual(record["metadata"]["_delivery"]["reconciled"]["note"], "observe under foreign owner")

    def test_other_inflight_side_effect_is_not_touched(self):
        orch, tid, _ = self._cancelled_flagged()
        req = {"schema_version": "1.0.0", "request_id": "treq_obs_extra0001",
               "task_id": tid, "step_id": "step_obs", "caller_role": "orchestrator",
               "tool_name": "write_file", "parameters": {"path": "target/other.py"},
               "timeout_ms": 1000, "risk_level": "workspace_write",
               "requires_human_approval": False, "created_at": "2026-10-18T00:00:00Z"}
        self.assertTrue(orch.storage.record_tool_start(req, has_side_effect=True, allowed_states=("cancelled",)))
        record = reconcile_host_effect(orch.storage, tid, note="observing beside an in-flight tool")
        self.assertTrue(record["_runtime"]["has_unconfirmed_side_effect"])
        inflight = [r for r in orch.storage.list_tool_executions(tid)
                    if r["status"] == "in_flight" and r["has_side_effect"]]
        self.assertEqual([r["request_id"] for r in inflight], ["treq_obs_extra0001"])

    def test_observation_refused_without_unconfirmed_effect(self):
        orch, tid, _ = self._waiting()  # reviewing, waiting, nothing flagged
        with self.assertRaises(CodeDeliveryRefused):
            reconcile_host_effect(orch.storage, tid)

    def test_observation_refused_for_applied_delivery(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        self.assertEqual(orch.complete_code_delivery(tid, self._receipt(tid, delivery))["status"], "succeeded")
        with self.assertRaises(CodeDeliveryRefused):
            reconcile_host_effect(orch.storage, tid)

    def test_observation_does_not_reopen_success_gate(self):
        orch, tid, _ = self._cancelled_flagged()
        reconcile_host_effect(orch.storage, tid)
        from src.state_machine import InvalidStateTransitionError
        with self.assertRaises(InvalidStateTransitionError):
            orch.storage.transition_task_state(tid, "succeeded", review_verdict="pass")


class TestPendingReconciliationDiscovery(_ReconcileCase):
    """R2: unresolved host effects stay discoverable; enumeration is read-only."""

    def _terminal_flagged_file_db(self):
        orch, _, _ = self._orch(use_file_db=True)
        payload = self._payload()
        tid = payload["task_id"]
        self._run_to_waiting(orch, payload)
        self._fixture_apply()
        orch.cancel_task(tid, reason="jh020 discovery probe")
        orch.storage.close()
        return tid

    def test_pending_list_survives_database_reopen(self):
        tid = self._terminal_flagged_file_db()
        reader = TaskStorage(str(self.db), read_only=True)
        try:
            rows = reader.list_unresolved_host_effects()
        finally:
            reader.close()
        self.assertEqual([r["task_id"] for r in rows], [tid])
        self.assertTrue(rows[0]["is_terminal"])
        self.assertEqual(rows[0]["status"], "cancelled")
        self.assertIsNotNone(rows[0]["delivery_id"])

    def test_recover_reports_terminal_unresolved_without_state_change(self):
        tid = self._terminal_flagged_file_db()
        orch, _, _ = self._orch(use_file_db=True)
        try:
            summary = orch.recover_interrupted_tasks()
            row = next(r for r in summary if r["task_id"] == tid)
            self.assertEqual(row["reason"], "host_side_effect_unresolved")
            self.assertEqual(row["recovered_state"], "cancelled")
            self.assertFalse(row["auto_replay_allowed"])
            after = orch.storage.get_task(tid)
            self.assertEqual(after["status"], "cancelled")
            self.assertTrue(after["_runtime"]["has_unconfirmed_side_effect"])
        finally:
            orch.storage.close()

    def test_enumeration_does_not_clear_marker_or_claim_owner(self):
        tid = self._terminal_flagged_file_db()
        orch, _, _ = self._orch(use_file_db=True)
        try:
            before = orch.storage.get_task(tid)
            orch.storage.list_unresolved_host_effects()
            orch.recover_interrupted_tasks()
            build_task_summary(orch.storage, tid)
            after = orch.storage.get_task(tid)
            self.assertEqual(after["status"], before["status"])
            self.assertTrue(after["_runtime"]["has_unconfirmed_side_effect"])
            self.assertIsNone(orch.storage.get_control(tid)["owner_id"])
        finally:
            orch.storage.close()

    def test_cli_pending_reconciliation_is_read_only(self):
        tid = self._terminal_flagged_file_db()
        code, out, err = self._cli_subprocess("pending-reconciliation")
        self.assertEqual(code, 0, err)
        body = json.loads(out)
        self.assertTrue(body["read_only"])
        self.assertEqual([r["task_id"] for r in body["pending"]], [tid])
        probe = TaskStorage(str(self.db), read_only=True)
        try:
            self.assertTrue(probe.get_task(tid)["_runtime"]["has_unconfirmed_side_effect"])
        finally:
            probe.close()


class TestReconcileCli(_ReconcileCase):
    def test_cli_reconcile_delivery_records_observation_only(self):
        orch, _, _ = self._orch(use_file_db=True)
        payload = self._payload()
        tid = payload["task_id"]
        self._run_to_waiting(orch, payload)
        self._fixture_apply()
        orch.cancel_task(tid, reason="jh020 cli observation probe")
        orch.storage.close()
        code, out, err = self._cli_subprocess("reconcile-delivery", tid, "--note", "cli probe")
        self.assertEqual(code, 0, err)
        body = json.loads(out)
        self.assertEqual(body["task_result"]["status"], "cancelled")
        self.assertEqual(body["observation"]["observation"]["state"], "matches_expected_after")
        self.assertTrue(body["task_result"]["runtime_usage"]["has_unconfirmed_side_effect"])
        probe = TaskStorage(str(self.db), read_only=True)
        try:
            self.assertTrue(probe.get_task(tid)["_runtime"]["has_unconfirmed_side_effect"])
        finally:
            probe.close()

    def test_cli_reconcile_refuses_when_nothing_to_observe(self):
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
