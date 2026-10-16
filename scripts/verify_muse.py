"""Run two small live Reviewer checks; save metadata and judgments, never credentials."""
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from src.adapters import AdapterError
from src.model_config import load_model_adapter


def main():
    adapter = load_model_adapter("reviewer")
    schema = json.loads((PROJECT_ROOT / "docs/schemas/review_result.v1.json").read_text(encoding="utf-8"))
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "endpoint_url": adapter.endpoint_url, "protocol": "responses",
        "requested_model": adapter.model_id, "reasoning_effort": adapter.reasoning_effort,
        "configured_context_window_tokens": adapter.config.get("context_window_tokens"),
        "configured_max_output_tokens": adapter.max_output_tokens,
        "capacity_source": "user_provided_not_load_tested", "is_mock": False,
        "health": adapter.check_health(), "cases": [],
    }
    report_path = PROJECT_ROOT / "reports/muse-verification.json"
    if report["health"]["healthy"]:
        for name, artifact in [("correct_addition", "def add(a, b):\n    return a + b\n"),
                               ("incorrect_addition", "def add(a, b):\n    return a - b\n")]:
            metadata = {
                "schema_version": "1.0.0", "review_id": "rev_muse_" + name,
                "task_id": "task_muse_" + name, "target_artifact_type": "code",
                "target_artifact_path": "fixtures/" + name + ".py",
                "reviewer_role": "muse_reviewer",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            system = (
                "You are an independent code reviewer. Requirement: add(a,b) returns a+b "
                "for ordinary integers. Review only the supplied artifact. Return the "
                "ReviewResult JSON object. Use verdict pass with empty issues if correct; "
                "otherwise revise or reject and cite the faulty expression in issue evidence. "
                "overall_score is 0-100. Preserve the supplied metadata exactly. "
                "Do not use tools or invent executed test results."
            )
            case = {"name": name, "artifact": artifact, "passed": False}
            try:
                result = adapter.invoke_structured(
                    [{"role": "system", "content": system},
                     {"role": "user", "content": json.dumps({"metadata": metadata, "artifact": artifact})}],
                    schema, max_output_tokens=8192,
                )
                review = result.parsed_json
                metadata_ok = all(review.get(key) == value for key, value in metadata.items())
                if name == "correct_addition":
                    judgment_ok = review["verdict"] == "pass" and not review["issues"]
                else:
                    evidence = " ".join(issue["evidence"] for issue in review["issues"])
                    judgment_ok = review["verdict"] in {"revise", "reject"} and "a - b" in evidence
                case.update(passed=metadata_ok and judgment_ok, review=review,
                            metadata_matches=metadata_ok, latency_ms=result.latency_ms,
                            usage={"input_tokens": result.tokens_prompt, "output_tokens": result.tokens_completion,
                                   "total_tokens": result.total_tokens},
                            response_metadata=result.raw_response)
            except AdapterError as error:
                case["error"] = str(error)
            report["cases"].append(case)
            print(json.dumps({"case": name, "passed": case["passed"]}), flush=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["passed"] = (report["health"]["healthy"] and len(report["cases"]) == 2
                        and all(case["passed"] for case in report["cases"]))
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "report": str(report_path)}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
