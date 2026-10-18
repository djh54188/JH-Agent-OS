"""Isolated check of option A for prepared_version_field (analysis only).
Copies the checkout to a temp dir, rewrites ONLY the first CLI call of
test_cli_prepare_and_complete_roundtrip in the COPY, and runs the unchanged
verify_jh006_guards harness on that one mutation. The repository is not touched.
Usage: python check_option_a.py <repo_root_at_fe0476d>"""
import importlib.util, json, shutil, sys, tempfile
from pathlib import Path
root = Path(sys.argv[1]).resolve()
OLD = '''        code, out = _run_cli(["--db", str(self.db), "--workspace", str(self.ws),
                              "--test-fixture-mode", "prepare-code-delivery", tid, "1"])
        body = json.loads(out)
        self.assertEqual(code, 0)
        scope = body["task_result"]["delivery_scope"]
        self.assertEqual(scope["delivery_state"], "waiting_host")'''
NEW = '''        prepare_buf = io.StringIO()
        with redirect_stdout(prepare_buf):
            prepare_code = cli_main(["--db", str(self.db), "--workspace", str(self.ws),
                                     "--test-fixture-mode", "prepare-code-delivery", tid, "1"])
        body = json.loads(prepare_buf.getvalue())
        self.assertEqual(prepare_code, 0)
        scope = body["task_result"]["delivery_scope"]
        self.assertEqual(scope["delivery_state"], "waiting_host")'''
spec = importlib.util.spec_from_file_location("v", root / "scripts" / "verify_jh006_guards.py")
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)
case = [c for c in v.build_cases() if c["name"] == "prepared_version_field"]
tmp = Path(tempfile.mkdtemp(prefix="jh-optA-"))
try:
    copy = tmp / "repo"
    shutil.copytree(root, copy, ignore=shutil.ignore_patterns(".git", "data", "__pycache__"))
    t = copy / "tests" / "test_jh006_code_delivery.py"
    src = t.read_text(encoding="utf-8"); assert src.count(OLD) == 1
    t.write_text(src.replace(OLD, NEW, 1), encoding="utf-8")
    rep = v.evaluate_mutations(copy, case)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print(json.dumps({"python": sys.version.split()[0], "option": "A (copy only)",
                  "rows": rep["rows"], "verdict": rep["verdict"]}, indent=2, ensure_ascii=False))
