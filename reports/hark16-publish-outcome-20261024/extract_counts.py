"""Independent count extraction for Hark-派单-16 raw logs. Usage: python reports/hark16-publish-outcome-20261024/extract_counts.py"""
import json, re
from pathlib import Path
L = Path(__file__).resolve().parent / "logs"
for i in (1, 2, 3):
    t = (L / f"hark16-tests-run{i}.txt").read_text(encoding="utf-8")
    ran = re.search(r"^Ran (\d+) tests", t, re.M).group(1)
    rows = [r.split("\t") for r in (L / f"weakening-run{i}.txt").read_text(encoding="utf-8").splitlines() if r]
    c = lambda col, v: sum(1 for r in rows if r[col] == v)
    print(f"run{i}: ran={ran} result={t.strip().splitlines()[-1]} guards={len(rows)} control_pass={c(1,'control=pass')} control_skipped={c(1,'control=skipped')} detected={c(2,'weakened=detected')} missed={c(2,'weakened=MISSED')}")
    r4 = re.findall(r"^(test_\w+) \(tests\.test_hark14_handoff_sync\.TestR4Outcome\.\w+\) \.\.\. (\w+)", t, re.M)
    print(f"  R4 tests: {len(r4)} ok={sum(1 for _, s in r4 if s == 'ok')}")
t = (L / "hark16-tests-no-pwsh.txt").read_text(encoding="utf-8")
print("no-pwsh:", re.search(r"^Ran (\d+)", t, re.M).group(1), t.strip().splitlines()[-1])
for r in json.loads((L / "probe-old-vs-new.json").read_text(encoding="utf-8"))["cases"]:
    print("probe", r)
