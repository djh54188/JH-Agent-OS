"""Contract conformance fixture for AgentSessionAdapter (JH-018).

Pins the design rules of src/agent_session.py: the surface is exactly the six
documented capabilities, an adapter that lacks a capability says so instead of
degrading silently, a second (mock) adapter is hosted without any core edit, and
the collaboration core holds no concrete adapter import. Nothing here runs a
real external session; no model calls, no writes outside temp dirs.
"""
import unittest
from pathlib import Path

from src.agent_session import (
    CAPABILITIES,
    AgentSessionAdapter,
    CollectedResult,
    MockAgentSessionAdapter,
    RunState,
    RunStatus,
    SessionAddress,
    SubmitRequest,
    UnsupportedCapability,
)

ROOT = Path(__file__).resolve().parents[1]


def _address(role="worker"):
    return SessionAddress(app_kind="mock", instance_id="inst-1",
                          session_id="sess-1", model_id="mock-model", role=role)


class TestContractSurface(unittest.TestCase):
    def test_six_capabilities_are_the_documented_set(self):
        self.assertEqual(set(CAPABILITIES),
                         {"capabilities", "submit", "status", "collect", "continue", "cancel"})

    def test_abstract_adapter_requires_every_capability(self):
        method_for = {"capabilities": "capabilities", "submit": "submit",
                      "status": "status", "collect": "collect",
                      "continue": "continue_run", "cancel": "cancel"}
        self.assertEqual(set(method_for), set(CAPABILITIES))
        for name, method in method_for.items():
            self.assertTrue(hasattr(AgentSessionAdapter, method), name)

    def test_run_state_has_no_success_state(self):
        states = {v for k, v in vars(RunState).items() if not k.startswith("_")}
        self.assertNotIn("succeeded", states)
        self.assertIn(RunState.DECLARED_COMPLETE, states)


class TestUnsupportedIsExplicit(unittest.TestCase):
    def test_missing_capability_raises_instead_of_simulating(self):
        adapter = MockAgentSessionAdapter(support=("capabilities", "submit"))
        self.assertFalse(adapter.capabilities()["cancel"]["supported"])
        with self.assertRaises(UnsupportedCapability) as ctx:
            adapter.cancel(_address(), "run-1")
        self.assertEqual(ctx.exception.capability, "cancel")

    def test_every_capability_reported_not_claimed(self):
        adapter = MockAgentSessionAdapter()
        report = adapter.capabilities()
        self.assertEqual(set(report), set(CAPABILITIES))
        for name, info in report.items():
            self.assertIn("supported", info, name)


class TestReplacementNeedsNoCoreEdit(unittest.TestCase):
    """Two adapters satisfy the same calls; the caller code below is written
    once, against the contract, and is unchanged between them."""

    def _drive(self, adapter):
        address = _address(role="reviewer")
        request = SubmitRequest(task_id="t1", attempt=1, session=address,
                                instructions="do the thing", workspace=".",
                                allowed_scope=["target/widget.py"],
                                idempotency_key="t1-a1")
        submitted = adapter.submit(request)
        again = adapter.submit(request)          # idempotent retry
        status = adapter.status(address, submitted.run_id)
        collected = adapter.collect(address, submitted.run_id)
        return submitted, again, status, collected

    def test_two_different_adapters_share_one_call_site(self):
        first = MockAgentSessionAdapter()
        second = MockAgentSessionAdapter(support=CAPABILITIES)  # different instance/impl config
        s1 = self._drive(first)
        s2 = self._drive(second)
        for submitted, again, status, collected in (s1, s2):
            self.assertEqual(submitted.state, RunState.ACCEPTED)
            self.assertEqual(again.run_id, submitted.run_id)      # retry deduped
            self.assertIn(status.state, (RunState.ACCEPTED, RunState.UNKNOWN))
            self.assertIsInstance(collected, CollectedResult)
            self.assertTrue(collected.unverified)                 # never auto-trusted

    def test_collect_never_reports_success(self):
        adapter = MockAgentSessionAdapter()
        _, _, _, collected = self._drive(adapter)
        self.assertNotEqual(collected.declared_status, "succeeded")
        self.assertTrue(collected.unverified)


class TestCoreHoldsNoConcreteAdapter(unittest.TestCase):
    def test_production_modules_do_not_import_this_contract(self):
        """The contract is design-only until an approved batch wires it in."""
        offenders = []
        for path in sorted((ROOT / "src").glob("*.py")):
            if path.name == "agent_session.py":
                continue
            text = path.read_text(encoding="utf-8-sig")
            if "agent_session" in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [], "unexpected production import of the contract")

    def test_contract_module_has_no_native_app_imports(self):
        text = (ROOT / "src" / "agent_session.py").read_text(encoding="utf-8-sig")
        for native in ("zcode", "codex", "subprocess", "sqlite3", "requests"):
            self.assertNotIn(f"import {native}", text, native)


if __name__ == "__main__":
    unittest.main()
