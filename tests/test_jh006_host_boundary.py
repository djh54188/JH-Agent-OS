"""Safety regressions for the rejected JH-006 host-record candidate.

All production-shaped tasks and source events are deliberately synthetic,
created by the test harness only. No host command/model is executed here.
These tests prove containment, never a working production bridge.
"""
import json
import uuid
from unittest.mock import patch
from src.cli import build_task_summary
from src.code_delivery import CodeDeliveryRefused
from test_jh006_code_delivery import (
    _Case, _run_cli, _reference_bytes, _baseline_bytes, _check_bytes, _sha,
    TPL, TARGET)

CHECK_REL = f"{TPL['fixture_dir']}/{TPL['check_rel']}"


class TestHostSourceBoundary(_Case):
    def _prepared(self):
        orch, worker, reviewer = self._orch(use_file_db=True)
        payload = self._payload()
        task, review, _ = self._assemble_reviewing(
            orch, payload, stamp_source="production", with_source_event=False)
        task = orch.prepare_code_delivery(payload["task_id"], 1)
        self._fixture_apply()
        return orch, payload["task_id"], task["metadata"]["_delivery"], review

    def _claim(self, tid, delivery, review):
        output = "case 0: ok\ncase 1: ok\ncase 2: ok\ncase 3: ok\nPASS\n"
        return {
            "task_id": tid, "delivery_id": delivery["delivery_id"],
            "thread_id": str(uuid.uuid4()), "host_call_id": "item_1",
            "apply_attempt_id": "apply_" + uuid.uuid4().hex,
            "workdir": str(self.ws), "artifact": dict(delivery["artifact"]),
            "review": {"review_id": review["review_id"], "verdict": "pass"},
            "target": {"path": TARGET, "sha256": _sha(_reference_bytes())},
            "baseline_seen": _sha(_baseline_bytes()),
            "tests": {"target_path": TARGET, "target_sha256": _sha(_reference_bytes()),
                      "check_script": CHECK_REL, "check_script_sha256": _sha(_check_bytes()),
                      "command": f'pwsh.exe -Command "& python {CHECK_REL} --target {self.ws}/{TARGET}"',
                      "exit_code": 0, "output_digest": _sha(output.encode()), "status": "completed"},
            "output": output, "uncertain": False, "recorder": "codex-transcript/v1",
            "recorded_at": "2026-10-16T00:00:00Z"}

    def _assert_refused_without_success(self, orch, tid, claim):
        before = orch.storage.get_task(tid)
        before_attempts = orch.storage.list_model_attempts(tid)
        before_events = orch.storage.list_events(tid)
        deadline = orch.storage.get_control(tid)["deadline"]
        target_bytes = (self.ws / TARGET).read_bytes()
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, claim)
        after = orch.storage.get_task(tid)
        self.assertEqual(after["status"], "reviewing")
        self.assertEqual(after["metadata"]["_delivery"], before["metadata"]["_delivery"])
        self.assertEqual(after["_runtime"], before["_runtime"])
        self.assertEqual(orch.storage.list_model_attempts(tid), before_attempts)
        self.assertEqual(orch.storage.list_events(tid), before_events)
        self.assertEqual(orch.storage.get_control(tid)["deadline"], deadline)
        self.assertEqual(orch.storage.list_review_bindings(tid), [])
        self.assertEqual(orch.storage.list_memory_records(tid), [])
        self.assertEqual((self.ws / TARGET).read_bytes(), target_bytes)
        scope = build_task_summary(orch.storage, tid)["task_result"]["delivery_scope"]
        self.assertFalse(scope["target_applied"])
        self.assertNotEqual(scope["evidence_kind"], "host_transcript")

    def test_self_consistent_claim_is_not_independent_host_evidence(self):
        orch, tid, delivery, review = self._prepared()
        self._assert_refused_without_success(orch, tid, self._claim(tid, delivery, review))

    def test_echoed_check_markers_cannot_complete(self):
        orch, tid, delivery, review = self._prepared()
        claim = self._claim(tid, delivery, review)
        claim["tests"]["command"] = f'Write-Output "PASS {CHECK_REL} {TARGET}"'
        self._assert_refused_without_success(orch, tid, claim)

    def test_other_workspace_in_command_cannot_complete(self):
        orch, tid, delivery, review = self._prepared()
        claim = self._claim(tid, delivery, review)
        claim["tests"]["command"] = f'python {CHECK_REL} --target D:/different-workspace/{TARGET}'
        self._assert_refused_without_success(orch, tid, claim)

    def test_rehashing_fabricated_output_does_not_create_authority(self):
        orch, tid, delivery, review = self._prepared()
        claim = self._claim(tid, delivery, review)
        claim["output"] = "PASS: self-reported output, zero host executions"
        claim["tests"]["output_digest"] = _sha(claim["output"].encode())
        self._assert_refused_without_success(orch, tid, claim)

    def test_arbitrary_thread_and_item_identifiers_cannot_complete(self):
        orch, tid, delivery, review = self._prepared()
        claim = self._claim(tid, delivery, review)
        claim["thread_id"] = str(uuid.uuid4())
        claim["host_call_id"] = "item_999"
        self._assert_refused_without_success(orch, tid, claim)

    def test_cli_rejects_unregistered_host_claim(self):
        orch, tid, delivery, review = self._prepared()
        claim = self._claim(tid, delivery, review)
        receipt = self.case / "host-claim.json"
        receipt.write_text(json.dumps(claim), encoding="utf-8")
        before = orch.storage.get_task(tid)["_runtime"]
        code, output = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                                 "complete-code-delivery", tid, str(receipt)])
        self.assertEqual(code, 1, output)
        self.assertFalse(json.loads(output)["command_ok"])
        self.assertEqual(orch.storage.get_task(tid)["status"], "reviewing")
        self.assertEqual(orch.storage.get_task(tid)["_runtime"], before)
        self.assertEqual(orch.storage.list_review_bindings(tid), [])

    def test_waiting_production_resume_is_read_only_and_needs_no_models(self):
        orch, tid, delivery, review = self._prepared()
        before = orch.storage.get_task(tid)["_runtime"]
        with patch("src.cli.create_orchestrator", side_effect=AssertionError("no model assembly")):
            code, output = _run_cli(["--db", str(self.db), "--workspace", str(self.ws), "resume", tid])
        self.assertEqual(code, 0, output)
        body = json.loads(output)
        self.assertTrue(body["read_only"])
        self.assertEqual(body["task_result"]["status"], "reviewing")
        self.assertFalse(body["task_result"]["delivery_scope"]["target_applied"])
        self.assertEqual(orch.storage.get_task(tid)["_runtime"], before)

    def test_claim_file_and_json_query_do_not_promote_application(self):
        orch, tid, delivery, review = self._prepared()
        (self.case / "untrusted-record.json").write_text(
            json.dumps(self._claim(tid, delivery, review)), encoding="utf-8")
        code, output = _run_cli(["--db", str(self.db), "--workspace", str(self.ws), "summary", tid])
        self.assertEqual(code, 0)
        scope = json.loads(output)["task_result"]["delivery_scope"]
        self.assertEqual(scope["delivery_state"], "waiting_host")
        self.assertFalse(scope["target_applied"])
        self.assertIsNone(scope["evidence_kind"])
