"""
JH-Agent OS Phase 2-4 / N1 Structured CLI Entrypoint v1.2.0 (ADR-0003)
Supports subcommands:
  - Read-only (never creates directories/DB, never migrates, never invokes models):
      status, events, artifact, summary
  - Bounded state-changing operations (under trusted local assembly):
      submit, resume, approve, cancel, recover
"""

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable, Dict, List, Optional

from src.acceptance import file_write_acceptance_evaluator, transform_acceptance_evaluator
from src.adapters import AdapterError, CredentialMissingError, SchemaValidationError
from src.file_tools import FileToolExecutor
from src.mock_components import MockToolExecutor, ScriptedReviewerAdapter, ScriptedWorkerAdapter
from src.model_config import has_credential, load_model_adapter
from src.orchestrator import Orchestrator
from src.state_machine import TERMINAL_STATES, InvalidStateTransitionError
from src.storage import TaskStorage

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "jh_agent_os.sqlite3"
READ_ONLY_COMMANDS = frozenset({"status", "events", "artifact", "summary"})


class TrustedAssemblyError(Exception):
    """Raised when a task payload violates trusted local CLI assembly rules."""


def open_readonly_storage(db_path: str | Path) -> TaskStorage:
    """Open an existing SQLite database in strict read-only mode (no dir/file creation, no DDL)."""
    return TaskStorage(db_path, read_only=True)


def build_task_summary(storage: TaskStorage, task_id: str) -> Dict[str, Any]:
    """Build a single-page structured summary from persisted records only (zero model/write side effects)."""
    task = storage.get_task(task_id)
    control = storage.get_control(task_id)
    runtime = task.get("_runtime") or {}
    latest_artifact = storage.get_latest_artifact(task_id)
    bindings = storage.list_review_bindings(task_id)
    latest_binding = bindings[-1] if bindings else None
    pending_approval = json.loads(control["pending_json"]) if control.get("pending_json") else None

    blockers: List[str] = []
    if task["status"] == "awaiting_approval":
        blockers.append("awaiting_human_approval")
    if task["status"] == "needs_attention":
        blockers.append("needs_human_attention")
    if runtime.get("tokens_unknown_count", 0) > 0:
        blockers.append("unknown_token_usage")
    if runtime.get("has_unconfirmed_side_effect"):
        blockers.append("unconfirmed_side_effect")
    if task.get("error_info"):
        blockers.append(f"error: {task['error_info']}")

    return {
        "command_ok": True,
        "read_only": True,
        "task_result": {
            "task_id": task_id,
            "title": task["title"],
            "status": task["status"],
            "is_terminal": task["status"] in TERMINAL_STATES,
            "review_verdict": task.get("review_verdict"),
            "error_info": task.get("error_info"),
            "assigned_worker": task.get("assigned_worker"),
            "assigned_reviewer": task.get("assigned_reviewer"),
            "budget": task.get("budget"),
            "runtime_usage": {
                "turns_used": runtime.get("turns_used", 0),
                "tokens_used": runtime.get("tokens_used"),
                "tokens_unknown_count": runtime.get("tokens_unknown_count", 0),
                "repair_count": runtime.get("repair_count", 0),
                "has_unconfirmed_side_effect": runtime.get("has_unconfirmed_side_effect", False),
            },
            "current_artifact": latest_artifact,
            "artifact_refs": task.get("artifact_refs", []),
            "latest_review": latest_binding["review"] if latest_binding else None,
            "acceptance_results": latest_binding["acceptance"] if latest_binding else [],
            "pending_approval": pending_approval,
            "blockers": blockers,
            "updated_at": task.get("updated_at"),
        },
    }


def resolve_trusted_evaluator(
    task_payload: Dict[str, Any],
    *,
    allow_file_write_gate: bool = False,
) -> Callable:
    """Select a control-owned acceptance evaluator from the trusted local registry.

    Task payloads and models can NEVER inject custom evaluator code or bypass
    acceptance. Real file-write tasks stay gated off in the CLI until JH-015
    passes independent review.
    """
    meta = task_payload.get("metadata") or {}
    exec_mode = meta.get("execution_mode", "generate")
    if exec_mode == "file_tools":
        if not allow_file_write_gate:
            raise TrustedAssemblyError(
                "Real file-write mode in CLI is closed until JH-015 passes independent review"
            )
        target_files = (task_payload.get("context") or {}).get("target_files") or []
        if len(target_files) != 1:
            raise TrustedAssemblyError("file_tools task requires exactly one authorized target_files entry")
        input_data = meta.get("input_data") or {}
        if "expected_json" not in input_data:
            raise TrustedAssemblyError("file_tools task requires metadata.input_data.expected_json for control acceptance")
        expected_str = json.dumps(input_data["expected_json"], ensure_ascii=False, sort_keys=True)
        return file_write_acceptance_evaluator(expected_str, target_files[0])

    input_data = meta.get("input_data")
    if not isinstance(input_data, dict):
        raise TrustedAssemblyError(
            "Trusted CLI assembly requires metadata.input_data dict for deterministic control acceptance"
        )
    if "records" in input_data and isinstance(input_data["records"], list):
        sort_key = input_data.get("sort_by", "region")
        expected = sorted(
            input_data["records"],
            key=lambda item: (item.get(sort_key, ""), item.get("sales", 0)) if isinstance(item, dict) else str(item),
        )
        return transform_acceptance_evaluator(expected)
    if "expected_output" in input_data:
        return transform_acceptance_evaluator(input_data["expected_output"])
    raise TrustedAssemblyError(
        "Unsupported task input_data shape for trusted acceptance evaluator (expected 'records' or 'expected_output')"
    )


def enforce_trusted_task_payload(
    task_payload: Dict[str, Any],
    *,
    trusted_workspace: Path,
    test_fixture_mode: bool = False,
) -> None:
    """Reject any payload attempt to escape the trusted workspace or select Mock in production."""
    ctx = task_payload.get("context") or {}
    payload_ws = ctx.get("workspace_root")
    if not payload_ws:
        raise TrustedAssemblyError("Task payload missing context.workspace_root")
    ws_norm = os.path.normcase(os.path.realpath(str(trusted_workspace)))
    payload_norm = os.path.normcase(os.path.realpath(str(Path(payload_ws))))
    if payload_norm != ws_norm and not payload_norm.startswith(ws_norm + os.sep):
        raise TrustedAssemblyError(
            f"Task workspace_root '{payload_ws}' is outside trusted CLI workspace '{trusted_workspace}'"
        )
    if not test_fixture_mode:
        for role_field in ("assigned_worker", "assigned_reviewer"):
            val = str(task_payload.get(role_field) or "").lower()
            if "mock" in val:
                raise TrustedAssemblyError(
                    f"Task payload cannot request mock profile '{val}' in production CLI mode"
                )
        meta = task_payload.get("metadata") or {}
        if meta.get("use_mock") or meta.get("allow_mock"):
            raise TrustedAssemblyError("Task payload cannot enable mock mode in production CLI")


def create_orchestrator(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    workspace_root: str | Path = PROJECT_ROOT,
    acceptance_evaluator: Optional[Callable] = None,
    test_fixture_mode: bool = False,
    allow_file_write_gate: bool = False,
    **kwargs,
) -> Orchestrator:
    ws = Path(workspace_root).resolve()
    db_file = Path(db_path)
    if str(db_path) != ":memory:":
        db_file.parent.mkdir(parents=True, exist_ok=True)
    storage = TaskStorage(db_file, read_only=False)

    if test_fixture_mode:
        worker = kwargs.pop("worker_adapter", None) or ScriptedWorkerAdapter(tool_requests_by_turn=[[]])
        reviewer = kwargs.pop("reviewer_adapter", None) or ScriptedReviewerAdapter(mode="pass")
        tool_exec = kwargs.pop("tool_executor", None) or (
            FileToolExecutor(ws) if allow_file_write_gate else MockToolExecutor()
        )
    else:
        if not has_credential("GEMINI_API_KEY"):
            storage.close()
            raise CredentialMissingError("Missing environment variable 'GEMINI_API_KEY' in production CLI mode")
        if not has_credential("MUSE_API_KEY"):
            storage.close()
            raise CredentialMissingError("Missing environment variable 'MUSE_API_KEY' in production CLI mode")
        worker = kwargs.pop("worker_adapter", None) or load_model_adapter("worker")
        reviewer = kwargs.pop("reviewer_adapter", None) or load_model_adapter("reviewer")
        tool_exec = kwargs.pop("tool_executor", None) or (
            FileToolExecutor(ws) if allow_file_write_gate else MockToolExecutor()
        )

    return Orchestrator(
        storage=storage,
        workspace_root=ws,
        worker_adapter=worker,
        reviewer_adapter=reviewer,
        tool_executor=tool_exec,
        acceptance_evaluator=acceptance_evaluator,
        enforce_target_files_scope=True,
        **kwargs,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="JH-Agent OS Task Control CLI (ADR-0003)")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to SQLite database file")
    parser.add_argument("--workspace", default=str(PROJECT_ROOT), help="Trusted workspace root")
    parser.add_argument(
        "--test-fixture-mode",
        action="store_true",
        help="Explicit offline test fixture mode (marks output is_mock=true; never counts as real evidence)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sub_submit = subparsers.add_parser("submit", help="Submit a Task JSON file and optionally run it")
    sub_submit.add_argument("task_file", help="Path to Task JSON file")
    sub_submit.add_argument("--run", action="store_true", help="Immediately run task after submission")

    sub_resume = subparsers.add_parser("resume", help="Continue an existing non-terminal task within its persisted budget")
    sub_resume.add_argument("task_id", help="Task ID")

    sub_status = subparsers.add_parser("status", help="Read-only: query task status by task_id")
    sub_status.add_argument("task_id", help="Task ID")

    sub_summary = subparsers.add_parser("summary", help="Read-only: structured single-page task summary & evidence")
    sub_summary.add_argument("task_id", help="Task ID")

    sub_events = subparsers.add_parser("events", help="Read-only: list audit events for a task_id")
    sub_events.add_argument("task_id", help="Task ID")

    sub_artifact = subparsers.add_parser(
        "artifact",
        help="Read-only: show final artifact, version lineage, review bindings, acceptance and memory evidence",
    )
    sub_artifact.add_argument("task_id", help="Task ID")

    sub_approve = subparsers.add_parser("approve", help="Approve a held Level 2 request_id and resume")
    sub_approve.add_argument("task_id", help="Task ID")
    sub_approve.add_argument("request_id", help="ToolRequest ID to approve")

    sub_cancel = subparsers.add_parser("cancel", help="Cancel a non-terminal task")
    sub_cancel.add_argument("task_id", help="Task ID")
    sub_cancel.add_argument("--reason", default="Cancelled via CLI", help="Cancellation reason")

    subparsers.add_parser("recover", help="Reconcile interrupted tasks after restart without blind replay")

    args = parser.parse_args(argv)
    trusted_ws = Path(args.workspace).resolve()

    # 1. Strict Read-Only Commands: NEVER create dirs/DB, NEVER migrate, NEVER invoke models.
    if args.command in READ_ONLY_COMMANDS:
        try:
            ro_storage = open_readonly_storage(args.db)
        except (FileNotFoundError, KeyError, PermissionError) as exc:
            print(json.dumps({"command_ok": False, "read_only": True, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        try:
            if args.command == "status":
                summary = build_task_summary(ro_storage, args.task_id)
                print(json.dumps(summary["task_result"], ensure_ascii=False, indent=2))
            elif args.command == "summary":
                print(json.dumps(build_task_summary(ro_storage, args.task_id), ensure_ascii=False, indent=2))
            elif args.command == "events":
                print(json.dumps(ro_storage.list_events(args.task_id), ensure_ascii=False, indent=2))
            elif args.command == "artifact":
                summary = build_task_summary(ro_storage, args.task_id)
                print(json.dumps({
                    "command_ok": True,
                    "read_only": True,
                    "task_status": summary["task_result"],
                    "latest_artifact": ro_storage.get_latest_artifact(args.task_id),
                    "artifact_versions": ro_storage.list_artifact_versions(args.task_id),
                    "review_bindings": ro_storage.list_review_bindings(args.task_id),
                    "memory_records": ro_storage.list_memory_records(args.task_id),
                }, ensure_ascii=False, indent=2))
            return 0
        except KeyError as exc:
            print(json.dumps({"command_ok": False, "read_only": True, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        finally:
            ro_storage.close()

    # 2. State-changing commands under trusted local assembly.
    orch: Optional[Orchestrator] = None
    try:
        if args.command == "submit":
            payload = json.loads(Path(args.task_file).read_text(encoding="utf-8"))
            enforce_trusted_task_payload(
                payload, trusted_workspace=trusted_ws, test_fixture_mode=args.test_fixture_mode
            )
            evaluator = resolve_trusted_evaluator(payload, allow_file_write_gate=False)
            orch = create_orchestrator(
                args.db,
                workspace_root=trusted_ws,
                acceptance_evaluator=evaluator,
                test_fixture_mode=args.test_fixture_mode,
            )
            task = orch.submit_task(payload)
            if args.run:
                task = orch.run_task(task["task_id"])
            out = build_task_summary(orch.storage, task["task_id"])
            out["read_only"] = False
            out["test_fixture_mode"] = bool(args.test_fixture_mode)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if task["status"] in {"created", "succeeded", "awaiting_approval"} else 1

        if args.command == "resume":
            ro = open_readonly_storage(args.db)
            try:
                existing = ro.get_task(args.task_id)
            finally:
                ro.close()
            if existing["status"] in TERMINAL_STATES or existing["status"] in {"needs_attention", "awaiting_approval"}:
                print(json.dumps({
                    "command_ok": False,
                    "error": f"Cannot resume task in state '{existing['status']}' without explicit resolution/approval",
                }, ensure_ascii=False, indent=2))
                return 1
            enforce_trusted_task_payload(
                existing, trusted_workspace=trusted_ws, test_fixture_mode=args.test_fixture_mode
            )
            evaluator = resolve_trusted_evaluator(existing, allow_file_write_gate=False)
            orch = create_orchestrator(
                args.db,
                workspace_root=trusted_ws,
                acceptance_evaluator=evaluator,
                test_fixture_mode=args.test_fixture_mode,
            )
            task = orch.run_task(args.task_id)
            out = build_task_summary(orch.storage, args.task_id)
            out["read_only"] = False
            out["test_fixture_mode"] = bool(args.test_fixture_mode)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if task["status"] in {"succeeded", "awaiting_approval"} else 1

        if args.command == "approve":
            ro = open_readonly_storage(args.db)
            try:
                existing = ro.get_task(args.task_id)
            finally:
                ro.close()
            evaluator = resolve_trusted_evaluator(existing, allow_file_write_gate=False)
            orch = create_orchestrator(
                args.db,
                workspace_root=trusted_ws,
                acceptance_evaluator=evaluator,
                test_fixture_mode=args.test_fixture_mode,
            )
            task = orch.approve_task_request(args.task_id, args.request_id)
            out = build_task_summary(orch.storage, args.task_id)
            out["read_only"] = False
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if task["status"] in {"succeeded", "awaiting_approval"} else 1

        if args.command == "cancel":
            storage = TaskStorage(args.db, read_only=False)
            try:
                storage.transition_task_state(
                    args.task_id, "cancelled", actor_role="user", reason=args.reason, error_info=args.reason
                )
                out = build_task_summary(storage, args.task_id)
                out["read_only"] = False
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0
            finally:
                storage.close()

        if args.command == "recover":
            storage = TaskStorage(args.db, read_only=False)
            try:
                summary = storage.recover_interrupted_tasks()
                print(json.dumps({"command_ok": True, "recovered": summary}, ensure_ascii=False, indent=2))
                return 0
            finally:
                storage.close()

        return 1
    except (
        TrustedAssemblyError,
        CredentialMissingError,
        AdapterError,
        SchemaValidationError,
        InvalidStateTransitionError,
        FileNotFoundError,
        KeyError,
    ) as exc:
        print(json.dumps({"command_ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    finally:
        if orch is not None:
            orch.storage.close()


if __name__ == "__main__":
    raise SystemExit(main())
