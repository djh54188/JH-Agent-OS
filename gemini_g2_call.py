"""G2 subtask runner: Gemini adversarial review/apply/recover binding analysis (JH-015 / ARCH-05).

Single bounded Gemini call. Provides orchestrator binding code + storage gate
context, asks for binding-break attack scenarios and a standalone verification
probe. Saves result to .test-tmp/g2_result.json. Never modifies shared source.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.model_config import load_model_adapter

ORCH = (ROOT / "src" / "orchestrator.py").read_text(encoding="utf-8")

PROMPT = """You are a security-focused reviewer auditing the review/apply/recover binding logic of a task orchestrator (Python, offline, SQLite-backed). Your job: find ways the binding could be broken, and produce ONE standalone verification probe. You do NOT modify the implementation.

RELEVANT IMPLEMENTATION (src/orchestrator.py, JH-015 hardened):
```python
{orch}
```

HOW THE CHAIN WORKS (verified facts):
- Worker stage_write calls are executed by FileToolExecutor and recorded in tool_executions with structured_data {target_path, staged_path, content_sha256, target_pre_sha256, bytes}.
- produced_artifacts = list of staged_path values; stored as task artifact_refs at the reviewing transition.
- snapshots = executor.snapshot_artifacts(artifact_refs) -> {staged_path: {content, sha256}}; then _verify_staged_snapshots_match_tool_records raises unless each snapshot sha256 equals the LATEST successful stage_write record for that staged_path.
- Reviewer reviews snapshots; review_json + snapshots are passed to _apply_staged_writes, which for the LATEST stage_write per target_path requires reviewed snapshot sha256 == stage record content_sha256, then calls executor.apply_staged with the REVIEWED sha (which re-checks staged bytes on disk == reviewed sha, and target pre-state == target_pre_sha256 on a locked handle).
- After apply, orchestrator re-reads the target from disk and requires disk sha == reviewed sha == applied_sha256, else ApplyBlockedError.
- Success gate in storage.transition_task_state: succeeded requires current review pass + acceptance pass + (if any stage_write exists) a successful apply_staged_write record whose applied_sha256 is in the reviewed artifact sha set.
- recover_interrupted_tasks (storage-level, restart path) NEVER replays: tasks in planning/reviewing/executing/repairing or with unconfirmed side effects go to needs_attention. In-apply SimulatedInterruptionError is classified by executor.recover_apply: applied -> record success; not_applied -> re-apply once with the SAME reviewed binding; uncertain -> needs_attention.
- The Reviewer adapter is untrusted input: validate_review checks its verdict/issues/artifact_hashes/hashes binding, but a PASSING reviewer does not by itself authorize any bytes; authorization comes from the control-owned snapshot + reviewed-sha chain above.

YOUR TASKS:
1. Enumerate up to 6 CONCRETE attack scenarios that attempt to break the binding (each: attacker capability within the documented threat model (worker output tampering, staged-file tampering at any phase, concurrent target edits, reviewer output tampering, restart at any point), exact step sequence, which check should stop it (function:line), and the expected fail-closed outcome (status + disk state)).
2. Identify up to 5 residual binding risks you see in the code above (location, one sentence, severity high/medium/low). Focus on: multi-target tasks where only the latest apply is checked by the success gate; recovered 'applied' path recording applied_sha without re-verifying against the reviewed snapshot; snapshots parameter being None in any reachable call; artifact_refs containing non-staged paths; acceptance evaluator seeing different bytes than the reviewer.
3. Write ONE standalone verification probe script (under 150 lines) that picks the SCARIEST scenario from (1) and proves fail-closed behavior end-to-end using ScriptedWorkerAdapter/ScriptedReviewerAdapter + real Orchestrator + real FileToolExecutor + real TaskStorage(':memory:'), with deterministic tampering (monkeypatching stage_write/apply_staged wrappers or fault_injector is allowed for the ATTACK simulation only). The probe must: create one unique root under --temp-base, keep everything inside it, print one JSON object {probe, status, target_exists, disk_sha, reviewed_sha, defect_reproduced}, exit 0 always, clean up its temp root.
4. Answer: does the current chain satisfy "apply/recover binds to the CURRENT valid review (task/attempt/artifact-version/hash), not to history or path sets"? List the exact chain of comparisons (values compared at each step) that justifies your answer, or the precise gap.

Reply with JSON ONLY, no other text, matching the provided schema.
""".replace("{orch}", ORCH[-45000:])

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "binding_chain": {"type": "array", "items": {"type": "string"}},
        "verdict": {"type": "string"},
        "attacks": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "capability": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "string"}},
                    "stopping_check": {"type": "string"},
                    "expected_outcome": {"type": "string"},
                },
                "required": ["name", "capability", "steps", "stopping_check", "expected_outcome"],
                "additionalProperties": False,
            },
        },
        "risks": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "description": {"type": "string"},
                    "severity": {"type": "string"},
                },
                "required": ["location", "description", "severity"],
                "additionalProperties": False,
            },
        },
        "probe": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "objective": {"type": "string"},
                "code": {"type": "string"},
                "expected": {"type": "string"},
            },
            "required": ["name", "objective", "code", "expected"],
            "additionalProperties": False,
        },
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "binding_chain", "verdict", "attacks", "risks", "probe", "open_questions"],
    "additionalProperties": False,
}

out_path = ROOT / ".test-tmp" / "g2_result.json"
raw_path = ROOT / ".test-tmp" / "g2_raw.txt"
out_path.parent.mkdir(parents=True, exist_ok=True)

worker = load_model_adapter("worker")
full_prompt = PROMPT + "\n\nSCHEMA (return a JSON object with exactly these keys):\n" + json.dumps(SCHEMA, ensure_ascii=False)
resp = worker.invoke(
    [{"role": "user", "content": full_prompt}], max_output_tokens=16384
)
raw_path.write_text(resp.content, encoding="utf-8")
text = resp.content.strip()
if text.startswith("```"):
    text = text.split("\n", 1)[1]
    if text.rstrip().endswith("```"):
        text = text.rstrip()[:-3]
parsed = json.loads(text)
from jsonschema import Draft7Validator
Draft7Validator(SCHEMA).validate(parsed)
record = {
    "model_id": resp.model_id,
    "is_mock": resp.is_mock,
    "latency_ms": resp.latency_ms,
    "tokens_prompt": resp.tokens_prompt,
    "tokens_completion": resp.tokens_completion,
    "total_tokens": resp.total_tokens,
    "payload": parsed,
}
out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
p = parsed or {}
print(json.dumps({
    "saved": str(out_path),
    "model": resp.model_id,
    "is_mock": resp.is_mock,
    "tokens": resp.total_tokens,
    "latency_ms": resp.latency_ms,
    "n_attacks": len(p.get("attacks", [])),
    "attack_names": [a.get("name") for a in p.get("attacks", [])],
    "n_risks": len(p.get("risks", [])),
    "probe": (p.get("probe") or {}).get("name"),
}, ensure_ascii=False, indent=2))
