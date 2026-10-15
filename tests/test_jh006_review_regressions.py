"""Independent JH-006 review regressions; isolated fixtures, never production evidence."""
import copy
import json
import shutil
from unittest.mock import patch

import src.code_delivery as helpers
from src.adapters import SchemaValidationError
from src.cli import build_task_summary
from src.code_delivery import CodeDeliveryRefused
from test_jh006_code_delivery import (
    _Case, _run_cli, _sha, _reference_bytes, _baseline_bytes, _patch_envelope,
    TARGET, TPL, FIXTURE_DIR, ScriptedWorkerAdapter, TaskStorage)


class TestReviewedDeliveryBoundaries(_Case):
    def _waiting(self):
        orch, _, _ = self._orch(use_file_db=True)
        payload = self._payload()
        _, delivery = self._run_to_waiting(orch, payload)
        return orch, payload["task_id"], delivery

    def test_input_cannot_claim_applied_or_waiting_delivery(self):
        for state in ("applied", "waiting_host"):
            orch, _, _ = self._orch()
            payload = self._payload()
            payload["metadata"]["_delivery"] = {"state": state}
            with self.assertRaises(SchemaValidationError):
                orch.submit_task(payload)
            with self.assertRaises(KeyError):
                orch.storage.get_task(payload["task_id"])

    def test_cli_rejects_delivery_marker_before_assembly(self):
        payload = self._payload()
        payload["metadata"]["_delivery"] = {"state": "applied"}
        task_file = self.case / "task.json"
        task_file.write_text(json.dumps(payload), encoding="utf-8")
        code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                              "--test-fixture-mode", "submit", str(task_file)])
        self.assertEqual(code, 1)
        self.assertIn("control-owned", json.loads(out)["error"])
        self.assertFalse(self.db.exists())

    def test_unregistered_receipt_cannot_succeed(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        receipt = self._receipt(tid, delivery)
        receipt["host_call_id"] = "host_unregistered"
        result = orch.complete_code_delivery(tid, receipt)
        self.assertEqual(result["status"], "needs_attention")
        self.assertFalse(build_task_summary(orch.storage, tid)["task_result"]["delivery_scope"]["target_applied"])
        self.assertEqual(orch.storage.list_review_bindings(tid), [])

    def test_host_output_digest_must_match_persisted_raw_output(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        receipt = self._receipt(tid, delivery, tests={"output_digest": _sha(b"invented output")})
        result = orch.complete_code_delivery(tid, receipt)
        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(orch.storage.list_review_bindings(tid), [])

    def test_wrong_script_path_is_not_a_verified_check(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        receipt = self._receipt(tid, delivery, tests={"check_script": "different/script.py"})
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, receipt)
        self.assertEqual(orch.storage.get_task(tid)["status"], "reviewing")

    def test_uncertainty_requires_bool_not_zero_or_one(self):
        for flag in (0, 1):
            orch, tid, delivery = self._waiting()
            self._fixture_apply()
            receipt = self._receipt(tid, delivery, uncertain=flag)
            with self.assertRaises(CodeDeliveryRefused):
                orch.complete_code_delivery(tid, receipt)
            self.assertEqual(orch.storage.get_task(tid)["status"], "reviewing")
            (self.ws / TARGET).write_bytes(_baseline_bytes())
            orch.storage.close()
            self.db = self.case / ("flag-" + str(flag) + ".sqlite3")

    def test_unsealed_check_command_is_refused(self):
        fake = self.case / "fake-repo"
        folder = fake / TPL["fixture_dir"]
        shutil.copytree(FIXTURE_DIR, folder)
        checks = json.loads((folder / "checks.json").read_text(encoding="utf-8"))
        checks["checks"][-1]["command"] = ["python", "different.py"]
        (folder / "checks.json").write_text(json.dumps(checks), encoding="utf-8")
        with patch.object(helpers, "_repo_root", return_value=fake):
            with self.assertRaises(CodeDeliveryRefused):
                helpers.materialized_post_apply_command(TPL, self.ws)

    def test_prepare_respects_existing_owner(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        self._assemble_reviewing(orch, payload)
        tid = payload["task_id"]
        self.assertTrue(orch.storage.claim_task(tid, "existing-owner"))
        with self.assertRaises(CodeDeliveryRefused):
            orch.prepare_code_delivery(tid, 1)
        self.assertEqual(orch.storage.get_control(tid)["owner_id"], "existing-owner")
        self.assertNotIn("_delivery", orch.storage.get_task(tid)["metadata"])

    def test_prepare_rechecks_pending_approval(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        self._assemble_reviewing(orch, payload)
        tid = payload["task_id"]
        orch.storage.hold_request(tid, {"request_id": "treq_pending0001"})
        with self.assertRaises(CodeDeliveryRefused):
            orch.prepare_code_delivery(tid, 1)
        self.assertNotIn("_delivery", orch.storage.get_task(tid)["metadata"])

    def test_prepare_rechecks_interrupted_tool(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        self._assemble_reviewing(orch, payload)
        tid = payload["task_id"]
        request = {"schema_version": "1.0.0", "request_id": "treq_inflight0001",
                   "task_id": tid, "step_id": "step_probe", "caller_role": "orchestrator",
                   "tool_name": "read_file", "parameters": {"path": TARGET},
                   "timeout_ms": 1000, "risk_level": "read_only",
                   "requires_human_approval": False, "created_at": "2026-10-15T00:00:00Z"}
        orch.storage.record_tool_start(request, has_side_effect=False, allowed_states=("reviewing",))
        with self.assertRaises(CodeDeliveryRefused):
            orch.prepare_code_delivery(tid, 1)
        self.assertNotIn("_delivery", orch.storage.get_task(tid)["metadata"])

    def test_prepare_unknown_usage_never_parks(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        self._assemble_reviewing(orch, payload)
        tid = payload["task_id"]
        orch.storage.record_usage(tid, actor_role="worker", tokens_used=None)
        result = orch.prepare_code_delivery(tid, 1)
        self.assertEqual(result["status"], "needs_attention")
        self.assertNotIn("_delivery", result["metadata"])

    def test_prepare_and_event_rollback_together(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        self._assemble_reviewing(orch, payload)
        tid = payload["task_id"]
        before = orch.storage.list_events(tid)
        with patch.object(orch.storage, "record_event", side_effect=OSError("fixture interrupted event write")):
            with self.assertRaises(OSError):
                orch.prepare_code_delivery(tid, 1)
        self.assertNotIn("_delivery", orch.storage.get_task(tid)["metadata"])
        self.assertEqual(orch.storage.list_events(tid), before)
        self.assertIsNone(orch.storage.get_control(tid)["owner_id"])

    def test_complete_reloads_cancel_after_owner_acquisition(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        receipt = self._receipt(tid, delivery)
        original = orch.storage.claim_task
        def claim(task_id, owner_id):
            result = original(task_id, owner_id)
            orch.cancel_task(task_id, reason="fixture cancel during claim")
            return result
        with patch.object(orch.storage, "claim_task", side_effect=claim):
            with self.assertRaises(CodeDeliveryRefused):
                orch.complete_code_delivery(tid, receipt)
        self.assertEqual(orch.storage.get_task(tid)["status"], "cancelled")
        self.assertEqual(orch.storage.list_review_bindings(tid), [])

    def test_control_only_commands_need_no_model_assembly(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        receipt = self._receipt(tid, delivery)
        receipt_file = self.case / "receipt.json"
        receipt_file.write_text(json.dumps(receipt), encoding="utf-8")
        with patch("src.cli.create_orchestrator", side_effect=AssertionError("must not load models")):
            for command in (["resume", tid], ["prepare-code-delivery", tid, "1"],
                            ["complete-code-delivery", tid, str(receipt_file)]):
                code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                                      "--test-fixture-mode", *command])
                self.assertEqual(code, 0, out)
        self.assertEqual(orch.storage.get_task(tid)["status"], "succeeded")

    def test_generation_receives_template_and_sealed_baseline(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        messages = orch._build_generation_messages(payload, 0)
        context = json.loads(messages[-1]["content"])
        self.assertIn("code_template", context)
        self.assertEqual(context["code_template"]["baseline"], _baseline_bytes().decode("utf-8"))
        self.assertEqual(context["code_template"]["patch_format"]["path"], TARGET)
        self.assertEqual(context["code_template"]["artifact_type"], "patch_text")

    def test_fixture_model_tools_never_execute(self):
        worker = ScriptedWorkerAdapter(tool_requests_by_turn=[[{
            "schema_version": "1.0.0", "request_id": "treq_forbidden0001",
            "task_id": "task_jh006case0001", "step_id": "step_probe", "caller_role": "worker",
            "tool_name": "read_file", "parameters": {"path": TARGET}, "timeout_ms": 1000,
            "risk_level": "read_only", "requires_human_approval": False,
            "created_at": "2026-10-15T00:00:00Z"}]],
            artifact_content_by_turn=[_patch_envelope(_reference_bytes())], artifact_type="patch_text")
        orch, _, _ = self._orch(worker=worker)
        payload = self._payload()
        orch.submit_task(payload)
        result = orch.run_task(payload["task_id"])
        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(orch.storage.list_tool_executions(payload["task_id"]), [])
        self.assertNotIn("_delivery", result["metadata"])

    def test_extra_file_change_never_satisfies_authorized_scope(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        (self.ws / "unauthorized.txt").write_text("fixture extra change", encoding="utf-8")
        result = orch.complete_code_delivery(tid, self._receipt(tid, delivery))
        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(orch.storage.list_review_bindings(tid), [])

    def test_review_scope_defers_unexecuted_host_checks(self):
        orch, _, reviewer = self._orch()
        messages = []
        original = reviewer.invoke_structured
        def record(calls, *args, **kwargs):
            messages.extend(calls)
            return original(calls, *args, **kwargs)
        with patch.object(reviewer, "invoke_structured", side_effect=record):
            self._run_to_waiting(orch, self._payload())
        context = json.loads(messages[-1]["content"])
        self.assertIn("code_review_scope", context)
        self.assertEqual(context["code_review_scope"]["pending_host_criteria"], TPL["criteria"][1:])
        self.assertIn("post-apply criteria remain pending", messages[0]["content"])

    def test_waiting_idempotence_revalidates_artifact_version(self):
        orch, tid, delivery = self._waiting()
        orch.artifact_store.save_version(tid,
            envelope={"schema_version": "1.0.0", "artifact_id": "art_afterprepare0001",
                      "task_id": tid, "artifact_type": "patch_text",
                      "content": _patch_envelope(_reference_bytes()), "summary": "fixture newer patch"},
            version=2, generation_attempt_id=None)
        with self.assertRaises(CodeDeliveryRefused):
            orch.prepare_code_delivery(tid, 1)
        self.assertEqual(orch.storage.get_task(tid)["metadata"]["_delivery"], delivery)

    def test_control_completion_does_not_reserve_an_extra_model_turn(self):
        orch, _, _ = self._orch()
        payload = self._payload(budget={"max_turns": 3})
        _, delivery = self._run_to_waiting(orch, payload)
        tid = payload["task_id"]
        self._fixture_apply()
        result = orch.complete_code_delivery(tid, self._receipt(tid, delivery))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["_runtime"]["turns_used"], 3)

    def test_deadline_is_rechecked_after_verification_reads(self):
        now = [1700000000.0]
        orch, _, _ = self._orch(clock=lambda: now[0])
        payload = self._payload()
        _, delivery = self._run_to_waiting(orch, payload)
        tid = payload["task_id"]
        self._fixture_apply()
        receipt = self._receipt(tid, delivery)
        original = helpers.workspace_hashes
        def slow_read(ws):
            result = original(ws)
            now[0] += 3600
            return result
        with patch("src.orchestrator.workspace_hashes", side_effect=slow_read):
            result = orch.complete_code_delivery(tid, receipt)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(orch.storage.list_review_bindings(tid), [])
