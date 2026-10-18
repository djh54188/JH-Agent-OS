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

TEST = "test_jh020_codex_agent_session."


def run_case(name):
    return unittest.TextTestRunner(stream=io.StringIO()).run(
        unittest.defaultTestLoader.loadTestsFromName(name))


# name, patchers, guard regression that must fail
MUTATIONS = [
    ("completion_association_ignored",
     [patch.object(codex_mod, "completion_association", new=lambda params, thread_id, turn_id: (True, True))],
     TEST + "TestGuardsNegativeControls.test_broken_transport_foreign_thread_is_rejected_by_the_adapter"),
    ("completion_status_ignored",
     [patch.object(codex_mod, "completion_status_ok", new=lambda completed: True)],
     TEST + "TestGuardsNegativeControls.test_completion_with_failed_status_is_not_declared_complete"),
    ("idempotency_conflict_ignored",
     [patch.object(codex_mod, "fingerprint_matches", new=lambda stored, current: True)],
     TEST + "TestIdempotency.test_same_key_different_request_conflicts"),
    ("address_binding_ignored",
     [patch.object(codex_mod, "address_matches", new=lambda run_binding, wanted: True)],
     TEST + "TestGuardsNegativeControls.test_address_mismatch_is_raised_for_status_and_collect"),
    ("unobserved_capability_claimed",
     [patch.object(codex_mod.CodexAgentSessionAdapter, "SUPPORTED",
                   new=frozenset({"capabilities", "submit", "status", "collect", "continue", "cancel"}))],
     TEST + "TestCapabilities.test_declares_every_capability_and_never_claims_unobserved"),
]


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
