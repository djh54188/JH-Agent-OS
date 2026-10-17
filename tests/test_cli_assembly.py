"""JH-015 / ADR-0003 CLI trusted-assembly regression tests (offline).

Covers the JH-004 read-only preparation already present in the working tree:
- status/events/artifact/summary never create directories, database files,
  migrations, or model invocations;
- missing database returns a structured failure without creating anything;
- submit/resume enforce the trusted workspace boundary and reject file_tools
  mode until JH-015 passes independent review;
- payloads cannot select mock profiles or enable mock mode in production mode;
- file_tools acceptance requires exactly one target and expected_json.
"""
import io
import json
import os
import shutil
import sys
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import cli as cli_mod
from src.cli import (
    TrustedAssemblyError,
    enforce_trusted_task_payload,
    main,
    resolve_trusted_evaluator,
)


def _run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


def _payload(task_id, ws, **over):
    base = {
        "schema_version": "1.0.0",
        "task_id": task_id,
        "title": "cli probe",
        "description": "cli probe",
        "acceptance_criteria": ["c1"],
        "priority": "high",
        "status": "created",
        "risk_assessment": {"risk_level": "read_only", "complexity": "low",
                            "uncertainty": "low", "risk_basis": "probe",
                            "routing_basis": "probe"},
        "budget": {"max_turns": 2, "max_tokens": 1000, "max_execution_time_seconds": 60},
        "context": {"workspace_root": str(ws), "target_files": []},
        "artifact_refs": [],
        "tool_execution_evidence": [],
        "review_verdict": None,
        "error_info": None,
        "assigned_worker": "gemini_worker",
        "assigned_reviewer": "muse_reviewer",
        "metadata": {"input_data": {"records": [{"region": "a", "sales": 1}]}},
        "created_at": "2026-10-12T00:00:00Z",
        "updated_at": "2026-10-12T00:00:00Z",
    }
    base.update(over)
    return base


def _seed_db(db_path, ws, task_id):
    from src.storage import TaskStorage
    storage = TaskStorage(db_path, read_only=False)
    try:
        storage.create_task(_payload(task_id, ws))
    finally:
        storage.close()


class TestCliReadOnlyAssembly(unittest.TestCase):

    def setUp(self):
        self.case = ROOT / "data" / "test-workspaces" / ("cli-" + uuid.uuid4().hex[:8])
        self.ws = self.case / "ws"
        self.ws.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.case, ignore_errors=True)
        self.db = self.case / "cli.sqlite3"

    def test_status_missing_db_creates_nothing(self):
        ghost_dir = self.case / "ghost-dir"
        ghost_db = ghost_dir / "ghost.sqlite3"
        code, out = _run_cli(["--db", str(ghost_db), "status", "task_nope000001"])
        self.assertEqual(code, 1)
        body = json.loads(out)
        self.assertFalse(body["command_ok"])
        self.assertTrue(body["read_only"])
        self.assertFalse(ghost_dir.exists())
        self.assertFalse(ghost_db.exists())

    def test_status_summary_events_artifact_are_read_only(self):
        tid = "task_cli0000001"
        _seed_db(self.db, self.ws, tid)
        before = self.db.stat().st_mtime_ns
        for cmd in ("status", "summary", "events", "artifact"):
            code, out = _run_cli(["--db", str(self.db), "status" if cmd == "status" else cmd,
                                  tid] if cmd != "status" else ["--db", str(self.db), "status", tid])
            self.assertEqual(code, 0, cmd)
            self.assertIn(tid, out)
        self.assertEqual(self.db.stat().st_mtime_ns, before)

    def test_read_only_commands_never_invoke_models(self):
        tid = "task_cli0000002"
        _seed_db(self.db, self.ws, tid)
        with patch.object(cli_mod, "create_orchestrator",
                          side_effect=AssertionError("model path touched")):
            for cmd in ("status", "summary", "events", "artifact"):
                code, _ = _run_cli(["--db", str(self.db), cmd, tid])
                self.assertEqual(code, 0, cmd)

    def test_submit_rejects_workspace_escape(self):
        evil = _payload("task_cli0000003", self.case / "elsewhere")
        task_file = self.case / "evil.json"
        task_file.write_text(json.dumps(evil), encoding="utf-8")
        code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                              "submit", str(task_file)])
        self.assertEqual(code, 1)
        self.assertIn("outside trusted CLI workspace", out)

    def test_submit_rejects_file_tools_until_jh015_review(self):
        file_payload = _payload(
            "task_cli0000004", self.ws,
            context={"workspace_root": str(self.ws), "target_files": ["out/report.json"]},
            metadata={"execution_mode": "file_tools",
                      "input_data": {"expected_json": [{"region": "a", "sales": 1}]}})
        task_file = self.case / "file.json"
        task_file.write_text(json.dumps(file_payload), encoding="utf-8")
        code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                              "--test-fixture-mode", "submit", str(task_file)])
        self.assertEqual(code, 1)
        self.assertIn("closed until JH-015 passes independent review", out)

    def test_production_payload_cannot_select_mock(self):
        for key, value in (("assigned_worker", "mock_worker"),
                           ("assigned_reviewer", "MockReviewer")):
            with self.subTest(key=key):
                with self.assertRaises(TrustedAssemblyError):
                    enforce_trusted_task_payload(
                        _payload("task_cli0000005", self.ws, **{key: value}),
                        trusted_workspace=self.ws, test_fixture_mode=False)
        with self.assertRaises(TrustedAssemblyError):
            enforce_trusted_task_payload(
                _payload("task_cli0000005", self.ws,
                         metadata={"use_mock": True,
                                   "input_data": {"records": []}}),
                trusted_workspace=self.ws, test_fixture_mode=False)

    def test_file_tools_evaluator_requires_single_target_and_expected(self):
        with self.assertRaises(TrustedAssemblyError):
            resolve_trusted_evaluator(
                _payload("t", self.ws,
                         context={"workspace_root": str(self.ws), "target_files": ["a", "b"]},
                         metadata={"execution_mode": "file_tools",
                                   "input_data": {"expected_json": []}}),
                allow_file_write_gate=True)
        with self.assertRaises(TrustedAssemblyError):
            resolve_trusted_evaluator(
                _payload("t", self.ws,
                         context={"workspace_root": str(self.ws), "target_files": ["a"]},
                         metadata={"execution_mode": "file_tools", "input_data": {}}),
                allow_file_write_gate=True)


if __name__ == "__main__":
    unittest.main()
