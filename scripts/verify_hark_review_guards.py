"""In-memory mutation checks for the two Hark review regression guards."""
import io
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
import src.capture_binding as original
import test_jh006_capture_binding as fixture
import test_hark_archive_location as audit


def run_suite(suite):
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    return result, stream.getvalue()


def main():
    cls = audit.TestHarkArchiveLocation
    control, detail = run_suite(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    if not control.wasSuccessful() or control.testsRun != 3 or control.skipped:
        print(json.dumps({"passed": False, "reason": "control failed", "detail": detail}))
        return 1
    source = Path(original.__file__).read_text(encoding="utf-8")
    rows = []
    for name, old, new, method in (
        ("archive-cwd-anchor", "archive_path = archive_path.resolve(strict=True)",
         "archive_path = archive_path", "test_relative_archive_survives_changed_cwd"),
        ("persisted-review-refusal", 'if json.loads(row["review_items_json"]):',
         "if False:", "test_persisted_unverified_review_items_are_rejected"),
    ):
        if source.count(old) != 1:
            raise RuntimeError("mutation anchor not unique: " + name)
        mutant = types.ModuleType(original.__name__)
        mutant.__file__ = original.__file__
        exec(compile(source.replace(old, new), original.__file__, "exec"), mutant.__dict__)
        with patch.object(audit, "cb", mutant), patch.object(fixture, "cb", mutant), \
             patch.object(fixture, "register_capture_binding", mutant.register_capture_binding):
            result, detail = run_suite(unittest.TestSuite([cls(method)]))
        detected = bool(result.failures) and not result.errors and not result.skipped
        rows.append({"name": name, "tests_run": result.testsRun, "detected": detected,
                     "assertion_failures": len(result.failures), "errors": len(result.errors),
                     "skipped": len(result.skipped), "detail": "assertion failure" if detected else detail})
    passed = all(r["detected"] and r["tests_run"] == 1 for r in rows)
    print(json.dumps({"synthetic": True, "api_calls": 0, "controls": control.testsRun,
                      "mutations": len(rows), "detected": sum(r["detected"] for r in rows),
                      "errors": sum(r["errors"] for r in rows), "rows": rows,
                      "passed": passed}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
