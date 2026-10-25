"""CONSTRUCTED process stand-in for Gemini CLI 0.65.0 (Hark-派单-21 tests only; no model).

It emits a stream-json stdout and a host chat record shaped like the ones the
real CLI 0.65.0 wrote in reports/hark-gemini-bridge-20261021/host-probe (real
tools, synthetic model endpoint). The tool outputs it writes are whatever the
scenario (built by tests/fixtures/hark21/build.py) supplies: CONSTRUCTED good or
bad inputs, never a real model's work.

The user message event is built from the ACTUAL stdin and -p text the adapter
passed, so prompt binding is exercised for real. Placeholders {SESSION_ID} and
{WORKSPACE} are substituted in decoded JSON values.
"""
import json
import os
import sys


def sub(value, values):
    if isinstance(value, str):
        for k, v in values.items():
            value = value.replace(k, v)
        return value
    if isinstance(value, list):
        return [sub(v, values) for v in value]
    if isinstance(value, dict):
        return {k: sub(v, values) for k, v in value.items()}
    return value


def enc(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    with open(os.environ["FAKE_T01_SCENARIO"], encoding="utf-8") as fh:
        sc = json.load(fh)
    argv = sys.argv[1:]
    if argv == ["--version"]:
        sys.stdout.write(sc.get("version", "0.65.0") + "\n")
        return 0
    session = argv[argv.index("--session-id") + 1]
    model = argv[argv.index("-m") + 1] if "-m" in argv else "auto"
    p_text = argv[argv.index("-p") + 1]
    stdin = sys.stdin.buffer.read().decode("utf-8")
    values = {"{SESSION_ID}": session, "{WORKSPACE}": os.getcwd()}
    ts = "2026-10-25T00:00:00.000Z"
    events = [{"type": "init", "timestamp": ts, "session_id": session,
               "model": sc.get("host_model", model)},
              {"type": "message", "timestamp": ts, "role": "user",
               "content": sc.get("user_content_override", stdin + "\n\n" + p_text)}]
    chat = [{"sessionId": session, "projectHash": "constructed", "startTime": ts, "lastUpdated": ts,
             "kind": "main"}]
    for tool in sub(sc.get("tools", []), values):
        events.append({"type": "tool_use", "timestamp": ts, "tool_name": tool["name"],
                       "tool_id": tool["id"], "parameters": tool["params"]})
        result = {"type": "tool_result", "timestamp": ts, "tool_id": tool["id"], "status": tool["status"]}
        if tool.get("stream_output") is not None:
            result["output"] = tool["stream_output"]
        events.append(result)
        if tool.get("chat", True):
            response = {"output": tool["chat_output"]} if tool["status"] == "success" else \
                {"error": tool.get("chat_output", "error")}
            fr = {"functionResponse": {"id": tool.get("chat_id", tool["id"]), "name": tool["name"],
                                       "response": response}}
            chat.append({"id": "g-" + tool["id"], "timestamp": ts, "type": "gemini", "content": "",
                         "model": model,
                         "toolCalls": [{"id": tool.get("chat_id", tool["id"]),
                                        "name": tool.get("chat_name", tool["name"]),
                                        "args": tool.get("chat_args", tool["params"]),
                                        "result": [fr], "status": tool["status"], "timestamp": ts}]})
            chat.append({"id": "u-" + tool["id"], "timestamp": ts, "type": "user", "content": [fr]})
    text = sub(sc.get("delivery", ""), values)
    events.append({"type": "message", "timestamp": ts, "role": "assistant", "content": text, "delta": True})
    stats = {"total_tokens": 10, "input_tokens": 6, "output_tokens": 4, "cached": 0, "input": 6,
             "duration_ms": 1, "tool_calls": len(sc.get("tools", [])),
             "models": {sc.get("host_model", model): {"total_tokens": 10, "input_tokens": 6,
                                                       "output_tokens": 4, "cached": 0, "input": 6}}}
    events.append({"type": "result", "timestamp": ts, "status": sc.get("result_status", "success"),
                   "stats": stats})
    sys.stdout.write("".join(enc(e) + "\n" for e in events))
    sys.stdout.flush()
    if sc.get("chat_record", True):
        home = os.environ["GEMINI_CLI_HOME"]
        d = os.path.join(home, ".gemini", "tmp", "ws", "chats")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "session-constructed.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(enc(c) + "\n" for c in chat))
    for rel in sc.get("mutate", []):
        with open(os.path.join(os.getcwd(), rel), "w") as fh:
            fh.write("x")
    return int(sc.get("exit_code", 0))


if __name__ == "__main__":
    sys.exit(main())
