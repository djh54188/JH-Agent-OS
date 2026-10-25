"""Hark-派单-21: independent extraction of counts from the raw logs (stdlib only).
Usage: python reports/hark21-gemini-task01-20261025/extract_counts.py"""
import ast
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
out = {}
for i in (1, 2, 3):
    text = open(os.path.join(HERE, "logs", f"hark21-tests-run{i}.txt"), "rb").read().decode("utf-8")
    ran = re.findall(r"^Ran (\d+) tests? in", text, re.M)
    status = re.findall(r"^(OK.*|FAILED.*)$", text, re.M)
    weak = re.findall(r"^WEAKENING (\{.*\})$", text, re.M)
    w = json.loads(weak[-1]) if weak else {}
    out[f"run{i}"] = {"ran": ran, "status": status,
                      "guards": len(w),
                      "control_fail": sum(1 for c, _ in w.values() if c == "fail"),
                      "weakened_no_longer_fail": sum(1 for _, x in w.values() if x != "fail")}
tree = ast.parse(open(os.path.join(ROOT, "tests", "test_hark21_gemini_task01.py"), "rb").read())
out["ast_test_methods"] = sum(1 for n in ast.walk(tree)
                              if isinstance(n, ast.FunctionDef) and n.name.startswith("test_"))
print(json.dumps(out, indent=1, sort_keys=True))
