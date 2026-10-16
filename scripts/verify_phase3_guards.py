"""Mutation guard verification for Phase 3 artifact contract (memory-isolated mutants).

Each mutation breaks one new Phase 3 guarantee; the matching test in
tests/test_phase3_loop.py must turn red. Reuses the in-memory mutation approach
of scripts/verify_phase2_guards.py; no files are modified on disk.
"""

import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

spec = importlib.util.spec_from_file_location("phase3_tests", ROOT / "tests/test_phase3_loop.py")
phase3_tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase3_tests)

MUTATIONS = [
    {
        "name": "artifact_task_correlation_skipped",
        "module": "src/orchestrator.py",
        "original": '            if envelope["task_id"] != task_id:\n                raise SchemaValidationError("Artifact task_id correlation mismatch")',
        "replacement": '            if False:\n                raise SchemaValidationError("Artifact task_id correlation mismatch")',
        "test": "test_c_artifact_task_correlation_mismatch",
    },
    {
        "name": "acceptance_evidence_ref_tampered",
        "module": "src/orchestrator.py",
        "original": '            evidence_ref = f"artifact:{envelope[\'artifact_id\']}@v{version}:sha256={record[\'sha256\']}"',
        "replacement": '            evidence_ref = f"artifact:{envelope[\'artifact_id\']}@v{version}:sha256={record[\'sha256\']}-tampered"',
        "test": "test_a_generation_success_full_loop",
    },
    {
        "name": "repair_context_downgraded_to_initial",
        "module": "src/orchestrator.py",
        "original": ('        if repair_count > 0:\n'
                     '            issues = []\n'
                     '            for binding in self.storage.list_review_bindings(task_id=task["task_id"]):\n'
                     '                issues.extend(binding["review"].get("issues") or [])\n'
                     '            acceptance_failures = ['),
        "replacement": ('        if False:\n'
                        '            issues = []\n'
                        '            for binding in self.storage.list_review_bindings(task_id=task["task_id"]):\n'
                        '                issues.extend(binding["review"].get("issues") or [])\n'
                        '            acceptance_failures = ['),
        "test": "test_b_injected_error_repaired_and_re_reviewed",
    },
    {
        "name": "generalization_marked_verified",
        "module": "src/memory.py",
        "original": '            task_id, "successful_pattern", "unverified",',
        "replacement": '            task_id, "successful_pattern", "verified",',
        "test": "test_a_generation_success_full_loop",
    },
    {
        "name": "gate_status_check_relaxed",
        "module": "src/control_guards.py",
        "original": '    if task.get("status") != "succeeded":',
        "replacement": '    if False:',
        "test": "test_gate_rejects_non_succeeded_status",
    },
    {
        "name": "gate_artifact_linkage_relaxed",
        "module": "src/control_guards.py",
        "original": '        raise SchemaValidationError(\n            "closed-loop gate: latest review does not cover the current artifact version (stale SHA-256)")',
        "replacement": "        pass",
        "test": "test_gate_rejects_stale_artifact_hash",
    },
    {
        "name": "gate_acceptance_check_relaxed",
        "module": "src/control_guards.py",
        "original": '        raise SchemaValidationError("closed-loop gate: acceptance evidence missing or failed")',
        "replacement": "        pass",
        "test": "test_gate_rejects_incomplete_acceptance",
    },
    {
        "name": "gate_unknown_usage_relaxed",
        "module": "src/control_guards.py",
        "original": '    if runtime.get("tokens_unknown_count"):',
        "replacement": '    if False:',
        "test": "test_gate_rejects_unknown_usage",
    },
]


def main():
    records = []
    for mutation in MUTATIONS:
        src_file = ROOT / mutation["module"]
        source = src_file.read_text(encoding="utf-8")
        if source.count(mutation["original"]) != 1:
            raise RuntimeError("Mutation target is ambiguous or missing: " + mutation["name"])
        mutated_source = source.replace(mutation["original"], mutation["replacement"])

        module_name = "src." + Path(mutation["module"]).stem
        mutant = types.ModuleType(module_name)
        mutant.__file__ = str(src_file)
        exec(compile(mutated_source, str(src_file), "exec"), mutant.__dict__)

        patches = []
        if module_name == "src.orchestrator":
            import src.orchestrator as live
            for attr in ("Orchestrator", "record_task_outcome_memory", "mem_artifact_path",
                         "is_mem_artifact_path", "ArtifactStore"):
                if hasattr(mutant, attr):
                    patches.append(unittest.mock.patch.object(live, attr, getattr(mutant, attr), create=True))
            patches.append(unittest.mock.patch.object(phase3_tests, "Orchestrator", mutant.Orchestrator))
        elif module_name == "src.memory":
            import src.orchestrator as live
            patches.append(unittest.mock.patch.object(live, "record_task_outcome_memory", mutant.record_task_outcome_memory))
        elif module_name == "src.control_guards":
            patches.append(unittest.mock.patch.object(
                phase3_tests, "verify_closed_loop_success", mutant.verify_closed_loop_success))

        def _find_test(test_name):
            for cls in (phase3_tests.TestPhase3Loop, phase3_tests.TestClosedLoopSuccessGate):
                if hasattr(cls, test_name):
                    return cls(test_name)
            raise RuntimeError("test not found: " + test_name)

        suite = unittest.TestSuite([_find_test(mutation["test"])])
        for p in patches:
            p.start()
        try:
            result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        finally:
            for p in reversed(patches):
                p.stop()

        detected = bool(result.failures or result.errors)
        records.append({"guard": mutation["name"], "module": mutation["module"], "detected": detected})

    passed = all(r["detected"] for r in records)
    print(json.dumps({"mutations": len(records), "passed": passed, "results": records}))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    import unittest.mock
    main()
