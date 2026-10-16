"""Capture one host tool execution from a live session log (JH-006 capture-only).

Usage (run by the trusted host operator, never by the model under test):
    python scripts/probe_host_capture.py --protocol desktop --thread-id <tid>
        --segment <rollout.jsonl> [--segment ...] --item-id <exec-id>
        --call-id <call-id> --expect-argv-json <argv.json>
        --expect-workdir <dir> --expect-exit 0 --expect-marker PASS
        --task-id <tid> --delivery-id <dlv> --apply-attempt-id <aid>
        --artifact-claim-json <json> --review-claim-json <json>
        --archive-dir <dir>

The operator pins selectors, expectations, and claim labels on the command
line. Execution facts (argv, workdir, exit, output, ids, timestamps) come
ONLY from session-log bytes read from --segment files; nothing model-pasted
feeds them. Each segment is read twice with a stability delay and must be
byte-identical, otherwise the capture aborts without writing anything.

On success the script writes exactly two files into --archive-dir:
events.jsonl (the verbatim selected raw lines) and manifest.json (claims +
recomputed digests), prints a verification JSON report, and exits 0.
Verification, input, and segment IO failures exit 2 with reasons and write
nothing. Usage errors follow argparse (exit 2). Environment failures such
as an unresolvable src/ import exit 1. No models, no network, no subprocesses.
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from src.host_capture import (
        CaptureError,
        build_capture_manifest,
        parse_jsonl,
        require_desktop_session_meta,
        select_cli_execution,
        select_desktop_execution,
        select_desktop_tool_call,
        verify_cli_binding,
        verify_desktop_binding,
    )
except ImportError as exc:
    print(json.dumps({"captured": False, "fatal": f"cannot import src.host_capture: {exc}. "
                      "Run from the worktree root so src/ resolves."}, ensure_ascii=False))
    raise SystemExit(1)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_stable(path: Path, *, delay_seconds: float = 1.0) -> bytes:
    try:
        first = path.read_bytes()
    except OSError as exc:
        raise CaptureError(f"segment-unreadable: {path}: {exc}") from None
    time.sleep(delay_seconds)
    try:
        second = path.read_bytes()
    except OSError as exc:
        raise CaptureError(f"segment-unreadable: {path}: {exc}") from None
    if first != second:
        raise CaptureError(f"segment-unstable: {path} changed during capture; retry later")
    return first


def _load_json_arg(text: str, name: str):
    try:
        return json.loads(text)
    except ValueError as exc:
        raise CaptureError(f"arg-malformed: {name} is not JSON: {exc}") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Capture one host tool execution record.")
    parser.add_argument("--protocol", required=True, choices=("desktop", "cli"))
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--segment", required=True, action="append",
                        help="Session log segment path (repeatable).")
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--call-id", help="Desktop exec tool call id (desktop only).")
    parser.add_argument("--expect-argv-json", help="JSON argv list (desktop only).")
    parser.add_argument("--expect-command", help="Exact command string (cli only).")
    parser.add_argument("--expect-workdir", required=True)
    parser.add_argument("--expect-exit", required=True, type=int)
    parser.add_argument("--expect-marker", default="")
    parser.add_argument("--expect-call-input-file",
                        help="File holding the exact pinned call script (desktop only).")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--delivery-id", required=True)
    parser.add_argument("--apply-attempt-id", required=True)
    parser.add_argument("--artifact-claim-json", required=True)
    parser.add_argument("--review-claim-json", required=True)
    parser.add_argument("--archive-dir", required=True)
    args = parser.parse_args(argv)

    try:
        if args.protocol == "desktop" and (not args.call_id or not args.expect_argv_json):
            raise CaptureError("arg-malformed: desktop requires --call-id and --expect-argv-json")
        if args.protocol == "cli" and not args.expect_command:
            raise CaptureError("arg-malformed: cli requires --expect-command")
        artifact_claim = _load_json_arg(args.artifact_claim_json, "--artifact-claim-json")
        review_claim = _load_json_arg(args.review_claim_json, "--review-claim-json")
        expected_call_input = None
        if args.expect_call_input_file:
            try:
                expected_call_input = Path(args.expect_call_input_file).read_text(encoding="utf-8")
            except OSError as exc:
                raise CaptureError(f"arg-malformed: cannot read call script file: {exc}") from None

        segments = []
        for seg in args.segment:
            raw = _read_stable(Path(seg))
            segments.append({"path": seg, "raw": raw,
                             "sha256": hashlib.sha256(raw).hexdigest(),
                             "bytes": len(raw)})
        records = []
        for seg in segments:
            records.extend(parse_jsonl(seg["raw"], source_label=seg["path"]))

        if args.protocol == "desktop":
            meta = require_desktop_session_meta(records, args.thread_id)
            selected = select_desktop_execution(records, thread_id=args.thread_id,
                                                item_id=args.item_id)
            call = select_desktop_tool_call(records, call_id=args.call_id,
                                            turn_id=selected["turn_id"])
            bindings = verify_desktop_binding(
                selected, call, expected_argv=_load_json_arg(args.expect_argv_json,
                                                             "--expect-argv-json"),
                expected_workdir=args.expect_workdir, expected_exit=args.expect_exit,
                expected_marker=args.expect_marker or None,
                expected_call_input=expected_call_input)
            archived_lines = [meta, selected, call]
        else:
            selected = select_cli_execution(records, thread_id=args.thread_id,
                                            item_id=args.item_id)
            bindings = verify_cli_binding(
                selected, expected_command=args.expect_command,
                expected_exit=args.expect_exit,
                expected_marker=args.expect_marker or None)
            archived_lines = [selected]

        manifest = build_capture_manifest(
            task_id=args.task_id, delivery_id=args.delivery_id,
            artifact_claim=artifact_claim, review_claim=review_claim,
            apply_attempt_id=args.apply_attempt_id, protocol=args.protocol,
            source_files=[{"path": s["path"], "sha256": s["sha256"], "bytes": s["bytes"]}
                          for s in segments],
            bindings=bindings)
        archive_dir = Path(args.archive_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)
        events_path = archive_dir / "events.jsonl"
        manifest_path = archive_dir / "manifest.json"
        with events_path.open("wb") as handle:
            for entry in archived_lines:
                for key in ("raw", "raw_call", "raw_output"):
                    if key in entry:
                        handle.write(entry[key] + b"\n")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        print(json.dumps({"captured": True, "protocol": args.protocol,
                          "task_claim": args.task_id, "delivery_claim": args.delivery_id,
                          "item_id": args.item_id, "bindings": bindings,
                          "archive": {"events": str(events_path),
                                      "events_sha256": _sha256_file(events_path),
                                      "manifest": str(manifest_path),
                                      "manifest_sha256": _sha256_file(manifest_path)}},
                         ensure_ascii=False, indent=2))
        return 0
    except CaptureError as exc:
        print(json.dumps({"captured": False, "reason": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
