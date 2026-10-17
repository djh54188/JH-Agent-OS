"""CLI + Skill entry wiring for host capture plan/register/query.

Every session log below is SYNTHETIC test data (thread ids prefixed
"test-bind-"); nothing here proves a real host executed anything. Tests
drive the real CLI entry points (in-process plus one cross-process round
trip) and assert JSON outputs, exit codes, persisted rows, and read-only
guarantees. Production complete stays closed.
"""
import io
import json
import os
import subprocess
import sys
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from test_jh006_capture_binding import _BindCase
from test_jh006_code_delivery import _run_cli
import src.capture_binding as cb
from src.capture_binding import capture_invocation_for_delivery
from src.storage import TaskStorage


class _EntryCase(_BindCase):
    def _prepared_applied(self, use_file_db=True):
        orch, tid, delivery = self._prepared_production(use_file_db=use_file_db)
        self._fixture_apply()
        return orch, tid, delivery

    def _cli(self, *args):
        return _run_cli(["--db", str(self.db), "--workspace", str(self.ws), *args])

    def _xproc(self, *args, env=None):
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", "-m", "src.cli",
             "--db", str(self.db), "--workspace", str(self.ws), *args],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
            timeout=180, env=env)
        return proc.returncode, proc.stdout, proc.stderr


class TestHostEntryPlan(_EntryCase):
    def test_plan_matches_direct_invocation(self):
        orch, tid, delivery = self._prepared_applied()
        code, out = self._cli("plan-host-check", tid, delivery["delivery_id"])
        body = json.loads(out)
        self.assertEqual(code, 0, out)
        self.assertTrue(body["command_ok"])
        self.assertTrue(body["read_only"])
        self.assertEqual(body["workdir"], str(self.ws))
        expected = capture_invocation_for_delivery(
            orch.storage, tid, delivery["delivery_id"])
        self.assertEqual(body["invocation"], expected)
        self.assertIn("plan only", body["note"])

    def test_plan_unknown_task(self):
        orch = self._orch(use_file_db=True)
        orch.storage.close()
        code, out = self._cli("plan-host-check", "task_missing0001", "dlv_x")
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertFalse(body["command_ok"])
        self.assertTrue(body["read_only"])

    def test_plan_non_waiting_task(self):
        orch = self._orch(use_file_db=True)
        payload = self._payload()
        orch.submit_task(payload)
        orch.storage.close()
        code, out = self._cli("plan-host-check", payload["task_id"], "dlv_x")
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertIn("status-mismatch", body["error"])

    def test_plan_stale_artifact(self):
        orch, tid, delivery = self._prepared_applied()
        orch.artifact_store.save_version(
            tid,
            envelope={"schema_version": "1.0.0", "artifact_id": "art_changed0003",
                      "task_id": tid, "artifact_type": "patch_text",
                      "content": "changed", "summary": "v2 probe"},
            version=2, generation_attempt_id=None)
        orch.storage.close()
        code, out = self._cli("plan-host-check", tid, delivery["delivery_id"])
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertIn("artifact-changed", body["error"])

    def test_plan_missing_db_creates_nothing(self):
        ghost = self.case / "ghost.sqlite3"
        code, out = _run_cli(
            ["--db", str(ghost), "--workspace", str(self.ws),
             "plan-host-check", "task_missing0001", "dlv_x"])
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertFalse(body["command_ok"])
        self.assertTrue(body["read_only"])
        self.assertFalse(ghost.exists())

    def test_plan_loads_no_models(self):
        orch, tid, delivery = self._prepared_applied()
        orch.storage.close()
        with patch("src.cli.create_orchestrator", side_effect=AssertionError("no model assembly")):
            code, out = self._cli("plan-host-check", tid, delivery["delivery_id"])
        self.assertEqual(code, 0, out)
        self.assertTrue(json.loads(out)["command_ok"])

    def test_plan_wrong_delivery(self):
        orch, tid, delivery = self._prepared_applied()
        orch.storage.close()
        code, out = self._cli("plan-host-check", tid, "dlv_other")
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertIn("delivery-mismatch", body["error"])


class TestHostEntryRegister(_EntryCase):
    def _ids(self):
        from test_jh006_capture_binding import THREAD, ITEM, CALL
        return THREAD, ITEM, CALL

    def test_register_persists_binding_and_event(self):
        orch, tid, delivery = self._prepared_applied()
        store = self._live_store()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        orch.storage.close()
        thread, item, call = self._ids()
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            code, out = self._cli("register-host-capture", tid, delivery["delivery_id"],
                                  str(archive), thread, item, call)
        body = json.loads(out)
        self.assertEqual(code, 0, out)
        self.assertTrue(body["command_ok"])
        self.assertFalse(body["read_only"])
        self.assertTrue(body["binding_id"].startswith("cb_"))
        self.assertEqual(
            (body["thread_id"], body["item_id"], body["call_id"]),
            (thread, item, call))
        probe = TaskStorage(str(self.db), read_only=True)
        try:
            row = probe.get_capture_binding(tid, delivery["delivery_id"])
            self.assertEqual(row["binding_id"], body["binding_id"])
            kinds = [e["event_type"] for e in probe.list_events(tid)]
            self.assertIn("capture_binding_registered", kinds)
            task = probe.get_task(tid)
            self.assertEqual(task["status"], "reviewing")
            self.assertEqual(task["metadata"]["_delivery"], delivery)
        finally:
            probe.close()

    def test_register_unknown_task(self):
        orch = self._orch(use_file_db=True)
        orch.storage.close()
        code, out = self._cli("register-host-capture", "task_missing0001", "dlv_x",
                              str(self.case), "t", "i", "c")
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertFalse(body["command_ok"])

    def test_register_wrong_delivery(self):
        orch, tid, delivery = self._prepared_applied()
        orch.storage.close()
        code, out = self._cli("register-host-capture", tid, "dlv_other",
                              str(self.case), "t", "i", "c")
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertIn("delivery-mismatch", body["error"])

    def test_register_missing_archive(self):
        orch, tid, delivery = self._prepared_applied()
        orch.storage.close()
        code, out = self._cli("register-host-capture", tid, delivery["delivery_id"],
                              str(self.case / "no-such-dir"), "t", "i", "c")
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertIn("archive-missing", body["error"])

    def test_register_tampered_archive(self):
        orch, tid, delivery = self._prepared_applied()
        store = self._live_store()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        manifest_path = archive / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["event"]["output_digest"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        orch.storage.close()
        thread, item, call = self._ids()
        code, out = self._cli("register-host-capture", tid, delivery["delivery_id"],
                              str(archive), thread, item, call)
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertIn("archive-tampered", body["error"])

    def test_register_stale_review(self):
        orch, tid, delivery = self._prepared_applied()
        aid = orch.storage.begin_attempt(tid, "reviewer", {})
        orch.storage.complete_attempt(tid, aid, {"review_id": "rev_newer0001"}, 10)
        orch.storage.record_event(task_id=tid, event_type="model_response_source",
                                  from_state="reviewing", to_state="reviewing",
                                  actor_role="orchestrator",
                                  details={"attempt_id": aid, "role": "reviewer",
                                           "adapter_class": "ProdAdapter",
                                           "model_id": "prod", "is_mock": False})
        orch.storage.close()
        code, out = self._cli("register-host-capture", tid, delivery["delivery_id"],
                              str(self.case), "t", "i", "c")
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertIn("review-stale", body["error"])

    def test_register_unknown_budget_blocked(self):
        orch, tid, delivery = self._prepared_applied()
        orch.storage.record_usage(tid, actor_role="worker", tokens_used=None)
        orch.storage.close()
        code, out = self._cli("register-host-capture", tid, delivery["delivery_id"],
                              str(self.case), "t", "i", "c")
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertIn("register blocked", body["error"])
        self.assertIn("unknown_token_usage", body["blockers"])

    def test_register_blocked_by_pending_approval(self):
        orch, tid, delivery = self._prepared_applied()
        orch.storage.hold_request(tid, {"request_id": "treq_probe0001"})
        orch.storage.close()
        code, out = self._cli("register-host-capture", tid, delivery["delivery_id"],
                              str(self.case), "t", "i", "c")
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertIn("control-blocked", body["error"])

    def test_register_fixture_task_refused(self):
        orch = self._orch(use_file_db=True)
        payload = self._payload()
        tid = payload["task_id"]
        self._assemble_reviewing(orch, payload, stamp_source="fixture",
                                 with_source_event=False)
        task = orch.prepare_code_delivery(tid, 1)
        delivery_id = task["metadata"]["_delivery"]["delivery_id"]
        orch.storage.close()
        code, out = _run_cli(
            ["--db", str(self.db), "--workspace", str(self.ws), "--test-fixture-mode",
             "register-host-capture", tid, delivery_id, str(self.case), "t", "i", "c"])
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertIn("source-not-production", body["error"])

    def test_register_mode_swap_refused(self):
        orch, tid, delivery = self._prepared_applied()
        orch.storage.close()
        code, out = _run_cli(
            ["--db", str(self.db), "--workspace", str(self.ws), "--test-fixture-mode",
             "register-host-capture", tid, delivery["delivery_id"],
             str(self.case), "t", "i", "c"])
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertIn("swap refused", body["error"])

    def test_register_loads_no_models(self):
        orch, tid, delivery = self._prepared_applied()
        store = self._live_store()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        orch.storage.close()
        thread, item, call = self._ids()
        with patch("src.cli.create_orchestrator", side_effect=AssertionError("no model assembly")):
            with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
                code, out = self._cli("register-host-capture", tid, delivery["delivery_id"],
                                      str(archive), thread, item, call)
        self.assertEqual(code, 0, out)
        self.assertTrue(json.loads(out)["command_ok"])

    def test_register_rejects_unknown_flags(self):
        orch, tid, delivery = self._prepared_applied()
        orch.storage.close()
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            with self.assertRaises(SystemExit) as raised:
                from src.cli import main as cli_main
                cli_main(["--db", str(self.db), "--workspace", str(self.ws),
                          "register-host-capture", tid, delivery["delivery_id"],
                          str(self.case), "t", "i", "c", "--trusted=true"])
        self.assertEqual(raised.exception.code, 2)


class TestHostEntryQuery(_EntryCase):
    def _registered(self):
        orch, tid, delivery = self._prepared_applied()
        store = self._live_store()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        from test_jh006_capture_binding import THREAD, ITEM, CALL
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            code, out = self._cli("register-host-capture", tid, delivery["delivery_id"],
                                  str(archive), THREAD, ITEM, CALL)
        self.assertEqual(code, 0, out)
        orch.storage.close()
        return tid, delivery["delivery_id"], json.loads(out)["binding_id"]

    def test_query_bound_current(self):
        tid, dlv, binding_id = self._registered()
        code, out = self._cli("capture-binding-status", tid, dlv)
        body = json.loads(out)
        self.assertEqual(code, 0, out)
        self.assertTrue(body["command_ok"])
        self.assertTrue(body["read_only"])
        self.assertTrue(body["bound"])
        self.assertTrue(body["current"])
        self.assertEqual(body["reasons"], [])
        self.assertEqual(body["binding"]["binding_id"], binding_id)

    def test_query_unbound(self):
        orch, tid, delivery = self._prepared_applied()
        orch.storage.close()
        code, out = self._cli("capture-binding-status", tid, delivery["delivery_id"])
        body = json.loads(out)
        self.assertEqual(code, 0, out)
        self.assertTrue(body["command_ok"])
        self.assertFalse(body["bound"])
        self.assertFalse(body["current"])
        self.assertIn("no-binding", body["reasons"])
        self.assertIsNone(body["binding"])

    def test_query_after_cancel(self):
        tid, dlv, _ = self._registered()
        orch = self._orch(use_file_db=True)
        orch.cancel_task(tid, reason="query staleness probe")
        orch.storage.close()
        code, out = self._cli("capture-binding-status", tid, dlv)
        body = json.loads(out)
        self.assertEqual(code, 0, out)
        self.assertTrue(body["bound"])
        self.assertFalse(body["current"])
        self.assertIn("task-not-waiting", body["reasons"])

    def test_query_unknown_task(self):
        orch = self._orch(use_file_db=True)
        orch.storage.close()
        code, out = self._cli("capture-binding-status", "task_missing0001", "dlv_x")
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertFalse(body["command_ok"])

    def test_query_missing_db_creates_nothing(self):
        ghost = self.case / "ghost.sqlite3"
        code, out = _run_cli(
            ["--db", str(ghost), "--workspace", str(self.ws),
             "capture-binding-status", "task_missing0001", "dlv_x"])
        body = json.loads(out)
        self.assertEqual(code, 1)
        self.assertFalse(body["command_ok"])
        self.assertTrue(body["read_only"])
        self.assertFalse(ghost.exists())

    def test_query_loads_no_models(self):
        tid, dlv, _ = self._registered()
        with patch("src.cli.create_orchestrator", side_effect=AssertionError("no model assembly")):
            code, out = self._cli("capture-binding-status", tid, dlv)
        self.assertEqual(code, 0, out)
        self.assertTrue(json.loads(out)["command_ok"])


class TestHostEntryCrossProcess(_EntryCase):
    def test_plan_register_query_across_processes(self):
        from test_jh006_capture_binding import THREAD, ITEM, CALL
        orch, tid, delivery = self._prepared_applied()
        dlv = delivery["delivery_id"]
        fake_home = self.case / "fake-home"
        store = fake_home / ".codex" / "sessions"
        store.mkdir(parents=True)
        env = dict(os.environ, HOME=str(fake_home), USERPROFILE=str(fake_home))
        code, out, err = self._xproc("plan-host-check", tid, dlv, env=env)
        self.assertEqual(code, 0, out + err)
        plan = json.loads(out)["invocation"]
        archive, _, _, _ = self._archive_for(
            tid, delivery, live_store=store, argv=list(plan["argv"]),
            call_script=plan["call_input"])
        manifest_path = archive / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["apply_attempt_id"] = plan["apply_attempt_id"]
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        orch.storage.close()
        code, out, err = self._xproc("register-host-capture", tid, dlv,
                                     str(archive), THREAD, ITEM, CALL, env=env)
        self.assertEqual(code, 0, out + err)
        binding_id = json.loads(out)["binding_id"]
        code, out, err = self._xproc("capture-binding-status", tid, dlv, env=env)
        body = json.loads(out)
        self.assertEqual(code, 0, out + err)
        self.assertTrue(body["current"])
        self.assertEqual(body["binding"]["binding_id"], binding_id)
        self.assertEqual(json.loads(body["binding"]["capture_json"])["argv"], plan["argv"])


class TestHostEntrySurface(_EntryCase):
    def test_command_names_match_parser(self):
        from src.cli import HOST_ENTRY_COMMANDS, main as cli_main
        self.assertEqual(len(HOST_ENTRY_COMMANDS), 3)
        for name in HOST_ENTRY_COMMANDS:
            buf_out, buf_err = io.StringIO(), io.StringIO()
            with redirect_stdout(buf_out), redirect_stderr(buf_err):
                with self.assertRaises(SystemExit) as raised:
                    cli_main([name, "--help"])
            self.assertEqual(raised.exception.code, 0, name)


if __name__ == "__main__":
    unittest.main()
