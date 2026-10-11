"""JH-Agent OS Phase 3 offline tests: real dual-model loop contract (A/B/C/D).

All fixtures are offline; no provider is contacted. De-duplicated against the
Phase 2 OPT suite: lifecycle/policy/persistence cases already covered there are
referenced, not reimplemented. New coverage targets the artifact generation
contract, control-owned hashing/versions, repair context, memory records, and
persistence of the full loop.

Scenarios:
A. Normal flow: generation -> trusted acceptance -> independent review -> succeeded.
B. Error discovery and repair: known-wrong artifact (fixture injection recorded),
   reviewer issues with evidence, repair regenerates v2, re-review, succeeded.
C. Failure paths: timeout / invalid JSON / truncated / refused / schema mismatch /
   task correlation mismatch / repair exhaustion / unknown usage (fail-closed).
D. Persistence: restart-safe query of terminal state, artifact, evidence, memory
   records without re-triggering any model call.
"""

import json
import tempfile
from pathlib import Path
import unittest

from src.acceptance import transform_acceptance_evaluator
from src.artifacts import content_sha256
from src.mock_components import (
    MockToolExecutor,
    ScriptedReviewerAdapter,
    ScriptedWorkerAdapter,
)
from src.orchestrator import Orchestrator
from src.storage import TaskStorage
from test_phase2_control_layer import make_task_payload


def make_generation_task(
    task_id: str,
    *,
    input_data: dict | None = None,
    expected_output: dict | None = None,
    risk_level: str = "read_only",
) -> dict:
    task = make_task_payload(
        task_id,
        title="Transform input records",
        description="Deterministic no-tool data transformation; output artifact is JSON text.",
        risk_level=risk_level,
    )
    task["metadata"] = {"input_data": input_data or {}}
    return task


def correct_content(task_id: str) -> str:
    return json.dumps({"task": task_id, "result": "ok", "rows": [1, 2, 3]}, ensure_ascii=False)


def wrong_content(task_id: str, *, injected_marker: str = "INJECTED_DEFECT_ROW_999") -> str:
    # Fixture-injected defect (Scenario B): recorded, never attributed to the model.
    return json.dumps({"task": task_id, "result": "ok", "rows": [1, 2, 3], "extra": injected_marker},
                      ensure_ascii=False)


def make_orchestrator(self, *, worker=None, reviewer=None, evaluator=None, storage=None):
    orch = Orchestrator(
        storage=storage,
        worker_adapter=worker or ScriptedWorkerAdapter(),
        reviewer_adapter=reviewer or ScriptedReviewerAdapter(),
        acceptance_evaluator=evaluator or mock_pass_evaluator,
    )
    self.addCleanup(orch.storage.close)
    return orch


def mock_pass_evaluator(task, tool_executions, artifact_store=None):
    latest = artifact_store.get_latest(task["task_id"]) if artifact_store else None
    evidence = [f"artifact:{latest['artifact_id']}@v{latest['version']}:sha256={latest['sha256']}"] if latest else []
    return [{"criterion": c, "passed": bool(evidence), "evidence": evidence}
            for c in task["acceptance_criteria"]]


class TestPhase3Loop(unittest.TestCase):

    # ------------------------------------------------------------------
    # A. Normal flow
    # ------------------------------------------------------------------

    def test_a_generation_success_full_loop(self):
        tid = "task_p3_gen_ok001"
        worker = ScriptedWorkerAdapter(tool_requests_by_turn=[[]], artifact_content_by_turn=[correct_content(tid)])
        reviewer = ScriptedReviewerAdapter(mode="pass")
        orch = make_orchestrator(self, worker=worker, reviewer=reviewer)
        orch.submit_task(make_generation_task(tid))

        result = orch.run_task(tid)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["review_verdict"], "pass")

        # Artifact lineage: exactly one version, control-owned SHA-256
        versions = orch.storage.list_artifact_versions(tid)
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["version"], 1)
        self.assertEqual(versions[0]["sha256"], content_sha256(correct_content(tid)))
        self.assertIsNotNone(versions[0]["generation_attempt_id"])

        # Review binding ties review to attempt and artifact snapshot
        bindings = orch.storage.list_review_bindings(tid)
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]["review"]["verdict"], "pass")
        self.assertEqual(bindings[0]["artifacts"][list(bindings[0]["artifacts"])[0]]["sha256"],
                         versions[0]["sha256"])

        # Acceptance evidence is traceable through tool_execution_evidence
        self.assertTrue(any(e.startswith("artifact:") for e in result["tool_execution_evidence"]))

        # Memory: verified fact + unverified generalization
        memories = orch.storage.list_memory_records(tid)
        self.assertEqual([m["verification_status"] for m in memories], ["verified", "unverified"])

        # Planning now receives trusted context (Phase 3 gap #1)
        # (planning call is calls[0]; generation is calls[1])
        self.assertGreaterEqual(len(worker.calls), 2)

        # Artifact is memory-resident only; nothing was written to the filesystem
        ref = result["artifact_refs"][0]
        self.assertTrue(ref.startswith("mem://"))

    def test_a_transform_acceptance_evaluator_is_control_owned(self):
        tid = "task_p3_acc_ok002"
        input_data = {"records": [{"region": "b", "sales": 2}, {"region": "a", "sales": 1}]}
        expected = [{"region": "a", "sales": 1}, {"region": "b", "sales": 2}]
        worker = ScriptedWorkerAdapter(
            tool_requests_by_turn=[[]],
            artifact_content_by_turn=[json.dumps(expected, ensure_ascii=False)],
            artifact_type="data_conversion",
        )
        reviewer = ScriptedReviewerAdapter(mode="pass")
        orch = make_orchestrator(
            self, worker=worker, reviewer=reviewer,
            evaluator=transform_acceptance_evaluator(expected),
        )
        orch.submit_task(make_generation_task(tid, input_data=input_data))
        result = orch.run_task(tid)
        self.assertEqual(result["status"], "succeeded")

    def test_a_acceptance_rejects_wrong_output_even_if_review_passes(self):
        # Control acceptance does NOT trust worker or reviewer opinion.
        tid = "task_p3_acc_bad03"
        expected = [{"region": "a", "sales": 1}]
        worker = ScriptedWorkerAdapter(
            tool_requests_by_turn=[[]],
            artifact_content_by_turn=[json.dumps([{"region": "a", "sales": 2}], ensure_ascii=False)],
        )
        orch = make_orchestrator(
            self, worker=worker,
            evaluator=transform_acceptance_evaluator(expected),
        )
        orch.submit_task(make_generation_task(tid, input_data={"records": [{"region": "a", "sales": 2}]}))
        result = orch.run_task(tid)
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("acceptance condition failed", result["error_info"])

    # ------------------------------------------------------------------
    # B. Error discovery and repair (fixture injection, recorded)
    # ------------------------------------------------------------------

    def test_b_injected_error_repaired_and_re_reviewed(self):
        tid = "task_p3_fix_ok004"
        bad = wrong_content(tid)
        good = correct_content(tid)
        # Fixture injection is explicit: turn 0 carries the injected defect.
        worker = ScriptedWorkerAdapter(tool_requests_by_turn=[[]], artifact_content_by_turn=[bad, good])
        reviewer = ScriptedReviewerAdapter(mode="revise_then_pass")
        orch = make_orchestrator(self, worker=worker, reviewer=reviewer)
        orch.submit_task(make_generation_task(tid))

        result = orch.run_task(tid)
        self.assertEqual(result["status"], "succeeded")

        versions = orch.storage.list_artifact_versions(tid)
        self.assertEqual([v["version"] for v in versions], [1, 2])
        self.assertNotEqual(versions[0]["sha256"], versions[1]["sha256"])
        self.assertEqual(versions[0]["content"], bad)   # injected fixture preserved verbatim
        self.assertEqual(versions[1]["content"], good)

        # Two independent reviews; the second authorizes success (old review not reused)
        bindings = orch.storage.list_review_bindings(tid)
        self.assertEqual(len(bindings), 2)
        self.assertNotEqual(bindings[0]["review_id"], bindings[1]["review_id"])
        self.assertNotEqual(bindings[0]["attempt_id"], bindings[1]["attempt_id"])
        self.assertEqual(bindings[1]["review"]["verdict"], "pass")
        self.assertIn(bad[:50], bindings[0]["artifacts"][list(bindings[0]["artifacts"])[0]]["content"])

        # Repair context carried reviewer issues + current artifact + acceptance failures
        repair_ctx = worker.generation_contexts[1]
        self.assertEqual(repair_ctx["attempt_kind"], "repair")
        self.assertTrue(repair_ctx["reviewer_issues"])
        self.assertTrue(all(i.get("evidence") for i in repair_ctx["reviewer_issues"]))
        self.assertEqual(repair_ctx["current_artifact"]["version"], 1)
        self.assertEqual(repair_ctx["current_artifact"]["sha256"], versions[0]["sha256"])

        self.assertEqual(result["_runtime"]["repair_count"], 1)
        memories = orch.storage.list_memory_records(tid)
        self.assertEqual([m["verification_status"] for m in memories], ["verified", "unverified"])

    def test_b_repair_exhaustion_fails(self):
        tid = "task_p3_fix_ex005"
        bad = wrong_content(tid)
        worker = ScriptedWorkerAdapter(tool_requests_by_turn=[[]], artifact_content_by_turn=[bad])
        reviewer = ScriptedReviewerAdapter(mode="always_revise")
        orch = make_orchestrator(self, worker=worker, reviewer=reviewer)
        orch.submit_task(make_generation_task(tid))
        result = orch.run_task(tid)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["_runtime"]["repair_count"], 2)
        self.assertEqual([v["version"] for v in orch.storage.list_artifact_versions(tid)], [1, 2, 3])  # same content re-generated each repair

        # Failure memory keeps the evidence
        memories = orch.storage.list_memory_records(tid)
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0]["category"], "negative_result")
        self.assertEqual(memories[0]["verification_status"], "unverified")

    # ------------------------------------------------------------------
    # C. Failure paths (new call-chain gaps; lifecycle ones live in OPT suite)
    # ------------------------------------------------------------------

    def test_c_generation_faults_fail_closed(self):
        # Faults are injected at the GENERATION call only: planning must succeed
        # first so the new call-chain is exercised (planning faults are covered by
        # the Phase 2 OPT suite and test_04).
        from src.adapters import AdapterError
        for mode, reason in (("timeout", "worker_timeout"),
                             ("invalid_json", "worker_generation_failed"),
                             ("truncated", "worker_truncated"),
                             ("refused", "worker_refused")):
            with self.subTest(mode=mode):
                tid = f"task_p3_c_{mode[:6]}"
                worker = ScriptedWorkerAdapter(tool_requests_by_turn=[[]])
                original = worker.invoke_structured
                def gen_fault(*args, **kwargs):
                    if args[1].get("title") == "ArtifactSchemaV1":
                        worker.mode = mode
                        try:
                            return original(*args, **kwargs)
                        finally:
                            worker.mode = "normal"
                    return original(*args, **kwargs)
                worker.invoke_structured = gen_fault
                orch = make_orchestrator(self, worker=worker)
                orch.submit_task(make_generation_task(tid))
                result = orch.run_task(tid)
                self.assertEqual(result["status"], "failed")
                event_blob = json.dumps(orch.list_task_events(tid), ensure_ascii=False)
                self.assertIn(reason, result["error_info"] + event_blob)
                self.assertEqual(orch.storage.list_artifact_versions(tid), [])

    def test_c_artifact_task_correlation_mismatch(self):
        tid = "task_p3_corr_bad6"
        worker = ScriptedWorkerAdapter(tool_requests_by_turn=[[]], artifact_content_by_turn=[correct_content(tid)])

        original = worker.invoke_structured
        def wrong_task(*args, **kwargs):
            response = original(*args, **kwargs)
            # Scope the tampering to the artifact call: the planning call keeps the
            # true task_id so this test isolates the ARTIFACT correlation guard.
            if args[1].get("title") == "ArtifactSchemaV1":
                response.parsed_json["task_id"] = "task_other12345678"
            return response
        worker.invoke_structured = wrong_task

        orch = make_orchestrator(self, worker=worker)
        orch.submit_task(make_generation_task(tid))
        result = orch.run_task(tid)
        self.assertEqual(result["status"], "failed")
        self.assertIn("correlation", result["error_info"])
        self.assertEqual(orch.storage.list_artifact_versions(tid), [])

    def test_c_artifact_schema_mismatch(self):
        # Model answers the artifact request with a plan-shaped payload -> rejected.
        tid = "task_p3_schm_bad7"
        worker = ScriptedWorkerAdapter(tool_requests_by_turn=[[]], artifact_content_by_turn=[correct_content(tid)])
        original = worker.invoke_structured
        def plan_shaped(*args, **kwargs):
            if args[1].get("title") == "ArtifactSchemaV1":
                from src.adapters import SchemaValidationError
                raise SchemaValidationError("Worker output is not valid JSON or failed schema validation")
            return original(*args, **kwargs)
        worker.invoke_structured = plan_shaped

        orch = make_orchestrator(self, worker=worker)
        orch.submit_task(make_generation_task(tid))
        result = orch.run_task(tid)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(orch.storage.list_artifact_versions(tid), [])

    def test_c_unknown_usage_in_generation_stops_further_calls(self):
        tid = "task_p3_unkn_008"
        worker = ScriptedWorkerAdapter(
            tool_requests_by_turn=[[]], artifact_content_by_turn=[correct_content(tid)], tokens_per_call=None)
        orch = make_orchestrator(self, worker=worker)
        orch.submit_task(make_generation_task(tid))
        result = orch.run_task(tid)
        self.assertEqual(result["status"], "needs_attention")
        self.assertIsNone(orch.get_task_status(tid)["tokens_used"])
        self.assertEqual(orch._reviewer_adapter.call_count, 0)

    # ------------------------------------------------------------------
    # D. Persistence and restart-safe query
    # ------------------------------------------------------------------

    def test_d_persisted_loop_survives_restart_without_model_calls(self):
        tid = "task_p3_pers_009"
        worker = ScriptedWorkerAdapter(tool_requests_by_turn=[[]], artifact_content_by_turn=[correct_content(tid)])
        reviewer = ScriptedReviewerAdapter(mode="pass")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "phase3.db"
            storage = TaskStorage(path)
            orch = make_orchestrator(self, worker=worker, reviewer=reviewer, storage=storage)
            orch.submit_task(make_generation_task(tid))
            self.assertEqual(orch.run_task(tid)["status"], "succeeded")
            calls_before = worker.call_count + reviewer.call_count
            storage.close()

            reopened = TaskStorage(path)
            try:
                query = Orchestrator(storage=reopened, worker_adapter=ScriptedWorkerAdapter(),
                                     reviewer_adapter=ScriptedReviewerAdapter(),
                                     acceptance_evaluator=mock_pass_evaluator)
                status = query.get_task_status(tid)
                self.assertEqual(status["status"], "succeeded")

                latest = reopened.get_latest_artifact(tid)
                self.assertEqual(latest["sha256"], content_sha256(correct_content(tid)))
                self.assertIn(correct_content(tid), latest["content"])

                bindings = reopened.list_review_bindings(tid)
                self.assertEqual(len(bindings), 1)
                self.assertEqual(bindings[0]["acceptance"][0]["passed"], True)

                memories = reopened.list_memory_records(tid)
                self.assertEqual([m["verification_status"] for m in memories], ["verified", "unverified"])

                # Query path must not trigger any model call
                self.assertEqual(worker.call_count + reviewer.call_count, calls_before)
                self.assertEqual(reopened.recover_interrupted_tasks(), [])
            finally:
                reopened.close()

    def test_d_artifact_never_written_to_filesystem(self):
        tid = "task_p3_nofs_010"
        worker = ScriptedWorkerAdapter(tool_requests_by_turn=[[]], artifact_content_by_turn=[correct_content(tid)])
        orch = make_orchestrator(self, worker=worker)
        orch.submit_task(make_generation_task(tid))
        result = orch.run_task(tid)
        self.assertEqual(result["status"], "succeeded")
        # The only artifact reference is a memory path; no real file was produced.
        for ref in result["artifact_refs"]:
            self.assertTrue(ref.startswith("mem://"))
        versions = orch.storage.list_artifact_versions(tid)
        self.assertEqual(versions[0]["content"], correct_content(tid))


if __name__ == "__main__":
    unittest.main()
