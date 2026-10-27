"""Independent extraction for Hark-派单-26 logs (stdlib only, no truncation).
Usage (repo root):  python reports/hark26-entry-boundary-20261027/extract.py [log ...]
Defaults to every *.txt under this directory's logs/. For each log prints: header lines, the
unittest totals, every WEAKENING25 / PSWEAKENING25 record in full (one JSON per guard), and a
per-guard verdict (control not bad, weakened bad). Exit 1 if any log lacks OK or a verdict fails."""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
logs = sys.argv[1:] or sorted(p for p in glob.glob(os.path.join(HERE, "logs", "**", "*.txt"), recursive=True)
                         if os.path.basename(p) != "extract-output.txt")
bad = 0
for path in logs:
    text = open(path, encoding="utf-8", errors="replace").read()
    print("=" * 8, os.path.relpath(path, HERE))
    print("  header:", text.splitlines()[:3])
    ran = re.findall(r"^Ran (\d+) tests? in ([\d.]+)s$", text, re.M)
    res = re.findall(r"^(OK.*|FAILED.*)$", text, re.M)
    print("  totals:", ran, res)
    if not res or not res[-1].startswith("OK"):
        bad += 1
    for line in text.splitlines():
        for tag in ("WEAKENING25 ", "PSWEAKENING25 "):
            if line.startswith(tag):
                data = json.loads(line[len(tag):])
                for guard, rec in sorted(data.items()):
                    print(f"  {tag.strip()} {guard} {json.dumps(rec, sort_keys=True)}")
                    if tag == "WEAKENING25 ":
                        ok = rec["control"]["bad"] is False and rec["weakened"]["bad"] is True
                    else:
                        c, w = rec["control"], rec["weakened"]
                        ok = (c == 4 and w != 4) if isinstance(c, int) else (not c["probe_ran"] and w["probe_ran"])
                    print(f"    verdict {guard}: {'detected' if ok else 'NOT DETECTED'}")
                    bad += 0 if ok else 1
print("RESULT", "all logs OK and every weakening detected" if not bad else f"{bad} problem(s)")
sys.exit(1 if bad else 0)
