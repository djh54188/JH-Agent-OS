"""Re-capture the already executed harmless marker from native desktop records.

No execution of the marker or model calls occurs here. Requires this machine's
session store and operator-controlled pin files; output directory must be new.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
PROOF = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--archive-dir", required=True)
args = parser.parse_args()
source_view = json.loads((PROOF / "selected-tool-events.json").read_text(encoding="utf8"))
event = next(r for r in source_view["records"] if r["type"] == "event_msg")
call = next(r for r in source_view["records"] if r.get("payload", {}).get("type") == "custom_tool_call")
thread = event["payload"]["thread_id"]
# Selectors locate an existing record; invocation expectations remain independently pinned.
segments = list((Path.home() / ".codex/sessions").rglob("*" + thread + "*.jsonl"))
segments = [p for p in segments if event["payload"]["item"]["id"].encode() in p.read_bytes()]
if len(segments) != 1:
    raise SystemExit("Require exactly one source segment for the existing execution")
argv = json.loads((PROOF / "argv-pin.json").read_text(encoding="utf8"))
command = [sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts/probe_host_capture.py"),
    "--protocol", "desktop", "--thread-id", thread, "--segment", str(segments[0]),
    "--item-id", event["payload"]["item"]["id"], "--call-id", call["payload"]["call_id"],
    "--expect-argv-json", json.dumps(argv), "--expect-workdir", str(ROOT), "--expect-exit", "0",
    "--expect-marker", "JH_CAPTURE_PLUMBING_20261016_OK", "--expect-call-input-file",
    str(PROOF / "call-input-pin.js"), "--task-id", "capture-plumbing-only",
    "--delivery-id", "not-a-code-delivery", "--apply-attempt-id", "no-apply-performed",
    "--artifact-claim-json", "{}", "--review-claim-json", "{}",
    "--archive-dir", str(Path(args.archive_dir).resolve())]
raise SystemExit(subprocess.run(command, cwd=ROOT).returncode)
