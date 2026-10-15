"""Independent mutation checks for the JH-005 fixture verifier (offline).

Usage (from repo root):
    python scripts/verify_jh005_mutations.py

Copies the checker, fixtures, src/ and the task schema into an isolated
scratch dir, seals one control copy, then applies fourteen inconsistency
mutations (mirroring the independent review probes). Each variant must exit
nonzero BECAUSE OF its corresponding check; harness crashes never count as
detection. Prints a JSON report; exit 0 only when the control passes and all
fourteen variants are detected by assertion with zero harness errors.
No models, no network. Scratch is cleaned up; originals are never touched.
"""
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / ".test-tmp" / ("jh005-mutations-" + uuid.uuid4().hex[:8])

# variant -> check name the checker must report (among its failures).
EXPECTED = {
    "data_input_mismatch": "task-input-match",
    "manifest_empty": "manifest-inventory",
    "manifest_omits_expected": "manifest-inventory",
    "absolute_target": "target_files",
    "scenarios_empty": "scenarios-complete",
    "checks_empty": "checks-complete",
    "boolean_sales": "input-shape",
    "manifest_string_seal": "seal-boolean",
    "post_apply_wrong_target": "post-apply-binding",
    "post_apply_wrong_parameter": "post-apply-binding",
    "post_apply_wrong_script": "post-apply-binding",
    "post_apply_bool_exit": "post-apply-binding",
    "code_wrong_authorization": "target_files",
    "data_wrong_authorization": "target_files",
}


def make_copy(name):
    root = CASE / name
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/verify_jh005_fixtures.py",
                 root / "scripts/verify_jh005_fixtures.py")
    shutil.copytree(ROOT / "tests/fixtures/n2", root / "tests/fixtures/n2")
    shutil.copytree(ROOT / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__"))
    (root / "docs/schemas").mkdir(parents=True)
    shutil.copy2(ROOT / "docs/schemas/task.v1.json", root / "docs/schemas/task.v1.json")
    return root


def put(root, rel, value):
    (root / rel).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def load(root, rel):
    return json.loads((root / rel).read_text(encoding="utf-8"))


def seal(root):
    import hashlib
    for fixture in ("code_sortfix", "data_rollup"):
        folder = root / "tests/fixtures/n2" / fixture
        manifest_path = folder / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sealed"] = True
        for entry in manifest["files"]:
            if entry["path"] != "manifest.json":
                entry["sha256"] = hashlib.sha256((folder / entry["path"]).read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def mutate(root, kind):
    taskpath = "tests/fixtures/n2/data_rollup/task.json"
    if kind == "data_input_mismatch":
        task = load(root, taskpath)
        task["metadata"]["input_data"]["records"] = [{"region": "different", "sales": 999}]
        put(root, taskpath, task)
    elif kind == "manifest_empty":
        path = "tests/fixtures/n2/data_rollup/manifest.json"
        manifest = load(root, path)
        manifest["files"] = []
        manifest["sealed"] = True
        put(root, path, manifest)
    elif kind == "manifest_omits_expected":
        path = "tests/fixtures/n2/data_rollup/manifest.json"
        manifest = load(root, path)
        manifest["files"] = [e for e in manifest["files"] if e["path"] != "expected.json"]
        put(root, path, manifest)
    elif kind == "absolute_target":
        task = load(root, taskpath)
        task["context"]["target_files"] = [r"C:\fixture-outside\widget.py"]
        put(root, taskpath, task)
    elif kind == "scenarios_empty":
        path = "tests/fixtures/n2/data_rollup/scenarios.json"
        doc = load(root, path)
        doc["scenarios"] = []
        put(root, path, doc)
    elif kind == "checks_empty":
        path = "tests/fixtures/n2/data_rollup/checks.json"
        doc = load(root, path)
        doc["checks"] = []
        put(root, path, doc)
    elif kind == "boolean_sales":
        for name in ("input.json", "expected.json"):
            path = "tests/fixtures/n2/data_rollup/" + name
            rows = load(root, path)
            rows = [dict(r, sales=True) if r["region"] == "west" else r for r in rows]
            put(root, path, rows)
    elif kind == "manifest_string_seal":
        path = "tests/fixtures/n2/data_rollup/manifest.json"
        doc = load(root, path)
        doc["sealed"] = "false"
        put(root, path, doc)
    elif kind.startswith("post_apply_"):
        path = "tests/fixtures/n2/code_sortfix/checks.json"
        doc = load(root, path)
        item = next(c for c in doc["checks"] if c["name"] == "post-apply-target-check")
        if kind == "post_apply_wrong_target":
            item["command"][-1] = "{HOST_WORKSPACE}/other/widget.py"
        elif kind == "post_apply_wrong_parameter":
            item["command"][-1] = "{OTHER_WORKSPACE}/target/widget.py"
            item["materialization"] = {"OTHER_WORKSPACE": "Another workspace"}
        elif kind == "post_apply_wrong_script":
            item["command"][1] = "tests/fixtures/n2/code_sortfix/target/widget.py"
        elif kind == "post_apply_bool_exit":
            item["expect_exit"] = False
        put(root, path, doc)
    elif kind in ("code_wrong_authorization", "data_wrong_authorization"):
        fixture = "code_sortfix" if kind == "code_wrong_authorization" else "data_rollup"
        path = f"tests/fixtures/n2/{fixture}/task.json"
        task = load(root, path)
        task["context"]["target_files"] = ["other.py"]
        put(root, path, task)


def run_checker(root):
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", "scripts/verify_jh005_fixtures.py"],
        cwd=root, text=True, capture_output=True, encoding="utf-8", timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def main():
    CASE.mkdir(parents=True)
    rows = []
    ok = True
    try:
        root = make_copy("sealed_control")
        seal(root)
        code, out, err = run_checker(root)
        try:
            body = json.loads(out)
            passed = code == 0 and body.get("status") == "ok"
        except ValueError:
            passed = False
        rows.append({"case": "sealed_control", "exit_code": code,
                     "verdict": "pass" if passed else "FAIL-control-rejected"})
        ok = ok and passed
        for kind, expected_check in EXPECTED.items():
            root = make_copy(kind)
            seal(root)
            mutate(root, kind)
            if kind not in ("manifest_empty", "manifest_omits_expected", "manifest_string_seal"):
                seal(root)
            code, out, err = run_checker(root)
            try:
                body = json.loads(out)
                reported = [f.get("check") for f in body.get("failures", [])]
                harness = [f for f in reported if f == "harness"]
            except ValueError:
                reported, harness = [], ["unparseable-output"]
            detected = code != 0 and expected_check in reported and not harness
            rows.append({"case": kind, "exit_code": code, "expected_check": expected_check,
                         "reported_checks": sorted(set(reported)),
                         "verdict": "detected_by_assertion" if detected else "FAIL-undetected-or-harness-error"})
            ok = ok and detected
    finally:
        assert CASE.parent == ROOT / ".test-tmp" and CASE.name.startswith("jh005-mutations-")
        resolved_temp = (ROOT / ".test-tmp").resolve()
        assert resolved_temp.parent == ROOT.resolve()
        assert CASE.resolve().parent == resolved_temp
        shutil.rmtree(CASE, ignore_errors=True)
    report = {"scope": "offline mutation checks for the JH-005 fixture verifier",
              "api_calls": 0, "rows": rows,
              "fixtures_cleaned": not CASE.exists(),
              "verdict": "pass" if ok else "fail"}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
