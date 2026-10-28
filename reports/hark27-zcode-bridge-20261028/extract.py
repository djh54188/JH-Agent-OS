"""Independent extraction for Hark-派单-27 logs (stdlib only, no truncation).
Usage (repo root): python reports/hark27-zcode-bridge-20261028/extract.py [log ...]
Prints each log's header, unittest totals, and every WEAKENING27 / PSWEAKENING27 record in full
with a verdict (control not bad, weakened bad). Exit 1 if a log lacks OK or a verdict fails."""
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
    print("  header:", text.splitlines()[:4])
    res = re.findall(r"^(OK.*|FAILED.*)$", text, re.M)
    print("  totals:", re.findall(r"^Ran (\d+) tests? in ([\d.]+)s$", text, re.M), res)
    if not res or not all(r.startswith("OK") for r in res):
        bad += 1
    for line in text.splitlines():
        for tag in ("WEAKENING27 ", "PSWEAKENING27 "):
            if line.startswith(tag):
                for guard, rec in sorted(json.loads(line[len(tag):]).items()):
                    print(f"  {tag.strip()} {guard} {json.dumps(rec, sort_keys=True)}")
                    c, w = rec["control"], rec["weakened"]
                    ok = (c.get("bad") is False and w.get("bad") is True) if "bad" in c else \
                        (not c["probe_ran"] and w["probe_ran"])
                    print(f"    verdict {guard}: {'detected' if ok else 'NOT DETECTED'}")
                    bad += 0 if ok else 1
print("RESULT", "all logs OK and every weakening detected" if not bad else f"{bad} problem(s)")
sys.exit(1 if bad else 0)
