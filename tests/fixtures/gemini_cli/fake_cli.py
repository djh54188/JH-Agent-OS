"""Process stand-in for the pinned Gemini CLI (SYNTHETIC; used only by tests).

It replays `host_probe_stream.template.jsonl` — a native stream captured from the
real Gemini CLI 0.65.0 driven by the synthetic endpoint, with the session id and
workspace path turned into {SESSION_ID}/{WORKSPACE} — so the adapter is tested
against native event bytes rather than a self-invented format.

The scenario JSON (path in FAKE_GEMINI_SCENARIO) can alter the replay to inject
faults: version, exit_code, sleep, session_override, drop_types, extra_lines,
mutate (relative path to create in the workspace), chat_record (bool),
tool_success_override (tool name whose result is turned into success).
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


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
    workspace = os.getcwd()
    with open(os.path.join(HERE, "host_probe_stream.template.jsonl"), "rb") as fh:
        template = fh.read().decode("utf-8")
    text = template.replace("{SESSION_ID}", native_session).replace("{WORKSPACE}", workspace)
    lines = text.splitlines()
    drop = set(scenario.get("drop_types", []))
    override = scenario.get("tool_success_override")
    out = []
    override_ids = set()
    for line in lines:
        event = json.loads(line)
        if event["type"] in drop:
            continue
        if override and event["type"] == "tool_use" and event.get("tool_name") == override:
            override_ids.add(event["tool_id"])
        if override and event["type"] == "tool_result" and event.get("tool_id") in override_ids:
            event = {"type": "tool_result", "timestamp": event["timestamp"], "tool_id": event["tool_id"],
                     "status": "success"}
            line = json.dumps(event)
        out.append(line)
    out.extend(scenario.get("extra_lines", []))
    if scenario.get("mutate"):
        with open(os.path.join(workspace, scenario["mutate"]), "w", encoding="utf-8") as fh:
            fh.write("mutated")
    if scenario.get("chat_record", True):
        chats = os.path.join(os.environ["GEMINI_CLI_HOME"], ".gemini", "tmp", "wt", "chats")
        os.makedirs(chats, exist_ok=True)
        with open(os.path.join(chats, f"session-fake-{session_id[:8]}.jsonl"), "wb") as fh:
            fh.write((json.dumps({"sessionId": native_session, "kind": "main"}) + "\n").encode("utf-8"))
    sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8"))
    sys.stdout.flush()
    return int(scenario.get("exit_code", 0))


if __name__ == "__main__":
    sys.exit(main())
