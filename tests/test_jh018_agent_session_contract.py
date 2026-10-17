"""Contract conformance fixture for AgentSessionAdapter (JH-018, R2/R3).

Behavioural, not structural: it drives the same call site through two genuinely
different adapter implementations and pins the rules that were previously only
asserted as field presence:

* a returned run_id always resolves to its own run, and a run queried under a
  different application instance / session / task / attempt raises
  AddressMismatch instead of silently reading `unknown`;
* an idempotency key is an index of its own: a retry of the same request reuses
  the run, a different request with the same key conflicts, and neither can be
  reached by guessing another run's id;
* cancel reports a REQUEST until native confirmation is observed;
* remediation adds a native turn while preserving task history, cumulative
  budget and authorization scope;
* no run state is `succeeded`.

Nothing here runs a real external session: no model calls, no processes.
"""
import unittest
from pathlib import Path

from src.agent_session import (
    CAPABILITIES,
    AddressMismatch,
    AgentSessionAdapter,
    CollectedResult,
    IdempotencyConflict,
    MockAgentSessionAdapter,
    RunState,
    SecondMockAgentSessionAdapter,
    SessionAddress,
    SubmitRequest,
    UnsupportedCapability,
)

ROOT = Path(__file__).resolve().parents[1]
ADAPTERS = (MockAgentSessionAdapter, SecondMockAgentSessionAdapter)


def _address(session_id="sess-1", instance_id="inst-1", role="worker",
             app_kind="mock"):
    return SessionAddress(app_kind=app_kind, instance_id=instance_id,
                          session_id=session_id, model_id="mock-model", role=role)


def _adapters():
    """A fresh instance of each genuinely different implementation."""
    return [cls() for cls in ADAPTERS]


def _request(**over):
    base = dict(task_id="t1", attempt=1, session=_address(),
                instructions="do the thing", workspace=".",
                allowed_scope=("target/widget.py",), idempotency_key="t1-a1",
                budget_tokens=1000)
    base.update(over)
    return SubmitRequest(**base)


class TestContractSurface(unittest.TestCase):
    def test_six_capabilities_are_the_documented_set(self):
        self.assertEqual(set(CAPABILITIES),
                         {"capabilities", "submit", "status", "collect", "continue", "cancel"})

    def test_run_state_has_no_success_state(self):
        states = {v for k, v in vars(RunState).items() if not k.startswith("_")}
        self.assertNotIn("succeeded", states)
        self.assertIn(RunState.DECLARED_COMPLETE, states)
        self.assertIn(RunState.CANCEL_REQUESTED, states)

    def test_every_adapter_implements_every_method(self):
        for cls in ADAPTERS:
            for name in ("capabilities", "submit", "status", "collect",
                         "continue_run", "cancel"):
                self.assertTrue(callable(getattr(cls, name)), f"{cls.__name__}.{name}")


class TestRunLookupAndAddressBinding(unittest.TestCase):
    """R2: returned run_id resolves; cross-address lookup is an error, not `unknown`."""

    def _submit(self, adapter):
        return adapter.submit(_request())

    def test_returned_run_id_resolves_to_its_own_run(self):
        for adapter in _adapters():
            status = self._submit(adapter)
            found = adapter.status(_address(), status.run_id)
            self.assertEqual(found.run_id, status.run_id)
            self.assertNotEqual(found.state, RunState.UNKNOWN)
            self.assertIsNotNone(found.native_turn)

    def test_unknown_run_id_is_reported_unknown_for_a_known_address(self):
        for adapter in _adapters():
            self._submit(adapter)
            missing = adapter.status(_address(), "no-such-run")
            self.assertEqual(missing.state, RunState.UNKNOWN)
            self.assertIn("no run", missing.note)

    def test_cross_session_lookup_raises_instead_of_unknown(self):
        for adapter in _adapters():
            status = self._submit(adapter)
            other = _address(session_id="sess-OTHER")
            with self.assertRaises(AddressMismatch):
                adapter.status(other, status.run_id)
            with self.assertRaises(AddressMismatch):
                adapter.collect(other, status.run_id)

    def test_cross_instance_and_cross_app_lookup_raise(self):
        for adapter in _adapters():
            status = self._submit(adapter)
            for other in (_address(instance_id="inst-9"), _address(app_kind="other-app")):
                with self.assertRaises(AddressMismatch):
                    adapter.status(other, status.run_id)

    def test_binding_records_task_and_attempt_and_ids_do_not_collide(self):
        """A run is bound to its logical task and attempt; different tasks or
        attempts get different runs and never share a run_id."""
        for adapter in _adapters():
            first = adapter.submit(_request(task_id="t1", attempt=1, idempotency_key="k1"))
            same_task_next = adapter.submit(_request(task_id="t1", attempt=2, idempotency_key="k2"))
            other_task = adapter.submit(_request(task_id="t2", attempt=1, idempotency_key="k3"))
            self.assertEqual(len({first.run_id, same_task_next.run_id, other_task.run_id}), 3)
            self.assertEqual(adapter.status(_address(), first.run_id).binding.task_id, "t1")
            self.assertEqual(adapter.status(_address(), first.run_id).binding.attempt, 1)
            self.assertEqual(adapter.status(_address(), same_task_next.run_id).binding.attempt, 2)
            self.assertEqual(adapter.status(_address(), other_task.run_id).binding.task_id, "t2")


class TestIdempotencyIsSeparateFromRunIndex(unittest.TestCase):
    def test_same_key_same_request_reuses_the_run(self):
        for cls in ADAPTERS:
            with self.subTest(adapter=cls.__name__):
                adapter = cls()
                first = adapter.submit(_request())
                again = adapter.submit(_request())
                self.assertEqual(first.run_id, again.run_id)

    def test_same_key_different_request_conflicts(self):
        for cls in ADAPTERS:
            with self.subTest(adapter=cls.__name__):
                adapter = cls()
                adapter.submit(_request(idempotency_key="shared"))
                with self.assertRaises(IdempotencyConflict):
                    adapter.submit(_request(instructions="something else",
                                            idempotency_key="shared"))
                with self.assertRaises(IdempotencyConflict):
                    adapter.submit(_request(session=_address(session_id="sess-2"),
                                            idempotency_key="shared"))

    def test_distinct_keys_create_distinct_runs(self):
        for cls in ADAPTERS:
            with self.subTest(adapter=cls.__name__):
                adapter = cls()
                a = adapter.submit(_request(idempotency_key="a"))
                b = adapter.submit(_request(idempotency_key="b", attempt=2))
                self.assertNotEqual(a.run_id, b.run_id)
                self.assertEqual(adapter.status(_address(), a.run_id).run_id, a.run_id)
                self.assertEqual(adapter.status(_address(), b.run_id).run_id, b.run_id)


class TestCancelSemantics(unittest.TestCase):
    """R3: an unconfirmed cancel is a request, never `cancelled`."""

    def test_cancel_without_confirmation_is_a_request(self):
        for cls in ADAPTERS:
            with self.subTest(adapter=cls.__name__):
                adapter = cls()
                run = adapter.submit(_request())
                cancelled = adapter.cancel(_address(), run.run_id)
                self.assertEqual(cancelled.state, RunState.CANCEL_REQUESTED)
                self.assertNotEqual(cancelled.state, RunState.CANCELLED)
                self.assertIn("not observed", cancelled.note)
                self.assertEqual(adapter.status(_address(), run.run_id).state,
                                 RunState.CANCEL_REQUESTED)

    def test_cancelled_only_after_native_confirmation(self):
        for cls in ADAPTERS:
            with self.subTest(adapter=cls.__name__):
                adapter = cls()
                run = adapter.submit(_request())
                adapter.cancel(_address(), run.run_id)
                confirmed = adapter.confirm_cancel(_address(), run.run_id)
                self.assertEqual(confirmed.state, RunState.CANCELLED)


class TestRemediationPreservesTaskContext(unittest.TestCase):
    """R3: remediation adds a native turn; task history, budget and scope stay."""

    def test_continue_adds_turn_and_keeps_history_budget_and_scope(self):
        for cls in ADAPTERS:
            with self.subTest(adapter=cls.__name__):
                adapter = cls()
                run = adapter.submit(_request(allowed_scope=("target/widget.py",),
                                              budget_tokens=1000))
                first_turn = run.native_turn.native_turn_id
                rem = adapter.continue_run(_address(), run.run_id, "fix line 12")
                self.assertEqual(rem.run_id, run.run_id)          # same logical task
                self.assertNotEqual(rem.native_turn.native_turn_id, first_turn)
                record = adapter.inspect(run.run_id)
                self.assertEqual(len(record.turns), 2)            # history kept
                self.assertEqual([t.native_turn_id for t in record.turns][0], first_turn)
                self.assertEqual(tuple(record.scope), ("target/widget.py",))  # scope unchanged
                self.assertEqual(record.budget_tokens, 1000)      # budget not restarted
                self.assertEqual(record.handle.binding.attempt, 1)

    def test_remediation_does_not_widen_scope(self):
        for cls in ADAPTERS:
            with self.subTest(adapter=cls.__name__):
                adapter = cls()
                run = adapter.submit(_request(allowed_scope=("target/widget.py",)))
                adapter.continue_run(_address(), run.run_id, "also touch other/file.py")
                self.assertEqual(tuple(adapter.inspect(run.run_id).scope), ("target/widget.py",))


class TestReplacementNeedsNoCoreEdit(unittest.TestCase):
    """One call site, two genuinely different implementations."""

    def _drive(self, adapter):
        address = _address(role="reviewer")
        request = _request(session=address, idempotency_key="t1-a1")
        submitted = adapter.submit(request)
        again = adapter.submit(request)
        status = adapter.status(address, submitted.run_id)
        collected = adapter.collect(address, submitted.run_id)
        remediation = adapter.continue_run(address, submitted.run_id, "tighten check")
        cancel = adapter.cancel(address, submitted.run_id)
        return submitted, again, status, collected, remediation, cancel

    def test_two_different_classes_share_one_call_site(self):
        results = [self._drive(cls()) for cls in ADAPTERS]
        for submitted, again, status, collected, remediation, cancel in results:
            self.assertEqual(submitted.state, RunState.ACCEPTED)
            self.assertEqual(again.run_id, submitted.run_id)
            self.assertEqual(status.run_id, submitted.run_id)
            self.assertIsInstance(collected, CollectedResult)
            self.assertTrue(collected.unverified)
            self.assertEqual(remediation.run_id, submitted.run_id)
            self.assertEqual(cancel.state, RunState.CANCEL_REQUESTED)

    def test_implementations_are_actually_different(self):
        self.assertNotEqual(MockAgentSessionAdapter.app_kind,
                            SecondMockAgentSessionAdapter.app_kind)
        self.assertNotEqual(MockAgentSessionAdapter._by_run_id,
                            SecondMockAgentSessionAdapter._by_run_id)
        a = MockAgentSessionAdapter().submit(_request())
        b = SecondMockAgentSessionAdapter().submit(_request())
        self.assertNotEqual(a.run_id, b.run_id)   # different id schemes

    def test_collect_never_reports_success(self):
        for cls in ADAPTERS:
            with self.subTest(adapter=cls.__name__):
                adapter = cls()
                run = adapter.submit(_request())
                collected = adapter.collect(_address(), run.run_id)
                self.assertNotEqual(collected.declared_status, "succeeded")
                self.assertTrue(collected.unverified)


class TestUnsupportedIsExplicit(unittest.TestCase):
    def test_missing_capability_raises_instead_of_simulating(self):
        for cls in ADAPTERS:
            with self.subTest(adapter=cls.__name__):
                adapter = cls(support=("capabilities", "submit"))
                self.assertFalse(adapter.capabilities()["cancel"]["supported"])
                with self.assertRaises(UnsupportedCapability) as ctx:
                    adapter.cancel(_address(), "r1")
                self.assertEqual(ctx.exception.capability, "cancel")

    def test_every_capability_reported_not_claimed(self):
        for cls in ADAPTERS:
            with self.subTest(adapter=cls.__name__):
                report = cls().capabilities()
                self.assertEqual(set(report), set(CAPABILITIES))
                for name, info in report.items():
                    self.assertIn("supported", info, name)


class TestCoreHoldsNoConcreteAdapter(unittest.TestCase):
    def test_production_modules_do_not_import_this_contract(self):
        offenders = []
        for path in sorted((ROOT / "src").glob("*.py")):
            if path.name == "agent_session.py":
                continue
            if "agent_session" in path.read_text(encoding="utf-8-sig"):
                offenders.append(path.name)
        self.assertEqual(offenders, [], "unexpected production import of the contract")

    def test_contract_module_has_no_native_app_imports(self):
        text = (ROOT / "src" / "agent_session.py").read_text(encoding="utf-8-sig")
        for native in ("zcode", "codex", "subprocess", "sqlite3", "requests"):
            self.assertNotIn(f"import {native}", text, native)

    def test_contract_is_abstract(self):
        with self.assertRaises(TypeError):
            AgentSessionAdapter()


if __name__ == "__main__":
    unittest.main()
