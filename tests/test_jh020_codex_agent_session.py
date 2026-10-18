"""JH-020 real Codex AgentSessionAdapter: offline contract and guard tests.

A fake transport stands in for the codex app-server, so these run with no
process and no model call. Fixtures carry the REAL association fields the server
sends (`params.threadId`, `params.turnId`, `params.item`, `params.tokenUsage`),
so an unbound fact looks exactly like the one the review counterexample planted.

Assertions target external state (returned run status, collected result,
registry file, exact params sent) — never private internals. Every guard that
protects association, durability or addressing is paired with a case where it is
violated and the adapter must NOT report a completed run.

No test here claims task success: the adapter only reports `declared_complete`,
and `unverified` always carries the boundary.
"""
import builtins
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
    RegistryUnavailable,
    UnsupportedAddress,
    UnsupportedRequest,
    parse_registry_text,
    read_registry_text,
    validate_registry_record,
    verify_raw_evidence,
)

THREAD = "01a14d5e-83d3-79d0-926d-7a472bd56b16"
TURN = "01a14d5e-88ba-7840-8ee6-81768912b851"
OTHER_THREAD = "01a00000-0000-7000-8000-000000000000"
OTHER_TURN = "01a00000-0000-7000-8000-111111111111"
NONCE = "JH020-RT-3F9A"
WS = r"D:\tmp\jh020-codex-ws"
INSTANCE = "codex-app-server/local"
MODEL = "gpt-6.1-sol"


def address(instance=INSTANCE, session="new", model=MODEL, role="worker", app_kind="codex"):
    return SessionAddress(app_kind=app_kind, instance_id=instance, session_id=session,
                          model_id=model, role=role)


def make_request(**over):
    base = dict(task_id="task_jh020_rt", attempt=1, session=address(),
                instructions=f"Reply with exactly this token and nothing else: {NONCE}. Do not use any tools.",
                workspace=WS, allowed_scope=(), idempotency_key=None,
                artifacts=(), budget_tokens=None)
    base.update(over)
    return SubmitRequest(**base)


def agent_item(text, thread=THREAD, turn=TURN):
    return {"method": "item/completed", "params": {
        "threadId": thread, "turnId": turn, "completedAtMs": 1,
        "item": {"type": "agentMessage", "id": "message_" + text, "text": text,
                 "phase": "final_answer"}}}


def token_usage(total, last, thread=THREAD, turn=TURN):
    return {"method": "thread/tokenUsage/updated", "params": {
        "threadId": thread, "turnId": turn,
        "tokenUsage": {"total": {"totalTokens": total, "inputTokens": total - 10, "outputTokens": 10},
                       "last": {"totalTokens": last, "inputTokens": last - 5, "outputTokens": 5},
                       "modelContextWindow": 475000}}}


def completion(thread=THREAD, turn=TURN, status="completed", error=None):
    return {"found": True, "frame": {"method": "turn/completed", "params": {
        "threadId": thread, "turn": {"id": turn, "status": status, "error": error, "items": []}}}}


class FakeCodexTransport:
    def __init__(self, *, completion_result=None, notes=(), unknown=(), force_completion=False,
                 reported_model=MODEL, timeout_method=None, error_method=None, registry_path=None):
        self.calls = []
        self.notify_calls = []
        self._completion = completion_result if completion_result is not None else completion()
        self._force = force_completion
        self._notes = list(notes)
        self._unknown = list(unknown)
        self._reported_model = reported_model
        self._timeout_method = timeout_method
        self._error_method = error_method
        self._registry_path = registry_path
        self.registry_during_dispatch = None
        self.completion_waits = 0
        self.closed = False

    def call(self, method, params, timeout=60.0):
        self.calls.append({"method": method, "params": params})
        if method == "thread/start" and self._registry_path:
            # Capture what was durably registered BEFORE this external call.
            try:
                self.registry_during_dispatch = Path(self._registry_path).read_text(encoding="utf-8")
            except OSError:
                self.registry_during_dispatch = None
        if method == self._timeout_method:
            return {"method": method, "timeout": True}
        if method == self._error_method:
            return {"method": method, "error": {"code": -32000, "message": "refused"}}
        if method == "initialize":
            return {"id": 1, "result": {"userAgent": "fake", "codexHome": "C:/x"}}
        if method == "thread/start":
            return {"id": 2, "result": {"thread": {"id": THREAD}, "sandbox": READ_ONLY_SANDBOX_MODE,
                                        "cwd": params.get("cwd"), "model": self._reported_model,
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
            return result
        if isinstance(result, dict) and result.get("found"):
            frame = result["frame"]["params"]
            if frame.get("threadId") != thread_id or (frame.get("turn") or {}).get("id") != turn_id:
                return {"found": False, "reason": (
                    f"no turn/completed for thread {thread_id!r} turn {turn_id!r} within {timeout}s")}
        return result

    def unknown_server_requests(self):
        return list(self._unknown)

    def close(self):
        self.closed = True


def make_adapter(transport=None, registry_path=None, **kw):
    transport = transport or FakeCodexTransport()
    return CodexAgentSessionAdapter(transport=transport, registry_path=registry_path,
                                    cwd=WS, **kw), transport


def registry_record(**over):
    """One complete, valid record in the exact format the adapter writes."""
    record = {"event": "reserved", "run_id": "codex-run-1-abcdef01", "app_kind": "codex",
              "instance_id": INSTANCE, "session_id": "new", "task_id": "task_jh020_rt", "attempt": 1,
              "request_fingerprint": "0" * 64, "request_snapshot": "{}", "idempotency_key": None,
              "native_session_id": None, "native_turns": [], "native_model": None,
              "state": RunState.QUEUED, "native_state": "reserved", "at": "2026-10-18T00:00:00Z"}
    record.update(over)
    return record


def registry_line(**over):
    return json.dumps(registry_record(**over), ensure_ascii=False)


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
        adapter, transport = make_adapter(FakeCodexTransport(
            notes=[agent_item(NONCE), token_usage(1000, 120)]))
        request = make_request(idempotency_key="key-1")
        status = adapter.submit(request)
        self.assertEqual(status.state, RunState.RUNNING)
        self.assertEqual(status.binding.session_id, "new")
        self.assertEqual(status.native_turn.native_turn_id, TURN)
        self.assertEqual(status.native_turn.native_session_id, THREAD)
        run = adapter.inspect(status.run_id)
        self.assertEqual(run.request_fingerprint, request.fingerprint())
        self.assertEqual(run.native_session_id, THREAD)
        self.assertEqual(run.native_model, MODEL)
        self.assertIn("thread/start", [c["method"] for c in transport.calls])

        result = adapter.collect(address(), status.run_id)
        self.assertEqual(result.declared_status, "completed")
        self.assertEqual(result.raw_refs, [f"{THREAD}/{TURN}"])
        self.assertEqual(result.artifacts[0]["assistant_text"], NONCE)
        self.assertEqual(adapter.status(address(), status.run_id).state, RunState.DECLARED_COMPLETE)

    def test_usage_is_split_into_current_turn_and_thread_total(self):
        adapter, _ = make_adapter(FakeCodexTransport(
            notes=[agent_item(NONCE), token_usage(1000, 120)]))
        status = adapter.submit(make_request())
        result = adapter.collect(address(), status.run_id)
        self.assertEqual(result.usage["turn"]["totalTokens"], 120)
        self.assertEqual(result.usage["thread_total"]["totalTokens"], 1000)
        self.assertEqual(result.usage["source"], {"thread_id": THREAD, "turn_id": TURN})

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
        adapter, _ = make_adapter(FakeCodexTransport(notes=[agent_item(NONCE)]))
        status = adapter.submit(make_request())
        result = adapter.collect(address(), status.run_id)
        self.assertNotIn("succeeded", json.dumps(result.__dict__))
        self.assertTrue(any("review decides success" in u for u in result.unverified))

    def test_collect_is_cached_and_does_not_rewait(self):
        adapter, transport = make_adapter(FakeCodexTransport(notes=[agent_item(NONCE)]))
        status = adapter.submit(make_request())
        first = adapter.collect(address(), status.run_id)
        second = adapter.collect(address(), status.run_id)
        self.assertEqual(first, second)
        self.assertEqual(transport.completion_waits, 1)


class TestR1FactsBoundToTurn(unittest.TestCase):
    """R1: assistant text and usage must be bound to the exact native thread+turn."""

    def _collect(self, notes, **kw):
        adapter, _ = make_adapter(FakeCodexTransport(notes=notes, **kw))
        status = adapter.submit(make_request())
        return adapter, status, adapter.collect(address(), status.run_id)

    def test_positive_control_current_turn_facts_are_adopted(self):
        _, _, result = self._collect([agent_item("CURRENT"), token_usage(1000, 120)])
        self.assertEqual([a["assistant_text"] for a in result.artifacts], ["CURRENT"])
        self.assertEqual(result.usage["turn"]["totalTokens"], 120)

    def test_same_thread_wrong_turn_facts_are_rejected(self):
        _, _, result = self._collect([agent_item("OLD_TURN", turn=OTHER_TURN),
                                      token_usage(9999, 9999, turn=OTHER_TURN)])
        self.assertEqual(result.artifacts, [])
        self.assertEqual(result.usage, {})
        self.assertTrue(any("not bound to this thread and turn" in u for u in result.unverified))

    def test_missing_thread_facts_are_rejected(self):
        _, _, result = self._collect([agent_item("NO_THREAD", thread=None),
                                      token_usage(8888, 8888, thread=None)])
        self.assertEqual(result.artifacts, [])
        self.assertEqual(result.usage, {})
        self.assertTrue(any("not bound to this thread and turn" in u for u in result.unverified))

    def test_foreign_thread_facts_are_rejected(self):
        _, _, result = self._collect([agent_item("FOREIGN", thread=OTHER_THREAD),
                                      token_usage(7777, 7777, thread=OTHER_THREAD)])
        self.assertEqual(result.artifacts, [])
        self.assertEqual(result.usage, {})
        self.assertTrue(any("ignored" in u for u in result.unverified))

    def test_current_turn_is_adopted_even_alongside_unbound_facts(self):
        _, _, result = self._collect([agent_item("CURRENT"), agent_item("OLD_TURN", turn=OTHER_TURN),
                                      agent_item("NO_THREAD", thread=None),
                                      token_usage(1000, 120), token_usage(9999, 9999, turn=OTHER_TURN)])
        self.assertEqual([a["assistant_text"] for a in result.artifacts], ["CURRENT"])
        self.assertEqual(result.usage["turn"]["totalTokens"], 120)
        self.assertTrue(any("not bound to this thread and turn" in u for u in result.unverified))

    def test_missing_usage_is_recorded_as_unproven(self):
        _, _, result = self._collect([agent_item("CURRENT")])
        self.assertEqual(result.usage, {})
        self.assertTrue(any("not proven" in u for u in result.unverified))


class TestR2DurableRegistration(unittest.TestCase):
    """R2: register before dispatch; keep unknown outcomes; never re-dispatch."""

    def test_reservation_is_durable_before_the_first_external_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "runs.jsonl")
            transport = FakeCodexTransport(registry_path=path)
            adapter = CodexAgentSessionAdapter(transport=transport, registry_path=path, cwd=WS)
            adapter.submit(make_request(idempotency_key="k1"))
            self.assertIsNotNone(transport.registry_during_dispatch)
            self.assertIn('"event": "reserved"', transport.registry_during_dispatch)
            self.assertIn('"request_snapshot"', transport.registry_during_dispatch)

    def test_timeout_keeps_unknown_outcome_and_known_ids_without_redispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "runs.jsonl")
            transport = FakeCodexTransport(timeout_method="turn/start", registry_path=path)
            adapter = CodexAgentSessionAdapter(transport=transport, registry_path=path, cwd=WS)
            first = adapter.submit(make_request(idempotency_key="same-key"))
            self.assertEqual(first.state, RunState.UNKNOWN)
            run = adapter.inspect(first.run_id)
            self.assertEqual(run.native_session_id, THREAD)           # known native id preserved
            second = adapter.submit(make_request(idempotency_key="same-key"))
            self.assertEqual(second.run_id, first.run_id)
            self.assertEqual([c["method"] for c in transport.calls].count("turn/start"), 1)
            records = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()]
            self.assertEqual([r["event"] for r in records], ["reserved", "dispatch_uncertain"])

    def test_reopened_registry_blocks_redispatch_of_an_uncertain_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "runs.jsonl")
            first_transport = FakeCodexTransport(timeout_method="turn/start", registry_path=path)
            first_adapter = CodexAgentSessionAdapter(transport=first_transport, registry_path=path, cwd=WS)
            first = first_adapter.submit(make_request(idempotency_key="same-key"))

            reopened_transport = FakeCodexTransport(registry_path=path)   # would succeed if dispatched
            reopened = CodexAgentSessionAdapter(transport=reopened_transport, registry_path=path, cwd=WS)
            replay = reopened.submit(make_request(idempotency_key="same-key"))
            self.assertEqual(replay.run_id, first.run_id)
            self.assertEqual(replay.state, RunState.UNKNOWN)
            self.assertEqual(reopened_transport.calls, [])                # nothing dispatched

    def test_registry_write_failure_at_dispatch_refuses_before_any_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            # The parent directory does not exist: the read finds nothing (a first
            # establishment), but the write before dispatch cannot be made.
            path = os.path.join(tmp, "missing-dir", "runs.jsonl")
            transport = FakeCodexTransport()
            adapter = CodexAgentSessionAdapter(transport=transport, registry_path=path, cwd=WS)
            with self.assertRaises(RegistryUnavailable):
                adapter.submit(make_request(idempotency_key="k1"))
            self.assertEqual(transport.calls, [])

    def test_registry_persists_snapshot_usage_and_locatable_raw_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "runs.jsonl")
            transport = FakeCodexTransport(notes=[agent_item(NONCE), token_usage(1000, 120)],
                                           registry_path=path)
            adapter = CodexAgentSessionAdapter(transport=transport, registry_path=path, cwd=WS)
            request = make_request(idempotency_key="k1")
            status = adapter.submit(request)
            adapter.collect(address(), status.run_id)
            records = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()]
            reserved = next(r for r in records if r["event"] == "reserved")
            self.assertEqual(reserved["request_snapshot"], request.snapshot())
            self.assertEqual(reserved["idempotency_key"], "k1")
            collected = next(r for r in records if r["event"] == "collected")
            self.assertEqual(collected["usage"]["turn"]["totalTokens"], 120)
            self.assertEqual(collected["assistant_texts"], [NONCE])
            raw = collected["raw"]
            self.assertTrue(os.path.isfile(raw["path"]))                  # locatable
            self.assertEqual(len(raw["sha256"]), 64)                      # immutable hash
            self.assertGreaterEqual(raw["frames"], 2)

    def test_identical_replay_of_a_completed_key_does_not_redispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "runs.jsonl")
            transport = FakeCodexTransport(notes=[agent_item(NONCE)], registry_path=path)
            adapter = CodexAgentSessionAdapter(transport=transport, registry_path=path, cwd=WS)
            request = make_request(idempotency_key="k1")
            first = adapter.submit(request)
            second = adapter.submit(request)
            self.assertEqual(first.run_id, second.run_id)
            self.assertEqual([c["method"] for c in transport.calls].count("turn/start"), 1)


class TestR3AddressAndConstraints(unittest.TestCase):
    """R3: refuse unsupported addresses/constraints before any dispatch."""

    def test_wrong_application_is_refused_without_dispatch(self):
        adapter, transport = make_adapter()
        with self.assertRaises(UnsupportedAddress):
            adapter.submit(make_request(session=address(app_kind="zcode", instance="different-instance",
                                                        session="existing-session")))
        self.assertEqual(transport.calls, [])

    def test_unsupported_instance_is_refused(self):
        adapter, transport = make_adapter()
        with self.assertRaises(UnsupportedAddress):
            adapter.submit(make_request(session=address(instance="some-other-instance")))
        self.assertEqual(transport.calls, [])

    def test_existing_or_designated_session_address_is_refused(self):
        adapter, transport = make_adapter()
        with self.assertRaises(UnsupportedAddress):
            adapter.submit(make_request(session=address(session="thread-session-1")))
        self.assertEqual(transport.calls, [])

    def test_new_session_prefix_is_accepted(self):
        adapter, transport = make_adapter()
        status = adapter.submit(make_request(session=address(session="new:jh020-label")))
        self.assertEqual(status.state, RunState.RUNNING)
        self.assertIn("thread/start", [c["method"] for c in transport.calls])

    def test_explicit_model_is_sent_and_verified(self):
        adapter, transport = make_adapter()
        adapter.submit(make_request(session=address(model=MODEL)))
        thread_call = next(c for c in transport.calls if c["method"] == "thread/start")
        self.assertEqual(thread_call["params"]["model"], MODEL)

    def test_unhonored_explicit_model_is_refused(self):
        adapter, transport = make_adapter(FakeCodexTransport(reported_model="other-model"))
        with self.assertRaises(UnsupportedRequest):
            adapter.submit(make_request(session=address(model="explicit-different-model")))
        self.assertNotIn("turn/start", [c["method"] for c in transport.calls])

    def test_finite_budget_is_refused_before_dispatch(self):
        adapter, transport = make_adapter()
        with self.assertRaises(UnsupportedRequest):
            adapter.submit(make_request(budget_tokens=1))
        self.assertEqual(transport.calls, [])


class TestGuardsNegativeControls(unittest.TestCase):
    def _submit(self, transport):
        adapter = CodexAgentSessionAdapter(transport=transport, cwd=WS)
        return adapter, adapter.submit(make_request())

    def test_foreign_completion_is_never_accepted_by_the_transport(self):
        transport = FakeCodexTransport(completion_result=completion(thread=OTHER_THREAD),
                                       notes=[agent_item(NONCE)])
        adapter, status = self._submit(transport)
        result = adapter.collect(address(), status.run_id)
        self.assertNotEqual(result.declared_status, "completed")
        self.assertNotEqual(adapter.status(address(), status.run_id).state, RunState.DECLARED_COMPLETE)

    def test_broken_transport_foreign_thread_is_rejected_by_the_adapter(self):
        transport = FakeCodexTransport(completion_result=completion(thread=OTHER_THREAD),
                                       notes=[agent_item(NONCE)], force_completion=True)
        adapter, status = self._submit(transport)
        result = adapter.collect(address(), status.run_id)
        self.assertEqual(adapter.status(address(), status.run_id).state, RunState.FAILED)
        self.assertNotEqual(result.declared_status, "completed")
        self.assertTrue(any("completion thread" in u for u in result.unverified))

    def test_broken_transport_foreign_turn_is_rejected_by_the_adapter(self):
        transport = FakeCodexTransport(completion_result=completion(turn=OTHER_TURN),
                                       notes=[agent_item(NONCE)], force_completion=True)
        adapter, status = self._submit(transport)
        result = adapter.collect(address(), status.run_id)
        self.assertEqual(adapter.status(address(), status.run_id).state, RunState.FAILED)
        self.assertNotEqual(result.declared_status, "completed")
        self.assertTrue(any("completion turn" in u for u in result.unverified))

    def test_completion_with_failed_status_is_not_declared_complete(self):
        transport = FakeCodexTransport(completion_result=completion(status="failed"),
                                       notes=[agent_item(NONCE)])
        adapter, status = self._submit(transport)
        result = adapter.collect(address(), status.run_id)
        self.assertEqual(adapter.status(address(), status.run_id).state, RunState.FAILED)
        self.assertEqual(result.declared_status, "failed")

    def test_completion_with_error_is_not_declared_complete(self):
        transport = FakeCodexTransport(completion_result=completion(error={"code": "boom"}),
                                       notes=[agent_item(NONCE)])
        adapter, status = self._submit(transport)
        adapter.collect(address(), status.run_id)
        self.assertEqual(adapter.status(address(), status.run_id).state, RunState.FAILED)

    def test_missing_completion_reports_unreported_without_retry(self):
        transport = FakeCodexTransport(completion_result={"found": False, "reason": "no completion"},
                                       notes=[agent_item(NONCE)])
        adapter, status = self._submit(transport)
        result = adapter.collect(address(), status.run_id)
        self.assertEqual(result.declared_status, "unreported")
        self.assertNotEqual(adapter.status(address(), status.run_id).state, RunState.DECLARED_COMPLETE)
        self.assertEqual(transport.completion_waits, 1)

    def test_unimplemented_server_requests_are_surfaced_not_ignored(self):
        transport = FakeCodexTransport(notes=[agent_item(NONCE)], unknown=["mcpServer/mystery/request"])
        adapter, status = self._submit(transport)
        result = adapter.collect(address(), status.run_id)
        self.assertTrue(any("unimplemented server requests" in u for u in result.unverified))

    def test_address_mismatch_is_raised_for_status_and_collect(self):
        adapter, transport = make_adapter()
        status = adapter.submit(make_request())
        other = address(instance=INSTANCE, session="new:other")
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


class TestR2ARegistryFailClosed(unittest.TestCase):
    """R2-A: an unusable registry history must refuse dispatch, not be skipped."""

    def _uncertain_registry(self, tmp, key="uncertain-key"):
        """Create a registry whose replay outcome is unknown (turn/start times out)."""
        path = os.path.join(tmp, "runs.jsonl")
        transport = FakeCodexTransport(timeout_method="turn/start")
        adapter = CodexAgentSessionAdapter(transport=transport, registry_path=path, cwd=WS)
        run = adapter.submit(make_request(idempotency_key=key))
        return path, run

    def test_new_registry_first_establishment_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "runs.jsonl")
            self.assertFalse(os.path.exists(path))
            adapter, transport = make_adapter(registry_path=path)
            status = adapter.submit(make_request(idempotency_key="k1"))
            self.assertEqual(status.state, RunState.RUNNING)
            self.assertTrue(os.path.exists(path))
            self.assertIn("thread/start", [c["method"] for c in transport.calls])

    def test_healthy_reopen_replays_without_dispatch_and_leaves_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, first = self._uncertain_registry(tmp)
            before = Path(path).read_bytes()
            transport = FakeCodexTransport()
            reopened = CodexAgentSessionAdapter(transport=transport, registry_path=path, cwd=WS)
            replay = reopened.submit(make_request(idempotency_key="uncertain-key"))
            self.assertEqual(replay.run_id, first.run_id)
            self.assertEqual(replay.state, RunState.UNKNOWN)
            self.assertEqual(transport.calls, [])
            self.assertEqual(Path(path).read_bytes(), before)

    def test_unreadable_registry_refuses_dispatch_before_any_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self._uncertain_registry(tmp)
            before = Path(path).read_bytes()
            real_open = builtins.open

            def faulty_open(file, mode="r", *args, **kwargs):
                if Path(file) == Path(path) and "r" in mode:
                    raise PermissionError("injected registry read failure")
                return real_open(file, mode, *args, **kwargs)

            transport = FakeCodexTransport()
            with mock.patch("builtins.open", faulty_open):
                poisoned = CodexAgentSessionAdapter(transport=transport, registry_path=path, cwd=WS)
            with self.assertRaises(RegistryUnavailable):
                poisoned.submit(make_request(idempotency_key="uncertain-key"))
            self.assertEqual(transport.calls, [])                              # zero dispatch
            self.assertEqual(Path(path).read_bytes(), before)                  # bytes preserved

    def test_corrupt_registry_refuses_dispatch_before_any_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self._uncertain_registry(tmp)
            original = Path(path).read_bytes()
            Path(path).write_bytes(original.replace(b'"event":', b'"event"!'))
            corrupted = Path(path).read_bytes()
            transport = FakeCodexTransport()
            poisoned = CodexAgentSessionAdapter(transport=transport, registry_path=path, cwd=WS)
            with self.assertRaises(RegistryUnavailable):
                poisoned.submit(make_request(idempotency_key="uncertain-key"))
            self.assertEqual(transport.calls, [])
            self.assertEqual(Path(path).read_bytes(), corrupted)               # not repaired or truncated

    def test_read_registry_text_distinguishes_absent_from_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_registry_text(os.path.join(tmp, "absent.jsonl")))
            with self.assertRaises(RegistryUnavailable):
                read_registry_text(tmp)                                        # a directory: existing but unreadable

    def test_parse_registry_text_never_skips_a_malformed_record(self):
        good = registry_line()
        with self.assertRaises(RegistryUnavailable):
            parse_registry_text(good + "\n" + "{not json}\n")
        with self.assertRaises(RegistryUnavailable):
            parse_registry_text("{}\n")                                        # not a usable record
        self.assertEqual(len(parse_registry_text(good + "\n")), 1)
        self.assertEqual(parse_registry_text("\n  \n"), [])
        self.assertEqual(len(parse_registry_text(registry_line(idempotency_key="k1") + "\n")), 1)


class TestR2BRawEvidenceBytes(unittest.TestCase):
    """R2-B: the evidence digest must describe the bytes actually on disk."""

    def _raw(self, tmp):
        path = os.path.join(tmp, "runs.jsonl")
        transport = FakeCodexTransport(notes=[agent_item(NONCE), token_usage(1000, 120)],
                                       registry_path=path)
        adapter = CodexAgentSessionAdapter(transport=transport, registry_path=path, cwd=WS)
        status = adapter.submit(make_request(idempotency_key="k1"))
        adapter.collect(address(), status.run_id)
        return adapter.inspect(status.run_id).raw_evidence

    def test_digest_matches_the_actual_file_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._raw(tmp)
            data = Path(raw["path"]).read_bytes()
            self.assertEqual(raw["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(raw["bytes"], len(data))
            self.assertEqual(raw["frames"], len([l for l in data.decode("utf-8").splitlines() if l.strip()]))
            self.assertNotIn(b"\r\n", data)                                    # no newline translation
            ok, detail = verify_raw_evidence(raw)
            self.assertTrue(ok, detail)

    def test_tampered_bytes_fail_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._raw(tmp)
            Path(raw["path"]).write_bytes(Path(raw["path"]).read_bytes() + b"{}")
            ok, detail = verify_raw_evidence(raw)
            self.assertFalse(ok)
            self.assertIn("bytes", detail)

    def test_missing_file_fails_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._raw(tmp)
            os.remove(raw["path"])
            ok, _ = verify_raw_evidence(raw)
            self.assertFalse(ok)

    def test_wrong_frame_count_fails_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._raw(tmp)
            self.assertFalse(verify_raw_evidence({**raw, "frames": raw["frames"] + 1})[0])
            self.assertFalse(verify_raw_evidence({**raw, "path": None})[0])


class TestR2ARecordShapeValidation(unittest.TestCase):
    """R2-A: a parseable but incomplete history must not silently lose a key."""

    def _uncertain_registry(self, tmp, key="uncertain-key"):
        path = os.path.join(tmp, "runs.jsonl")
        transport = FakeCodexTransport(timeout_method="turn/start")
        adapter = CodexAgentSessionAdapter(transport=transport, registry_path=path, cwd=WS)
        run = adapter.submit(make_request(idempotency_key=key))
        return path, run

    def _rewrite(self, path, mutate):
        """Rewrite the fixture the way the reviewer does, preserving the original."""
        original = Path(path).read_bytes()
        Path(path[:-6] + ".original.jsonl").write_bytes(original)
        records = [json.loads(line) for line in original.decode("utf-8").splitlines()]
        for record in records:
            mutate(record)
        Path(path).write_bytes(("\n".join(json.dumps(r) for r in records) + "\n").encode("utf-8"))
        return Path(path).read_bytes()

    def _replay(self, path, key="uncertain-key"):
        transport = FakeCodexTransport()
        adapter = CodexAgentSessionAdapter(transport=transport, registry_path=path, cwd=WS)
        return adapter.submit(make_request(idempotency_key=key)), transport

    def test_validator_accepts_a_complete_record(self):
        self.assertEqual(validate_registry_record(registry_record())["run_id"], "codex-run-1-abcdef01")

    def test_validator_accepts_a_legitimate_null_idempotency_key(self):
        record = registry_record(idempotency_key=None)
        self.assertIn("idempotency_key", record)
        self.assertIsNone(validate_registry_record(record)["idempotency_key"])

    def test_validator_rejects_missing_idempotency_key_field(self):
        record = registry_record()
        record.pop("idempotency_key")                                           # absent != null
        with self.assertRaises(RegistryUnavailable):
            validate_registry_record(record)

    def test_validator_rejects_invalid_values(self):
        for over in ({"run_id": None}, {"run_id": ""}, {"attempt": "1"}, {"attempt": True},
                     {"native_turns": "x"}, {"native_turns": [""]}, {"state": None},
                     {"app_kind": ""}, {"request_fingerprint": ""}, {"idempotency_key": ""},
                     {"native_session_id": 5}):
            with self.assertRaises(RegistryUnavailable, msg=str(over)):
                validate_registry_record(registry_record(**over))

    def test_healthy_history_replays_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, first = self._uncertain_registry(tmp)
            before = Path(path).read_bytes()
            replay, transport = self._replay(path)
            self.assertEqual(replay.run_id, first.run_id)
            self.assertEqual(replay.state, RunState.UNKNOWN)
            self.assertEqual(transport.calls, [])
            self.assertEqual(Path(path).read_bytes(), before)

    def test_dispatched_run_without_an_idempotency_key_is_still_a_healthy_history(self):
        # A legitimate null key must load as a healthy history, not poison it.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "runs.jsonl")
            transport = FakeCodexTransport()
            adapter = CodexAgentSessionAdapter(transport=transport, registry_path=path, cwd=WS)
            run = adapter.submit(make_request())                                 # no idempotency key
            before = Path(path).read_bytes()
            reopened = CodexAgentSessionAdapter(transport=FakeCodexTransport(), registry_path=path, cwd=WS)
            self.assertIsNotNone(reopened.inspect(run.run_id))                   # history recovered
            self.assertEqual(reopened.inspect(run.run_id).idempotency_key, None)
            self.assertEqual(Path(path).read_bytes(), before)

    def test_null_run_id_refuses_dispatch_without_rewriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, first = self._uncertain_registry(tmp)
            corrupted = self._rewrite(path, lambda r: r.__setitem__("run_id", None))
            poisoned = CodexAgentSessionAdapter(transport=FakeCodexTransport(), registry_path=path, cwd=WS)
            transport = FakeCodexTransport()
            poisoned._transport = transport
            with self.assertRaises(RegistryUnavailable):
                poisoned.submit(make_request(idempotency_key="uncertain-key"))
            self.assertEqual(transport.calls, [])
            self.assertEqual(Path(path).read_bytes(), corrupted)
            self.assertFalse(poisoned.inspect(first.run_id))                     # index not silently shortened

    def test_missing_idempotency_key_field_refuses_dispatch_without_rewriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self._uncertain_registry(tmp)
            corrupted = self._rewrite(path, lambda r: r.pop("idempotency_key"))
            poisoned = CodexAgentSessionAdapter(transport=FakeCodexTransport(), registry_path=path, cwd=WS)
            transport = FakeCodexTransport()
            poisoned._transport = transport
            with self.assertRaises(RegistryUnavailable):
                poisoned.submit(make_request(idempotency_key="uncertain-key"))
            self.assertEqual(transport.calls, [])
            self.assertEqual(Path(path).read_bytes(), corrupted)


if __name__ == "__main__":
    unittest.main()
