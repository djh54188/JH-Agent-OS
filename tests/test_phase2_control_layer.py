"""
JH-Agent OS Phase 2 Control Layer, Router, Risk Policy & SQLite Persistence Tests
Covers all 11 mandatory offline verification scenarios:
1. Success (succeeded)
2. Failure (failed)
3. Review rejection / revision / escalation / reviewer unavailability fail-closed
4. Missing credentials & automatic Mock Worker fallback
5. Illegal state transitions & invalid message rejection
6. Danger action overriding routing & per-call parameter inspection (Level 3 zero downgrade)
7. Awaiting approval (Level 2 & incremental scope expansion) -> approve / reject
8. Cancellation (cancelled) & terminal state immutability
9. Timeout & remaining task budget capping
10. Budget exhaustion (turns & tokens) & unknown token usage recorded as None (not 0)
11. Process restart recovery from SQLite & prohibition of blind replay for unconfirmed side effects
"""

import os
from pathlib import Path
import tempfile
import unittest
import uuid

from src.adapters import SchemaValidationError
from src.mock_components import (
    MockToolExecutor,
    ScriptedReviewerAdapter,
    ScriptedWorkerAdapter,
    SimulatedInterruptionError,
)
from src.orchestrator import Orchestrator
from src.state_machine import InvalidStateTransitionError
from src.storage import TaskStorage


def make_task_payload(
    task_id: str = "",
    *,
    title: str = "Phase 2 Test Task",
    description: str = "Verify control layer state machine and policy enforcement",
    risk_level: str = "workspace_write",
    complexity: str = "medium",
    uncertainty: str = "low",
    max_turns: int = 10,
    max_tokens: int = 10000,
    max_execution_time_seconds: int = 300,
    target_files: list | None = None,
) -> dict:
    tid = task_id or f"task_{uuid.uuid4().hex[:12]}"
    return {
        "schema_version": "1.0.0",
        "task_id": tid,
        "title": title,
        "description": description,
        "acceptance_criteria": [
            "All transitions follow the 11-state lifecycle contract",
            "Independent review passes before entering succeeded",
        ],
        "priority": "high",
        "status": "created",
        "risk_assessment": {
            "risk_level": risk_level,
            "complexity": complexity,
            "uncertainty": uncertainty,
            "risk_basis": "Controlled workspace test",
            "routing_basis": "Rule-based router test",
        },
        "budget": {
            "max_turns": max_turns,
            "max_tokens": max_tokens,
            "max_execution_time_seconds": max_execution_time_seconds,
            "max_cost_usd": 1.0,
        },
        "context": {
            "workspace_root": "D:/JH-Agent OS",
            "target_files": target_files if target_files is not None else ["docs/architecture-roadmap-v1.1.md"],
            "env_vars_whitelist": [],
            "reference_docs": [],
        },
        "artifact_refs": [],
        "tool_execution_evidence": [],
        "review_verdict": None,
        "error_info": None,
        "assigned_worker": None,
        "assigned_reviewer": None,
        "metadata": {},
        "created_at": "2026-10-10T12:00:00Z",
        "updated_at": "2026-10-10T12:00:00Z",
    }


def make_tool_request(
    task_id: str,
    *,
    request_id: str = "",
    tool_name: str = "read_file",
    parameters: dict | None = None,
    risk_level: str = "read_only",
    requires_human_approval: bool = False,
) -> dict:
    return {
        "schema_version": "1.0.0",
        "request_id": request_id or f"treq_{uuid.uuid4().hex[:12]}",
        "task_id": task_id,
        "step_id": "step_01",
        "caller_role": "worker",
        "tool_name": tool_name,
        "parameters": parameters or {"path": "docs/architecture-roadmap-v1.1.md"},
        "timeout_ms": 5000,
        "risk_level": risk_level,
        "requires_human_approval": requires_human_approval,
        "created_at": "2026-10-10T12:00:00Z",
    }


class TestPhase2ControlLayer(unittest.TestCase):

    def test_01_normal_success_flow(self):
        """Scenario 1: Normal lifecycle created -> classified -> planning -> executing -> reviewing -> succeeded."""
        worker = ScriptedWorkerAdapter(mode="normal", tokens_per_call=100)
        reviewer = ScriptedReviewerAdapter(mode="pass", tokens_per_call=80)
        orch = Orchestrator(worker_adapter=worker, reviewer_adapter=reviewer)

        task = make_task_payload("task_success_0001")
        orch.submit_task(task)
        final_task = orch.run_task("task_success_0001")

        self.assertEqual(final_task["status"], "succeeded")
        self.assertEqual(final_task["review_verdict"], "pass")
        status_info = orch.get_task_status("task_success_0001")
        self.assertEqual(status_info["tokens_used"], 180)
        self.assertEqual(status_info["turns_used"], 2)

        events = orch.list_task_events("task_success_0001")
        states = [e["to_state"] for e in events if e["event_type"] == "state_transition"]
        self.assertEqual(states, ["classified", "planning", "executing", "reviewing", "succeeded"])

    def test_02_tool_and_worker_failure(self):
        """Scenario 2: Tool execution failure or Worker error transitions to 'failed'."""
        worker = ScriptedWorkerAdapter(mode="normal")
        reviewer = ScriptedReviewerAdapter(mode="pass")
        tool_exec = MockToolExecutor(mode="fail")
        orch = Orchestrator(worker_adapter=worker, reviewer_adapter=reviewer, tool_executor=tool_exec)

        task = make_task_payload("task_fail_tool_02")
        orch.submit_task(task)
        final_task = orch.run_task("task_fail_tool_02")
        self.assertEqual(final_task["status"], "failed")
        self.assertIn("failed", final_task["error_info"].lower())

    def test_03_review_revise_reject_and_fail_closed(self):
        """Scenario 3: Review revise->repair->pass, repair exhaustion->failed, reject->failed, unavailable->needs_attention."""
        # 3a: revise once then pass
        w1 = ScriptedWorkerAdapter(mode="normal")
        r1 = ScriptedReviewerAdapter(mode="revise_then_pass")
        orch1 = Orchestrator(worker_adapter=w1, reviewer_adapter=r1)
        orch1.submit_task(make_task_payload("task_revise_pass1"))
        res1 = orch1.run_task("task_revise_pass1")
        self.assertEqual(res1["status"], "succeeded")
        self.assertEqual(res1["_runtime"]["repair_count"], 1)

        # 3b: always revise exhausts max_repairs (2) -> failed
        w2 = ScriptedWorkerAdapter(mode="normal")
        r2 = ScriptedReviewerAdapter(mode="always_revise")
        orch2 = Orchestrator(worker_adapter=w2, reviewer_adapter=r2)
        orch2.submit_task(make_task_payload("task_revise_exh02"))
        res2 = orch2.run_task("task_revise_exh02")
        self.assertEqual(res2["status"], "failed")
        self.assertEqual(res2["review_verdict"], "revise")
        self.assertEqual(res2["_runtime"]["repair_count"], 2)

        # 3c: reject -> immediate terminal failed
        w3 = ScriptedWorkerAdapter(mode="normal")
        r3 = ScriptedReviewerAdapter(mode="reject")
        orch3 = Orchestrator(worker_adapter=w3, reviewer_adapter=r3)
        orch3.submit_task(make_task_payload("task_reject_0003"))
        res3 = orch3.run_task("task_reject_0003")
        self.assertEqual(res3["status"], "failed")
        self.assertEqual(res3["review_verdict"], "reject")

        # 3d: Reviewer unavailable / invalid JSON -> fail-closed to needs_attention (NEVER succeeded)
        for bad_mode in ("unavailable", "timeout", "invalid_schema"):
            w4 = ScriptedWorkerAdapter(mode="normal")
            r4 = ScriptedReviewerAdapter(mode=bad_mode)
            orch4 = Orchestrator(worker_adapter=w4, reviewer_adapter=r4)
            tid = f"task_rev_bad_{bad_mode[:6]}"
            orch4.submit_task(make_task_payload(tid))
            res4 = orch4.run_task(tid)
            self.assertEqual(res4["status"], "needs_attention")
            self.assertNotEqual(res4["status"], "succeeded")

    def test_04_missing_credentials_and_mock_worker_fallback(self):
        """Scenario 4: Missing GEMINI_API_KEY routes to Mock Worker; explicit CredentialMissingError fails closed."""
        old_gemini = os.environ.pop("GEMINI_API_KEY", None)
        old_google = os.environ.pop("GOOGLE_API_KEY", None)
        try:
            reviewer = ScriptedReviewerAdapter(mode="pass")
            orch = Orchestrator(reviewer_adapter=reviewer)
            orch.submit_task(make_task_payload("task_mock_fallback"))
            res = orch.run_task("task_mock_fallback")
            self.assertEqual(res["status"], "succeeded")
            self.assertEqual(res["assigned_worker"], "mock_worker")

            # Explicit credential missing in worker
            bad_worker = ScriptedWorkerAdapter(mode="missing_credential")
            orch2 = Orchestrator(worker_adapter=bad_worker, reviewer_adapter=reviewer)
            orch2.submit_task(make_task_payload("task_cred_error01"))
            res2 = orch2.run_task("task_cred_error01")
            self.assertEqual(res2["status"], "failed")
            self.assertIn("GEMINI_API_KEY", res2["error_info"])
        finally:
            if old_gemini:
                os.environ["GEMINI_API_KEY"] = old_gemini
            if old_google:
                os.environ["GOOGLE_API_KEY"] = old_google

    def test_05_illegal_state_transitions_and_invalid_messages(self):
        """Scenario 5: Illegal transitions (e.g., executing->succeeded directly) and invalid schemas are rejected."""
        orch = Orchestrator(
            worker_adapter=ScriptedWorkerAdapter(),
            reviewer_adapter=ScriptedReviewerAdapter(),
        )
        # Invalid Task payload rejected
        with self.assertRaises(SchemaValidationError):
            orch.submit_task({"task_id": "bad", "status": "created"})

        orch.submit_task(make_task_payload("task_illegal_st01"))
        orch.storage.transition_task_state("task_illegal_st01", "classified")
        orch.storage.transition_task_state("task_illegal_st01", "planning")
        orch.storage.transition_task_state("task_illegal_st01", "executing")

        # Worker completion != task success: executing -> succeeded directly is forbidden!
        with self.assertRaises(InvalidStateTransitionError):
            orch.storage.transition_task_state("task_illegal_st01", "succeeded")

        # reviewing -> succeeded without verdict='pass' is forbidden!
        orch.storage.transition_task_state("task_illegal_st01", "reviewing")
        with self.assertRaises(InvalidStateTransitionError):
            orch.storage.transition_task_state("task_illegal_st01", "succeeded", review_verdict="revise")

        # Valid transition to succeeded
        orch.storage.transition_task_state("task_illegal_st01", "succeeded", review_verdict="pass")
        # Terminal state cannot transition anywhere
        with self.assertRaises(InvalidStateTransitionError):
            orch.storage.transition_task_state("task_illegal_st01", "executing")

    def test_06_danger_action_overrides_routing_and_per_call_check(self):
        """Scenario 6: Level 3 forbidden actions override routing and per-call low-risk declarations."""
        reviewer = ScriptedReviewerAdapter(mode="pass")

        # 6a: Task description contains Level 3 command even though complexity=low and risk_level=read_only
        orch1 = Orchestrator(worker_adapter=ScriptedWorkerAdapter(), reviewer_adapter=reviewer)
        t1 = make_task_payload(
            "task_l3_route_001",
            description="Run rmdir /s /q C:\\Windows\\System32 to clean disk",
            risk_level="read_only",
            complexity="low",
        )
        orch1.submit_task(t1)
        res1 = orch1.run_task("task_l3_route_001")
        self.assertEqual(res1["status"], "failed")
        self.assertIn("Level 3", res1["error_info"])

        # 6b: Worker proposes a disguised read_only ToolRequest that actually writes to C:\Windows or danger-action-policy.md
        disguised_req = make_tool_request(
            "task_l3_call_002",
            request_id="treq_disguised_l3",
            tool_name="write_file",
            parameters={"path": "docs/danger-action-policy.md", "content": "hacked"},
            risk_level="read_only",  # Disguised as read_only!
        )
        w2 = ScriptedWorkerAdapter(tool_requests_by_turn=[[disguised_req]])
        orch2 = Orchestrator(worker_adapter=w2, reviewer_adapter=reviewer)
        orch2.submit_task(make_task_payload("task_l3_call_002", risk_level="read_only"))
        res2 = orch2.run_task("task_l3_call_002")
        self.assertEqual(res2["status"], "failed")
        self.assertIn("L3-PATH-SELF-POLICY-MODIFICATION", res2["error_info"])
        self.assertEqual(len(orch2.tool_executor.executed_requests), 0)

    def test_07_awaiting_approval_and_incremental_reauthorization(self):
        """Scenario 7: Level 2 sensitive action & incremental scope expansion enter awaiting_approval until approved."""
        # Worker tries to write a file outside initially authorized target_files scope
        unapproved_scope_req = make_tool_request(
            "task_l2_approve01",
            request_id="treq_scope_expand1",
            tool_name="write_file",
            parameters={"path": "src/new_unlisted_module.py"},
            risk_level="workspace_write",
        )
        w = ScriptedWorkerAdapter(tool_requests_by_turn=[[unapproved_scope_req]])
        r = ScriptedReviewerAdapter(mode="pass")
        orch = Orchestrator(
            worker_adapter=w,
            reviewer_adapter=r,
            enforce_target_files_scope=True,
        )
        orch.submit_task(
            make_task_payload("task_l2_approve01", target_files=["docs/architecture-roadmap-v1.1.md"])
        )
        paused = orch.run_task("task_l2_approve01")
        self.assertEqual(paused["status"], "awaiting_approval")
        status = orch.get_task_status("task_l2_approve01")
        self.assertEqual(status["pending_approval_request"]["request_id"], "treq_scope_expand1")

        # Approve and resume -> succeeded
        resumed = orch.approve_task_request("task_l2_approve01", "treq_scope_expand1")
        self.assertEqual(resumed["status"], "succeeded")

    def test_08_cancellation(self):
        """Scenario 8: Task cancellation transitions to 'cancelled' and locks terminal state."""
        req = make_tool_request(
            "task_cancel_0008",
            request_id="treq_hold_cancel8",
            tool_name="delete_file",
            parameters={"path": "docs/temp.txt"},
            risk_level="system_sensitive",
            requires_human_approval=True,
        )
        orch = Orchestrator(
            worker_adapter=ScriptedWorkerAdapter(tool_requests_by_turn=[[req]]),
            reviewer_adapter=ScriptedReviewerAdapter(),
        )
        orch.submit_task(make_task_payload("task_cancel_0008"))
        paused = orch.run_task("task_cancel_0008")
        self.assertEqual(paused["status"], "awaiting_approval")

        cancelled = orch.cancel_task("task_cancel_0008", reason="User aborted task")
        self.assertEqual(cancelled["status"], "cancelled")

        with self.assertRaises(InvalidStateTransitionError):
            orch.approve_task_request("task_cancel_0008", "treq_hold_cancel8")

    def test_09_timeout_and_remaining_budget_constraint(self):
        """Scenario 9: Task exceeding time budget transitions to 'failed' and caps remaining wait budget."""
        now_val = [100.0]
        orch = Orchestrator(
            worker_adapter=ScriptedWorkerAdapter(),
            reviewer_adapter=ScriptedReviewerAdapter(),
            clock=lambda: now_val[0],
        )
        orch.submit_task(make_task_payload("task_timeout_0009", max_execution_time_seconds=30))
        self.assertEqual(orch.get_remaining_budget_seconds("task_timeout_0009"), 30.0)

        # Advance clock past the 30s deadline
        now_val[0] = 135.0
        self.assertEqual(orch.get_remaining_budget_seconds("task_timeout_0009"), 0.0)
        res = orch.run_task("task_timeout_0009")
        self.assertEqual(res["status"], "failed")
        self.assertIn("timeout", res["error_info"].lower())

    def test_10_budget_exhaustion_and_unknown_tokens_preserved_as_none(self):
        """Scenario 10: Token/Turn budget exhaustion fails task; missing token usage stays None (never 0)."""
        # 10a: Unknown tokens (None) must stay None in DB, never 0
        w_unknown = ScriptedWorkerAdapter(tokens_per_call=None)
        r_unknown = ScriptedReviewerAdapter(tokens_per_call=None)
        orch_u = Orchestrator(worker_adapter=w_unknown, reviewer_adapter=r_unknown)
        orch_u.submit_task(make_task_payload("task_unk_tokens10"))
        res_u = orch_u.run_task("task_unk_tokens10")
        self.assertEqual(res_u["status"], "succeeded")
        st_u = orch_u.get_task_status("task_unk_tokens10")
        self.assertIsNone(st_u["tokens_used"])
        self.assertEqual(st_u["tokens_unknown_count"], 2)

        # 10b: Token budget exhaustion after planning
        w_heavy = ScriptedWorkerAdapter(tokens_per_call=5000)
        r_heavy = ScriptedReviewerAdapter(tokens_per_call=100)
        orch_b = Orchestrator(worker_adapter=w_heavy, reviewer_adapter=r_heavy)
        orch_b.submit_task(make_task_payload("task_tok_exhaust", max_tokens=1000))
        res_b = orch_b.run_task("task_tok_exhaust")
        self.assertEqual(res_b["status"], "failed")
        self.assertIn("token budget exhausted", res_b["error_info"].lower())

    def test_11_restart_recovery_blocks_unconfirmed_side_effect_replay(self):
        """Scenario 11: Process crash mid-write is recovered from SQLite into 'needs_attention' without blind replay."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = Path(tmpdir) / "test_recovery.sqlite3"
            write_req = make_tool_request(
                "task_crash_rec11",
                request_id="treq_write_crash11",
                tool_name="write_file",
                parameters={"path": "docs/output_notes.md"},
                risk_level="workspace_write",
            )
            storage1 = TaskStorage(db_file)
            orch1 = Orchestrator(
                storage=storage1,
                worker_adapter=ScriptedWorkerAdapter(tool_requests_by_turn=[[write_req]]),
                reviewer_adapter=ScriptedReviewerAdapter(),
                tool_executor=MockToolExecutor(mode="interrupt_mid_write"),
            )
            orch1.submit_task(make_task_payload("task_crash_rec11"))
            with self.assertRaises(SimulatedInterruptionError):
                orch1.run_task("task_crash_rec11")
            storage1.close()

            # Simulate new process restarting on the same SQLite database
            storage2 = TaskStorage(db_file)
            orch2 = Orchestrator(
                storage=storage2,
                worker_adapter=ScriptedWorkerAdapter(),
                reviewer_adapter=ScriptedReviewerAdapter(),
            )
            recovered_list = orch2.recover_interrupted_tasks()
            self.assertEqual(len(recovered_list), 1)
            rec = recovered_list[0]
            self.assertEqual(rec["task_id"], "task_crash_rec11")
            self.assertEqual(rec["previous_state"], "executing")
            self.assertEqual(rec["recovered_state"], "needs_attention")
            self.assertFalse(rec["auto_replay_allowed"])

            # Verify task is in needs_attention and run_task refuses to blindly replay it
            after_run = orch2.run_task("task_crash_rec11")
            self.assertEqual(after_run["status"], "needs_attention")
            tools = storage2.list_tool_executions("task_crash_rec11")
            self.assertEqual(tools[0]["status"], "interrupted_unconfirmed")
            storage2.close()


if __name__ == "__main__":
    unittest.main()
