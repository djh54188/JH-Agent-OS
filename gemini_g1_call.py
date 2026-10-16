"""G1 subtask runner: Gemini adversarial boundary/concurrency probe design (JH-015).

Single bounded Gemini call. Reads src/file_tools.py as context, asks for NEW
adversarial probe scripts (stdlib + FileToolExecutor only, isolated temp dirs).
Saves raw structured result to .test-tmp/g1_result.json. Never modifies shared source.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.model_config import load_model_adapter

SRC = (ROOT / "src" / "file_tools.py").read_text(encoding="utf-8")

PROMPT = """You are a security-focused test engineer reviewing a workspace-bounded file tool executor (Windows, Python, JH-015 hardened version). Your job: design ADVERSARIAL probe scripts that try to break its safety invariants. You do NOT modify the implementation; you only produce probe scripts.

IMPLEMENTATION UNDER TEST (src/file_tools.py):
```python
{src}
```

INVARIANTS TO ATTACK (each probe must assert the invariant holds by checking ACTUAL disk state, not return values alone):
- I1: Any refused operation must leave every file outside the workspace byte-identical (none created, truncated, or modified).
- I2: Pre-existing neighbor files (any name, including temp-like names) must never be overwritten, replaced, or deleted by staging/backup/temp/commit steps.
- I3: If the final target changes after the pre-review version snapshot, apply must refuse and must NOT overwrite the concurrent content.
- I4: If any ancestor directory of the target becomes a junction/symlink before commit, apply must refuse with zero writes outside the workspace.
- I5: Staging/backup/temp file creation must never follow a junction that appeared after the initial check.

HARD RULES FOR EVERY PROBE SCRIPT:
- Runnable standalone: `python <probe>.py --repo D:\\JH-Agent OS --temp-base D:\\JH-Agent OS\\.test-tmp` (argparse, same convention).
- Create ONE unique root dir under temp-base (uuid suffix); ALL test files, workspaces, and simulated "outside" dirs live inside that root. Never touch anything outside it.
- Use only stdlib + `from src.file_tools import FileToolExecutor, PathBoundaryError`. No network, no new dependencies.
- Junctions via `_winapi.CreateJunction` with `cmd /c mklink /J` fallback; cleanup: rmdir each junction first, then rmtree the unique root (guard root name prefix before rmtree).
- Use the executor's public API plus its `fault_injector(phase)` hook (phases: apply_start, backed_up, temp_written, replaced, confirmed) to simulate interleavings deterministically. Do NOT monkeypatch private methods.
- Each probe prints one JSON object to stdout: {"probe": name, "invariant": "I1".."I5", "refused": bool, "disk_checks": {label: bool}, "defect_reproduced": bool}.
- Exit code 0 always (report verdicts in JSON, even on unexpected exceptions: {"probe": name, "error": str}).

PRODUCE AT MOST 6 probe scripts, each under 120 lines, covering DISTINCT attack angles. Prefer angles NOT in this already-covered list: staging-root junction; fixed `.jh_tmp` neighbor collision; concurrent edit at backed_up phase; parent-dir junction swap at temp_written phase. Suggested uncovered angles (pick/extend): junction planted at deeper ancestor levels; junction at the FINAL target component pre-existing before apply; workspace root itself is a junction; pre-existing decoy files with backup-like or uuid-like temp names inside staging; concurrent edit at EVERY other fault phase (apply_start, temp_written, replaced, confirmed); concurrent DELETE of target mid-apply; read_file racing a junction swap (must never return outside bytes); case-variant and prefix-collision paths (e.g. workspace vs workspace_evil).

Also list up to 5 residual code risks you see (file:line, one sentence each, severity high/medium/low).

Reply with JSON ONLY, no other text, matching the provided schema.
""".replace("{src}", SRC)

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "probes": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "invariant": {"type": "string"},
                    "objective": {"type": "string"},
                    "code": {"type": "string"},
                    "expected": {"type": "string"},
                },
                "required": ["name", "invariant", "objective", "code", "expected"],
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
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "probes", "risks", "open_questions"],
    "additionalProperties": False,
}

out_path = ROOT / ".test-tmp" / "g1_result.json"
out_path.parent.mkdir(parents=True, exist_ok=True)
raw_path = ROOT / ".test-tmp" / "g1_raw.txt"

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
    "n_probes": len(p.get("probes", [])),
    "probe_names": [q.get("name") for q in p.get("probes", [])],
    "n_risks": len(p.get("risks", [])),
}, ensure_ascii=False, indent=2))
