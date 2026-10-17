"""JH-019 application-interface tests: the core is usable without any GUI.

These tests drive `src.app_api.AppInterface` directly — no HTTP server, no
browser, no static assets — and pin the truthfulness rules of this batch:

* unsupported capabilities are reported as unsupported;
* every registered Agent session says connected=false / simulated=true;
* a missing database, an unknown task, an empty result and a corrupt draft file
  each have their own explicit shape;
* a saved goal is a marked DRAFT: no task is created, no Agent/model/tool runs.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.app_api import AppInterface, ApiError
from src.storage import TaskStorage

ROOT = Path(__file__).resolve().parents[1]


def _payload(task_id, ws, **over):
    base = {
        "schema_version": "1.0.0",
        "task_id": task_id,
        "title": "gui probe " + task_id,
        "description": "gui probe",
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


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = self.tmp / "gui.sqlite3"
        self.drafts = self.tmp / "drafts.json"

    def tearDown(self):
        self._tmp.cleanup()

    def seed(self, *task_ids, **over):
        storage = TaskStorage(self.db, read_only=False)
        try:
            for task_id in task_ids:
                storage.create_task(_payload(task_id, self.tmp, **over))
        finally:
            storage.close()

    def api(self, db=None, drafts=None):
        return AppInterface(db_path=db or self.db, drafts_path=drafts or self.drafts)


class TestCapabilitiesAreExplicit(_Base):
    def test_supported_and_unsupported_are_listed_separately(self):
        caps = self.api().capabilities()
        self.assertTrue(caps["ok"])
        self.assertTrue(caps["capabilities"]["workbench"]["supported"])
        for name in ("dispatch", "collect", "review_trigger"):
            self.assertFalse(caps["capabilities"][name]["supported"], name)
            self.assertIn("JH-020", caps["capabilities"][name]["reason"])
            self.assertIn(name, caps["unsupported"])

    def test_no_capability_claims_to_execute(self):
        caps = self.api().capabilities()
        self.assertIn("不触发", caps["note"])


class TestSessionsAreNeverFakeConnected(_Base):
    def test_every_session_is_simulated_and_disconnected(self):
        payload = self.api().list_sessions()
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(payload["count"], 1)
        for session in payload["sessions"]:
            self.assertIs(session["connected"], False, session["session_id"])
            self.assertIs(session["simulated"], True, session["session_id"])
            self.assertIn("未接入", session["note"])
            self.assertTrue(session["evidence"])

    def test_transport_names_the_static_registry(self):
        payload = self.api().list_sessions()
        self.assertIn("static-registry", payload["transport"])


class TestEmptyAndErrorStates(_Base):
    def test_missing_database_is_an_explicit_empty_state(self):
        payload = self.api(db=self.tmp / "absent.sqlite3").list_tasks()
        self.assertTrue(payload["ok"])                  # not a failure
        self.assertIs(payload["database_present"], False)
        self.assertEqual(payload["tasks"], [])
        self.assertIn("不存在", payload["note"])

    def test_empty_database_is_distinguishable_from_missing(self):
        self.seed()                                     # creates the file, no rows
        payload = self.api().list_tasks()
        self.assertIs(payload["database_present"], True)
        self.assertEqual(payload["tasks"], [])
        self.assertNotIn("不存在", payload.get("note", ""))

    def test_unknown_task_raises_task_not_found(self):
        self.seed("task_gui00000001")
        with self.assertRaises(ApiError) as ctx:
            self.api().task_detail("task_nope000001")
        self.assertEqual(ctx.exception.code, "task_not_found")
        self.assertEqual(ctx.exception.http_status, 404)

    def test_detail_on_missing_database_says_so(self):
        with self.assertRaises(ApiError) as ctx:
            self.api(db=self.tmp / "absent.sqlite3").task_detail("task_any0000001")
        self.assertEqual(ctx.exception.code, "database_missing")


class TestWorkbenchAndDetailReuseExistingQueries(_Base):
    def test_list_tasks_returns_recorded_rows_only(self):
        self.seed("task_gui00000001", "task_gui00000002")
        payload = self.api().list_tasks()
        ids = sorted(row["task_id"] for row in payload["tasks"])
        self.assertEqual(ids, ["task_gui00000001", "task_gui00000002"])
        for row in payload["tasks"]:
            self.assertEqual(set(row), {"task_id", "title", "status", "review_verdict", "updated_at"})

    def test_limit_is_respected(self):
        self.seed(*["task_gui0000000%d" % i for i in range(1, 4)])
        self.assertEqual(self.api().list_tasks(limit=2)["count"], 2)

    def test_task_detail_status_comes_from_the_record(self):
        self.seed("task_gui00000001")
        detail = self.api().task_detail("task_gui00000001")
        self.assertTrue(detail["ok"])
        self.assertEqual(detail["task_result"]["status"], "created")
        # task creation itself writes an audit event, so at least one exists
        self.assertGreaterEqual(detail["events_count"], 1)
        self.assertEqual(detail["artifact_versions"], [])

    def test_review_delivery_exposes_recorded_facts(self):
        self.seed("task_gui00000001")
        payload = self.api().review_delivery("task_gui00000001")
        self.assertEqual(payload["task_status"], "created")
        self.assertIsNone(payload["review_verdict"])
        self.assertEqual(payload["review_bindings_count"], 0)
        self.assertIn("delivery_scope", payload)


class TestGoalDraftIsInert(_Base):
    def test_draft_is_marked_and_persisted(self):
        saved = self.api().save_goal_draft({"title": "t", "goal": "g"})
        self.assertTrue(saved["ok"])
        draft = saved["draft"]
        self.assertIs(draft["draft"], True)
        self.assertEqual(draft["execution"], "not_triggered")
        self.assertEqual(draft["kind"], "jh019-goal-draft/v1")
        self.assertIn("不", draft["note"])
        listed = self.api().list_drafts()
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["drafts"][0]["draft_id"], draft["draft_id"])

    def test_draft_creates_no_task(self):
        self.seed()
        before = self.api().list_tasks()["count"]
        self.api().save_goal_draft({"title": "t", "goal": "g"})
        self.assertEqual(self.api().list_tasks()["count"], before)      # no task created
        self.assertEqual(self.api().list_drafts()["count"], 1)          # draft is separate

    def test_draft_refuses_to_execute(self):
        for forbidden in ("dispatch", "execute", "run", "start_task"):
            with self.assertRaises(ApiError) as ctx:
                self.api().save_goal_draft({"title": "t", "goal": "g", forbidden: True})
            self.assertEqual(ctx.exception.code, "draft_cannot_execute")
            self.assertEqual(ctx.exception.http_status, 409)

    def test_incomplete_and_malformed_drafts_are_distinct_errors(self):
        with self.assertRaises(ApiError) as ctx:
            self.api().save_goal_draft({"title": "only title"})
        self.assertEqual(ctx.exception.code, "draft_incomplete")
        with self.assertRaises(ApiError) as ctx:
            self.api().save_goal_draft("not an object")
        self.assertEqual(ctx.exception.code, "draft_malformed")

    def test_corrupt_draft_file_is_reported_and_not_overwritten(self):
        self.drafts.parent.mkdir(parents=True, exist_ok=True)
        self.drafts.write_text("{not json", encoding="utf-8")
        before = self.drafts.read_bytes()
        with self.assertRaises(ApiError) as ctx:
            self.api().list_drafts()
        self.assertEqual(ctx.exception.code, "drafts_unreadable")
        self.assertEqual(self.drafts.read_bytes(), before)              # untouched


class TestCoreHasNoExecutionPath(_Base):
    def test_app_api_never_reaches_the_orchestrator_or_adapters(self):
        source = (ROOT / "src" / "app_api.py").read_text(encoding="utf-8")
        for forbidden in ("orchestrator", "Orchestrator", "ModelAdapter",
                          "create_task(", "transition_task_state", "subprocess"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_app_api_reuses_the_existing_read_query(self):
        source = (ROOT / "src" / "app_api.py").read_text(encoding="utf-8")
        self.assertIn("from src.cli import build_task_summary", source)

    def test_core_works_without_importing_the_gui_transport(self):
        """A fresh interpreter that imports only the application interface never
        pulls in the HTTP transport, and the core still answers."""
        code = ("import sys; import src.app_api as api; "
                "print('src.gui_http' in sys.modules); "
                "print(api.AppInterface.__name__)")
        done = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", code],
                              cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        first = done.stdout.splitlines()[0]
        self.assertEqual(first, "False", "core import pulled in the transport")

    def test_core_answers_with_no_gui_module_loaded(self):
        self.seed("task_gui00000001")
        payload = self.api().list_tasks()               # works with no GUI at all
        self.assertEqual(payload["count"], 1)


if __name__ == "__main__":
    unittest.main()
