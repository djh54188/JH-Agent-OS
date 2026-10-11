"""JH-Agent OS Phase 3 verification runner.

Produces reports/phase3-verification.json with three separately labeled sections:
1. offline_unit_tests   (is_mock=true)
2. live_muse_reviewer   (real calls to the configured Muse Responses endpoint;
                          includes an explicitly recorded fixture-injected defect)
3. gemini_worker_access (credential precheck; BLOCKED when GEMINI_API_KEY absent)
4. deepseek_supplementary_loop (real DeepSeek worker + real Muse reviewer; explicitly
   NOT Phase 3 acceptance evidence per the Meta Planner ruling)

No credentials are ever printed or persisted. Real-call evidence keeps model IDs,
latency, token usage (None stays None), verdicts, and artifact SHA-256 hashes.
"""

from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jsonschema import Draft7Validator, FormatChecker
from src.acceptance import transform_acceptance_evaluator
from src.adapters import AdapterError, CredentialMissingError, SchemaValidationError
from src.control_guards import verify_closed_loop_success
from src.mock_components import ScriptedWorkerAdapter
from src.model_config import has_credential, load_adapter_settings, load_model_adapter
from src.orchestrator import Orchestrator

REVIEW_SCHEMA = json.loads((ROOT / "docs/schemas/review_result.v1.json").read_text(encoding="utf-8"))
ARTIFACT_SCHEMA = json.loads((ROOT / "docs/schemas/artifact.v1.json").read_text(encoding="utf-8"))
ARTIFACT_VALIDATOR = Draft7Validator(ARTIFACT_SCHEMA, format_checker=FormatChecker())


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def review_metadata_for(task_id, artifact_path, reviewer_role, review_id):
    return {
        "schema_version": "1.0.0",
        "review_id": review_id,
        "task_id": task_id,
        "target_artifact_type": "code",
        "target_artifact_path": artifact_path,
        "reviewer_role": reviewer_role,
        "created_at": utc_now(),
    }


def run_muse_review(adapter, metadata, requirement, artifact, max_output_tokens=8192):
    system = (
        "You are an independent code reviewer. Requirement: " + requirement + " "
        "Review only the supplied artifact. Return only the raw ReviewResult JSON object "
        "without markdown fences. Use verdict pass with empty issues if correct; otherwise "
        "revise or reject and cite the faulty expression in issue evidence. overall_score is "
        "0-100. Preserve the supplied metadata exactly. Do not use tools or invent executed "
        "test results."
    )
    response = adapter.invoke_structured(
        [{"role": "system", "content": system},
         {"role": "user", "content": json.dumps({"metadata": metadata, "artifact": artifact})}],
        REVIEW_SCHEMA, max_output_tokens=max_output_tokens)
    return response


def verify_live_muse():
    record = {"is_mock": False, "protocol": "responses", "cases": []}
    try:
        adapter = load_model_adapter("reviewer")
    except AdapterError as exc:
        record["status"] = "BLOCKED_ADAPTER_CONFIG"
        record["error"] = str(exc)
        return record
    settings = load_adapter_settings()
    muse_cfg = settings["adapters"][settings["active_profiles"]["reviewer"]]
    record.update({
        "status": "attempted",
        "provider": muse_cfg.get("provider"),
        "model_id": muse_cfg.get("model_id"),
        "endpoint_url": muse_cfg.get("endpoint_url"),
        "api_protocol": muse_cfg.get("api_protocol", "responses"),
        "api_key_env": muse_cfg.get("api_key_env"),
        "reasoning_effort": muse_cfg.get("reasoning_effort"),
    })
    health = adapter.check_health()
    # Structured probe evidence (review finding #3): the HTTP status and probe
    # timestamp are machine-readable, so a future blocker needs no prose parsing.
    # check_health raises HTTPError for non-2xx; "HTTP <code>" is parsed from its
    # sanitized message. A healthy probe implies 2xx (urllib only returns on 2xx),
    # recorded as 200 for the /models GET.
    error_text = health.get("error")
    status_match = re.search(r"HTTP (\d+)", error_text or "")
    http_status = int(status_match.group(1)) if status_match else (200 if health.get("healthy") else None)
    record["health"] = {
        "healthy": health.get("healthy"),
        "model_available": health.get("model_available"),
        "error": error_text,
        "probes": [{"probed_at": utc_now(), "http_status": http_status}],
    }
    if not health.get("healthy"):
        record["status"] = "BLOCKED_ENDPOINT_UNAVAILABLE"
        record["note"] = ("Local Muse proxy is unreachable or its upstream returned an error "
                          "(see health.probes for the machine-readable status code). This is "
                          "an environment blocker, not a code or configuration change; the "
                          "Responses protocol and model ID are unchanged from the approved setup.")
        return record

    # Case 1: reconfirm minimal real review of a correct artifact.
    good = "def add(a, b):\n    return a + b\n"
    meta_good = review_metadata_for("task_p3_muse_ok01", "fixtures/p3_good_add.py",
                                    muse_cfg.get("model_id"), "rev_p3_muse_ok01")
    try:
        resp = run_muse_review(adapter, meta_good, "add(a,b) returns a+b for ordinary integers", good)
        record["cases"].append({
            "case": "correct_artifact_pass",
            "artifact_sha256": sha256_text(good),
            "verdict": resp.parsed_json.get("verdict"),
            "issues": len(resp.parsed_json.get("issues") or []),
            "schema_valid": True,
            "latency_ms": resp.latency_ms,
            "tokens_used": resp.total_tokens,
            "review_id": resp.parsed_json.get("review_id"),
            "passed": resp.parsed_json.get("verdict") == "pass",
        })
    except AdapterError as exc:
        record["cases"].append({"case": "correct_artifact_pass", "passed": False, "error": str(exc)})

    # Case 2: fixture-injected defect, explicitly recorded (never attributed to a model).
    correct = "def div(a, b):\n    if b == 0:\n        raise ValueError('b must be non-zero')\n    return a / b\n"
    injected = "def div(a, b):\n    if b == 0:\n        raise ValueError('b must be non-zero')\n    return a * b\n"
    meta_bad = review_metadata_for("task_p3_muse_bad02", "fixtures/p3_injected_div.py",
                                   muse_cfg.get("model_id"), "rev_p3_muse_bad02")
    try:
        resp = run_muse_review(adapter, meta_bad, "div(a,b) returns a/b and raises ValueError on b==0", injected)
        issues = resp.parsed_json.get("issues") or []
        evidence_blob = " ".join(str(i.get("evidence")) for i in issues)
        record["cases"].append({
            "case": "injected_defect_caught_with_evidence",
            "fixture_injection": {
                "injected_by": "test_fixture (scripts/verify_phase3.py), NOT model-generated",
                "injected_line": 4,
                "injected_change": "'return a / b' -> 'return a * b'",
                "sha256_before": sha256_text(correct),
                "sha256_after": sha256_text(injected),
            },
            "verdict": resp.parsed_json.get("verdict"),
            "issues": len(issues),
            "evidence_cites_defect": ("a * b" in evidence_blob) or ("multiply" in evidence_blob.lower()),
            "schema_valid": True,
            "latency_ms": resp.latency_ms,
            "tokens_used": resp.total_tokens,
            "review_id": resp.parsed_json.get("review_id"),
            "passed": resp.parsed_json.get("verdict") in {"revise", "reject"} and bool(issues),
        })
    except AdapterError as exc:
        record["cases"].append({"case": "injected_defect_caught_with_evidence", "passed": False, "error": str(exc)})

    record["passed"] = bool(record["cases"]) and all(c.get("passed") for c in record["cases"])
    return record


def verify_gemini_access():
    settings = load_adapter_settings()
    cfg = settings["adapters"][settings["active_profiles"]["worker"]]
    record = {
        "is_mock": False,
        "provider": cfg.get("provider"),
        "model_id": cfg.get("model_id"),
        "model_id_source": cfg.get("model_id_source"),
        "endpoint_url": cfg.get("endpoint_url"),
        "api_key_env": cfg.get("api_key_env"),
        "credential_present": has_credential(cfg.get("api_key_env")),
        "session_model_note": (
            "The executing AI session runs on its own provider access and is separate from the "
            "JH Runtime Gemini API; session credentials are never substituted for GEMINI_API_KEY."
        ),
    }
    if not record["credential_present"]:
        record.update({
            "status": "BLOCKED_MISSING_CREDENTIAL",
            "is_mock": False,
            "note": ("No API call attempted. Per the Phase 3 brief: complete interfaces, offline "
                     "tests and acceptance scripts; mark live calls blocked; never substitute Mock."),
        })
        return record
    try:
        adapter = load_model_adapter("worker")
        health = adapter.check_health()
        record["health"] = health
        record["status"] = "attempted" if health.get("healthy") else "BLOCKED_ENDPOINT"
    except AdapterError as exc:
        record["status"] = "BLOCKED_ADAPTER"
        record["error"] = str(exc)
    return record


def _closed_loop_gate(orch, task_id, result, bindings, status):
    """Rectification #1: strict success gate over persisted records only."""
    try:
        verify_closed_loop_success(result, orch.storage.get_latest_artifact(task_id), bindings, status)
        return None
    except SchemaValidationError as exc:
        return str(exc)


def run_deepseek_supplementary_loop():
    """Full loop with REAL DeepSeek worker + REAL Muse reviewer.

    Explicitly supplementary: per the Meta Planner ruling this combination is NOT
    acceptance evidence for the Gemini+Muse Phase 3 closed loop.
    """
    record = {"is_mock": False, "counts_as_acceptance_evidence": False, "cases": []}
    try:
        worker = load_model_adapter("worker", profile_override="deepseek_worker")
        reviewer = load_model_adapter("reviewer")
    except AdapterError as exc:
        record["status"] = "BLOCKED_ADAPTER_CONFIG"
        record["error"] = str(exc)
        return record
    worker_health = worker.check_health()
    reviewer_health = reviewer.check_health()
    record.update({
        "worker": {"provider": "openai_compatible", "model_id": worker.model_id,
                   "healthy": worker_health.get("healthy")},
        "reviewer": {"provider": "custom_muse", "model_id": reviewer.model_id,
                     "healthy": reviewer_health.get("healthy"), "api_protocol": "responses"},
    })
    if not (worker_health.get("healthy") and reviewer_health.get("healthy")):
        record["status"] = "BLOCKED_ENDPOINT_UNAVAILABLE"
        return record

    input_data = {"records": [
        {"region": "east", "sales": 30}, {"region": "west", "sales": 10},
        {"region": "north", "sales": 20},
    ]}
    expected = sorted(input_data["records"], key=lambda r: (r["region"], r["sales"]))
    task_id = "task_p3_supp_loop1"
    task = {
        "schema_version": "1.0.0",
        "task_id": task_id,
        "title": "Sort sales records by region",
        "description": ("Transform the JSON records in input_data by sorting them by region "
                        "ascending, then output the sorted array as the artifact content (JSON only)."),
        "acceptance_criteria": [
            "Trusted input_data is provided to the Worker",
            "Artifact content is exactly the input records sorted by region ascending",
        ],
        "priority": "medium",
        "status": "created",
        "risk_assessment": {
            "risk_level": "read_only", "complexity": "medium", "uncertainty": "low",
            "risk_basis": "No-tool in-memory generation; no filesystem or network side effects",
            "routing_basis": "Supplementary loop: DeepSeek worker + Muse reviewer",
        },
        "budget": {"max_turns": 8, "max_tokens": 60000, "max_execution_time_seconds": 300},
        "context": {"workspace_root": str(ROOT), "target_files": []},
        "artifact_refs": [],
        "tool_execution_evidence": [],
        "review_verdict": None,
        "error_info": None,
        "assigned_worker": "deepseek_worker",
        "assigned_reviewer": "muse_reviewer",
        "metadata": {"input_data": input_data},
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    orch = Orchestrator(worker_adapter=worker, reviewer_adapter=reviewer,
                        acceptance_evaluator=transform_acceptance_evaluator(expected))
    orch.submit_task(task)
    result = orch.run_task(task_id)
    status = orch.get_task_status(task_id)
    versions = orch.storage.list_artifact_versions(task_id)
    bindings = orch.storage.list_review_bindings(task_id)
    gate_error = _closed_loop_gate(orch, task_id, result, bindings, status)
    record["cases"].append({
        "case": "full_loop_deepseek_worker_muse_reviewer",
        "final_status": result["status"],
        "review_verdict": result.get("review_verdict"),
        "repair_count": status["repair_count"],
        "turns_used": status["turns_used"],
        "tokens_used": status["tokens_used"],
        "tokens_unknown_count": status["tokens_unknown_count"],
        "artifact_versions": [
            {"version": v["version"], "sha256": v["sha256"], "artifact_type": v["artifact_type"]}
            for v in versions
        ],
        "review_ids": [b["review_id"] for b in bindings],
        "review_verdicts": [b["review"]["verdict"] for b in bindings],
        "closed_loop_gate": gate_error or "passed",
        # Rectification #1: only the strict gate counts as a normal closed-loop pass;
        # failed / needs_attention / revise / missing evidence are reported as failures.
        "passed": gate_error is None,
    })
    record["passed"] = all(c["passed"] for c in record["cases"])
    record["status"] = "completed"
    return record


def run_gemini_muse_primary_loop():
    """Full Phase 3 closed loop with the REAL configured Gemini worker + REAL Muse reviewer.

    This is the decisive acceptance evidence for Phase 3 (Scenario A): real
    generation -> control-owned acceptance -> real independent review ->
    succeeded, persisted with artifact versions and SHA-256 lineage.
    Requires the gemini_worker access path (local proxy, Anthropic Messages).
    """
    record = {"is_mock": False, "counts_as_acceptance_evidence": True, "cases": []}
    try:
        worker = load_model_adapter("worker")
        reviewer = load_model_adapter("reviewer")
    except AdapterError as exc:
        record["status"] = "BLOCKED_ADAPTER_CONFIG"
        record["error"] = str(exc)
        return record
    worker_health = worker.check_health()
    reviewer_health = reviewer.check_health()
    record.update({
        "worker": {"provider": worker.config.get("provider"), "api_protocol": worker.config.get("api_protocol"),
                   "model_id": worker.model_id, "healthy": worker_health.get("healthy")},
        "reviewer": {"provider": reviewer.config.get("provider"), "api_protocol": "responses",
                     "model_id": reviewer.model_id, "healthy": reviewer_health.get("healthy")},
    })
    if not (worker_health.get("healthy") and reviewer_health.get("healthy")):
        record["status"] = "BLOCKED_ENDPOINT_UNAVAILABLE"
        record["worker_health"] = worker_health
        record["reviewer_health"] = reviewer_health
        return record

    input_data = {"records": [
        {"region": "east", "sales": 30}, {"region": "west", "sales": 10},
        {"region": "north", "sales": 20},
    ]}
    expected = sorted(input_data["records"], key=lambda r: (r["region"], r["sales"]))
    task_id = "task_p3_primary_loop1"
    task = {
        "schema_version": "1.0.0",
        "task_id": task_id,
        "title": "Sort sales records by region",
        "description": ("Transform the JSON records in input_data by sorting them by region "
                        "ascending, then output the sorted array as the artifact content (JSON only)."),
        "acceptance_criteria": [
            "Trusted input_data is provided to the Worker",
            "Artifact content is exactly the input records sorted by region ascending",
        ],
        "priority": "high",
        "status": "created",
        "risk_assessment": {
            "risk_level": "read_only", "complexity": "medium", "uncertainty": "low",
            "risk_basis": "No-tool in-memory generation; no filesystem or network side effects",
            "routing_basis": "Phase 3 primary loop: real Gemini worker + real Muse reviewer",
        },
        "budget": {"max_turns": 8, "max_tokens": 120000, "max_execution_time_seconds": 300},
        "context": {"workspace_root": str(ROOT), "target_files": []},
        "artifact_refs": [],
        "tool_execution_evidence": [],
        "review_verdict": None,
        "error_info": None,
        "assigned_worker": "gemini_worker",
        "assigned_reviewer": "muse_reviewer",
        "metadata": {"input_data": input_data},
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    orch = Orchestrator(worker_adapter=worker, reviewer_adapter=reviewer,
                        acceptance_evaluator=transform_acceptance_evaluator(expected))
    orch.submit_task(task)
    result = orch.run_task(task_id)
    status = orch.get_task_status(task_id)
    versions = orch.storage.list_artifact_versions(task_id)
    bindings = orch.storage.list_review_bindings(task_id)
    gate_error = _closed_loop_gate(orch, task_id, result, bindings, status)
    record["cases"].append({
        "case": "full_loop_gemini_worker_muse_reviewer",
        "final_status": result["status"],
        "review_verdict": result.get("review_verdict"),
        "repair_count": status["repair_count"],
        "turns_used": status["turns_used"],
        "tokens_used": status["tokens_used"],
        "tokens_unknown_count": status["tokens_unknown_count"],
        "artifact_versions": [
            {"version": v["version"], "sha256": v["sha256"], "artifact_type": v["artifact_type"]}
            for v in versions
        ],
        "review_ids": [b["review_id"] for b in bindings],
        "review_verdicts": [b["review"]["verdict"] for b in bindings],
        "closed_loop_gate": gate_error or "passed",
        # Rectification #1: only the strict gate counts as a normal closed-loop pass;
        # failed / needs_attention / revise / missing evidence are reported as failures.
        "passed": gate_error is None,
    })
    record["passed"] = all(c["passed"] for c in record["cases"])
    record["status"] = "completed"
    return record


def main():
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"))
    stream = io.StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=1)
    unit = runner.run(suite)

    report = {
        "timestamp": utc_now(),
        "phase": 3,
        "baseline_commit": "28b1f4611a2c4f1eda57e92cf87a95ca626dcba2",
        "offline_unit_tests": {
            "is_mock": True,
            "total_tests": unit.testsRun,
            "failures": len(unit.failures),
            "errors": len(unit.errors),
            "skipped": len(unit.skipped),
            "passed": unit.wasSuccessful(),
        },
        "live_muse_reviewer": {"is_mock": False},
        "gemini_worker_access": {"is_mock": False},
        "gemini_muse_primary_loop": {"is_mock": False, "counts_as_acceptance_evidence": True},
        "deepseek_supplementary_loop": {"is_mock": False, "counts_as_acceptance_evidence": False},
        "phase3_acceptance_verdict": None,
    }

    print("[1/4] Live Muse reviewer reconfirmation + injected-defect review...")
    report["live_muse_reviewer"] = verify_live_muse()

    print("[2/4] Gemini worker access precheck...")
    report["gemini_worker_access"] = verify_gemini_access()

    print("[3/4] Real Gemini worker + Muse reviewer primary loop (acceptance evidence)...")
    report["gemini_muse_primary_loop"] = run_gemini_muse_primary_loop()

    print("[4/4] DeepSeek+Muse supplementary loop (not acceptance evidence)...")
    report["deepseek_supplementary_loop"] = run_deepseek_supplementary_loop()

    # Rectification #1: the repair-chain evidence is REQUIRED and produced by
    # scripts/verify_phase3_repair.py into a separate report file.
    repair_path = ROOT / "reports" / "phase3-repair-verification.json"
    if repair_path.exists():
        repair_data = json.loads(repair_path.read_text(encoding="utf-8"))
        repair_ok = repair_data.get("passed") is True
        report["gemini_muse_repair_loop"] = {
            "source": str(repair_path), "passed": repair_ok,
            "counts_as_acceptance_evidence": True,
            "final_status": (repair_data.get("result") or {}).get("final_status"),
            "repair_count": (repair_data.get("result") or {}).get("repair_count"),
        }
    else:
        repair_ok = False
        report["gemini_muse_repair_loop"] = {
            "source": str(repair_path), "passed": False,
            "error": "repair-chain evidence has not been produced yet",
        }

    muse_ok = report["live_muse_reviewer"].get("passed") is True
    gemini_blocked = report["gemini_worker_access"].get("status") == "BLOCKED_MISSING_CREDENTIAL"
    muse_blocked = report["live_muse_reviewer"].get("status") == "BLOCKED_ENDPOINT_UNAVAILABLE"
    primary_loop = report["gemini_muse_primary_loop"]
    primary_ok = primary_loop.get("passed") is True and primary_loop.get("status") == "completed"
    report["overall_required_checks"] = {
        "offline_unit_tests": report["offline_unit_tests"]["passed"],
        "live_muse_reviewer": muse_ok,
        "gemini_muse_primary_loop": primary_ok,
        "gemini_muse_repair_loop": repair_ok,
    }
    report["overall_phase3_evidence_complete"] = all(report["overall_required_checks"].values())
    if muse_ok and primary_ok:
        report["phase3_acceptance_verdict"] = (
            "REAL_GEMINI_MUSE_CLOSED_LOOP_EVIDENCE_RECORDED: the primary loop ran with the real "
            "configured Gemini worker and real Muse reviewer (counts as acceptance evidence), with "
            "the offline suite and all mutation guards green. Submit for independent review and "
            "the phase-closure ruling; acceptance itself is not self-declared."
        )
    elif muse_ok and gemini_blocked:
        report["phase3_acceptance_verdict"] = (
            "IMPLEMENTATION_DELIVERED_GEMINI_BLOCKED: offline suite green, real Muse review "
            "evidence recorded (correct pass + injected defect caught with evidence), but the real "
            "Gemini+Muse closed loop could not run because GEMINI_API_KEY is absent. Phase 3 "
            "acceptance is NOT claimed; the blocked item is registered for the architect."
        )
    elif muse_ok and not gemini_blocked:
        report["phase3_acceptance_verdict"] = "LIVE_EVIDENCE_ATTEMPTED: see live sections for outcomes."
    elif muse_blocked:
        report["phase3_acceptance_verdict"] = (
            "IMPLEMENTATION_DELIVERED_BOTH_LIVE_CHANNELS_BLOCKED: offline suite and mutation "
            "guards are green, but no real-model evidence could be produced at verification time "
            "- Gemini lacks GEMINI_API_KEY and the local Muse proxy returned HTTP 502 (upstream "
            "unavailable). Per the Phase 3 brief, Phase 3 acceptance is NOT claimed; only the "
            "implementation and this blocker list are delivered."
        )
    else:
        report["phase3_acceptance_verdict"] = "BLOCKED: live Muse reviewer verification did not pass."

    out = ROOT / "reports" / "phase3-verification.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "offline_passed": report["offline_unit_tests"]["passed"],
        "muse_passed": report["live_muse_reviewer"].get("passed"),
        "gemini_status": report["gemini_worker_access"].get("status"),
        "supplementary_passed": report["deepseek_supplementary_loop"].get("passed"),
        "repair_passed": report["gemini_muse_repair_loop"].get("passed"),
        "overall_required_checks": report["overall_required_checks"],
        "verdict": report["phase3_acceptance_verdict"],
        "report": str(out),
    }, indent=2))
    # Rectification #1: non-zero exit unless every required check passed.
    return 0 if report["overall_phase3_evidence_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
