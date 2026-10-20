"""Summarise the frozen 0250dc6 probe_cleanup.py results (a/b identical, minus the python version)."""
import json, sys
from pathlib import Path
d = Path(__file__).resolve().parent / "frozen-probe"
for label in ("baseline", "postcommit"):
    a = json.loads((d / f"probe08-{label}-a.json").read_text()); b = json.loads((d / f"probe08-{label}-b.json").read_text())
    a.pop("python"); b.pop("python")
    print(label, "a==b:", a == b)
    for r in a["rows"]:
        print(" ", r["case"], "thread_starts", r["thread_starts"], r["last_error"].get("code"),
              r["target_before"], "->", r["target_after"], r["target_claim_before"], "->", r["target_claim_after"])
    for c in a["classifications"]:
        print(" ", "active", c["active"], c["mutated"], c["verdict"], "counted", c["counted"])
    m = a["inactive_assertion_main"]
    print("  main exit", m["exit_code"], m["verdict"], m["row"]["verdict"], "counted", m["row"]["counted"])
