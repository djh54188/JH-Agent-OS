"""JH-004 artifact-entry regression tests (offline, no real models or host skills).

Covers the N1 generation-entry path on the isolated branch:
- production NoTool assembly denies every tool request; tools can never succeed;
- unknown execution_mode / illegal template shapes are refused before models run;
- fixed records_transform_v1 template: trusted sort, exactly two conditions;
- trusted source/template stamping; fixture/production swap refusal on resume;
- resume gates: owner-held, in-flight, unconfirmed side effects, unknown usage;
- recover skips owner-held tasks without preemption;
- read-only query edge cases and delivery-scope summary fields;
- real CLI subprocesses across processes: submit/query/resume/read.
- R01-R04: locked template criteria text/order, production source gates,
  skill worktree binding markers, events missing-task failure parity.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import cli as cli_mod
from src.acceptance import transform_acceptance_evaluator
from src.cli import RECORDS_TRANSFORM_CRITERIA
from src.mock_components import MockToolExecutor, ScriptedReviewerAdapter, ScriptedWorkerAdapter
from src.orchestrator import Orchestrator
from src.production_executor import NoToolExecutor
from src.storage import TaskStorage
from test_phase2_control_layer import make_tool_request

# Canonical template criteria, pinned to the single trusted definition in src.
# Independent probes import this name; it must always equal the source of truth.
CRITERIA = list(RECORDS_TRANSFORM_CRITERIA)


class _UncalledStubAdapter:
    """Non-mock adapter stand-in for assembly-only tests; never invoked.

    Carries no mock marker, so trusted assembly accepts it. Tests using it
    never run the task and never claim a real model call.
    """

    def __init__(self, model_id):
        self.model_id = model_id


RECORDS = [
    {"region": "b", "sales": 2},
    {"region": "a", "sales": 1},
]
EXPECTED_SORTED = sorted(RECORDS, key=lambda item: (item["region"], item["sales"]))


def _payload(task_id, ws, **over):
    base = {
        "schema_version": "1.0.0",
        "task_id": task_id,
        "title": "records transform probe",
        "description": "records transform probe",
        "acceptance_criteria": list(CRITERIA),
        "priority": "high",
        "status": "created",
        "risk_assessment": {"risk_level": "read_only", "complexity": "low",
                            "uncertainty": "low", "risk_basis": "probe",
                            "routing_basis": "probe"},
        "budget": {"max_turns": 4, "max_tokens": 6000, "max_execution_time_seconds": 120},
        "context": {"workspace_root": str(ws), "target_files": []},
        "artifact_refs": [],
        "tool_execution_evidence": [],
        "review_verdict": None,
        "error_info": None,
        "assigned_worker": "gemini_worker",
        "assigned_reviewer": "muse_reviewer",
        "metadata": {"input_data": {"records": [dict(r) for r in RECORDS]}},
        "created_at": "2026-10-13T00:00:00Z",
        "updated_at": "2026-10-13T00:00:00Z",
    }
    base.update(over)
    return base


def _run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli_mod.main(argv)
    return code, buf.getvalue()


def _write_task_file(case_dir, payload):
    task_file = case_dir / (payload["task_id"] + ".json")
    task_file.write_text(json.dumps(payload), encoding="utf-8")
    return task_file


class _Case(unittest.TestCase):
    def setUp(self):
        self.case = ROOT / "data" / "test-workspaces" / ("jh004-" + uuid.uuid4().hex[:8])
        self.ws = self.case / "ws"
        self.ws.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.case, ignore_errors=True)
        self.db = self.case / "jh004.sqlite3"


class TestNoToolProduction(_Case):
    def test_execute_denies_everything(self):
        ex = NoToolExecutor()
        req = make_tool_request("task_nt0000001", tool_name="read_file",
                                parameters={"path": "out/x.txt"})
        res = ex.execute(req)
        self.assertEqual(res["status"], "denied_by_policy")
        self.assertTrue(res["structured_data"]["production_no_tool"])
        self.assertEqual(len(ex.denied_requests), 1)
        self.assertFalse(hasattr(ex, "executed_requests"))
        self.assertEqual(ex.snapshot_artifacts(["anything"]), {})

    def test_production_assembly_rejects_mock_adapters(self):
        from src.adapters import MockModelAdapter
        from src.cli import TrustedAssemblyError, create_orchestrator
        with patch.object(cli_mod, "has_credential", return_value=True), \
             patch.object(cli_mod, "load_model_adapter",
                          side_effect=lambda role: ScriptedWorkerAdapter(tool_requests_by_turn=[[]])
                          if role == "worker" else ScriptedReviewerAdapter(mode="pass")):
            with self.assertRaises(TrustedAssemblyError):
                create_orchestrator(self.db, workspace_root=self.ws, test_fixture_mode=False)
        with patch.object(cli_mod, "has_credential", return_value=True):
            with self.assertRaises(TrustedAssemblyError):
                create_orchestrator(
                    ":memory:", workspace_root=self.ws, test_fixture_mode=False,
                    worker_adapter=MockModelAdapter({"model_id": "mock-test"}),
                    reviewer_adapter=_UncalledStubAdapter("probe-reviewer-real"))

    def test_production_assembly_accepts_non_mock_adapters(self):
        from src.cli import create_orchestrator
        with patch.object(cli_mod, "has_credential", return_value=True):
            orch = create_orchestrator(
                self.db, workspace_root=self.ws, test_fixture_mode=False,
                worker_adapter=_UncalledStubAdapter("probe-worker-real"),
                reviewer_adapter=_UncalledStubAdapter("probe-reviewer-real"))
            try:
                self.assertIsInstance(orch.tool_executor, NoToolExecutor)
                self.assertTrue(orch._require_real_responses)
            finally:
                orch.storage.close()

    def test_fixture_assembly_uses_mock(self):
        from src.cli import create_orchestrator
        orch = create_orchestrator(":memory:", workspace_root=self.ws, test_fixture_mode=True)
        try:
            self.assertIsInstance(orch.tool_executor, MockToolExecutor)
            self.assertFalse(orch._require_real_responses)
        finally:
            orch.storage.close()

    def test_tool_proposal_in_generate_mode_fails(self):
        tid = "task_nt0000002"
        req = make_tool_request(tid, tool_name="read_file", parameters={"path": "out/x.txt"})
        storage = TaskStorage(":memory:")
        self.addCleanup(storage.close)
        orch = Orchestrator(
            storage=storage, workspace_root=self.ws,
            tool_executor=NoToolExecutor(),
            worker_adapter=ScriptedWorkerAdapter(tool_requests_by_turn=[[req]]),
            reviewer_adapter=ScriptedReviewerAdapter(mode="pass"),
            acceptance_evaluator=transform_acceptance_evaluator(EXPECTED_SORTED),
        )
        orch.submit_task(_payload(tid, self.ws))
        result = orch.run_task(tid)
        self.assertEqual(result["status"], "failed")
        self.assertIn("denied", (result.get("error_info") or "").lower())
        execs = storage.list_tool_executions(tid)
        self.assertEqual(len(execs), 1)
        self.assertEqual(execs[0]["status"], "denied_by_policy")
        self.assertEqual(len(orch.tool_executor.denied_requests), 1)


class TestFixedTemplate(_Case):
    def test_template_end_to_end_succeeds(self):
        tid = "task_tp0000001"
        expected_text = json.dumps(EXPECTED_SORTED, ensure_ascii=False, sort_keys=True)
        storage = TaskStorage(":memory:")
        self.addCleanup(storage.close)
        orch = Orchestrator(
            storage=storage, workspace_root=self.ws,
            tool_executor=NoToolExecutor(),
            worker_adapter=ScriptedWorkerAdapter(
                tool_requests_by_turn=[[]], artifact_content_by_turn=[expected_text]),
            reviewer_adapter=ScriptedReviewerAdapter(mode="pass"),
            acceptance_evaluator=transform_acceptance_evaluator(EXPECTED_SORTED),
        )
        orch.submit_task(_payload(tid, self.ws))
        result = orch.run_task(tid)
        self.assertEqual(result["status"], "succeeded")
        bindings = storage.list_review_bindings(tid)
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]["review"]["task_id"], tid)
        self.assertTrue(all(item["passed"] for item in bindings[0]["acceptance"]))
        self.assertEqual(len(bindings[0]["acceptance"]), 2)
        latest = storage.get_latest_artifact(tid)
        self.assertIn(latest["sha256"], json.dumps(bindings[0]["artifacts"]))

    def test_template_rejects_untrusted_shapes(self):
        from src.cli import TrustedAssemblyError, resolve_trusted_evaluator
        ws = self.ws
        bad_inputs = [
            {"records": [dict(r) for r in RECORDS], "sort_by": "sales"},
            {"expected_output": EXPECTED_SORTED},
            {"records": [dict(r) for r in RECORDS], "expected_output": EXPECTED_SORTED},
            {},
            {"records": []},
            {"records": [{"region": "a"}]},
            {"records": [{"region": "a", "sales": True}]},
            {"records": "not-a-list"},
        ]
        for idx, bad in enumerate(bad_inputs):
            with self.subTest(idx=idx):
                with self.assertRaises(TrustedAssemblyError):
                    resolve_trusted_evaluator(
                        _payload(f"task_tp0001{idx:02d}", ws, metadata={"input_data": bad}))
        with self.assertRaises(TrustedAssemblyError):
            resolve_trusted_evaluator(
                _payload("task_tp0000102", ws, acceptance_criteria=["only-one"]))
        with self.assertRaises(TrustedAssemblyError):
            resolve_trusted_evaluator(
                _payload("task_tp0000103", ws,
                         metadata={"execution_mode": "shell",
                                   "input_data": {"records": [dict(r) for r in RECORDS]}}))
        # The fixed rule ignores nothing: unsorted input still yields sorted expected.
        evaluator = resolve_trusted_evaluator(_payload("task_tp0000104", ws))
        self.assertTrue(callable(evaluator))

    def test_submit_refuses_before_model_call(self):
        tid = "task_tp0000002"
        task_file = _write_task_file(self.case, _payload(
            tid, self.ws,
            metadata={"execution_mode": "nope",
                      "input_data": {"records": [dict(r) for r in RECORDS]}}))
        load_mock = MagicMock(side_effect=AssertionError("model path touched"))
        cred_mock = MagicMock(side_effect=AssertionError("credential path touched"))
        with patch.object(cli_mod, "load_model_adapter", load_mock), \
             patch.object(cli_mod, "has_credential", cred_mock):
            code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                                  "submit", str(task_file)])
        self.assertEqual(code, 1)
        self.assertIn("Unknown execution_mode", out)
        load_mock.assert_not_called()
        cred_mock.assert_not_called()


def _scripted_adapters():
    return (
        ScriptedWorkerAdapter(tool_requests_by_turn=[[]]),
        ScriptedReviewerAdapter(mode="pass"),
    )


class TestSourceBinding(_Case):
    def _submit(self, tid, fixture_mode, payload_extra=None):
        payload = _payload(tid, self.ws, **(payload_extra or {}))
        task_file = _write_task_file(self.case, payload)
        argv = ["--db", str(self.db), "--workspace", str(self.ws)]
        if fixture_mode:
            argv.append("--test-fixture-mode")
        argv += ["submit", str(task_file)]
        return _run_cli(argv)

    def _stored_stamp(self, tid):
        storage = TaskStorage(self.db, read_only=True)
        try:
            return (storage.get_task(tid).get("metadata") or {}).get("_trusted")
        finally:
            storage.close()

    def test_fixture_submit_stamps_fixture(self):
        code, _ = self._submit("task_sb0000001", True)
        self.assertEqual(code, 0)
        stamp = self._stored_stamp("task_sb0000001")
        self.assertEqual(stamp["source"], "fixture")
        self.assertEqual(stamp["template_id"], "records_transform_v1")
        self.assertEqual(stamp["execution_mode"], "generate")

    def test_production_submit_stamps_production(self):
        worker = _UncalledStubAdapter("probe-worker-real")
        reviewer = _UncalledStubAdapter("probe-reviewer-real")
        with patch.object(cli_mod, "has_credential", return_value=True), \
             patch.object(cli_mod, "load_model_adapter",
                          side_effect=lambda role: worker if role == "worker" else reviewer):
            code, _ = self._submit("task_sb0000002", False)
        self.assertEqual(code, 0)
        self.assertEqual(self._stored_stamp("task_sb0000002")["source"], "production")

    def test_payload_stamp_cannot_be_forged(self):
        code, _ = self._submit("task_sb0000003", True, {
            "metadata": {"input_data": {"records": [dict(r) for r in RECORDS]},
                         "_trusted": {"source": "production", "template_id": "x"}}})
        self.assertEqual(code, 0)
        stamp = self._stored_stamp("task_sb0000003")
        self.assertEqual(stamp["source"], "fixture")
        self.assertEqual(stamp["template_id"], "records_transform_v1")

    def test_resume_swap_refused_both_directions(self):
        self._submit("task_sb0000004", True)
        code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                              "resume", "task_sb0000004"])
        self.assertEqual(code, 1)
        self.assertIn("Source mismatch", out)

        worker = _UncalledStubAdapter("probe-worker-real")
        reviewer = _UncalledStubAdapter("probe-reviewer-real")
        with patch.object(cli_mod, "has_credential", return_value=True), \
             patch.object(cli_mod, "load_model_adapter",
                          side_effect=lambda role: worker if role == "worker" else reviewer):
            code, _ = self._submit("task_sb0000005", False)
        self.assertEqual(code, 0)
        code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                              "--test-fixture-mode", "resume", "task_sb0000005"])
        self.assertEqual(code, 1)
        self.assertIn("Source mismatch", out)

    def test_legacy_unstamped_resume_refused(self):
        storage = TaskStorage(self.db, read_only=False)
        try:
            storage.create_task(_payload("task_sb0000006", self.ws))
        finally:
            storage.close()
        code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                              "--test-fixture-mode", "resume", "task_sb0000006"])
        self.assertEqual(code, 1)
        self.assertIn("predates trusted source binding", out)


class TestResumeGates(_Case):
    def _submit_fixture(self, tid):
        payload = _payload(tid, self.ws)
        task_file = _write_task_file(self.case, payload)
        code, _ = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                            "--test-fixture-mode", "submit", str(task_file)])
        self.assertEqual(code, 0)

    def _resume_fixture(self, tid):
        return _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                         "--test-fixture-mode", "resume", tid])

    def test_owner_held_blocks_then_release_proceeds(self):
        tid = "task_rg0000001"
        self._submit_fixture(tid)
        storage = TaskStorage(self.db, read_only=False)
        try:
            self.assertTrue(storage.claim_task(tid, "other-process"))
            code, out = self._resume_fixture(tid)
            self.assertEqual(code, 1)
            self.assertIn("owner_held_by_active_execution", out)
            storage.release_task(tid, "other-process")
            before = len(storage.list_model_attempts(tid))
            code, _ = self._resume_fixture(tid)
            after = len(storage.list_model_attempts(tid))
            self.assertGreater(after, before)
        finally:
            storage.close()

    def test_inflight_attempt_blocks(self):
        tid = "task_rg0000002"
        self._submit_fixture(tid)
        storage = TaskStorage(self.db, read_only=False)
        try:
            storage.begin_attempt(tid, "worker", {"probe": True})
        finally:
            storage.close()
        code, out = self._resume_fixture(tid)
        self.assertEqual(code, 1)
        self.assertIn("unconfirmed_model_attempts", out)

    def test_inflight_tool_blocks(self):
        tid = "task_rg0000003"
        self._submit_fixture(tid)
        storage = TaskStorage(self.db, read_only=False)
        try:
            for state in ("classified", "planning", "executing"):
                storage.transition_task_state(tid, state)
            req = make_tool_request(tid, tool_name="read_file", parameters={"path": "x"})
            self.assertTrue(storage.record_tool_start(req, has_side_effect=False))
        finally:
            storage.close()
        code, out = self._resume_fixture(tid)
        self.assertEqual(code, 1)
        self.assertIn("unconfirmed_tool_executions", out)

    def test_unconfirmed_flag_and_unknown_usage_block(self):
        for idx, column in enumerate(("has_unconfirmed_side_effect", "tokens_unknown_count")):
            tid = f"task_rg000000{4 + idx}"
            self._submit_fixture(tid)
            storage = TaskStorage(self.db, read_only=False)
            try:
                with storage.transaction():
                    storage._conn.execute(
                        f"UPDATE tasks SET {column} = 1 WHERE task_id = ?", (tid,))
            finally:
                storage.close()
            code, out = self._resume_fixture(tid)
            self.assertEqual(code, 1, column)
            self.assertIn("unconfirmed_side_effect" if idx == 0 else "unknown_token_usage", out)


class TestRecoverNoPreempt(_Case):
    def test_recover_skips_held_owner(self):
        tid = "task_rc0000001"
        payload = _payload(tid, self.ws)
        task_file = _write_task_file(self.case, payload)
        code, _ = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                            "--test-fixture-mode", "submit", str(task_file)])
        self.assertEqual(code, 0)
        storage = TaskStorage(self.db, read_only=False)
        try:
            for state in ("classified", "planning", "executing"):
                storage.transition_task_state(tid, state)
            self.assertTrue(storage.claim_task(tid, "live-owner"))
            summary = storage.recover_interrupted_tasks()
        finally:
            storage.close()
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["reason"], "skipped_owner_held")
        self.assertFalse(summary[0]["auto_replay_allowed"])
        check = TaskStorage(self.db, read_only=True)
        try:
            self.assertEqual(check.get_task(tid)["status"], "executing")
            self.assertEqual(check.get_control(tid)["owner_id"], "live-owner")
        finally:
            check.close()


class TestReadOnlyEdges(_Case):
    def test_artifact_without_artifact_shape(self):
        tid = "task_ro0000001"
        payload = _payload(tid, self.ws)
        task_file = _write_task_file(self.case, payload)
        code, _ = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                            "--test-fixture-mode", "submit", str(task_file)])
        self.assertEqual(code, 0)
        code, out = _run_cli(["--db", str(self.db), "artifact", tid])
        self.assertEqual(code, 0)
        body = json.loads(out)
        self.assertTrue(body["command_ok"])
        self.assertTrue(body["read_only"])
        self.assertIsNone(body["latest_artifact"])
        self.assertEqual(body["artifact_versions"], [])
        self.assertEqual(body["review_bindings"], [])

    def test_status_missing_task_structured(self):
        storage = TaskStorage(self.db, read_only=False)
        try:
            storage.create_task(_payload("task_ro0000002", self.ws))
        finally:
            storage.close()
        code, out = _run_cli(["--db", str(self.db), "status", "task_nope000002"])
        self.assertEqual(code, 1)
        body = json.loads(out)
        self.assertFalse(body["command_ok"])
        self.assertTrue(body["read_only"])
        self.assertIn("not found", body["error"])

    def test_summary_has_scope_and_source(self):
        tid = "task_ro0000003"
        payload = _payload(tid, self.ws)
        task_file = _write_task_file(self.case, payload)
        code, _ = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                            "--test-fixture-mode", "submit", str(task_file)])
        self.assertEqual(code, 0)
        code, out = _run_cli(["--db", str(self.db), "summary", tid])
        self.assertEqual(code, 0)
        result = json.loads(out)["task_result"]
        self.assertEqual(result["source_binding"]["source"], "fixture")
        self.assertEqual(result["source_binding"]["template_id"], "records_transform_v1")
        self.assertFalse(result["delivery_scope"]["generated_artifact"])
        self.assertFalse(result["delivery_scope"]["target_applied"])


class TestCrossProcessCLI(_Case):
    def _cli(self, *args):
        proc = subprocess.run(
            [sys.executable, "-m", "src.cli", "--db", str(self.db),
             "--workspace", str(self.ws), *args],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
            timeout=180)
        return proc.returncode, proc.stdout, proc.stderr

    def test_four_process_handoff(self):
        tid = "task_xp0000001"
        task_file = _write_task_file(self.case, _payload(tid, self.ws))

        # Process A: submit (no run) then exit.
        code, out, err = self._cli("--test-fixture-mode", "submit", str(task_file))
        self.assertEqual(code, 0, err)
        submitted = json.loads(out)["task_result"]
        self.assertEqual(submitted["task_id"], tid)
        self.assertEqual(submitted["status"], "created")

        # Process B: read-only queries observe the same persisted task.
        code, out, err = self._cli("status", tid)
        self.assertEqual(code, 0, err)
        status_body = json.loads(out)
        self.assertEqual(status_body["task_id"], tid)
        self.assertEqual(status_body["budget"], submitted["budget"])
        code, out, err = self._cli("summary", tid)
        self.assertEqual(code, 0, err)
        summary_body = json.loads(out)["task_result"]
        self.assertEqual(summary_body["source_binding"]["source"], "fixture")

        # Process C: resume the persisted-but-never-run task in a new process.
        storage = TaskStorage(self.db, read_only=True)
        try:
            attempts_before = len(storage.list_model_attempts(tid))
            events_before = len(storage.list_events(tid))
            deadline_before = storage.get_control(tid)["deadline"]
        finally:
            storage.close()
        code, out, err = self._cli("--test-fixture-mode", "resume", tid)
        self.assertIn(tid, out)
        storage = TaskStorage(self.db, read_only=True)
        try:
            attempts_after = len(storage.list_model_attempts(tid))
            events_after = len(storage.list_events(tid))
            task_after = storage.get_task(tid)
            deadline_after = storage.get_control(tid)["deadline"]
        finally:
            storage.close()
        # Fixture scripted content does not match the trusted expected output,
        # so the run must NOT succeed; it must visibly advance instead.
        self.assertNotEqual(task_after["status"], "created")
        self.assertIn(task_after["status"], {"repairing", "needs_attention", "failed"})
        self.assertGreater(attempts_after, attempts_before)
        self.assertGreater(events_after, events_before)
        self.assertEqual(deadline_after, deadline_before)
        self.assertEqual((task_after.get("metadata") or {}).get("_trusted", {}).get("source"), "fixture")
        self.assertEqual(task_after["budget"], submitted["budget"])

        # Re-resume must not duplicate execution: terminal-ish states refuse.
        code2, out2, _ = self._cli("--test-fixture-mode", "resume", tid)
        storage = TaskStorage(self.db, read_only=True)
        try:
            attempts_final = len(storage.list_model_attempts(tid))
        finally:
            storage.close()
        if task_after["status"] in {"needs_attention", "failed"}:
            self.assertEqual(code2, 1)
            self.assertEqual(attempts_final, attempts_after)

        # Process D: read-only result delivery; state untouched by reads.
        code, out, err = self._cli("artifact", tid)
        self.assertEqual(code, 0, err)
        self.assertIn(tid, out)
        code, out, err = self._cli("summary", tid)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["task_result"]["task_id"], tid)


class TestTemplateCriteriaLocked(_Case):
    """R01: the two standard conditions and their order are trusted; anything
    else is refused before models run, never echoed as passed."""

    def test_non_template_criteria_refused_before_model_call(self):
        impossible = ["Input records contain exactly 999 rows",
                      "Output JSON must contain exactly 999 rows"]
        tid = "task_r01000001"
        seed_file = _write_task_file(self.case, _payload("task_r01000000", self.ws))
        code, _ = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                            "--test-fixture-mode", "submit", str(seed_file)])
        self.assertEqual(code, 0)
        task_file = _write_task_file(
            self.case, _payload(tid, self.ws, acceptance_criteria=impossible))
        load_mock = MagicMock(side_effect=AssertionError("model path touched"))
        cred_mock = MagicMock(side_effect=AssertionError("credential path touched"))
        with patch.object(cli_mod, "load_model_adapter", load_mock), \
             patch.object(cli_mod, "has_credential", cred_mock):
            code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                                  "--test-fixture-mode", "submit", str(task_file), "--run"])
        self.assertEqual(code, 1)
        self.assertIn("standard", out)
        load_mock.assert_not_called()
        cred_mock.assert_not_called()
        storage = TaskStorage(self.db, read_only=True)
        try:
            with self.assertRaises(KeyError):
                storage.get_task(tid)
            self.assertEqual(storage.list_model_attempts(tid), [])
        finally:
            storage.close()

    def test_misordered_and_extra_criteria_refused(self):
        from src.cli import TrustedAssemblyError, resolve_trusted_evaluator
        with self.assertRaises(TrustedAssemblyError):
            resolve_trusted_evaluator(
                _payload("task_r01000002", self.ws,
                         acceptance_criteria=[CRITERIA[1], CRITERIA[0]]))
        with self.assertRaises(TrustedAssemblyError):
            resolve_trusted_evaluator(
                _payload("task_r01000003", self.ws,
                         acceptance_criteria=CRITERIA + ["And one more"]))

    def test_canonical_criteria_match_trusted_definition(self):
        self.assertEqual(CRITERIA, list(RECORDS_TRANSFORM_CRITERIA))


class _FlaggedStubAdapter:
    """Adapter double returning a caller-chosen is_mock flag; used only to
    exercise the production source gate, never as real-call evidence."""

    def __init__(self, model_id, is_mock):
        self.model_id = model_id
        self._is_mock = is_mock

    def invoke_structured(self, messages, schema, **kwargs):
        import types
        namespace = {"parsed_json": {}, "total_tokens": 10}
        if self._is_mock != "missing":
            namespace["is_mock"] = self._is_mock
        return types.SimpleNamespace(**namespace)


class TestProductionSourceGate(_Case):
    def test_production_run_rejects_mock_worker_response(self):
        tid = "task_r02000001"
        storage = TaskStorage(":memory:")
        self.addCleanup(storage.close)
        orch = Orchestrator(
            storage=storage, workspace_root=self.ws,
            tool_executor=NoToolExecutor(),
            worker_adapter=ScriptedWorkerAdapter(tool_requests_by_turn=[[]]),
            reviewer_adapter=ScriptedReviewerAdapter(mode="pass"),
            acceptance_evaluator=transform_acceptance_evaluator(EXPECTED_SORTED),
            require_real_responses=True,
        )
        orch.submit_task(_payload(tid, self.ws))
        result = orch.run_task(tid)
        self.assertEqual(result["status"], "failed")
        self.assertIn("mock", (result.get("error_info") or "").lower())
        self.assertEqual(storage.list_review_bindings(tid), [])
        sources = [e for e in storage.list_events(tid)
                   if e["event_type"] == "model_response_source"]
        self.assertEqual(len(sources), 1)
        self.assertTrue(sources[0]["details"]["is_mock"])
        self.assertEqual(sources[0]["details"]["role"], "worker")

    def test_invoke_model_gates_worker_reviewer_and_missing_flags(self):
        from src.adapters import AdapterError
        tid = "task_r02000002"
        storage = TaskStorage(":memory:")
        self.addCleanup(storage.close)
        orch = Orchestrator(storage=storage, workspace_root=self.ws,
                            require_real_responses=True)
        orch.submit_task(_payload(tid, self.ws))
        worker = ScriptedWorkerAdapter(tool_requests_by_turn=[[]])
        reviewer = ScriptedReviewerAdapter(mode="pass")
        with self.assertRaises(AdapterError):
            orch._invoke_model(tid, "worker", worker, [], {})
        with self.assertRaises(AdapterError):
            orch._invoke_model(tid, "reviewer", reviewer, [], {})
        with self.assertRaises(AdapterError):
            orch._invoke_model(tid, "worker",
                               _FlaggedStubAdapter("probe-no-flag", "missing"), [], {})
        resp, _ = orch._invoke_model(
            tid, "worker", _FlaggedStubAdapter("probe-confirmed-real", False), [], {})
        self.assertFalse(resp.is_mock)

    def test_production_resume_refuses_tainted_history(self):
        from src.cli import _production_taint_blocker
        tid = "task_r02000003"
        worker = _UncalledStubAdapter("probe-worker-real")
        reviewer = _UncalledStubAdapter("probe-reviewer-real")
        with patch.object(cli_mod, "has_credential", return_value=True), \
             patch.object(cli_mod, "load_model_adapter",
                          side_effect=lambda role: worker if role == "worker" else reviewer):
            payload = _payload(tid, self.ws)
            task_file = _write_task_file(self.case, payload)
            code, _ = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                                "submit", str(task_file)])
        self.assertEqual(code, 0)
        storage = TaskStorage(self.db, read_only=False)
        try:
            aid = storage.begin_attempt(tid, "worker", {"fixture": "completed call"})
            storage.complete_attempt(tid, aid, {"fixture": "response"}, 10)
            storage.record_event(
                task_id=tid, event_type="model_response_source",
                from_state="created", to_state="created", actor_role="orchestrator",
                details={"attempt_id": aid, "role": "worker",
                         "adapter_class": "ScriptedWorkerAdapter",
                         "model_id": "mock-gemini-worker", "is_mock": True})
        finally:
            storage.close()
        with patch.object(cli_mod, "has_credential", return_value=True):
            code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                                  "resume", tid])
        self.assertEqual(code, 1)
        self.assertIn("tainted_production_history", out)

    def test_taint_helper_passes_clean_and_empty_history(self):
        from src.cli import _production_taint_blocker
        tid = "task_r02000004"
        storage = TaskStorage(self.db, read_only=False)
        try:
            storage.create_task(_payload(tid, self.ws))
            self.assertIsNone(_production_taint_blocker(storage, tid))
            storage.record_event(
                task_id=tid, event_type="model_response_source",
                from_state="created", to_state="created", actor_role="orchestrator",
                details={"attempt_id": "attempt_clean1", "role": "worker",
                         "adapter_class": "GeminiAdapter",
                         "model_id": "gemini-3.8-flash", "is_mock": False})
            self.assertIsNone(_production_taint_blocker(storage, tid))
        finally:
            storage.close()


class TestSkillWorktreeBinding(_Case):
    def test_skill_binds_to_own_worktree(self):
        text = (ROOT / ".agents" / "skills" / "jh-task" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("D:/JH-Agent OS", text)
        for marker in ("contains THIS skill file", "rev-parse --show-toplevel",
                       "rev-parse --short HEAD", "src.cli.__file__",
                       "RECORDS_TRANSFORM_TEMPLATE_ID"):
            self.assertIn(marker, text)

    def test_skill_documents_output_shapes_and_criteria(self):
        text = (ROOT / ".agents" / "skills" / "jh-task" / "SKILL.md").read_text(encoding="utf-8")
        for marker in ("bare JSON list", "task_result", "Trusted input records were provided",
                       "Artifact content equals the expected sorted records"):
            self.assertIn(marker, text)


class TestEventsMissingTask(_Case):
    def test_events_missing_task_fails(self):
        storage = TaskStorage(self.db, read_only=False)
        try:
            storage.create_task(_payload("task_ev0000001", self.ws))
        finally:
            storage.close()
        code, out = _run_cli(["--db", str(self.db), "events", "task_nope000003"])
        self.assertEqual(code, 1)
        body = json.loads(out)
        self.assertFalse(body["command_ok"])
        self.assertTrue(body["read_only"])
        self.assertIn("not found", body["error"])

    def test_events_existing_task_succeeds_with_list(self):
        tid = "task_ev0000002"
        storage = TaskStorage(self.db, read_only=False)
        try:
            storage.create_task(_payload(tid, self.ws))
        finally:
            storage.close()
        code, out = _run_cli(["--db", str(self.db), "events", tid])
        self.assertEqual(code, 0)
        body = json.loads(out)
        self.assertIsInstance(body, list)
        self.assertTrue(all(e["task_id"] == tid for e in body))

    def test_missing_task_error_parity_across_queries(self):
        storage = TaskStorage(self.db, read_only=False)
        try:
            storage.create_task(_payload("task_ev0000003", self.ws))
        finally:
            storage.close()
        errors = {}
        for command in ("status", "summary", "events", "artifact"):
            code, out = _run_cli(["--db", str(self.db), command, "task_nope000004"])
            self.assertEqual(code, 1, command)
            errors[command] = json.loads(out)["error"]
        self.assertEqual(len(set(errors.values())), 1)

    def test_read_only_queries_leave_db_bytes_untouched(self):
        tid = "task_ev0000004"
        storage = TaskStorage(self.db, read_only=False)
        try:
            storage.create_task(_payload(tid, self.ws))
        finally:
            storage.close()
        before = Path(self.db).read_bytes()
        for command in ("status", "summary", "events", "artifact"):
            _run_cli(["--db", str(self.db), command, tid])
            _run_cli(["--db", str(self.db), command, "task_nope000005"])
        self.assertEqual(Path(self.db).read_bytes(), before)


class TestHistoricalSourceCoverage(_Case):
    """P1 followup: every completed call needs a matching trusted source record.

    Mirrors the independent historical_missing_source scenario: a legacy
    completed attempt without provenance must block production resume before
    assembly, with no new calls, attempts, state, or budget changes.
    """

    def _submit_production(self, tid):
        worker = _UncalledStubAdapter("probe-worker-real")
        reviewer = _UncalledStubAdapter("probe-reviewer-real")
        with patch.object(cli_mod, "has_credential", return_value=True), \
             patch.object(cli_mod, "load_model_adapter",
                          side_effect=lambda role: worker if role == "worker" else reviewer):
            payload = _payload(tid, self.ws)
            task_file = _write_task_file(self.case, payload)
            code, _ = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                                "submit", str(task_file)])
        self.assertEqual(code, 0)

    def _complete_worker_attempt(self, tid):
        storage = TaskStorage(self.db, read_only=False)
        try:
            aid = storage.begin_attempt(tid, "worker", {"fixture": "completed legacy call"})
            storage.complete_attempt(tid, aid, {"legacy": "response"}, 10)
            return aid
        finally:
            storage.close()

    def _record_source(self, tid, details):
        storage = TaskStorage(self.db, read_only=False)
        try:
            storage.record_event(
                task_id=tid, event_type="model_response_source",
                from_state="created", to_state="created", actor_role="orchestrator",
                details=details)
        finally:
            storage.close()

    def _resume_production_strict(self, tid):
        load_mock = MagicMock(side_effect=AssertionError("adapter load touched"))
        with patch.object(cli_mod, "has_credential", return_value=True), \
             patch.object(cli_mod, "load_model_adapter", load_mock):
            code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                                  "resume", tid])
        load_mock.assert_not_called()
        return code, out

    def _snapshot(self, tid):
        storage = TaskStorage(self.db, read_only=True)
        try:
            task = storage.get_task(tid)
            return {
                "status": task["status"],
                "budget": task["budget"],
                "attempts": storage.list_model_attempts(tid),
                "events": storage.list_events(tid),
            }
        finally:
            storage.close()

    def test_missing_source_blocks_resume_without_side_effects(self):
        tid = "task_hs0000001"
        self._submit_production(tid)
        self._complete_worker_attempt(tid)
        before = self._snapshot(tid)
        code, out = self._resume_production_strict(tid)
        self.assertEqual(code, 1)
        self.assertIn("no trusted source record", out)
        self.assertEqual(self._snapshot(tid), before)

    def test_wrong_role_source_refused(self):
        tid = "task_hs0000002"
        self._submit_production(tid)
        aid = self._complete_worker_attempt(tid)
        self._record_source(tid, {"attempt_id": aid, "role": "reviewer",
                                  "adapter_class": "X", "model_id": "y", "is_mock": False})
        code, out = self._resume_production_strict(tid)
        self.assertEqual(code, 1)
        self.assertIn("no trusted source record", out)

    def test_contradictory_sources_refused(self):
        tid = "task_hs0000003"
        self._submit_production(tid)
        aid = self._complete_worker_attempt(tid)
        self._record_source(tid, {"attempt_id": aid, "role": "worker",
                                  "adapter_class": "X", "model_id": "y", "is_mock": False})
        self._record_source(tid, {"attempt_id": aid, "role": "worker",
                                  "adapter_class": "X", "model_id": "y", "is_mock": True})
        code, out = self._resume_production_strict(tid)
        self.assertEqual(code, 1)
        self.assertIn("mock/unconfirmed", out)

    def test_missing_flag_source_refused(self):
        tid = "task_hs0000004"
        self._submit_production(tid)
        aid = self._complete_worker_attempt(tid)
        self._record_source(tid, {"attempt_id": aid, "role": "worker",
                                  "adapter_class": "X", "model_id": "y"})
        code, out = self._resume_production_strict(tid)
        self.assertEqual(code, 1)
        self.assertIn("mock/unconfirmed", out)

    def test_full_coverage_passes_helper(self):
        from src.cli import _production_taint_blocker
        tid = "task_hs0000005"
        self._submit_production(tid)
        aid = self._complete_worker_attempt(tid)
        self._record_source(tid, {"attempt_id": aid, "role": "worker",
                                  "adapter_class": "GeminiAdapter",
                                  "model_id": "gemini-3.8-flash", "is_mock": False})
        storage = TaskStorage(self.db, read_only=True)
        try:
            self.assertIsNone(_production_taint_blocker(storage, tid))
        finally:
            storage.close()


if __name__ == "__main__":
    unittest.main()
