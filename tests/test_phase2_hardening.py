"""Executable OPT-01..30 specifications; fixtures never invoke real providers/tools."""

import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from src.adapters import AdapterError, OpenAICompatibleAdapter, GeminiAdapter
from src.control_guards import call_limits
from src.mock_components import MockToolExecutor, ScriptedReviewerAdapter, ScriptedWorkerAdapter, SimulatedInterruptionError, mock_acceptance
from src.orchestrator import Orchestrator
from src.responses_adapter import OpenAIResponsesAdapter
from src.router_policy import request_fingerprint
from src.storage import StaleAttemptError, TaskStorage
from test_phase2_control_layer import make_task_payload, make_tool_request


class AlteredReviewer(ScriptedReviewerAdapter):
    def __init__(self, alter):
        super().__init__()
        self.alter = alter

    def invoke_structured(self, *args, **kwargs):
        response = super().invoke_structured(*args, **kwargs)
        self.alter(response.parsed_json)
        return response


class TestPhase2Hardening(unittest.TestCase):
    def make(self, *, task=None, **kwargs):
        kwargs.setdefault("worker_adapter", ScriptedWorkerAdapter())
        kwargs.setdefault("reviewer_adapter", ScriptedReviewerAdapter())
        kwargs.setdefault("acceptance_evaluator", mock_acceptance)
        orch = Orchestrator(**kwargs)
        self.addCleanup(orch.storage.close)
        task = task or make_task_payload()
        orch.submit_task(task)
        return orch, task["task_id"]

    def issue(self, **kwargs):
        issue = {"issue_id": "iss_01", "severity": "p1_critical", "category": "correctness",
                 "description": "Defect", "evidence": "fixture line", "recommendation": "Repair",
                 "file_path": "docs/architecture-roadmap-v1.1.md", "line_start": 1, "line_end": 1}
        issue.update(kwargs)
        return issue

    def test_opt_01_complete_success(self):
        orch, tid = self.make()
        self.assertEqual(orch.run_task(tid)["status"], "succeeded")
        self.assertEqual(orch._reviewer_adapter.call_count, 1)
        self.assertEqual(orch.storage._conn.execute("SELECT COUNT(*) FROM review_bindings").fetchone()[0], 1)

    def test_opt_02_failed_acceptance(self):
        def failed(task, tools, artifact_store=None):
            result = mock_acceptance(task, tools)
            result[0]["passed"] = False
            return result
        orch, tid = self.make(acceptance_evaluator=failed)
        result = orch.run_task(tid)
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("acceptance condition failed", result["error_info"])

    def test_opt_03_revision_is_bounded(self):
        orch, tid = self.make(reviewer_adapter=ScriptedReviewerAdapter(mode="always_revise"))
        result = orch.run_task(tid)
        self.assertEqual(result["status"], "failed")
        states = [e["to_state"] for e in orch.list_task_events(tid) if e["event_type"] == "state_transition"]
        self.assertEqual(states.count("repairing"), 2)
        self.assertEqual(orch._reviewer_adapter.call_count, 3)

    def test_opt_04_wrong_task_review(self):
        orch, tid = self.make(reviewer_adapter=AlteredReviewer(lambda r: r.update(task_id="task_other12345")))
        result = orch.run_task(tid)
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("task_id", result["error_info"])
        self.assertEqual(orch.storage._conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 0)

    def test_opt_05_old_artifact_hash(self):
        executor = MockToolExecutor()
        def change(_):
            executor.artifacts["docs/architecture-roadmap-v1.1.md"] = "changed after dispatch"
        orch, tid = self.make(tool_executor=executor, reviewer_adapter=AlteredReviewer(change))
        result = orch.run_task(tid)
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("SHA-256", result["error_info"])

    def test_opt_06_late_attempt_does_not_overwrite(self):
        orch, tid = self.make()
        orch.storage.transition_task_state(tid, "classified")
        orch.storage.transition_task_state(tid, "planning")
        old = orch.storage.begin_attempt(tid, "worker", {})
        current = orch.storage.begin_attempt(tid, "worker", {})
        orch.storage.complete_attempt(tid, current, {"current": True}, 42)
        before = orch.storage.get_task(tid)
        with self.assertRaises(StaleAttemptError):
            orch.storage.complete_attempt(tid, old, {"old": True}, 999)
        self.assertEqual(orch.storage.get_task(tid), before)
        self.assertEqual(orch.storage.get_control(tid)["active_attempt_id"], current)

    def test_opt_07_contradictory_pass(self):
        orch, tid = self.make(reviewer_adapter=AlteredReviewer(lambda r: r.update(issues=[self.issue()])))
        result = orch.run_task(tid)
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("contradicts", result["error_info"])

    def test_opt_08_invalid_evidence(self):
        for issue in (self.issue(line_start=20, line_end=10), self.issue(file_path="missing.py"), self.issue(line_end=999)):
            with self.subTest(issue=issue):
                orch, tid = self.make(reviewer_adapter=AlteredReviewer(lambda r: r.update(verdict="revise", issues=[issue])))
                self.assertEqual(orch.run_task(tid)["status"], "needs_attention")
                self.assertEqual(orch.storage._conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 0)

    def test_opt_09_planning_is_not_permission(self):
        orch, tid = self.make(task=make_task_payload(complexity="high", risk_level="read_only"))
        self.assertEqual(orch.run_task(tid)["status"], "succeeded")
        classified = next(e for e in orch.list_task_events(tid) if e["to_state"] == "classified")
        self.assertEqual(classified["details"]["planning_depth"], "deep")
        self.assertEqual(orch.tool_executor.executed_requests[0]["tool_name"], "read_file")
        forbidden = make_tool_request(tid, tool_name="delete_file", parameters={"path": "C:/Windows/System32"})
        self.assertEqual(orch.policy_engine.evaluate_tool_request(forbidden).action, "deny_and_fail")

    def run_path(self, path, *, tool_name="write_file"):
        task = make_task_payload(risk_level="read_only")
        request = make_tool_request(task["task_id"], tool_name=tool_name, parameters={"path": path})
        orch, tid = self.make(task=task, worker_adapter=ScriptedWorkerAdapter(tool_requests_by_turn=[[request]]))
        result = orch.run_task(tid)
        self.assertEqual(len(orch.tool_executor.executed_requests), 0)
        return orch, tid, result

    def test_opt_10_declared_read_only_delete(self):
        _, _, result = self.run_path("C:/Windows/System32", tool_name="delete_file")
        self.assertEqual(result["status"], "failed")

    def test_opt_11_model_approval_flag_ignored(self):
        _, _, result = self.run_path("D:/another-project/notes.md")
        self.assertEqual(result["status"], "awaiting_approval")

    def test_opt_12_sibling_prefix(self):
        _, _, result = self.run_path("D:/JH-Agent OS-archive/notes.md")
        self.assertEqual(result["status"], "awaiting_approval")

    def test_opt_13_normalized_escape(self):
        _, _, result = self.run_path("D:/JH-Agent OS/../other-project/notes.md")
        self.assertEqual(result["status"], "awaiting_approval")

    @unittest.skip("Phase 4: real junction resolution and filesystem race verification")
    def test_opt_14_real_junction(self):
        pass

    def test_opt_15_changed_approved_action(self):
        orch, tid, _ = self.run_path("D:/another-project/notes.md")
        request = orch._worker_adapter.tool_requests_by_turn[0][0]
        request_id = request["request_id"]
        request["parameters"]["path"] = "D:/another-project/other.md"
        result = orch.approve_task_request(tid, request_id)
        self.assertEqual(result["status"], "awaiting_approval")
        self.assertEqual(len(orch.tool_executor.executed_requests), 0)
        self.assertEqual(orch.get_task_status(tid)["pending_approval_request"]["parameters"]["path"], request["parameters"]["path"])

    def test_opt_16_terminal_tool_request(self):
        for status in ("succeeded", "failed", "cancelled"):
            orch, tid = self.make()
            if status == "succeeded":
                orch.run_task(tid)
            else:
                orch.storage.transition_task_state(tid, status)
            before = len(orch.tool_executor.executed_requests)
            self.assertFalse(orch.storage.record_tool_start(make_tool_request(tid), has_side_effect=True))
            self.assertEqual(orch.run_task(tid)["status"], status)
            self.assertEqual(len(orch.tool_executor.executed_requests), before)
            self.assertEqual(orch.list_task_events(tid)[-1]["event_type"], "late_tool_request_rejected")

    def test_opt_17_remaining_timeout_reaches_transport(self):
        now = [100.0]
        orch, tid = self.make(clock=lambda: now[0], task=make_task_payload(max_execution_time_seconds=30))
        now[0] = 120.0
        self.assertEqual(orch.run_task(tid)["status"], "succeeded")
        for adapter in (orch._worker_adapter, orch._reviewer_adapter):
            self.assertEqual(adapter.calls[0]["timeout"], 10.0)
        adapter = OpenAIResponsesAdapter({"role": "reviewer", "model_id": "fixture", "endpoint_url": "http://localhost/v1", "api_key_env": "FIXTURE_KEY"})
        payload = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}
        with patch.dict(os.environ, {"FIXTURE_KEY": "fixture"}), patch.object(adapter._opener, "open", return_value=io.BytesIO(json.dumps(payload).encode())) as opened:
            adapter.invoke([], timeout=10)
        self.assertEqual(opened.call_args.kwargs["timeout"], 10)

    def test_opt_18_zero_time_no_call(self):
        now = [100.0]
        orch, tid = self.make(clock=lambda: now[0], task=make_task_payload(max_execution_time_seconds=30))
        now[0] = 130.0
        self.assertEqual(orch.run_task(tid)["status"], "failed")
        self.assertEqual(orch._worker_adapter.call_count, 0)

    def test_opt_19_input_exhausts_tokens(self):
        orch, tid = self.make(task=make_task_payload(max_tokens=1000))
        self.assertEqual(orch.run_task(tid)["status"], "failed")
        self.assertEqual(orch._worker_adapter.call_count, 0)
        self.assertIn("input reservation", orch.storage.get_task(tid)["error_info"])

    def test_opt_20_output_cap(self):
        worker = ScriptedWorkerAdapter()
        worker.max_output_tokens = 131072
        orch, tid = self.make(worker_adapter=worker, task=make_task_payload(max_tokens=20000))
        with patch("src.orchestrator.input_reserve", return_value=644):
            self.assertEqual(orch.run_task(tid)["status"], "succeeded")
        self.assertEqual(worker.calls[0]["max_output_tokens"], 19356)
        self.assertLessEqual(orch._reviewer_adapter.calls[0]["max_output_tokens"], 20000 - 120 - 644)

    def test_opt_21_unknown_usage_stops(self):
        orch, tid = self.make(worker_adapter=ScriptedWorkerAdapter(tokens_per_call=None))
        self.assertEqual(orch.run_task(tid)["status"], "needs_attention")
        status = orch.get_task_status(tid)
        self.assertIsNone(status["tokens_used"])
        self.assertEqual(status["tokens_unknown_count"], 1)
        self.assertEqual(orch._reviewer_adapter.call_count, 0)
        self.assertEqual(len(orch.tool_executor.executed_requests), 0)

    def test_opt_22_repair_keeps_original_deadline(self):
        now = [100.0]
        reviewer = ScriptedReviewerAdapter(mode="revise_then_pass")
        original = reviewer.invoke_structured
        def delayed(*args, **kwargs):
            response = original(*args, **kwargs)
            now[0] += 20
            return response
        reviewer.invoke_structured = delayed
        orch, tid = self.make(clock=lambda: now[0], reviewer_adapter=reviewer, task=make_task_payload(max_execution_time_seconds=60))
        self.assertEqual(orch.run_task(tid)["status"], "succeeded")
        self.assertEqual([c["timeout"] for c in reviewer.calls], [60, 40])
        self.assertEqual(orch.get_task_status(tid)["turns_used"], 3)
        self.assertEqual(orch.storage.get_control(tid)["deadline"], 160)

    def test_opt_23_response_before_commit_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recovery.db"
            storage = TaskStorage(path)
            orch, tid = self.make(storage=storage)
            with patch.object(storage, "complete_attempt", side_effect=SimulatedInterruptionError("crash before commit")):
                with self.assertRaises(SimulatedInterruptionError):
                    orch.run_task(tid)
            storage.close()
            restarted = TaskStorage(path)
            try:
                recovery = restarted.recover_interrupted_tasks()
                self.assertEqual(recovery[0]["recovered_state"], "needs_attention")
                self.assertFalse(recovery[0]["auto_replay_allowed"])
                self.assertEqual(restarted._conn.execute("SELECT status FROM model_attempts").fetchone()[0], "interrupted_unconfirmed")
                self.assertEqual(restarted.get_task(tid)["_runtime"]["turns_used"], 0)
            finally:
                restarted.close()

    def test_opt_24_transaction_rolls_back_both(self):
        orch, tid = self.make()
        before = orch.list_task_events(tid)
        with patch.object(orch.storage, "record_event", side_effect=sqlite3.OperationalError("injected event failure")):
            with self.assertRaises(sqlite3.OperationalError):
                orch.storage.transition_task_state(tid, "classified")
        self.assertEqual(orch.storage.get_task(tid)["status"], "created")
        self.assertEqual(orch.list_task_events(tid), before)
        # Reverse write order: an earlier event must roll back if the task update fails.
        orch.storage._conn.execute("CREATE TEMP TRIGGER fail_update BEFORE UPDATE ON tasks BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            with orch.storage.transaction():
                orch.storage.record_event(task_id=tid, event_type="test", from_state="created", to_state="classified", actor_role="test")
                orch.storage.transition_task_state(tid, "classified")
        self.assertEqual(orch.list_task_events(tid), before)

    def test_opt_25_duplicate_completion_not_charged(self):
        orch, tid = self.make()
        attempt = orch.storage.begin_attempt(tid, "worker", {})
        self.assertTrue(orch.storage.complete_attempt(tid, attempt, {"result": True}, 123))
        self.assertFalse(orch.storage.complete_attempt(tid, attempt, {"result": True}, 123))
        self.assertEqual(orch.get_task_status(tid)["tokens_used"], 123)
        self.assertEqual(orch.get_task_status(tid)["turns_used"], 1)
        self.assertEqual(len([e for e in orch.list_task_events(tid) if e["event_type"] == "usage_recorded"]), 1)

    def test_opt_26_duplicate_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claims.db"
            a, b = TaskStorage(path), TaskStorage(path)
            self.addCleanup(a.close)
            self.addCleanup(b.close)
            task = make_task_payload()
            a.create_task(task)
            barrier = threading.Barrier(2)
            results, errors = [], []
            def claim(storage, owner):
                try:
                    barrier.wait(timeout=5)
                    results.append(storage.claim_task(task["task_id"], owner))
                except Exception as exc:
                    errors.append(exc)
            threads = [threading.Thread(target=claim, args=(s, o)) for s, o in ((a, "owner-a"), (b, "owner-b"))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
            a.close()
            b.close()
            self.assertFalse(errors)
            self.assertEqual(sorted(results), [False, True])

    def test_opt_27_pid_is_not_ownership(self):
        task = make_task_payload()
        task["metadata"]["pid"] = os.getpid()  # Deliberately a live, unrelated process.
        orch, tid = self.make(task=task)
        with patch("os.kill", side_effect=AssertionError("Must not kill a recorded PID")) as kill:
            orch.recover_interrupted_tasks()
        self.assertEqual(orch.storage.get_task(tid)["status"], "created")
        kill.assert_not_called()

    def test_opt_28_terminal_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "terminal.db"
            storage = TaskStorage(path)
            for status in ("succeeded", "failed", "cancelled"):
                orch, tid = self.make(storage=storage)
                if status == "succeeded":
                    orch.run_task(tid)
                else:
                    storage.transition_task_state(tid, status)
            before = list(storage._conn.execute("SELECT task_id, status FROM tasks ORDER BY task_id"))
            before = [tuple(r) for r in before]
            storage.close()
            restarted = TaskStorage(path)
            try:
                self.assertEqual(restarted.recover_interrupted_tasks(), [])
                self.assertEqual([tuple(r) for r in restarted._conn.execute("SELECT task_id, status FROM tasks ORDER BY task_id")], before)
            finally:
                restarted.close()

    def test_opt_29_reviewer_unavailable(self):
        for mode in ("timeout", "unavailable"):
            orch, tid = self.make(reviewer_adapter=ScriptedReviewerAdapter(mode=mode))
            self.assertEqual(orch.run_task(tid)["status"], "needs_attention")
            self.assertEqual(orch._reviewer_adapter.call_count, 1)

    def test_opt_30_invalid_provider_output(self):
        for mode in ("invalid_schema",):
            orch, tid = self.make(reviewer_adapter=ScriptedReviewerAdapter(mode=mode))
            self.assertEqual(orch.run_task(tid)["status"], "needs_attention")
            self.assertEqual(orch.storage._conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 0)

    def test_missing_evaluator_never_succeeds(self):
        orch, tid = self.make(acceptance_evaluator=None)
        result = orch.run_task(tid)
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("acceptance evidence is missing", result["error_info"])

    def test_unconfirmed_side_effect_blocks_success(self):
        orch, tid = self.make()
        def mark_uncertain(_):
            with orch.storage.transaction():
                orch.storage._conn.execute("UPDATE tasks SET has_unconfirmed_side_effect = 1 WHERE task_id = ?", (tid,))
        orch._reviewer_adapter = AlteredReviewer(mark_uncertain)
        result = orch.run_task(tid)
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("side effect", result["error_info"])

    def test_invalid_usage_cannot_increase_budget(self):
        for value in (-1, True, "100"):
            worker = ScriptedWorkerAdapter()
            original = worker.invoke_structured
            def invalid(*args, **kwargs):
                response = original(*args, **kwargs)
                response.total_tokens = value
                return response
            worker.invoke_structured = invalid
            orch, tid = self.make(worker_adapter=worker)
            self.assertEqual(orch.run_task(tid)["status"], "failed")
            self.assertIsNone(orch.get_task_status(tid)["tokens_used"])
            self.assertEqual(orch._reviewer_adapter.call_count, 0)

    def test_approval_and_event_commit_together(self):
        orch, tid, _ = self.run_path("D:/another-project/notes.md")
        pending = orch.get_task_status(tid)["pending_approval_request"]
        with patch.object(orch.storage, "record_event", side_effect=sqlite3.OperationalError("event write failed")):
            with self.assertRaises(sqlite3.OperationalError):
                orch.approve_task_request(tid, pending["request_id"])
        control = orch.storage.get_control(tid)
        self.assertEqual(json.loads(control["pending_json"]), pending)
        self.assertEqual(json.loads(control["approvals_json"]), {})
        self.assertEqual(len(orch.tool_executor.executed_requests), 0)

    def test_unknown_mixed_usage_remains_null(self):
        orch, tid = self.make()
        for tokens in (50, None, 20):
            orch.storage.record_usage(tid, actor_role="worker", tokens_used=tokens)
        self.assertIsNone(orch.get_task_status(tid)["tokens_used"])

    def test_restart_does_not_reset_deadline_or_approval(self):
        orch, tid, _ = self.run_path("D:/another-project/notes.md")
        replacement = Orchestrator(storage=orch.storage, worker_adapter=orch._worker_adapter,
                                   reviewer_adapter=ScriptedReviewerAdapter(), acceptance_evaluator=mock_acceptance)
        self.assertEqual(replacement.run_task(tid)["status"], "awaiting_approval")
        pending = replacement.get_task_status(tid)["pending_approval_request"]
        self.assertEqual(replacement.approve_task_request(tid, pending["request_id"])["status"], "succeeded")
        self.assertLessEqual(replacement.get_remaining_budget_seconds(tid), 300)

    def test_timeout_after_final_review_cannot_succeed(self):
        now = [0.0]
        orch, tid = self.make(clock=lambda: now[0], reviewer_adapter=AlteredReviewer(lambda _: now.__setitem__(0, 301)))
        self.assertEqual(orch.run_task(tid)["status"], "failed")

    def test_transport_limits_for_other_http_adapters(self):
        config = {"role": "worker", "model_id": "fixture", "endpoint_url": "http://localhost/v1", "api_key_env": "FIXTURE_KEY"}
        for adapter in (OpenAICompatibleAdapter(config), GeminiAdapter(config)):
            with patch.dict(os.environ, {"FIXTURE_KEY": "fixture"}), patch("urllib.request.urlopen", return_value=io.BytesIO(b"{}")) as opened:
                adapter.invoke([], timeout=7, max_output_tokens=321)
            self.assertEqual(opened.call_args.kwargs["timeout"], 7)
            body = json.loads(opened.call_args.args[0].data)
            limit = body.get("max_tokens") or body["generationConfig"]["maxOutputTokens"]
            self.assertEqual(limit, 321)

    def test_invalid_review_nonce_rejected(self):
        orch, tid = self.make(reviewer_adapter=AlteredReviewer(lambda r: r.update(review_id="rev_previous12345")))
        result = orch.run_task(tid)
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("review_id", result["error_info"])

    def test_unknown_tool_and_empty_write_scope_not_allowed(self):
        orch, tid = self.make()
        for name, params, scope in (("arbitrary_shell", {"command": "unknown"}, None),
                                    ("apply_patch", {"patch": "unknown target"}, None),
                                    ("write_file", {"path": "docs/notes.md"}, set())):
            decision = orch.policy_engine.evaluate_tool_request(make_tool_request(tid, tool_name=name, parameters=params), authorized_files=scope)
            self.assertEqual(decision.action, "hold_for_approval")

    def test_cancelled_during_tool_or_review(self):
        orch, tid = self.make()
        original = orch.tool_executor.execute
        def cancel_tool(request):
            orch.cancel_task(tid)
            return original(request)
        orch.tool_executor.execute = cancel_tool
        self.assertEqual(orch.run_task(tid)["status"], "cancelled")
        self.assertEqual(orch._reviewer_adapter.call_count, 0)
        other, other_tid = self.make()
        other._reviewer_adapter = AlteredReviewer(lambda _: other.cancel_task(other_tid))
        self.assertEqual(other.run_task(other_tid)["status"], "cancelled")

    def test_crash_cannot_be_retried_without_reconciliation(self):
        orch, tid = self.make()
        with patch.object(orch.storage, "complete_attempt", side_effect=SimulatedInterruptionError("crash")):
            with self.assertRaises(SimulatedInterruptionError):
                orch.run_task(tid)
        self.assertEqual(orch.run_task(tid)["status"], "needs_attention")
        self.assertEqual(orch._worker_adapter.call_count, 1)

    def test_two_schedulers_do_not_execute_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.db"
            first_storage, second_storage = TaskStorage(path), TaskStorage(path)
            first, tid = self.make(storage=first_storage)
            second = Orchestrator(storage=second_storage, worker_adapter=ScriptedWorkerAdapter(),
                                  reviewer_adapter=ScriptedReviewerAdapter(), acceptance_evaluator=mock_acceptance)
            entered, release = threading.Event(), threading.Event()
            original = first._worker_adapter.invoke_structured
            def blocked(*args, **kwargs):
                entered.set()
                if not release.wait(timeout=5):
                    raise AssertionError("Concurrent test did not release provider")
                return original(*args, **kwargs)
            first._worker_adapter.invoke_structured = blocked
            outcomes, errors = [], []
            def run():
                try:
                    outcomes.append(first.run_task(tid)["status"])
                except Exception as exc:
                    errors.append(exc)
            thread = threading.Thread(target=run)
            thread.start()
            try:
                self.assertTrue(entered.wait(timeout=5))
                self.assertEqual(second.run_task(tid)["status"], "planning")
                self.assertEqual(second._worker_adapter.call_count, 0)
                self.assertEqual(len(second.tool_executor.executed_requests), 0)
            finally:
                release.set()
                thread.join(timeout=5)
                first_storage.close()
                second_storage.close()
            self.assertFalse(errors)
            self.assertEqual(outcomes, ["succeeded"])
            self.assertEqual(len(first.tool_executor.executed_requests), 1)


if __name__ == "__main__":
    unittest.main()
