"""JH-020 run-history transport tests: the endpoints answer over real HTTP.

A real loopback server is started with a server-configured registry path. The
browser-facing surface can only ask for the list or for a run id that exists in
the history; it cannot choose a file. Each endpoint is checked against a running
process, and the run-history queries are asserted to be read-only.
"""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from src.gui_http import make_server

ROOT = Path(__file__).resolve().parents[1]

RUN_ID = "codex-run-1-http0001"
THREAD = "01a1cccc-3333-7000-8000-000000000003"
TURN = "01a1dddd-4444-7000-8000-000000000004"


def _registry(path: Path) -> Path:
    snapshot = json.dumps({"schema": "jh018/submit/v1", "app_kind": "codex",
                           "instance_id": "codex-app-server/local", "session_id": "new",
                           "model_id": "gpt-6.1-sol", "role": "worker",
                           "task_id": "task_http_runs", "attempt": 1, "workspace": "D:/ws",
                           "allowed_scope": [], "budget_tokens": None,
                           "instructions": "x", "artifacts": []}, ensure_ascii=False)
    base = {"run_id": RUN_ID, "app_kind": "codex", "instance_id": "codex-app-server/local",
            "session_id": "new", "task_id": "task_http_runs", "attempt": 1,
            "request_fingerprint": "b" * 64, "request_snapshot": snapshot,
            "idempotency_key": "key-http", "native_session_id": None, "native_turns": [],
            "native_model": None, "state": "queued", "native_state": "reserved",
            "at": "2026-10-18T00:00:00Z"}
    records = [
        dict(base, event="reserved"),
        dict(base, event="dispatched", native_session_id=THREAD, native_turns=[TURN],
             native_model="gpt-6.1-sol", state="running", native_state="turn-started"),
        dict(base, event="collected", native_session_id=THREAD, native_turns=[TURN],
             native_model="gpt-6.1-sol", state="declared_complete", native_state="completed",
             declared_status="completed", artifacts=[], raw_refs=[f"{THREAD}/{TURN}"],
             unverified=["adapter observation only; review decides success"],
             usage={"turn": {"totalTokens": 7}, "thread_total": {"totalTokens": 7}}, raw=None),
    ]
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                    encoding="utf-8")
    return path


class _ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.runs = _registry(cls.tmp / "runs.jsonl")
        cls.db = cls.tmp / "absent.sqlite3"
        cls.httpd = make_server("127.0.0.1", 0, db_path=str(cls.db),
                                drafts_path=str(cls.tmp / "drafts.json"),
                                runs_path=str(cls.runs))
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls._tmp.cleanup()

    def get(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


class TestRunHistoryEndpoints(_ServerCase):
    def test_list_endpoint_returns_the_configured_history(self):
        status, payload = self.get("/api/agent-runs")
        self.assertEqual(status, 200)
        self.assertEqual(payload["state"], "ok")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["runs"][0]["run_id"], RUN_ID)
        self.assertFalse(payload["source"]["browser_supplied"])

    def test_detail_endpoint_returns_one_run(self):
        status, payload = self.get("/api/agent-runs/" + RUN_ID)
        self.assertEqual(status, 200)
        self.assertEqual(payload["run"]["run_id"], RUN_ID)
        self.assertEqual(payload["run"]["evidence"]["state"], "missing")

    def test_unknown_run_is_404(self):
        status, payload = self.get("/api/agent-runs/no-such-run")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "run_not_found")

    def test_a_query_string_cannot_change_the_source(self):
        # The browser cannot name a file: the query is ignored and the server's
        # configured registry is still the only source.
        status, payload = self.get("/api/agent-runs?path=" + urllib.parse.quote("/etc/passwd")
                                   + "&runs=C:/windows/win.ini")
        self.assertEqual(status, 200)
        self.assertEqual(payload["source"]["path"], str(self.runs))
        self.assertFalse(payload["source"]["browser_supplied"])
        self.assertEqual(payload["runs"][0]["run_id"], RUN_ID)

    def test_run_id_that_looks_like_a_path_is_not_read_as_one(self):
        status, payload = self.get("/api/agent-runs/..%2F..%2Fwindows%2Fwin.ini")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "run_not_found")


class TestRunHistoryIsReadOnly(_ServerCase):
    def test_queries_leave_the_registry_byte_identical(self):
        before = self.runs.read_bytes()
        self.get("/api/agent-runs")
        self.get("/api/agent-runs/" + RUN_ID)
        self.assertEqual(self.runs.read_bytes(), before)

    def test_post_to_the_run_history_is_not_a_supported_operation(self):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/agent-runs",
                                         data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
