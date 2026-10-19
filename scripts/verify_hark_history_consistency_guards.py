"""Hark-派单-03 history-consistency guard verification by targeted mutation (offline).

Each guard of src/run_history_consistency.py (and its wiring in AppInterface) is
weakened alone, and the behaviour test that owns it must then FAIL on an
assertion. Rules:

* every target first passes unpatched (control), with message checks OFF, so a
  green result cannot come from a broken harness;
* message checks stay OFF under every mutant: a mutant only counts when the
  ok/corrupt OUTCOME changes, never because a message's wording changed;
* an error (exception) is never a detection; a run with zero tests is a failure.

A separate "redundant" table re-runs the accepted JH-020 row
`registry_field_validation_skipped` to show how the new layer interacts with it;
those rows are reported, never counted as detections.

No process, no model call, no session send, and no source file is touched.
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

import src.app_api as app_api  # noqa: E402
import src.run_history_consistency as rhc  # noqa: E402
import test_jh020_history_consistency as hc_tests  # noqa: E402

T = "test_jh020_history_consistency."
BIND = T + "TestBindingFields."
NATIVE = T + "TestNativeEvolution."
ORDER = T + "TestEventOrder."
SHAPE = T + "TestEventShape."
IDEM = T + "TestIdempotencyScope."
WIRE = T + "TestApiWiring."


def run_case(names):
    suite = unittest.TestSuite()
    for name in names:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
    return unittest.TextTestRunner(stream=io.StringIO()).run(suite)


def _noop(*args, **kwargs):
    return None


def _evolution_without_null_regress(prev, record, run_id, index):
    for name in rhc.NATIVE_SCALARS:
        before, after = prev.get(name), record.get(name)
        if before is None or after is None:          # weakened: null "regress" accepted
            continue
        if after != before:
            rhc._fail(run_id, index, f"{name} conflicts")
    before_turns = list(prev.get("native_turns") or [])
    after_turns = list(record.get("native_turns") or [])
    if after_turns[:len(before_turns)] != before_turns:
        rhc._fail(run_id, index, "native_turns changed")


def _evolution_without_turns(prev, record, run_id, index):
    for name in rhc.NATIVE_SCALARS:
        before, after = prev.get(name), record.get(name)
        if before is None:
            continue
        if after is None or after != before:
            rhc._fail(run_id, index, f"{name} changed")
    # weakened: native_turns not compared


_real_transition = rhc.check_transition


def _transition_accepts_unknown(previous_event, event, run_id, index):
    if event not in rhc.EVENTS:                       # weakened: unknown events pass
        return
    _real_transition(previous_event, event, run_id, index)


_real_shape = rhc.check_event_shape


def _shape_without_collect_identity(event, record, dispatched, run_id, index):
    if event in ("collect_timeout", "collected"):    # weakened: only the state is checked
        if event == "collect_timeout" and record.get("state") != "unknown":
            rhc._fail(run_id, index, "collect_timeout state")
        if event == "collected" and record.get("state") not in ("declared_complete", "failed"):
            rhc._fail(run_id, index, "collected state")
        return
    _real_shape(event, record, dispatched, run_id, index)


def _shape_without_dispatch_branches(event, record, dispatched, run_id, index):
    if event in ("dispatch_failed", "dispatch_uncertain"):   # weakened: any branch passes
        return
    _real_shape(event, record, dispatched, run_id, index)


ALL_EVENTS = frozenset(rhc.EVENTS)

MUTATIONS = [
    # 1. binding / request / key
    ("binding_check_disabled",
     [patch.object(rhc, "check_binding", new=_noop)],
     [BIND + "test_every_binding_field_change_is_corrupt_at_every_later_record"]),
] + [
    (f"binding_field_dropped:{name}",
     [patch.object(rhc, "BINDING_FIELDS", new=tuple(f for f in rhc.BINDING_FIELDS if f != name))],
     [BIND + "test_every_binding_field_change_is_corrupt_at_every_later_record"])
    for name in rhc.BINDING_FIELDS
] + [
    # 2. native evolution
    ("native_evolution_disabled",
     [patch.object(rhc, "check_native_evolution", new=_noop)],
     [NATIVE + "test_native_session_conflict", NATIVE + "test_native_model_conflict"]),
    ("native_null_regression_accepted",
     [patch.object(rhc, "check_native_evolution", new=_evolution_without_null_regress)],
     [NATIVE + "test_native_session_regresses_to_null", NATIVE + "test_native_model_regresses_to_null"]),
    ("native_turns_not_compared",
     [patch.object(rhc, "check_native_evolution", new=_evolution_without_turns)],
     [NATIVE + "test_native_turn_replaced", NATIVE + "test_native_turns_dropped"]),
    # 4. event order
    ("transition_check_disabled",
     [patch.object(rhc, "check_transition", new=_noop)],
     [ORDER + "test_reordered_events", ORDER + "test_duplicates"]),
    ("unknown_event_accepted",
     [patch.object(rhc, "check_transition", new=_transition_accepts_unknown)],
     [ORDER + "test_unknown_event"]),
    ("run_may_start_anywhere",
     [patch.dict(rhc.TRANSITIONS, {None: ALL_EVENTS})],
     [ORDER + "test_run_must_start_reserved"]),
    ("append_after_terminal_accepted",
     [patch.dict(rhc.TRANSITIONS, {name: ALL_EVENTS for name in
                                   ("dispatch_failed", "dispatch_uncertain", "collect_timeout", "collected")})],
     [ORDER + "test_append_after_terminal"]),
    ("duplicate_events_accepted",
     [patch.dict(rhc.TRANSITIONS, {"reserved": rhc.TRANSITIONS["reserved"] | {"reserved"},
                                   "dispatched": rhc.TRANSITIONS["dispatched"] | {"dispatched"}})],
     [ORDER + "test_duplicates"]),
    ("collection_without_dispatch_accepted",
     [patch.dict(rhc.TRANSITIONS, {"reserved": rhc.TRANSITIONS["reserved"] | {"collected", "collect_timeout"}})],
     [ORDER + "test_collection_without_dispatch"]),
    # 4 (branches). event shape
    ("event_shape_disabled",
     [patch.object(rhc, "check_event_shape", new=_noop)],
     [SHAPE + "test_reserved_state", SHAPE + "test_dispatched_shape"]),
    ("dispatch_branches_unchecked",
     [patch.object(rhc, "check_event_shape", new=_shape_without_dispatch_branches)],
     [SHAPE + "test_dispatch_failed_branch_not_emitted_by_writer", SHAPE + "test_dispatch_uncertain_shape"]),
    ("collection_may_introduce_identifiers",
     [patch.object(rhc, "check_event_shape", new=_shape_without_collect_identity)],
     [SHAPE + "test_collection_never_introduces_identifiers"]),
    # 3. idempotency scope
    ("idempotency_check_disabled",
     [patch.object(rhc, "check_idempotency", new=_noop)],
     [IDEM + "test_second_run_same_key_same_fingerprint", IDEM + "test_second_run_same_key_other_fingerprint"]),
    # 5. wiring
    ("app_interface_skips_consistency",
     [patch.object(app_api, "check_history", new=_noop)],
     [WIRE + "test_list_returns_no_partial_summary",
      WIRE + "test_detail_of_a_healthy_run_inside_a_corrupt_history"]),
]


def _lenient_parse(text):
    # Same regression as the accepted JH-020 verifier row: validate nothing.
    records = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


def _tolerant_check(records):
    # Lets the lenient parser's malformed record through to the old behaviour.
    return None


ACCEPTED_ROW = "test_jh020_run_history_api.TestRunHistoryStates.test_corrupt_registry_is_reported_and_left_untouched"
REDUNDANT = [
    ("jh020_registry_field_validation_skipped (new layer active)",
     [patch.object(app_api, "parse_registry_text", new=_lenient_parse)]),
    ("jh020_registry_field_validation_skipped (new layer also off)",
     [patch.object(app_api, "parse_registry_text", new=_lenient_parse),
      patch.object(app_api, "check_history", new=_tolerant_check)]),
]


def main():
    flag = patch.object(hc_tests, "CHECK_MESSAGES", new=False)
    flag.start()
    try:
        controls = [run_case(row[-1]) for row in MUTATIONS]
        bad = [row[0] for row, r in zip(MUTATIONS, controls)
               if not (r.wasSuccessful() and r.testsRun == len(row[-1]) and not r.skipped)]
        if bad:
            print(json.dumps({"verdict": "fail", "reason": "baseline control failed", "cases": bad}))
            return 1
        rows = []
        for name, patchers, tests in MUTATIONS:
            with ExitStack() as stack:
                for patcher in patchers:
                    stack.enter_context(patcher)
                result = run_case(tests)
            detected = bool(result.failures) and not result.errors and result.testsRun == len(tests)
            rows.append({"case": name, "detected": detected, "tests": len(tests),
                         "failures": len(result.failures), "errors": len(result.errors)})
    finally:
        flag.stop()
    redundant = []
    control = run_case([ACCEPTED_ROW])
    for name, patchers in REDUNDANT:
        with ExitStack() as stack:
            for patcher in patchers:
                stack.enter_context(patcher)
            result = run_case([ACCEPTED_ROW])
        redundant.append({"case": name, "control_passed": control.wasSuccessful(),
                          "test_failed_on_assertion": bool(result.failures) and not result.errors,
                          "failures": len(result.failures), "errors": len(result.errors),
                          "counted": False})
    passed = all(row["detected"] for row in rows)
    print(json.dumps({"scope": "hark_history_consistency_offline_only", "model_calls": 0,
                      "processes": 0, "message_checks": "off under controls and mutants",
                      "controls": len(controls), "mutations": len(rows),
                      "detected": sum(row["detected"] for row in rows),
                      "rows": rows, "redundant_not_counted": redundant,
                      "verdict": "pass" if passed else "fail"}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
