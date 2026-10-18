"""JH-020 C-2/C-3 guard verification by targeted mutation (offline, in-memory).

For each guard we disable exactly that guard with targeted patches, re-run its
regression test, and require the test to FAIL on an assertion (an error never
counts). A baseline run of each test (unpatched) must pass first, so a green
result cannot come from a broken harness.

Patches replace whole callables/attributes, not recompiled modules, so a mutant
never fails merely because an in-memory copy changed class identity. No
production source, sealed fixture bytes, or task state is modified.
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

import src.cli as cli_mod
import src.code_delivery as code_delivery_mod
import src.orchestrator as orchestrator_mod
import src.storage as storage_mod
import test_jh020_delivery_reconcile as jh020_tests

TEST = "test_jh020_delivery_reconcile."


def run_case(name):
    result = unittest.TextTestRunner(stream=io.StringIO()).run(
        unittest.defaultTestLoader.loadTestsFromName(name))
    return result


_real_get_task = storage_mod.TaskStorage.get_task
_real_reconcile = code_delivery_mod.reconcile_host_effect


def _get_task_flag_forced(self, task_id):
    task = _real_get_task(self, task_id)
    task["_runtime"] = dict(task.get("_runtime") or {})
    task["_runtime"]["has_unconfirmed_side_effect"] = True
    return task


def _reconcile_that_preempts_owner(storage, task_id, *, note="", actor_role="operator"):
    # Violates the invariant: the observation entry must never take the owner.
    storage._conn.execute("UPDATE task_control SET owner_id = ? WHERE task_id = ?",
                          ("mutant_owner", task_id))
    return _real_reconcile(storage, task_id, note=note, actor_role=actor_role)


def _reconcile_that_releases_marker(storage, task_id, *, note="", actor_role="operator"):
    # Re-introduces the pre-rectification bug: a single read releases the marker.
    out = _real_reconcile(storage, task_id, note=note, actor_role=actor_role)
    storage._conn.execute("UPDATE tasks SET has_unconfirmed_side_effect = 0 WHERE task_id = ?", (task_id,))
    return storage.get_task(task_id)


def _empty_pending(self):
    return []


def _patches_for_reconcile_release():
    return [
        patch.object(code_delivery_mod, "reconcile_host_effect", new=_reconcile_that_releases_marker),
        patch.object(cli_mod, "reconcile_host_effect", new=_reconcile_that_releases_marker),
        patch.object(jh020_tests, "reconcile_host_effect", new=_reconcile_that_releases_marker),
    ]


def _patches_for_owner_preempt():
    return [
        patch.object(code_delivery_mod, "reconcile_host_effect", new=_reconcile_that_preempts_owner),
        patch.object(cli_mod, "reconcile_host_effect", new=_reconcile_that_preempts_owner),
        patch.object(jh020_tests, "reconcile_host_effect", new=_reconcile_that_preempts_owner),
    ]


# name, patchers (list), guard regression that must fail
MUTATIONS = [
    ("sealed_output_assertion_disabled",
     [patch.object(orchestrator_mod, "registered_output_satisfies_sealing",
                   new=lambda raw, expectation: True)],
     TEST + "TestSealedOutputAssertion.test_matching_digest_with_failing_text_never_succeeds"),
    ("sealed_marker_source_replaced",
     [patch.object(orchestrator_mod, "sealed_post_apply_expectation",
                   new=lambda template: {"expect_exit": 0, "expect_stdout_contains": "FAIL"})],
     TEST + "TestSealedOutputAssertion.test_passing_registered_output_still_succeeds"),
    ("unconfirmed_target_states_disabled",
     [patch.object(storage_mod, "UNCONFIRMED_HOST_EFFECT_TARGETS", new=frozenset())],
     TEST + "TestUnconfirmedHostEffectPersistence.test_cancel_of_waiting_delivery_flags_unconfirmed"),
    ("reconcile_releases_marker_again",
     _patches_for_reconcile_release(),
     TEST + "TestReconcileHostEffectObservationOnly.test_observation_records_state_and_keeps_marker"),
    ("reconcile_flag_requirement_removed",
     [patch.object(storage_mod.TaskStorage, "get_task", new=_get_task_flag_forced)],
     TEST + "TestReconcileHostEffectObservationOnly.test_observation_refused_without_unconfirmed_effect"),
    ("reconcile_owner_preempt_enabled",
     _patches_for_owner_preempt(),
     TEST + "TestReconcileHostEffectObservationOnly.test_owner_held_task_is_observed_without_preemption"),
    ("pending_reconciliation_hidden",
     [patch.object(storage_mod.TaskStorage, "list_unresolved_host_effects", new=_empty_pending)],
     TEST + "TestPendingReconciliationDiscovery.test_pending_list_survives_database_reopen"),
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
    print(json.dumps({"scope": "jh020_c2c3_offline_only", "model_calls": 0, "host_runs": 0,
                      "controls": len(controls), "mutations": len(rows),
                      "detected": sum(row["detected"] for row in rows),
                      "rows": rows, "verdict": "pass" if passed else "fail"}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
