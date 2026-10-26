"""Hark-派单-24: independent extraction from the raw logs (stdlib only)."""
import ast, json, os, re
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
out = {}
for i in (1, 2, 3):
    t = open(os.path.join(HERE, "logs", f"hark24-tests-run{i}.txt"), encoding="utf-8").read()
    w = json.loads(re.findall(r"^WEAKENING24 (\{.*\})$", t, re.M)[-1])
    out[f"run{i}"] = {"ran": re.findall(r"^Ran (\d+) tests", t, re.M), "status": re.findall(r"^(OK.*|FAILED.*)$", t, re.M),
                      "ok_lines": len(re.findall(r" \.\.\. ok$", t, re.M)), "skipped": len(re.findall(r" \.\.\. skipped", t, re.M)),
                      "weakening": {k: [v["control"], v["control_exit"], v["control_dispatches"], v["weakened"],
                                        v["weakened_mechanical"], v["weakened_exit"], v["weakened_dispatches"]] for k, v in w.items()}}
r = open(os.path.join(HERE, "logs", "related-regression.txt"), encoding="utf-8").read()
out["related"] = {"ran": re.findall(r"^Ran (\d+) tests", r, re.M), "status": re.findall(r"^(OK.*|FAILED.*)$", r, re.M)}
tree = ast.parse(open(os.path.join(ROOT, "tests", "test_hark24_gemini_task01_entry.py"), encoding="utf-8").read())
out["ast_test_methods"] = sum(isinstance(n, ast.FunctionDef) and n.name.startswith("test_") for n in ast.walk(tree))
print(json.dumps(out, indent=1, sort_keys=True))
