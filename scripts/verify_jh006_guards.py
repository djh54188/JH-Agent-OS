"""JH-006 offline source mutations; assertion failures detect, errors never do.

Mutants run in memory. Production source and sealed fixture bytes stay intact.
Each test creates its own isolated workspace and SQLite records. No APIs.
"""
import importlib
import io
import json
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import test_jh006_code_delivery as fixture_tests
import test_jh006_review_regressions as review_tests

R = "test_jh006_review_regressions.TestReviewedDeliveryBoundaries."
F = "test_jh006_code_delivery."
# name, module, unique source anchor, replacement, observable regression
MUTATIONS = [
    ("payload_delivery_marker", "src.orchestrator",
     '        if "_delivery" in (task_payload.get("metadata") or {}):',
     '        if False:', R + "test_input_cannot_claim_applied_or_waiting_delivery"),
    ("cli_delivery_marker", "src.cli", '    if "_delivery" in meta:',
     '    if False:', R + "test_cli_rejects_delivery_marker_before_assembly"),
    ("unregistered_host_record", "src.orchestrator",
     '        if not fixture_host_evidence_matches(self.storage, task_id, norm):',
     '        if False:', R + "test_unregistered_receipt_cannot_succeed"),
    ("raw_output_digest", "src.code_delivery",
     '            and sha256_hex(output.encode("utf-8")) == receipt["tests"]["output_digest"])',
     '            and True)', R + "test_host_output_digest_must_match_persisted_raw_output"),
    ("strict_uncertainty_bool", "src.code_delivery",
     '    if type(receipt["uncertain"]) is not bool:',
     '    if receipt["uncertain"] not in (True, False):', R + "test_uncertainty_requires_bool_not_zero_or_one"),
    ("fixed_check_script", "src.orchestrator",
     '        if norm["tests"]["check_script"] != expected_script:',
     '        if False:', R + "test_wrong_script_path_is_not_a_verified_check"),
    ("sealed_check_command", "src.code_delivery",
     '        checks = json.loads(sealed_fixture_bytes(template, "checks_rel"))',
     '        checks = json.loads((_repo_root() / template["fixture_dir"] / "checks.json").read_text(encoding="utf-8"))',
     R + "test_unsealed_check_command_is_refused"),
    ("prepare_owner", "src.orchestrator",
     '    def prepare_code_delivery(self, task_id: str, artifact_version: int) -> Dict[str, Any]:\n        owner_id = uuid.uuid4().hex\n        if not self.storage.claim_task(task_id, owner_id):',
     '    def prepare_code_delivery(self, task_id: str, artifact_version: int) -> Dict[str, Any]:\n        owner_id = uuid.uuid4().hex\n        if False:',
     R + "test_prepare_respects_existing_owner"),
    ("pending_approval", "src.orchestrator",
     '        if self.storage.get_control(task_id).get("pending_json"):',
     '        if False:', R + "test_prepare_rechecks_pending_approval"),
    ("interrupted_tool", "src.orchestrator",
     '        if any(t.get("status") in uncertain for t in self.storage.list_tool_executions(task_id)):',
     '        if False:', R + "test_prepare_rechecks_interrupted_tool"),
    ("prepare_transaction", "src.orchestrator",
     '            with self.storage.transaction():\n                return self._prepare_code_delivery_locked(task_id, artifact_version)',
     '            with __import__("contextlib").nullcontext():\n                return self._prepare_code_delivery_locked(task_id, artifact_version)',
     R + "test_prepare_and_event_rollback_together"),
    ("authorized_workspace_scope", "src.orchestrator",
     '        if unauthorized or scope_after.get(tpl["authorized_target"]) != actual_sha:',
     '        if scope_after.get(tpl["authorized_target"]) != actual_sha:',
     R + "test_extra_file_change_never_satisfies_authorized_scope"),
    ("no_model_tools", "src.orchestrator",
     '            if proposed_requests and is_code_delivery_task(task):',
     '            if False:', R + "test_fixture_model_tools_never_execute"),
    ("generation_baseline_context", "src.orchestrator",
     '        if is_code_delivery_task(task):\n            tpl = template_for_task(task)\n            context["code_template"] = {',
     '        if False:\n            tpl = template_for_task(task)\n            context["code_template"] = {',
     R + "test_generation_receives_template_and_sealed_baseline"),
    ("review_deferred_scope", "src.orchestrator",
     '                if is_code_delivery_task(task):\n                    tpl = template_for_task(task)\n                    independent_review_context["code_review_scope"] = {',
     '                if False:\n                    tpl = template_for_task(task)\n                    independent_review_context["code_review_scope"] = {',
     R + "test_review_scope_defers_unexecuted_host_checks"),
    ("fresh_cancel_state", "src.orchestrator",
     '        if task["status"] != "reviewing":\n            raise CodeDeliveryRefused(f"complete requires reviewing, got',
     '        if False:\n            raise CodeDeliveryRefused(f"complete requires reviewing, got',
     R + "test_complete_reloads_cancel_after_owner_acquisition"),
    ("prepared_version_field", "src.orchestrator",
     '            if (pinned.get("version") == artifact_version',
     '            if (pinned.get("artifact_version") == artifact_version',
     F + "TestCliDeliveryCommands.test_cli_prepare_and_complete_roundtrip"),
    ("no_extra_model_turn", "src.orchestrator",
     '    def _complete_code_delivery_locked(self, task_id, receipt, tpl, delivery):\n        budget_failed = self._check_budget_or_fail(task_id, before_call=False)',
     '    def _complete_code_delivery_locked(self, task_id, receipt, tpl, delivery):\n        budget_failed = self._check_budget_or_fail(task_id)',
     R + "test_control_completion_does_not_reserve_an_extra_model_turn"),
    ("final_deadline_gate", "src.orchestrator",
     '        stopped = self._check_budget_or_fail(task_id, before_call=False)\n        if stopped is not None:\n            return stopped\n        criteria = task["acceptance_criteria"]',
     '        stopped = None\n        if stopped is not None:\n            return stopped\n        criteria = task["acceptance_criteria"]',
     R + "test_deadline_is_rechecked_after_verification_reads"),
]


def run_case(name):
    result = unittest.TextTestRunner(stream=io.StringIO()).run(
        unittest.defaultTestLoader.loadTestsFromName(name))
    return result


def main():
    controls = [run_case(row[-1]) for row in MUTATIONS]
    if not all(r.wasSuccessful() and r.testsRun == 1 and not r.skipped for r in controls):
        print(json.dumps({"verdict": "fail", "reason": "control regression failed"}))
        return 1
    rows = []
    for name, module_name, original, replacement, test_name in MUTATIONS:
        live = importlib.import_module(module_name)
        path = Path(live.__file__)
        source = path.read_text(encoding="utf-8")
        if source.count(original) != 1:
            rows.append({"case": name, "detected": False, "harness_error": "anchor not unique"})
            continue
        ns = dict(live.__dict__)
        exec(compile(source.replace(original, replacement, 1), str(path), "exec"), ns)
        with ExitStack() as stack:
            if module_name == "src.orchestrator":
                stack.enter_context(patch.object(fixture_tests, "Orchestrator", ns["Orchestrator"]))
                stack.enter_context(patch("src.cli.Orchestrator", ns["Orchestrator"]))
            elif module_name == "src.cli":
                stack.enter_context(patch.object(live, "stamp_trusted_binding", ns["stamp_trusted_binding"]))
            else:
                # Keep the public exception identity stable across in-memory modules.
                ns["CodeDeliveryRefused"] = live.CodeDeliveryRefused
                for function in ("validate_apply_receipt", "materialized_post_apply_command",
                                 "fixture_host_evidence_matches"):
                    stack.enter_context(patch.object(live, function, ns[function]))
                    stack.enter_context(patch("src.orchestrator." + function, ns[function]))
                    if hasattr(fixture_tests, function):
                        stack.enter_context(patch.object(fixture_tests, function, ns[function]))
            result = run_case(test_name)
        rows.append({"case": name, "detected": bool(result.failures) and not result.errors,
                     "failures": len(result.failures), "errors": len(result.errors)})
    passed = all(row["detected"] for row in rows)
    print(json.dumps({"scope": "offline_fixture_only", "api_calls": 0,
                      "controls": len(controls), "mutations": len(rows),
                      "detected": sum(row["detected"] for row in rows),
                      "rows": rows, "verdict": "pass" if passed else "fail"}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
