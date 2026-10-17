"""JH-019 live GUI tests: the minimal GUI actually opens and answers locally.

A real HTTP server is started on an ephemeral loopback port and queried with
urllib — no browser needed — so "本机可打开" is checked against a running
process rather than assumed. The static assets, the JSON endpoints, the error
shapes and the traversal guard are all exercised.
"""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from src.gui_http import make_server
from src.storage import TaskStorage

ROOT = Path(__file__).resolve().parents[1]


def _payload(task_id, ws):
    return {
        "schema_version": "1.0.0", "task_id": task_id, "title": "gui task " + task_id,
        "description": "gui", "acceptance_criteria": ["c1"], "priority": "high",
        "status": "created",
        "risk_assessment": {"risk_level": "read_only", "complexity": "low",
                            "uncertainty": "low", "risk_basis": "p", "routing_basis": "p"},
        "budget": {"max_turns": 2, "max_tokens": 1000, "max_execution_time_seconds": 60},
        "context": {"workspace_root": str(ws), "target_files": []},
        "artifact_refs": [], "tool_execution_evidence": [], "review_verdict": None,
        "error_info": None, "assigned_worker": "gemini_worker", "assigned_reviewer": "muse_reviewer",
        "metadata": {}, "created_at": "2026-10-12T00:00:00Z", "updated_at": "2026-10-12T00:00:00Z",
    }


class _ServerCase(unittest.TestCase):
    db_present = True

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.db = cls.tmp / "gui.sqlite3"
        cls.drafts = cls.tmp / "drafts.json"
        if cls.db_present:
            storage = TaskStorage(cls.db, read_only=False)
            try:
                storage.create_task(_payload("task_http00000001", cls.tmp))
            finally:
                storage.close()
        cls.httpd = make_server("127.0.0.1", 0, db_path=str(cls.db), drafts_path=str(cls.drafts))
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls._tmp.cleanup()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path):
        try:
            with urllib.request.urlopen(self.url(path), timeout=10) as response:
                return response.status, response.headers.get("Content-Type", ""), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Content-Type", ""), exc.read()

    def post(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.url(path), data=data, method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()


class TestGuiOpensLocally(_ServerCase):
    def test_index_page_is_served(self):
        status, ctype, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        text = body.decode("utf-8")
        for view in ("工作台", "任务详情", "Agent / 会话", "审查与交付"):
            self.assertIn(view, text)
        self.assertIn("只读 + 草案", text)

    def test_all_three_layers_are_served(self):
        for name, marker in (("data_access.js", "JH.api"), ("components.js", "JH.components"),
                             ("views.js", "JH.views"), ("styles.css", "#view-root")):
            status, ctype, body = self.get("/static/" + name)
            self.assertEqual(status, 200, name)
            self.assertIn(marker, body.decode("utf-8"), name)

    def test_traversal_outside_gui_is_blocked(self):
        status, _, body = self.get("/static/../src/cli.py")
        self.assertIn(status, (403, 404))
        self.assertNotIn("def main", body.decode("utf-8", "replace"))

    def test_unknown_path_is_a_json_404(self):
        status, ctype, body = self.get("/api/nope")
        self.assertEqual(status, 404)
        self.assertIn("application/json", ctype)
        self.assertEqual(json.loads(body)["error"]["code"], "not_found")


class TestGuiEndpoints(_ServerCase):
    def test_capabilities_show_unsupported_explicitly(self):
        status, _, body = self.get("/api/capabilities")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("dispatch", payload["unsupported"])

    def test_task_index_uses_recorded_rows(self):
        status, _, body = self.get("/api/tasks")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIs(payload["database_present"], True)
        self.assertEqual([row["task_id"] for row in payload["tasks"]], ["task_http00000001"])
        self.assertNotIn("is_terminal", payload["tasks"][0])

    def test_task_detail_and_delivery(self):
        detail = json.loads(self.get("/api/tasks/task_http00000001")[2])
        self.assertEqual(detail["task_result"]["status"], "created")
        delivery = json.loads(self.get("/api/tasks/task_http00000001/delivery")[2])
        self.assertEqual(delivery["task_status"], "created")
        self.assertIn("delivery_scope", delivery)

    def test_unknown_task_is_a_structured_404(self):
        status, _, body = self.get("/api/tasks/task_missing00001")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"]["code"], "task_not_found")

    def test_sessions_are_simulated_and_disconnected(self):
        payload = json.loads(self.get("/api/sessions")[2])
        self.assertTrue(payload["sessions"])
        for session in payload["sessions"]:
            self.assertIs(session["connected"], False)
            self.assertIs(session["simulated"], True)

    def test_draft_round_trip_is_marked_and_inert(self):
        status, body = self.post("/api/drafts", {"title": "t", "goal": "g"})
        self.assertEqual(status, 201)
        payload = json.loads(body)
        self.assertIs(payload["draft"]["draft"], True)
        self.assertEqual(payload["draft"]["execution"], "not_triggered")
        listed = json.loads(self.get("/api/drafts")[2])
        self.assertEqual(listed["count"], 1)
        # the draft did not become a task
        self.assertEqual(json.loads(self.get("/api/tasks")[2])["count"], 1)

    def test_draft_execution_request_is_refused(self):
        status, body = self.post("/api/drafts", {"title": "t", "goal": "g", "dispatch": True})
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["error"]["code"], "draft_cannot_execute")


class TestMissingDatabaseIsShownHonestly(_ServerCase):
    db_present = False

    def test_index_still_opens(self):
        self.assertEqual(self.get("/")[0], 200)

    def test_task_list_reports_the_absence(self):
        payload = json.loads(self.get("/api/tasks")[2])
        self.assertTrue(payload["ok"])
        self.assertIs(payload["database_present"], False)
        self.assertEqual(payload["tasks"], [])
        self.assertIn("不存在", payload["note"])


if __name__ == "__main__":
    unittest.main()
