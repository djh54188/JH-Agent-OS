"""Phase 3 rectification #3: REAL repair closed loop with a fixture-injected defect.

Chain under test (all model calls real, no retries, original budget, max 2 repairs):
  real Gemini generates artifact
  -> TEST FIXTURE injects a defect into the FIRST artifact (clearly recorded;
     never attributed to the model; original + injected content and hashes kept)
  -> real Muse reviews and must issue revise with concrete evidence
  -> real Gemini regenerates from the repair context (reviewer issues + current
     artifact + acceptance failures) - a genuine model-driven fix
  -> control layer re-accepts
  -> real Muse reviews the new version -> pass -> final succeeded.

Evidence is persisted to a project SQLite file (re-queryable offline via
`python -m src.cli --db <db> artifact <task_id>`) and to
reports/phase3-repair-verification.json, including FULL artifact contents,
review issues, per-attempt usage, and the strict closed-loop gate result.
Exit code is non-zero unless the whole chain (revise -> repair -> pass ->
succeeded) actually happened.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jsonschema import Draft7Validator, FormatChecker
from src.acceptance import transform_acceptance_evaluator
from src.adapters import AdapterError, SchemaValidationError
from src.control_guards import verify_closed_loop_success
from src.model_config import load_model_adapter
from src.orchestrator import Orchestrator
from src.storage import TaskStorage

REVIEW_SCHEMA = json.loads((ROOT / "docs/schemas/review_result.v1.json").read_text(encoding="utf-8"))


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FixtureInjectedWorker:
    """Delegating wrapper around the REAL Gemini worker adapter.

    Only the FIRST artifact generation's content is tampered, by the test
    fixture and with a full before/after record. Planning calls, the repair
    generation, and health checks pass through untouched, so the repair turn is
    genuinely model-driven from Muse's evidence.
    """

    def __init__(self, real):
        self.real = real
        self.injections = []

    def __getattr__(self, name):
        return getattr(self.real, name)

    def invoke_structured(self, messages, schema, **kwargs):
        response = self.real.invoke_structured(messages, schema, **kwargs)
        if schema.get("title") == "ArtifactSchemaV1" and not self.injections:
            envelope = dict(response.parsed_json)
            original_content = envelope["content"]
            injected_content, change_desc = inject_defect(original_content)
            envelope["content"] = injected_content
            response.parsed_json = envelope
            self.injections.append({
                "injected_by": "test fixture (scripts/verify_phase3_repair.py); NOT model-generated",
                "change": change_desc,
                "original_content": original_content,
                "original_sha256": sha256_text(original_content),
                "injected_content": injected_content,
                "injected_sha256": sha256_text(injected_content),
            })
        return response


def inject_defect(content):
    """Deterministically break the ascending region order by swapping the first
    two records. Returns (injected_content, change_description)."""
    try:
        produced = json.loads(content)
    except ValueError:
        marker = "\n# INJECTED_DEFECT: malformed line added by test fixture"
        return content + marker, "appended a marker line (content was not JSON)"
    if isinstance(produced, list) and len(produced) >= 2:
        injected = [produced[1], produced[0]] + produced[2:]
        return (json.dumps(injected, ensure_ascii=False),
                "swapped the first two records, breaking ascending region order")
    if isinstance(produced, dict):
        injected = dict(produced)
        injected["injected_defect"] = True
        return (json.dumps(injected, ensure_ascii=False),
                "added an injected_defect key to the JSON object")
    return content + "\nINJECTED_DEFECT", "appended an INJECTED_DEFECT line"


def main():
    worker = load_model_adapter("worker")
    reviewer = load_model_adapter("reviewer")
    worker_health = worker.check_health()
    reviewer_health = reviewer.check_health()

    report = {
        "timestamp": utc_now(),
        "phase": 3,
        "rectification": 3,
        "is_mock": False,
        "counts_as_acceptance_evidence": True,
        "worker": {"provider": worker.config.get("provider"), "api_protocol": worker.config.get("api_protocol"),
                   "model_id": worker.model_id, "healthy": worker_health.get("healthy")},
        "reviewer": {"provider": reviewer.config.get("provider"), "api_protocol": "responses",
                     "model_id": reviewer.model_id, "healthy": reviewer_health.get("healthy")},
    }
    if not (worker_health.get("healthy") and reviewer_health.get("healthy")):
        report.update({"status": "BLOCKED_ENDPOINT_UNAVAILABLE", "passed": False})
        out = ROOT / "reports" / "phase3-repair-verification.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": report["status"], "report": str(out)}))
        return 1

    input_data = {"records": [
        {"region": "east", "sales": 30}, {"region": "west", "sales": 10},
        {"region": "north", "sales": 20},
    ]}
    expected = sorted(input_data["records"], key=lambda r: (r["region"], r["sales"]))
    task_id = "task_p3_repair_loop1"
    task = {
        "schema_version": "1.0.0",
        "task_id": task_id,
        "title": "Sort sales records by region (repair chain)",
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
            "routing_basis": "Phase 3 repair chain: real Gemini worker + real Muse reviewer",
        },
        # Same budget shape as the primary loop; max 2 repairs come from routing.
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

    db_path = ROOT / "data" / "phase3-repair-loop.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()  # reproducible run: fresh DB per invocation

    injected_worker = FixtureInjectedWorker(worker)
    orch = Orchestrator(storage=TaskStorage(db_path), worker_adapter=injected_worker,
                        reviewer_adapter=reviewer,
                        acceptance_evaluator=transform_acceptance_evaluator(expected))
    orch.submit_task(task)
    result = orch.run_task(task_id)
    status = orch.get_task_status(task_id)
    versions = orch.storage.list_artifact_versions(task_id)
    bindings = orch.storage.list_review_bindings(task_id)
    attempts = orch.storage.list_model_attempts(task_id)
    usage_events = [
        {"attempt_id": e["details"].get("attempt_id"), "actor_role": e["actor_role"],
         "tokens_used": e["tokens_used"], "tokens_status": e["details"].get("tokens_status")}
        for e in orch.list_task_events(task_id) if e["event_type"] == "usage_recorded"
    ]
    gate_error = None
    try:
        verify_closed_loop_success(result, orch.storage.get_latest_artifact(task_id), bindings, status)
    except SchemaValidationError as exc:
        gate_error = str(exc)
    orch.storage.close()

    chain_shape_ok = (
        len(versions) == 2
        and [v["version"] for v in versions] == [1, 2]
        and len(bindings) == 2
        and bindings[0]["review"]["verdict"] in {"revise", "reject"}
        and bindings[1]["review"]["verdict"] == "pass"
        and result["status"] == "succeeded"
        and status["repair_count"] == 1
        and gate_error is None
    )
    revision_ok = (
        len(bindings) >= 1
        and bindings[0]["review"]["verdict"] in {"revise", "reject"}
        and bool(bindings[0]["review"].get("issues"))
        and all(bool(i.get("evidence")) for i in bindings[0]["review"]["issues"])
    )
    repair_actually_changed = (
        len(versions) == 2 and versions[0]["sha256"] != versions[1]["sha256"]
    )

    report.update({
        "status": "completed",
        "injection": injected_worker.injections[0] if injected_worker.injections else None,
        "result": {
            "final_status": result["status"],
            "review_verdict": result.get("review_verdict"),
            "repair_count": status["repair_count"],
            "turns_used": status["turns_used"],
            "tokens_used": status["tokens_used"],
            "tokens_unknown_count": status["tokens_unknown_count"],
            "error_info": result.get("error_info"),
        },
        "artifact_versions": [
            {"version": v["version"], "sha256": v["sha256"], "artifact_type": v["artifact_type"],
             "content": v["content"], "generation_attempt_id": v["generation_attempt_id"]}
            for v in versions
        ],
        "review_bindings": [
            {"review_id": b["review_id"], "attempt_id": b["attempt_id"],
             "review": b["review"], "acceptance": b["acceptance"]}
            for b in bindings
        ],
        "model_attempts": [
            {"attempt_id": a["attempt_id"], "actor_role": a["actor_role"], "status": a["status"]}
            for a in attempts
        ],
        "usage_per_attempt": usage_events,
        "closed_loop_gate": gate_error or "passed",
        "checks": {
            "muse_found_injected_defect_with_evidence": revision_ok,
            "gemini_repair_changed_the_artifact": repair_actually_changed,
            "re_review_pass_and_succeeded": result["status"] == "succeeded" and result.get("review_verdict") == "pass",
            "strict_closed_loop_gate": gate_error is None,
            "chain_shape_two_versions_two_reviews_one_repair": chain_shape_ok,
        },
    })
    report["passed"] = all(report["checks"].values())

    out = ROOT / "reports" / "phase3-repair-verification.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "passed": report["passed"],
        "final_status": result["status"],
        "review_verdicts": [b["review"]["verdict"] for b in bindings],
        "repair_count": status["repair_count"],
        "tokens_used": status["tokens_used"],
        "checks": report["checks"],
        "report": str(out),
        "db": str(db_path),
    }, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
