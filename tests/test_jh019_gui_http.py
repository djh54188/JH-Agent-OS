"""JH-019 live GUI tests: the minimal GUI actually opens and answers locally.

A real HTTP server is started on an ephemeral loopback port and queried with
urllib — no browser needed — so "本机可打开" is checked against a running
process rather than assumed. The static assets, the JSON endpoints, the error
shapes and the traversal guard are all exercised.
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

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


class TestLoopbackBindingOnly(unittest.TestCase):
    """R3: this batch binds loopback only, and refuses anything else BEFORE binding."""

    def test_loopback_hosts_are_accepted_by_validation(self):
        """The boundary rule itself: all three loopback literals pass validation."""
        from src.gui_http import validate_host, LOOPBACK_HOSTS
        self.assertEqual(sorted(LOOPBACK_HOSTS), ["127.0.0.1", "::1", "localhost"])
        for host in ("127.0.0.1", "::1", "localhost", "  127.0.0.1  "):
            with self.subTest(host=host):
                self.assertEqual(validate_host(host).strip(), host.strip())

    def test_loopback_hosts_can_actually_bind(self):
        """End-to-end for the literals this machine can bind (IPv6 may be absent)."""
        from src.gui_http import make_server
        for host in ("127.0.0.1", "localhost"):
            with self.subTest(host=host):
                server = make_server(host, 0)
                try:
                    self.assertTrue(server.server_address[0])
                finally:
                    server.server_close()

    def test_non_loopback_hosts_are_refused_without_binding(self):
        from src import gui_http
        for host in ("0.0.0.0", "::", "192.168.1.5", "10.0.0.1", "example.com"):
            with self.subTest(host=host):
                with mock.patch.object(gui_http, "ThreadingHTTPServer") as server_class:
                    with self.assertRaises(ValueError) as ctx:
                        gui_http.make_server(host, 0)
                    self.assertIn("环回", str(ctx.exception))
                    server_class.assert_not_called()       # nothing was ever bound

    def test_default_host_is_loopback(self):
        from src.gui_http import make_server
        server = make_server(port=0)
        try:
            self.assertEqual(server.server_address[0], "127.0.0.1")
        finally:
            server.server_close()


class _DraftServerCase(unittest.TestCase):
    """A server with its own drafts file, for write-path tests."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.drafts = self.tmp / "drafts.json"
        self.httpd = make_server("127.0.0.1", 0, db_path=str(self.tmp / "absent.sqlite3"),
                                 drafts_path=str(self.drafts))
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._tmp.cleanup()

    def post(self, payload):
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/drafts", data=data,
                                         method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def get_drafts(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/drafts", timeout=15) as response:
            return json.loads(response.read())


class TestDraftWritesOverRealHTTP(_DraftServerCase):
    def test_concurrent_saves_over_http_all_persist(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(
                lambda i: self.post({"title": f"t{i}", "goal": f"g{i}"}), range(8)))
        statuses = [status for status, _ in outcomes]
        self.assertEqual(statuses, [201] * 8,
                         f"non-201 bodies: {[b for st, b in outcomes if st != 201]}")
        returned = {body["draft"]["draft_id"] for _, body in outcomes}
        self.assertEqual(len(returned), 8)
        listed = self.get_drafts()
        self.assertEqual(listed["count"], 8)
        self.assertEqual({d["draft_id"] for d in listed["drafts"]}, returned)

    def test_refused_save_leaves_no_record(self):
        status, body = self.post({"title": "t", "goal": "g", "dispatch": True})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "draft_cannot_execute")
        self.assertEqual(self.get_drafts()["count"], 0)

    def test_failed_publish_returns_an_error_and_keeps_old_data(self):
        status, _ = self.post({"title": "keep", "goal": "g"})
        self.assertEqual(status, 201)
        before = self.drafts.read_bytes()
        with mock.patch("src.app_api.os.replace", side_effect=OSError("disk full")):
            status, body = self.post({"title": "lost", "goal": "g"})
        self.assertEqual(status, 500)
        self.assertEqual(body["error"]["code"], "internal_error")
        self.assertEqual(self.drafts.read_bytes(), before)
        self.assertEqual(self.get_drafts()["count"], 1)
        leftovers = [p.name for p in self.tmp.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])


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
