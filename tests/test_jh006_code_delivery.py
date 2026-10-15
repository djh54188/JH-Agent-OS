"""JH-006 offline two-step code delivery regression tests.

Covers the fixed code_sortfix_v1 template only: no-tool patch generation,
independent review, prepare/waiting/complete delivery, and the offline
fixture host. Every synthetic apply/receipt is marked fixture
(fixture-offline-host/v1) and runs in an isolated workspace; nothing here
is production or real evidence. Assertions target external state (task
status, adapter call counts, database records, workspace bytes), never
just implementation return values.
"""
import hashlib
import io
import json
import shutil
import subprocess
import sys
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.acceptance import code_deferred_acceptance_evaluator
from src.cli import RECORDS_TRANSFORM_CRITERIA, build_task_summary, main as cli_main
from src.code_delivery import (
    CODE_FIXTURE_ID,
    CODE_TEMPLATE_ID,
    CODE_TEMPLATES,
    OFFLINE_FIXTURE_RECORDER,
    CodeDeliveryRefused,
    materialized_post_apply_command,
    parse_fixture_patch,
    resolve_target_path,
    sealed_fixture_bytes,
    validate_apply_receipt,
)
from src.mock_components import MockToolExecutor, ScriptedReviewerAdapter, ScriptedWorkerAdapter
from src.orchestrator import Orchestrator
from src.storage import TaskStorage

TPL = CODE_TEMPLATES[CODE_FIXTURE_ID]
FIXTURE_DIR = ROOT / TPL["fixture_dir"]
CRITERIA = list(TPL["criteria"])
TARGET = TPL["authorized_target"]
CREATED_AT = "2026-10-15T00:00:00Z"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _baseline_bytes() -> bytes:
    return (FIXTURE_DIR / TPL["baseline_rel"]).read_bytes()


def _reference_bytes() -> bytes:
    return (FIXTURE_DIR / TPL["reference_rel"]).read_bytes()


def _check_bytes() -> bytes:
    return (FIXTURE_DIR / TPL["check_rel"]).read_bytes()


def _patch_envelope(content_bytes: bytes, path: str = TARGET) -> str:
    return json.dumps({"path": path, "content": content_bytes.decode("utf-8")}, ensure_ascii=False)


def _run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli_main(argv)
    return code, buf.getvalue()


class _Case(unittest.TestCase):
    def setUp(self):
        self.case = ROOT / "data" / "test-workspaces" / ("jh006-" + uuid.uuid4().hex[:8])
        self.ws = self.case / "ws"
        (self.ws / "target").mkdir(parents=True)
        (self.ws / TARGET).write_bytes(_baseline_bytes())
        self.addCleanup(shutil.rmtree, self.case, ignore_errors=True)
        self.db = self.case / "jh006.sqlite3"
        self._seq = 0

    def _task_id(self):
        self._seq += 1
        return f"task_jh006case{self._seq:04d}"

    def _payload(self, task_id=None, **over):
        payload = {
            "schema_version": "1.0.0",
            "task_id": task_id or self._task_id(),
            "title": "jh006 offline code delivery probe",
            "description": "jh006 offline code delivery probe",
            "acceptance_criteria": list(CRITERIA),
            "priority": "high",
            "status": "created",
            "risk_assessment": {"risk_level": "workspace_write", "complexity": "low",
                                "uncertainty": "low", "risk_basis": "jh006 fixture",
                                "routing_basis": "jh006"},
            "budget": {"max_turns": 8, "max_tokens": 60000, "max_execution_time_seconds": 600},
            "context": {"workspace_root": str(self.ws), "target_files": [TARGET]},
            "artifact_refs": [],
            "tool_execution_evidence": [],
            "review_verdict": None,
            "error_info": None,
            "assigned_worker": "fixture_worker",
            "assigned_reviewer": "fixture_reviewer",
            "metadata": {"fixture_id": CODE_FIXTURE_ID, "review_target_artifact_type": "patch",
                         "_trusted": {"source": "fixture", "template_id": CODE_TEMPLATE_ID,
                                      "execution_mode": "generate"}},
            "created_at": CREATED_AT,
            "updated_at": CREATED_AT,
        }
        for key, val in over.items():
            if isinstance(val, dict) and isinstance(payload.get(key), dict):
                payload[key] = {**payload[key], **val}
            else:
                payload[key] = val
        return payload

    def _orch(self, *, use_file_db=False, worker=None, reviewer=None, clock=None):
        storage = TaskStorage(str(self.db) if use_file_db else ":memory:")
        self.addCleanup(storage.close)
        if worker is None:
            worker = ScriptedWorkerAdapter(
                tool_requests_by_turn=[[]],
                artifact_content_by_turn=[_patch_envelope(_reference_bytes())],
                artifact_type="patch_text")
        if reviewer is None:
            reviewer = ScriptedReviewerAdapter(mode="pass")
        orch = Orchestrator(
            storage=storage, workspace_root=self.ws,
            worker_adapter=worker, reviewer_adapter=reviewer,
            tool_executor=MockToolExecutor(),
            acceptance_evaluator=code_deferred_acceptance_evaluator(),
            clock=clock)
        return orch, worker, reviewer

    def _run_to_waiting(self, orch, payload):
        task = orch.submit_task(payload)
        self.assertEqual(task["status"], "created")
        result = orch.run_task(payload["task_id"])
        self.assertEqual(result["status"], "reviewing")
        delivery = (result.get("metadata") or {}).get("_delivery") or {}
        self.assertEqual(delivery.get("state"), "waiting_host")
        return result, delivery

    def _fixture_apply(self, content_bytes=None):
        (self.ws / TARGET).write_bytes(
            content_bytes if content_bytes is not None else _reference_bytes())

    def _receipt(self, task_id, delivery, **over):
        actual = (self.ws / TARGET).read_bytes()
        receipt = {
            "task_id": task_id,
            "delivery_id": delivery["delivery_id"],
            "target": {"path": TARGET, "sha256": _sha(actual)},
            "baseline_seen": _sha(_baseline_bytes()),
            "tests": {"target_path": TARGET, "target_sha256": _sha(actual),
                      "check_script": f"{TPL['fixture_dir']}/{TPL['check_rel']}",
                      "check_script_sha256": _sha(_check_bytes()),
                      "command": materialized_post_apply_command(TPL, str(self.ws)),
                      "exit_code": 0,
                      "output_digest": _sha(b"fixture-offline-host: PASS JH-006")},
            "uncertain": False,
            "recorder": OFFLINE_FIXTURE_RECORDER,
            "recorded_at": CREATED_AT,
        }
        for key, val in over.items():
            if isinstance(val, dict) and isinstance(receipt.get(key), dict):
                receipt[key] = {**receipt[key], **val}
            else:
                receipt[key] = val
        return receipt

    def _assemble_reviewing(self, orch, payload, *, stamp_source="fixture",
                            artifact_content=None, artifact_type="patch_text",
                            review_verdict="pass", with_source_event=None):
        """Manually assemble a reviewing task with one completed review (no model calls)."""
        payload["metadata"]["_trusted"] = {"source": stamp_source, "template_id": CODE_TEMPLATE_ID,
                                           "execution_mode": "generate"}
        orch.submit_task(payload)
        tid = payload["task_id"]
        tag = f"{self._seq:04d}{uuid.uuid4().hex[:4]}"
        envelope = {"schema_version": "1.0.0", "artifact_id": f"art_manual{tag}",
                    "task_id": tid, "artifact_type": artifact_type,
                    "content": artifact_content or _patch_envelope(_reference_bytes()),
                    "summary": "jh006 manual assembly"}
        orch.artifact_store.save_version(tid, envelope=envelope, version=1,
                                         generation_attempt_id=None)
        latest = orch.artifact_store.get_latest(tid)
        for state in ("classified", "planning", "executing", "reviewing"):
            orch.storage.transition_task_state(tid, state, actor_role="orchestrator",
                                               reason="jh006 manual assembly")
        review = {"schema_version": "1.0.0", "review_id": f"rev_manual{tag}",
                  "task_id": tid, "target_artifact_type": "patch",
                  "target_artifact_path": f"mem://{tid}/{envelope['artifact_id']}",
                  "verdict": review_verdict, "overall_score": 95.0, "issues": [],
                  "summary": "jh006 manual pass", "reviewer_role": "fixture_reviewer",
                  "created_at": CREATED_AT}
        aid = orch.storage.begin_attempt(tid, "reviewer", {"artifact_hashes": {"manual": latest["sha256"]}})
        orch.storage.complete_attempt(tid, aid, review, 100)
        if with_source_event is not None:
            orch.storage.record_event(task_id=tid, event_type="model_response_source",
                                      from_state="reviewing", to_state="reviewing",
                                      actor_role="orchestrator",
                                      details={"attempt_id": aid, "role": "reviewer",
                                               "adapter_class": "FixtureAdapter",
                                               "model_id": "fixture",
                                               "is_mock": with_source_event})
        return orch.storage.get_task(tid), review, aid

    def _assert_needs_attention_for(self, orch, tid, reason):
        task = orch.storage.get_task(tid)
        self.assertEqual(task["status"], "needs_attention")
        reasons = [e["details"].get("reason") for e in orch.storage.list_events(tid)]
        self.assertIn(reason, reasons)

    def _cli_subprocess(self, *args):
        proc = subprocess.run(
            [sys.executable, "-m", "src.cli", "--db", str(self.db),
             "--workspace", str(self.ws), "--test-fixture-mode", *args],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=180)
        return proc.returncode, proc.stdout, proc.stderr


class TestPrepareWaiting(_Case):
    def test_run_parks_waiting_not_succeeded(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        result, delivery = self._run_to_waiting(orch, payload)
        self.assertNotEqual(result["status"], "succeeded")
        latest = orch.artifact_store.get_latest(tid)
        self.assertEqual(latest["version"], 1)
        self.assertEqual(delivery["delivery_id"], f"dlv_{tid}_v1")
        self.assertEqual(delivery["artifact"]["version"], 1)
        self.assertEqual(delivery["artifact"]["sha256"], latest["sha256"])
        self.assertEqual(delivery["baseline"]["sha256_before"], _sha(_baseline_bytes()))
        self.assertEqual(delivery["expected_after"]["sha256"], _sha(_reference_bytes()))
        self.assertEqual(delivery["authorized_files"], [TARGET])
        self.assertIsNone(delivery["apply_receipt"])
        events = [e["event_type"] for e in orch.storage.list_events(tid)]
        self.assertIn("code_delivery_prepared", events)
        scope = build_task_summary(orch.storage, tid)["task_result"]["delivery_scope"]
        self.assertTrue(scope["generated_artifact"])
        self.assertEqual(scope["delivery_state"], "waiting_host")
        self.assertFalse(scope["target_applied"])

    def test_resume_in_waiting_makes_zero_new_calls(self):
        orch, worker, reviewer = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        self._run_to_waiting(orch, payload)
        worker_calls, reviewer_calls = worker.call_count, reviewer.call_count
        turns = orch.storage.get_task(tid)["_runtime"]["turns_used"]
        attempts = len(orch.storage.list_model_attempts(tid))
        events = len(orch.storage.list_events(tid))
        parked = orch.run_task(tid)
        self.assertEqual(parked["status"], "reviewing")
        self.assertEqual(parked["metadata"]["_delivery"]["state"], "waiting_host")
        self.assertEqual((worker.call_count, reviewer.call_count), (worker_calls, reviewer_calls))
        self.assertEqual(orch.storage.get_task(tid)["_runtime"]["turns_used"], turns)
        self.assertEqual(len(orch.storage.list_model_attempts(tid)), attempts)
        self.assertEqual(len(orch.storage.list_events(tid)), events)

    def test_prepare_idempotent_same_version(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery = self._run_to_waiting(orch, payload)
        again = orch.prepare_code_delivery(tid, 1)
        self.assertEqual(again["metadata"]["_delivery"]["delivery_id"], delivery["delivery_id"])

    def test_prepare_rejects_unknown_patch_shapes(self):
        for content in ("not json at all",
                        json.dumps({"files": [{"path": TARGET}]}),
                        json.dumps({"path": TARGET, "content": ""})):
            with self.subTest(content=content[:24]):
                worker = ScriptedWorkerAdapter(
                    tool_requests_by_turn=[[]], artifact_content_by_turn=[content],
                    artifact_type="patch_text")
                orch, _, _ = self._orch(worker=worker)
                payload = self._payload()
                orch.submit_task(payload)
                result = orch.run_task(payload["task_id"])
                self.assertEqual(result["status"], "needs_attention")
                self._assert_needs_attention_for(orch, payload["task_id"],
                                                 "code_delivery_prepare_refused")

    def test_prepare_rejects_patch_target_mismatch(self):
        worker = ScriptedWorkerAdapter(
            tool_requests_by_turn=[[]],
            artifact_content_by_turn=[_patch_envelope(_reference_bytes(), path="target/other.py")],
            artifact_type="patch_text")
        orch, _, _ = self._orch(worker=worker)
        payload = self._payload()
        orch.submit_task(payload)
        result = orch.run_task(payload["task_id"])
        self.assertEqual(result["status"], "needs_attention")
        self._assert_needs_attention_for(orch, payload["task_id"], "code_delivery_prepare_refused")

    def test_prepare_rejects_unsealed_patch_bytes(self):
        worker = ScriptedWorkerAdapter(
            tool_requests_by_turn=[[]],
            artifact_content_by_turn=[_patch_envelope(_baseline_bytes())],
            artifact_type="patch_text")
        orch, _, _ = self._orch(worker=worker)
        payload = self._payload()
        orch.submit_task(payload)
        result = orch.run_task(payload["task_id"])
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("sealed reference", (result.get("error_info") or "").lower())

    def test_prepare_rejects_baseline_drift(self):
        (self.ws / TARGET).write_bytes(b"# tampered baseline\n")
        orch, _, _ = self._orch()
        payload = self._payload()
        orch.submit_task(payload)
        result = orch.run_task(payload["task_id"])
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("sealed baseline", (result.get("error_info") or "").lower())

    def test_prepare_rejects_unknown_source(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        payload["metadata"]["_trusted"]["source"] = "self-reported"
        orch.submit_task(payload)
        result = orch.run_task(payload["task_id"])
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("unknown task source", (result.get("error_info") or "").lower())

    def test_production_prepare_refuses_missing_and_mock_sources(self):
        for with_source_event in (None, True):
            with self.subTest(with_source_event=with_source_event):
                orch, _, _ = self._orch()
                payload = self._payload()
                self._assemble_reviewing(orch, payload, stamp_source="production",
                                         with_source_event=with_source_event)
                with self.assertRaises(CodeDeliveryRefused):
                    orch.prepare_code_delivery(payload["task_id"], 1)
                task = orch.storage.get_task(payload["task_id"])
                self.assertEqual(task["status"], "reviewing")
                self.assertNotIn("_delivery", task.get("metadata") or {})

    def test_prepare_rejects_non_patch_text_artifact(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        self._assemble_reviewing(orch, payload, artifact_type="text_transformation")
        with self.assertRaises(CodeDeliveryRefused):
            orch.prepare_code_delivery(payload["task_id"], 1)
        self.assertNotIn("_delivery", orch.storage.get_task(payload["task_id"]).get("metadata"))

    def test_no_pass_review_never_parks(self):
        orch, _, _ = self._orch(reviewer=ScriptedReviewerAdapter(mode="reject"))
        payload = self._payload()
        orch.submit_task(payload)
        result = orch.run_task(payload["task_id"])
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("_delivery", (result.get("metadata") or {}))
        with self.assertRaises(CodeDeliveryRefused):
            orch.prepare_code_delivery(payload["task_id"], 1)

    def test_cli_submit_code_fixture_runs_to_needs_attention_never_success(self):
        payload = self._payload()
        task_file = self.case / "code_task.json"
        task_file.write_text(json.dumps(payload), encoding="utf-8")
        code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                              "--test-fixture-mode", "submit", str(task_file), "--run"])
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(body["task_result"]["status"], "needs_attention")
        storage = TaskStorage(str(self.db), read_only=True)
        try:
            reasons = [e["details"].get("reason") for e in storage.list_events(payload["task_id"])]
        finally:
            storage.close()
        self.assertIn("code_delivery_prepare_refused", reasons)


class TestCompleteHappyPath(_Case):
    def test_legal_complete_succeeds_with_verified_apply(self):
        orch, worker, reviewer = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery = self._run_to_waiting(orch, payload)
        worker_calls, reviewer_calls = worker.call_count, reviewer.call_count
        turns = orch.storage.get_task(tid)["_runtime"]["turns_used"]
        self._fixture_apply()
        result = orch.complete_code_delivery(tid, self._receipt(tid, delivery))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["review_verdict"], "pass")
        done = result["metadata"]["_delivery"]
        self.assertEqual(done["state"], "applied")
        self.assertEqual(done["apply_receipt"]["delivery_id"], delivery["delivery_id"])
        self.assertIsNotNone(done["completed_at"])
        self.assertEqual((self.ws / TARGET).read_bytes(), _reference_bytes())
        bindings = orch.storage.list_review_bindings(tid)
        self.assertEqual(len(bindings), 1)
        self.assertEqual(len(bindings[0]["acceptance"]), 3)
        for item in bindings[0]["acceptance"]:
            self.assertTrue(item["passed"])
            self.assertTrue(item["evidence"])
        evidence = result["tool_execution_evidence"]
        self.assertIn(f"delivery:{delivery['delivery_id']}:post-apply-checks:exit0", evidence)
        self.assertIn(f"delivery:{delivery['delivery_id']}:scope-ok", evidence)
        self.assertGreaterEqual(len(orch.storage.list_memory_records(tid)), 1)
        self.assertEqual((worker.call_count, reviewer.call_count), (worker_calls, reviewer_calls))
        self.assertEqual(orch.storage.get_task(tid)["_runtime"]["turns_used"], turns)
        scope = build_task_summary(orch.storage, tid)["task_result"]["delivery_scope"]
        self.assertEqual(scope["delivery_state"], "applied")
        self.assertTrue(scope["target_applied"])

    def test_complete_is_cross_process(self):
        orch1, _, _ = self._orch(use_file_db=True)
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery = self._run_to_waiting(orch1, payload)
        orch1.storage.close()
        probe = TaskStorage(str(self.db), read_only=True)
        try:
            seen = probe.get_task(tid)
        finally:
            probe.close()
        self.assertEqual(seen["status"], "reviewing")
        self.assertEqual(seen["metadata"]["_delivery"]["state"], "waiting_host")
        orch2, _, _ = self._orch(use_file_db=True)
        parked = orch2.prepare_code_delivery(tid, 1)
        self.assertEqual(parked["metadata"]["_delivery"]["delivery_id"], delivery["delivery_id"])
        orch2.storage.close()
        self._fixture_apply()
        orch3, _, _ = self._orch(use_file_db=True)
        result = orch3.complete_code_delivery(tid, self._receipt(tid, delivery))
        self.assertEqual(result["status"], "succeeded")
        orch3.storage.close()
        final = TaskStorage(str(self.db), read_only=True)
        try:
            task = final.get_task(tid)
        finally:
            final.close()
        self.assertEqual(task["status"], "succeeded")
        self.assertEqual(task["metadata"]["_delivery"]["state"], "applied")


class TestCompleteRefusals(_Case):
    def _waiting(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        _, delivery = self._run_to_waiting(orch, payload)
        return orch, payload["task_id"], delivery

    def _assert_still_waiting(self, orch, tid):
        task = orch.storage.get_task(tid)
        self.assertEqual(task["status"], "reviewing")
        self.assertEqual(task["metadata"]["_delivery"]["state"], "waiting_host")

    def test_extra_bytes_refused_even_when_checks_pass(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply(_reference_bytes() + b"\n# fixture-extra: behavior unchanged\n")
        proc = subprocess.run(
            [sys.executable, str(FIXTURE_DIR / "check_widget.py"),
             "--target", str(self.ws / TARGET)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("PASS", proc.stdout)
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, self._receipt(tid, delivery))
        self._assert_still_waiting(orch, tid)

    def test_unapplied_baseline_refused(self):
        orch, tid, delivery = self._waiting()
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, self._receipt(tid, delivery))
        self._assert_still_waiting(orch, tid)

    def test_receipt_target_path_mismatch_refused(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        receipt = self._receipt(tid, delivery, target={"path": "target/evil.py"})
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, receipt)
        self._assert_still_waiting(orch, tid)

    def test_receipt_forged_check_script_goes_uncertain(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        receipt = self._receipt(tid, delivery,
                                tests={"check_script_sha256": _sha(b"forged-check")})
        result = orch.complete_code_delivery(tid, receipt)
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("sealed check", (result.get("error_info") or "").lower())
        self._assert_needs_attention_for(orch, tid, "code_delivery_uncertain")

    def test_receipt_exit_nonzero_goes_uncertain(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        result = orch.complete_code_delivery(tid, self._receipt(tid, delivery, tests={"exit_code": 1}))
        self.assertEqual(result["status"], "needs_attention")
        self._assert_needs_attention_for(orch, tid, "code_delivery_uncertain")

    def test_receipt_self_declared_uncertain_goes_uncertain(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        result = orch.complete_code_delivery(tid, self._receipt(tid, delivery, uncertain=True))
        self.assertEqual(result["status"], "needs_attention")
        self._assert_needs_attention_for(orch, tid, "code_delivery_uncertain")

    def test_receipt_forged_recorder_refused(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        receipt = self._receipt(tid, delivery, recorder="prod-host/v9")
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, receipt)
        self._assert_still_waiting(orch, tid)

    def test_receipt_command_mismatch_refused(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        forged_command = ["python", str(FIXTURE_DIR / "check_widget.py"),
                          "--target", str(FIXTURE_DIR / "expected" / "widget.py")]
        receipt = self._receipt(tid, delivery, tests={"command": forged_command})
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, receipt)
        self._assert_still_waiting(orch, tid)

    def test_new_artifact_version_after_prepare_refused(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        orch.artifact_store.save_version(
            tid,
            envelope={"schema_version": "1.0.0", "artifact_id": f"art_v2{uuid.uuid4().hex[:8]}",
                      "task_id": tid, "artifact_type": "patch_text",
                      "content": _patch_envelope(_baseline_bytes()),
                      "summary": "second version after prepare"},
            version=2, generation_attempt_id=None)
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, self._receipt(tid, delivery))
        self._assert_still_waiting(orch, tid)

    def test_prepare_old_version_after_new_refused(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        self._assemble_reviewing(orch, payload)
        orch.artifact_store.save_version(
            payload["task_id"],
            envelope={"schema_version": "1.0.0", "artifact_id": f"art_v2{uuid.uuid4().hex[:8]}",
                      "task_id": payload["task_id"], "artifact_type": "patch_text",
                      "content": _patch_envelope(_reference_bytes()),
                      "summary": "second version before prepare"},
            version=2, generation_attempt_id=None)
        with self.assertRaises(CodeDeliveryRefused):
            orch.prepare_code_delivery(payload["task_id"], 1)

    def test_review_drift_after_prepare_refused(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        aid2 = orch.storage.begin_attempt(tid, "reviewer", {"note": "second review"})
        orch.storage.complete_attempt(
            tid, aid2, {"review_id": f"rev_drift{uuid.uuid4().hex[:8]}",
                       "verdict": "pass", "issues": []}, 50)
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, self._receipt(tid, delivery))
        self._assert_still_waiting(orch, tid)

    def test_cross_task_receipt_refused(self):
        orch, _, _ = self._orch()
        payload_a = self._payload()
        _, delivery_a = self._run_to_waiting(orch, payload_a)
        payload_b = self._payload()
        _, delivery_b = self._run_to_waiting(orch, payload_b)
        self._fixture_apply()
        receipt_a = self._receipt(payload_a["task_id"], delivery_a)
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(payload_b["task_id"], receipt_a)
        self._assert_still_waiting(orch, payload_a["task_id"])
        self._assert_still_waiting(orch, payload_b["task_id"])

    def test_duplicate_complete_refused(self):
        orch, tid, delivery = self._waiting()
        self._fixture_apply()
        result = orch.complete_code_delivery(tid, self._receipt(tid, delivery))
        self.assertEqual(result["status"], "succeeded")
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, self._receipt(tid, delivery))
        self.assertEqual(orch.storage.get_task(tid)["status"], "succeeded")

    def test_late_complete_after_cancel_refused(self):
        orch, tid, delivery = self._waiting()
        orch.cancel_task(tid, reason="jh006 late-complete probe")
        self.assertEqual(orch.storage.get_task(tid)["status"], "cancelled")
        self._fixture_apply()
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, self._receipt(tid, delivery))
        self.assertEqual(orch.storage.get_task(tid)["status"], "cancelled")

    def test_production_waiting_complete_refused_offline(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        self._assemble_reviewing(orch, payload, stamp_source="production",
                                 with_source_event=False)
        prepared = orch.prepare_code_delivery(tid, 1)
        self.assertEqual(prepared["metadata"]["_delivery"]["state"], "waiting_host")
        self._fixture_apply()
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, self._receipt(tid, prepared["metadata"]["_delivery"]))
        self._assert_still_waiting(orch, tid)


class TestCompleteGates(_Case):
    def test_owner_held_complete_refused_then_released_succeeds(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery = self._run_to_waiting(orch, payload)
        self._fixture_apply()
        receipt = self._receipt(tid, delivery)
        self.assertTrue(orch.storage.claim_task(tid, "other-owner"))
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, receipt)
        orch.storage.release_task(tid, "other-owner")
        result = orch.complete_code_delivery(tid, receipt)
        self.assertEqual(result["status"], "succeeded")

    def test_inflight_model_attempt_blocks_complete(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery = self._run_to_waiting(orch, payload)
        orch.storage.begin_attempt(tid, "worker", {"note": "unconfirmed probe"})
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, self._receipt(tid, delivery))
        task = orch.storage.get_task(tid)
        self.assertEqual(task["status"], "reviewing")
        self.assertEqual(task["metadata"]["_delivery"]["state"], "waiting_host")

    def test_unknown_token_usage_sends_complete_to_needs_attention(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery = self._run_to_waiting(orch, payload)
        self._fixture_apply()
        orch.storage.record_usage(tid, actor_role="worker", tokens_used=None)
        result = orch.complete_code_delivery(tid, self._receipt(tid, delivery))
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("unknown", (result.get("error_info") or "").lower())

    def test_pending_approval_blocks_complete(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery = self._run_to_waiting(orch, payload)
        orch.storage.hold_request(tid, {"request_id": "treq_probe0001"})
        with self.assertRaises(CodeDeliveryRefused):
            orch.complete_code_delivery(tid, self._receipt(tid, delivery))
        task = orch.storage.get_task(tid)
        self.assertEqual(task["status"], "reviewing")

    def test_deadline_exhausted_complete_fails(self):
        now = [1700000000.0]
        orch, _, _ = self._orch(clock=lambda: now[0])
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery = self._run_to_waiting(orch, payload)
        now[0] += 3600.0
        parked = orch.run_task(tid)
        self.assertEqual(parked["status"], "reviewing")
        self._fixture_apply()
        result = orch.complete_code_delivery(tid, self._receipt(tid, delivery))
        self.assertEqual(result["status"], "failed")
        self.assertIn("timeout", (result.get("error_info") or "").lower())


class TestCancelAndQuery(_Case):
    def test_cancel_in_waiting_and_query(self):
        orch, _, _ = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery = self._run_to_waiting(orch, payload)
        cancelled = orch.cancel_task(tid, reason="jh006 cancel probe")
        self.assertEqual(cancelled["status"], "cancelled")
        task = orch.storage.get_task(tid)
        self.assertEqual(task["metadata"]["_delivery"]["delivery_id"], delivery["delivery_id"])
        kinds = [(e["event_type"], e.get("to_state")) for e in orch.storage.list_events(tid)]
        self.assertIn(("state_transition", "cancelled"), kinds)

    def test_cli_resume_waiting_returns_waiting_with_zero_new_attempts(self):
        orch, _, _ = self._orch(use_file_db=True)
        payload = self._payload()
        tid = payload["task_id"]
        self._run_to_waiting(orch, payload)
        orch.storage.close()
        probe = TaskStorage(str(self.db), read_only=True)
        try:
            attempts_before = len(probe.list_model_attempts(tid))
            turns_before = probe.get_task(tid)["_runtime"]["turns_used"]
        finally:
            probe.close()
        code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                              "--test-fixture-mode", "resume", tid])
        body = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(body["task_result"]["status"], "reviewing")
        self.assertEqual(body["task_result"]["delivery_scope"]["delivery_state"], "waiting_host")
        probe = TaskStorage(str(self.db), read_only=True)
        try:
            self.assertEqual(len(probe.list_model_attempts(tid)), attempts_before)
            self.assertEqual(probe.get_task(tid)["_runtime"]["turns_used"], turns_before)
        finally:
            probe.close()


class TestCliDeliveryCommands(_Case):
    def _usage(self, tid):
        probe = TaskStorage(str(self.db), read_only=True)
        try:
            return (len(probe.list_model_attempts(tid)),
                    probe.get_task(tid)["_runtime"]["turns_used"])
        finally:
            probe.close()

    def test_cli_prepare_and_complete_roundtrip(self):
        orch, _, _ = self._orch(use_file_db=True)
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery = self._run_to_waiting(orch, payload)
        orch.storage.close()
        usage_before = self._usage(tid)
        code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                              "--test-fixture-mode", "prepare-code-delivery", tid, "1"])
        body = json.loads(out)
        self.assertEqual(code, 0)
        scope = body["task_result"]["delivery_scope"]
        self.assertEqual(scope["delivery_state"], "waiting_host")
        self.assertEqual(scope["delivery_id"], delivery["delivery_id"])
        self.assertFalse(scope["target_applied"])
        self.assertIn("waiting on host", scope["note"])
        self.assertEqual(self._usage(tid), usage_before)
        self._fixture_apply()
        receipt_file = self.case / "receipt.json"
        receipt_file.write_text(json.dumps(self._receipt(tid, delivery)), encoding="utf-8")
        code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                              "--test-fixture-mode", "complete-code-delivery", tid,
                              str(receipt_file)])
        body = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(body["task_result"]["status"], "succeeded")
        scope = body["task_result"]["delivery_scope"]
        self.assertEqual(scope["delivery_state"], "applied")
        self.assertTrue(scope["target_applied"])
        self.assertEqual(self._usage(tid), usage_before)

    def test_cli_prepare_complete_subprocess_roundtrip(self):
        orch, _, _ = self._orch(use_file_db=True)
        payload = self._payload()
        tid = payload["task_id"]
        _, delivery = self._run_to_waiting(orch, payload)
        orch.storage.close()
        code, out, _ = self._cli_subprocess("prepare-code-delivery", tid, "1")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["task_result"]["delivery_scope"]["delivery_state"],
                         "waiting_host")
        self._fixture_apply()
        receipt_file = self.case / "receipt.json"
        receipt_file.write_text(json.dumps(self._receipt(tid, delivery)), encoding="utf-8")
        code, out, _ = self._cli_subprocess("complete-code-delivery", tid, str(receipt_file))
        self.assertEqual(code, 0)
        body = json.loads(out)
        self.assertEqual(body["task_result"]["status"], "succeeded")
        self.assertTrue(body["task_result"]["delivery_scope"]["target_applied"])

    def test_cli_complete_rejects_bad_receipt_shape(self):
        orch, _, _ = self._orch(use_file_db=True)
        payload = self._payload()
        tid = payload["task_id"]
        self._run_to_waiting(orch, payload)
        orch.storage.close()
        receipt_file = self.case / "bad_receipt.json"
        receipt_file.write_text(json.dumps({"task_id": tid}), encoding="utf-8")
        code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                              "--test-fixture-mode", "complete-code-delivery", tid,
                              str(receipt_file)])
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertFalse(body["command_ok"])
        probe = TaskStorage(str(self.db), read_only=True)
        try:
            task = probe.get_task(tid)
        finally:
            probe.close()
        self.assertEqual(task["status"], "reviewing")
        self.assertEqual(task["metadata"]["_delivery"]["state"], "waiting_host")

    def test_cli_summary_keeps_records_note(self):
        payload = self._payload()
        payload["acceptance_criteria"] = list(RECORDS_TRANSFORM_CRITERIA)
        payload["metadata"] = {"input_data": {"records": [{"region": "a", "sales": 1}]}}
        storage = TaskStorage(str(self.db))
        try:
            storage.create_task(payload)
        finally:
            storage.close()
        code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws), "summary",
                              payload["task_id"]])
        self.assertEqual(code, 0)
        scope = json.loads(out)["task_result"]["delivery_scope"]
        self.assertFalse(scope["generated_artifact"])
        self.assertFalse(scope["target_applied"])
        self.assertIsNone(scope["delivery_state"])
        self.assertIn("JH-004", scope["note"])

    def test_cli_prepare_unknown_task_fails_clean(self):
        code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                              "--test-fixture-mode", "prepare-code-delivery",
                              "task_missing0001", "1"])
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertFalse(body["command_ok"])
        self.assertFalse(self.db.exists())


class TestCodeDeliveryHelpers(_Case):
    def test_parse_patch_envelope_pins_single_authorized_file(self):
        good = parse_fixture_patch(_patch_envelope(_reference_bytes()), TARGET)
        self.assertEqual(good["content_bytes"], _reference_bytes())
        bad_inputs = (b"bytes-not-str", "not json",
                      json.dumps({"files": [{"path": TARGET}]}),
                      json.dumps({"path": TARGET}),
                      json.dumps({"path": TARGET, "content": ""}),
                      json.dumps({"path": "target/other.py", "content": "x"}),
                      json.dumps({"path": TARGET, "content": "x", "extra": 1}))
        for bad in bad_inputs:
            with self.subTest(bad=str(bad)[:32]):
                with self.assertRaises(CodeDeliveryRefused):
                    parse_fixture_patch(bad, TARGET)

    def test_resolve_target_path_pins_workspace(self):
        resolved = resolve_target_path(self.ws, TARGET)
        self.assertTrue(resolved.endswith("widget.py"))
        for bad in ("/abs/path.py", "C:\\win.py", "../escape.py",
                    "target/../../escape.py", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(CodeDeliveryRefused):
                    resolve_target_path(self.ws, bad)

    def test_sealed_fixture_bytes_match_manifest(self):
        manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["sealed"])
        for rel_key in ("reference_rel", "baseline_rel", "check_rel"):
            data = sealed_fixture_bytes(TPL, rel_key)
            recorded = next(e["sha256"] for e in manifest["files"] if e["path"] == TPL[rel_key])
            self.assertEqual(_sha(data), recorded)

    def test_validate_receipt_requires_exact_shape_and_fixed_command(self):
        command = materialized_post_apply_command(TPL, str(self.ws))
        self.assertFalse(any("expected/" in token or "expected\\" in token for token in command))
        tid, dlv = "task_probe0001", "dlv_probe_v1"
        digest = _sha(b"probe")
        base = {"task_id": tid, "delivery_id": dlv,
                "target": {"path": TARGET, "sha256": digest},
                "baseline_seen": digest,
                "tests": {"target_path": TARGET, "target_sha256": digest,
                          "check_script": "check", "check_script_sha256": digest,
                          "command": command, "exit_code": 0, "output_digest": digest},
                "uncertain": False, "recorder": OFFLINE_FIXTURE_RECORDER,
                "recorded_at": CREATED_AT}
        validate_apply_receipt(dict(base), task_id=tid, delivery_id=dlv,
                               authorized_target=TARGET, expected_command=command)
        mutations = []
        dropped = dict(base)
        del dropped["uncertain"]
        mutations.append(dropped)
        for key, val in (("task_id", "task_other0001"), ("delivery_id", "dlv_other"),
                         ("uncertain", "yes"), ("exit_code", None),
                         ("recorder", "unknown-host"),
                         ("target", {"path": "target/evil.py", "sha256": digest}),
                         ("target", {"path": TARGET, "sha256": "not-hex"}),
                         ("tests", {**base["tests"], "command": ["python", "other.py"]}),
                         ("tests", {**base["tests"], "exit_code": True})):
            mutated = dict(base)
            mutated[key] = val
            mutations.append(mutated)
        for mutated in mutations:
            with self.subTest(mutated=str(mutated)[:64]):
                with self.assertRaises(CodeDeliveryRefused):
                    validate_apply_receipt(mutated, task_id=tid, delivery_id=dlv,
                                           authorized_target=TARGET, expected_command=command)


if __name__ == "__main__":
    unittest.main()
