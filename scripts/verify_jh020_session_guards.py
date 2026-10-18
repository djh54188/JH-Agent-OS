"""JH-020 Codex AgentSessionAdapter guard verification by targeted mutation.

Each guard the adapter relies on is disabled alone with a targeted patch, and
its regression test must then FAIL on an assertion (an error never counts). A
baseline run of every test (unpatched) must pass first, so a green result cannot
come from a broken harness.

Patches replace single callables/attributes by name, so a mutant never fails
just because an in-memory module copy changed identity. No source file, no
process, and no model call is involved.
"""
import io
import json
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import src.agent_session_codex as codex_mod

T = "test_jh020_codex_agent_session."
R1 = T + "TestR1FactsBoundToTurn."
R2 = T + "TestR2DurableRegistration."
R3 = T + "TestR3AddressAndConstraints."
NEG = T + "TestGuardsNegativeControls."

# name, patchers, guard regression that must fail
MUTATIONS = [
    ("completion_association_ignored",
     [patch.object(codex_mod, "completion_association", new=lambda params, thread_id, turn_id: (True, True))],
     NEG + "test_broken_transport_foreign_thread_is_rejected_by_the_adapter"),
    ("completion_status_ignored",
     [patch.object(codex_mod, "completion_status_ok", new=lambda completed: True)],
     NEG + "test_completion_with_failed_status_is_not_declared_complete"),
    ("idempotency_conflict_ignored",
     [patch.object(codex_mod, "fingerprint_matches", new=lambda stored, current: True)],
     T + "TestIdempotency.test_same_key_different_request_conflicts"),
    ("address_binding_ignored",
     [patch.object(codex_mod, "address_matches", new=lambda run_binding, wanted: True)],
     NEG + "test_address_mismatch_is_raised_for_status_and_collect"),
    ("unobserved_capability_claimed",
     [patch.object(codex_mod.CodexAgentSessionAdapter, "SUPPORTED",
                   new=frozenset({"capabilities", "submit", "status", "collect", "continue", "cancel"}))],
     T + "TestCapabilities.test_declares_every_capability_and_never_claims_unobserved"),
    # R1 — facts must bind to the exact native thread AND turn
    ("notification_binding_ignored",
     [patch.object(codex_mod, "notification_binding", new=lambda params, thread_id, turn_id: (True, True))],
     R1 + "test_same_thread_wrong_turn_facts_are_rejected"),
    # R2 — durable pre-registration and no redispatch
    ("registry_reload_ignored",
     [patch.object(codex_mod.CodexAgentSessionAdapter, "_load_registry", new=lambda self: None)],
     R2 + "test_reopened_registry_blocks_redispatch_of_an_uncertain_key"),
    ("registry_write_failure_swallowed",
     [patch.object(codex_mod, "append_record", new=lambda path, record: None)],
     R2 + "test_registry_write_failure_refuses_dispatch"),
    # R3 — address, model and budget constraints
    ("new_session_address_guard_ignored",
     [patch.object(codex_mod, "is_new_session_request", new=lambda session_id: True)],
     R3 + "test_existing_or_designated_session_address_is_refused"),
    ("model_honored_check_ignored",
     [patch.object(codex_mod, "model_is_honored", new=lambda requested, reported: True)],
     R3 + "test_unhonored_explicit_model_is_refused"),
    ("budget_refusal_ignored",
     [patch.object(codex_mod, "budget_is_dispatchable", new=lambda budget_tokens: True)],
     R3 + "test_finite_budget_is_refused_before_dispatch"),
]


def run_case(name):
    return unittest.TextTestRunner(stream=io.StringIO()).run(
        unittest.defaultTestLoader.loadTestsFromName(name))


def main():
    controls = [run_case(row[-1]) for row in MUTATIONS]
    if not all(r.wasSuccessful() and r.testsRun == 1 and not r.skipped for r in controls):
        failing = [row[0] for row, r in zip(MUTATIONS, controls) if not r.wasSuccessful()]
        print(json.dumps({"verdict": "fail", "reason": "baseline control failed", "cases": failing}))
        return 1
    rows = []
    for name, patchers, test_name in MUTATIONS:
        with ExitStack() as stack:
            for patcher in patchers:
                stack.enter_context(patcher)
            result = run_case(test_name)
        detected = bool(result.failures) and not result.errors
        rows.append({"case": name, "detected": detected,
                     "failures": len(result.failures), "errors": len(result.errors)})
    passed = all(row["detected"] for row in rows)
    print(json.dumps({"scope": "jh020_codex_adapter_offline_only", "model_calls": 0,
                      "processes": 0, "controls": len(controls), "mutations": len(rows),
                      "detected": sum(row["detected"] for row in rows),
                      "rows": rows, "verdict": "pass" if passed else "fail"}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
