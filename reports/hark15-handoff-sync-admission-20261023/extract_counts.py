"""Independent count extraction for Hark-派单-15 raw logs (no PowerShell/Git needed).
Usage: python reports/hark15-handoff-sync-admission-20261023/extract_counts.py"""
import json, re
from pathlib import Path
L = Path(__file__).resolve().parent / "logs"
for i in (1, 2, 3):
    t = (L / f"hark15-tests-run{i}.txt").read_text(encoding="utf-8")
    ran = re.search(r"^Ran (\d+) tests", t, re.M).group(1)
    last = t.strip().splitlines()[-1]
    rows = [r.split("\t") for r in (L / f"weakening-run{i}.txt").read_text(encoding="utf-8").splitlines() if r]
    ctrl = {k: sum(1 for r in rows if r[1] == f"control={k}") for k in ("pass", "skipped")}
    weak = {k: sum(1 for r in rows if r[2] == f"weakened={k}") for k in ("detected", "MISSED", "skipped", "not-run")}
    print(f"run{i}: ran={ran} result={last} guards={len(rows)} control={ctrl} weakened={weak}")
t = (L / "hark15-tests-no-pwsh.txt").read_text(encoding="utf-8")
print("no-pwsh:", re.search(r"^Ran (\d+)", t, re.M).group(1), t.strip().splitlines()[-1])
p = json.loads((L / "probe-old-vs-new.json").read_text(encoding="utf-8"))
for r in p["r1"]:
    print("R1", r["case"], r["script"], "exit", r["exit"], "final_match", r["final_targets_match"], "moved", r["existing_different_refs_moved"], "left", r["other_new_target_left"])
for r in p["r2"]:
    print("R2", r["url"], r["script"], "transport_reached", r["reached_git_transport"], "refused_before", r["refused_before_transport"])
for k, v in p["r3"].items():
    print("R3", k, v)
