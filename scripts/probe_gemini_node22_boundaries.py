"""JH-09 read-only boundary probe for the pinned Gemini CLI bridge.

The workspace, outside directory and junction are created under --out, never in
the target worktree. The model is the repository's synthetic loopback endpoint;
this script never uses a real model, credential, login or user proxy.

The checks are deliberately about the host's own answer:
  * three read_file inputs outside the workspace must be refused;
  * write_file and run_shell_command must not be registered;
  * the declared tool list must still be the four read-only tools;
  * the workspace manifest and the outside file must be unchanged.

Exit codes: 0 all checks passed, 1 ran but a check failed, 2 could not run.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent_session import SessionAddress, SubmitRequest  # noqa: E402
from src.agent_session_gemini import (  # noqa: E402
    READ_ONLY_TOOLS,
    GeminiCliAgentSessionAdapter,
    GeminiCliConfig,
    verify_run_evidence,
)

SECRET = "OUTSIDE_SECRET_123"
TASK = "Exercise the read-only boundary cases requested by the verification plan."
SCRIPT = [
    {"parts": [{"functionCall": {"name": "read_file", "args": {"file_path": "{OUTSIDE}/secret.txt"}}}]},
    {"parts": [{"functionCall": {"name": "read_file", "args": {"file_path": "{JUNCTION}/secret.txt"}}}]},
    {"parts": [{"functionCall": {"name": "read_file", "args": {"file_path": "../outside/secret.txt"}}}]},
    {"parts": [{"functionCall": {"name": "write_file", "args": {
        "file_path": "{WORKSPACE}/forbidden.txt", "content": "bad"}}}]},
    {"parts": [{"functionCall": {"name": "run_shell_command", "args": {
        "command": "echo bad"}}}]},
    {"parts": [{"text": "Completed all boundary tool attempts."}]},
]


def _write_bytes(path: Path, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_port(port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"synthetic endpoint did not listen on 127.0.0.1:{port}")


def _make_junction(link: Path, target: Path) -> dict:
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True)
    record = {
        "command": ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        "returncode": done.returncode,
        "stdout": done.stdout.decode("utf-8", "replace"),
        "stderr": done.stderr.decode("utf-8", "replace"),
        "isjunction": os.path.isjunction(link) if hasattr(os.path, "isjunction") else None,
        "realpath": os.path.realpath(link),
        "target_realpath": os.path.realpath(target),
        "secret_visible": os.path.isfile(link / "secret.txt"),
    }
    if done.returncode != 0 or not record["secret_visible"]:
        raise RuntimeError(f"junction was not established: {record}")
    if hasattr(os.path, "isjunction") and not record["isjunction"]:
        raise RuntimeError(f"link is not a Windows junction: {record}")
    return record


def _tool_calls(artifact: dict) -> list:
    return artifact.get("tool_calls") or []


def _read_outcome(artifact: dict, file_path: str) -> dict:
    wanted = os.path.normcase(os.path.normpath(file_path))
    for call in _tool_calls(artifact):
        parameters = call.get("parameters") or {}
        actual = parameters.get("file_path")
        if call.get("tool_name") == "read_file" and isinstance(actual, str) and \
                os.path.normcase(os.path.normpath(actual)) == wanted:
            result = call.get("result") or {}
            error = result.get("error") or {}
            return {"status": result.get("status"), "type": error.get("type"),
                    "message": error.get("message") or result.get("output")}
    return {"status": None, "type": None, "message": "call not found"}


def _tool_outcome(artifact: dict, tool_name: str) -> dict:
    for call in _tool_calls(artifact):
        if call.get("tool_name") == tool_name:
            result = call.get("result") or {}
            error = result.get("error") or {}
            return {"status": result.get("status"), "type": error.get("type"),
                    "message": error.get("message") or result.get("output")}
    return {"status": None, "type": None, "message": "call not found"}


def _declared_tools(requests_path: Path) -> list:
    declared = set()
    with open(requests_path, "rb") as fh:
        for line in fh.read().decode("utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            for tool in (record.get("request") or {}).get("tools", []):
                for fn in tool.get("functionDeclarations", []):
                    declared.add(fn["name"])
    return sorted(declared)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", required=True)
    ap.add_argument("--cli-js", required=True)
    ap.add_argument("--out", required=True, help="fresh evidence root outside the target worktree")
    ap.add_argument("--cli-home", help="isolated CLI home (default: <out>/cli-home)")
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args(argv)

    out = Path(args.out).resolve()
    fixtures = out / "fixtures"
    workspace = fixtures / "workspace"
    outside = fixtures / "outside"
    if (out / "bridge").exists() or fixtures.exists():
        print(f"refusing to overwrite existing evidence: {out}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True)
    outside.mkdir(parents=True)
    (workspace / "inside.txt").write_bytes(b"INSIDE_FILE_CONTENT")
    (outside / "secret.txt").write_bytes(SECRET.encode("utf-8"))
    junction = workspace / "link_to_outside"
    junction_record = _make_junction(junction, outside)
    _write_bytes(out / "junction.json", json.dumps(junction_record, indent=1, sort_keys=True).encode("utf-8") + b"\n")

    cli_home = Path(args.cli_home).resolve() if args.cli_home else out / "cli-home"
    cli_home.mkdir(parents=True, exist_ok=True)
    # The CLI writes an atomic project index on every launch. Pre-creating the
    # valid empty shape avoids the first-creation EPERM prelude seen in Node22
    # run 1; it changes no tool policy and is not a CLI byte edit.
    projects = cli_home / ".gemini" / "projects.json"
    if not projects.exists():
        projects.parent.mkdir(parents=True, exist_ok=True)
        _write_bytes(projects, b'{\n  "projects": {}\n}\n')

    outside_file = outside / "secret.txt"
    junction_file = junction / "secret.txt"
    script_path = out / "synthetic-script.json"
    script = json.dumps(SCRIPT).replace("{WORKSPACE}", str(workspace).replace("\\", "\\\\"))
    script = script.replace("{OUTSIDE}", str(outside).replace("\\", "\\\\"))
    script = script.replace("{JUNCTION}", str(junction).replace("\\", "\\\\"))
    _write_bytes(script_path, script.encode("utf-8"))
    requests_log = out / "synthetic-requests.jsonl"

    port = _free_port()
    server = subprocess.Popen([
        sys.executable, str(ROOT / "tests" / "fixtures" / "gemini_cli" / "synthetic_endpoint.py"),
        str(port), str(script_path), str(requests_log), str(workspace),
    ])
    checks = {}
    summary = {"mode": "boundary-probe", "tool_checks": {}}
    try:
        _wait_port(port)
        config = GeminiCliConfig(
            argv_prefix=(str(Path(args.node).resolve()), str(Path(args.cli_js).resolve())),
            cli_home=str(cli_home),
            evidence_root=str(out / "bridge"),
            auth_type="gemini-api-key",
            extra_env=(
                ("GOOGLE_GEMINI_BASE_URL", f"http://127.0.0.1:{port}"),
                ("GEMINI_API_KEY", "offline-placeholder-not-a-credential"),
                ("HTTPS_PROXY", "http://127.0.0.1:9"),
                ("HTTP_PROXY", "http://127.0.0.1:9"),
                ("NO_PROXY", "127.0.0.1,localhost"),
            ),
        )
        adapter = GeminiCliAgentSessionAdapter(config)
        address = SessionAddress(app_kind="gemini-cli", instance_id="gemini-cli/local",
                                 session_id="new", model_id="gemini-2.5-flash")
        request = SubmitRequest(
            task_id="jh09-readonly-boundaries", attempt=1, session=address,
            instructions=TASK, workspace=str(workspace),
            idempotency_key="jh09-boundary-" + str(os.getpid()),
        )
        submitted = adapter.submit(request)
        result = adapter.collect(address, submitted.run_id, timeout=args.timeout)
        status = adapter.status(address, submitted.run_id)
        evidence = verify_run_evidence(os.path.join(config.evidence_root, "registry.jsonl"), submitted.run_id)
        artifact = result.artifacts[0] if result.artifacts else {}
        outcomes = {
            "absolute": _read_outcome(artifact, str(outside_file).replace("\\", "/")),
            "junction": _read_outcome(artifact, str(junction_file).replace("\\", "/")),
            "parent_relative": _read_outcome(artifact, "../outside/secret.txt"),
            "write": _tool_outcome(artifact, "write_file"),
            "shell": _tool_outcome(artifact, "run_shell_command"),
        }
        outputs = json.dumps(artifact.get("tool_calls") or [], ensure_ascii=False)
        declared = _declared_tools(requests_log)
        checks["cli_exit_zero"] = artifact.get("exit_code") == 0
        checks["declared_complete"] = status.state == "declared_complete"
        checks["not_marked_succeeded"] = status.state != "succeeded"
        checks["host_declared_success"] = result.declared_status == "success"
        checks["junction_established"] = bool(junction_record["isjunction"])
        checks["all_three_reads_refused"] = all(
            outcomes[name]["status"] == "error" and outcomes[name]["type"] == "invalid_tool_params"
            for name in ("absolute", "junction", "parent_relative"))
        checks["no_outside_content_exposed"] = SECRET not in outputs
        checks["write_refused"] = outcomes["write"]["status"] == "error" and \
            outcomes["write"]["type"] == "tool_not_registered"
        checks["shell_refused"] = outcomes["shell"]["status"] == "error" and \
            outcomes["shell"]["type"] == "tool_not_registered"
        checks["advertised_tools_exactly_read_only"] = declared == sorted(READ_ONLY_TOOLS)
        checks["workspace_unchanged"] = artifact.get("workspace_unchanged") is True
        checks["forbidden_file_absent"] = not os.path.lexists(workspace / "forbidden.txt")
        checks["junction_still_established"] = bool(os.path.isjunction(junction))
        checks["outside_file_unchanged"] = outside_file.read_bytes() == SECRET.encode("utf-8")
        checks["evidence_bytes_match"] = evidence.get("ok") is True
        summary.update({
            "run_id": submitted.run_id,
            "state": status.state,
            "declared_status": result.declared_status,
            "exit_code": artifact.get("exit_code"),
            "native_state": status.native_state,
            "tool_checks": outcomes,
            "declared_tools": declared,
            "workspace": str(workspace),
            "outside": str(outside),
            "junction": str(junction),
            "evidence": evidence,
            "checks": checks,
        })
    except Exception as exc:  # report, never mask
        summary["error"] = f"{type(exc).__name__}: {exc}"
        summary["checks"] = checks
        _write_bytes(out / "summary.json", json.dumps(summary, ensure_ascii=False, indent=1,
                                                       sort_keys=True).encode("utf-8"))
        print(json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True))
        return 2
    finally:
        server.kill()
        server.wait()

    _write_bytes(out / "summary.json", json.dumps(summary, ensure_ascii=False, indent=1,
                                                   sort_keys=True).encode("utf-8"))
    print(json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True))
    for name, value in checks.items():
        print(f"{'PASS' if value else 'FAIL'} {name}")
    return 0 if checks and all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
