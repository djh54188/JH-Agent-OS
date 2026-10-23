"""Independent extraction for Hark-派单-14 counts. Usage: python reports/hark14-workflow-handoff-20261023/check_h14.py"""
import ast, hashlib, pathlib, re
here = pathlib.Path(__file__).resolve().parent
root = here.parents[1]
for i in (1, 2, 3):
    t = (here / "logs" / f"hark14-tests-run{i}.txt").read_text(encoding="utf-8")
    print(f"run{i}:", re.findall(r"^Ran (\d+) tests", t, re.M), re.findall(r"^(OK.*|FAILED.*)$", t, re.M))
t = (here / "logs" / "hark14-tests-no-pwsh.txt").read_text(encoding="utf-8")
print("no-pwsh:", re.findall(r"^(OK.*|FAILED.*)$", t, re.M))
for i in (1, 2, 3):
    print(f"weakening run{i}:", (here / "logs" / f"weakening-run{i}.txt").read_text(encoding="utf-8").count("weakened=detected"), "detected of 6")
tree = ast.parse((root / "tests" / "test_hark14_handoff_sync.py").read_text(encoding="utf-8"))
print("ast test methods:", sum(1 for c in tree.body if isinstance(c, ast.ClassDef) for f in c.body
                               if isinstance(f, ast.FunctionDef) and f.name.startswith("test_")))
print("script sha256:", hashlib.sha256((root / "scripts" / "win" / "hark14-handoff-sync.ps1").read_bytes()).hexdigest())
