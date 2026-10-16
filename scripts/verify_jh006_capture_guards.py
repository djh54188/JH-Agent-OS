"""Capture boundary mutation checks: assertion failures count, errors never do.

Runs synthetic tests only, swaps in-memory modules and restores them after each
mutation. No working source, native logs, production routes or fixtures change.
"""
import contextlib
import importlib.util
import io
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
import src.host_capture as original
import src.capture_binding as boundmod
import src.storage as storagemod
import test_jh006_binding_audit as audit
import test_jh006_code_delivery as fixtures
import test_jh006_capture_boundaries as boundary
import test_jh006_host_capture as existing
import test_jh006_capture_binding as bound


def run_case(name, target, old, new, tests):
    source = Path(target.__file__).read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise RuntimeError(f"mutation anchor not unique: {name}")
    module = types.ModuleType(target.__name__)
    module.__file__ = target.__file__
    exec(compile(source.replace(old, new), target.__file__, "exec"), module.__dict__)
    suite = unittest.TestSuite()
    saved = {}
    if target in (original, boundmod, storagemod):
        # Tests import function aliases, so update every actual consumer.
        consumers = ((boundary, existing, bound, boundmod, audit) if target is original
                     else (bound, audit, fixtures))
        for consumer in consumers:
            for key in list(vars(consumer)):
                value = getattr(consumer, key)
                if value is target:
                    saved[(consumer, key)] = value
                    setattr(consumer, key, module)
                    continue
                if callable(value) and getattr(value, "__module__", None) == target.__name__:
                    saved[(consumer, key)] = value
                    setattr(consumer, key, getattr(module, key))
    else:
        saved[(boundary, "entry")] = boundary.entry
        boundary.entry = module
    try:
        for cls, test in tests:
            suite.addTest(cls(test))
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    finally:
        for (consumer, key), value in saved.items():
            setattr(consumer, key, value)
    return {"name": name, "tests_run": result.testsRun,
            "assertion_failures": len(result.failures), "errors": len(result.errors),
            "skipped": len(result.skipped),
            "detected": bool(result.failures) and not result.errors,
            "detail": stream.getvalue() if result.errors or not result.failures else "assertion failure"}


def main():
    B = boundary.TestCaptureBoundaries
    cases = [
        ("source-root", boundary.entry,
         "resolved.relative_to(SESSION_ROOT.resolve(strict=True))", "pass",
         [(B, "test_workspace_file_is_not_a_session_source")]),
        ("file-uri-authority", original,
         "if parsed.netloc or parsed.query or parsed.fragment:",
         "if parsed.query or parsed.fragment:",
         [(B, "test_foreign_uri_authority_is_not_local_cwd")]),
        ("session-conflict", original,
         'if len(metas) != 1 or metas[0]["obj"]["payload"].get("id") != thread_id:',
         'if not metas or metas[0]["obj"]["payload"].get("id") != thread_id:',
         [(B, "test_conflicting_session_meta_refused")]),
        ("single-execution", original, "if len(executions) != 1:",
         "if not executions:", [(B, "test_multiple_executions_in_window_refused")]),
        ("nonoverlapping-call", original,
         'raise CaptureError("call-window-ambiguous: overlapping tool calls")',
         "pass", [(B, "test_nested_call_window_refused")]),
        ("execution-window", original,
         'and _is_int(ordinal) and start < ordinal < end\n                    and calls[0]["line_no"] < r["line_no"] < outputs[0]["line_no"]',
         'and _is_int(ordinal)', 
         [(B, "test_execution_before_call_refused")]),
        ("mandatory-script-pin", original,
         'or expected_exit is None or not isinstance(expected_call_input, str)\n            or not expected_call_input):',
         'or expected_exit is None):',
         [(B, "test_missing_exact_script_pin_refused")]),
        ("exact-script-pin", original,
         'if expected_call_input is not None and call_record["input"] != expected_call_input:',
         "if False:",
         [(existing.TestDesktopCaptureNegative, "test_call_input_mismatch_refused")]),
        ("whole-archive", original,
         'if sha256_hex(archive_raw) != manifest.get("archive_digest"):',
         "if False:", [(B, "test_meta_context_tamper_refused")]),
        ("manifest-facts", original,
         'selected, call, expected_argv=event.get("argv"), expected_workdir=event.get("workdir"),',
         'selected, call, expected_argv=event.get("argv"), expected_workdir=_file_uri_to_path(selected["cwd_uri"]),',
         [(B, "test_manifest_workdir_must_match_raw_cwd")]),
        ("binding-owner-held", boundmod,
         "    if not storage.claim_task(task_id, owner_id):",
         "    if False:",
         [(bound.TestCaptureBindingLifecycle, "test_owner_held_refused")]),
        ("binding-delivery-request", boundmod,
         '        raise BindingRefused("delivery-mismatch: request delivery is not the current waiting delivery")',
         "        pass",
         [(bound.TestCaptureBindingRegister, "test_wrong_delivery_id_refused")]),
        ("binding-claim-identity", boundmod,
         '        raise BindingRefused("claim-identity-mismatch: manifest task/delivery claims differ from the request")',
         "        pass",
         [(bound.TestCaptureBindingClaims, "test_manifest_delivery_claim_mismatch_refused")]),
        ("binding-claim-artifact", boundmod,
         '        raise BindingRefused("claim-artifact-mismatch: manifest artifact claim differs from the prepared version")',
         "        pass",
         [(bound.TestCaptureBindingClaims, "test_manifest_artifact_claim_mismatch_refused")]),
        ("binding-claim-review", boundmod,
         '        raise BindingRefused("claim-review-mismatch: manifest review claim differs from the prepared review")',
         "        pass",
         [(bound.TestCaptureBindingClaims, "test_manifest_review_claim_mismatch_refused")]),
        ("binding-artifact-current", boundmod,
         '        raise BindingRefused("artifact-changed: current artifact differs from the prepared version")',
         "        pass",
         [(bound.TestCaptureBindingClaims, "test_changed_artifact_refused")]),
        ("binding-review-current", boundmod,
         '        raise BindingRefused("review-stale: current review differs from the prepared review")',
         "        pass",
         [(bound.TestCaptureBindingClaims, "test_new_review_attempt_refused")]),
        ("binding-target-bytes", boundmod,
         '        raise BindingRefused("target-bytes-mismatch: workspace target differs from expected_after")',
         "        pass",
         [(bound.TestCaptureBindingLifecycle, "test_baseline_target_refused_and_state_kept")]),
        ("binding-liveness", boundmod,
         '            raise BindingRefused("archive-live-mismatch: archived bytes differ from the live store")',
         "            pass",
         [(bound.TestCaptureBindingTruth, "test_live_bytes_diverged_refused")]),
        ("binding-source", boundmod,
         '        raise BindingRefused("source-not-production: only production tasks bind real captures")',
         "        pass",
         [(bound.TestCaptureBindingRegister, "test_fixture_task_refused")]),
        ("binding-exit-passthrough", boundmod,
         '        expected_exit=snapshot["check"]["expect_exit"],',
         '        expected_exit=selected["exit_code"],',
         [(bound.TestCaptureBindingTruth, "test_nonzero_exit_refused")]),
        ("binding-marker-passthrough", boundmod,
         '        expected_marker=snapshot["check"]["marker"] or None,',
         "        expected_marker=None,",
         [(bound.TestCaptureBindingTruth, "test_markerless_output_refused")]),
        ("binding-argv", original,
         '    if list(exec_record["argv"]) != list(expected_argv):',
         "    if False:",
         [(bound.TestCaptureBindingTruth, "test_echo_argv_refused")]),
        ("binding-workdir", original,
         '    if _norm_path(_file_uri_to_path(exec_record["cwd_uri"])) != _norm_path(expected_workdir):',
         "    if False:",
         [(bound.TestCaptureBindingTruth, "test_wrong_workdir_refused")]),
        ("binding-output-digest", original,
         '    if sha256_hex(output.encode("utf-8")) != want_output:',
         "    if False:",
         [(bound.TestCaptureBindingArchive, "test_tampered_manifest_digest_refused")]),
        ("binding-fixed-invocation", boundmod,
         '        raise BindingRefused("invocation-unverified: caller pin differs from the control-owned fixed check")',
         '        pass', [(audit.TestBindingAudit, "test_echo_reassertion_is_not_a_fixed_check")]),
        ("binding-current-reload", boundmod,
         '        current = _control_snapshot(storage, task_id, delivery_id)',
         '        current = snapshot', [(audit.TestBindingAudit, "test_target_drift_invalidates_current"),
                                        (audit.TestBindingAudit, "test_review_payload_revoked_invalidates_current")]),
        ("binding-atomic-event", boundmod,
         '        with storage.transaction():', '        if True:',
         [(audit.TestBindingAudit, "test_registration_event_failure_rolls_back_row")]),
        ("binding-unique-call", storagemod,
         '                CREATE UNIQUE INDEX IF NOT EXISTS capture_binding_call_unique\n                    ON capture_bindings(thread_id, call_id);',
         '', [(audit.TestBindingAudit, "test_same_call_with_another_item_violates_database_unique")]),
        ("binding-unique-item", storagemod,
         '                CREATE UNIQUE INDEX IF NOT EXISTS capture_binding_item_unique\n                    ON capture_bindings(thread_id, item_id);',
         '', [(audit.TestBindingAudit, "test_same_item_with_another_call_violates_database_unique")]),
    ]
    results = [run_case(*case) for case in cases]
    # The full-manifest fact recomputation still rejects a changed output digest.
    # A surviving single-line mutation here is redundant, never counted detected.
    for result in results:
        result["redundant"] = (result["name"] == "binding-output-digest"
                               and not result["detected"] and result["errors"] == 0
                               and result["tests_run"] == 1 and result["skipped"] == 0)

    report = {"kind": "synthetic-mutations", "mutations": len(results),
              "detected": sum(r["detected"] for r in results),
              "errors": sum(r["errors"] for r in results),
              "redundant": sum(r["redundant"] for r in results), "results": results}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(r["detected"] or r["redundant"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
