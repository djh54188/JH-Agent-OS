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
ONLY from files resolved within the observed desktop session store; caller-written
workspace logs are refused. The exact outer call script is mandatory. One
nonoverlapping call window must contain exactly one completed command; parallel
or ambiguous windows are refused. This is operator-invoked capture plumbing,
not an authenticated production recorder and never a production completion gate. Each segment is read twice with a stability delay and must be
byte-identical, otherwise the capture aborts without writing anything.

On success the script writes exactly two files into --archive-dir:
events.jsonl (the verbatim selected raw lines) and manifest.json (claims +
recomputed digests), prints a verification JSON report, and exits 0.
Verification/input failures exit 2 before publication. Existing evidence is never
overwritten. An interrupted publication or failed reporting step requires archive
inspection before retry; an exit code alone cannot prove absence of disk effects. Usage errors follow argparse (exit 2). Environment failures such
as an unresolvable src/ import exit 1. No models, no network, no subprocesses.
"""

import argparse
import hashlib
import json
import os
import tempfile
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from src.host_capture import (
        CaptureError,
        MAX_INPUT_BYTES,
        verify_capture_archive,
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


# Observed desktop store only. No caller-supplied root or workspace logs qualify.
SESSION_ROOT = Path.home() / ".codex" / "sessions"


def _trusted_segment(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(SESSION_ROOT.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise CaptureError(f"source-untrusted: require a file in the observed desktop session store: {exc}") from None
    if not resolved.is_file() or resolved.suffix != ".jsonl":
        raise CaptureError("source-untrusted: require a JSONL session file")
    return resolved


def _bounded_read(path: Path) -> bytes:
    with path.open("rb") as handle:
        raw = handle.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise CaptureError("input-too-large: session bytes exceed the capture bound")
    return raw


def _read_stable(path: Path, *, delay_seconds: float = 1.0) -> bytes:
    try:
        first = _bounded_read(path)
    except OSError as exc:
        raise CaptureError(f"segment-unreadable: {path}: {exc}") from None
    time.sleep(delay_seconds)
    try:
        second = _bounded_read(path)
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
        if args.protocol == "desktop" and (not args.call_id or not args.expect_argv_json
                                           or not args.expect_call_input_file):
            raise CaptureError("arg-malformed: desktop requires call id, argv and exact call script file")
        if args.protocol == "cli":
            raise CaptureError("workdir-unattested: CLI capture is not admitted without independent cwd attestation")
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
            path = _trusted_segment(Path(seg))
            if any(s["path"] == str(path) for s in segments):
                raise CaptureError("source-duplicate: repeated segment")
            raw = _read_stable(path)
            segments.append({"path": str(path), "raw": raw,
                             "sha256": hashlib.sha256(raw).hexdigest(),
                             "bytes": len(raw)})
        # Select one owning segment; never stitch a foreign session_meta onto events.
        owners = []
        for seg in segments:
            rs = parse_jsonl(seg["raw"], source_label=seg["path"])
            require_desktop_session_meta(rs, args.thread_id)
            if any(r["obj"].get("type") == "event_msg"
                   and r["obj"].get("payload", {}).get("item", {}).get("id") == args.item_id
                   for r in rs):
                owners.append((seg, rs))
        if len(owners) != 1:
            raise CaptureError("segment-ambiguous: require exactly one owning segment")
        owner, records = owners[0]

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
                          for s in [owner]],
            bindings=bindings)
        # Preserve source ordering and seal the WHOLE selected context, not just execution.
        raw_lines = {entry[key] for entry in archived_lines
                     for key in ("raw", "raw_call", "raw_output") if key in entry}
        archive_raw = b"".join(r["raw"] + b"\n" for r in records if r["raw"] in raw_lines)
        manifest["archive_digest"] = hashlib.sha256(archive_raw).hexdigest()
        manifest["source_policy"] = "observed-desktop-store/operator-invoked-capture-only/v1"
        verify_capture_archive(manifest, archive_raw=archive_raw)
        archive_dir = Path(args.archive_dir)
        if archive_dir.exists():
            raise CaptureError("archive-exists: refusing to overwrite existing evidence")
        archive_dir.parent.mkdir(parents=True, exist_ok=True)
        # Publish the two-file evidence directory together; IO failures leave no final archive.
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        archive_digests = {"events": hashlib.sha256(archive_raw).hexdigest(),
                           "manifest": hashlib.sha256(manifest_bytes).hexdigest()}
        with tempfile.TemporaryDirectory(prefix=".capture-", dir=archive_dir.parent) as tmp:
            staged = Path(tmp) / "sealed"
            staged.mkdir()
            (staged / "events.jsonl").write_bytes(archive_raw)
            (staged / "manifest.json").write_bytes(manifest_bytes)
            if archive_dir.exists():
                raise CaptureError("archive-exists: refusing to overwrite existing evidence")
            os.rename(staged, archive_dir)
        events_path = archive_dir / "events.jsonl"
        manifest_path = archive_dir / "manifest.json"
        print(json.dumps({"captured": True, "protocol": args.protocol,
                          "task_claim": args.task_id, "delivery_claim": args.delivery_id,
                          "item_id": args.item_id, "bindings": bindings,
                          "archive": {"events": str(events_path),
                                      "events_sha256": archive_digests["events"],
                                      "manifest": str(manifest_path),
                                      "manifest_sha256": archive_digests["manifest"]}},
                         ensure_ascii=False, indent=2))
        return 0
    except (CaptureError, OSError, UnicodeError) as exc:
        print(json.dumps({"captured": False, "reason": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
