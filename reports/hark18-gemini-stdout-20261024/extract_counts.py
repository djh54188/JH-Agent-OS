"""Independent count extraction for Hark-派单-18 raw logs.
Usage (repo root): python reports/hark18-gemini-stdout-20261024/extract_counts.py"""
import json, re
from pathlib import Path
L = Path(__file__).resolve().parent / "logs"
LINE = re.compile(r"^(test_\w+) \(([\w\.]+)\)(?: \[[^\]]*\])? \.\.\. (ok|FAIL|ERROR|skipped.*)$", re.M)
def statuses(name):
    t = (L / name).read_text(encoding="utf-8")
    ran = re.search(r"^Ran (\d+)", t, re.M).group(1)
    last = re.findall(r"^(OK.*|FAILED.*)$", t, re.M)[-1]
    rows = {}
    for m in re.finditer(r"^(test_\w+) \(([\w\.]+)\)[^\n]*?\.\.\. ", t, re.M):
        cls = m.group(2) if m.group(2).endswith(m.group(1)) else m.group(2) + "." + m.group(1)
        rest = t[m.end():].lstrip()
        st = "ok" if rest.startswith("ok") else "skipped" if rest.startswith("skipped") else rest.split("\n")[0][:20]
        rows[cls] = st
    return ran, last, rows
for i in (1, 2, 3):
    ran, last, rows = statuses(f"hark18-tests-run{i}.txt")
    w = [r.split("\t") for r in (L / f"weakening-run{i}.txt").read_text(encoding="utf-8").splitlines() if r]
    print(f"run{i}: ran={ran} result={last} skipped={[k for k, v in rows.items() if v == 'skipped']} "
          f"guards={len(w)} control_pass={sum(r[1] == 'control=pass' for r in w)} detected={sum(r[2] == 'weakened=detected' for r in w)}")
b = statuses("regression-base-e3b2017.txt"); c = statuses("regression-candidate.txt")
print(f"regression base: ran={b[0]} {b[1]}; candidate: ran={c[0]} {c[1]}; per-test status identical={b[2] == c[2]} ({len(c[2])} named)")
for r in json.loads((L / "probe-base-vs-candidate.json").read_text(encoding="utf-8")):
    print("probe", r["case"], r["adapter"], r.get("state"), r.get("not_json_problems"), "host_diagnostics=", r.get("host_diagnostics"))
print("constructed replay logic:", (L / "constructed-replay-logic.txt").read_text(encoding="utf-8").strip().splitlines()[-1])
for n in ("linux-dryrun-missing-original.txt", "linux-dryrun-wrong-original.txt"):
    print(n, [l for l in (L / n).read_text(encoding="utf-8").splitlines() if l.startswith("VERDICT")])
