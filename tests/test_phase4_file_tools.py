"""Phase 4 batch 1 offline tests: real file tools, path boundaries, staged writes,
post-review apply, junction escapes, and write interruption recovery.

Per the ruling, development/acceptance use an ISOLATED test workspace inside the
project (data/test-workspaces/<case>, gitignored) — never project sources or
user files. No network is contacted; providers are fixtures.

Coverage per ruling section 六:
normal read/write, review rejection, target change, parameter substitution,
duplicate requests, out-of-bounds paths, real junctions, write interruption.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.adapters import AdapterError, SchemaValidationError
from src.control_guards import verify_closed_loop_success
from src.file_tools import FileToolExecutor, PathBoundaryError
from src.acceptance import file_write_acceptance_evaluator
from src.mock_components import (
    MockToolExecutor,
    ScriptedReviewerAdapter,
    ScriptedWorkerAdapter,
    SimulatedInterruptionError,
)
from src.orchestrator import Orchestrator
from src.storage import TaskStorage
from test_phase2_control_layer import make_tool_request

FILE_ACCEPTANCE_CRITERIA = [
    "Worker 通过 stage_write 提交目标文件的写入请求且执行成功",
    "暂存内容与控制层独立计算的期望内容逐字节一致",
    "目标路径位于工作区内且在授权文件清单中",
]


def make_ws(name: str) -> Path:
    ws = ROOT / "data" / "test-workspaces" / name
    if ws.exists():
        shutil.rmtree(ws)
    (ws / "out").mkdir(parents=True, exist_ok=True)
    return ws


def make_file_task(task_id: str, ws: Path, *, target: str = "out/report.json",
                   expected_content: str | None = None, execution_mode: str = "file_tools") -> dict:
    expected = expected_content or json.dumps([{"region": "a", "sales": 1}], ensure_ascii=False)
    return {
        "schema_version": "1.0.0",
        "task_id": task_id,
        "title": "Stage and write report file",
        "description": "Stage a file via controlled tool and apply after review.",
        "acceptance_criteria": list(FILE_ACCEPTANCE_CRITERIA),
        "priority": "high",
        "status": "created",
        "risk_assessment": {
            "risk_level": "workspace_write", "complexity": "low", "uncertainty": "low",
            "risk_basis": "Workspace-bounded staged file write",
            "routing_basis": "Phase 4 file tool chain",
        },
        "budget": {"max_turns": 8, "max_tokens": 60000, "max_execution_time_seconds": 300},
        "context": {"workspace_root": str(ws), "target_files": [target]},
        "artifact_refs": [],
        "tool_execution_evidence": [],
        "review_verdict": None,
        "error_info": None,
        "assigned_worker": "mock_worker",
        "assigned_reviewer": "mock_reviewer",
        "metadata": {"execution_mode": execution_mode},
        "created_at": "2026-10-11T12:00:00Z",
        "updated_at": "2026-10-11T12:00:00Z",
    }


def stage_request(task_id: str, target: str, content: str, request_id: str | None = None) -> dict:
    return {
        "schema_version": "1.0.0",
        "request_id": request_id or f"treq_{uuid.uuid4().hex[:12]}",
        "task_id": task_id,
        "step_id": "step_01",
        "caller_role": "worker",
        "tool_name": "stage_write",
        "parameters": {"path": target, "content": content},
        "timeout_ms": 5000,
        "risk_level": "workspace_write",
        "requires_human_approval": False,
        "created_at": "2026-10-11T12:00:00Z",
    }


def make_orch(self, *, ws: Path, worker=None, reviewer=None, evaluator=None, storage=None,
              enforce_scope=True, tool_executor=None):
    orch = Orchestrator(
        storage=storage, workspace_root=ws,
        tool_executor=tool_executor or FileToolExecutor(ws),
        worker_adapter=worker or ScriptedWorkerAdapter(),
        reviewer_adapter=reviewer or ScriptedReviewerAdapter(),
        acceptance_evaluator=evaluator,
        enforce_target_files_scope=enforce_scope,
    )
    self.addCleanup(orch.storage.close)
    return orch


class TestFileToolExecutor(unittest.TestCase):
    """Direct executor tests on an isolated in-project workspace."""

    def setUp(self):
        self.ws = make_ws("executor-" + uuid.uuid4().hex[:8])
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        self.executor = FileToolExecutor(self.ws)

    def test_normal_read_and_stage_apply(self):
        (self.ws / "out" / "existing.txt").write_text("hello", encoding="utf-8")
        content = self.executor.read_file("out/existing.txt")
        self.assertEqual(content, "hello")

        result = self.executor.execute(stage_request("task_stagewr0001", "out/report.json", '{"a":1}'))
        self.assertEqual(result["status"], "success")
        staged_rel = result["structured_data"]["staged_path"]
        staged_sha = result["structured_data"]["content_sha256"]
        # Staged file exists on disk inside workspace; target untouched.
        self.assertTrue((self.ws / staged_rel).is_file())
        self.assertFalse((self.ws / "out" / "report.json").exists())

        outcome = self.executor.apply_staged(
            "task_stagewr0001", target_path="out/report.json", staged_path=staged_rel,
            content_sha256=staged_sha, target_pre_sha256=result["structured_data"]["target_pre_sha256"])
        self.assertTrue(outcome["applied"])
        self.assertEqual((self.ws / "out" / "report.json").read_text(encoding="utf-8"), '{"a":1}')
        # Audit phases recorded
        kinds = [e["kind"] for e in self.executor.events]
        for kind in ("staged_write", "apply_start", "backup_created", "temp_written",
                     "replaced", "apply_confirmed"):
            self.assertIn(kind, kinds)
        self.assertIsNone(outcome["backup_path"])  # new file: no backup needed

    def test_out_of_bounds_paths_refused(self):
        for bad in ("../evil.txt", "..\\evil.txt", "C:/Windows/evil.txt", "D:/elsewhere/x.txt",
                    "out/sub/../x.txt", "./out/./nested/../x.txt" if False else "out/./x.txt",
                    "out/x/", "", "out/./../evil.txt"):
            with self.subTest(bad=bad):
                if bad == "out/./x.txt":
                    continue  # same-directory dot is legal
                if bad == "out/x/":
                    result = self.executor.execute(stage_request("task_oob0000001", bad, "x"))
                    self.assertEqual(result["status"], "denied_by_policy")
                    continue
                if bad == "out/./../evil.txt":
                    # Component walk proves this stays inside the workspace
                    # (out verified real, then '..' pops to the ws root): allowed.
                    result = self.executor.execute(stage_request("task_oob0000001", bad, "x"))
                    self.assertEqual(result["status"], "success")
                    continue
                # execute() converts PathBoundaryError into a denied_by_policy result.
                result = self.executor.execute(stage_request("task_oob0000001", bad, "x"))
                self.assertEqual(result["status"], "denied_by_policy", bad)
                self.assertTrue(result["structured_data"]["violated_boundary"], bad)
        # Direct resolution raises for the same violations (no result wrapping).
        with self.assertRaises(PathBoundaryError):
            self.executor.resolve_workspace_path("../evil.txt")
        # same-name prefix directory (ws vs ws-evil style) at workspace level
        sibling = self.ws.parent / (self.ws.name + "-evil")
        sibling.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, sibling, ignore_errors=True)
        with self.assertRaises(PathBoundaryError):
            self.executor.resolve_workspace_path(str(sibling / "x.txt"))
        # execute-level: the sibling write must be denied and the sibling
        # directory must stay empty (safe out-of-bounds probe target).
        result = self.executor.execute(stage_request("task_oob0000001", str(sibling / "x.txt"), "x"))
        self.assertEqual(result["status"], "denied_by_policy")
        self.assertEqual(os.listdir(sibling), [])

    def test_drive_and_case_and_prefix(self):
        (self.ws / "out").mkdir(exist_ok=True)
        # Case-insensitive: upper-case variant of an in-workspace path is the SAME file.
        res = self.executor.execute(stage_request("task_case0000001", "out/CaseFile.txt", "x"))
        outcome = self.executor.apply_staged(
            "task_case0000001", target_path="out/CaseFile.txt",
            staged_path=res["structured_data"]["staged_path"],
            content_sha256=self.executor.events[-1]["content_sha256"], target_pre_sha256=None)
        self.assertTrue((self.ws / "out" / "casefile.txt").exists())  # same file, folded
        # Same-name prefix: 'ws' must not authorize 'ws-evil'
        sibling = self.ws.parent / (self.ws.name + "-evil")
        sibling.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, sibling, ignore_errors=True)
        with self.assertRaises(PathBoundaryError):
            self.executor.resolve_workspace_path(str(sibling / "x.txt"))
        # Drive-letter change refused
        with self.assertRaises(PathBoundaryError):
            self.executor.resolve_workspace_path("Q:/x.txt" if os.path.exists("Q:/") is False else "Q:/x.txt")

    def test_real_junction_escape_refused(self):
        outside = self.ws.parent / ("outside-" + self.ws.name[-6:])
        outside.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        link = self.ws / "link"
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                           capture_output=True)
        if r.returncode != 0:
            import _winapi
            _winapi.CreateJunction(str(outside), str(link))
        self.addCleanup(lambda: link.exists() and os.path.islink(link) and os.rmdir(link))
        # Junction as ancestor component
        result = self.executor.execute(stage_request("task_junc0000001", "link/evil.txt", "x"))
        self.assertEqual(result["status"], "denied_by_policy")
        self.assertIn("link/junction", result["stderr"])
        # Junction as the final parent component
        result = self.executor.execute(stage_request("task_junc0000002", "link/sub/x.txt", "x"))
        self.assertEqual(result["status"], "denied_by_policy")
        # The outside directory must be untouched
        self.assertEqual(os.listdir(outside), [])

    def test_new_file_requires_existing_parent(self):
        result = self.executor.execute(stage_request("task_newpar00001", "missing-dir/x.txt", "x"))
        self.assertEqual(result["status"], "denied_by_policy")
        self.assertFalse((self.ws / "missing-dir").exists())

    def test_race_planted_junction_refused(self):
        """TOCTOU: a junction planted between boundary check and open is caught
        by the post-open realpath containment re-check."""
        outside = self.ws.parent / ("race-outside-" + self.ws.name[-6:])
        outside.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        executor = FileToolExecutor(self.ws)
        armed = {"on": False}
        def plant_junction(path):
            if armed["on"] and path.name.endswith("race.txt") and not path.exists():
                import _winapi
                _winapi.CreateJunction(str(outside), str(path))
        executor.race_hook = plant_junction
        armed["on"] = True
        result = executor.execute(stage_request("task_race00000001", "out/race.txt", "x"))
        armed["on"] = False
        self.assertEqual(result["status"], "denied_by_policy")
        self.assertTrue(
            "link/junction" in (result["stderr"] or "") or "containment" in (result["stderr"] or ""),
            result["stderr"])
        # Cleanup the planted junction (refused before open, but the link exists).
        planted_dir = self.ws / "_staging" / "task_race00000001"
        leftovers = list(planted_dir.iterdir()) if planted_dir.is_dir() else []
        for planted in leftovers:
            if planted.exists() and os.path.islink(planted):
                os.rmdir(planted)
        # The junction is cleaned up and the outside directory stays empty of our file.
        junction = self.ws / "out" / "race.txt"
        if junction.exists() and os.path.islink(junction):
            os.rmdir(junction)
        self.assertEqual(os.listdir(outside), [])

    def test_duplicate_request_rejected(self):
        # The executor is stateless per request; duplicate/parameter-substitution
        # protection lives in the storage control layer (record_tool_start).
        req = stage_request("task_dupl0000001", "out/dup.txt", "x")
        first = self.executor.execute(req)
        self.assertEqual(first["status"], "success")
        second = self.executor.execute(req)
        self.assertEqual(second["status"], "success")
        from src.storage import TaskStorage
        storage = TaskStorage(":memory:")
        self.addCleanup(storage.close)
        storage.create_task(make_file_task("task_dupl0000001", self.ws))
        for state in ("classified", "planning", "executing"):
            storage.transition_task_state("task_dupl0000001", state)
        self.assertTrue(storage.record_tool_start(req, has_side_effect=True))
        with self.assertRaises(ValueError):
            storage.record_tool_start(req, has_side_effect=True)

    def test_unknown_tool_refused(self):
        req = make_tool_request("task_unkn0000001", tool_name="arbitrary_shell",
                                parameters={"command": "dir"})
        result = self.executor.execute(req)
        self.assertEqual(result["status"], "denied_by_policy")


class TestPhase4FileLoop(unittest.TestCase):
    """Orchestrator-level staged write -> review -> apply chain (offline)."""

    def setUp(self):
        self.ws = make_ws("loop-" + uuid.uuid4().hex[:8])
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def _expected(self):
        return json.dumps([{"region": "a", "sales": 1}], ensure_ascii=False)

    def _worker(self, contents, task_id):
        reqs = [[stage_request(task_id, "out/report.json", c)]
                for c in contents]
        return ScriptedWorkerAdapter(tool_requests_by_turn=reqs)

    def test_normal_stage_review_apply_succeeds(self):
        expected = self._expected()
        worker = self._worker([expected], "task_p4loop000001")
        reviewer = ScriptedReviewerAdapter(mode="pass")
        orch = make_orch(self, ws=self.ws, worker=worker, reviewer=reviewer,
                         evaluator=file_write_acceptance_evaluator(expected, "out/report.json"))
        orch.submit_task(make_file_task("task_p4loop000001", self.ws))
        result = orch.run_task("task_p4loop000001")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["review_verdict"], "pass")
        # Real file written by the APPLY step (not by the worker)
        self.assertEqual((self.ws / "out" / "report.json").read_text(encoding="utf-8"), expected)
        # Apply recorded as evidence and tool execution
        self.assertTrue(any("apply_staged_write:applied" in e for e in result["tool_execution_evidence"]))
        apply_execs = [t for t in orch.storage.list_tool_executions("task_p4loop000001")
                       if t["tool_name"] == "apply_staged_write"]
        self.assertEqual(len(apply_execs), 1)
        self.assertEqual(apply_execs[0]["status"], "success")
        # Review snapshots covered the STAGED file (artifact ref is the staged path)
        binding = orch.storage.list_review_bindings("task_p4loop000001")[0]
        staged_ref = list(binding["artifacts"])[0]
        self.assertIn("_staging", staged_ref)

    def test_apply_refuses_tampered_staged_content(self):
        """Staged content tampered AFTER review must never be applied."""
        expected = self._expected()
        worker = self._worker([expected], "task_p4tam0000001")
        reviewer = ScriptedReviewerAdapter(mode="pass")
        tool_executor = FileToolExecutor(self.ws)
        orch = make_orch(self, ws=self.ws, worker=worker, reviewer=reviewer,
                         tool_executor=tool_executor,
                         evaluator=file_write_acceptance_evaluator(expected, "out/report.json"))
        orch.submit_task(make_file_task("task_p4tam0000001", self.ws))
        # Tamper the staged file between staging and apply (review still passes
        # because the tamper happens after the review snapshot via reviewer seam).
        # Tamper the staged file at the LAST possible moment: after validate_review
        # and before the apply reads it (wrap the executor's apply entry).
        original_apply = tool_executor.apply_staged
        def tamper_then_apply(*args, **kwargs):
            staged = Path(kwargs["staged_path"])
            staged.write_text("tampered staged content", encoding="utf-8")
            return original_apply(*args, **kwargs)
        tool_executor.apply_staged = tamper_then_apply
        result = orch.run_task("task_p4tam0000001")
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("staged content sha", result["error_info"])
        # The target was never created with the tampered content.
        self.assertFalse((self.ws / "out" / "report.json").exists())

    def test_review_rejection_blocks_apply(self):
        expected = self._expected()
        worker = self._worker([expected], "task_p4rej0000001")
        reviewer = ScriptedReviewerAdapter(mode="revise_then_pass")
        orch = make_orch(self, ws=self.ws, worker=worker, reviewer=reviewer,
                         evaluator=file_write_acceptance_evaluator(expected, "out/report.json"))
        orch.submit_task(make_file_task("task_p4rej0000001", self.ws))
        # Reviewer revises the FIRST staged write: apply must NOT have happened yet.
        self.assertFalse((self.ws / "out" / "report.json").exists())
        result = orch.run_task("task_p4rej0000001")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["_runtime"]["repair_count"], 1)
        # After repair the file is applied exactly once with the reviewed content.
        self.assertEqual((self.ws / "out" / "report.json").read_text(encoding="utf-8"), expected)

    def test_target_changed_between_review_and_apply(self):
        expected = self._expected()
        worker = self._worker([expected], "task_p4tgt0000001")
        # A concurrent writer changes the target AFTER the review snapshot but
        # BEFORE the apply (deterministic seam inside the reviewer wrapper).
        reviewer = ScriptedReviewerAdapter(mode="pass")
        original_review = reviewer.invoke_structured
        def concurrent_writer(*args, **kwargs):
            response = original_review(*args, **kwargs)
            (self.ws / "out" / "report.json").write_text("concurrent edit", encoding="utf-8")
            return response
        reviewer.invoke_structured = concurrent_writer
        orch = make_orch(self, ws=self.ws, worker=worker, reviewer=reviewer,
                         evaluator=file_write_acceptance_evaluator(expected, "out/report.json"))
        orch.submit_task(make_file_task("task_p4tgt0000001", self.ws))
        result = orch.run_task("task_p4tgt0000001")
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("target changed since review", result["error_info"])
        # The concurrent edit must NOT be overwritten by the stale apply.
        self.assertEqual((self.ws / "out" / "report.json").read_text(encoding="utf-8"), "concurrent edit")

    def test_parameter_substitution_rejected(self):
        """Parameter substitution: a recorded request_id reused with changed
        parameters is rejected by the control layer before any execution."""
        from src.storage import TaskStorage
        storage = TaskStorage(":memory:")
        self.addCleanup(storage.close)
        tid = "task_p4sub0000001"
        storage.create_task(make_file_task(tid, self.ws))
        for state in ("classified", "planning", "executing"):
            storage.transition_task_state(tid, state)
        original = stage_request(tid, "out/report.json", self._expected())
        self.assertTrue(storage.record_tool_start(original, has_side_effect=True))
        substituted = dict(original)
        substituted["parameters"] = {"path": "out/other.json", "content": "different"}
        with self.assertRaises(ValueError):
            storage.record_tool_start(substituted, has_side_effect=True)

    def test_write_interruption_recovery_drill(self):
        expected = self._expected()
        worker = self._worker([expected], "task_p4drill00001")
        reviewer = ScriptedReviewerAdapter(mode="pass")
        tool_executor = FileToolExecutor(self.ws)
        orch = make_orch(self, ws=self.ws, worker=worker, reviewer=reviewer,
                         tool_executor=tool_executor,
                         evaluator=file_write_acceptance_evaluator(expected, "out/report.json"))
        # Drill 1: interrupt after backup, before replace -> not_applied -> recover.
        phases = {"fired": False}
        def interrupt_after_backup(phase):
            if phase == "backed_up" and not phases["fired"]:
                phases["fired"] = True
                raise SimulatedInterruptionError("drill: crash after backup")
        tool_executor.fault_injector = interrupt_after_backup
        orch.submit_task(make_file_task("task_p4drill00001", self.ws))
        result = orch.run_task("task_p4drill00001")
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue((self.ws / "out" / "report.json").exists())
        self.assertEqual((self.ws / "out" / "report.json").read_text(encoding="utf-8"), expected)
        # Recovery classified as not_applied and re-applied (storage audit events).
        storage_events = [e["event_type"] for e in orch.list_task_events("task_p4drill00001")]
        self.assertIn("apply_recovered_not_applied", storage_events)
        executor_kinds = [e["kind"] for e in tool_executor.events]
        self.assertIn("recover_not_applied_confirmed", executor_kinds)
        self.assertIn("apply_confirmed", executor_kinds)
        # Original pre-state: the file did not exist before, so nothing else was damaged.
        self.assertFalse((self.ws / "out" / "report.json.jh_tmp").exists())

    def test_apply_uncertain_enters_needs_attention(self):
        expected = self._expected()
        worker = self._worker([expected], "task_p4unc0000001")
        reviewer = ScriptedReviewerAdapter(mode="pass")
        tool_executor = FileToolExecutor(self.ws)
        orch = make_orch(self, ws=self.ws, worker=worker, reviewer=reviewer,
                         tool_executor=tool_executor,
                         evaluator=file_write_acceptance_evaluator(expected, "out/report.json"))
        # Drill 2: after replace, tamper the target so the read-back cannot confirm.
        def tamper_after_replace(phase):
            if phase == "replaced":
                (self.ws / "out" / "report.json").write_text("tampered-unknown-state", encoding="utf-8")
        tool_executor.fault_injector = tamper_after_replace
        orch.submit_task(make_file_task("task_p4unc0000001", self.ws))
        result = orch.run_task("task_p4unc0000001")
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("uncertain", result["error_info"].lower())
        # Tampered content preserved as evidence; no blind replay.
        self.assertEqual((self.ws / "out" / "report.json").read_text(encoding="utf-8"),
                         "tampered-unknown-state")

    def test_out_of_scope_target_refused(self):
        expected = self._expected()
        worker = self._worker([expected], "task_p4tgt0000001")
        orch = make_orch(self, ws=self.ws, worker=worker,
                         evaluator=file_write_acceptance_evaluator(expected, "out/report.json"))
        task = make_file_task("task_p4scope00001", self.ws, target="outside/secret.txt")
        task["context"]["target_files"] = ["outside/secret.txt"]
        orch.submit_task(task)
        result = orch.run_task("task_p4scope00001")
        # Outside-workspace target must never be created.
        self.assertFalse((self.ws / "outside").exists())

    def test_real_worker_tool_plan_correlation(self):
        expected = self._expected()
        worker = self._worker([expected], "task_p4plan000001")
        reviewer = ScriptedReviewerAdapter(mode="pass")
        orch = make_orch(self, ws=self.ws, worker=worker, reviewer=reviewer,
                         evaluator=file_write_acceptance_evaluator(expected, "out/report.json"))
        orch.submit_task(make_file_task("task_p4plan000001", self.ws))
        result = orch.run_task("task_p4plan000001")
        self.assertEqual(result["status"], "succeeded")
        # The tool plan was proposed through the structured protocol (ToolPlanSchemaV1)
        self.assertEqual(worker.generation_contexts[0]["attempt_kind"], "initial")
        # And the tool plan call is registered as a model attempt with usage
        attempts = orch.storage.list_model_attempts("task_p4plan000001")
        self.assertEqual([a["actor_role"] for a in attempts], ["worker", "worker", "reviewer"])

    def test_arch01_staging_junction_does_not_truncate_outside_file(self):
        """ARCH-01: if _staging is a junction to outside, stage_write refuses BEFORE truncating victim."""
        outside = self.ws.parent / ("arch01-outside-" + self.ws.name[-6:])
        outside.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        victim = outside / "report.json"
        victim.write_text("KEEP_OUTSIDE", encoding="utf-8")
        link = self.ws / "_staging"
        import _winapi
        _winapi.CreateJunction(str(outside), str(link))
        self.addCleanup(lambda: os.path.lexists(str(link)) and os.rmdir(link))
        executor = FileToolExecutor(self.ws)
        with self.assertRaises(PathBoundaryError):
            executor.stage_write("task_arch01", target_path="out/report.json", content="OVERWRITTEN")
        self.assertEqual(victim.read_text(encoding="utf-8"), "KEEP_OUTSIDE")

    def test_arch02_neighbor_temp_file_preserved(self):
        """ARCH-02: neighbor file main.txt.jh_tmp must not be overwritten or deleted by apply_staged."""
        target = self.ws / "out" / "main.txt"
        target.write_text("ORIGINAL", encoding="utf-8")
        neighbor = self.ws / "out" / "main.txt.jh_tmp"
        neighbor.write_text("UNAUTHORIZED_NEIGHBOR", encoding="utf-8")
        executor = FileToolExecutor(self.ws)
        st = executor.stage_write("task_arch02", target_path="out/main.txt", content="CANDIDATE")
        ap = executor.apply_staged(
            "task_arch02", target_path="out/main.txt", staged_path=st["staged_path"],
            content_sha256=st["content_sha256"], target_pre_sha256=st["target_pre_sha256"],
            authorized_files={"out/main.txt"},
        )
        self.assertTrue(ap["applied"])
        self.assertTrue(neighbor.exists())
        self.assertEqual(neighbor.read_text(encoding="utf-8"), "UNAUTHORIZED_NEIGHBOR")
        self.assertEqual(target.read_text(encoding="utf-8"), "CANDIDATE")

    def test_arch03_concurrent_target_edit_during_backed_up_preserved(self):
        """ARCH-03: concurrent edit after initial check (during backed_up) must not be overwritten."""
        target = self.ws / "out" / "main.txt"
        target.write_text("ORIGINAL", encoding="utf-8")
        executor = FileToolExecutor(self.ws)
        st = executor.stage_write("task_arch03", target_path="out/main.txt", content="CANDIDATE_2")
        executor.fault_injector = lambda phase: (
            target.write_text("CONCURRENT_UPDATE", encoding="utf-8") if phase == "backed_up" else None
        )
        with self.assertRaises(PathBoundaryError):
            executor.apply_staged(
                "task_arch03", target_path="out/main.txt", staged_path=st["staged_path"],
                content_sha256=st["content_sha256"], target_pre_sha256=st["target_pre_sha256"],
                authorized_files={"out/main.txt"},
            )
        self.assertEqual(target.read_text(encoding="utf-8"), "CONCURRENT_UPDATE")

    def test_arch04_parent_swapped_to_junction_before_commit_refused(self):
        """ARCH-04: if target parent is swapped to a junction at temp_written, commit refuses without touching outside."""
        outside = self.ws.parent / ("arch04-outside-" + self.ws.name[-6:])
        outside.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        inside_target = self.ws / "out" / "main.txt"
        inside_target.write_text("INSIDE_ORIGINAL", encoding="utf-8")
        outside_target = outside / "main.txt"
        outside_target.write_text("OUTSIDE_ORIGINAL", encoding="utf-8")
        executor = FileToolExecutor(self.ws)
        st = executor.stage_write("task_arch04", target_path="out/main.txt", content="CANDIDATE_REPLACE")

        def swap_parent(phase):
            if phase == "temp_written":
                os.rename(self.ws / "out", self.ws / "out_original")
                import _winapi
                _winapi.CreateJunction(str(outside), str(self.ws / "out"))

        executor.fault_injector = swap_parent
        self.addCleanup(lambda: os.path.lexists(str(self.ws / "out")) and _is_reparse(self.ws / "out") and os.rmdir(self.ws / "out"))
        with self.assertRaises(PathBoundaryError):
            executor.apply_staged(
                "task_arch04", target_path="out/main.txt", staged_path=st["staged_path"],
                content_sha256=st["content_sha256"], target_pre_sha256=st["target_pre_sha256"],
                authorized_files={"out/main.txt"},
            )
        self.assertEqual(outside_target.read_text(encoding="utf-8"), "OUTSIDE_ORIGINAL")
        self.assertEqual((self.ws / "out_original" / "main.txt").read_text(encoding="utf-8"), "INSIDE_ORIGINAL")

    def test_arch05_reviewed_content_mismatch_blocked_at_snapshot_and_apply(self):
        """ARCH-05: staged content swapped before review (and restored before apply) must fail closed."""
        unreviewed = '[{"region":"a","sales":666}]'
        reviewed = '[{"region":"a","sales":1}]'
        executor = FileToolExecutor(self.ws)
        orig_stage = executor.stage_write

        def swap_before_review(*a, **kw):
            r = orig_stage(*a, **kw)
            Path(r["staged_path"]).write_text(reviewed, encoding="utf-8")
            return r

        executor.stage_write = swap_before_review
        worker = self._worker([unreviewed], "task_arch05000001")
        reviewer = ScriptedReviewerAdapter(mode="pass")
        orch = make_orch(self, ws=self.ws, worker=worker, reviewer=reviewer,
                         tool_executor=executor,
                         evaluator=file_write_acceptance_evaluator(reviewed, "out/report.json"))
        orch.submit_task(make_file_task("task_arch05000001", self.ws))
        result = orch.run_task("task_arch05000001")
        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(reviewer.call_count, 0)
        self.assertFalse((self.ws / "out" / "report.json").exists())

    def test_junction_workspace_root_rejected(self):
        """R1: a junction passed as workspace_root is rejected before resolve()."""
        real = self.ws.parent / ("r1-real-" + self.ws.name[-6:])
        real.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, real, ignore_errors=True)
        link = self.ws.parent / ("r1-link-" + self.ws.name[-6:])
        import _winapi
        _winapi.CreateJunction(str(real), str(link))
        self.addCleanup(lambda: os.path.lexists(str(link)) and os.rmdir(link))
        with self.assertRaises(PathBoundaryError):
            FileToolExecutor(link)

    def test_same_basename_targets_get_distinct_staged_files(self):
        """H1: two targets sharing a basename must not overwrite each other's staged bytes."""
        (self.ws / "out" / "sub").mkdir(parents=True, exist_ok=True)
        executor = FileToolExecutor(self.ws)
        first = executor.stage_write("task_h1dup0001", target_path="out/dup.txt", content="FIRST")
        second = executor.stage_write("task_h1dup0001", target_path="out/sub/dup.txt", content="SECOND")
        self.assertNotEqual(first["staged_path"], second["staged_path"])
        self.assertEqual(Path(first["staged_path"]).read_text(encoding="utf-8"), "FIRST")
        self.assertEqual(Path(second["staged_path"]).read_text(encoding="utf-8"), "SECOND")

    def test_stale_turn_staged_write_does_not_block_current_apply(self):
        """G2-high: a historical stage_write dropped from the current reviewed set is ignored, not fatal."""
        expected = self._expected()
        executor = FileToolExecutor(self.ws)
        # Turn 1 stages content the reviewer rejects; turn 2 (repair) stages the
        # good content. The turn-1 record stays in history but is not reviewed.
        stale = '{"stale":true}'
        worker = ScriptedWorkerAdapter(tool_requests_by_turn=[
            [stage_request("task_g2hist0001", "out/report.json", stale)],
            [stage_request("task_g2hist0001", "out/report.json", expected)],
        ])
        reviewer = ScriptedReviewerAdapter(mode="revise_then_pass")
        orch = make_orch(self, ws=self.ws, worker=worker, reviewer=reviewer,
                         tool_executor=executor,
                         evaluator=file_write_acceptance_evaluator(expected, "out/report.json"))
        orch.submit_task(make_file_task("task_g2hist0001", self.ws))
        result = orch.run_task("task_g2hist0001")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual((self.ws / "out" / "report.json").read_text(encoding="utf-8"), expected)
        stages = [t for t in orch.storage.list_tool_executions("task_g2hist0001")
                  if t["tool_name"] == "stage_write" and t["status"] == "success"]
        self.assertEqual(len(stages), 2)  # history kept, current turn applied

    def test_read_file_does_not_pollute_reviewed_artifact_set(self):
        """G2-F2: with FileToolExecutor, read_file results must not enter artifact_refs."""
        (self.ws / "out" / "existing.txt").write_text("hello", encoding="utf-8")
        expected = self._expected()
        worker = ScriptedWorkerAdapter(tool_requests_by_turn=[[
            make_tool_request("task_g2f20001", tool_name="read_file",
                              parameters={"path": "out/existing.txt"}),
            stage_request("task_g2f20001", "out/report.json", expected),
        ]])
        orch = make_orch(self, ws=self.ws, worker=worker,
                         reviewer=ScriptedReviewerAdapter(mode="pass"),
                         evaluator=file_write_acceptance_evaluator(expected, "out/report.json"))
        orch.submit_task(make_file_task("task_g2f20001", self.ws))
        result = orch.run_task("task_g2f20001")
        self.assertEqual(result["status"], "succeeded")
        for ref in result["artifact_refs"]:
            self.assertIn("_staging", ref)

    def test_recovered_applied_hash_must_match_reviewed_snapshot(self):
        """G2-F2b: recovered 'applied' bytes differing from the reviewed sha fail closed."""
        expected = self._expected()
        executor = FileToolExecutor(self.ws)
        orig_recover = executor.recover_apply

        def lying_recover(*a, **kw):
            verdict = orig_recover(*a, **kw)
            if verdict["state"] == "not_applied":
                # Simulate a crash window where an outsider wrote unexpected bytes.
                (self.ws / "out" / "report.json").write_text("OUTSIDER_BYTES", encoding="utf-8")
                return {"state": "applied",
                        "target_sha256": _sha256_of("OUTSIDER_BYTES")}
            return verdict

        def interrupt_once(phase):
            if phase == "temp_written" and not interrupt_once.fired:
                interrupt_once.fired = True
                raise SimulatedInterruptionError("drill: crash at temp_written")
        interrupt_once.fired = False
        executor.fault_injector = interrupt_once
        executor.recover_apply = lying_recover
        worker = self._worker([expected], "task_g2rec00001")
        orch = make_orch(self, ws=self.ws, worker=worker,
                         reviewer=ScriptedReviewerAdapter(mode="pass"),
                         tool_executor=executor,
                         evaluator=file_write_acceptance_evaluator(expected, "out/report.json"))
        orch.submit_task(make_file_task("task_g2rec00001", self.ws))
        result = orch.run_task("task_g2rec00001")
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("hash mismatch", (result["error_info"] or "").lower())

    def test_backup_uses_verified_read_before_any_target_write(self):
        """F1: backup creation refuses via verified open when the target escapes mid-apply."""
        (self.ws / "out" / "victim.txt").write_text("V1", encoding="utf-8")
        outside = self.ws.parent / ("f1-outside-" + self.ws.name[-6:])
        outside.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        (outside / "victim.txt").write_text("OUTSIDE", encoding="utf-8")
        executor = FileToolExecutor(self.ws)
        st = executor.stage_write("task_f1000001", target_path="out/victim.txt", content="V2")

        def swap_before_backup(phase):
            if phase == "apply_start":
                os.rename(self.ws / "out", self.ws / "out_moved")
                import _winapi
                _winapi.CreateJunction(str(outside), str(self.ws / "out"))
        executor.fault_injector = swap_before_backup
        self.addCleanup(lambda: os.path.lexists(str(self.ws / "out")) and _is_reparse(self.ws / "out") and os.rmdir(self.ws / "out"))
        with self.assertRaises(PathBoundaryError):
            executor.apply_staged(
                "task_f1000001", target_path="out/victim.txt", staged_path=st["staged_path"],
                content_sha256=st["content_sha256"], target_pre_sha256=st["target_pre_sha256"])
        self.assertEqual((outside / "victim.txt").read_text(encoding="utf-8"), "OUTSIDE")
        self.assertEqual((self.ws / "out_moved" / "victim.txt").read_text(encoding="utf-8"), "V1")


def _is_reparse(p: Path) -> bool:
    try:
        return bool(getattr(os.lstat(p), "st_file_attributes", 0) & 0x400) or os.path.islink(p)
    except OSError:
        return False


def _sha256_of(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    unittest.main()
