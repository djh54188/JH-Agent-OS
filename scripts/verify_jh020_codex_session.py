"""JH-020 real Codex AgentSessionAdapter round trip (one isolated session).

Runs ONE real dispatch through src/agent_session_codex.py against a freshly
spawned `codex app-server --stdio`:

  * a NEW isolated workspace under data/ is the thread cwd;
  * the turn is pinned read-only (sandbox readOnly, network off, approvals
    never) and asks the session to reply with a fixed, unpredictable token;
  * the wait is bounded and never retried;
  * the script proves start + completion + association: the collected text must
    contain the token AND the completion must match THIS thread and THIS turn.

Delivery success is NOT task acceptance: the script reports the adapter's
observation only and exits non-zero unless the association is proven. It writes
raw evidence (request fingerprint, target session, native thread/turn, the
collected result, and the workspace listing) next to itself.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.agent_session import RunState, SessionAddress, SubmitRequest  # noqa: E402
from src.agent_session_codex import CodexAgentSessionAdapter, READ_ONLY_SANDBOX  # noqa: E402

EVIDENCE_DIR = ROOT / "data" / "jh020-codex-session-20261018"
APP_KIND = "codex"
INSTANCE_ID = "codex-app-server/local"
SESSION_ID = "new:jh020-isolated-exec-session"  # this slice only creates a NEW isolated session
MODEL_ID = "gpt-6.1-sol"  # explicit model requirement, verified against the server report
ROLE = "worker"


def workspace_listing(root: Path):
    out = []
    for current, _dirs, files in os.walk(root):
        for name in sorted(files):
            path = Path(current) / name
            out.append({"path": str(path.relative_to(root)), "bytes": path.stat().st_size})
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="JH-020 real Codex session round trip")
    parser.add_argument("--timeout", type=float, default=240.0, help="bounded wait for turn/completed")
    parser.add_argument("--out", default=str(EVIDENCE_DIR))
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ws = out_dir / "ws"
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)

    nonce = "JH020-RT-" + secrets.token_hex(4).upper()
    instructions = f"Reply with exactly this token and nothing else: {nonce}. Do not use any tools."
    adapter = CodexAgentSessionAdapter(registry_path=str(out_dir / "runs.jsonl"))
    session = SessionAddress(app_kind=APP_KIND, instance_id=INSTANCE_ID,
                             session_id=SESSION_ID, model_id=MODEL_ID, role=ROLE)
    request = SubmitRequest(task_id="task_jh020_codex_roundtrip", attempt=1, session=session,
                            instructions=instructions, workspace=str(ws),
                            allowed_scope=(), idempotency_key="jh020-roundtrip-" + secrets.token_hex(4),
                            artifacts=(), budget_tokens=None)

    report: dict = {
        "scope": "one real isolated session; no file writes; delivery != task success",
        "nonce": nonce,
        "workspace": str(ws),
        "read_only_sandbox": READ_ONLY_SANDBOX,
        "request_fingerprint": request.fingerprint(),
        "target_session": {"app_kind": APP_KIND, "instance_id": INSTANCE_ID,
                           "session_id": SESSION_ID, "role": ROLE, "required_model": MODEL_ID},
    }
    failures = []

    def check(name, ok, detail=""):
        report.setdefault("checks", []).append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            failures.append(f"{name}: {detail}" if detail else name)
        return bool(ok)

    before = workspace_listing(ws)
    try:
        status = adapter.submit(request)
        report["submit"] = {"run_id": status.run_id, "state": status.state,
                            "native_turn": status.native_turn and {
                                "native_turn_id": status.native_turn.native_turn_id,
                                "native_session_id": status.native_turn.native_session_id},
                            "binding": status.binding and {
                                "app_kind": status.binding.app_kind,
                                "instance_id": status.binding.instance_id,
                                "session_id": status.binding.session_id,
                                "task_id": status.binding.task_id, "attempt": status.binding.attempt}}
        check("submit_started_a_turn", bool(status.native_turn), "no native turn returned")
        check("bindings_match_request",
              status.binding is not None and status.binding.session_id == SESSION_ID,
              str(report["submit"]["binding"]))
        check("fingerprint_registered",
              adapter.inspect(status.run_id).request_fingerprint == request.fingerprint())
        check("explicit_model_honored",
              adapter.inspect(status.run_id).native_model == MODEL_ID,
              f"server reported {adapter.inspect(status.run_id).native_model!r}")

        result = adapter.collect(session, status.run_id, timeout=args.timeout)
        report["collect"] = {"declared_status": result.declared_status, "usage": result.usage,
                             "raw_refs": result.raw_refs, "unverified": result.unverified,
                             "artifacts": result.artifacts}
        final_state = adapter.status(session, status.run_id).state
        report["final_run_state"] = final_state
        report["completion"] = adapter.inspect(status.run_id).completion
        report["native_model"] = adapter.inspect(status.run_id).native_model
        report["raw_evidence"] = adapter.inspect(status.run_id).raw_evidence
        report["unknown_server_requests"] = adapter.inspect(status.run_id).unknown_server_requests

        check("completion_observed", final_state == RunState.DECLARED_COMPLETE,
              f"final state {final_state}; unverified={result.unverified}")
        check("declared_status_completed", result.declared_status == "completed", result.declared_status)
        texts = [a.get("assistant_text") for a in result.artifacts]
        check("result_carries_the_fixed_token", nonce in texts,
              f"token {nonce!r} not in collected texts {texts}")
        check("completion_matched_this_thread_and_turn",
              report["completion"] and report["completion"].get("turn_id") == status.native_turn.native_turn_id,
              str(report["completion"]))
        check("no_unimplemented_server_requests", not report["unknown_server_requests"],
              str(report["unknown_server_requests"]))
    except Exception as exc:  # noqa: BLE001 - report the failure, do not retry
        report["exception"] = f"{type(exc).__name__}: {exc}"
        check("no_exception", False, report["exception"])
    finally:
        adapter.close()

    after = workspace_listing(ws)
    report["workspace_before"] = before
    report["workspace_after"] = after
    check("isolated_workspace_not_written", after == before, f"before={before} after={after}")

    report["verdict"] = "pass" if not failures else "fail"
    report["failures"] = failures
    report["note"] = ("adapter observation only; this proves start/completion/association, "
                      "NOT task success and NOT the designated review session")
    path = out_dir / "roundtrip.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "run_id": report.get("submit", {}).get("run_id"),
                      "final_state": report.get("final_run_state"), "failures": failures,
                      "evidence": str(path)}, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
