"""
Mutation Guard Verification for Phase 2 Control Layer (state_machine, router_policy, storage, orchestrator).
Verifies that intentionally injected defects in memory are caught by tests/test_phase2_control_layer.py.
"""

import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("phase2_tests", ROOT / "tests/test_phase2_control_layer.py")
phase2_tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase2_tests)

mutations = [
    (
        "worker_completion_bypass_review",
        "src/state_machine.py",
        '"executing": frozenset({"reviewing", "awaiting_approval", "failed", "cancelled", "needs_attention"}),',
        '"executing": frozenset({"succeeded", "reviewing", "awaiting_approval", "failed", "cancelled", "needs_attention"}),',
        "if current_state != \"reviewing\":",
        "if False:",
        "test_05_illegal_state_transitions_and_invalid_messages",
    ),
    (
        "reviewer_failure_auto_pass",
        "src/orchestrator.py",
        '"needs_attention",\n                    actor_role="reviewer",\n                    reason="reviewer_unavailable_or_invalid_fail_closed",',
        '"succeeded",\n                    actor_role="reviewer",\n                    reason="reviewer_unavailable_or_invalid_fail_closed",\n                    review_verdict="pass",',
        None,
        None,
        "test_03_review_revise_reject_and_fail_closed",
    ),
    (
        "level3_downgrade_to_allow",
        "src/router_policy.py",
        'action="deny_and_fail",',
        'action="allow",',
        None,
        None,
        "test_06_danger_action_overrides_routing_and_per_call_check",
    ),
    (
        "unknown_tokens_default_zero",
        "src/storage.py",
        "if tokens_used is None:\n            unknown_count += 1",
        "if tokens_used is None:\n            current_tokens = 0",
        None,
        None,
        "test_10_budget_exhaustion_and_unknown_tokens_preserved_as_none",
    ),
    (
        "restart_blind_auto_replay",
        "src/storage.py",
        '"auto_replay_allowed": False,',
        '"auto_replay_allowed": True,',
        None,
        None,
        "test_11_restart_recovery_blocks_unconfirmed_side_effect_replay",
    ),
]

records = []
for name, rel_path, orig1, repl1, orig2, repl2, test_name in mutations:
    src_file = ROOT / rel_path
    source = src_file.read_text(encoding="utf-8")
    if orig1 not in source:
        raise RuntimeError(f"Mutation target not found in {rel_path}: {name}")
    mutated_src = source.replace(orig1, repl1)
    if orig2 and repl2:
        mutated_src = mutated_src.replace(orig2, repl2)

    mod_name = "src." + Path(rel_path).stem
    orig_mod = sys.modules[mod_name]
    mutant_mod = types.ModuleType(mod_name)
    mutant_mod.__file__ = str(src_file)
    exec(compile(mutated_src, str(src_file), "exec"), mutant_mod.__dict__)

    suite = unittest.TestSuite([phase2_tests.TestPhase2ControlLayer(test_name)])
    # Patch references in dependent modules
    patches = [patch.dict(sys.modules, {mod_name: mutant_mod})]
    if mod_name == "src.state_machine":
        import src.storage
        patches.append(patch.object(src.storage, "validate_transition", mutant_mod.validate_transition))
    elif mod_name == "src.router_policy":
        import src.orchestrator
        patches.append(patch.object(src.orchestrator, "RiskPolicyEngine", mutant_mod.RiskPolicyEngine))
        patches.append(patch.object(src.orchestrator, "RuleBasedRouter", mutant_mod.RuleBasedRouter))
    elif mod_name == "src.storage":
        import src.orchestrator
        patches.append(patch.object(src.orchestrator, "TaskStorage", mutant_mod.TaskStorage))
        patches.append(patch.object(phase2_tests, "TaskStorage", mutant_mod.TaskStorage))
    elif mod_name == "src.orchestrator":
        patches.append(patch.object(phase2_tests, "Orchestrator", mutant_mod.Orchestrator))

    for p in patches:
        p.start()
    try:
        res = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    finally:
        for p in reversed(patches):
            p.stop()

    detected = bool(res.failures or res.errors)
    records.append({"guard": name, "module": rel_path, "detected": detected})

passed = all(r["detected"] for r in records)
print(json.dumps({"mutations": len(records), "passed": passed, "results": records}))
raise SystemExit(0 if passed else 1)
