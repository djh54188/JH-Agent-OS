"""JH-020 detail-association guard verification by frontend mutation.

Each guard in gui/views.js that binds the detail pane to the current panel, the
latest request and the requested run id is removed ALONE in a disposable copy of
the GUI sources; the behaviour harness must then report the corresponding
scenario as failed. A baseline run against an unmutated copy must pass first.

Nothing in gui/ is modified: the copy lives in a temp directory and the harness
is pointed at it through JH_GUI_DIR. No browser, network or model is involved.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui"
HARNESS = ROOT / "tests" / "js" / "jh020_run_history_harness.mjs"
NODE = shutil.which("node")

GENERATION_GUARD = "        if (myGeneration !== panelDetailGeneration) { return; }   // a newer detail request exists\n"
REQUESTED_RUN_GUARD = "        if (currentDetailRunId !== requestedRunId) { return; }\n"
PANEL_GUARD = "        if (panelGeneration !== renderGeneration) { return; }     // this panel was replaced\n"
RUN_ID_BLOCK = """        if (envelope && envelope.ok === true && envelope.run
            && envelope.run.run_id !== requestedRunId) {
"""

# Guards that must be independently detectable: weakening each one has to turn
# its scenario red.
MUTATIONS = [
    ("detail_request_generation_removed", GENERATION_GUARD, "", "detail_same_id_repeat"),
    ("detail_response_run_id_ignored", RUN_ID_BLOCK, "        if (false) {\n", "detail_unrequested_run_id"),
]

# Defence in depth whose removal is masked by another guard or by the DOM, so a
# mutation cannot turn a rendered assertion red. Recorded honestly as redundant,
# never counted as a detection.
REDUNDANT_MUTATIONS = [
    ("detail_requested_run_binding_removed", REQUESTED_RUN_GUARD, "", "detail_reverse_replies"),
    ("detail_panel_generation_removed", PANEL_GUARD, "", "detail_leave_view"),
]


def run_harness(gui_dir):
    env = dict(os.environ)
    env["JH_GUI_DIR"] = str(gui_dir)
    proc = subprocess.run([NODE, str(HARNESS), "--json"], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=120)
    try:
        report = json.loads(proc.stdout)
    except ValueError:
        return proc.returncode, None, proc.stderr[-800:]
    return proc.returncode, report, ""


def main():
    if NODE is None:
        print(json.dumps({"verdict": "fail", "reason": "node is not available"}))
        return 1
    rows = []
    with tempfile.TemporaryDirectory(prefix="jh020-views-") as tmp:
        base = Path(tmp) / "baseline"
        shutil.copytree(GUI, base)
        code, report, err = run_harness(base)
        if code != 0 or not report or not report.get("ok"):
            print(json.dumps({"verdict": "fail", "reason": "baseline control failed",
                              "detail": err or report}, ensure_ascii=False))
            return 1
        source = (GUI / "views.js").read_text(encoding="utf-8")
        for name, anchor, replacement, scenario in MUTATIONS + REDUNDANT_MUTATIONS:
            redundant = any(name == row[0] for row in REDUNDANT_MUTATIONS)
            if source.count(anchor) != 1:
                rows.append({"case": name, "detected": False, "redundant": redundant,
                             "reason": f"anchor occurs {source.count(anchor)} times"})
                continue
            target = Path(tmp) / name
            shutil.copytree(GUI, target)
            (target / "views.js").write_text(source.replace(anchor, replacement, 1),
                                             encoding="utf-8")
            code, report, err = run_harness(target)
            failed = (report is not None
                      and report.get("scenarios", {}).get(scenario, {}).get("ok") is False)
            rows.append({"case": name, "scenario": scenario, "detected": bool(failed),
                         "redundant": redundant, "exit": code, "error": err or None})
    required = [row for row in rows if not row.get("redundant")]
    passed = all(row["detected"] for row in required)
    print(json.dumps({"scope": "frontend_copy_only", "model_calls": 0, "browser": False,
                      "controls": 1, "mutations": len(required),
                      "detected": sum(1 for row in required if row["detected"]),
                      "redundant": sum(1 for row in rows if row.get("redundant")),
                      "rows": rows, "verdict": "pass" if passed else "fail"}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
