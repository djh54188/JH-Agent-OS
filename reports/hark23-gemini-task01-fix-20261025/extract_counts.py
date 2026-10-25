"""Hark-派单-23: independent extraction of counts from the raw logs (stdlib only).
Usage: python reports/hark23-gemini-task01-fix-20261025/extract_counts.py"""
import ast
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
out = {}
for i in (1, 2, 3):
    text = open(os.path.join(HERE, "logs", f"hark23-tests-run{i}.txt"), "rb").read().decode("utf-8")
    weak = re.findall(r"^WEAKENING (\{.*\})$", text, re.M)
    w = json.loads(weak[-1]) if weak else {}
    sent = {k: (v["sentinel_access_control"], v["sentinel_access_weakened"]) for k, v in w.items()
            if "sentinel_access_control" in v}
    out[f"run{i}"] = {
        "r23_cases": {k: (v["control"], v["control_exit"], v["weakened"], v["weakened_exit"]) for k, v in w.items() if k.startswith("R23")},
        "ran": re.findall(r"^Ran (\d+) tests? in", text, re.M),
        "status": re.findall(r"^(OK.*|FAILED.*)$", text, re.M),
        "cases": len(w),
        "guards": sorted({v["guard"] for v in w.values()}),
        "control_not_all_pass": sum(1 for v in w.values() if v["control"] != "all_pass" and v["control_exit"] != 0),
        "control_unknown_only": sorted(k for k, v in w.items() if v["control"] == "undetermined"),
        "weakened_all_pass_exit0": sum(1 for v in w.values() if v["weakened"] == "all_pass" and v["weakened_exit"] == 0),
        "io_sentinel_access_control_weakened": sent,
    }
tree = ast.parse(open(os.path.join(ROOT, "tests", "test_hark21_gemini_task01.py"), "rb").read())
out["ast_test_methods"] = sum(1 for n in ast.walk(tree)
                              if isinstance(n, ast.FunctionDef) and n.name.startswith("test_"))
print(json.dumps(out, indent=1, sort_keys=True))
