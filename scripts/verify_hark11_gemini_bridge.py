"""Local verification entry for the Gemini CLI session bridge (Hark-派单-11).

One dispatch through the existing AgentSessionAdapter contract, then collect,
re-read evidence bytes and check the read-only claims.

Modes
  host-probe  Real pinned Gemini CLI, model traffic answered by the SYNTHETIC
              endpoint in tests/fixtures/gemini_cli (no model, no key). The
              script includes a write_file and a run_shell_command attempt.
  real        Real Gemini CLI and real model. Requires --authorize-real-model-call
              and passes only the variable NAMES given with --env-name (values
              are never printed or stored). Not run in the cloud copy.

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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.agent_session import SessionAddress, SubmitRequest  # noqa: E402
from src.agent_session_gemini import (  # noqa: E402
    READ_ONLY_TOOLS, GeminiCliAgentSessionAdapter, GeminiCliConfig, verify_run_evidence)

TASK = ("Find where the class RunState is defined in this repository and which states it lists. "
        "Answer with the file path and line number as path:line. Do not modify anything.")

PROBE_SCRIPT = [
    {"parts": [{"functionCall": {"name": "grep_search", "args": {"pattern": "^class RunState"}}}]},
    {"parts": [{"functionCall": {"name": "read_file",
                                 "args": {"file_path": "{WORKSPACE}/src/agent_session.py",
                                          "start_line": 94, "end_line": 106}}}]},
    {"parts": [{"functionCall": {"name": "write_file",
                                 "args": {"file_path": "{WORKSPACE}/HARK11_SHOULD_NOT_EXIST.txt",
                                          "content": "x"}}}]},
    {"parts": [{"functionCall": {"name": "run_shell_command",
                                 "args": {"command": "touch {WORKSPACE}/HARK11_SHELL_SHOULD_NOT_EXIST"}}}]},
    {"parts": [{"text": "RunState is defined at src/agent_session.py:94. It lists unknown, queued, "
                        "accepted, running, declared_complete, failed, cancel_requested and cancelled."}]},
]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("host-probe", "real"), required=True)
    ap.add_argument("--node", default="node")
    ap.add_argument("--cli-js", required=True, help="path to @google/gemini-cli/bundle/gemini.js (0.65.0)")
    ap.add_argument("--workspace", required=True, help="absolute path of the target worktree")
    ap.add_argument("--out", required=True, help="evidence root (outside the workspace)")
    ap.add_argument("--cli-home", help="isolated CLI home (default: <out>/cli-home)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--auth-type", default=None)
    ap.add_argument("--env-name", action="append", default=[])
    ap.add_argument("--authorize-real-model-call", action="store_true")
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args(argv)

    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    cli_home = os.path.abspath(args.cli_home or os.path.join(out, "cli-home"))
    os.makedirs(cli_home, exist_ok=True)
    # Hark-派单-12 R3: the workspace is passed to the adapter exactly as given;
    # a relative input is refused here instead of being resolved.
    if not os.path.isabs(args.workspace):
        print("--workspace must be an absolute path", file=sys.stderr)
        return 2
    workspace = args.workspace
    server = None
    extra_env = []
    env_names = tuple(args.env_name)
    auth_type = args.auth_type
    if args.mode == "real":
        if not args.authorize_real_model_call:
            print("real mode needs --authorize-real-model-call", file=sys.stderr)
            return 2
    else:
        env_names = ()
        auth_type = "gemini-api-key"
        # An explicit model avoids the host's "auto" router, whose classifier
        # makes extra non-streamed model calls.
        args.model = args.model or "gemini-2.5-flash"
        port = _free_port()
        script_path = os.path.join(out, "synthetic-script.json")
        with open(script_path, "wb") as fh:
            fh.write(json.dumps(PROBE_SCRIPT, indent=1).encode("utf-8"))
        server = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "fixtures", "gemini_cli",
                                                                 "synthetic_endpoint.py"),
                                   str(port), script_path, os.path.join(out, "synthetic-requests.jsonl"),
                                   workspace])
        time.sleep(1.0)
        # Placeholder text, not a credential: the CLI insists on a value for its
        # API-key auth path, and all model traffic goes to 127.0.0.1.
        extra_env = [("GOOGLE_GEMINI_BASE_URL", f"http://127.0.0.1:{port}"),
                     ("GEMINI_API_KEY", "offline-placeholder-not-a-credential"),
                     ("HTTPS_PROXY", "http://127.0.0.1:9"), ("HTTP_PROXY", "http://127.0.0.1:9"),
                     ("NO_PROXY", "127.0.0.1,localhost")]
    config = GeminiCliConfig(argv_prefix=(args.node, os.path.abspath(args.cli_js)), cli_home=cli_home,
                             evidence_root=os.path.join(out, "bridge"), env_names=env_names,
                             extra_env=tuple(extra_env), auth_type=auth_type)
    checks = {}
    try:
        adapter = GeminiCliAgentSessionAdapter(config)
        address = SessionAddress(app_kind="gemini-cli", instance_id="gemini-cli/local",
                                 session_id="new", model_id=args.model)
        request = SubmitRequest(task_id="hark11-locate-runstate", attempt=1, session=address,
                                instructions=TASK, workspace=workspace,
                                idempotency_key="hark11-verify-" + args.mode)
        submitted = adapter.submit(request)
        replay = adapter.submit(request)
        checks["idempotent_replay_same_run"] = replay.run_id == submitted.run_id
        result = adapter.collect(address, submitted.run_id, timeout=args.timeout)
        art = result.artifacts[0] if result.artifacts else {}
        status = adapter.status(address, submitted.run_id)
        evidence = verify_run_evidence(os.path.join(config.evidence_root, "registry.jsonl"), submitted.run_id)
        used = [c["tool_name"] for c in art.get("tool_calls", [])]
        ok_calls = [c["tool_name"] for c in art.get("tool_calls", [])
                    if (c.get("result") or {}).get("status") == "success"]
        checks["declared_complete"] = status.state == "declared_complete"
        checks["not_marked_succeeded"] = result.declared_status != "succeeded" and status.state != "succeeded"
        checks["session_bound"] = bool(art.get("native_session_id"))
        checks["all_successful_tools_read_only"] = all(t in READ_ONLY_TOOLS for t in ok_calls)
        checks["read_tool_used"] = any(t in READ_ONLY_TOOLS for t in ok_calls)
        checks["workspace_unchanged"] = art.get("workspace_unchanged") is True
        checks["source_ref_verified"] = any(r.get("verified") for r in art.get("source_refs", []))
        checks["evidence_bytes_match"] = evidence.get("ok") is True
        if args.mode == "host-probe":
            checks["write_and_shell_refused"] = sorted(art.get("denied_tool_requests", [])) == [
                "run_shell_command", "write_file"]
            checks["probe_files_absent"] = not any(os.path.lexists(os.path.join(workspace, n)) for n in (
                "HARK11_SHOULD_NOT_EXIST.txt", "HARK11_SHELL_SHOULD_NOT_EXIST"))
            declared = set()
            with open(os.path.join(out, "synthetic-requests.jsonl"), "rb") as fh:
                for line in fh.read().decode("utf-8").splitlines():
                    rec = json.loads(line)
                    for tool in (rec.get("request") or {}).get("tools", []):
                        for fn in tool.get("functionDeclarations", []):
                            declared.add(fn["name"])
            checks["declared_tools_exactly_read_only"] = declared == set(READ_ONLY_TOOLS)
            checks["host_model_matches_request"] = art.get("host_reported_model") == args.model
        summary = {"mode": args.mode, "run_id": submitted.run_id, "state": status.state,
                   "declared_status": result.declared_status, "tools_used": used,
                   "usage": result.usage, "unverified": result.unverified,
                   "evidence": evidence, "checks": checks,
                   "host_reported_model": art.get("host_reported_model"),
                   "backend_models": art.get("backend_models"),
                   "source_refs": art.get("source_refs")}
    except Exception as exc:  # report, never mask
        summary = {"mode": args.mode, "error": f"{type(exc).__name__}: {exc}", "checks": checks}
        with open(os.path.join(out, "summary.json"), "wb") as fh:
            fh.write(json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8"))
        print(json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True))
        return 2
    finally:
        if server is not None:
            server.kill()
            server.wait()
    data = json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8")
    with open(os.path.join(out, "summary.json"), "wb") as fh:
        fh.write(data)
    print(data.decode("utf-8"))
    for name, value in checks.items():
        print(f"{'PASS' if value else 'FAIL'} {name}")
    return 0 if checks and all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
