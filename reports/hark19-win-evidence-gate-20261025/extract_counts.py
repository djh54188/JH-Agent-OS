"""Hark-派单-19: recompute the report's counts from the raw logs in logs/ (stdlib only)."""
import json, re
from pathlib import Path
L = Path(__file__).resolve().parent / "logs"
for i in (1, 2, 3):
    t = (L / f"hark19-tests-run{i}.txt").read_text()
    w = [r for r in (L / f"gate-weakening-run{i}.txt").read_text().splitlines() if r]
    print(f"run{i}: ran={re.search(r'^Ran (\d+)', t, re.M).group(1)} result={t.strip().splitlines()[-1]} "
          f"gates={len(w)} control_pass={sum('control=pass' in r for r in w)} detected={sum('weakened=detected' in r for r in w)} "
          f"MISSED={sum('MISSED' in r for r in w)}")
t = (L / "hark19-tests-no-pwsh.txt").read_text(); print("no-pwsh:", t.strip().splitlines()[-1])
rows = json.loads((L / "probe-old-vs-new.json").read_text())
for e in ("old", "new"):
    rs = [r for r in rows if r["entry"] == e]
    print(e, "cases", len(rs), "PASS/exit0", sum(r["exit"] == 0 for r in rs),
          "faulted cases PASS/exit0", sum(r["exit"] == 0 for r in rs if r["fault"] != "none"))
    for r in rs: print("  ", r["fault"], r["exit"], r["result_json_verdict"], r["failed_stage"], "push" if r["push_line_printed"] else "-")
