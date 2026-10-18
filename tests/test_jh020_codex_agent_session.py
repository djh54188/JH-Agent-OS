"""JH-020 real Codex AgentSessionAdapter: offline contract and guard tests.

A fake transport stands in for the codex app-server, so these run with no
process and no model call. Assertions target external state (returned run
status, collected result, registry file, exact params sent) — never private
internals. Positive and negative controls are paired: every guard that protects
association has a case where the association is broken and the adapter must NOT
report a completed run.

No test here claims task success: the adapter only ever reports
`declared_complete`, and `unverified` always carries the boundary.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent_session import (  # noqa: E402
    CAPABILITIES,
    AddressMismatch,
    IdempotencyConflict,
    RunState,
    SessionAddress,
    SubmitRequest,
    UnsupportedCapability,
)
from src.agent_session_codex import (  # noqa: E402
    READ_ONLY_SANDBOX,
    READ_ONLY_SANDBOX_MODE,
    CodexAgentSessionAdapter,
)

THREAD = "01a14d5e-83d3-79d0-926d-7a472bd56b16"
TURN = "01a14d5e-88ba-7840-8ee6-81768912b851"
OTHER_THREAD = "01a00000-0000-7000-8000-000000000000"
OTHER_TURN = "01a00000-0000-7000-8000-111111111111"
NONCE = "JH020-RT-3F9A"
WS = r"D:\tmp\jh020-codex-ws"


def address(instance="codex-instance-1", session="thread-session-1"):
    return SessionAddress(app_kind="codex", instance_id=instance, session_id=session,
                          model_id="gpt-6.1-sol", role="worker")


def make_request(**over):
    base = dict(task_id="task_jh020_rt", attempt=1, session=address(),
                instructions=f"Reply with exactly this token and nothing else: {NONCE}. Do not use any tools.",
                workspace=WS, allowed_scope=(), idempotency_key=None,
                artifacts=(), budget_tokens=None)
    base.update(over)
    return SubmitRequest(**base)


def assistant_note(text, turn=TURN, thread=THREAD):
    return {"method": "item/completed", "params": {
        "threadId": thread,
        "item": {"type": "agentMessage", "id": "msg_1", "text": text, "phase": "final_answer"}}}


def usage_note(thread=THREAD):
    return {"method": "thread/tokenUsage/updated", "params": {
        "threadId": thread, "usage": {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120}}}


def completion(thread=THREAD, turn=TURN, status="completed", error=None):
    return {"found": True, "frame": {"method": "turn/completed", "params": {
        "threadId": thread, "turn": {"id": turn, "status": status, "error": error, "items": []}}}}


class FakeCodexTransport:
    def __init__(self, *, completion_result=None, notes=(), unknown=(), force_completion=False):
        self.calls = []
        self.notify_calls = []
        self._completion = completion_result if completion_result is not None else completion()
        self._force = force_completion
        self._notes = list(notes)
        self._unknown = list(unknown)
        self.completion_waits = 0
        self.closed = False
        self.fail_on = None

    def call(self, method, params, timeout=60.0):
        self.calls.append({"method": method, "params": params})
        if method == self.fail_on:
            return {"method": method, "timeout": True}
        if method == "initialize":
            return {"id": 1, "result": {"userAgent": "fake", "codexHome": "C:/x"}}
        if method == "thread/start":
            return {"id": 2, "result": {"thread": {"id": THREAD}, "sandbox": READ_ONLY_SANDBOX_MODE,
                                        "cwd": params.get("cwd"), "model": "gpt-6.1-sol",
                                        "approvalPolicy": "never"}}
        if method == "turn/start":
            return {"id": 3, "result": {"turn": {"id": TURN, "status": "inProgress"}}}
        return {"method": method, "timeout": True}

    def notify(self, method, params):
        self.notify_calls.append({"method": method, "params": params})

    def notifications(self):
        return list(self._notes)

    def wait_completion(self, thread_id, turn_id, timeout):
        self.completion_waits += 1
        result = self._completion
        if self._force:
            # A deliberately broken transport: hands back whatever frame it has,
            # matching or not, so the adapter's own association guard is exercised.
            return result
        if isinstance(result, dict) and result.get("found"):
            frame = result["frame"]["params"]
            if frame.get("threadId") != thread_id or (frame.get("turn") or {}).get("id") != turn_id:
                # A correct transport never returns a foreign completion; it
                # keeps waiting and reports not-found, which the adapter turns
                # into an unreported collection rather than a completed run.
                return {"found": False, "reason": (
                    f"no turn/completed for thread {thread_id!r} turn {turn_id!r} within {timeout}s")}
        return result

    def unknown_server_requests(self):
        return list(self._unknown)

    def close(self):
        self.closed = True


def make_adapter(transport=None, registry_path=None):
    transport = transport or FakeCodexTransport()
    return CodexAgentSessionAdapter(transport=transport, registry_path=registry_path,
                                    cwd=WS), transport


class TestCapabilities(unittest.TestCase):
    def test_declares_every_capability_and_never_claims_unobserved(self):
        adapter, _ = make_adapter()
        caps = adapter.capabilities()
        self.assertEqual(set(caps), set(CAPABILITIES))
        for name in ("capabilities", "submit", "status", "collect"):
            self.assertTrue(caps[name]["supported"], name)
        for name in ("continue", "cancel"):
            self.assertFalse(caps[name]["supported"], name)
            self.assertTrue(caps[name]["reason"], name)

    def test_unsupported_operations_raise_instead_of_simulating(self):
        adapter, _ = make_adapter()
        with self.assertRaises(UnsupportedCapability):
            adapter.continue_run(address(), "codex-run-1", "more")
        with self.assertRaises(UnsupportedCapability):
            adapter.cancel(address(), "codex-run-1")


class TestSubmitCollect(unittest.TestCase):
    def test_happy_path_records_binding_fingerprint_and_native_turn(self):
        adapter, transport = make_adapter(FakeCodexTransport(notes=[assistant_note(NONCE), usage_note()]))
        request = make_request(idempotency_key="key-1")
        status = adapter.submit(request)
        self.assertEqual(status.state, RunState.RUNNING)
        self.assertEqual(status.binding.session_id, "thread-session-1")
        self.assertEqual(status.native_turn.native_turn_id, TURN)
        self.assertEqual(status.native_turn.native_session_id, THREAD)
        run = adapter.inspect(status.run_id)
        self.assertEqual(run.request_fingerprint, request.fingerprint())
        self.assertEqual(run.native_session_id, THREAD)
        self.assertIn("thread/start", [c["method"] for c in transport.calls])

        result = adapter.collect(address(), status.run_id)
        self.assertEqual(result.declared_status, "completed")
        self.assertEqual(result.raw_refs, [f"{THREAD}/{TURN}"])
        self.assertEqual(result.artifacts[0]["assistant_text"], NONCE)
        self.assertEqual(result.usage.get("totalTokens"), 120)
        self.assertEqual(adapter.status(address(), status.run_id).state, RunState.DECLARED_COMPLETE)

    def test_submit_is_read_only_and_never_approves(self):
        adapter, transport = make_adapter()
        adapter.submit(make_request())
        turn_call = next(c for c in transport.calls if c["method"] == "turn/start")
        self.assertEqual(turn_call["params"]["approvalPolicy"], "never")
        self.assertEqual(turn_call["params"]["sandboxPolicy"], READ_ONLY_SANDBOX)
        thread_call = next(c for c in transport.calls if c["method"] == "thread/start")
        self.assertEqual(thread_call["params"]["sandbox"], READ_ONLY_SANDBOX_MODE)
        self.assertEqual(thread_call["params"]["approvalPolicy"], "never")
        self.assertEqual([c["method"] for c in transport.notify_calls], ["initialized"])

    def test_result_is_never_marked_success(self):
        adapter, _ = make_adapter(FakeCodexTransport(notes=[assistant_note(NONCE)]))
        status = adapter.submit(make_request())
        result = adapter.collect(address(), status.run_id)
        self.assertNotIn("succeeded", json.dumps(result.__dict__))
        self.assertTrue(any("review decides success" in u for u in result.unverified))

    def test_collect_is_cached_and_does_not_rewait(self):
        adapter, transport = make_adapter(FakeCodexTransport(notes=[assistant_note(NONCE)]))
        status = adapter.submit(make_request())
        first = adapter.collect(address(), status.run_id)
        second = adapter.collect(address(), status.run_id)
        self.assertEqual(first, second)
        self.assertEqual(transport.completion_waits, 1)


class TestGuardsNegativeControls(unittest.TestCase):
    def _submit(self, transport):
        adapter = CodexAgentSessionAdapter(transport=transport, cwd=WS)
        return adapter, adapter.submit(make_request())

    def test_foreign_completion_is_never_accepted_by_the_transport(self):
        # A correct transport keeps waiting (foreign frames are not returned) and
        # the collection stays unreported — never declared complete.
        transport = FakeCodexTransport(completion_result=completion(thread=OTHER_THREAD),
                                       notes=[assistant_note(NONCE)])
        adapter, status = self._submit(transport)
        result = adapter.collect(address(), status.run_id)
        self.assertNotEqual(result.declared_status, "completed")
        self.assertNotEqual(adapter.status(address(), status.run_id).state, RunState.DECLARED_COMPLETE)

    def test_broken_transport_foreign_thread_is_rejected_by_the_adapter(self):
        # Broken transport hands back a completion for ANOTHER thread; the
        # adapter must refuse to declare complete and record the mismatch.
        transport = FakeCodexTransport(completion_result=completion(thread=OTHER_THREAD),
                                       notes=[assistant_note(NONCE)], force_completion=True)
        adapter, status = self._submit(transport)
        result = adapter.collect(address(), status.run_id)
        self.assertEqual(adapter.status(address(), status.run_id).state, RunState.FAILED)
        self.assertNotEqual(result.declared_status, "completed")
        self.assertTrue(any("completion thread" in u for u in result.unverified))

    def test_broken_transport_foreign_turn_is_rejected_by_the_adapter(self):
        transport = FakeCodexTransport(completion_result=completion(turn=OTHER_TURN),
                                       notes=[assistant_note(NONCE)], force_completion=True)
        adapter, status = self._submit(transport)
        result = adapter.collect(address(), status.run_id)
        self.assertEqual(adapter.status(address(), status.run_id).state, RunState.FAILED)
        self.assertNotEqual(result.declared_status, "completed")
        self.assertTrue(any("completion turn" in u for u in result.unverified))

    def test_completion_with_failed_status_is_not_declared_complete(self):
        transport = FakeCodexTransport(completion_result=completion(status="failed"),
                                       notes=[assistant_note(NONCE)])
        adapter, status = self._submit(transport)
        result = adapter.collect(address(), status.run_id)
        self.assertEqual(adapter.status(address(), status.run_id).state, RunState.FAILED)
        self.assertEqual(result.declared_status, "failed")

    def test_completion_with_error_is_not_declared_complete(self):
        transport = FakeCodexTransport(completion_result=completion(error={"code": "boom"}),
                                       notes=[assistant_note(NONCE)])
        adapter, status = self._submit(transport)
        adapter.collect(address(), status.run_id)
        self.assertEqual(adapter.status(address(), status.run_id).state, RunState.FAILED)

    def test_missing_completion_reports_unreported_without_retry(self):
        transport = FakeCodexTransport(completion_result={"found": False, "reason": "no completion"},
                                       notes=[assistant_note(NONCE)])
        adapter, status = self._submit(transport)
        result = adapter.collect(address(), status.run_id)
        self.assertEqual(result.declared_status, "unreported")
        self.assertNotEqual(adapter.status(address(), status.run_id).state, RunState.DECLARED_COMPLETE)
        self.assertEqual(transport.completion_waits, 1)

    def test_unimplemented_server_requests_are_surfaced_not_ignored(self):
        transport = FakeCodexTransport(notes=[assistant_note(NONCE)], unknown=["mcpServer/mystery/request"])
        adapter, status = self._submit(transport)
        result = adapter.collect(address(), status.run_id)
        self.assertTrue(any("unimplemented server requests" in u for u in result.unverified))

    def test_address_mismatch_is_raised_for_status_and_collect(self):
        adapter, transport = make_adapter()
        status = adapter.submit(make_request())
        other = address(instance="codex-instance-2", session="thread-session-2")
        with self.assertRaises(AddressMismatch):
            adapter.status(other, status.run_id)
        with self.assertRaises(AddressMismatch):
            adapter.collect(other, status.run_id)

    def test_unknown_run_reports_unknown_without_touching_transport(self):
        adapter, transport = make_adapter()
        before = len(transport.calls)
        self.assertEqual(adapter.status(address(), "codex-run-nope").state, RunState.UNKNOWN)
        result = adapter.collect(address(), "codex-run-nope")
        self.assertEqual(result.declared_status, "unreported")
        self.assertEqual(len(transport.calls), before)


class TestIdempotency(unittest.TestCase):
    def test_same_key_same_request_replays_one_run_and_turn(self):
        adapter, transport = make_adapter()
        request = make_request(idempotency_key="key-1")
        first = adapter.submit(request)
        second = adapter.submit(request)
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(second.native_turn.native_turn_id, first.native_turn.native_turn_id)
        self.assertEqual([c["method"] for c in transport.calls].count("turn/start"), 1)

    def test_same_key_different_request_conflicts(self):
        adapter, _ = make_adapter()
        adapter.submit(make_request(idempotency_key="key-1"))
        with self.assertRaises(IdempotencyConflict):
            adapter.submit(make_request(idempotency_key="key-1", instructions="a different instruction"))

    def test_fingerprint_covers_semantics_not_just_task(self):
        a = make_request(workspace=WS, instructions="x")
        b = make_request(workspace=WS + "-other", instructions="x")
        c = make_request(workspace=WS, instructions="y")
        self.assertNotEqual(a.fingerprint(), b.fingerprint())
        self.assertNotEqual(a.fingerprint(), c.fingerprint())


class TestRegistryPersistence(unittest.TestCase):
    def test_registry_records_fingerprint_session_turn_and_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "runs.jsonl")
            adapter, _ = make_adapter(FakeCodexTransport(notes=[assistant_note(NONCE)]), registry_path=path)
            request = make_request(idempotency_key="key-9")
            status = adapter.submit(request)
            adapter.collect(address(), status.run_id)
            records = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]
            events = [r["event"] for r in records]
            self.assertIn("submitted", events)
            self.assertIn("collected", events)
            submitted = next(r for r in records if r["event"] == "submitted")
            self.assertEqual(submitted["request_fingerprint"], request.fingerprint())
            self.assertEqual(submitted["native_session_id"], THREAD)
            self.assertEqual(submitted["native_turns"], [TURN])
            self.assertEqual(submitted["session_id"], "thread-session-1")
            collected = next(r for r in records if r["event"] == "collected")
            self.assertEqual(collected["assistant_texts"], [NONCE])
            self.assertEqual(collected["completion"]["turn_id"], TURN)


if __name__ == "__main__":
    unittest.main()
