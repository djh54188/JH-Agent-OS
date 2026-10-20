"""Hark-派单-06 guard weakening for the GUI session probe.

Each guard is weakened ALONE and the behaviour test that pins it must then fail
by ASSERTION. An error, a skip, zero tests run or a control that is not green is
never counted as a detection.

Python guards are weakened in-process with unittest.mock (src/ is not edited);
GUI guards are weakened in a disposable copy of gui/ that the JS harness is
pointed at through JH_GUI_DIR (gui/ is not edited). No browser, network, real
transport or model is involved.

Classification:
  detected   - control green, mutated target fails with an assertion;
  redundant  - only the rows listed in REDUNDANT, and only when a stated
               diagnostic proves the dispatch count still holds (not counted);
  anything else (control red, error, skip, survived) fails the run.
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
HARNESS = ROOT / "tests" / "js" / "hark06_session_probe_harness.mjs"
NODE = shutil.which("node")

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
    ("history_source_clash_ignored", lambda: mock.patch.object(probe.SessionProbeService, "_same_file",
                                                               lambda self, a, b: False),
     T + "TestRefusalBeforeDispatch.test_history_source_cannot_be_the_probe_registry"),
    ("budget_demand_ignored", lambda: mock.patch.object(probe, "budget_satisfiable", lambda budget: True),
     T + "TestRefusalBeforeDispatch.test_finite_budget_demand_is_refused_not_ignored"),
    ("usage_binding_ignored", lambda: mock.patch.object(probe, "usage_bound_to_run", lambda u, t, n: True),
     T + "TestBinding.test_foreign_reply_and_usage_are_not_adopted"),
    ("echo_check_ignored", lambda: mock.patch.object(probe, "echo_matches", lambda texts, token: True),
     T + "TestBinding.test_wrong_echo_is_not_a_round_trip"),
    ("tool_item_check_ignored", lambda: mock.patch.object(probe, "TOOL_ITEM_TYPES", frozenset()),
     T + "TestBinding.test_tool_item_in_the_stream_is_not_a_clean_probe"),
    ("replay_check_removed", lambda: mock.patch.object(probe, "already_used", lambda pid, consumed, recorded: False),
     T + "TestAtMostOnce.test_same_probe_id_twice_dispatches_once"),
]

# The replay check is backed by single-use prepared ids (deleted on use) and by
# the adapter's idempotency index, so removing it alone cannot cause a second
# dispatch. It is recorded as redundant ONLY when the diagnostic below confirms
# exactly one thread/start after a repeated start.
REDUNDANT = {"replay_check_removed"}

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
    result = unittest.TextTestRunner(stream=io.StringIO()).run(
        unittest.defaultTestLoader.loadTestsFromName(name))
    if result.testsRun == 0:
        return "zero_tests"
    if result.skipped:
        return "skipped"
    if result.errors:
        return "error"
    if result.failures:
        return "assertion"
    return "passed"


def replay_diagnostic():
    """Thread/start count after starting the same probe id twice (mutation active)."""
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(probe_tests.codex_mod, "is_special_path", probe_tests._posix_evidence_seam):
            rig = probe_tests.Rig(tmp)
            prepared, _ = rig.run_probe()
            outcome = "replayed"
            try:
                rig.api.start_session_probe({"probe_id": prepared["probe_id"], "confirm": True})
            except Exception as exc:                  # the single-use prepared id refuses it
                outcome = getattr(exc, "code", type(exc).__name__)
            return {"thread_starts": rig.thread_starts(), "second_start": outcome}


def run_js(gui_dir):
    env = dict(os.environ, JH_GUI_DIR=str(gui_dir))
    proc = subprocess.run([NODE, str(HARNESS), "--json"], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=120)
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


def main() -> int:
    rows = []
    for name, make_patch, target in PY_MUTATIONS:
        control = run_case(target)
        with make_patch():
            mutated = run_case(target)
            diagnostic = replay_diagnostic() if name in REDUNDANT else None
        if control != "passed":
            verdict = "control_failed"
        elif mutated == "assertion" and name not in REDUNDANT:
            verdict = "detected"
        elif name in REDUNDANT and diagnostic and diagnostic["thread_starts"] == 1:
            verdict = "redundant"
        else:
            verdict = "not_detected"
        rows.append({"layer": "python", "case": name, "target": target, "control": control,
                     "mutated": mutated, "diagnostic": diagnostic, "verdict": verdict,
                     "counted": verdict == "detected"})

    if NODE is None:
        rows.append({"layer": "js", "case": "node", "verdict": "control_failed", "counted": False})
    else:
        with tempfile.TemporaryDirectory(prefix="hark06-gui-") as tmp:
            control_dir = Path(tmp) / "control"
            shutil.copytree(ROOT / "gui", control_dir)
            control = run_js(control_dir)
            source = (ROOT / "gui" / "views.js").read_text(encoding="utf-8")
            for name, anchor, replacement, scenario in JS_MUTATIONS:
                row = {"layer": "js", "case": name, "target": scenario, "anchor_count": source.count(anchor)}
                control_ok = bool(control) and control["scenarios"].get(scenario, {}).get("ok") is True
                if source.count(anchor) != 1 or not control_ok:
                    row.update(verdict="control_failed" if not control_ok else "anchor_not_unique", counted=False)
                    rows.append(row)
                    continue
                target_dir = Path(tmp) / name
                shutil.copytree(ROOT / "gui", target_dir)
                (target_dir / "views.js").write_text(source.replace(anchor, replacement, 1), encoding="utf-8")
                report = run_js(target_dir)
                result = (report or {}).get("scenarios", {}).get(scenario, {})
                detected = result.get("ok") is False and result.get("kind") == "assertion"
                row.update(control="passed", mutated=result.get("kind") or ("survived" if result else "no_report"),
                           error=result.get("error"), verdict="detected" if detected else "not_detected",
                           counted=detected)
                rows.append(row)

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
