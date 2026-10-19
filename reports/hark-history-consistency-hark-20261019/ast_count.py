"""Count test methods: top-level ClassDef in tests/test_*.py, methods named test*."""
import ast, sys
from pathlib import Path
root = Path(sys.argv[1])
total = 0
per = {}
for path in sorted((root / "tests").glob("test_*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    n = sum(1 for node in tree.body if isinstance(node, ast.ClassDef)
            for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test"))
    per[path.name] = n
    total += n
print(total)
if len(sys.argv) > 2:
    print(per.get(sys.argv[2]))
