"""JH-020 Codex AgentSessionAdapter guard verification by targeted mutation.

Each guard the adapter relies on is disabled alone with a targeted patch, and
its regression test must then FAIL on an assertion (an error never counts). A
baseline run of every test (unpatched) must pass first, so a green result cannot
come from a broken harness.

Patches replace single callables/attributes by name, so a mutant never fails
just because an in-memory module copy changed identity. No source file, no
process, and no model call is involved.
"""
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

import src.agent_session_codex as codex_mod
import test_jh020_codex_agent_session as jh020_tests

T = "test_jh020_codex_agent_session."
R1 = T + "TestR1FactsBoundToTurn."
R2 = T + "TestR2DurableRegistration."
R2A = T + "TestR2ARegistryFailClosed."
R2ASHAPE = T + "TestR2ARecordShapeValidation."
R2B = T + "TestR2BRawEvidenceBytes."
R3 = T + "TestR3AddressAndConstraints."
NEG = T + "TestGuardsNegativeControls."


def _swallowing_read(path):
    # The pre-rectification behaviour: any read error is treated as "no history".
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def _skipping_parse(text):
    # The pre-rectification behaviour: malformed records are silently skipped.
    records = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


def _text_mode_write(path, data):
    # Re-introduces the newline-translation bug: disk bytes no longer equal the hash.
    with open(path, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write(data.decode("utf-8"))


def _lenient_record_validation(record):
    # Pre-rectification shape check: only the key NAMES are required, so a null
    # run_id or an absent idempotency_key field slips through the validator.
    if not isinstance(record, dict) or "run_id" not in record or "event" not in record:
        raise codex_mod.RegistryUnavailable(
            "registry history is corrupt: a record is missing its run_id or event")
    return record


# name, patchers, guard regression that must fail
MUTATIONS = [
    ("completion_association_ignored",
     [patch.object(codex_mod, "completion_association", new=lambda params, thread_id, turn_id: (True, True))],
     NEG + "test_broken_transport_foreign_thread_is_rejected_by_the_adapter"),
    ("completion_status_ignored",
     [patch.object(codex_mod, "completion_status_ok", new=lambda completed: True)],
     NEG + "test_completion_with_failed_status_is_not_declared_complete"),
    ("idempotency_conflict_ignored",
     [patch.object(codex_mod, "fingerprint_matches", new=lambda stored, current: True)],
     T + "TestIdempotency.test_same_key_different_request_conflicts"),
    ("address_binding_ignored",
     [patch.object(codex_mod, "address_matches", new=lambda run_binding, wanted: True)],
     NEG + "test_address_mismatch_is_raised_for_status_and_collect"),
    ("unobserved_capability_claimed",
     [patch.object(codex_mod.CodexAgentSessionAdapter, "SUPPORTED",
                   new=frozenset({"capabilities", "submit", "status", "collect", "continue", "cancel"}))],
     T + "TestCapabilities.test_declares_every_capability_and_never_claims_unobserved"),
    # R1 — facts must bind to the exact native thread AND turn
    ("notification_binding_ignored",
     [patch.object(codex_mod, "notification_binding", new=lambda params, thread_id, turn_id: (True, True))],
     R1 + "test_same_thread_wrong_turn_facts_are_rejected"),
    # R2 — durable pre-registration and no redispatch
    ("registry_reload_ignored",
     [patch.object(codex_mod.CodexAgentSessionAdapter, "_load_registry", new=lambda self: None)],
     R2 + "test_reopened_registry_blocks_redispatch_of_an_uncertain_key"),
    ("registry_write_failure_swallowed",
     [patch.object(codex_mod, "append_record", new=lambda path, record: None)],
     R2 + "test_registry_write_failure_at_dispatch_refuses_before_any_call"),
    # R3 — address, model and budget constraints
    ("new_session_address_guard_ignored",
     [patch.object(codex_mod, "is_new_session_request", new=lambda session_id: True)],
     R3 + "test_existing_or_designated_session_address_is_refused"),
    ("model_honored_check_ignored",
     [patch.object(codex_mod, "model_is_honored", new=lambda requested, reported: True)],
     R3 + "test_unhonored_explicit_model_is_refused"),
    ("budget_refusal_ignored",
     [patch.object(codex_mod, "budget_is_dispatchable", new=lambda budget_tokens: True)],
     R3 + "test_finite_budget_is_refused_before_dispatch"),
    # R2-A — an unusable registry history must refuse dispatch, never be skipped
    ("registry_read_failure_swallowed",
     [patch.object(codex_mod, "read_registry_text", new=_swallowing_read)],
     R2A + "test_unreadable_registry_refuses_dispatch_before_any_call"),
    ("registry_corruption_skipped",
     [patch.object(codex_mod, "parse_registry_text", new=_skipping_parse)],
     R2A + "test_corrupt_registry_refuses_dispatch_before_any_call"),
    # R2-B — the digest must describe the bytes on disk
    ("raw_evidence_written_as_text",
     [patch.object(codex_mod, "write_bytes_file", new=_text_mode_write)],
     R2B + "test_digest_matches_the_actual_file_bytes"),
    ("raw_evidence_byte_check_ignored",
     [patch.object(codex_mod, "verify_raw_evidence", new=lambda raw: (True, "")),
      patch.object(jh020_tests, "verify_raw_evidence", new=lambda raw: (True, ""))],
     R2B + "test_tampered_bytes_fail_verification"),
    # R2-A — record shape/field validation
    ("record_validation_bypassed",
     [patch.object(codex_mod, "validate_registry_record", new=lambda record: record)],
     R2ASHAPE + "test_null_run_id_refuses_dispatch_without_rewriting"),
    ("missing_idempotency_field_defaulted",
     [patch.object(codex_mod, "validate_registry_record", new=_lenient_record_validation)],
     R2ASHAPE + "test_missing_idempotency_key_field_refuses_dispatch_without_rewriting"),
]


def run_case(name):
    return unittest.TextTestRunner(stream=io.StringIO()).run(
        unittest.defaultTestLoader.loadTestsFromName(name))


def main():
    controls = [run_case(row[-1]) for row in MUTATIONS]
    if not all(r.wasSuccessful() and r.testsRun == 1 and not r.skipped for r in controls):
        failing = [row[0] for row, r in zip(MUTATIONS, controls) if not r.wasSuccessful()]
        print(json.dumps({"verdict": "fail", "reason": "baseline control failed", "cases": failing}))
        return 1
    rows = []
    for name, patchers, test_name in MUTATIONS:
        with ExitStack() as stack:
            for patcher in patchers:
                stack.enter_context(patcher)
            result = run_case(test_name)
        detected = bool(result.failures) and not result.errors
        rows.append({"case": name, "detected": detected,
                     "failures": len(result.failures), "errors": len(result.errors)})
    passed = all(row["detected"] for row in rows)
    print(json.dumps({"scope": "jh020_codex_adapter_offline_only", "model_calls": 0,
                      "processes": 0, "controls": len(controls), "mutations": len(rows),
                      "detected": sum(row["detected"] for row in rows),
                      "rows": rows, "verdict": "pass" if passed else "fail"}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
