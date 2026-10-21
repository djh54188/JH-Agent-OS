"""AST count of unittest test methods: def test_* inside classes deriving from a *TestCase/_Base-like class (all classes counted if they define test_ methods). Usage: ast_count.py TESTS_DIR"""
import ast, sys, pathlib
total = 0; per = {}
for p in sorted(pathlib.Path(sys.argv[1]).rglob("test_*.py")):
    tree = ast.parse(p.read_text(encoding="utf-8-sig"))
    n = sum(1 for c in ast.walk(tree) if isinstance(c, ast.ClassDef)
            for f in c.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name.startswith("test"))
    per[p.name] = n; total += n
print("total", total)
for k in ("test_hark11_gemini_session.py", "test_jh018_agent_session_contract.py"): print(k, per.get(k))
