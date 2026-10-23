"""JH-09 zero-dispatch workspace input classification probe.

Checks the adapter's input guard directly. No CLI, endpoint, model, registry or
dispatch is started. The point is to separate workspace-entry syntax from the
tool-argument `../` denial already exercised by the native boundary probe.

Cases:
  * relative, Windows root-relative and drive-relative inputs, each with an
    existing and a missing target;
  * an existing Chinese-named directory (accepted by the syntax/preflight
    checks) and a missing Chinese-named directory (not a directory).

Exit codes: 0 all checks passed, 1 ran but a check failed, 2 could not run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent_session import SessionAddress, SubmitRequest  # noqa: E402
from src.agent_session_gemini import (  # noqa: E402
    GeminiCliAgentSessionAdapter,
    GeminiCliConfig,
    HostRestrictionUnavailable,
    workspace_path_problem,
    workspace_preflight,
)

RELATIVE = "workspace must be an absolute path (relative input refused)"
ROOT_RELATIVE = "workspace must be an absolute path (Windows root-relative input refused)"
DRIVE_RELATIVE = "workspace must be an absolute path (Windows drive-relative input refused)"


def _write_bytes(path: Path, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _make_dirs(*paths: Path) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def _request(workspace: str) -> SubmitRequest:
    return SubmitRequest(
        task_id="jh09-workspace-inputs", attempt=1,
        session=SessionAddress(app_kind="gemini-cli", instance_id="gemini-cli/local",
                               session_id="new", model_id="gemini-2.5-flash"),
        instructions="Classify this workspace input without dispatching.",
        workspace=workspace, idempotency_key="jh09-inputs-" + str(os.getpid()),
    )


def _adapter(out: Path) -> GeminiCliAgentSessionAdapter:
    # argv_prefix is never executed: only _validate() is called.
    config = GeminiCliConfig(
        argv_prefix=("__jh09_never_execute__",),
        cli_home=str(out / "guard" / "cli-home"),
        evidence_root=str(out / "guard" / "evidence"),
    )
    return GeminiCliAgentSessionAdapter(config)


def _validate(adapter: GeminiCliAgentSessionAdapter, workspace: str) -> dict:
    try:
        adapter._validate(_request(workspace))
        return {"raised": None, "message": None}
    except HostRestrictionUnavailable as exc:
        return {"raised": type(exc).__name__, "message": str(exc)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="fresh evidence root")
    args = ap.parse_args(argv)
    out = Path(args.out).resolve()
    if (out / "summary.json").exists() or (out / "guard").exists():
        print(f"refusing to overwrite existing evidence: {out}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)

    inputs = out / "inputs"
    existing = inputs / "existing"
    missing_parent = inputs / "missing"
    drive = inputs / "drive"
    root_target = inputs / "root" / "target"
    chinese = inputs / "中文目录"
    chinese_ws = chinese / "工作区"
    _make_dirs(existing / "target", existing / "subdir", drive / "target", root_target,
               chinese_ws, missing_parent)
    _write_bytes(existing / "target" / "inside.txt", b"EXISTING_RELATIVE_TARGET\n")
    _write_bytes(drive / "target" / "inside.txt", b"EXISTING_DRIVE_TARGET\n")
    _write_bytes(root_target / "inside.txt", b"EXISTING_ROOT_TARGET\n")
    _write_bytes(chinese_ws / "说明.txt", "中文目录内容\n".encode("utf-8"))

    root_relative_existing = "\\" + str(root_target).replace("\\", "/")[3:]
    root_relative_missing = "\\" + str(inputs / "root" / "absent").replace("\\", "/")[3:]
    cases = []
    old_cwd = os.getcwd()
    try:
        os.chdir(existing / "subdir")
        cases += [
            {"category": "relative-existing", "input": "../target", "expected": RELATIVE,
             "resolved": str((existing / "target").resolve()), "resolved_exists": True},
            {"category": "relative-missing", "input": "../absent",
             "resolved": str((existing / "absent").resolve()), "expected": RELATIVE,
             "resolved_exists": False},
        ]
        os.chdir("D:\\")
        cases += [
            {"category": "root-relative-existing", "input": root_relative_existing,
             "expected": ROOT_RELATIVE, "resolved": str(root_target), "resolved_exists": True},
            {"category": "root-relative-missing", "input": root_relative_missing,
             "expected": ROOT_RELATIVE, "resolved": str(inputs / "root" / "absent"),
             "resolved_exists": False},
        ]
        os.chdir(drive)
        cases += [
            {"category": "drive-relative-existing", "input": "D:target", "expected": DRIVE_RELATIVE,
             "resolved": str((drive / "target").resolve()), "resolved_exists": True},
            {"category": "drive-relative-missing", "input": "D:absent", "expected": DRIVE_RELATIVE,
             "resolved": str((drive / "absent").resolve()), "resolved_exists": False},
        ]
    finally:
        os.chdir(old_cwd)

    adapter = _adapter(out)
    results = []
    for case in cases:
        problem = workspace_path_problem(case["input"])
        preflight = workspace_preflight(case["input"], str(out / "guard" / "evidence"),
                                        str(out / "guard" / "cli-home"))
        validated = _validate(adapter, case["input"])
        passed = (problem == case["expected"] and preflight == [case["expected"]]
                  and validated["raised"] == "HostRestrictionUnavailable"
                  and validated["message"] == case["expected"])
        results.append({**case, "path_problem": problem, "preflight": preflight,
                        "adapter_validate": validated, "pass": passed})

    chinese_existing = {
        "category": "chinese-existing", "input": str(chinese_ws), "expected": "accepted",
        "path_problem": workspace_path_problem(str(chinese_ws)),
        "preflight": workspace_preflight(str(chinese_ws), str(out / "guard" / "evidence"),
                                         str(out / "guard" / "cli-home")),
        "adapter_validate": _validate(adapter, str(chinese_ws)),
    }
    chinese_existing["pass"] = (chinese_existing["path_problem"] is None
                                and chinese_existing["preflight"] == []
                                and chinese_existing["adapter_validate"]["raised"] is None)

    chinese_missing = {
        "category": "chinese-missing", "input": str(chinese / "不存在"),
        "expected": "not a directory",
        "path_problem": workspace_path_problem(str(chinese / "不存在")),
        "preflight": workspace_preflight(str(chinese / "不存在"), str(out / "guard" / "evidence"),
                                         str(out / "guard" / "cli-home")),
        "adapter_validate": _validate(adapter, str(chinese / "不存在")),
    }
    chinese_missing["pass"] = (chinese_missing["path_problem"] is None
                               and len(chinese_missing["preflight"]) == 1
                               and "is not a directory" in chinese_missing["preflight"][0]
                               and chinese_missing["adapter_validate"]["raised"] is None)

    guard_created = (out / "guard").exists()
    results += [chinese_existing, chinese_missing]
    checks = {item["category"]: bool(item["pass"]) for item in results}
    checks["no_dispatch_state_created"] = not guard_created
    summary = {
        "mode": "workspace-input-probe",
        "dispatch": 0,
        "cases": results,
        "checks": checks,
        "guard_path_created": guard_created,
        "platform": sys.platform,
        "python": sys.version,
    }
    _write_bytes(out / "summary.json",
                 json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8"))
    print(json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True))
    for name, value in checks.items():
        print(f"{'PASS' if value else 'FAIL'} {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
