"""Offline persistent binding between control deliveries and captured host records.

Register/read helpers (stdlib + repo modules only): bind one independently
captured desktop tool execution to one waiting code delivery, persist the
association atomically with uniqueness in both directions, and refuse swaps,
staleness, and tampering. Reads re-verify persisted bindings without any
side effects.

Trust design:
- Control facts (task/delivery/artifact/review/workspace/sealed check/
  expected bytes) are read from TaskStorage and sealed fixtures here, never
  copied from manifest claims or model payloads.
- The archive directory argument is UNTRUSTED INPUT: every byte read from it
  is re-parsed, re-derived, and byte-compared against the live session store
  before anything persists. Caller-passed JSON/digests/paths confer nothing.
- Fixed invocation, check seal, workspace and target hashes come from control
  facts. Operator re-assertions must equal that single supported plan.
- The live session root below is FIXED and mirrors the capture entry; no
  parameter substitutes it (a signature test pins the register surface).
- Only the fixed, control-generated Windows desktop wrapper is admitted.
  Arbitrary scripts, echo substitutes and old delivery invocations are refused.
- This module NEVER feeds complete_code_delivery and creates no task states.
  Production complete stays closed; bindings are evidence for review only.
"""

import json
import re
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.adapters import SchemaValidationError
from src.control_guards import validate_review
from src.artifacts import content_sha256
from src.code_delivery import (
    CODE_FIXTURE_ID,
    is_clean_pass_review,
    CODE_TEMPLATES,
    CodeDeliveryRefused,
    resolve_target_path,
    sealed_fixture_bytes,
    template_for_task,
    parse_fixture_patch,
    verify_production_history_clean,
)
from src.host_capture import (
    MAX_INPUT_BYTES,
    CaptureError,
    parse_jsonl,
    require_desktop_session_meta,
    select_desktop_execution,
    select_desktop_tool_call,
    sha256_hex,
    verify_capture_archive,
    verify_desktop_binding,
)


class BindingRefused(Exception):
    """A capture binding request was refused with no state change."""


# Fixed live session store. Mirrors scripts/probe_host_capture.py SESSION_ROOT;
# a regression test asserts the two stay identical. Never a parameter.
DESKTOP_SESSION_ROOT = Path.home() / ".codex" / "sessions"


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sealed_check_expectation() -> Dict[str, Any]:
    """Sealed post-apply check oracle for the fixed template (control-side facts)."""
    try:
        tpl = CODE_TEMPLATES[CODE_FIXTURE_ID]
        checks = json.loads(sealed_fixture_bytes(tpl, "checks_rel"))
        entries = checks.get("checks", []) if isinstance(checks, dict) else []
        entry = next((e for e in entries
                      if isinstance(e, dict) and e.get("name") == tpl["check_name"]), None)
        if entry is None or entry.get("template") is not True:
            raise ValueError("template entry missing")
        expect_exit = entry.get("expect_exit")
        marker = entry.get("expect_stdout_contains")
        if not isinstance(expect_exit, int) or isinstance(expect_exit, bool):
            raise ValueError("expect_exit malformed")
        if not isinstance(marker, str):
            raise ValueError("marker malformed")
        check_sha = sha256_hex(sealed_fixture_bytes(tpl, "check_rel"))
        return {"expect_exit": expect_exit, "marker": marker,
                "check_rel": f"{tpl['fixture_dir']}/{tpl['check_rel']}",
                "check_sha256": check_sha}
    except (KeyError, ValueError, CodeDeliveryRefused) as exc:
        raise BindingRefused(f"check-seal-failure: sealed check oracle unreadable: {exc}") from None


def _resolve_live_segments(thread_id: str) -> List[Path]:
    if not isinstance(thread_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", thread_id):
        raise BindingRefused(f"source-unresolvable: malformed thread id {thread_id!r}")
    try:
        root = DESKTOP_SESSION_ROOT.resolve(strict=True)
    except OSError as exc:
        raise BindingRefused(f"source-unresolvable: session store unavailable: {exc}") from None
    found = []
    for path in sorted(root.rglob(f"*{thread_id}*.jsonl")):
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise BindingRefused(f"source-unresolvable: live segment escapes the store: {exc}") from None
        if not resolved.is_file() or resolved.suffix != ".jsonl":
            raise BindingRefused("source-unresolvable: live segment is not a JSONL file")
        found.append(resolved)
    if not found:
        raise BindingRefused(f"source-unresolvable: no live segment for thread {thread_id!r}")
    return found


def _read_live_segments(thread_id: str) -> List[Dict[str, Any]]:
    segments = []
    for path in _resolve_live_segments(thread_id):
        try:
            with path.open("rb") as handle:
                raw = handle.read(MAX_INPUT_BYTES + 1)
            with path.open("rb") as handle:
                second = handle.read(MAX_INPUT_BYTES + 1)
            if raw != second:
                raise BindingRefused("source-unstable: live segment changed during read")
        except OSError as exc:
            raise BindingRefused(f"source-unreadable: live segment unreadable: {exc}") from None
        if len(raw) > MAX_INPUT_BYTES:
            raise BindingRefused("source-unreadable: live segment exceeds the capture bound")
        try:
            records = parse_jsonl(raw, source_label=str(path))
        except CaptureError as exc:
            raise BindingRefused(f"source-unreadable: live segment failed strict parse: {exc}") from None
        segments.append({"path": str(path), "records": records})
    return segments


def register_capture_binding(storage, task_id: str, *, delivery_id: str,
                             archive_dir, thread_id: str, item_id: str,
                             call_id: str, expected_argv: List[str],
                             expected_call_input: str) -> Dict[str, Any]:
    """Bind one live-captured desktop execution to one waiting delivery.

    All control facts come from storage and sealed fixtures; all capture
    facts are re-derived from archive bytes and byte-compared against the
    live session store. Refusals raise BindingRefused with no state change.
    Unknown tasks raise KeyError, matching storage conventions.
    """
    for name, value in (("task_id", task_id), ("delivery_id", delivery_id),
                        ("thread_id", thread_id), ("item_id", item_id),
                        ("call_id", call_id)):
        if not isinstance(value, str) or not value:
            raise BindingRefused(f"arg-malformed: {name} must be a non-empty string")
    if not isinstance(expected_argv, list) or not expected_argv or any(
            not isinstance(t, str) or not t for t in expected_argv):
        raise BindingRefused("arg-malformed: expected_argv must be a non-empty string list")
    if not isinstance(expected_call_input, str) or not expected_call_input:
        raise BindingRefused("arg-malformed: expected_call_input must be a non-empty string")
    archive_path = Path(archive_dir) if isinstance(archive_dir, (str, Path)) else None
    if archive_path is None or not archive_path.is_dir():
        raise BindingRefused(f"archive-missing: archive directory unavailable: {archive_dir!r}")
    storage.get_task(task_id)
    owner_id = uuid.uuid4().hex
    if not storage.claim_task(task_id, owner_id):
        raise BindingRefused("owner-held: task owner is held by another execution")
    try:
        with storage.transaction():
            return _register_locked(
                storage, task_id, delivery_id=delivery_id, archive_dir=archive_path,
                thread_id=thread_id, item_id=item_id, call_id=call_id,
                expected_argv=list(expected_argv), expected_call_input=expected_call_input)
    finally:
        storage.release_task(task_id, owner_id)


def _register_locked(storage, task_id: str, *, delivery_id: str, archive_dir: Path,
                     thread_id: str, item_id: str, call_id: str,
                     expected_argv: List[str], expected_call_input: str) -> Dict[str, Any]:
    try:
        return _bind_verified(
            storage, task_id, delivery_id=delivery_id, archive_dir=archive_dir,
            thread_id=thread_id, item_id=item_id, call_id=call_id,
            expected_argv=expected_argv, expected_call_input=expected_call_input)
    except (CaptureError, CodeDeliveryRefused, RecursionError) as exc:
        raise BindingRefused(str(exc)) from exc


def _control_snapshot(storage, task_id: str, delivery_id: str, *, check_target=True) -> Dict[str, Any]:
    task = storage.get_task(task_id)
    if task["status"] != "reviewing":
        raise BindingRefused(f"status-mismatch: binding requires reviewing, got {task['status']!r}")
    delivery = (task.get("metadata") or {}).get("_delivery")
    if not isinstance(delivery, dict) or delivery.get("state") != "waiting_host":
        raise BindingRefused("no-waiting-delivery: task has no prepared waiting delivery")
    if delivery.get("delivery_id") != delivery_id:
        raise BindingRefused("delivery-mismatch: request delivery is not the current waiting delivery")
    shape_ok = (
        isinstance(delivery.get("artifact"), dict)
        and isinstance(delivery.get("baseline"), dict)
        and isinstance(delivery.get("expected_after"), dict)
        and isinstance(delivery.get("review_attempt_id"), str)
        and isinstance(delivery.get("review_id"), str)
        and isinstance(delivery.get("review_sha256"), str)
    )
    if not shape_ok:
        raise BindingRefused("delivery-malformed: waiting delivery record is malformed")
    try:
        tpl = template_for_task(task)
    except CodeDeliveryRefused as exc:
        raise BindingRefused(f"template-mismatch: {exc}") from None
    stamped = (task.get("metadata") or {}).get("_trusted") or {}
    if stamped.get("template_id") != tpl["template_id"]:
        raise BindingRefused("template-mismatch: task lacks a matching trusted template stamp")
    if stamped.get("source") != "production":
        raise BindingRefused("source-not-production: only production tasks bind real captures")
    latest = storage.get_latest_artifact(task_id)
    pinned = delivery.get("artifact") or {}
    if (latest is None or latest["version"] != pinned.get("version")
            or latest["artifact_id"] != pinned.get("artifact_id")
            or latest["sha256"] != pinned.get("sha256")
            or latest.get("generation_attempt_id") != delivery.get("generation_attempt_id")):
        raise BindingRefused("artifact-changed: current artifact differs from the prepared version")
    attempts = [a for a in storage.list_model_attempts(task_id)
                if a.get("actor_role") == "reviewer" and a.get("status") == "completed"]
    review = attempts[-1].get("response") or {} if attempts else {}
    if (not attempts or attempts[-1]["attempt_id"] != delivery.get("review_attempt_id")
            or review.get("review_id") != delivery.get("review_id")
            or content_sha256(json.dumps(review, sort_keys=True)) != delivery.get("review_sha256")
            or storage.get_control(task_id).get("active_attempt_id") != delivery.get("review_attempt_id")):
        raise BindingRefused("review-stale: current review differs from the prepared review")
    if (list(task.get("acceptance_criteria") or []) != tpl["criteria"]
            or (task.get("context") or {}).get("target_files") != [tpl["authorized_target"]]):
        raise BindingRefused("template-mismatch: acceptance or authorized targets changed")
    if verify_production_history_clean(storage, task_id) is not None:
        raise BindingRefused("source-not-production: model history is missing confirmed real provenance")
    control = storage.get_control(task_id)
    runtime = task["_runtime"]
    if (control.get("pending_json") or storage.has_uncertain_attempt(task_id)
            or runtime.get("tokens_unknown_count") or runtime.get("has_unconfirmed_side_effect")):
        raise BindingRefused("control-blocked: approval, usage or execution result is unresolved")
    target_ref = f"mem://{task_id}/{latest['artifact_id']}"
    snapshots = {target_ref: {"content": latest["content"], "sha256": latest["sha256"]}}
    if (latest.get("artifact_type") != "patch_text" or task.get("artifact_refs") != [target_ref]
            or content_sha256(latest["content"]) != latest["sha256"]
            or not is_clean_pass_review(review)
            or attempts[-1].get("context", {}).get("artifact_hashes") != {target_ref: latest["sha256"]}):
        raise BindingRefused("review-stale: no valid pass covering the current patch")
    try:
        validate_review(review, {"task_id": task_id, "review_id": review["review_id"],
            "target_artifact_path": target_ref, "reviewer_role": review["reviewer_role"]},
            snapshots, snapshots)
        patch_result = parse_fixture_patch(latest["content"], tpl["authorized_target"])
    except (SchemaValidationError, CodeDeliveryRefused, KeyError, TypeError) as exc:
        raise BindingRefused(f"review-stale: current patch/review correlation invalid: {exc}") from None
    if (patch_result["content_bytes"] != sealed_fixture_bytes(tpl, "reference_rel")
            or delivery["baseline"] != {"path": tpl["authorized_target"],
                 "sha256_before": sha256_hex(sealed_fixture_bytes(tpl, "baseline_rel"))}
            or delivery["expected_after"] != {"path": tpl["authorized_target"],
                 "sha256": sha256_hex(patch_result["content_bytes"])}
            or delivery.get("authorized_files") != [tpl["authorized_target"]]):
        raise BindingRefused("delivery-malformed: before/after/scope differs from the reviewed sealed patch")
    expectation = sealed_check_expectation()
    ws = (task.get("context") or {}).get("workspace_root")
    if not isinstance(ws, str) or not ws:
        raise BindingRefused("snapshot-malformed: task has no trusted workspace root")
    try:
        target_abs = resolve_target_path(ws, tpl["authorized_target"])
    except CodeDeliveryRefused as exc:
        raise BindingRefused(f"snapshot-malformed: {exc}") from None
    try:
        actual_sha = sha256_hex(Path(target_abs).read_bytes())
    except OSError as exc:
        raise BindingRefused(f"target-unreadable: cannot read authorized target: {exc}") from None
    if check_target and actual_sha != delivery["expected_after"]["sha256"]:
        raise BindingRefused("target-bytes-mismatch: workspace target differs from expected_after")
    return {"task_id": task_id, "status": task["status"], "delivery": delivery,
            "workspace_root": ws, "authorized_target": tpl["authorized_target"],
            "artifact": {"version": latest["version"], "artifact_id": latest["artifact_id"],
                         "sha256": latest["sha256"],
                         "generation_attempt_id": latest.get("generation_attempt_id")},
            "review": {"attempt_id": attempts[-1]["attempt_id"],
                       "review_id": review.get("review_id"),
                       "sha256": content_sha256(json.dumps(review, sort_keys=True))},
            "check": expectation, "observed_target_sha256": actual_sha,
            "snapshot_at": _utc_now_iso()}


def _invocation_for_snapshot(snapshot):
    """One frozen local Windows invocation. No arbitrary shell/JS validation."""
    delivery = snapshot["delivery"]
    context = {key: snapshot[key] for key in ("task_id", "workspace_root", "artifact", "review", "check")}
    context["delivery"] = delivery
    context_hash = sha256_hex(json.dumps(context, ensure_ascii=False, sort_keys=True).encode("utf8"))
    target = resolve_target_path(snapshot["workspace_root"], snapshot["authorized_target"])
    checker = str((Path(__file__).resolve().parents[1] / snapshot["check"]["check_rel"]).resolve())
    # Execute exactly the bytes whose seal was checked. Verify target bytes before
    # and after the check; no claim of exclusion against noncooperating writers.
    program = "\n".join([
        "import hashlib,sys",
        "from pathlib import Path",
        f"# JH delivery context {context_hash}",
        f"target=Path({target!r})",
        f"checker=Path({checker!r})",
        f"expected={delivery['expected_after']['sha256']!r}",
        "def require_target():",
        "    if hashlib.sha256(target.read_bytes()).hexdigest()!=expected: raise SystemExit(3)",
        "require_target()",
        "raw=checker.read_bytes()",
        f"if hashlib.sha256(raw).hexdigest()!={snapshot['check']['check_sha256']!r}: raise SystemExit(4)",
        "sys.argv=[str(checker),'--target',str(target)]",
        "try:",
        "    exec(compile(raw,str(checker),'exec'),{'__name__':'__main__','__file__':str(checker)})",
        "except SystemExit as result:",
        "    if result.code not in (None,0): raise",
        "require_target()",
    ])
    quote = lambda value: "'" + value.replace("'", "''") + "'"
    command = "& " + " ".join(quote(t) for t in [str(Path(sys.executable).resolve()),
              "-X", "utf8", "-B", "-c", program]) + "\nexit $LASTEXITCODE"
    # This wrapper was measured in the actual desktop runtime. Other wrappers
    # must be separately verified; they are not inferred from caller expectations.
    shell = str(Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe")
    call = "text(await tools.exec_command(" + json.dumps({"cmd": command,
        "workdir": snapshot["workspace_root"], "max_output_tokens": 1000},
        ensure_ascii=False, separators=(",", ":")) + "));\n"
    return {"argv": [shell, "-Command", command], "call_input": call,
            "apply_attempt_id": "capture-check-" + context_hash, "context_sha256": context_hash}


def capture_invocation_for_delivery(storage, task_id, delivery_id):
    """Read-only plan for the host, before or after host application.

    Planning does not verify applied bytes or register any evidence. Registration
    always calls the post-apply snapshot gate; no public bypass parameter exists.
    """
    return _invocation_for_snapshot(_control_snapshot(storage, task_id, delivery_id, check_target=False))


def _read_archive(archive_dir: Path):
    try:
        with (archive_dir / "events.jsonl").open("rb") as handle:
            events_raw = handle.read(MAX_INPUT_BYTES + 1)
        with (archive_dir / "manifest.json").open("rb") as handle:
            manifest_raw = handle.read(MAX_INPUT_BYTES + 1)
        if max(len(events_raw), len(manifest_raw)) > MAX_INPUT_BYTES:
            raise BindingRefused("archive-too-large: archive exceeds read bound")
    except OSError as exc:
        raise BindingRefused(f"archive-missing: capture archive unreadable: {exc}") from None
    try:
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise BindingRefused(f"manifest-malformed: manifest is not JSON: {exc}") from None
    if not isinstance(manifest, dict):
        raise BindingRefused("manifest-malformed: manifest is not an object")
    if manifest.get("protocol") != "desktop":
        raise BindingRefused("protocol-not-admitted: only desktop captures bind")
    return events_raw, manifest_raw, manifest


def _verify_archive_self_consistent(manifest: Dict[str, Any], events_raw: bytes):
    verify_capture_archive(manifest, archive_raw=events_raw)
    records = parse_jsonl(events_raw, source_label="archive")
    event = manifest["event"]
    meta = require_desktop_session_meta(records, event["thread_id"])
    selected = select_desktop_execution(records, thread_id=event["thread_id"],
                                        item_id=event["item_id"])
    call = select_desktop_tool_call(records, call_id=event["call_id"], turn_id=event["turn_id"])
    return meta, selected, call


def _verify_liveness(thread_id: str, item_id: str, call_id: str,
                     archived_lines: List[bytes]) -> Dict[str, str]:
    owners = []
    for segment in _read_live_segments(thread_id):
        records = segment["records"]
        try:
            meta = require_desktop_session_meta(records, thread_id)
            selected = select_desktop_execution(records, thread_id=thread_id,
                                                item_id=item_id)
            call = select_desktop_tool_call(records, call_id=call_id,
                                            turn_id=selected["turn_id"])
        except CaptureError:
            continue
        owners.append((segment, meta, selected, call))
    if not owners:
        raise BindingRefused("source-unresolvable: live store lacks the captured selection")
    if len(owners) > 1:
        raise BindingRefused("source-ambiguous: captured selection appears in several live segments")
    _, live_meta, live_selected, live_call = owners[0]
    live_lines = {live_meta["raw"], live_selected["raw"], live_call["raw_call"],
                  live_call["raw_output"]}
    for raw in archived_lines:
        if raw not in live_lines:
            raise BindingRefused("archive-live-mismatch: archived bytes differ from the live store")
    return {"turn_id": live_selected["turn_id"]}


def _bind_verified(storage, task_id: str, *, delivery_id: str, archive_dir: Path,
                   thread_id: str, item_id: str, call_id: str,
                   expected_argv: List[str], expected_call_input: str) -> Dict[str, Any]:
    snapshot = _control_snapshot(storage, task_id, delivery_id)
    delivery = snapshot["delivery"]
    events_raw, manifest_raw, manifest = _read_archive(archive_dir)
    if manifest.get("task_claim") != task_id or manifest.get("delivery_claim") != delivery_id:
        raise BindingRefused("claim-identity-mismatch: manifest task/delivery claims differ from the request")
    art_claim = manifest.get("artifact_claim")
    if not isinstance(art_claim, dict):
        raise BindingRefused("claim-artifact-mismatch: artifact claim must be an object")
    pinned = delivery.get("artifact") or {}
    if (type(art_claim.get("version")) is not int
            or art_claim.get("version") != pinned.get("version")
            or art_claim.get("artifact_id") != pinned.get("artifact_id")
            or art_claim.get("sha256") != pinned.get("sha256")):
        raise BindingRefused("claim-artifact-mismatch: manifest artifact claim differs from the prepared version")
    rev_claim = manifest.get("review_claim")
    if not isinstance(rev_claim, dict):
        raise BindingRefused("claim-review-mismatch: review claim must be an object")
    if rev_claim.get("review_id") != delivery.get("review_id"):
        raise BindingRefused("claim-review-mismatch: manifest review claim differs from the prepared review")
    apply_attempt = manifest.get("apply_attempt_id")
    if not isinstance(apply_attempt, str) or not apply_attempt:
        raise BindingRefused("manifest-claim-malformed: apply_attempt_id must be a non-empty string")
    meta, selected, call = _verify_archive_self_consistent(manifest, events_raw)
    if (selected["thread_id"], selected["item_id"], call["call_id"]) != (thread_id, item_id, call_id):
        raise BindingRefused("claim-identity-mismatch: archived selection differs from the request")
    archived_lines = [meta["raw"], selected["raw"], call["raw_call"], call["raw_output"]]
    _verify_liveness(thread_id, item_id, call_id, archived_lines)
    bindings = verify_desktop_binding(
        selected, call, expected_argv=expected_argv,
        expected_workdir=snapshot["workspace_root"],
        expected_exit=snapshot["check"]["expect_exit"],
        expected_marker=snapshot["check"]["marker"] or None,
        expected_call_input=expected_call_input)
    invocation = _invocation_for_snapshot(snapshot)
    if expected_argv != invocation["argv"] or expected_call_input != invocation["call_input"]:
        raise BindingRefused("invocation-unverified: caller pin differs from the control-owned fixed check")
    if apply_attempt != invocation["apply_attempt_id"]:
        raise BindingRefused("apply-attempt-mismatch: capture does not identify the current delivery check")
    event_digest = sha256_hex(events_raw)
    manifest_digest = sha256_hex(manifest_raw)
    existing = storage.get_capture_binding(task_id, delivery_id)
    if existing is not None:
        if (existing["thread_id"], existing["item_id"], existing["call_id"],
                existing["event_digest"], existing["manifest_digest"]) == (
                thread_id, item_id, call_id, event_digest, manifest_digest):
            return existing
        raise BindingRefused("binding-conflict: delivery already bound to a different capture")
    if storage.get_capture_binding_by_call(thread_id, item_id, call_id) is not None:
        raise BindingRefused("binding-conflict: capture call already bound to another delivery")
    final_snapshot = _control_snapshot(storage, task_id, delivery_id)
    if {k:v for k,v in snapshot.items() if k != "snapshot_at"} != {
            k:v for k,v in final_snapshot.items() if k != "snapshot_at"}:
        raise BindingRefused("control-snapshot-changed: facts drifted during source verification")
    binding = {
        "binding_id": f"cb_{uuid.uuid4().hex}",
        "task_id": task_id,
        "delivery_id": delivery_id,
        "thread_id": thread_id,
        "item_id": item_id,
        "call_id": call_id,
        "protocol": "desktop",
        "snapshot_json": json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
        "capture_json": json.dumps(bindings, ensure_ascii=False, sort_keys=True),
        "manifest_json": manifest_raw.decode("utf-8"),
        "archive_dir": str(archive_dir),
        "event_digest": event_digest,
        "manifest_digest": manifest_digest,
        "apply_attempt_id": apply_attempt,
        "recorded_at": _utc_now_iso(),
        "review_items_json": json.dumps(
            [],
            ensure_ascii=False),
    }
    try:
        row = storage.save_capture_binding(binding)
    except ValueError as exc:
        raise BindingRefused(f"binding-conflict: {exc}") from None
    storage.record_event(
        task_id=task_id, event_type="capture_binding_registered",
        from_state="reviewing", to_state="reviewing", actor_role="orchestrator",
        details={"binding_id": row["binding_id"], "delivery_id": delivery_id,
                 "thread_id": thread_id, "item_id": item_id, "call_id": call_id,
                 "event_digest": event_digest, "manifest_digest": manifest_digest,
                 "invoked_by": "operator-capture-binding"})
    return row


def _read_rows(storage, reader):
    """Read binding rows, tolerating only a pre-feature database (no table yet)."""
    try:
        return reader()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None
        raise


def get_capture_binding(storage, task_id: str, delivery_id: str) -> Optional[Dict[str, Any]]:
    """Read a persisted binding. Side-effect free; safe on read-only handles."""
    return _read_rows(storage, lambda: storage.get_capture_binding(task_id, delivery_id))


def get_capture_binding_by_call(storage, thread_id: str, item_id: str,
                                call_id: str) -> Optional[Dict[str, Any]]:
    """Read a persisted binding by capture call identity. Side-effect free."""
    return _read_rows(
        storage, lambda: storage.get_capture_binding_by_call(thread_id, item_id, call_id))


def verify_binding_current(storage, task_id: str, delivery_id: str) -> Dict[str, Any]:
    """Re-check a stored binding against current control state and archive bytes.

    No writes, no live-store reads. Liveness was established at registration;
    this re-verifies staleness and post-register tampering only.
    """
    row = get_capture_binding(storage, task_id, delivery_id)
    if row is None:
        return {"current": False, "reasons": ["no-binding"], "binding": None}
    reasons = []
    try:
        snapshot = json.loads(row["snapshot_json"])
        current = _control_snapshot(storage, task_id, delivery_id)
        def stable(value):
            return {key: item for key, item in value.items() if key != "snapshot_at"}
        if stable(snapshot) != stable(current):
            reasons.append("control-snapshot-changed")
        events_raw, manifest_raw, manifest = _read_archive(Path(row["archive_dir"]))
        if sha256_hex(events_raw) != row["event_digest"] or sha256_hex(manifest_raw) != row["manifest_digest"]:
            reasons.append("archive-tampered")
        else:
            verify_capture_archive(manifest, archive_raw=events_raw)
        if json.loads(row["review_items_json"]):
            reasons.append("invocation-unverified")
    except (BindingRefused, CaptureError, CodeDeliveryRefused, KeyError, TypeError, ValueError) as exc:
        reason = str(exc).split(":", 1)[0]
        # Preserve the public coarse stale categories used by earlier controls.
        reason = {"status-mismatch": "task-not-waiting", "review-stale": "review-changed",
                  "delivery-mismatch": "delivery-changed"}.get(reason, reason)
        reasons.append(reason)
    return {"current": not reasons, "reasons": reasons, "binding": row}
