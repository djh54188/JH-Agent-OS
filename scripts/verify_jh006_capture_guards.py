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
import test_jh006_capture_boundaries as boundary
import test_jh006_host_capture as existing


def run_case(name, target, old, new, tests):
    source = Path(target.__file__).read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise RuntimeError(f"mutation anchor not unique: {name}")
    module = types.ModuleType(target.__name__)
    module.__file__ = target.__file__
    exec(compile(source.replace(old, new), target.__file__, "exec"), module.__dict__)
    suite = unittest.TestSuite()
    saved = {}
    if target is original:
        # Tests import function aliases, so update every actual consumer.
        for consumer in (boundary, existing):
            for key in list(vars(consumer)):
                value = getattr(consumer, key)
                if callable(value) and getattr(value, "__module__", None) == original.__name__:
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
    ]
    results = [run_case(*case) for case in cases]
    report = {"kind": "synthetic-mutations", "mutations": len(results),
              "detected": sum(r["detected"] for r in results),
              "errors": sum(r["errors"] for r in results), "results": results}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(r["detected"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
