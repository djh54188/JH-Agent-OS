"""Process stand-in for the pinned Gemini CLI (SYNTHETIC; used only by tests).

It replays `host_probe_stream.template.jsonl` — a native stream captured from the
real Gemini CLI 0.65.0 driven by the synthetic endpoint, with the session id and
workspace path turned into {SESSION_ID}/{WORKSPACE} — so the adapter is tested
against native event bytes rather than a self-invented format.

The scenario JSON (path in FAKE_GEMINI_SCENARIO) can alter the replay to inject
faults: version, exit_code, sleep, session_override, drop_types, extra_lines,
mutate (relative path to create in the workspace), chat_record (bool),
tool_success_override (tool name whose result is turned into success),
workspace_display (string substituted for {WORKSPACE} instead of the real cwd,
e.g. a synthetic Windows path), edit_events (list of {"type", "index", "set",
"delete"}: the index-th event of that type gets fields set/deleted, any JSON
value allowed), chat_head (any JSON value written as the chat record's first
line instead of the session header).

Placeholders are substituted in the DECODED JSON values and the event is
re-encoded with JSON.stringify-compatible separators, so a workspace path with
backslashes or quotes stays valid JSON (Hark-派单-12 R3). For the template's
Linux paths the output bytes equal the old text substitution.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def substitute(value, values):
    """Replace placeholders inside decoded JSON strings (never in raw JSON text)."""
    if isinstance(value, str):
        for placeholder, text in values.items():
            value = value.replace(placeholder, text)
        return value
    if isinstance(value, list):
        return [substitute(v, values) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, values) for k, v in value.items()}
    return value


def encode(event):
    """JSON.stringify-compatible encoding (compact separators, non-ASCII kept)."""
    return json.dumps(event, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    with open(os.environ["FAKE_GEMINI_SCENARIO"], encoding="utf-8") as fh:
        scenario = json.load(fh)
    argv = sys.argv[1:]
    if argv == ["--version"]:
        sys.stdout.write(scenario.get("version", "0.65.0") + "\n")
        return 0
    with open(os.path.join(os.path.dirname(os.environ["FAKE_GEMINI_SCENARIO"]), "argv.json"), "w",
              encoding="utf-8") as fh:
        json.dump(argv, fh)
    session_id = argv[argv.index("--session-id") + 1]
    sys.stdin.buffer.read()
    if scenario.get("sleep"):
        time.sleep(scenario["sleep"])
    native_session = scenario.get("session_override") or session_id
    workspace = scenario.get("workspace_display") or os.getcwd()
    with open(os.path.join(HERE, "host_probe_stream.template.jsonl"), "rb") as fh:
        template = fh.read().decode("utf-8")
    values = {"{SESSION_ID}": native_session, "{WORKSPACE}": workspace}
    drop = set(scenario.get("drop_types", []))
    override = scenario.get("tool_success_override")
    edits = scenario.get("edit_events", [])
    seen = {}
    out = []
    override_ids = set()
    for line in template.splitlines():
        event = substitute(json.loads(line), values)
        kind = event["type"]
        if kind in drop:
            continue
        if override and kind == "tool_use" and event.get("tool_name") == override:
            override_ids.add(event["tool_id"])
        if override and kind == "tool_result" and event.get("tool_id") in override_ids:
            event = {"type": "tool_result", "timestamp": event["timestamp"], "tool_id": event["tool_id"],
                     "status": "success"}
        index = seen.get(kind, 0)
        seen[kind] = index + 1
        for edit in edits:
            if edit.get("type") == kind and edit.get("index", 0) == index:
                for name in edit.get("delete", []):
                    event.pop(name, None)
                event.update(edit.get("set", {}))
        out.append(encode(event))
    out.extend(scenario.get("extra_lines", []))
    if scenario.get("mutate"):
        with open(os.path.join(workspace, scenario["mutate"]), "w", encoding="utf-8") as fh:
            fh.write("mutated")
    if scenario.get("chat_record", True):
        chats = os.path.join(os.environ["GEMINI_CLI_HOME"], ".gemini", "tmp", "wt", "chats")
        os.makedirs(chats, exist_ok=True)
        with open(os.path.join(chats, f"session-fake-{session_id[:8]}.jsonl"), "wb") as fh:
            head = scenario["chat_head"] if "chat_head" in scenario else {"sessionId": native_session, "kind": "main"}
            fh.write((json.dumps(head) + "\n").encode("utf-8"))
    sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8"))
    sys.stdout.flush()
    return int(scenario.get("exit_code", 0))


if __name__ == "__main__":
    sys.exit(main())
