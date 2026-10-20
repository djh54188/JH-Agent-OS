"""Hark-派单-06: GUI real session probe — offline behaviour tests.

The probe drives the REAL `CodexAgentSessionAdapter` through the application
interface and the HTTP transport; only the app-server process is replaced by the
accepted offline fake transport (tests/test_jh020_codex_agent_session.py), so no
process starts and no model is called. Every assertion is about external facts:
how many `thread/start` calls reached the transport, what the registry and the
evidence files hold on disk, and what the status payload reports.

Fault classes: zero dispatch (open/refresh/query, disabled, unconfirmed, illegal
fields, unprepared/expired id, cross-origin / bad host / non-JSON), at most once
(repeat, concurrent, restart, timeouts), refusal before dispatch (corrupt /
unreadable / inconsistent registry, history-as-probe source, budget demand,
missing runtime, unwritable registry, link), binding (reply/usage/evidence bound
to this run; foreign facts not adopted; tampered evidence; tool items), and no
collateral (history source, static sessions, capabilities, task success).
"""
import hashlib
import http.client
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_jh020_codex_agent_session as fx  # noqa: E402
import src.agent_session_codex as codex_mod  # noqa: E402
from src.agent_session_codex import READ_ONLY_SANDBOX, READ_ONLY_SANDBOX_MODE, CodexAgentSessionAdapter  # noqa: E402
from src.app_api import ApiError, AppInterface  # noqa: E402
from src.gui_http import make_server  # noqa: E402
from src.session_probe import ProbeConfig, probe_instruction, probe_token  # noqa: E402

TOKEN_RE = re.compile(r"JH-PROBE-[0-9A-F]{16}")


class EchoTransport(fx.FakeCodexTransport):
    """Fake app-server that answers with whatever the probe asked it to echo."""

    def __init__(self, *, reply=None, foreign_text=False, foreign_usage=False,
                 tool_item=False, usage=True, **kw):
        super().__init__(**kw)
        self._reply = reply
        self._foreign_text = foreign_text
        self._foreign_usage = foreign_usage
        self._tool_item = tool_item
        self._usage = usage

    def _asked_token(self):
        for call in reversed(self.calls):
            if call["method"] == "turn/start":
                text = call["params"]["input"][0]["text"]
                found = TOKEN_RE.search(text)
                return found.group(0) if found else None
        return None

    def notifications(self):
        token = self._asked_token()
        text = self._reply if self._reply is not None else token
        notes = []
        if text:
            if self._foreign_text:
                notes.append(fx.agent_item(text, thread=fx.OTHER_THREAD, turn=fx.OTHER_TURN))
            else:
                notes.append(fx.agent_item(text))
        if self._usage:
            if self._foreign_usage:
                notes.append(fx.token_usage(900, 120, thread=fx.THREAD, turn=fx.OTHER_TURN))
            else:
                notes.append(fx.token_usage(900, 120))
        if self._tool_item:
            notes.append({"method": "item/completed", "params": {
                "threadId": fx.THREAD, "turnId": fx.TURN,
                "item": {"type": "commandExecution", "id": "cmd_1", "command": "dir"}}})
        return notes + list(self._notes)


class Rig:
    """One temporary world: history source, probe dir, adapter factory, API."""

    def __init__(self, tmp, *, enabled=True, transport_kw=None, runner="sync", probe_dir=None,
                 budget=None, runtime_problem=None, history_text=None):
        self.tmp = Path(tmp)
        self.history = self.tmp / "history" / "agent-runs.jsonl"
        self.history.parent.mkdir(parents=True, exist_ok=True)
        if history_text is not None:
            self.history.write_text(history_text, encoding="utf-8")
        self.probe_dir = Path(probe_dir) if probe_dir else self.tmp / "probe"
        self.transport_kw = dict(transport_kw or {})
        self.transports = []
        self.deferred = []
        self.runner_mode = runner
        self.config = ProbeConfig(enabled=enabled, probe_dir=str(self.probe_dir), budget_tokens=budget)
        self.runtime_problem = runtime_problem
        self.api = self.make_api()

    def factory(self, registry_path, workspace):
        transport = EchoTransport(**self.transport_kw)
        self.transports.append(transport)
        return CodexAgentSessionAdapter(transport=transport, registry_path=registry_path,
                                        cwd=workspace, instance_ids=("codex-app-server/local",))

    def runner(self, job):
        if self.runner_mode == "sync":
            job()
        else:
            self.deferred.append(job)

    def make_api(self):
        return AppInterface(db_path=self.tmp / "no.sqlite3", drafts_path=self.tmp / "drafts.json",
                            runs_path=self.history, session_probe=self.config,
                            probe_adapter_factory=self.factory,
                            probe_runtime_check=lambda: self.runtime_problem,
                            probe_runner=self.runner)

    def thread_starts(self):
        return sum(1 for t in self.transports for c in t.calls if c["method"] == "thread/start")

    def registry(self):
        return self.probe_dir / "probe-runs.jsonl"

    def run_probe(self):
        prepared = self.api.prepare_session_probe({})
        started = self.api.start_session_probe({"probe_id": prepared["probe_id"], "confirm": True})
        return prepared, started


_REAL_IS_SPECIAL_PATH = codex_mod.is_special_path


def _posix_evidence_seam(value):
    """TEST-ONLY platform seam (declared in the report).

    The accepted evidence policy is written for Win32 paths and refuses every
    path that starts with "/" as root-relative, so on Linux no absolute evidence
    path can ever be verified. Off Windows, a plain POSIX absolute path is let
    through to the SAME containment, reparse and digest checks; UNC, device and
    ADS forms are still refused by the real function. On Windows the real
    function runs unchanged.
    """
    text = value.strip()
    if text.startswith("/") and not text.startswith("//"):
        return None
    return _REAL_IS_SPECIAL_PATH(value)


class _Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        if os.name != "nt":
            patcher = mock.patch.object(codex_mod, "is_special_path", _posix_evidence_seam)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def assertRefused(self, fn, code):
        with self.assertRaises(ApiError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        return caught.exception


class TestZeroDispatch(_Case):
    def test_status_never_dispatches_or_writes(self):
        rig = Rig(self.tmp)
        for _ in range(5):
            status = rig.api.session_probe_status()
        self.assertTrue(status["available"], status["blockers"])
        self.assertEqual(rig.thread_starts(), 0)
        self.assertEqual(rig.transports, [])
        self.assertFalse(rig.probe_dir.exists())

    def test_disabled_refuses_prepare_and_start(self):
        rig = Rig(self.tmp, enabled=False)
        status = rig.api.session_probe_status()
        self.assertFalse(status["available"])
        self.assertIn("probe_disabled", [b["code"] for b in status["blockers"]])
        error = self.assertRefused(lambda: rig.api.prepare_session_probe({}), "probe_disabled")
        self.assertEqual(error.http_status, 403)
        self.assertRefused(lambda: rig.api.start_session_probe(
            {"probe_id": "0" * 16, "confirm": True}), "probe_not_prepared")
        self.assertEqual(rig.thread_starts(), 0)
        self.assertFalse(rig.probe_dir.exists())

    def test_unconfirmed_start_dispatches_nothing(self):
        rig = Rig(self.tmp)
        prepared = rig.api.prepare_session_probe({})
        for confirm in (None, False, "true", 1, "yes"):
            payload = {"probe_id": prepared["probe_id"]}
            if confirm is not None:
                payload["confirm"] = confirm
            self.assertRefused(lambda: rig.api.start_session_probe(payload), "probe_not_confirmed")
        self.assertEqual(rig.thread_starts(), 0)
        self.assertFalse(rig.registry().exists())

    def test_forbidden_fields_dispatch_nothing(self):
        rig = Rig(self.tmp)
        prepared = rig.api.prepare_session_probe({})
        for key, value in (("instructions", "rm -rf /"), ("workspace", "C:/"),
                           ("session_id", "019a-existing-thread"), ("model", "other"),
                           ("path", "../../x"), ("command", "dir"), ("registry", "x.jsonl"),
                           ("target", "architect")):
            payload = {"probe_id": prepared["probe_id"], "confirm": True, key: value}
            self.assertRefused(lambda: rig.api.start_session_probe(payload), "probe_field_forbidden")
        self.assertRefused(lambda: rig.api.prepare_session_probe({"instructions": "x"}),
                           "probe_field_forbidden")
        self.assertEqual(rig.thread_starts(), 0)
        self.assertFalse(rig.registry().exists())

    def test_unprepared_or_expired_probe_id_dispatches_nothing(self):
        rig = Rig(self.tmp)
        self.assertRefused(lambda: rig.api.start_session_probe(
            {"probe_id": "a" * 16, "confirm": True}), "probe_not_prepared")
        self.assertRefused(lambda: rig.api.start_session_probe(
            {"probe_id": "../../etc", "confirm": True}), "probe_id_invalid")
        prepared = rig.api.prepare_session_probe({})
        rig.api.session_probe._clock = lambda: 10 ** 12          # far past the TTL
        self.assertRefused(lambda: rig.api.start_session_probe(
            {"probe_id": prepared["probe_id"], "confirm": True}), "probe_not_prepared")
        self.assertEqual(rig.thread_starts(), 0)


class _HttpCase(_Case):
    def start_server(self, rig):
        self.server = make_server("127.0.0.1", 0, db_path=str(rig.tmp / "no.sqlite3"),
                                  drafts_path=str(rig.tmp / "drafts.json"),
                                  runs_path=str(rig.history), session_probe=rig.config,
                                  probe_adapter_factory=rig.factory,
                                  probe_runtime_check=lambda: None, probe_runner=rig.runner)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = None if body is None else json.dumps(body).encode("utf-8")
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        merged = {"Host": f"127.0.0.1:{self.port}"}
        merged.update(headers or {})
        for key, value in merged.items():
            if value is not None:
                conn.putheader(key, value)
        if data is not None:
            conn.putheader("Content-Length", str(len(data)))
        conn.endheaders(data)
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        conn.close()
        return response.status, payload

    def same_origin(self, **over):
        headers = {"Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json",
                   "Sec-Fetch-Site": "same-origin"}
        headers.update(over)
        return headers


class TestHttpBoundary(_HttpCase):
    def test_get_status_over_http_never_dispatches(self):
        rig = Rig(self.tmp)
        self.start_server(rig)
        for _ in range(3):
            status, payload = self.request("GET", "/api/session-probe")
            self.assertEqual(status, 200)
        self.assertTrue(payload["available"])
        self.assertEqual(rig.thread_starts(), 0)
        self.assertFalse(rig.probe_dir.exists())

    def test_illegal_origin_or_host_dispatches_nothing(self):
        rig = Rig(self.tmp)
        self.start_server(rig)
        cases = {
            "cross_origin": self.same_origin(Origin="http://evil.example"),
            "missing_origin": self.same_origin(Origin=None),
            "null_origin": self.same_origin(Origin="null"),
            "other_port": self.same_origin(Origin=f"http://127.0.0.1:{self.port + 1}"),
            "non_loopback_host": dict(self.same_origin(Origin=f"http://evil.example:{self.port}"),
                                      Host=f"evil.example:{self.port}"),
            "form_content_type": self.same_origin(**{"Content-Type": "text/plain"}),
            "cross_site_fetch": self.same_origin(**{"Sec-Fetch-Site": "cross-site"}),
        }
        for label, headers in cases.items():
            for path, body in (("/api/session-probe/prepare", {}),
                               ("/api/session-probe/start", {"probe_id": "a" * 16, "confirm": True})):
                status, payload = self.request("POST", path, body, headers)
                self.assertEqual(status, 403, label)
                self.assertEqual(payload["error"]["code"], "probe_origin_refused", label)
        self.assertEqual(self.server.RequestHandlerClass.api.session_probe._prepared, {})
        self.assertEqual(rig.thread_starts(), 0)
        self.assertFalse(rig.probe_dir.exists())

    def test_same_origin_confirmed_flow_dispatches_once(self):
        rig = Rig(self.tmp)
        self.start_server(rig)
        status, prepared = self.request("POST", "/api/session-probe/prepare", {}, self.same_origin())
        self.assertEqual(status, 200, prepared)
        self.assertFalse(prepared["dispatched"])
        self.assertEqual(rig.thread_starts(), 0)
        body = {"probe_id": prepared["probe_id"], "confirm": True}
        status, started = self.request("POST", "/api/session-probe/start", body, self.same_origin())
        self.assertEqual(status, 202, started)
        status, again = self.request("POST", "/api/session-probe/start", body, self.same_origin())
        self.assertEqual(status, 202)
        self.assertTrue(again["replay"])
        self.assertEqual(rig.thread_starts(), 1)
        _, payload = self.request("GET", "/api/session-probe")
        self.assertEqual(payload["latest"]["outcome"], "round_trip_complete")
        self.assertEqual(payload["adapter_source"], "injected_offline_adapter")


class TestAtMostOnce(_Case):
    def test_same_probe_id_twice_dispatches_once(self):
        rig = Rig(self.tmp)
        prepared, first = rig.run_probe()
        self.assertTrue(first["accepted"])
        second = rig.api.start_session_probe({"probe_id": prepared["probe_id"], "confirm": True})
        self.assertTrue(second["replay"])
        self.assertFalse(second["dispatched_now"])
        self.assertEqual(rig.thread_starts(), 1)

    def test_repeat_start_of_one_id_never_dispatches_twice(self):
        """Hark-派单-07 formal sample for the declared-redundant replay row: a
        repeated start of one id may be answered as a replay OR refused, but it
        never causes a second dispatch. Asserts only that external fact."""
        rig = Rig(self.tmp)
        prepared, first = rig.run_probe()
        self.assertTrue(first["accepted"])
        for _ in range(3):
            try:
                again = rig.api.start_session_probe({"probe_id": prepared["probe_id"], "confirm": True})
                self.assertFalse(again["accepted"])
            except ApiError as exc:
                self.assertEqual(exc.code, "probe_not_prepared")
        self.assertEqual(rig.thread_starts(), 1)

    def test_concurrent_same_id_dispatches_once(self):
        rig = Rig(self.tmp)
        prepared = rig.api.prepare_session_probe({})
        barrier = threading.Barrier(8)
        results = []

        def worker():
            barrier.wait()
            results.append(rig.api.start_session_probe({"probe_id": prepared["probe_id"], "confirm": True}))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(1 for r in results if r["accepted"]), 1)
        self.assertEqual(rig.thread_starts(), 1)

    def test_second_confirmation_while_in_flight_is_refused(self):
        rig = Rig(self.tmp, runner="deferred")
        first = rig.api.prepare_session_probe({})
        second = rig.api.prepare_session_probe({})
        rig.api.start_session_probe({"probe_id": first["probe_id"], "confirm": True})
        self.assertRefused(lambda: rig.api.start_session_probe(
            {"probe_id": second["probe_id"], "confirm": True}), "probe_in_flight")
        self.assertRefused(lambda: rig.api.prepare_session_probe({}), "probe_in_flight")
        self.assertEqual(rig.api.session_probe_status()["inflight"]["probe_id"], first["probe_id"])
        for job in rig.deferred:
            job()
        self.assertEqual(rig.thread_starts(), 1)

    def test_restart_does_not_redispatch(self):
        rig = Rig(self.tmp)
        prepared, _ = rig.run_probe()
        reopened = rig.make_api()                             # a fresh server process
        replay = reopened.start_session_probe({"probe_id": prepared["probe_id"], "confirm": True})
        self.assertTrue(replay["replay"])
        self.assertRefused(lambda: reopened.start_session_probe(
            {"probe_id": "b" * 16, "confirm": True}), "probe_not_prepared")
        self.assertEqual(rig.thread_starts(), 1)

    def test_prepared_id_does_not_survive_restart(self):
        rig = Rig(self.tmp)
        prepared = rig.api.prepare_session_probe({})
        reopened = rig.make_api()
        self.assertRefused(lambda: reopened.start_session_probe(
            {"probe_id": prepared["probe_id"], "confirm": True}), "probe_not_prepared")
        self.assertEqual(rig.thread_starts(), 0)

    def _assert_unknown_blocks(self, rig, event):
        status = rig.api.session_probe_status()
        latest = status["latest"]
        self.assertEqual(latest["events"][-1], event)
        self.assertEqual(latest["outcome"], "unknown_manual")
        self.assertIn("probe_unresolved", [b["code"] for b in status["blockers"]])
        self.assertRefused(lambda: rig.api.prepare_session_probe({}), "probe_unresolved")
        reopened = rig.make_api()
        self.assertRefused(lambda: reopened.prepare_session_probe({}), "probe_unresolved")
        self.assertEqual(rig.thread_starts(), 1)

    def test_thread_start_timeout_stays_unknown_and_blocks(self):
        rig = Rig(self.tmp, transport_kw={"timeout_method": "thread/start"})
        rig.run_probe()
        self._assert_unknown_blocks(rig, "dispatch_uncertain")

    def test_turn_start_timeout_stays_unknown_and_blocks(self):
        rig = Rig(self.tmp, transport_kw={"timeout_method": "turn/start"})
        rig.run_probe()
        self._assert_unknown_blocks(rig, "dispatch_uncertain")

    def test_collect_timeout_stays_unknown_and_blocks(self):
        rig = Rig(self.tmp, transport_kw={"completion_result": {"found": False, "reason": "no turn/completed"}})
        rig.run_probe()
        self._assert_unknown_blocks(rig, "collect_timeout")

    def test_unfinished_run_after_crash_blocks(self):
        rig = Rig(self.tmp)
        rig.probe_dir.mkdir(parents=True)
        rig.registry().write_text(fx.registry_line(idempotency_key="c" * 16) + "\n", encoding="utf-8")
        before = rig.registry().read_bytes()
        status = rig.api.session_probe_status()
        self.assertEqual(status["latest"]["outcome"], "unknown_manual")
        self.assertRefused(lambda: rig.api.prepare_session_probe({}), "probe_unresolved")
        self.assertEqual(rig.registry().read_bytes(), before)
        self.assertEqual(rig.thread_starts(), 0)

    def test_dispatch_failure_is_recorded_and_not_retried(self):
        rig = Rig(self.tmp, transport_kw={"error_method": "turn/start"})
        rig.run_probe()
        status = rig.api.session_probe_status()
        self.assertEqual(status["latest"]["outcome"], "dispatch_failed")
        self.assertEqual(status["last_error"]["type"], "RuntimeError")
        self.assertEqual(rig.thread_starts(), 1)


class TestRefusalBeforeDispatch(_Case):
    def _seed(self, rig, text):
        rig.probe_dir.mkdir(parents=True, exist_ok=True)
        rig.registry().write_text(text, encoding="utf-8")
        return rig.registry().read_bytes()

    def _assert_refused_untouched(self, rig, before, code):
        self.assertRefused(lambda: rig.api.prepare_session_probe({}), code)
        self.assertEqual(rig.registry().read_bytes(), before)
        self.assertEqual(rig.thread_starts(), 0)

    def test_corrupt_json_registry_refuses(self):
        rig = Rig(self.tmp)
        before = self._seed(rig, '{"event": "reserved",\n')
        self._assert_refused_untouched(rig, before, "probe_registry_corrupt")

    def test_field_invalid_registry_refuses(self):
        rig = Rig(self.tmp)
        before = self._seed(rig, fx.registry_line(run_id="") + "\n")
        self._assert_refused_untouched(rig, before, "probe_registry_corrupt")

    def test_inconsistent_history_refuses(self):
        rig = Rig(self.tmp)
        before = self._seed(rig, fx.registry_line(event="dispatched") + "\n")
        self._assert_refused_untouched(rig, before, "probe_registry_corrupt")

    def test_unreadable_registry_refuses(self):
        rig = Rig(self.tmp)
        rig.registry().mkdir(parents=True)                    # a directory where the file should be
        self.assertRefused(lambda: rig.api.prepare_session_probe({}), "probe_registry_unreadable")
        self.assertEqual(rig.thread_starts(), 0)

    def test_history_source_cannot_be_the_probe_registry(self):
        rig = Rig(self.tmp, history_text="")
        rig.history.unlink()
        clash = Rig(self.tmp, probe_dir=rig.history.parent)
        clash.history = clash.probe_dir / "probe-runs.jsonl"
        clash.api = AppInterface(db_path=clash.tmp / "no.sqlite3", runs_path=clash.history,
                                 session_probe=clash.config, probe_adapter_factory=clash.factory,
                                 probe_runtime_check=lambda: None, probe_runner=clash.runner)
        self.assertRefused(lambda: clash.api.prepare_session_probe({}),
                           "probe_registry_is_history_source")
        self.assertEqual(clash.thread_starts(), 0)

    def test_finite_budget_demand_is_refused_not_ignored(self):
        rig = Rig(self.tmp, budget=5000)
        status = rig.api.session_probe_status()
        self.assertIn("probe_budget_unenforceable", [b["code"] for b in status["blockers"]])
        self.assertRefused(lambda: rig.api.prepare_session_probe({}), "probe_budget_unenforceable")
        self.assertEqual(rig.thread_starts(), 0)

    def test_missing_runtime_refuses(self):
        rig = Rig(self.tmp, runtime_problem="codex app-server script not found")
        self.assertRefused(lambda: rig.api.prepare_session_probe({}), "probe_runtime_missing")
        self.assertFalse(rig.probe_dir.exists())

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "POSIX permission bits are not enforced here")
    def test_unwritable_registry_refuses_before_transport(self):
        rig = Rig(self.tmp)
        rig.probe_dir.mkdir(parents=True)
        rig.registry().write_bytes(b"")
        os.chmod(rig.registry(), 0o400)
        try:
            rig.run_probe()
            self.assertEqual(rig.thread_starts(), 0)
            self.assertEqual(rig.api.session_probe_status()["last_error"]["type"], "RegistryUnavailable")
            self.assertEqual(rig.registry().read_bytes(), b"")
        finally:
            os.chmod(rig.registry(), 0o600)

    @unittest.skipIf(os.name == "nt", "symlink creation needs privileges on Windows")
    def test_linked_probe_dir_refuses(self):
        target = Path(self.tmp) / "elsewhere"
        target.mkdir()
        link = Path(self.tmp) / "probe-link"
        os.symlink(target, link)
        rig = Rig(self.tmp, probe_dir=link)
        self.assertRefused(lambda: rig.api.prepare_session_probe({}), "probe_path_is_link")
        self.assertEqual(list(target.iterdir()), [])


class TestBinding(_Case):
    def test_success_binds_reply_usage_and_evidence_to_this_run(self):
        rig = Rig(self.tmp)
        prepared, _ = rig.run_probe()
        token = probe_token(prepared["probe_id"])
        latest = rig.api.session_probe_status()["latest"]
        self.assertEqual(latest["outcome"], "round_trip_complete")
        self.assertEqual(latest["token"], token)
        self.assertEqual(latest["reply_texts"], [token])
        self.assertEqual(latest["echo"], "matched")
        self.assertTrue(latest["instruction_bound"])
        self.assertEqual((latest["native_session_id"], latest["native_turn_id"]), (fx.THREAD, fx.TURN))
        self.assertEqual(latest["usage_state"], "bound")
        self.assertEqual(latest["usage"]["turn"]["totalTokens"], 120)
        self.assertEqual(latest["usage"]["source"], {"thread_id": fx.THREAD, "turn_id": fx.TURN})
        evidence = latest["evidence"]
        self.assertEqual(evidence["state"], "verified")
        disk = Path(evidence["path"]).read_bytes()
        self.assertEqual(hashlib.sha256(disk).hexdigest(), evidence["sha256"])
        self.assertTrue(Path(evidence["path"]).resolve().is_relative_to((rig.probe_dir / "raw").resolve()))
        self.assertEqual(latest["tool_items"], [])
        self.assertEqual(latest["workspace_files"], [])
        self.assertNotEqual(latest["record_state"], "succeeded")

    def test_request_is_server_decided_and_read_only(self):
        rig = Rig(self.tmp)
        prepared, _ = rig.run_probe()
        calls = {c["method"]: c["params"] for c in rig.transports[0].calls}
        workspace = rig.probe_dir / "ws" / prepared["probe_id"]
        self.assertEqual(calls["thread/start"]["sandbox"], READ_ONLY_SANDBOX_MODE)
        self.assertEqual(calls["thread/start"]["approvalPolicy"], "never")
        self.assertEqual(calls["thread/start"]["model"], "gpt-6.1-sol")
        self.assertEqual(Path(calls["thread/start"]["cwd"]), workspace)
        self.assertEqual(calls["turn/start"]["sandboxPolicy"], READ_ONLY_SANDBOX)
        self.assertFalse(calls["turn/start"]["sandboxPolicy"]["networkAccess"])
        self.assertEqual(calls["turn/start"]["input"],
                         [{"type": "text", "text": probe_instruction(probe_token(prepared["probe_id"]))}])
        first = json.loads(rig.registry().read_text(encoding="utf-8").splitlines()[0])
        snapshot = json.loads(first["request_snapshot"])
        self.assertTrue(snapshot["session_id"].startswith("new:"))
        self.assertEqual(snapshot["role"], "worker")
        self.assertIsNone(snapshot["budget_tokens"])
        self.assertEqual(first["idempotency_key"], prepared["probe_id"])

    def test_foreign_reply_and_usage_are_not_adopted(self):
        rig = Rig(self.tmp, transport_kw={"foreign_text": True, "foreign_usage": True})
        rig.run_probe()
        latest = rig.api.session_probe_status()["latest"]
        self.assertEqual(latest["reply_texts"], [])
        self.assertEqual(latest["echo"], "mismatch")
        self.assertIsNone(latest["usage"])
        self.assertEqual(latest["usage_state"], "unknown")
        self.assertNotEqual(latest["outcome"], "round_trip_complete")

    def test_unbound_usage_is_unknown_not_zero(self):
        rig = Rig(self.tmp, transport_kw={"usage": False})
        rig.run_probe()
        latest = rig.api.session_probe_status()["latest"]
        self.assertIsNone(latest["usage"])
        self.assertEqual(latest["usage_state"], "unknown")
        self.assertNotIn('"totalTokens": 0', json.dumps(latest))

    def test_wrong_echo_is_not_a_round_trip(self):
        rig = Rig(self.tmp, transport_kw={"reply": "<img src=x onerror=alert(1)>"})
        rig.run_probe()
        latest = rig.api.session_probe_status()["latest"]
        self.assertEqual(latest["reply_texts"], ["<img src=x onerror=alert(1)>"])
        self.assertEqual(latest["echo"], "mismatch")
        self.assertEqual(latest["outcome"], "round_trip_not_proven")

    def test_tampered_evidence_is_reported(self):
        rig = Rig(self.tmp)
        rig.run_probe()
        path = Path(rig.api.session_probe_status()["latest"]["evidence"]["path"])
        path.write_bytes(path.read_bytes() + b"{}\n")
        latest = rig.api.session_probe_status()["latest"]
        self.assertEqual(latest["evidence"]["state"], "mismatch")
        self.assertEqual(latest["outcome"], "round_trip_not_proven")

    def test_tool_item_in_the_stream_is_not_a_clean_probe(self):
        rig = Rig(self.tmp, transport_kw={"tool_item": True})
        rig.run_probe()
        latest = rig.api.session_probe_status()["latest"]
        self.assertEqual(latest["tool_items"], ["commandExecution"])
        self.assertEqual(latest["outcome"], "round_trip_not_proven")


class TestNoCollateral(_Case):
    def test_history_source_static_sessions_and_capabilities_untouched(self):
        history = fx.registry_line() + "\n"
        rig = Rig(self.tmp, history_text=history)
        before = rig.history.read_bytes()
        rig.run_probe()
        self.assertEqual(rig.history.read_bytes(), before)
        sessions = rig.api.list_sessions()["sessions"]
        self.assertTrue(all(s["connected"] is False and s["simulated"] is True for s in sessions))
        caps = rig.api.capabilities()
        for name in ("dispatch", "collect", "review_trigger"):
            self.assertFalse(caps["capabilities"][name]["supported"], name)
        self.assertTrue(caps["capabilities"]["session_probe"]["supported"])
        self.assertFalse(Rig(self.tmp + "/off", enabled=False).api.capabilities()
                         ["capabilities"]["session_probe"]["supported"])

    def test_only_the_probe_directory_is_written(self):
        rig = Rig(self.tmp, history_text="")
        snapshot = {p: p.read_bytes() for p in Path(self.tmp).rglob("*") if p.is_file()}
        rig.run_probe()
        after = {p for p in Path(self.tmp).rglob("*") if p.is_file()}
        for path in after:
            if path in snapshot:
                self.assertEqual(path.read_bytes(), snapshot[path], path)
            else:
                self.assertTrue(path.resolve().is_relative_to(rig.probe_dir.resolve()), path)

    def test_status_never_claims_task_success(self):
        rig = Rig(self.tmp)
        rig.run_probe()
        text = json.dumps(rig.api.session_probe_status(), ensure_ascii=False)
        self.assertNotIn("succeeded\"", text.replace("Task succeeded", ""))
        self.assertIn("不授予任何任务成功", text)


class TestLayering(unittest.TestCase):
    @staticmethod
    def _imported_modules(name):
        import ast
        tree = ast.parse((ROOT / "src" / name).read_text(encoding="utf-8"))
        out = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                out.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                out.add(node.module)
        return out

    def test_transport_wires_only_through_the_application_interface(self):
        imported = self._imported_modules("gui_http.py")
        src_modules = {m for m in imported if m.startswith("src")}
        self.assertEqual(src_modules, {"src.app_api"})
        self.assertNotIn("subprocess", imported)

    def test_application_interface_builds_no_model_request(self):
        source = (ROOT / "src" / "app_api.py").read_text(encoding="utf-8")
        for forbidden in ("SubmitRequest", "SessionAddress", "subprocess", "src.agent_session import"):
            self.assertFalse(forbidden in source, forbidden)

    def test_core_modules_do_not_import_the_probe(self):
        for name in ("state_machine.py", "orchestrator.py", "control_guards.py", "router_policy.py",
                     "acceptance.py", "storage.py", "production_executor.py"):
            imported = self._imported_modules(name)
            self.assertFalse({"src.session_probe", "src.agent_session_codex"} & imported, name)


if __name__ == "__main__":
    unittest.main()
