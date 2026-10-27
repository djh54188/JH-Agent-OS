"""CONSTRUCTED stand-in for scripts/gemini_task01_run/entry.py, committed only in a TEST clone whose
tree the test config pins (Hark-派单-25 PowerShell exit/output tests). It reads stub.json from the
parent of the work root and prints / exits as told."""
import json
import os
import sys

args = sys.argv[1:]
if "--work-root" in args:
    work_root = args[args.index("--work-root") + 1]
else:
    work_root = os.path.dirname(args[args.index("--context") + 1])
with open(os.path.join(os.path.dirname(work_root), "stub.json"), encoding="utf-8") as fh:
    cfg = json.load(fh)
if cfg.get("stdout") == "single":
    sys.stdout.write(json.dumps({"outcome": cfg.get("label", "X"), "n": 1}) + "\n")
elif cfg.get("stdout") == "multi":
    sys.stdout.write(json.dumps({"outcome": cfg.get("label", "X"), "n": 2}, indent=1) + "\n")
if cfg.get("stderr"):
    sys.stderr.write(cfg["stderr"] + "\n")
sys.exit(int(cfg["exit"]))
