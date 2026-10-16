"""Reproducible offline review; use --out data/... for postcommit evidence."""
import argparse
import ast
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument("--out", default=str(Path(__file__).resolve().parent))
args = parser.parse_args()
OUT = Path(args.out).resolve()
OUT.mkdir(parents=True, exist_ok=True)
PY = [sys.executable, "-X", "utf8", "-B"]
commands = {
    "tests": PY + ["-m", "unittest", "discover", "-s", "tests"],
    "capture_guards": PY + ["scripts/verify_jh006_capture_guards.py"],
    "jh005_fixtures": PY + ["scripts/verify_jh005_fixtures.py"],
    "jh005_mutations": PY + ["scripts/verify_jh005_mutations.py"],
    "jh006_guards": PY + ["scripts/verify_jh006_guards.py"],
    "host_boundary": PY + ["scripts/verify_jh006_host_boundary.py"],
    "phase2_guards": PY + ["scripts/verify_phase2_guards.py"],
    "phase3_guards": PY + ["scripts/verify_phase3_guards.py"],
    "phase4_guards": PY + ["scripts/verify_phase4_guards.py"],
    "responses_guards": PY + ["scripts/verify_responses_guards.py"],
    "anthropic_guards": PY + ["scripts/verify_anthropic_guards.py"],
}
# This historical guard writes a tracked report. Redirect that write alone.
redirect = """
from pathlib import Path
from unittest.mock import patch
import runpy
original = Path.write_text
wanted = Path.cwd() / 'reports/phase2-hardening-verification.json'
out = Path(%r)
def write(self, data, *a, **kw):
    if self.resolve() == wanted.resolve():
        self = out / 'phase2-hardening-verification.json'
    return original(self, data, *a, **kw)
with patch.object(Path, 'write_text', write):
    runpy.run_path('scripts/verify_phase2_hardening.py', run_name='__main__')
""" % str(OUT)
commands["phase2_hardening"] = PY + ["-c", redirect]


def run(pair):
    name, command = pair
    p = subprocess.run(command, capture_output=True, text=True, encoding="utf8", timeout=180)
    output = p.stdout + p.stderr
    (OUT / (name + ".txt")).write_text(output, encoding="utf8")
    return name, {"exit": p.returncode, "tail": output[-1100:]}


with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    results = dict(pool.map(run, commands.items()))
source_count = sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")
                   for p in (ROOT / "tests").glob("test_*.py")
                   for n in ast.walk(ast.parse(p.read_text(encoding="utf-8-sig")) ))
reported = re.search(r"Ran (\d+) tests", results["tests"]["tail"])
base = "dce2e773c36f70c36003f8f05f93bace0758bb98"
runtime_files = ["src/cli.py", "src/orchestrator.py", "src/storage.py", "src/acceptance.py"]
unchanged = {}
for name in runtime_files:
    old = subprocess.check_output(["git", "show", base + ":" + name])
    # Git's baseline uses normalized newlines, compare via git hash-object filters.
    current = subprocess.check_output(["git", "hash-object", name], text=True).strip()
    baseline = subprocess.check_output(["git", "rev-parse", base + ":" + name], text=True).strip()
    unchanged[name] = current == baseline
# Raw archives and the sealed fixtures must stay exactly as accepted.
preserved = {}
for folder in ("tests/fixtures/n2", "reports/jh006-host-review-20261016/submitted"):
    for path in (ROOT / folder).rglob("*"):
        if path.is_file():
            rel = path.relative_to(ROOT).as_posix()
            old = subprocess.check_output(["git", "show", base + ":" + rel])
            preserved[rel] = old == path.read_bytes()
from src.host_capture import verify_capture_archive
archive = ROOT / "data/jh006-capture-proof-20261016/real-desktop-capture"
manifest = json.loads((archive / "manifest.json").read_text(encoding="utf8"))
verify_capture_archive(manifest, archive_raw=(archive / "events.jsonl").read_bytes())
summary = {"commands": results, "independent_ast_count": source_count,
    "reported_test_count": int(reported.group(1)) if reported else None,
    "runtime_unchanged": unchanged, "preserved_bytes": preserved,
    "real_capture_archive_readback": True,
    "scope": "capture-only, no production completion, no provider calls"}
summary["passed"] = (all(r["exit"] == 0 for r in results.values())
    and source_count == 299 and summary["reported_test_count"] == 299
    and all(unchanged.values()) and all(preserved.values()))
(OUT / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8")
print(json.dumps({k:v for k,v in summary.items() if k not in ["commands", "preserved_bytes"]}, ensure_ascii=False))
for name, result in results.items():
    print(name, result["exit"], result["tail"][-240:].replace("\n", " "))
raise SystemExit(0 if summary["passed"] else 1)
