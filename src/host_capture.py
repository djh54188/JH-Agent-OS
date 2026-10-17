"""Independent host-record capture parsing and binding verification.

Pure helpers (stdlib only): parse host session event bytes in the two OBSERVED
protocols (desktop rollout jsonl, CLI event stream), select the single tool
execution record for pinned call identifiers, and verify its bindings against
operator-pinned expectations. Execution facts come only from raw log bytes
supplied by the caller; the trusted capture entry reads them from the live
session store. Model-filled text is never an input here.

Measured protocol sources (never assumed):
- desktop: C:\\Users\\a\\.codex\\sessions rollout jsonl; envelope
  {timestamp,ordinal,type,payload}; CommandExecution items carry argv arrays,
  file:// cwd, process_id, status, exit_code, duration, aggregated_output;
  custom_tool_call / custom_tool_call_output link via call_id.
- cli: `codex exec --json` stdout stream (official doc
  learn.chatgpt.com/docs/non-interactive-mode plus the committed 33-line
  sample): flat {type,item} events; command_execution items carry id, string
  command, aggregated output, exit code, status. No cwd, no argv array,
  no timestamps, no duration.

Command verification uses exact equality only (argv list for desktop, full
command string for CLI). Substring/contains matching is forbidden: echoing
keywords must never pass. The CLI stream carries no working directory, so
CLI bindings always fail closed with workdir-unattested; launcher-side
cwd attestation is an architect decision, not smuggled in here.

This module NEVER feeds complete_code_delivery. Production complete stays
closed; these helpers produce capture evidence for architect review only.
"""

import json
import os
import ntpath
import re
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse


class CaptureError(Exception):
    """A capture parse or binding check failed. Reason codes lead the message."""


MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_EVENT_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_CHARS = 1024 * 1024


def sha256_hex(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def parse_jsonl(raw: bytes, *, source_label: str) -> List[Dict[str, Any]]:
    """Split raw session bytes into parsed lines, keeping raw bytes per line.

    Strict: any non-blank line that is not a JSON object fails the whole
    parse (a torn or tampered read must be retried by the operator, never
    papered over). Returns [{line_no, raw, obj}].
    """
    if not isinstance(raw, bytes):
        raise CaptureError("input-malformed: session bytes must be bytes")
    if len(raw) > MAX_INPUT_BYTES:
        raise CaptureError("input-too-large: session bytes exceed the capture bound")
    records = []
    for index, line in enumerate(raw.split(b"\n"), start=1):
        if not line.strip():
            continue
        if len(line) > MAX_EVENT_BYTES:
            raise CaptureError(f"event-too-large: line {index} exceeds the event bound")
        try:
            obj = json.loads(line.decode("utf-8"), object_pairs_hook=_strict_object,
                             parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
        except (UnicodeDecodeError, ValueError) as exc:
            raise CaptureError(f"unparsed-line: line {index} in {source_label}: {exc}") from None
        if not isinstance(obj, dict):
            raise CaptureError(f"unparsed-line: line {index} in {source_label} is not an object")
        if obj.get("type") in ("session_meta", "event_msg", "response_item"):
            if not isinstance(obj.get("payload"), dict):
                raise CaptureError(f"record-malformed: payload at line {index} must be an object")
            if "item" in obj["payload"] and not isinstance(obj["payload"]["item"], dict):
                raise CaptureError(f"record-malformed: item at line {index} must be an object")
        if obj.get("type") == "item.completed" and not isinstance(obj.get("item"), dict):
            raise CaptureError(f"record-malformed: CLI item at line {index} must be an object")
        records.append({"line_no": index, "raw": line, "obj": obj, "source": source_label})
    if not records:
        raise CaptureError("no-records: session input holds no event lines")
    return records


def require_desktop_session_meta(records: List[Dict[str, Any]], thread_id: str) -> Dict[str, Any]:
    """Require a session_meta line attributing the segment to the thread."""
    metas = [r for r in records if r["obj"].get("type") == "session_meta"]
    if len(metas) != 1 or metas[0]["obj"]["payload"].get("id") != thread_id:
        raise CaptureError("meta-mismatch: require exactly one matching session_meta per segment")
    record = metas[0]
    payload = record["obj"]["payload"]
    return {"thread_id": thread_id, "line_no": record["line_no"],
            "cwd": payload.get("cwd"), "originator": payload.get("originator"),
            "raw": record["raw"], "source": record["source"]}


def select_desktop_execution(records: List[Dict[str, Any]], *, thread_id: str,
                             item_id: str) -> Dict[str, Any]:
    """Select the single desktop CommandExecution item for the pinned ids."""
    matches = []
    for record in records:
        obj = record["obj"]
        if obj.get("type") != "event_msg":
            continue
        payload = obj.get("payload") or {}
        if payload.get("type") != "item_completed":
            continue
        item = payload.get("item") or {}
        if (payload.get("thread_id") == thread_id and item.get("type") == "CommandExecution"
                and item.get("id") == item_id):
            matches.append((record, payload, item))
    if not matches:
        raise CaptureError(f"missing-item: no CommandExecution {item_id!r} in thread {thread_id!r}")
    if len(matches) > 1:
        raise CaptureError(f"duplicate-item: {item_id!r} appears {len(matches)} times")
    record, payload, item = matches[0]
    argv = item.get("command")
    if not isinstance(argv, list) or not argv or any(not isinstance(t, str) for t in argv):
        raise CaptureError("command-malformed: desktop execution argv must be a non-empty string list")
    if not isinstance(item.get("cwd"), str) or not item.get("cwd"):
        raise CaptureError("cwd-missing: desktop execution carries no working directory")
    if item.get("status") != "completed":
        raise CaptureError(f"status-not-completed: desktop execution status is {item.get('status')!r}")
    if not _is_int(item.get("exit_code")):
        raise CaptureError("exit-malformed: desktop execution exit code is not an integer")
    if not isinstance(item.get("aggregated_output"), str):
        raise CaptureError("output-malformed: desktop execution output is not a string")
    if not isinstance(payload.get("turn_id"), str) or not payload.get("turn_id"):
        raise CaptureError("turn-missing: desktop execution has no turn id")
    if not _is_int(record["obj"].get("ordinal")):
        raise CaptureError("ordinal-missing: desktop execution has no ordinal")
    if not isinstance(record["obj"].get("timestamp"), str):
        raise CaptureError("timestamp-missing: desktop execution has no timestamp")
    return {"protocol": "desktop", "thread_id": thread_id, "turn_id": payload["turn_id"],
            "item_id": item_id, "ordinal": record["obj"]["ordinal"],
            "timestamp": record["obj"]["timestamp"], "argv": list(argv),
            "cwd_uri": item["cwd"], "status": item["status"],
            "exit_code": item["exit_code"], "output": item["aggregated_output"],
            "process_id": item.get("process_id"), "duration": item.get("duration"),
            "raw": record["raw"], "source": record["source"]}


def select_desktop_tool_call(records: List[Dict[str, Any]], *, call_id: str,
                             turn_id: str) -> Dict[str, Any]:
    """Select the exec tool call/output pair and link it to the execution turn.

    The call input script is captured for exact-equality pinning by the
    operator; it is never substring-matched.
    """
    calls = [r for r in records
             if r["obj"].get("type") == "response_item"
             and (r["obj"].get("payload") or {}).get("type") == "custom_tool_call"
             and (r["obj"].get("payload") or {}).get("call_id") == call_id]
    if not calls:
        raise CaptureError(f"missing-tool-call: no custom_tool_call {call_id!r}")
    if len(calls) > 1:
        raise CaptureError(f"duplicate-tool-call: {call_id!r} appears {len(calls)} times")
    outputs = [r for r in records
               if r["obj"].get("type") == "response_item"
               and (r["obj"].get("payload") or {}).get("type") == "custom_tool_call_output"
               and (r["obj"].get("payload") or {}).get("call_id") == call_id]
    if not outputs:
        raise CaptureError(f"missing-tool-output: no output for call {call_id!r}")
    if len(outputs) > 1:
        raise CaptureError(f"duplicate-tool-output: call {call_id!r} has {len(outputs)} outputs")
    call = calls[0]["obj"]["payload"]
    output = outputs[0]["obj"]["payload"]
    if call.get("name") != "exec":
        raise CaptureError(f"tool-not-exec: call {call_id!r} invokes {call.get('name')!r}")
    if call.get("status") != "completed":
        raise CaptureError(f"call-not-completed: call {call_id!r} status is {call.get('status')!r}")
    if not isinstance(call.get("input"), str):
        raise CaptureError("call-input-malformed: exec call input is not a string")
    if not isinstance(output.get("output"), list):
        raise CaptureError("call-output-malformed: exec call output is not a list")

    def _turn_of(payload: Dict[str, Any]) -> Optional[str]:
        meta = payload.get("internal_chat_message_metadata_passthrough") or {}
        return meta.get("turn_id") if isinstance(meta, dict) else None

    if _turn_of(call) != turn_id or _turn_of(output) != turn_id:
        raise CaptureError("call-turn-mismatch: call/output turn differs from the execution turn")
    start, end = calls[0]["obj"].get("ordinal"), outputs[0]["obj"].get("ordinal")
    if (not _is_int(start) or not _is_int(end) or start >= end
            or calls[0]["line_no"] >= outputs[0]["line_no"]
            or calls[0]["source"] != outputs[0]["source"]):
        raise CaptureError("call-window-invalid: call must precede its output in one segment")
    source = calls[0]["source"]
    for r in records:
        pay = r["obj"].get("payload", {})
        if (r["obj"].get("type") != "response_item" or pay.get("type") != "custom_tool_call"
                or pay.get("call_id") == call_id or _turn_of(pay) != turn_id):
            continue
        other_start = r["obj"].get("ordinal")
        other_ends = [x["obj"].get("ordinal") for x in records
                      if x["obj"].get("type") == "response_item"
                      and x["obj"]["payload"].get("type") == "custom_tool_call_output"
                      and x["obj"]["payload"].get("call_id") == pay.get("call_id")]
        if (not _is_int(other_start) or (other_start < end and
                (len(other_ends) != 1 or not _is_int(other_ends[0]) or other_ends[0] > start))):
            raise CaptureError("call-window-ambiguous: overlapping tool calls")
    executions = []
    for r in records:
        pay = r["obj"].get("payload", {})
        if (r["obj"].get("type") == "event_msg" and pay.get("type") == "item_completed"
                and pay.get("turn_id") == turn_id
                and pay.get("item", {}).get("type") == "CommandExecution"):
            ordinal = r["obj"].get("ordinal")
            if (r["source"] == source and _is_int(ordinal) and start < ordinal < end
                    and calls[0]["line_no"] < r["line_no"] < outputs[0]["line_no"]):
                executions.append((pay.get("thread_id"), pay["item"].get("id")))
    if len(executions) != 1:
        raise CaptureError("call-window-ambiguous: require exactly one completed execution")
    return {"call_id": call_id, "turn_id": turn_id, "tool_name": call["name"],
            "input": call["input"], "output": output["output"], "source": source,
            "execution_key": executions[0], "call_ordinal": start, "output_ordinal": end,
            "raw_call": calls[0]["raw"], "raw_output": outputs[0]["raw"]}


def select_cli_execution(records: List[Dict[str, Any]], *, thread_id: str,
                         item_id: str) -> Dict[str, Any]:
    """Select the single CLI command_execution item for the pinned ids.

    The CLI stream carries no cwd, argv array, timestamps, or durations;
    workdir binding is therefore impossible from this protocol alone (the
    verifier fails closed on it).
    """
    starts = [r["obj"].get("thread_id") for r in records if r["obj"].get("type") == "thread.started"]
    if not starts:
        raise CaptureError("thread-unverifiable: CLI stream has no thread.started envelope")
    if any(t != thread_id for t in starts):
        raise CaptureError("multi-thread-stream: CLI stream mixes thread ids")
    matches = []
    for record in records:
        obj = record["obj"]
        if obj.get("type") != "item.completed":
            continue
        item = obj.get("item") or {}
        if item.get("type") == "command_execution" and item.get("id") == item_id:
            matches.append((record, item))
    if not matches:
        raise CaptureError(f"missing-item: no command_execution {item_id!r}")
    if len(matches) > 1:
        raise CaptureError(f"duplicate-item: {item_id!r} appears {len(matches)} times")
    record, item = matches[0]
    if not isinstance(item.get("command"), str) or not item.get("command"):
        raise CaptureError("command-missing: CLI execution carries no command string")
    if not isinstance(item.get("aggregated_output"), str):
        raise CaptureError("output-malformed: CLI execution output is not a string")
    if item.get("status") != "completed":
        raise CaptureError(f"status-not-completed: CLI execution status is {item.get('status')!r}")
    if not _is_int(item.get("exit_code")):
        raise CaptureError("exit-unconfirmed: CLI execution exit code is not an integer")
    return {"protocol": "cli", "thread_id": thread_id, "item_id": item_id,
            "command": item["command"], "output": item["aggregated_output"],
            "status": item["status"], "exit_code": item["exit_code"],
            "raw": record["raw"], "source": record["source"]}


def _file_uri_to_path(uri: str) -> str:
    if not isinstance(uri, str) or not uri.startswith("file://"):
        raise CaptureError(f"cwd-unparseable: not a file URI: {uri!r}")
    parsed = urlparse(uri)
    if parsed.netloc or parsed.query or parsed.fragment:
        raise CaptureError("cwd-unparseable: only local file URIs without query/fragment are accepted")
    path = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:($|/)", path):
        path = path[1:]
    if not re.match(r"^[A-Za-z]:/", path) or "\x00" in path:
        raise CaptureError("cwd-unparseable: require an absolute Windows drive path")
    return path


def _norm_path(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(path))


def _require_expectations(*, expected_argv=None, expected_workdir=None, expected_exit=None,
                          expected_command=None) -> None:
    if expected_argv is not None and (
            not isinstance(expected_argv, list) or not expected_argv
            or any(not isinstance(t, str) or not t for t in expected_argv)):
        raise CaptureError("expectation-malformed: expected argv must be a non-empty string list")
    if expected_workdir is not None and (
            not isinstance(expected_workdir, str) or not expected_workdir):
        raise CaptureError("expectation-malformed: expected workdir must be a non-empty string")
    if expected_exit is not None and not _is_int(expected_exit):
        raise CaptureError("expectation-malformed: expected exit must be an integer")
    if expected_command is not None and (
            not isinstance(expected_command, str) or not expected_command):
        raise CaptureError("expectation-malformed: expected command must be a non-empty string")


def verify_desktop_binding(exec_record: Dict[str, Any], call_record: Dict[str, Any], *,
                           expected_argv: List[str], expected_workdir: str,
                           expected_exit: int, expected_marker: Optional[str] = None,
                           expected_call_input: Optional[str] = None) -> Dict[str, Any]:
    """Verify a desktop execution against pinned expectations. Exact equality only."""
    _require_expectations(expected_argv=expected_argv, expected_workdir=expected_workdir,
                          expected_exit=expected_exit)
    if (expected_argv is None or not isinstance(expected_workdir, str)
            or not re.match(r"^[A-Za-z]:[\\/]", expected_workdir)
            or expected_exit is None or not isinstance(expected_call_input, str)
            or not expected_call_input):
        raise CaptureError("expectation-malformed: absolute workdir and exact call script are mandatory")
    if (call_record.get("execution_key") != (exec_record["thread_id"], exec_record["item_id"])
            or call_record["turn_id"] != exec_record["turn_id"]
            or call_record["source"] != exec_record["source"]):
        raise CaptureError("call-execution-mismatch: execution is outside the selected call window")
    if list(exec_record["argv"]) != list(expected_argv):
        raise CaptureError("argv-mismatch: execution argv differs from the pinned invocation")
    if _norm_path(_file_uri_to_path(exec_record["cwd_uri"])) != _norm_path(expected_workdir):
        raise CaptureError("cwd-mismatch: execution workdir differs from the pinned workspace")
    if exec_record["exit_code"] != expected_exit:
        raise CaptureError(
            f"exit-mismatch: execution exit {exec_record['exit_code']} differs from {expected_exit}")
    if expected_marker and expected_marker not in exec_record["output"]:
        raise CaptureError(f"marker-absent: execution output lacks {expected_marker!r}")
    if len(exec_record["output"]) > MAX_OUTPUT_CHARS:
        raise CaptureError("output-too-large: execution output exceeds the capture bound")
    if expected_call_input is not None and call_record["input"] != expected_call_input:
        raise CaptureError("call-input-mismatch: tool call script differs from the pinned script")
    return {"protocol": "desktop", "thread_id": exec_record["thread_id"],
            "turn_id": exec_record["turn_id"], "item_id": exec_record["item_id"],
            "call_id": call_record["call_id"], "process_id": exec_record.get("process_id"),
            "ordinal": exec_record["ordinal"], "timestamp": exec_record["timestamp"],
            "argv": list(exec_record["argv"]), "workdir": _norm_path(expected_workdir),
            "exit_code": exec_record["exit_code"],
            "output_digest": sha256_hex(exec_record["output"].encode("utf-8")),
            "event_digest": sha256_hex(exec_record["raw"]),
            "call_digest": sha256_hex(call_record["raw_call"]),
            "call_output_digest": sha256_hex(call_record["raw_output"]),
            "association": "single-execution-nonoverlapping-call-window/v1"}


def verify_cli_binding(exec_record: Dict[str, Any], *, expected_command: str,
                       expected_exit: int,
                       expected_marker: Optional[str] = None) -> Dict[str, Any]:
    """Verify a CLI execution. Always fails closed: no workdir in this protocol.

    Command/exit/marker gates run first so each is independently testable;
    the workdir gate then refuses unconditionally with its own reason.
    """
    _require_expectations(expected_command=expected_command, expected_exit=expected_exit)
    if exec_record["command"] != expected_command:
        raise CaptureError("command-mismatch: execution command differs from the pinned string")
    if exec_record["exit_code"] != expected_exit:
        raise CaptureError(
            f"exit-mismatch: execution exit {exec_record['exit_code']} differs from {expected_exit}")
    if expected_marker and expected_marker not in exec_record["output"]:
        raise CaptureError(f"marker-absent: execution output lacks {expected_marker!r}")
    if len(exec_record["output"]) > MAX_OUTPUT_CHARS:
        raise CaptureError("output-too-large: execution output exceeds the capture bound")
    raise CaptureError("workdir-unattested: CLI stream carries no working directory; "
                       "refusing closed pending launcher-side cwd attestation")


def build_capture_manifest(*, task_id: str, delivery_id: str, artifact_claim: Dict[str, Any],
                           review_claim: Dict[str, Any], apply_attempt_id: str,
                           protocol: str, source_files: List[Dict[str, Any]],
                           bindings: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble the capture manifest. Claims stay labeled claims, never proof."""
    for name, value in (("task_id", task_id), ("delivery_id", delivery_id),
                        ("apply_attempt_id", apply_attempt_id)):
        if not isinstance(value, str) or not value:
            raise CaptureError(f"manifest-claim-malformed: {name} must be a non-empty string")
    for name, value in (("artifact_claim", artifact_claim), ("review_claim", review_claim)):
        if not isinstance(value, dict):
            raise CaptureError(f"manifest-claim-malformed: {name} must be an object")
    if protocol not in ("desktop", "cli"):
        raise CaptureError("manifest-malformed: unknown protocol")
    return {"manifest_version": "host-capture/1", "task_claim": task_id,
            "delivery_claim": delivery_id, "artifact_claim": artifact_claim,
            "review_claim": review_claim, "apply_attempt_id": apply_attempt_id,
            "protocol": protocol, "source_files": list(source_files),
            "event": dict(bindings),
            "notes": ("Claims require control-layer cross-checks. Digests prove "
                      "post-archive consistency only, never execution by themselves.")}


def verify_archive_consistency(manifest: Dict[str, Any], *, event_raw: bytes,
                               output: str) -> None:
    """Recompute digests over archived bytes; any drift fails the read."""
    try:
        event = manifest["event"]
        want_event = event["event_digest"]
        want_output = event["output_digest"]
    except (KeyError, TypeError) as exc:
        raise CaptureError(f"manifest-malformed: digest fields missing: {exc}") from None
    if sha256_hex(event_raw) != want_event:
        raise CaptureError("archive-tampered: event bytes differ from the manifest digest")
    if sha256_hex(output.encode("utf-8")) != want_output:
        raise CaptureError("archive-tampered: output bytes differ from the manifest digest")



def verify_capture_archive(manifest: Dict[str, Any], *, archive_raw: bytes) -> None:
    """Verify all sealed context bytes. This is consistency, not source authentication."""
    if (not isinstance(manifest, dict) or manifest.get("protocol") != "desktop"
            or not isinstance(manifest.get("event"), dict)):
        raise CaptureError("manifest-malformed: require a desktop capture object")
    event = manifest["event"]
    for key in ("thread_id", "turn_id", "item_id", "call_id", "workdir"):
        if not isinstance(event.get(key), str) or not event[key]:
            raise CaptureError(f"manifest-malformed: missing string {key}")
    if sha256_hex(archive_raw) != manifest.get("archive_digest"):
        raise CaptureError("archive-tampered: capture context digest differs")
    records = parse_jsonl(archive_raw, source_label="archive")
    event = manifest["event"]
    require_desktop_session_meta(records, event["thread_id"])
    selected = select_desktop_execution(records, thread_id=event["thread_id"], item_id=event["item_id"])
    call = select_desktop_tool_call(records, call_id=event["call_id"], turn_id=event["turn_id"])
    verify_archive_consistency(manifest, event_raw=selected["raw"], output=selected["output"])
    if (sha256_hex(call["raw_call"]) != event.get("call_digest")
            or sha256_hex(call["raw_output"]) != event.get("call_output_digest")):
        raise CaptureError("archive-tampered: call context differs")
    recomputed = verify_desktop_binding(
        selected, call, expected_argv=event.get("argv"), expected_workdir=event.get("workdir"),
        expected_exit=event.get("exit_code"), expected_call_input=call["input"])
    if recomputed != event:
        raise CaptureError("archive-tampered: manifest facts differ from raw context")
