"""Hark-派单-05 targeted weakening of the run-history selection behaviour.

Three single mutations of gui/views.js, each applied ALONE to a disposable copy
of gui/ (gui/ itself is never modified):

  selection_update_removed   - the click no longer marks the current selection;
  detail_positioning_removed - the click no longer positions the detail pane;
  narrow_restriction_removed - positioning is no longer limited to single column.

The selection harness is pointed at the copy through JH_GUI_DIR. A mutation is
detected only when the harness produced its JSON report and the named scenario
failed with kind "assertion"; an exception, a missing report, a load error or a
non-unique anchor is reported and never counted as a detection. The unmutated
copy must pass first. No browser, network or model is involved.
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
HARNESS = ROOT / "tests" / "js" / "hark05_run_selection_harness.mjs"
NODE = shutil.which("node")

MUTATIONS = [
    ("selection_update_removed", "      markSelected(button.dataset.runId);\n", "", "a_to_b"),
    ("detail_positioning_removed", "      revealDetail();\n", "", "breakpoints_1100_1101"),
    ("narrow_restriction_removed", "      if (!isSingleColumn()) { return; }\n", "", "breakpoints_1100_1101"),
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
    with tempfile.TemporaryDirectory(prefix="hark05-views-") as tmp:
        base = Path(tmp) / "control"
        shutil.copytree(GUI, base)
        code, report, err = run_harness(base)
        control_ok = code == 0 and bool(report) and report.get("ok") is True
        source = (GUI / "views.js").read_text(encoding="utf-8")
        for name, anchor, replacement, scenario in MUTATIONS:
            row = {"case": name, "scenario": scenario, "anchor_count": source.count(anchor)}
            if source.count(anchor) != 1:
                row.update(detected=False, outcome="anchor_not_unique")
                rows.append(row)
                continue
            target = Path(tmp) / name
            shutil.copytree(GUI, target)
            (target / "views.js").write_text(source.replace(anchor, replacement, 1), encoding="utf-8")
            code, report, err = run_harness(target)
            if report is None:
                row.update(detected=False, outcome="no_report", exit=code, error=err)
            else:
                result = report.get("scenarios", {}).get(scenario, {})
                failed_names = sorted(k for k, v in report.get("scenarios", {}).items() if not v.get("ok"))
                kinds = {k: report["scenarios"][k].get("kind") for k in failed_names}
                detected = result.get("ok") is False and result.get("kind") == "assertion"
                row.update(detected=detected,
                           outcome="assertion" if detected else (result.get("kind") or "survived"),
                           exit=code, target_error=result.get("error"),
                           failed_scenarios=failed_names, failure_kinds=kinds)
            rows.append(row)
    detected = sum(1 for r in rows if r["detected"])
    verdict = "pass" if control_ok and detected == len(MUTATIONS) else "fail"
    print(json.dumps({"scope": "frontend_copy_only", "model_calls": 0, "browser": False,
                      "control_ok": control_ok, "mutations": len(MUTATIONS), "detected": detected,
                      "rows": rows, "verdict": verdict}, ensure_ascii=False, indent=2))
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
