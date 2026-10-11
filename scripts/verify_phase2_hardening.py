"""Run offline acceptance cases and in-memory guard mutations; write deterministic evidence."""

from contextlib import ExitStack
import hashlib
import importlib
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_phase2_hardening import TestPhase2Hardening


MUTATIONS = [
    ("wrong_task_review", "control_guards", '("task_id", "review_id", "target_artifact_path", "reviewer_role")',
     '("review_id", "target_artifact_path", "reviewer_role")', "test_opt_04_wrong_task_review"),
    ("stale_artifact_review", "control_guards", "if before != after:", "if False:", "test_opt_05_old_artifact_hash"),
    ("review_nonce", "control_guards", '("task_id", "review_id", "target_artifact_path", "reviewer_role")',
     '("task_id", "target_artifact_path", "reviewer_role")', "test_invalid_review_nonce_rejected"),
    ("contradictory_verdict", "control_guards", 'if review["verdict"] == "pass" and issue["severity"]',
     'if False and issue["severity"]', "test_opt_07_contradictory_pass"),
    ("acceptance_failure", "control_guards", 'or result.get("passed") is not True',
     'or False', "test_opt_02_failed_acceptance"),
    ("approval_parameter_binding", "router_policy", 'approved.get(req_id) == request_fingerprint(tool_request)',
     'req_id in approved', "test_opt_15_changed_approved_action"),
    ("remaining_token_cap", "control_guards", "remaining_tokens - reserved_input,",
     "131072,", "test_opt_20_output_cap"),
    ("transport_timeout", "responses_adapter", 'timeout=kwargs.get("timeout")',
     'timeout=None', "test_opt_17_remaining_timeout_reaches_transport"),
    ("unknown_usage_stop", "orchestrator", 'if runtime["tokens_unknown_count"]:',
     'if False:', "test_opt_21_unknown_usage_stops"),
    ("transaction_atomicity", "storage", "if outer:\n                    self._conn.commit()",
     "if True:\n                    self._conn.commit()", "test_opt_24_transaction_rolls_back_both"),
    ("duplicate_completion", "storage", 'if attempt["status"] == "completed":',
     'if False:', "test_opt_25_duplicate_completion_not_charged"),
    ("single_owner", "storage", 'AND owner_id IS NULL',
     '', "test_opt_26_duplicate_claims"),
    ("terminal_tool_rejection", "storage", 'if task["status"] != "executing":',
     'if False:', "test_opt_16_terminal_tool_request"),
    ("interrupted_attempt_replay", "orchestrator", 'if self.storage.has_uncertain_attempt(task_id):',
     'if False:', "test_crash_cannot_be_retried_without_reconciliation"),
]


def run_test(name):
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output).run(unittest.TestSuite([TestPhase2Hardening(name)]))
    return result, output.getvalue()


def mutation_checks():
    import src.orchestrator as orchestrator
    import test_phase2_hardening as tests
    records = []
    for name, module, before, after, test in MUTATIONS:
        baseline, baseline_output = run_test(test)
        if not baseline.wasSuccessful():
            raise RuntimeError(f"Mutation baseline failed: {name}\n{baseline_output}")
        path = ROOT / "src" / f"{module}.py"
        source = path.read_text(encoding="utf-8")
        if before not in source:
            raise RuntimeError(f"Mutation anchor absent: {name}")
        original = importlib.import_module(f"src.{module}")
        mutant = types.ModuleType(original.__name__)
        mutant.__file__ = str(path)
        exec(compile(source.replace(before, after, 1), str(path), "exec"), mutant.__dict__)
        with ExitStack() as stack:
            if module == "control_guards":
                for export in ("artifact_snapshot", "call_limits", "input_reserve", "validate_acceptance", "validate_review"):
                    stack.enter_context(patch.object(orchestrator, export, getattr(mutant, export)))
            elif module == "router_policy":
                stack.enter_context(patch.object(orchestrator, "RiskPolicyEngine", mutant.RiskPolicyEngine))
            elif module == "storage":
                stack.enter_context(patch.object(orchestrator, "TaskStorage", mutant.TaskStorage))
                stack.enter_context(patch.object(tests, "TaskStorage", mutant.TaskStorage))
            elif module == "orchestrator":
                stack.enter_context(patch.object(tests, "Orchestrator", mutant.Orchestrator))
            elif module == "responses_adapter":
                stack.enter_context(patch.object(tests, "OpenAIResponsesAdapter", mutant.OpenAIResponsesAdapter))
            result, output = run_test(test)
        records.append({"mutation": name, "test": test, "detected": bool(result.failures) and not result.errors,
                        "assertion_failures": len(result.failures), "runtime_errors": len(result.errors)})
    return records


def main():
    spec = json.loads((ROOT / "docs/optimization-20261010-side/acceptance-cases.json").read_text(encoding="utf-8"))
    methods = unittest.defaultTestLoader.getTestCaseNames(TestPhase2Hardening)
    cases = []
    for case in spec["cases"]:
        prefix = "test_" + case["id"].lower().replace("-", "_") + "_"
        matches = [name for name in methods if name.startswith(prefix)]
        if len(matches) != 1:
            raise RuntimeError(f"Expected exactly one executable case for {case['id']}")
        result, output = run_test(matches[0])
        status = "not_applicable_phase2" if result.skipped else "passed" if result.wasSuccessful() else "failed"
        cases.append({"id": case["id"], "test": matches[0], "status": status,
                      "reason": result.skipped[0][1] if result.skipped else None})
        if not result.wasSuccessful():
            print(output, file=sys.stderr)
    mutations = mutation_checks()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    all_result = unittest.TextTestRunner(verbosity=1).run(suite)
    report = {
        "scope": "Phase 2 offline control layer only; providers, tools and acceptance evaluator are fixtures",
        "specification_baseline": spec["baseline_commit"],
        "python_version": sys.version.split()[0],
        "source_sha256": {
            str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted([*ROOT.glob("src/*.py"), *ROOT.glob("tests/test_*.py"),
                                ROOT / "scripts/verify_phase2_hardening.py"])
        },
        "cases": cases,
        "case_counts": {status: sum(c["status"] == status for c in cases)
                        for status in ("passed", "failed", "not_applicable_phase2")},
        "unit_tests": {"run": all_result.testsRun, "failures": len(all_result.failures),
                       "errors": len(all_result.errors), "skipped": len(all_result.skipped)},
        "mutation_checks": mutations,
        "limitations": [
            "OPT-14 real junction/TOCTOU behavior remains Phase 4 work.",
            "No live provider invocation or real file/shell tool execution in this verification.",
            "Token input reservation is an estimate; no hard token/cost guarantee is claimed.",
            "Transport timeout is capped per request; a strict wall-clock network watchdog is not implemented.",
            "Production acceptance evaluator must be supplied explicitly; absent evidence fails closed.",
            "Recovery is an exclusive startup operation; no live multi-process takeover or PID adoption.",
        ],
    }
    report["passed"] = all_result.wasSuccessful() and all(c["status"] != "failed" for c in cases) and all(m["detected"] for m in mutations)
    target = ROOT / "reports/phase2-hardening-verification.json"
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "cases": report["case_counts"], "mutations_detected": sum(m["detected"] for m in mutations), "report": str(target)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
