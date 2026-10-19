"""Independent test count by AST: unittest.TestCase subclasses' test_* methods
in tests/test_*.py (the discover pattern). Not grep: literal 'def test_' inside
strings is not counted."""
import ast, pathlib, sys
root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "tests")
total = 0
for path in sorted(root.glob("test_*.py")):
    tree = ast.parse(path.read_bytes(), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            total += sum(1 for item in node.body
                         if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and item.name.startswith("test"))
print(total)
