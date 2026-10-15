"""Verify JH-005 N2 fixtures for structural consistency (offline, stdlib only).

Usage (from repo root):
    python scripts/verify_jh005_fixtures.py [--fixture NAME]

Checks JSON validity, required task keys, budget sanity, authorization scope,
data expected-output recomputation, code check-script compilability, manifest
seal state, and that no test_*.py files pollute the unit-test discovery count.
Prints a JSON report; exit 0 only when every selected fixture is fully green.
Unsealed fixtures report UNSEALED with computed hashes for human review and
exit nonzero; the verifying session seals them only after reading the bytes.
No models, no network, no writes outside stdout.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "n2"

FAILURES = []


def fail(fixture, check, detail):
    FAILURES.append({"fixture": fixture, "check": check, "detail": detail})


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return None, f"{path}: {exc}"


def check_common(name, fdir):
    task, err = load_json(fdir / "task.json")
    if err:
        fail(name, "task.json parses", err)
        return None
    for key in ("task_id", "title", "acceptance_criteria", "budget", "context", "metadata"):
        if key not in task:
            fail(name, "task.json keys", f"missing key: {key}")
    criteria = task.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria or not all(isinstance(c, str) and c for c in criteria):
        fail(name, "criteria", "acceptance_criteria must be a non-empty string list")
    budget = task.get("budget") or {}
    for key in ("max_turns", "max_tokens", "max_execution_time_seconds"):
        value = budget.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            fail(name, "budget", f"{key} must be a positive int, got {value!r}")
    targets = (task.get("context") or {}).get("target_files")
    if not isinstance(targets, list):
        fail(name, "target_files", "context.target_files must be a list")
    else:
        for entry in targets:
            if not isinstance(entry, str) or not entry or entry.startswith(("/", "\\")) or ".." in Path(entry).parts:
                fail(name, "target_files", f"must be workspace-relative without .. : {entry!r}")
    scenarios, err = load_json(fdir / "scenarios.json")
    if err:
        fail(name, "scenarios.json parses", err)
    elif not isinstance(scenarios, dict):
        fail(name, "scenarios shape", "top level must be an object")
    else:
        items = scenarios.get("scenarios", [])
        if not isinstance(items, list):
            fail(name, "scenarios shape", "scenarios must be a list")
            items = []
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                fail(name, "scenarios shape", f"scenario entry must be an object: {item!r}")
                continue
            for key in ("id", "kind", "setup", "expected", "evidence"):
                if key not in item:
                    fail(name, "scenarios shape", f"scenario missing {key}: {item.get('id')}")
            if item.get("id") in seen:
                fail(name, "scenarios unique", f"duplicate id: {item.get('id')}")
            seen.add(item.get("id"))
        if scenarios.get("status") != "not_run":
            fail(name, "scenarios status", "must stay not_run until executed")
    checks, err = load_json(fdir / "checks.json")
    if err:
        fail(name, "checks.json parses", err)
    elif not isinstance(checks, dict):
        fail(name, "checks shape", "top level must be an object")
    else:
        items = checks.get("checks", [])
        if not isinstance(items, list):
            fail(name, "checks shape", "checks must be a list")
            items = []
        for item in items:
            if not isinstance(item, dict):
                fail(name, "checks shape", f"check entry must be an object: {item!r}")
                continue
            for key in ("name", "command", "expect_exit"):
                if key not in item:
                    fail(name, "checks shape", f"check missing {key}: {item.get('name')}")
            command = item.get("command", [])
            if not isinstance(command, list):
                fail(name, "checks shape", f"command must be a list: {item.get('name')}")
                continue
            for token in command:
                if isinstance(token, str) and token.endswith((".py", ".json")) and not token.startswith("-"):
                    if not (ROOT / token).exists():
                        fail(name, "checks reference", f"missing file: {token}")
        if checks.get("status") != "not_run":
            fail(name, "checks status", "must stay not_run until executed")
    return task


def check_manifest(name, fdir):
    manifest, err = load_json(fdir / "manifest.json")
    if err:
        fail(name, "manifest.json parses", err)
        return
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        fail(name, "manifest shape", "top level must be an object with a files list")
        return
    computed = {}
    for entry in manifest.get("files", []):
        if not isinstance(entry, dict) or not entry.get("path"):
            fail(name, "manifest shape", f"bad file entry: {entry!r}")
            continue
        path = fdir / entry["path"]
        if entry["path"] == "manifest.json":
            continue
        if not path.is_file():
            fail(name, "manifest files", f"missing: {entry['path']}")
            continue
        computed[entry["path"]] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not manifest.get("sealed", False):
        fail(name, "seal", {"verdict": "UNSEALED", "computed_sha256": computed,
                            "note": "review bytes, fill manifest, re-run"})
        return
    for entry in manifest.get("files", []):
        if not isinstance(entry, dict) or not entry.get("path"):
            continue  # already reported above
        if entry["path"] == "manifest.json":
            continue
        if entry["path"] in computed and entry.get("sha256") != computed[entry["path"]]:
            fail(name, "seal", f"hash mismatch: {entry['path']}")


def check_no_test_files():
    for path in sorted(FIXTURES.rglob("test_*.py")):
        fail(path.parent.name, "suite-guard", f"must not match test discovery: {path.name}")


def check_data_rollup(fdir):
    name = "data_rollup"
    task = check_common(name, fdir)
    if task is not None:
        criteria = task.get("acceptance_criteria")
        if criteria != ["Trusted input records were provided",
                        "Artifact content equals the expected sorted records"]:
            fail(name, "locked-criteria", "must equal the records_transform_v1 standard strings")
    check_manifest(name, fdir)
    records_in, err = load_json(fdir / "input.json")
    if err:
        fail(name, "input.json parses", err)
        return
    expected_file, err = load_json(fdir / "expected.json")
    if err:
        fail(name, "expected.json parses", err)
        return
    if (not isinstance(records_in, list) or not records_in or not all(
            isinstance(r, dict) and isinstance(r.get("region"), str)
            and isinstance(r.get("sales"), (int, float)) for r in records_in)):
        fail(name, "input shape", "expected non-empty list of {region: str, sales: number}")
        return
    recomputed = sorted(records_in, key=lambda r: (r["region"], r["sales"]))
    if recomputed != expected_file:
        fail(name, "expected-recomputed", "expected.json differs from the fixed (region, sales) sort")


def check_code_sortfix(fdir):
    name = "code_sortfix"
    check_common(name, fdir)
    check_manifest(name, fdir)
    for rel in ("check_widget.py", "target/widget.py", "expected/widget.py"):
        path = fdir / rel
        if not path.is_file():
            fail(name, "code files", f"missing: {rel}")
            continue
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except (OSError, SyntaxError) as exc:
            fail(name, "code compiles", f"{rel}: {exc}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify JH-005 N2 fixtures.")
    parser.add_argument("--fixture", choices=["code_sortfix", "data_rollup"], default=None)
    args = parser.parse_args(argv)
    if not FIXTURES.is_dir():
        print(json.dumps({"status": "fail", "error": "fixtures dir missing"}, indent=2))
        return 1
    selected = [args.fixture] if args.fixture else ["code_sortfix", "data_rollup"]
    for name in selected:
        fdir = FIXTURES / name
        if not fdir.is_dir():
            fail(name, "fixture dir", "missing")
            continue
        if name == "code_sortfix":
            check_code_sortfix(fdir)
        else:
            check_data_rollup(fdir)
    check_no_test_files()
    if FAILURES:
        print(json.dumps({"status": "fail", "failures": FAILURES}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"status": "ok", "fixtures": selected}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
