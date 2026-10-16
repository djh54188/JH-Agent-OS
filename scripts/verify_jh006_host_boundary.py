"""Replay the rejected production route in memory to prove containment regressions.

One route regression, five observable counterexamples. Does not count
exceptions/harness errors as detection; never changes runtime source on disk.
All executions are isolated synthetic fixtures, not real host evidence.
"""
import io
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import test_jh006_code_delivery as fixture_tests
import test_jh006_host_boundary as boundary_tests

ARCHIVE = ROOT / "reports/jh006-host-review-20261016/submitted"
TESTS = [
    "test_self_consistent_claim_is_not_independent_host_evidence",
    "test_echoed_check_markers_cannot_complete",
    "test_other_workspace_in_command_cannot_complete",
    "test_rehashing_fabricated_output_does_not_create_authority",
    "test_cli_rejects_unregistered_host_claim",
]


def run(name):
    return unittest.TextTestRunner(stream=io.StringIO()).run(
        boundary_tests.TestHostSourceBoundary(name))


def main():
    controls = [run(name) for name in TESTS]
    if not all(r.wasSuccessful() and r.testsRun == 1 for r in controls):
        print(json.dumps({"passed": False, "reason": "live control failed"}))
        return 1
    helper = types.ModuleType("src.host_records")
    helper.__file__ = str(ROOT / "src/host_records.py")
    exec(compile((ARCHIVE / "src/host_records.py").read_text(encoding="utf-8"),
                 helper.__file__, "exec"), helper.__dict__)
    candidate = types.ModuleType("src.orchestrator")
    candidate.__file__ = str(ROOT / "src/orchestrator.py")
    with patch.dict(sys.modules, {"src.host_records": helper}):
        exec(compile((ARCHIVE / "src/orchestrator.py").read_text(encoding="utf-8"),
                     candidate.__file__, "exec"), candidate.__dict__)
    rows = []
    with patch.object(fixture_tests, "Orchestrator", candidate.Orchestrator), \
         patch("src.cli.Orchestrator", candidate.Orchestrator):
        for name in TESTS:
            result = run(name)
            rows.append({"case": name, "detected": bool(result.failures) and not result.errors,
                         "failures": len(result.failures), "errors": len(result.errors)})
    passed = all(r["detected"] for r in rows)
    print(json.dumps({"scope": "containment_only_not_bridge_acceptance", "api_calls": 0,
                      "route_regressions": 1, "controls": len(controls), "replays": len(rows),
                      "detected": sum(r["detected"] for r in rows), "rows": rows,
                      "passed": passed}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
