"""Verify JH-005 N2 fixtures for structural consistency (offline).

Usage (from repo root):
    python scripts/verify_jh005_fixtures.py [--fixture NAME]

Checks task materialization against the frozen Task Schema, the fixed
records_transform_v1 assembly path, task/input/expected consistency, manifest
seal state with closed inventory, exact scenario/check sets, the reference
byte relation, and suite guards. Prints a JSON report; exit 0 only when every
selected fixture is fully green. Unsealed fixtures report UNSEALED with
computed values and exit nonzero; the verifying session seals them only after
reading the bytes. No models, no network, no writes outside stdout.

The template-resolver check imports src.cli from this checkout's root. Running
from a scratch copy without src/ fails closed with an explicit remedy instead
of silently skipping.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "n2"
SCHEMA = ROOT / "docs" / "schemas" / "task.v1.json"

WORKSPACE_PLACEHOLDER = "FILL_AT_SUBMIT"
REFERENCE_FRAGMENT = b", reverse=True"

REQUIRED_MANIFEST_FILES = {
    "code_sortfix": {"task.json", "target/widget.py", "expected/widget.py",
                     "check_widget.py", "checks.json", "scenarios.json", "manifest.json"},
    "data_rollup": {"task.json", "input.json", "expected.json",
                    "checks.json", "scenarios.json", "manifest.json"},
}
REQUIRED_SCENARIOS = {"normal-delivery", "repair-injected-defect", "budget-exhausted",
                      "provider-unavailable", "illegal-output", "user-cancel",
                      "uncertain-side-effect"}
REQUIRED_CHECKS = {
    "code_sortfix": {"fixed-behavior", "baseline-behavior-known-fail",
                     "fixture-consistency", "post-apply-target-check"},
    "data_rollup": {"expected-recomputed"},
}

REQUIRED_TARGETS = {"code_sortfix": ["target/widget.py"], "data_rollup": []}
POST_APPLY_COMMAND = [
    "python", "tests/fixtures/n2/code_sortfix/check_widget.py",
    "--target", "{HOST_WORKSPACE}/target/widget.py",
]

FAILURES = []


def fail(fixture, check, detail):
    FAILURES.append({"fixture": fixture, "check": check, "detail": detail})


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return None, f"{path}: {exc}"


def is_bad_path(entry):
    if not isinstance(entry, str) or not entry:
        return True
    if entry.startswith(("/", "\\")):
        return True
    if re.match(r"^[A-Za-z]:", entry):
        return True
    return ".." in Path(entry).parts


def materialize_task(task, fdir):
    """Deterministic fill rule: FILL_AT_SUBMIT becomes the fixture's
    materialized-workspace path. No directories are created."""
    task = json.loads(json.dumps(task))
    ctx = task.get("context")
    if isinstance(ctx, dict) and ctx.get("workspace_root") == WORKSPACE_PLACEHOLDER:
        ctx["workspace_root"] = str(fdir / "materialized-workspace")
        task["context"] = ctx
    return task


def check_task_schema(name, task):
    try:
        from jsonschema import Draft7Validator, FormatChecker
    except ImportError as exc:
        fail(name, "task-schema", f"jsonschema unavailable: {exc}")
        return
    try:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail(name, "task-schema", f"schema unreadable: {exc}")
        return
    errors = [{"path": "/".join(map(str, e.absolute_path)), "message": e.message}
              for e in Draft7Validator(schema, format_checker=FormatChecker()).iter_errors(task)]
    if errors:
        fail(name, "task-schema", errors)


def check_common(name, fdir):
    task, err = load_json(fdir / "task.json")
    if err:
        fail(name, "task.json parses", err)
        return None
    if not isinstance(task, dict):
        fail(name, "task.json shape", "top level must be an object")
        return None
    for key in ("task_id", "title", "acceptance_criteria", "budget", "context", "metadata"):
        if key not in task:
            fail(name, "task.json keys", f"missing key: {key}")
    criteria = task.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria or not all(isinstance(c, str) and c for c in criteria):
        fail(name, "criteria", "acceptance_criteria must be a non-empty string list")
    budget = task.get("budget")
    if not isinstance(budget, dict):
        fail(name, "budget", "budget must be an object")
        budget = {}
    for key in ("max_turns", "max_tokens", "max_execution_time_seconds"):
        value = budget.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            fail(name, "budget", f"{key} must be a positive int, got {value!r}")
    context = task.get("context")
    if not isinstance(context, dict):
        fail(name, "context", "context must be an object")
        context = {}
    targets = context.get("target_files")
    if not isinstance(targets, list):
        fail(name, "target_files", "context.target_files must be a list")
    else:
        for entry in targets:
            if is_bad_path(entry):
                fail(name, "target_files", f"must be workspace-relative without drive/.. : {entry!r}")
    if targets != REQUIRED_TARGETS[name]:
        fail(name, "target_files",
             f"must match this fixture's authorized targets: {REQUIRED_TARGETS[name]}")
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
        if seen != REQUIRED_SCENARIOS:
            fail(name, "scenarios-complete",
                 f"must be exactly {sorted(REQUIRED_SCENARIOS)}, "
                 f"got {sorted(x for x in seen if isinstance(x, str))}")
        if scenarios.get("status") != "not_run":
            fail(name, "scenarios status", "must stay not_run until executed")
    checks, err = load_json(fdir / "checks.json")
    if err:
        fail(name, "checks.json parses", err)
    else:
        check_checks(name, checks)
    return task


PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def check_checks(name, checks):
    if not isinstance(checks, dict):
        fail(name, "checks shape", "top level must be an object")
        return
    items = checks.get("checks", [])
    if not isinstance(items, list):
        fail(name, "checks shape", "checks must be a list")
        items = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            fail(name, "checks shape", f"check entry must be an object: {item!r}")
            continue
        for key in ("name", "command", "expect_exit"):
            if key not in item:
                fail(name, "checks shape", f"check missing {key}: {item.get('name')}")
        seen.add(item.get("name"))
        command = item.get("command", [])
        if not isinstance(command, list):
            fail(name, "checks shape", f"command must be a list: {item.get('name')}")
            continue
        is_template = item.get("template", False) is True
        placeholders = {m.group(1) for token in command if isinstance(token, str)
                        for m in PLACEHOLDER_RE.finditer(token)}
        material = item.get("materialization", {})
        if item.get("name") == "post-apply-target-check":
            if (
                not is_template
                or command != POST_APPLY_COMMAND
                or type(item.get("expect_exit")) is not int
                or item["expect_exit"] != 0
                or not isinstance(material, dict)
                or set(material) != {"HOST_WORKSPACE"}
                or not isinstance(material.get("HOST_WORKSPACE"), str)
                or not material["HOST_WORKSPACE"].strip()
            ):
                fail(name, "post-apply-binding",
                     "must run the pinned checker on {HOST_WORKSPACE}/target/widget.py "
                     "with exactly HOST_WORKSPACE and integer exit 0")
        if is_template:
            if not isinstance(material, dict) or not material:
                fail(name, "checks-template", f"{item.get('name')}: template needs a materialization map")
                material = {}
            if set(material) != placeholders or not placeholders:
                fail(name, "checks-template",
                     f"{item.get('name')}: declared params must equal used placeholders")
            if item.get("expect_exit") != 0:
                fail(name, "checks-template", f"{item.get('name')}: template must expect exit 0")
            if any(isinstance(token, str) and ("expected/" in token or "expected\\" in token)
                   for token in command):
                fail(name, "checks-template",
                     f"{item.get('name')}: post-apply checks must target the applied file, never expected/")
        elif placeholders:
            fail(name, "checks-template", f"{item.get('name')}: placeholders require template:true")
        for token in command:
            if not isinstance(token, str) or token.startswith("-") or "{" in token:
                continue
            if token.endswith((".py", ".json")):
                if Path(token).is_absolute() or not (ROOT / token).exists():
                    fail(name, "checks reference", f"missing or non-repo file: {token}")
    if seen != REQUIRED_CHECKS[name]:
        fail(name, "checks-complete",
             f"must be exactly {sorted(REQUIRED_CHECKS[name])}, "
             f"got {sorted(x for x in seen if isinstance(x, str))}")
    if checks.get("status") != "not_run":
        fail(name, "checks status", "must stay not_run until executed")


def check_manifest(name, fdir):
    manifest, err = load_json(fdir / "manifest.json")
    if err:
        fail(name, "manifest.json parses", err)
        return
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        fail(name, "manifest shape", "top level must be an object with a files list")
        return
    entries = manifest.get("files")
    listed = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry.get("path"):
            fail(name, "manifest shape", f"bad file entry: {entry!r}")
            continue
        if entry["path"] in listed:
            fail(name, "manifest-inventory", f"duplicate path: {entry['path']}")
        if is_bad_path(entry["path"]):
            fail(name, "manifest-inventory", f"path must be relative without drive/.. : {entry['path']!r}")
        listed.add(entry["path"])
    if listed != REQUIRED_MANIFEST_FILES[name]:
        fail(name, "manifest-inventory",
             f"must be exactly {sorted(REQUIRED_MANIFEST_FILES[name])}, got {sorted(listed)}")
    on_disk = set()
    for path in fdir.rglob("*"):
        if path.is_dir() or "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
            continue
        on_disk.add(path.relative_to(fdir).as_posix())
    if on_disk != REQUIRED_MANIFEST_FILES[name]:
        fail(name, "manifest-inventory",
             f"fixture dir must contain exactly {sorted(REQUIRED_MANIFEST_FILES[name])}, "
             f"got {sorted(on_disk)}")
    computed = {}
    for rel in sorted(REQUIRED_MANIFEST_FILES[name]):
        if rel == "manifest.json":
            continue
        target = fdir / rel
        if target.is_file():
            computed[rel] = hashlib.sha256(target.read_bytes()).hexdigest()
    sealed = manifest.get("sealed")
    if sealed is True:
        for rel, digest in computed.items():
            recorded = next((e.get("sha256") for e in entries
                             if isinstance(e, dict) and e.get("path") == rel), None)
            if recorded != digest:
                fail(name, "seal", f"hash mismatch: {rel}")
    elif sealed is False:
        fail(name, "seal", {"verdict": "UNSEALED", "computed_sha256": computed,
                            "note": "review bytes, fill manifest, re-run"})
    else:
        fail(name, "seal-boolean", f"sealed must be boolean true/false, got {sealed!r}")


def check_no_test_files():
    for path in sorted(FIXTURES.rglob("test_*.py")):
        fail(path.parent.name, "suite-guard", f"must not match test discovery: {path.name}")


def check_template_resolver(name, task):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from src.cli import resolve_trusted_evaluator
    except Exception as exc:
        fail(name, "template-resolver",
             f"src.cli unavailable ({type(exc).__name__}: {exc}); run from the worktree or include src/")
        return
    try:
        resolve_trusted_evaluator(task)
    except Exception as exc:
        fail(name, "template-resolver", f"{type(exc).__name__}: {exc}")


def check_data_rollup(fdir):
    name = "data_rollup"
    task = check_common(name, fdir)
    check_manifest(name, fdir)
    if task is None:
        return
    materialized = materialize_task(task, fdir)
    check_task_schema(name, materialized)
    check_template_resolver(name, materialized)
    records_in, err = load_json(fdir / "input.json")
    if err:
        fail(name, "input.json parses", err)
        return
    expected_file, err = load_json(fdir / "expected.json")
    if err:
        fail(name, "expected.json parses", err)
        return
    meta = materialized.get("metadata")
    sculpted = meta.get("input_data") if isinstance(meta, dict) else None
    task_records = sculpted.get("records") if isinstance(sculpted, dict) else None
    if task_records != records_in:
        fail(name, "task-input-match", "task records must equal input.json exactly")
    if (not isinstance(records_in, list) or not records_in or not all(
            isinstance(r, dict) and isinstance(r.get("region"), str)
            and isinstance(r.get("sales"), (int, float)) and not isinstance(r.get("sales"), bool)
            for r in records_in)):
        fail(name, "input-shape", "expected non-empty list of {region: str, sales: non-bool number}")
        return
    recomputed = sorted(records_in, key=lambda r: (r["region"], r["sales"]))
    if recomputed != expected_file:
        fail(name, "expected-recomputed", "expected.json differs from the fixed (region, sales) sort")


def check_code_sortfix(fdir):
    name = "code_sortfix"
    task = check_common(name, fdir)
    check_manifest(name, fdir)
    if task is not None:
        check_task_schema(name, materialize_task(task, fdir))
    for rel in ("check_widget.py", "target/widget.py", "expected/widget.py"):
        path = fdir / rel
        if not path.is_file():
            fail(name, "code files", f"missing: {rel}")
            continue
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except (OSError, SyntaxError) as exc:
            fail(name, "code compiles", f"{rel}: {exc}")
    base_file, ref_file = fdir / "target/widget.py", fdir / "expected/widget.py"
    if base_file.is_file() and ref_file.is_file():
        base = base_file.read_bytes()
        ref = ref_file.read_bytes()
        if base.count(REFERENCE_FRAGMENT) != 1:
            fail(name, "reference-byte-contract", "baseline must contain the fragment exactly once")
        elif base.replace(REFERENCE_FRAGMENT, b"") != ref:
            fail(name, "reference-byte-contract",
                 "reference must equal baseline minus the fragment, byte-for-byte")


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
        try:
            if name == "code_sortfix":
                check_code_sortfix(fdir)
            else:
                check_data_rollup(fdir)
        except Exception as exc:
            fail(name, "harness", f"{type(exc).__name__}: {str(exc)[:300]}")
    check_no_test_files()
    if FAILURES:
        print(json.dumps({"status": "fail", "failures": FAILURES}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"status": "ok", "fixtures": selected}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
