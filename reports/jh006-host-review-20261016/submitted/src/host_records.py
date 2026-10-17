"""Trusted host transcript records for production code delivery (JH-006 batch 2).

Pure control helpers: shape + binding validation for host-bridge records
presented by the native host, plus sealed post-apply check expectations.
The offline fixture registration path never satisfies this module: different
recorder, different shape, and the orchestrator routes by stamped source.

Trust model: the host presents the actual transcript item (full command line,
raw aggregated output, exit code, thread/item ids). JH recomputes the output
digest from the carried output, re-reads target/check bytes and workspace
scope, and pins task/delivery/workdir/artifact/review. Anything beyond
mechanical verification (output authenticity beyond the digest binding) stays
an explicit architect-review item against the archived transcript bundle; it
is never manufactured into success here.

Nothing here writes files, spawns processes, or calls models. Workdir checks
resolve paths read-only; callers supply the trusted workspace.
"""

import json
import os
import re
from typing import Any, Dict

from src.code_delivery import CodeDeliveryRefused, sealed_fixture_bytes, sha256_hex


# The only assembler recognized on the production host-record path. Fixture
# registration ("fixture-offline-host/v1") is test-only and never validates
# a production task.
HOST_BRIDGE_RECORDER = "codex-transcript/v1"

HOST_RECORD_REQUIRED_KEYS = {"task_id", "delivery_id", "thread_id", "host_call_id",
                             "apply_attempt_id", "workdir", "artifact", "review",
                             "target", "baseline_seen", "tests", "output",
                             "uncertain", "recorder", "recorded_at"}
HOST_RECORD_ARTIFACT_KEYS = {"version", "artifact_id", "sha256"}
HOST_RECORD_REVIEW_KEYS = {"review_id", "verdict"}
HOST_RECORD_TARGET_KEYS = {"path", "sha256"}
HOST_RECORD_TESTS_KEYS = {"target_path", "target_sha256", "check_script",
                          "check_script_sha256", "command", "exit_code",
                          "output_digest", "status"}

# Raw transcript output carried in-band is bounded so one record cannot bloat
# the event store. The fixed post-apply check prints a handful of lines; a
# larger output means the wrong command ran.
MAX_OUTPUT_CHARS = 65536

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _is_hex64(value: Any) -> bool:
    return isinstance(value, str) and _HEX64.match(value) is not None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def sealed_check_expectation(template: Dict[str, Any]) -> Dict[str, Any]:
    """Return the sealed expected exit + stdout marker for the template check.

    Reads the manifest-sealed checks.json and selects the template entry by
    name. Anything else refuses instead of trusting unchecked expectations.
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
    expect_exit = entry.get("expect_exit", 0)
    marker = entry.get("expect_stdout_contains", "")
    if not _is_int(expect_exit) or not isinstance(marker, str):
        raise CodeDeliveryRefused("sealed post-apply check expectation malformed")
    return {"expect_exit": expect_exit, "expect_stdout_contains": marker}


def validate_host_record(record: Dict[str, Any], *, task_id: str, delivery_id: str,
                         authorized_target: str, workdir, pinned_artifact: Dict[str, Any],
                         pinned_review_id: str, check_script_rel: str) -> Dict[str, Any]:
    """Validate a production host transcript record. Never mutates, never trusts.

    Checks exact shape, assembler identity, task/delivery/workdir/artifact/
    review pins, completed status, and digest recomputation over the carried
    output. Returns the record unchanged on success; raises CodeDeliveryRefused
    (no state change) on any malformed or mismatched field.

    Execution-truth judgments (exit value, stdout marker, command markers,
    declared uncertainty) belong to the caller, which maps them to
    needs_attention instead of success.
    """
    if not isinstance(record, dict) or set(record) != HOST_RECORD_REQUIRED_KEYS:
        raise CodeDeliveryRefused("host record must contain exactly the required keys")
    if record["recorder"] != HOST_BRIDGE_RECORDER:
        raise CodeDeliveryRefused(
            f"unknown host record assembler {record.get('recorder')!r}; "
            f"only {HOST_BRIDGE_RECORDER!r} validates production tasks, "
            "fixture registration is not production evidence")
    if record["task_id"] != task_id or record["delivery_id"] != delivery_id:
        raise CodeDeliveryRefused("host record task/delivery mismatch")
    for key in ("thread_id", "host_call_id", "apply_attempt_id"):
        if not isinstance(record[key], str) or not record[key]:
            raise CodeDeliveryRefused(f"host record {key} missing")
    if not isinstance(record["workdir"], str) or not record["workdir"]:
        raise CodeDeliveryRefused("host record workdir missing")
    try:
        trusted_norm = os.path.normcase(os.path.realpath(str(workdir)))
        record_norm = os.path.normcase(os.path.realpath(record["workdir"]))
    except (OSError, ValueError) as exc:
        raise CodeDeliveryRefused(f"host record workdir unreadable: {exc}") from None
    if record_norm != trusted_norm:
        raise CodeDeliveryRefused("host record workdir differs from the trusted task workspace")
    artifact = record["artifact"]
    if not isinstance(artifact, dict) or set(artifact) != HOST_RECORD_ARTIFACT_KEYS:
        raise CodeDeliveryRefused("host record artifact block malformed")
    if (not _is_int(artifact["version"]) or artifact["version"] != pinned_artifact.get("version")
            or artifact["artifact_id"] != pinned_artifact.get("artifact_id")
            or artifact["sha256"] != pinned_artifact.get("sha256")):
        raise CodeDeliveryRefused("host record artifact differs from the prepared version")
    if not _is_hex64(artifact["sha256"]):
        raise CodeDeliveryRefused("host record artifact sha256 malformed")
    review = record["review"]
    if not isinstance(review, dict) or set(review) != HOST_RECORD_REVIEW_KEYS:
        raise CodeDeliveryRefused("host record review block malformed")
    if review["review_id"] != pinned_review_id or review["verdict"] != "pass":
        raise CodeDeliveryRefused("host record review differs from the prepared pass review")
    target = record["target"]
    if not isinstance(target, dict) or set(target) != HOST_RECORD_TARGET_KEYS:
        raise CodeDeliveryRefused("host record target block malformed")
    if target["path"] != authorized_target:
        raise CodeDeliveryRefused(
            f"host record target {target['path']!r} is outside {authorized_target!r}")
    if not _is_hex64(target["sha256"]):
        raise CodeDeliveryRefused("host record target sha256 malformed")
    if not _is_hex64(record["baseline_seen"]):
        raise CodeDeliveryRefused("host record baseline hash malformed")
    tests = record["tests"]
    if not isinstance(tests, dict) or set(tests) != HOST_RECORD_TESTS_KEYS:
        raise CodeDeliveryRefused("host record tests block malformed")
    if tests["target_path"] != authorized_target:
        raise CodeDeliveryRefused("host record test target mismatch")
    for key in ("target_sha256", "check_script_sha256", "output_digest"):
        if not _is_hex64(tests[key]):
            raise CodeDeliveryRefused(f"host record {key} malformed")
    if tests["check_script"] != check_script_rel:
        raise CodeDeliveryRefused("host record check script path differs from the fixed check")
    if not isinstance(tests["command"], str) or not tests["command"]:
        raise CodeDeliveryRefused("host record command missing")
    if not _is_int(tests["exit_code"]):
        raise CodeDeliveryRefused("host record exit code malformed")
    if tests["status"] != "completed":
        raise CodeDeliveryRefused("host record execution is not a completed transcript item")
    output = record["output"]
    if not isinstance(output, str):
        raise CodeDeliveryRefused("host record output must be the raw transcript string")
    if len(output) > MAX_OUTPUT_CHARS:
        raise CodeDeliveryRefused("host record output exceeds the carried-output bound")
    if sha256_hex(output.encode("utf-8")) != tests["output_digest"]:
        raise CodeDeliveryRefused("host record output digest does not match the carried output")
    if type(record["uncertain"]) is not bool:
        raise CodeDeliveryRefused("host record uncertain flag malformed")
    if not isinstance(record["recorded_at"], str) or not record["recorded_at"]:
        raise CodeDeliveryRefused("host record recorded_at malformed")
    return record


def transcript_command_markers_ok(command: str, *, check_script_rel: str,
                                  authorized_target: str) -> bool:
    """True when the transcript command line embeds the fixed check markers.

    The native host wraps the fixed check in its own shell invocation, so the
    control layer cannot demand token equality. It demands instead that the
    command line name the fixed check script and the authorized target (path
    separators normalized), and never the sealed reference tree.
    """
    if not isinstance(command, str) or not command:
        return False
    norm = command.replace("\\", "/")
    if "expected/" in norm:
        return False
    return check_script_rel.replace("\\", "/") in norm and authorized_target in norm
