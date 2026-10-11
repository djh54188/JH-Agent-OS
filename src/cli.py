"""
JH-Agent OS Phase 2 Task Submission & Status Query CLI Entrypoint v1.0.0
Supports subcommands: submit, status, events, approve, cancel, recover.
"""

import argparse
import json
from pathlib import Path
import sys
from typing import List, Optional

from src.orchestrator import Orchestrator
from src.storage import TaskStorage

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "jh_agent_os.sqlite3"


def create_orchestrator(db_path: str | Path = DEFAULT_DB_PATH, **kwargs) -> Orchestrator:
    db_file = Path(db_path)
    if str(db_path) != ":memory:":
        db_file.parent.mkdir(parents=True, exist_ok=True)
    storage = TaskStorage(db_file)
    return Orchestrator(storage=storage, workspace_root=PROJECT_ROOT, **kwargs)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="JH-Agent OS Task Control CLI")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to SQLite database file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sub_submit = subparsers.add_parser("submit", help="Submit a Task JSON file and optionally run it")
    sub_submit.add_argument("task_file", help="Path to Task JSON file")
    sub_submit.add_argument("--run", action="store_true", help="Immediately run task after submission")

    sub_status = subparsers.add_parser("status", help="Query task status by task_id")
    sub_status.add_argument("task_id", help="Task ID")

    sub_events = subparsers.add_parser("events", help="List audit events for a task_id")
    sub_events.add_argument("task_id", help="Task ID")

    sub_approve = subparsers.add_parser("approve", help="Approve a held Level 2 request_id and resume")
    sub_approve.add_argument("task_id", help="Task ID")
    sub_approve.add_argument("request_id", help="ToolRequest ID to approve")

    sub_cancel = subparsers.add_parser("cancel", help="Cancel a non-terminal task")
    sub_cancel.add_argument("task_id", help="Task ID")
    sub_cancel.add_argument("--reason", default="Cancelled via CLI", help="Cancellation reason")

    subparsers.add_parser("recover", help="Recover interrupted tasks after restart")

    args = parser.parse_args(argv)
    orch = create_orchestrator(args.db)
    try:
        if args.command == "submit":
            payload = json.loads(Path(args.task_file).read_text(encoding="utf-8"))
            task = orch.submit_task(payload)
            if args.run:
                task = orch.run_task(task["task_id"])
            print(json.dumps(orch.get_task_status(task["task_id"]), ensure_ascii=False, indent=2))
        elif args.command == "status":
            print(json.dumps(orch.get_task_status(args.task_id), ensure_ascii=False, indent=2))
        elif args.command == "events":
            print(json.dumps(orch.list_task_events(args.task_id), ensure_ascii=False, indent=2))
        elif args.command == "approve":
            orch.approve_task_request(args.task_id, args.request_id)
            print(json.dumps(orch.get_task_status(args.task_id), ensure_ascii=False, indent=2))
        elif args.command == "cancel":
            orch.cancel_task(args.task_id, reason=args.reason)
            print(json.dumps(orch.get_task_status(args.task_id), ensure_ascii=False, indent=2))
        elif args.command == "recover":
            summary = orch.recover_interrupted_tasks()
            print(json.dumps({"recovered": summary}, ensure_ascii=False, indent=2))
        return 0
    finally:
        orch.storage.close()


if __name__ == "__main__":
    raise SystemExit(main())
