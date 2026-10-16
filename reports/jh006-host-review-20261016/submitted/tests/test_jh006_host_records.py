"""JH-006 batch 2 offline controls for production host transcript records.

Positive and negative controls for the host-record bridge only. All
transcript-shaped records below are SYNTHETIC test fixtures (thread/item ids
prefixed "test-"); they prove the control gates work, never that a real host
executed anything. Real evidence requires a reviewed native-host session.
Assertions target external state (task status, database records, workspace
bytes), never just implementation return values.
"""
import hashlib
import io
import json
import shutil
import sys
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.artifacts import mem_artifact_path
from src.cli import build_task_summary, main as cli_main
from src.code_delivery import (
    CODE_FIXTURE_ID,
    CODE_TEMPLATE_ID,
    CODE_TEMPLATES,
    OFFLINE_FIXTURE_RECORDER,
    CodeDeliveryRefused,
    record_fixture_host_evidence,
)
from src.host_records import (
    HOST_BRIDGE_RECORDER,
    MAX_OUTPUT_CHARS,
    sealed_check_expectation,
    transcript_command_markers_ok,
)
from src.mock_components import MockToolExecutor
from src.orchestrator import Orchestrator
from src.storage import TaskStorage

TPL = CODE_TEMPLATES[CODE_FIXTURE_ID]
FIXTURE_DIR = ROOT / TPL["fixture_dir"]
CRITERIA = list(TPL["criteria"])
TARGET = TPL["authorized_target"]
CHECK_REL = f"{TPL['fixture_dir']}/{TPL['check_rel']}"
CREATED_AT = "2026-10-15T00:00:00Z"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _baseline_bytes() -> bytes:
    return (FIXTURE_DIR / TPL["baseline_rel"]).read_bytes()


def _reference_bytes() -> bytes:
    return (FIXTURE_DIR / TPL["reference_rel"]).read_bytes()


def _check_bytes() -> bytes:
    return (FIXTURE_DIR / TPL["check_rel"]).read_bytes()


def _patch_envelope(content_bytes: bytes) -> str:
    return json.dumps({"path": TARGET, "content": content_bytes.decode("utf-8")},
                      ensure_ascii=False)


def _run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli_main(argv)
    return code, buf.getvalue()


class _Case(unittest.TestCase):
    def setUp(self):
        self.case = ROOT / "data" / "test-workspaces" / ("jh006hr-" + uuid.uuid4().hex[:8])
        self.ws = self.case / "ws"
        (self.ws / "target").mkdir(parents=True)
        (self.ws / TARGET).write_bytes(_baseline_bytes())
        self.addCleanup(shutil.rmtree, self.case, ignore_errors=True)
        self.db = self.case / "jh006hr.sqlite3"
        self._seq = 0

    def _task_id(self):
        self._seq += 1
        return f"task_hostrec{self._seq:04d}"

    def _payload(self, task_id=None, *, source="production"):
        return {
            "schema_version": "1.0.0",
            "task_id": task_id or self._task_id(),
            "title": "jh006 host record bridge probe",
            "description": "jh006 host record bridge probe",
            "acceptance_criteria": list(CRITERIA),
            "priority": "high",
            "status": "created",
            "risk_assessment": {"risk_level": "workspace_write", "complexity": "low",
                                "uncertainty": "low", "risk_basis": "jh006 bridge",
                                "routing_basis": "jh006"},
            "budget": {"max_turns": 8, "max_tokens": 60000, "max_execution_time_seconds": 600},
            "context": {"workspace_root": str(self.ws), "target_files": [TARGET]},
            "artifact_refs": [],
            "tool_execution_evidence": [],
            "review_verdict": None,
            "error_info": None,
            "assigned_worker": "prod_worker",
            "assigned_reviewer": "prod_reviewer",
            "metadata": {"fixture_id": CODE_FIXTURE_ID, "review_target_artifact_type": "patch",
                         "_trusted": {"source": source, "template_id": CODE_TEMPLATE_ID,
                                      "execution_mode": "generate"}},
            "created_at": CREATED_AT,
            "updated_at": CREATED_AT,
        }

    def _orch(self, *, use_file_db=False):
        storage = TaskStorage(str(self.db) if use_file_db else ":memory:")
        self.addCleanup(storage.close)
        orch = Orchestrator(storage=storage, workspace_root=self.ws,
                            tool_executor=MockToolExecutor())
        return orch

    def _assemble_waiting(self, orch, payload, *, with_source_event=True):
        """Manually assemble a prepared waiting task (no model calls, no adapters)."""
        orch.submit_task(payload)
        tid = payload["task_id"]
        tag = f"{self._seq:04d}{uuid.uuid4().hex[:4]}"
        envelope = {"schema_version": "1.0.0", "artifact_id": f"art_bridge{tag}",
                    "task_id": tid, "artifact_type": "patch_text",
                    "content": _patch_envelope(_reference_bytes()),
                    "summary": "jh006 bridge assembly"}
        orch.artifact_store.save_version(tid, envelope=envelope, version=1,
                                         generation_attempt_id=None)
        latest = orch.artifact_store.get_latest(tid)
        mem_ref = mem_artifact_path(tid, envelope["artifact_id"])
        orch.storage.attach_artifact(
            tid, mem_ref,
            f"artifact:{envelope['artifact_id']}@v1:sha256={latest['sha256']}")
        for state in ("classified", "planning", "executing", "reviewing"):
            orch.storage.transition_task_state(tid, state, actor_role="orchestrator",
                                               reason="jh006 bridge assembly")
        review = {"schema_version": "1.0.0", "review_id": f"rev_bridge{tag}",
                  "task_id": tid, "target_artifact_type": "patch",
                  "target_artifact_path": mem_ref,
                  "verdict": "pass", "overall_score": 95.0, "issues": [],
                  "summary": "jh006 bridge pass", "reviewer_role": "prod_reviewer",
                  "created_at": CREATED_AT}
        aid = orch.storage.begin_attempt(
            tid, "reviewer", {"artifact_hashes": {mem_ref: latest["sha256"]}})
        orch.storage.complete_attempt(tid, aid, review, 100)
        if with_source_event:
            orch.storage.record_event(
                task_id=tid, event_type="model_response_source",
                from_state="reviewing", to_state="reviewing", actor_role="orchestrator",
                details={"attempt_id": aid, "role": "reviewer",
                         "adapter_class": "ProdAdapter", "model_id": "prod",
                         "is_mock": False})
        prepared = orch.prepare_code_delivery(tid, 1)
        delivery = prepared["metadata"]["_delivery"]
        self.assertEqual(prepared["status"], "reviewing")
        self.assertEqual(delivery["state"], "waiting_host")
        return prepared, delivery, review

    def _host_record(self, tid, delivery, review, **over):
        actual = (self.ws / TARGET).read_bytes()
        output = "case 0: ok\ncase 1: ok\ncase 2: ok\ncase 3: ok\nPASS\n"
        command = (f"pwsh.exe -Command \"& python {CHECK_REL} "
                   f"--target {self.ws}/{TARGET}\"")
        art = delivery["artifact"]
        record = {
            "task_id": tid,
            "delivery_id": delivery["delivery_id"],
            "thread_id": "test-thread-0001",
            "host_call_id": "test-item-0001",
            "apply_attempt_id": "test-apply-0001",
            "workdir": str(self.ws),
            "artifact": {"version": art["version"], "artifact_id": art["artifact_id"],
                         "sha256": art["sha256"]},
            "review": {"review_id": review["review_id"], "verdict": "pass"},
            "target": {"path": TARGET, "sha256": _sha(actual)},
            "baseline_seen": _sha(_baseline_bytes()),
            "tests": {"target_path": TARGET, "target_sha256": _sha(actual),
                      "check_script": CHECK_REL,
                      "check_script_sha256": _sha(_check_bytes()),
                      "command": command, "exit_code": 0,
                      "output_digest": _sha(output.encode("utf-8")),
                      "status": "completed"},
            "output": output,
            "uncertain": False,
            "recorder": HOST_BRIDGE_RECORDER,
            "recorded_at": CREATED_AT,
        }
        for key, val in over.items():
            if isinstance(val, dict) and isinstance(record.get(key), dict):
                record[key] = {**record[key], **val}
            else:
                record[key] = val
        return record

    def _assert_still_waiting(self, orch, tid):
        task = orch.storage.get_task(tid)
        self.assertEqual(task["status"], "reviewing")
        self.assertEqual(task["metadata"]["_delivery"]["state"], "waiting_host")

    def _assert_needs_attention(self, orch, tid):
        task = orch.storage.get_task(tid)
        self.assertEqual(task["status"], "needs_attention")
        reasons = [e["details"].get("reason") for e in orch.storage.list_events(tid)]
        self.assertIn("code_delivery_uncertain", reasons)


class TestProductionHappyPath(_Case):
    def test_host_record_completes_with_verified_apply(self):
        orch = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery, review = self._assemble_waiting(orch, payload)
        turns = orch.storage.get_task(tid)["_runtime"]["turns_used"]
        attempts = len(orch.storage.list_model_attempts(tid))
        (self.ws / TARGET).write_bytes(_reference_bytes())
        result = orch.complete_code_delivery(tid, self._host_record(tid, delivery, review))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["review_verdict"], "pass")
        done = result["metadata"]["_delivery"]
        self.assertEqual(done["state"], "applied")
        self.assertEqual(done["apply_receipt"]["recorder"], HOST_BRIDGE_RECORDER)
        self.assertEqual((self.ws / TARGET).read_bytes(), _reference_bytes())
        checks_ref = f"delivery:{delivery['delivery_id']}:host-transcript-checks:exit0"
        self.assertIn(checks_ref, result["tool_execution_evidence"])
        bindings = orch.storage.list_review_bindings(tid)
        self.assertEqual(len(bindings), 1)
        self.assertEqual(len(bindings[0]["acceptance"]), 3)
        for item in bindings[0]["acceptance"]:
            self.assertTrue(item["passed"])
            self.assertTrue(item["evidence"])
        accepted = [e for e in orch.storage.list_events(tid)
                    if e.get("event_type") == "host_record_accepted"]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["details"]["host_call_id"], "test-item-0001")
        self.assertEqual(accepted[0]["details"]["apply_attempt_id"], "test-apply-0001")
        self.assertEqual(accepted[0]["details"]["record"]["output_digest"],
                         accepted[0]["details"]["output_digest"])
        self.assertGreaterEqual(len(orch.storage.list_memory_records(tid)), 1)
        self.assertEqual(orch.storage.get_task(tid)["_runtime"]["turns_used"], turns)
        self.assertEqual(len(orch.storage.list_model_attempts(tid)), attempts)
        scope = build_task_summary(orch.storage, tid)["task_result"]["delivery_scope"]
        self.assertEqual(scope["delivery_state"], "applied")
        self.assertTrue(scope["target_applied"])
        self.assertEqual(scope["evidence_kind"], "host_transcript")

    def test_production_prepare_still_refuses_tainted_history(self):
        orch = self._orch()
        payload = self._payload()
        orch.submit_task(payload)
        tid = payload["task_id"]
        tag = f"{self._seq:04d}{uuid.uuid4().hex[:4]}"
        envelope = {"schema_version": "1.0.0", "artifact_id": f"art_bridge{tag}",
                    "task_id": tid, "artifact_type": "patch_text",
                    "content": _patch_envelope(_reference_bytes()),
                    "summary": "taint probe"}
        orch.artifact_store.save_version(tid, envelope=envelope, version=1,
                                         generation_attempt_id=None)
        latest = orch.artifact_store.get_latest(tid)
        mem_ref = mem_artifact_path(tid, envelope["artifact_id"])
        orch.storage.attach_artifact(tid, mem_ref, f"artifact:{envelope['artifact_id']}@v1")
        for state in ("classified", "planning", "executing", "reviewing"):
            orch.storage.transition_task_state(tid, state, actor_role="orchestrator",
                                               reason="taint probe")
        review = {"schema_version": "1.0.0", "review_id": f"rev_bridge{tag}",
                  "task_id": tid, "target_artifact_type": "patch",
                  "target_artifact_path": mem_ref, "verdict": "pass",
                  "overall_score": 95.0, "issues": [], "summary": "taint probe",
                  "reviewer_role": "prod_reviewer", "created_at": CREATED_AT}
        aid = orch.storage.begin_attempt(
            tid, "reviewer", {"artifact_hashes": {mem_ref: latest["sha256"]}})
        orch.storage.complete_attempt(tid, aid, review, 100)
        with self.assertRaises(CodeDeliveryRefused):
            orch.prepare_code_delivery(tid, 1)
        task = orch.storage.get_task(tid)
        self.assertEqual(task["status"], "reviewing")
        self.assertNotIn("_delivery", task.get("metadata") or {})


class TestSourceSeparation(_Case):
    def test_fixture_registration_refuses_production_tasks(self):
        orch = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery, _ = self._assemble_waiting(orch, payload)
        with self.assertRaises(CodeDeliveryRefused):
            record_fixture_host_evidence(orch.storage, tid, {"delivery_id": delivery["delivery_id"]},
                                         "synthetic output")
        self._assert_still_waiting(orch, tid)

    def test_fixture_receipt_cannot_complete_production(self):
        orch = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery, _ = self._assemble_waiting(orch, payload)
        (self.ws / TARGET).write_bytes(_reference_bytes())
        fixture_receipt = {
            "task_id": tid, "delivery_id": delivery["delivery_id"],
            "target": {"path": TARGET, "sha256": _sha(_reference_bytes())},
            "baseline_seen": _sha(_baseline_bytes()),
            "tests": {"target_path": TARGET, "target_sha256": _sha(_reference_bytes()),
                      "check_script": CHECK_REL,
                      "check_script_sha256": _sha(_check_bytes()),
                      "command": ["python", CHECK_REL], "exit_code": 0,
                      "output_digest": _sha(b"fixture")},
            "uncertain": False, "recorder": OFFLINE_FIXTURE_RECORDER,
            "recorded_at": CREATED_AT, "host_call_id": "host_x", "apply_attempt_id": "apply_x",
        }
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, fixture_receipt)
        self._assert_still_waiting(orch, tid)

    def test_production_record_cannot_complete_fixture_task(self):
        orch = self._orch()
        payload = self._payload(source="fixture")
        tid = payload["task_id"]
        _, delivery, review = self._assemble_waiting(orch, payload)
        (self.ws / TARGET).write_bytes(_reference_bytes())
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, self._host_record(tid, delivery, review))
        self._assert_still_waiting(orch, tid)


class TestHostRecordTruthGates(_Case):
    def _waiting(self):
        orch = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery, review = self._assemble_waiting(orch, payload)
        (self.ws / TARGET).write_bytes(_reference_bytes())
        return orch, tid, delivery, review

    def test_tampered_output_digest_refused(self):
        orch, tid, delivery, review = self._waiting()
        record = self._host_record(tid, delivery, review)
        record["output"] = record["output"] + "# appended after digest\n"
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, record)
        self._assert_still_waiting(orch, tid)

    def test_output_missing_marker_goes_uncertain(self):
        orch, tid, delivery, review = self._waiting()
        output = "case 0: FAIL got=['c'] expected=['a']\nFAIL\n"
        record = self._host_record(
            tid, delivery, review, output=output,
            tests={"output_digest": _sha(output.encode("utf-8"))})
        result = orch.complete_code_delivery(tid, record)
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("sealed marker", (result.get("error_info") or "").lower())
        self.assertIn("test-item-0001", result.get("error_info") or "")
        self._assert_needs_attention(orch, tid)

    def test_nonzero_exit_goes_uncertain(self):
        orch, tid, delivery, review = self._waiting()
        record = self._host_record(tid, delivery, review, tests={"exit_code": 1})
        result = orch.complete_code_delivery(tid, record)
        self.assertEqual(result["status"], "needs_attention")
        self._assert_needs_attention(orch, tid)

    def test_declared_uncertainty_goes_uncertain(self):
        orch, tid, delivery, review = self._waiting()
        record = self._host_record(tid, delivery, review, uncertain=True)
        result = orch.complete_code_delivery(tid, record)
        self.assertEqual(result["status"], "needs_attention")
        self._assert_needs_attention(orch, tid)

    def test_unfinished_item_refused(self):
        orch, tid, delivery, review = self._waiting()
        record = self._host_record(tid, delivery, review, tests={"status": "in_progress"})
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, record)
        self._assert_still_waiting(orch, tid)

    def test_command_missing_markers_goes_uncertain(self):
        orch, tid, delivery, review = self._waiting()
        record = self._host_record(
            tid, delivery, review, tests={"command": "pwsh.exe -Command \"echo hi\""})
        result = orch.complete_code_delivery(tid, record)
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("command", (result.get("error_info") or "").lower())
        self._assert_needs_attention(orch, tid)

    def test_command_targeting_expected_goes_uncertain(self):
        orch, tid, delivery, review = self._waiting()
        command = (f"pwsh.exe -Command \"& python {CHECK_REL} "
                   f"--target {self.ws}/expected/widget.py\"")
        record = self._host_record(tid, delivery, review, tests={"command": command})
        result = orch.complete_code_delivery(tid, record)
        self.assertEqual(result["status"], "needs_attention")
        self._assert_needs_attention(orch, tid)


class TestHostRecordBindingGates(_Case):
    def _waiting(self):
        orch = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery, review = self._assemble_waiting(orch, payload)
        (self.ws / TARGET).write_bytes(_reference_bytes())
        return orch, tid, delivery, review

    def test_artifact_claim_drift_refused(self):
        orch, tid, delivery, review = self._waiting()
        record = self._host_record(tid, delivery, review, artifact={"version": 2})
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, record)
        self._assert_still_waiting(orch, tid)

    def test_review_claim_drift_refused(self):
        orch, tid, delivery, review = self._waiting()
        record = self._host_record(tid, delivery, review,
                                   review={"review_id": "rev_other0001"})
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, record)
        self._assert_still_waiting(orch, tid)

    def test_workdir_mismatch_refused(self):
        orch, tid, delivery, review = self._waiting()
        record = self._host_record(tid, delivery, review, workdir=str(self.case))
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, record)
        self._assert_still_waiting(orch, tid)

    def test_fixture_recorder_on_host_shape_refused(self):
        orch, tid, delivery, review = self._waiting()
        record = self._host_record(tid, delivery, review,
                                   recorder=OFFLINE_FIXTURE_RECORDER)
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, record)
        self._assert_still_waiting(orch, tid)

    def test_unknown_recorder_refused(self):
        orch, tid, delivery, review = self._waiting()
        record = self._host_record(tid, delivery, review, recorder="self-reported/v9")
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, record)
        self._assert_still_waiting(orch, tid)

    def test_oversized_output_refused(self):
        orch, tid, delivery, review = self._waiting()
        output = "PASS\n" + "x" * MAX_OUTPUT_CHARS
        record = self._host_record(
            tid, delivery, review, output=output,
            tests={"output_digest": _sha(output.encode("utf-8"))})
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, record)
        self._assert_still_waiting(orch, tid)

    def test_missing_key_refused(self):
        orch, tid, delivery, review = self._waiting()
        record = self._host_record(tid, delivery, review)
        del record["thread_id"]
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, record)
        self._assert_still_waiting(orch, tid)

    def test_cross_task_record_refused(self):
        orch = self._orch()
        payload_a = self._payload()
        _, delivery_a, review_a = self._assemble_waiting(orch, payload_a)
        payload_b = self._payload()
        _, delivery_b, _ = self._assemble_waiting(orch, payload_b)
        (self.ws / TARGET).write_bytes(_reference_bytes())
        record_a = self._host_record(payload_a["task_id"], delivery_a, review_a)
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(payload_b["task_id"], record_a)
        self._assert_still_waiting(orch, payload_a["task_id"])
        self._assert_still_waiting(orch, payload_b["task_id"])

    def test_check_script_path_drift_refused(self):
        orch, tid, delivery, review = self._waiting()
        record = self._host_record(tid, delivery, review,
                                   tests={"check_script": "other/check.py"})
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, record)
        self._assert_still_waiting(orch, tid)


class TestHostRecordByteGates(_Case):
    def _waiting(self):
        orch = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery, review = self._assemble_waiting(orch, payload)
        return orch, tid, delivery, review

    def test_unapplied_baseline_refused(self):
        orch, tid, delivery, review = self._waiting()
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, self._host_record(tid, delivery, review))
        self._assert_still_waiting(orch, tid)

    def test_extra_bytes_go_uncertain(self):
        orch, tid, delivery, review = self._waiting()
        (self.ws / TARGET).write_bytes(_reference_bytes() + b"\n# extra\n")
        result = orch.complete_code_delivery(tid, self._host_record(tid, delivery, review))
        self.assertEqual(result["status"], "needs_attention")
        self._assert_needs_attention(orch, tid)

    def test_unauthorized_scope_change_goes_uncertain(self):
        orch, tid, delivery, review = self._waiting()
        (self.ws / TARGET).write_bytes(_reference_bytes())
        (self.ws / "intruder.txt").write_text("out of scope", encoding="utf-8")
        result = orch.complete_code_delivery(tid, self._host_record(tid, delivery, review))
        self.assertEqual(result["status"], "needs_attention")
        self._assert_needs_attention(orch, tid)

    def test_target_attestation_mismatch_goes_uncertain(self):
        orch, tid, delivery, review = self._waiting()
        (self.ws / TARGET).write_bytes(_reference_bytes())
        record = self._host_record(
            tid, delivery, review, target={"sha256": _sha(_baseline_bytes())})
        result = orch.complete_code_delivery(tid, record)
        self.assertEqual(result["status"], "needs_attention")
        self._assert_needs_attention(orch, tid)

    def test_check_script_hash_mismatch_goes_uncertain(self):
        orch, tid, delivery, review = self._waiting()
        (self.ws / TARGET).write_bytes(_reference_bytes())
        record = self._host_record(
            tid, delivery, review, tests={"check_script_sha256": _sha(b"forged-check")})
        result = orch.complete_code_delivery(tid, record)
        self.assertEqual(result["status"], "needs_attention")
        self._assert_needs_attention(orch, tid)


class TestHostRecordLifecycle(_Case):
    def test_duplicate_complete_refused(self):
        orch = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery, review = self._assemble_waiting(orch, payload)
        (self.ws / TARGET).write_bytes(_reference_bytes())
        result = orch.complete_code_delivery(tid, self._host_record(tid, delivery, review))
        self.assertEqual(result["status"], "succeeded")
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, self._host_record(tid, delivery, review))
        self.assertEqual(orch.storage.get_task(tid)["status"], "succeeded")

    def test_late_complete_after_cancel_refused(self):
        orch = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery, review = self._assemble_waiting(orch, payload)
        orch.cancel_task(tid, reason="jh006 bridge late-complete probe")
        (self.ws / TARGET).write_bytes(_reference_bytes())
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, self._host_record(tid, delivery, review))
        self.assertEqual(orch.storage.get_task(tid)["status"], "cancelled")

    def test_owner_held_complete_refused_then_released_succeeds(self):
        orch = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery, review = self._assemble_waiting(orch, payload)
        (self.ws / TARGET).write_bytes(_reference_bytes())
        record = self._host_record(tid, delivery, review)
        self.assertTrue(orch.storage.claim_task(tid, "other-owner"))
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, record)
        orch.storage.release_task(tid, "other-owner")
        result = orch.complete_code_delivery(tid, record)
        self.assertEqual(result["status"], "succeeded")

    def test_deadline_exhausted_complete_fails(self):
        now = [1700000000.0]
        storage = TaskStorage(":memory:")
        self.addCleanup(storage.close)
        orch = Orchestrator(storage=storage, workspace_root=self.ws,
                            tool_executor=MockToolExecutor(),
                            clock=lambda: now[0])
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery, review = self._assemble_waiting(orch, payload)
        now[0] += 3600.0
        (self.ws / TARGET).write_bytes(_reference_bytes())
        result = orch.complete_code_delivery(tid, self._host_record(tid, delivery, review))
        self.assertEqual(result["status"], "failed")
        self.assertIn("timeout", (result.get("error_info") or "").lower())


class TestHostRecordCli(_Case):
    def test_cli_production_prepare_complete_roundtrip(self):
        orch = self._orch(use_file_db=True)
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery, review = self._assemble_waiting(orch, payload)
        orch.storage.close()
        code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                              "prepare-code-delivery", tid, "1"])
        body = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(body["task_result"]["delivery_scope"]["delivery_state"], "waiting_host")
        self.assertEqual(body["task_result"]["delivery_scope"]["evidence_kind"], "host_transcript")
        (self.ws / TARGET).write_bytes(_reference_bytes())
        record_file = self.case / "host_record.json"
        record_file.write_text(json.dumps(self._host_record(tid, delivery, review)),
                               encoding="utf-8")
        code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                              "complete-code-delivery", tid, str(record_file)])
        body = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(body["task_result"]["status"], "succeeded")
        scope = body["task_result"]["delivery_scope"]
        self.assertEqual(scope["delivery_state"], "applied")
        self.assertTrue(scope["target_applied"])
        self.assertEqual(scope["evidence_kind"], "host_transcript")
        probe = TaskStorage(str(self.db), read_only=True)
        try:
            kinds = [e.get("event_type") for e in probe.list_events(tid)]
        finally:
            probe.close()
        self.assertIn("host_record_accepted", kinds)

    def test_cli_complete_rejects_fixture_receipt_for_production(self):
        orch = self._orch(use_file_db=True)
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery, _ = self._assemble_waiting(orch, payload)
        orch.storage.close()
        (self.ws / TARGET).write_bytes(_reference_bytes())
        receipt_file = self.case / "bad_receipt.json"
        receipt_file.write_text(json.dumps({"task_id": tid,
                                            "delivery_id": delivery["delivery_id"]}),
                                encoding="utf-8")
        code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                              "complete-code-delivery", tid, str(receipt_file)])
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertFalse(body["command_ok"])
        probe = TaskStorage(str(self.db), read_only=True)
        try:
            task = probe.get_task(tid)
        finally:
            probe.close()
        self.assertEqual(task["status"], "reviewing")


class TestHostRecordHelpers(_Case):
    def test_sealed_check_expectation(self):
        expectation = sealed_check_expectation(TPL)
        self.assertEqual(expectation["expect_exit"], 0)
        self.assertEqual(expectation["expect_stdout_contains"], "PASS")

    def test_transcript_command_markers(self):
        genuine = (f"pwsh.exe -Command \"& python {CHECK_REL} "
                   f"--target D:/isolated-ws/{TARGET}\"")
        self.assertTrue(transcript_command_markers_ok(
            genuine, check_script_rel=CHECK_REL, authorized_target=TARGET))
        for bad in ("", "pwsh.exe -Command \"echo hi\"",
                    f"python {CHECK_REL} --target other/widget.py",
                    f"python other/check.py --target {TARGET}",
                    f"python {CHECK_REL} --target expected/widget.py"):
            with self.subTest(bad=bad[:40]):
                self.assertFalse(transcript_command_markers_ok(
                    bad, check_script_rel=CHECK_REL, authorized_target=TARGET))


if __name__ == "__main__":
    unittest.main()
