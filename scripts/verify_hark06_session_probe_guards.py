"""Hark-派单-06/07 guard weakening for the GUI session probe.

Each guard is weakened ALONE and the behaviour test that pins it must then fail
by ASSERTION. Python guards are weakened in-process with unittest.mock (src/ is
not edited); GUI guards are weakened in a disposable copy of gui/ that the JS
harness is pointed at through JH_GUI_DIR (gui/ is not edited). No browser,
network, real transport or model is involved.

Classification (Hark-派单-07 correction, see `classify_python_row`):

  detected   control passed AND mutated target failed by assertion only.
             Counted. A row declared redundant that is in fact detected is
             reported as detected with `redundancy_contradicted: true`.
  redundant  ONLY for rows listed in REDUNDANT, and ONLY when the control passed,
             the mutated formal target ALSO passed cleanly (not error / skip /
             zero tests), the verifier itself observed that the weakening was
             active, and the declared diagnostic is valid. Not counted.
  everything else fails the run: control_failed, mutated error / skipped /
  zero_tests / unexpected, mutation_inactive, mutation_activity_unproven,
  invalid_diagnostic, not_detected.

Hark-派单-08: `mutation_active is False` (the verifier PROVED the weakening
inactive) fails the row before any outcome is read, so an assertion failure of
an unweakened target is never a detection. `None` means no proof was taken:
ordinary detected rows need none (their assertion is the evidence); a
redundancy claim with no proof is `mutation_activity_unproven`.

The 06 version classified `replay_check_removed` as redundant from the side
diagnostic alone while its formal target had ERRORED (mutated=error); that
"16 detected + 1 redundant, pass" summary was wrong and is corrected here.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import src.gui_http as gui_http  # noqa: E402
import src.session_probe as probe  # noqa: E402
import test_hark06_session_probe as probe_tests  # noqa: E402

T = "test_hark06_session_probe."
S = "test_hark07_source_isolation."
C = "test_hark08_cleanup_isolation."
HARNESS = ROOT / "tests" / "js" / "hark06_session_probe_harness.mjs"
NODE = shutil.which("node")


def _always_create(original):
    def lock(probe_dir, timeout=None, *, create=True):
        return original(probe_dir, timeout, create=True)
    return lock


def _no_lock(probe_dir, timeout=None, **_):
    import contextlib
    return contextlib.nullcontext()


PY_MUTATIONS = [
    ("disabled_switch_ignored", lambda: mock.patch.object(probe, "probe_enabled", lambda config: True),
     T + "TestZeroDispatch.test_disabled_refuses_prepare_and_start"),
    ("confirmation_ignored", lambda: mock.patch.object(probe, "confirmation_given", lambda payload: True),
     T + "TestZeroDispatch.test_unconfirmed_start_dispatches_nothing"),
    ("field_whitelist_ignored", lambda: mock.patch.object(probe, "payload_fields_allowed", lambda payload: True),
     T + "TestZeroDispatch.test_forbidden_fields_dispatch_nothing"),
    ("origin_check_ignored", lambda: mock.patch.object(gui_http, "probe_request_allowed",
                                                       lambda headers, address: (True, "")),
     T + "TestHttpBoundary.test_illegal_origin_or_host_dispatches_nothing"),
    ("inflight_blocker_ignored", lambda: mock.patch.object(probe, "inflight_blocks", lambda inflight: False),
     T + "TestAtMostOnce.test_second_confirmation_while_in_flight_is_refused"),
    ("unresolved_blocker_ignored", lambda: mock.patch.object(probe, "run_is_resolved", lambda events: True),
     T + "TestAtMostOnce.test_thread_start_timeout_stays_unknown_and_blocks"),
    ("registry_state_ignored", lambda: mock.patch.object(probe, "registry_usable", lambda state: True),
     T + "TestRefusalBeforeDispatch.test_corrupt_json_registry_refuses"),
    ("history_source_clash_ignored", lambda: mock.patch.object(probe, "history_conflict", lambda r, d, h: None),
     T + "TestRefusalBeforeDispatch.test_history_source_cannot_be_the_probe_registry"),
    ("budget_demand_ignored", lambda: mock.patch.object(probe, "budget_satisfiable", lambda budget: True),
     T + "TestRefusalBeforeDispatch.test_finite_budget_demand_is_refused_not_ignored"),
    ("usage_binding_ignored", lambda: mock.patch.object(probe, "usage_bound_to_run", lambda u, t, n: True),
     T + "TestBinding.test_foreign_reply_and_usage_are_not_adopted"),
    ("echo_check_ignored", lambda: mock.patch.object(probe, "echo_matches", lambda texts, token: True),
     T + "TestBinding.test_wrong_echo_is_not_a_round_trip"),
    ("tool_item_check_ignored", lambda: mock.patch.object(probe, "TOOL_ITEM_TYPES", frozenset()),
     T + "TestBinding.test_tool_item_in_the_stream_is_not_a_clean_probe"),
    # Hark-派单-07: the formal target accepts either a replay response or a
    # refusal and asserts only the dispatch count, so a removed replay check
    # passes it CLEANLY (single-use prepared ids + adapter idempotency).
    ("replay_check_removed", lambda: mock.patch.object(probe, "already_used", lambda pid, consumed, recorded: False),
     T + "TestAtMostOnce.test_repeat_start_of_one_id_never_dispatches_twice"),
    # Hark-派单-07 source exclusion and isolation guards.
    ("source_claim_ignored", lambda: mock.patch.object(probe, "claim_blocks", lambda state, claim: False),
     S + "TestSourceExclusion.test_second_object_refused_after_accept_before_reserve"),
    ("source_lock_disabled", lambda: mock.patch.object(probe, "acquire_source_lock", _no_lock),
     S + "TestSourceExclusion.test_lock_serialises_check_and_claim_across_objects"),
    ("hardlink_identity_ignored", lambda: mock.patch.object(probe, "file_identity_problem", lambda path: None),
     S + "TestSourceIsolation.test_registry_hardlinked_to_an_unrelated_file_is_refused"),
    ("ancestor_link_check_ignored", lambda: mock.patch.object(probe, "link_component", lambda path: None),
     S + "TestSourceIsolation.test_linked_ancestor_is_refused_before_any_write"),
    ("subdir_link_check_ignored", lambda: mock.patch.object(probe, "dir_problem", lambda path: None),
     S + "TestSourceIsolation.test_linked_raw_and_ws_are_refused"),
    ("worker_recheck_ignored", lambda: mock.patch.object(probe.SessionProbeService, "_precheck_before_submit",
                                                         lambda self, probe_id=None: None),
     S + "TestSourceIsolation.test_layout_changed_after_accept_is_refused_before_submit"),
    # Hark-派单-08 cleanup guards.
    ("cleanup_check_ignored", lambda: mock.patch.object(probe.SessionProbeService, "_cleanup_problem",
                                                        lambda self, probe_id: None),
     C + "TestCleanupAfterPlainReplacement.test_swapped_plain_directory_is_neither_dispatched_to_nor_cleaned_up"),
    ("source_identity_recheck_ignored", lambda: mock.patch.object(probe.SessionProbeService, "_identity_problem",
                                                                  lambda self, probe_id: None),
     C + "TestCleanupAfterPlainReplacement.test_replaced_claim_file_is_not_rewritten"),
    ("lock_recreate_guard_ignored", lambda: mock.patch.object(probe, "acquire_source_lock",
                                                              _always_create(probe.acquire_source_lock)),
     C + "TestCleanupAfterPlainReplacement.test_missing_lock_is_not_recreated_and_the_claim_stays_blocking"),
]

REDUNDANT = {"replay_check_removed"}

# The verifier's OWN proof that a declared-redundant weakening is active while
# the formal target runs. A row whose mutation is not demonstrably active can
# never be called redundant.
MUTATION_ACTIVE_CHECKS = {
    "replay_check_removed": lambda: probe.already_used("0" * 16, {"0" * 16}, True) is False,
}

DIAGNOSTIC_SECOND_START = frozenset({"replayed", "probe_not_prepared"})

JS_MUTATIONS = [
    ("double_click_guard_removed",
     "if (busy || start.disabled || checkbox.checked !== true || !canStart) { return; }",
     "if (checkbox.checked !== true || !canStart) { return; }", "confirmed_click_once"),
    ("confirmation_check_removed",
     "if (busy || start.disabled || checkbox.checked !== true || !canStart) { return; }",
     "if (busy || !canStart) { return; }", "unconfirmed_click_does_nothing"),
    ("left_view_start_guard_removed",
     "          if (!isCurrent(gen)) { return; }\n          if (!prepared || prepared.ok !== true) {",
     "          if (!prepared || prepared.ok !== true) {", "leave_before_start"),
    ("poll_bound_removed",
     "if (polls < Math.ceil(seconds * 1000 / PROBE_POLL_MS)) {", "if (true) {", "inflight_polling_bounded"),
]


def run_case(name):
    """passed | assertion | error | skipped | zero_tests for one named test."""
    try:
        suite = unittest.defaultTestLoader.loadTestsFromName(name)
        result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    except Exception:
        return "error"
    if result.testsRun == 0:
        return "zero_tests"
    if result.errors or getattr(result, "unexpectedSuccesses", None):
        return "error"
    if result.skipped or getattr(result, "expectedFailures", None):
        return "skipped"
    if result.failures:
        return "assertion"
    return "passed"


def diagnostic_valid(diagnostic):
    """(ok, reason). A diagnostic must be a complete observation, not a token."""
    if not isinstance(diagnostic, dict):
        return False, "diagnostic is not an object"
    if diagnostic.get("error"):
        return False, f"diagnostic raised: {diagnostic['error']}"
    starts = diagnostic.get("thread_starts")
    if isinstance(starts, bool) or not isinstance(starts, int):
        return False, "thread_starts is not an integer"
    if starts != 1:
        return False, f"thread_starts={starts}, expected exactly 1"
    if diagnostic.get("first_start_accepted") is not True:
        return False, "the first start was not observed as accepted"
    if diagnostic.get("second_start") not in DIAGNOSTIC_SECOND_START:
        return False, f"second_start={diagnostic.get('second_start')!r} is not a known outcome"
    return True, ""


def classify_python_row(name, control, mutated, mutation_active, diagnostic):
    """(verdict, counted, extra). Pure; the meta-tests drive every branch."""
    extra = {}
    if control != "passed":
        return "control_failed", False, extra
    if mutation_active is False:
        # Hark-派单-08: the verifier itself established that the weakening did
        # NOT take effect. Whatever the target did (even fail by assertion),
        # that run is no evidence about the guard, so it can never count.
        extra["mutation_activity"] = "explicitly_inactive"
        return "mutation_inactive", False, extra
    if mutated == "assertion":
        if name in REDUNDANT:
            extra["redundancy_contradicted"] = True
        return "detected", True, extra
    if mutated != "passed":
        return f"mutated_{mutated}", False, extra
    if name not in REDUNDANT:
        return "not_detected", False, extra
    if mutation_active is not True:
        # Unset (None): no activity proof was taken. Not the same as an
        # explicit False, but a redundancy claim needs a positive proof.
        extra["mutation_activity"] = "unproven"
        return "mutation_activity_unproven", False, extra
    ok, reason = diagnostic_valid(diagnostic)
    if not ok:
        extra["diagnostic_problem"] = reason
        return "invalid_diagnostic", False, extra
    return "redundant", False, extra


def replay_diagnostic():
    """Observe, with the replay check removed, how many dispatches one id causes."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(probe_tests.codex_mod, "is_special_path", probe_tests._posix_evidence_seam):
                rig = probe_tests.Rig(tmp)
                prepared, first = rig.run_probe()
                outcome = "replayed"
                try:
                    rig.api.start_session_probe({"probe_id": prepared["probe_id"], "confirm": True})
                except Exception as exc:
                    outcome = getattr(exc, "code", type(exc).__name__)
                return {"thread_starts": rig.thread_starts(), "first_start_accepted": first.get("accepted") is True,
                        "second_start": outcome}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


DIAGNOSTICS = {"replay_check_removed": lambda: replay_diagnostic()}


def run_js(gui_dir):
    env = dict(os.environ, JH_GUI_DIR=str(gui_dir))
    proc = subprocess.run([NODE, str(HARNESS), "--json"], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=120)
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


def classify_js_row(control_ok, anchor_count, result):
    if anchor_count != 1:
        return "anchor_not_unique", False
    if not control_ok:
        return "control_failed", False
    if not isinstance(result, dict) or not result:
        return "no_report", False
    if result.get("ok") is False and result.get("kind") == "assertion":
        return "detected", True
    if result.get("ok") is False:
        return f"mutated_{result.get('kind') or 'unknown'}", False
    return "not_detected", False


def main() -> int:
    rows = []
    for entry in PY_MUTATIONS:
        name, make_patch, target = entry[0], entry[1], entry[2]
        control = run_case(target)
        mutation_active = None
        diagnostic = None
        try:
            with make_patch():
                mutated = run_case(target)
                check = MUTATION_ACTIVE_CHECKS.get(name)
                if check is not None:
                    try:
                        mutation_active = bool(check())
                    except Exception:
                        mutation_active = False
                if name in REDUNDANT:
                    diagnostic = (DIAGNOSTICS.get(name) or replay_diagnostic)()
        except Exception as exc:
            mutated = "error"
            diagnostic = {"error": f"patch failed: {exc}"}
        verdict, counted, extra = classify_python_row(name, control, mutated, mutation_active, diagnostic)
        rows.append(dict({"layer": "python", "case": name, "target": target, "control": control,
                          "mutated": mutated, "mutation_active": mutation_active, "diagnostic": diagnostic,
                          "verdict": verdict, "counted": counted}, **extra))

    if NODE is None:
        rows.append({"layer": "js", "case": "node", "verdict": "control_failed", "counted": False})
    else:
        with tempfile.TemporaryDirectory(prefix="hark06-gui-") as tmp:
            control_dir = Path(tmp) / "control"
            shutil.copytree(ROOT / "gui", control_dir)
            control = run_js(control_dir) or {}
            source = (ROOT / "gui" / "views.js").read_text(encoding="utf-8")
            for name, anchor, replacement, scenario in JS_MUTATIONS:
                control_ok = (control.get("scenarios") or {}).get(scenario, {}).get("ok") is True
                count = source.count(anchor)
                result = None
                if count == 1 and control_ok:
                    target_dir = Path(tmp) / name
                    shutil.copytree(ROOT / "gui", target_dir)
                    (target_dir / "views.js").write_text(source.replace(anchor, replacement, 1), encoding="utf-8")
                    result = ((run_js(target_dir) or {}).get("scenarios") or {}).get(scenario)
                verdict, counted = classify_js_row(control_ok, count, result)
                rows.append({"layer": "js", "case": name, "target": scenario, "anchor_count": count,
                             "control": "passed" if control_ok else "failed",
                             "mutated": (result or {}).get("kind") or ("survived" if result else "not_run"),
                             "error": (result or {}).get("error"), "verdict": verdict, "counted": counted})

    detected = sum(1 for r in rows if r["verdict"] == "detected")
    redundant = sum(1 for r in rows if r["verdict"] == "redundant")
    bad = [r["case"] for r in rows if r["verdict"] not in ("detected", "redundant")]
    summary = {"scope": "in-process patches + disposable gui copy", "model_calls": 0, "real_transport": False,
               "python_mutations": len(PY_MUTATIONS), "js_mutations": len(JS_MUTATIONS),
               "detected": detected, "redundant": redundant, "failed_rows": bad,
               "verdict": "pass" if not bad else "fail", "rows": rows}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
