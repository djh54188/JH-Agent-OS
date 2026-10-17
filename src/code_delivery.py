"""Two-step code delivery for the fixed N2 fixture template (JH-006).

Pure control helpers (stdlib only): template registry, patch-envelope parsing,
production-history verification, and apply-receipt validation. Orchestration
flow lives in Orchestrator (prepare/complete methods); CLI wiring in src/cli.py.

Nothing here touches user files, spawns processes, or calls models. Fixture
paths resolve against this checkout; sealed reference bytes come from the
committed fixture files whose manifest seal is verified separately.
"""

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class CodeDeliveryRefused(Exception):
    """A prepare/complete request was refused with no state change."""


class CodeDeliveryUncertain(Exception):
    """Post-apply state cannot be established; needs operator reconciliation."""


CODE_FIXTURE_ID = "n2-code-sortfix/v1"
CODE_TEMPLATE_ID = "code_sortfix_v1"

# Frozen per-fixture template definition. Only fixtures listed here can use
# the code delivery path; anything else is refused (no generic patch platform).
CODE_TEMPLATES = {
    "n2-code-sortfix/v1": {
        "template_id": CODE_TEMPLATE_ID,
        "authorized_target": "target/widget.py",
        "criteria": [
            "Independent review of the patch passes with no unresolved blocking issues",
            "Post-apply widget checks pass in the host workspace",
            "Only authorized files changed and the target baseline matched at apply time",
        ],
        "fixture_dir": "tests/fixtures/n2/code_sortfix",
        "reference_rel": "expected/widget.py",
        "baseline_rel": "target/widget.py",
        "check_rel": "check_widget.py",
        "checks_rel": "checks.json",
        "check_name": "post-apply-target-check",
    },
}

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

RECEIPT_REQUIRED_KEYS = {"task_id", "delivery_id", "target", "baseline_seen",
                         "tests", "uncertain", "recorder", "recorded_at",
                         "host_call_id", "apply_attempt_id"}
RECEIPT_TARGET_KEYS = {"path", "sha256"}
RECEIPT_TESTS_KEYS = {"target_path", "target_sha256", "check_script",
                      "check_script_sha256", "command", "exit_code", "output_digest"}

WAITING_STATE = "waiting_host"
APPLIED_STATE = "applied"

# The only apply-receipt assembler recognized in the JH-006 offline batch.
# Production host assembly arrives after the offline independent review;
# unknown recorders are refused, never trusted as proof.
OFFLINE_FIXTURE_RECORDER = "fixture-offline-host/v1"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def is_code_delivery_task(task: Dict[str, Any]) -> bool:
    meta = task.get("metadata") or {}
    return isinstance(meta, dict) and meta.get("fixture_id") in CODE_TEMPLATES


def template_for_task(task: Dict[str, Any]) -> Dict[str, Any]:
    fixture_id = (task.get("metadata") or {}).get("fixture_id")
    try:
        return CODE_TEMPLATES[fixture_id]
    except KeyError:
        raise CodeDeliveryRefused(f"unknown code fixture: {fixture_id!r}") from None


def delivery_state(task: Dict[str, Any]):
    delivery = (task.get("metadata") or {}).get("_delivery")
    if not isinstance(delivery, dict):
        return None
    return delivery.get("state")


def parse_fixture_patch(content: Any, authorized_target: str) -> Dict[str, Any]:
    """Parse the frozen single-file patch envelope.

    Format: exactly {"path": str, "content": non-empty str}. Unknown shapes,
    multi-file payloads, and out-of-scope paths are refused. Never guesses.
    Returns {"path", "content_bytes"}.
    """
    if not isinstance(content, str):
        raise CodeDeliveryRefused("patch artifact content must be a string")
    try:
        envelope = json.loads(content)
    except ValueError as exc:
        raise CodeDeliveryRefused(f"patch envelope is not JSON: {exc}") from None
    if not isinstance(envelope, dict) or set(envelope) != {"path", "content"}:
        raise CodeDeliveryRefused("patch envelope must be exactly {path, content}")
    if envelope["path"] != authorized_target:
        raise CodeDeliveryRefused(
            f"patch target {envelope['path']!r} is outside the authorized target {authorized_target!r}")
    if not isinstance(envelope["content"], str) or not envelope["content"]:
        raise CodeDeliveryRefused("patch content must be a non-empty string")
    return {"path": envelope["path"], "content_bytes": envelope["content"].encode("utf-8")}


def verify_production_history_clean(storage, task_id: str) -> Optional[str]:
    """Refusal reason unless every completed call has a matching False source record.

    Baselines on persisted model_attempts: every COMPLETED call needs a source
    record for this task, this attempt_id, and the matching actor role, with
    is_mock strictly False. Missing, mis-associated, contradictory, or
    unconfirmable coverage refuses. No completed calls -> clean (None).
    """
    attempts = [a for a in storage.list_model_attempts(task_id) if a.get("status") == "completed"]
    if not attempts:
        return None
    sources = [e.get("details") or {}
               for e in storage.list_events(task_id)
               if e.get("event_type") == "model_response_source"]
    for attempt in attempts:
        aid = attempt.get("attempt_id")
        role = attempt.get("actor_role")
        matching = [d for d in sources
                    if d.get("attempt_id") == aid and d.get("role") == role]
        if not matching:
            return (f"completed {role} call has no trusted source record "
                    f"(attempt {aid}); re-submit or reconcile manually")
        if any(d.get("is_mock") is not False for d in matching):
            return (f"recorded {role} call has mock/unconfirmed "
                    f"source (attempt {aid})")
    return None


def resolve_target_path(workspace_root, rel_path: str) -> str:
    """Resolve an authorized relative target under a workspace root.

    Refuses absolute paths, drive letters, and anything escaping the root
    (symlink-aware via realpath). Read-only; callers decide what to do.
    """
    if not isinstance(rel_path, str) or not rel_path:
        raise CodeDeliveryRefused(f"invalid target path: {rel_path!r}")
    if rel_path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", rel_path):
        raise CodeDeliveryRefused(f"target path must be workspace-relative: {rel_path!r}")
    ws_norm = os.path.normcase(os.path.realpath(str(workspace_root)))
    candidate = os.path.normcase(os.path.realpath(os.path.join(str(workspace_root), rel_path)))
    if candidate != ws_norm and not candidate.startswith(ws_norm + os.sep):
        raise CodeDeliveryRefused(f"target path escapes workspace: {rel_path!r}")
    return candidate


def _is_hex64(value: Any) -> bool:
    return isinstance(value, str) and _HEX64.match(value) is not None


def sealed_fixture_bytes(template: Dict[str, Any], rel_key: str) -> bytes:
    """Read fixture bytes only when the sealed manifest covers them.

    The manifest must exist, be sealed, list the file, and match its bytes.
    Anything else refuses loudly instead of trusting unchecked bytes.
    """
    rel = template[rel_key]
    fixture_dir = _repo_root() / template["fixture_dir"]
    try:
        manifest = json.loads((fixture_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CodeDeliveryRefused(f"fixture manifest unreadable: {exc}") from None
    if not isinstance(manifest, dict) or manifest.get("sealed") is not True:
        raise CodeDeliveryRefused("fixture manifest is not sealed")
    recorded = next((e.get("sha256") for e in manifest.get("files", [])
                     if isinstance(e, dict) and e.get("path") == rel), None)
    try:
        data = (fixture_dir / rel).read_bytes()
    except OSError as exc:
        raise CodeDeliveryRefused(f"sealed fixture file unreadable: {rel}: {exc}") from None
    if hashlib.sha256(data).hexdigest() != recorded:
        raise CodeDeliveryRefused(f"sealed fixture file changed on disk: {rel}")
    return data


def materialized_post_apply_command(template: Dict[str, Any], workspace_root) -> List[str]:
    """Materialize the sealed post-apply check template for a workspace.

    Reads the fixture checks.json, finds the template entry by name, and
    substitutes {HOST_WORKSPACE} with the trusted workspace root. The result
    is the ONLY command a valid apply receipt may claim to have run.
    """
    try:
        checks = json.loads(sealed_fixture_bytes(template, "checks_rel"))
    except (OSError, ValueError) as exc:
        raise CodeDeliveryRefused(f"fixture checks unreadable: {exc}") from None
    entries = checks.get("checks", []) if isinstance(checks, dict) else []
    entry = next((e for e in entries
                  if isinstance(e, dict) and e.get("name") == template["check_name"]), None)
    if entry is None or entry.get("template") is not True:
        raise CodeDeliveryRefused("sealed post-apply check template missing")
    command = []
    for token in entry.get("command", []):
        if not isinstance(token, str):
            raise CodeDeliveryRefused("post-apply template command malformed")
        command.append(token.replace("{HOST_WORKSPACE}", str(workspace_root)))
    if any("{" in t or "}" in t for t in command):
        raise CodeDeliveryRefused("post-apply template has undeclared placeholders")
    joined = " ".join(command)
    if "expected/" in joined or "expected\\" in joined:
        raise CodeDeliveryRefused("post-apply template must target the applied file")
    return command


def sealed_post_apply_expectation(template: Dict[str, Any]) -> Dict[str, Any]:
    """The sealed assertion a registered post-apply raw output must satisfy.

    Read from the same sealed checks.json template the fixed command comes
    from: the expected exit code and the stdout marker the host output must
    contain. 'complete' checks the logged raw output against this; the control
    layer never re-runs the host command itself.
    """
    try:
        sealed_checks = json.loads(sealed_fixture_bytes(template, "checks_rel"))
    except (OSError, ValueError) as exc:
        raise CodeDeliveryRefused(f"fixture checks unreadable: {exc}") from None
    entries = sealed_checks.get("checks", []) if isinstance(sealed_checks, dict) else []
    entry = next((e for e in entries
                  if isinstance(e, dict) and e.get("name") == template["check_name"]), None)
    if entry is None or entry.get("template") is not True:
        raise CodeDeliveryRefused("sealed post-apply check template missing")
    expect_exit = entry.get("expect_exit")
    if isinstance(expect_exit, bool) or not isinstance(expect_exit, int):
        raise CodeDeliveryRefused("sealed check template lacks an integer exit expectation")
    expect_stdout = entry.get("expect_stdout_contains")
    if not isinstance(expect_stdout, str) or not expect_stdout:
        raise CodeDeliveryRefused("sealed check template lacks a stdout assertion")
    return {"expect_exit": expect_exit, "expect_stdout_contains": expect_stdout}


def registered_host_raw_output(storage, task_id: str, receipt: Dict[str, Any]) -> Optional[str]:
    """Raw host output registered for a receipt, or None when it cannot be read.

    Called only after fixture_host_evidence_matches accepted the same receipt,
    so the single matching record and its string output are known to exist.
    """
    records = [e for e in storage.list_events(task_id)
               if e.get("event_type") == "fixture_host_record"
               and e.get("event_id") == receipt.get("host_call_id")
               and e.get("actor_role") == "fixture_host"]
    if len(records) != 1:
        return None
    output = (records[0].get("details") or {}).get("raw_output")
    return output if isinstance(output, str) else None


def registered_output_satisfies_sealing(raw_output: Any, expectation: Dict[str, Any]) -> bool:
    """Whether a registered raw host output carries the sealed stdout assertion."""
    marker = expectation.get("expect_stdout_contains")
    return (isinstance(raw_output, str) and isinstance(marker, str)
            and bool(marker) and marker in raw_output)


def validate_apply_receipt(receipt: Dict[str, Any], *, task_id: str, delivery_id: str,
                           authorized_target: str, expected_command) -> Dict[str, Any]:
    """Validate receipt shape + ids + fixed command. Never mutates, never trusts.

    Returns the receipt unchanged on success. Raises CodeDeliveryRefused on any
    malformed or mismatched field. Attested values (hashes, exit codes) are
    checked for shape here; their truth is verified against fresh reads by the
    caller, never assumed from the receipt itself.
    """
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_REQUIRED_KEYS:
        raise CodeDeliveryRefused("apply receipt must contain exactly the required keys")
    if receipt["task_id"] != task_id or receipt["delivery_id"] != delivery_id:
        raise CodeDeliveryRefused("apply receipt task/delivery mismatch")
    target = receipt["target"]
    if not isinstance(target, dict) or set(target) != RECEIPT_TARGET_KEYS:
        raise CodeDeliveryRefused("apply receipt target block malformed")
    if target["path"] != authorized_target:
        raise CodeDeliveryRefused(
            f"apply receipt target {target['path']!r} is outside {authorized_target!r}")
    if not _is_hex64(target["sha256"]):
        raise CodeDeliveryRefused("apply receipt target sha256 malformed")
    if not _is_hex64(receipt["baseline_seen"]):
        raise CodeDeliveryRefused("apply receipt baseline hash malformed")
    tests = receipt["tests"]
    if not isinstance(tests, dict) or set(tests) != RECEIPT_TESTS_KEYS:
        raise CodeDeliveryRefused("apply receipt tests block malformed")
    if tests["target_path"] != authorized_target:
        raise CodeDeliveryRefused("apply receipt test target mismatch")
    for key in ("target_sha256", "check_script_sha256", "output_digest"):
        if not _is_hex64(tests[key]):
            raise CodeDeliveryRefused(f"apply receipt {key} malformed")
    if not isinstance(tests["check_script"], str) or not tests["check_script"]:
        raise CodeDeliveryRefused("apply receipt check script malformed")
    if (not isinstance(tests["command"], list)
            or any(not isinstance(t, str) for t in tests["command"])
            or tests["command"] != list(expected_command)):
        raise CodeDeliveryRefused("apply receipt command differs from the fixed post-apply command")
    if not isinstance(tests["exit_code"], int) or isinstance(tests["exit_code"], bool):
        raise CodeDeliveryRefused("apply receipt exit code malformed")
    if type(receipt["uncertain"]) is not bool:
        raise CodeDeliveryRefused("apply receipt uncertain flag malformed")
    if receipt["recorder"] != OFFLINE_FIXTURE_RECORDER:
        raise CodeDeliveryRefused(
            f"unknown apply receipt assembler {receipt.get('recorder')!r}; "
            f"only {OFFLINE_FIXTURE_RECORDER!r} is recognized in the offline batch")
    if not isinstance(receipt["recorded_at"], str) or not receipt["recorded_at"]:
        raise CodeDeliveryRefused("apply receipt recorded_at malformed")
    for key in ("host_call_id", "apply_attempt_id"):
        if not isinstance(receipt[key], str) or not receipt[key]:
            raise CodeDeliveryRefused(f"apply receipt {key} missing")
    return receipt


def record_fixture_host_evidence(storage, task_id, receipt, raw_output):
    """Trusted test-harness seam, deliberately absent from the production CLI.

    Registers synthetic fixture records only. It runs no check or application;
    the caller is the explicit offline fixture harness, never model text.
    The complete CLI can reference an existing record but cannot create one.
    """
    with storage.transaction():
        task = storage.get_task(task_id)
        stamp = (task.get("metadata") or {}).get("_trusted") or {}
        if (stamp.get("source") != "fixture" or task["status"] != "reviewing"
                or delivery_state(task) != WAITING_STATE):
            raise CodeDeliveryRefused("fixture host records require an already prepared fixture task")
        if not isinstance(receipt, dict) or not isinstance(raw_output, str):
            raise CodeDeliveryRefused("fixture host record requires a receipt and raw output")
        record = json.loads(json.dumps(receipt))
        record["host_call_id"] = "host_" + uuid.uuid4().hex
        record["apply_attempt_id"] = "apply_" + uuid.uuid4().hex
        storage.record_event(
            task_id=task_id, event_id=record["host_call_id"], event_type="fixture_host_record",
            from_state=task["status"], to_state=task["status"], actor_role="fixture_host",
            details={"evidence_kind": "offline_fixture", "receipt": record,
                     "raw_output": raw_output})
        return record


def fixture_host_evidence_matches(storage, task_id, receipt):
    records = [e for e in storage.list_events(task_id)
               if e.get("event_type") == "fixture_host_record"
               and e.get("event_id") == receipt["host_call_id"]
               and e.get("actor_role") == "fixture_host"]
    if len(records) != 1:
        return False
    details = records[0].get("details") or {}
    output = details.get("raw_output")
    return (details.get("evidence_kind") == "offline_fixture"
            and details.get("receipt") == receipt and isinstance(output, str)
            and sha256_hex(output.encode("utf-8")) == receipt["tests"]["output_digest"])


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


HOST_EFFECT_RECONCILED_EVENT = "host_effect_reconciled"


def host_effect_observation(workspace_root, authorized_target: str,
                            delivery: Dict[str, Any]) -> Dict[str, Any]:
    """Read-only classification of the current target bytes against a delivery.

    Compares the target on disk with the prepared baseline and expected_after
    hashes. Reads bytes only; it never runs the host check command, so it is a
    reconciliation aid, not fresh evidence.
    """
    summary: Dict[str, Any] = {"path": authorized_target, "state": "unreadable", "sha256": None}
    try:
        target_abs = resolve_target_path(workspace_root, authorized_target)
        data = Path(target_abs).read_bytes()
    except (CodeDeliveryRefused, OSError) as exc:
        summary["error"] = str(exc)
        return summary
    sha = sha256_hex(data)
    baseline = (delivery.get("baseline") or {}).get("sha256_before")
    expected = (delivery.get("expected_after") or {}).get("sha256")
    summary["sha256"] = sha
    if expected is not None and sha == expected:
        summary["state"] = "matches_expected_after"
    elif baseline is not None and sha == baseline:
        summary["state"] = "matches_baseline"
    else:
        summary["state"] = "diverged"
    return summary


def reconcile_host_effect(storage, task_id: str, *, note: str = "",
                          actor_role: str = "operator") -> Dict[str, Any]:
    """Control-owned reconciliation entry for an unconfirmed host apply.

    Reads the current target bytes (never re-runs the host check), records what
    it observed alongside the delivery, clears the durable unconfirmed marker,
    and writes one audit event. It never grants success and never changes the
    task state: reconciliation records the observed reality, it does not
    re-decide the outcome.
    """
    with storage.transaction():
        task = storage.get_task(task_id)
        delivery = (task.get("metadata") or {}).get("_delivery")
        if not isinstance(delivery, dict) or delivery.get("state") != "waiting_host":
            raise CodeDeliveryRefused("no waiting-host delivery to reconcile")
        if not (task.get("_runtime") or {}).get("has_unconfirmed_side_effect"):
            raise CodeDeliveryRefused("no unconfirmed host side effect to reconcile")
        tpl = template_for_task(task)
        workspace_root = (task.get("context") or {}).get("workspace_root")
        observation = host_effect_observation(workspace_root, tpl["authorized_target"], delivery)
        done = dict(delivery)
        done["unconfirmed_host_effect"] = False
        done["reconciled"] = {"at": _utc_now_iso(), "observation": observation,
                              "note": note, "actor_role": actor_role}
        storage.save_code_delivery(task_id, done)
        storage.set_unconfirmed_side_effect(task_id, False)
        status = storage.get_task(task_id)["status"]
        storage.record_event(task_id=task_id, event_type=HOST_EFFECT_RECONCILED_EVENT,
                             from_state=status, to_state=status, actor_role=actor_role,
                             details={"delivery_id": delivery.get("delivery_id"),
                                      "observation": observation, "note": note,
                                      "task_status": status})
    return storage.get_task(task_id)


def is_clean_pass_review(review: Any) -> bool:
    """True only for a well-formed pass verdict with no blocking issues."""
    if not isinstance(review, dict) or review.get("verdict") != "pass":
        return False
    issues = review.get("issues")
    if not isinstance(issues, list) or any(not isinstance(i, dict) for i in issues):
        return False
    return not any(i.get("severity") in {"p0_blocker", "p1_critical", "p2_major"} for i in issues)


def workspace_hashes(workspace_root):
    """Read-only scope inventory for the small isolated fixed-template workspace.

    Does not claim a race-free filesystem snapshot. Links, excessive fixture
    size, or unreadable entries refuse; never follows them outside the scope.
    """
    root = Path(workspace_root).resolve()
    result = {}
    total = 0
    def fail_walk(exc):
        raise exc

    try:
        for current, dirs, files in os.walk(root, followlinks=False, onerror=fail_walk):
            for name in sorted(dirs + files):
                path = Path(current) / name
                if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                    raise CodeDeliveryRefused("scope inventory refuses filesystem links")
                if name in dirs:
                    continue
                if len(result) >= 100 or total + path.stat().st_size > 2 * 1024 * 1024:
                    raise CodeDeliveryRefused("isolated code fixture workspace exceeds scope inventory limits")
                data = path.read_bytes()
                total += len(data)
                if total > 2 * 1024 * 1024:
                    raise CodeDeliveryRefused("scope inventory changed beyond its byte limit")
                result[path.relative_to(root).as_posix()] = sha256_hex(data)
    except OSError as exc:
        raise CodeDeliveryRefused(f"scope inventory unreadable: {exc}") from None
    return result
