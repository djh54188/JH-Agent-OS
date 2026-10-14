"""
JH-Agent OS N1 Structured CLI Entrypoint v1.3.0 (ADR-0003/ADR-0004, JH-004)
Supports subcommands:
  - Read-only (never creates directories/DB, never migrates, never invokes models):
      status, events, artifact, summary
  - Bounded state-changing operations (under trusted local assembly):
      submit, resume, approve, cancel, recover

JH-004 production path: explicit NoToolExecutor (no model tools), one fixed
records-transform template, trusted source/template binding, owner and
uncertainty resume gates. Real file-write mode stays closed.
"""

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable, Dict, List, Optional

from src.acceptance import file_write_acceptance_evaluator, transform_acceptance_evaluator
from src.adapters import AdapterError, CredentialMissingError, MockModelAdapter, SchemaValidationError
from src.file_tools import FileToolExecutor
from src.mock_components import MockToolExecutor, ScriptedReviewerAdapter, ScriptedWorkerAdapter
from src.model_config import has_credential, load_model_adapter
from src.orchestrator import Orchestrator
from src.production_executor import NoToolExecutor
from src.state_machine import TERMINAL_STATES, InvalidStateTransitionError
from src.storage import TaskStorage

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "jh_agent_os.sqlite3"
READ_ONLY_COMMANDS = frozenset({"status", "events", "artifact", "summary"})

# JH-004 fixed template: records transform with a trusted sort rule and exactly
# two required conditions. The id is stamped into the trusted binding at submit.
RECORDS_TRANSFORM_TEMPLATE_ID = "records_transform_v1"
# The two standard conditions, in order. The template evaluator judges exactly
# these checks positionally, so anything else (non-template text, misordered or
# extra conditions) is refused before models run rather than echoed as passed.
RECORDS_TRANSFORM_CRITERIA = (
    "Trusted input records were provided",
    "Artifact content equals the expected sorted records",
)
# Metadata key for the CLI-stamped provenance block. Overwritten (never merged)
# at submit; models and payloads cannot set it.
TRUSTED_STAMP_KEY = "_trusted"


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
            "source_binding": (task.get("metadata") or {}).get(TRUSTED_STAMP_KEY),
            "delivery_scope": {
                "generated_artifact": latest_artifact is not None,
                "target_applied": False,
                "note": "JH-004 delivers generated artifacts only; no target files are applied",
            },
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
    passes independent review. The generate path accepts exactly one fixed
    template (records_transform_v1); anything else is refused before models run.
    """
    meta = task_payload.get("metadata") or {}
    exec_mode = meta.get("execution_mode", "generate")
    if exec_mode not in ("generate", "file_tools"):
        raise TrustedAssemblyError(
            f"Unknown execution_mode '{exec_mode}'; supported: 'generate' (gated 'file_tools' only)"
        )
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
    if set(input_data) != {"records"}:
        raise TrustedAssemblyError(
            "Template records_transform_v1 accepts only input_data.records; "
            "sort_by/expected_output/custom shapes are unsupported"
        )
    records = input_data["records"]
    if (
        not isinstance(records, list)
        or not records
        or any(
            not isinstance(item, dict)
            or not isinstance(item.get("region"), str)
            or not isinstance(item.get("sales"), (int, float))
            or isinstance(item.get("sales"), bool)
            for item in records
        )
    ):
        raise TrustedAssemblyError(
            "Template records_transform_v1 requires records: non-empty list of {region: str, sales: number}"
        )
    criteria = task_payload.get("acceptance_criteria") or []
    if list(criteria) != list(RECORDS_TRANSFORM_CRITERIA):
        raise TrustedAssemblyError(
            "Template records_transform_v1 requires exactly the two standard "
            "criteria in order; non-template, misordered, or extra conditions refused"
        )
    expected = sorted(records, key=lambda item: (item.get("region", ""), item.get("sales", 0)))
    return transform_acceptance_evaluator(expected)


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


def stamp_trusted_binding(task_payload: Dict[str, Any], *, test_fixture_mode: bool) -> None:
    """Overwrite (never merge) the trusted provenance block from CLI flags.

    Runs after resolve_trusted_evaluator accepted the payload shape and before
    any adapter load or model call. Payload-supplied _trusted content is
    discarded; the persisted block is authoritative on later resume/approve.
    """
    meta = task_payload.get("metadata")
    if not isinstance(meta, dict):
        raise TrustedAssemblyError("Task payload metadata must be an object for trusted binding")
    exec_mode = meta.get("execution_mode", "generate")
    template_id = "file_write_v1" if exec_mode == "file_tools" else RECORDS_TRANSFORM_TEMPLATE_ID
    meta[TRUSTED_STAMP_KEY] = {
        "source": "fixture" if test_fixture_mode else "production",
        "template_id": template_id,
        "execution_mode": exec_mode,
    }


def check_source_binding(
    existing_task: Dict[str, Any],
    *,
    test_fixture_mode: bool,
    expected_template_id: str,
) -> None:
    """Refuse fixture/production swaps and template drift on resume/approve."""
    want = "fixture" if test_fixture_mode else "production"
    stamped = ((existing_task.get("metadata") or {}).get(TRUSTED_STAMP_KEY) or {})
    got = stamped.get("source")
    if got is None:
        raise TrustedAssemblyError(
            "Task predates trusted source binding; re-submit is required (refusing blind resume/approve)"
        )
    if got != want:
        raise TrustedAssemblyError(
            f"Source mismatch: task is '{got}' but CLI runs '{want}'; fixture/production swap refused"
        )
    if stamped.get("template_id") != expected_template_id:
        raise TrustedAssemblyError(
            "Template binding mismatch: persisted template does not match the resolved evaluator"
        )


def _production_taint_blocker(ro_storage: TaskStorage, task_id: str) -> Optional[str]:
    """Refusal reason when recorded production calls show mock/unconfirmed source.

    Reads the persisted per-call source records, never the current CLI switch:
    a task whose history contains a mock-marked or unconfirmed model response
    must not resume as production even if the flags look clean today.
    """
    for event in ro_storage.list_events(task_id):
        if event.get("event_type") != "model_response_source":
            continue
        details = event.get("details") or {}
        if details.get("is_mock") is not False:
            return (
                f"recorded {details.get('role', 'model')} call has mock/unconfirmed "
                f"source (attempt {details.get('attempt_id')})"
            )
    return None


def resume_blockers(ro_storage: TaskStorage, existing_task: Dict[str, Any]) -> List[str]:
    """Owner/uncertainty gates that ordinary resume must not cross."""
    task_id = existing_task["task_id"]
    runtime = existing_task.get("_runtime") or {}
    blockers: List[str] = []
    if (ro_storage.get_control(task_id) or {}).get("owner_id"):
        blockers.append("owner_held_by_active_execution")
    if any(a.get("status") == "in_flight" for a in ro_storage.list_model_attempts(task_id)):
        blockers.append("unconfirmed_model_attempts")
    if any(t.get("status") == "in_flight" for t in ro_storage.list_tool_executions(task_id)):
        blockers.append("unconfirmed_tool_executions")
    if runtime.get("has_unconfirmed_side_effect"):
        blockers.append("unconfirmed_side_effect")
    if runtime.get("tokens_unknown_count", 0) > 0:
        blockers.append("unknown_token_usage")
    return blockers


def _is_known_mock_adapter(adapter: Any) -> bool:
    """True when the adapter object is a known mock/test double.

    Catches both directly injected doubles and adapters loaded from a mock
    config (a mock config still produces a mock adapter object). Real
    provider adapters never match: their classes and model ids carry no
    mock marker.
    """
    if adapter is None:
        return False
    if isinstance(adapter, (MockModelAdapter, ScriptedWorkerAdapter, ScriptedReviewerAdapter)):
        return True
    name = type(adapter).__name__.lower()
    model_id = str(getattr(adapter, "model_id", "") or "").lower()
    return "mock" in name or "mock" in model_id


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
        # Fixture branch only: an explicit mock executor for offline tests.
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
        for role, adapter in (("worker", worker), ("reviewer", reviewer)):
            if _is_known_mock_adapter(adapter):
                storage.close()
                raise TrustedAssemblyError(
                    f"Production assembly refused known mock {role} adapter "
                    f"'{type(adapter).__name__}' (model_id '{getattr(adapter, 'model_id', None)}')"
                )
        # Production branch: explicit NoTool executor. Mock successes must never
        # appear here; every model-proposed tool request is denied instead.
        tool_exec = kwargs.pop("tool_executor", None) or (
            FileToolExecutor(ws) if allow_file_write_gate else NoToolExecutor()
        )

    return Orchestrator(
        storage=storage,
        workspace_root=ws,
        worker_adapter=worker,
        reviewer_adapter=reviewer,
        tool_executor=tool_exec,
        acceptance_evaluator=acceptance_evaluator,
        enforce_target_files_scope=True,
        require_real_responses=not test_fixture_mode,
        **kwargs,
    )


def _expected_template_id(task_payload: Dict[str, Any]) -> str:
    exec_mode = (task_payload.get("metadata") or {}).get("execution_mode", "generate")
    return "file_write_v1" if exec_mode == "file_tools" else RECORDS_TRANSFORM_TEMPLATE_ID


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
                ro_storage.get_task(args.task_id)  # fail closed on missing task, like other queries
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
            stamp_trusted_binding(payload, test_fixture_mode=args.test_fixture_mode)
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
            check_source_binding(
                existing, test_fixture_mode=args.test_fixture_mode,
                expected_template_id=_expected_template_id(existing),
            )
            ro2 = open_readonly_storage(args.db)
            try:
                blockers = resume_blockers(ro2, existing)
                if not args.test_fixture_mode:
                    taint = _production_taint_blocker(ro2, args.task_id)
                    if taint:
                        blockers.append("tainted_production_history: " + taint)
            finally:
                ro2.close()
            if blockers:
                print(json.dumps({
                    "command_ok": False,
                    "error": "resume blocked: " + ", ".join(blockers),
                    "blockers": blockers,
                }, ensure_ascii=False, indent=2))
                return 1
            orch = create_orchestrator(
                args.db,
                workspace_root=trusted_ws,
                acceptance_evaluator=evaluator,
                test_fixture_mode=args.test_fixture_mode,
            )
            task = orch.run_task(args.task_id)
            ro3 = open_readonly_storage(args.db)
            try:
                raced_owner = (ro3.get_control(args.task_id) or {}).get("owner_id")
            finally:
                ro3.close()
            if raced_owner:
                print(json.dumps({
                    "command_ok": False,
                    "error": "resume raced with another active owner; result not driven by this call",
                }, ensure_ascii=False, indent=2))
                return 1
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
            check_source_binding(
                existing, test_fixture_mode=args.test_fixture_mode,
                expected_template_id=_expected_template_id(existing),
            )
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
