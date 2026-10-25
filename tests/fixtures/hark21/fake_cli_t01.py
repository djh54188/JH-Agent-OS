"""CONSTRUCTED process stand-in for Gemini CLI 0.65.0 (Hark-派单-21/22 tests only; no model).

It emits a stream-json stdout and a host chat record shaped like the ones the
real CLI 0.65.0 wrote in reports/hark-gemini-bridge-20261021/host-probe (real
tools, synthetic model endpoint): session header, the prompt as a user record,
`$set` updates, each gemini record written first without and then again WITH its
toolCalls (same id: the host's rewrite), and the functionResponse repeated in the
next user record. The tool outputs it writes are whatever the scenario (built by
tests/fixtures/hark21/build.py) supplies: CONSTRUCTED good or bad inputs, never a
real model's work.

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
    values = {"{SESSION_ID}": session, "{WORKSPACE}": sc.get("workspace_alias") or os.getcwd()}
    ts = "Sun 2026-10-25 8:00 AM CST (UTC+08:00)"
    host_model = sc.get("host_model", model)
    chat_model = sc.get("chat_model", model)
    events = [{"type": "init", "timestamp": ts, "session_id": session, "model": host_model},
              {"type": "message", "timestamp": ts, "role": "user",
               "content": sc.get("user_content_override", stdin + "\n\n" + p_text)}]
    setl = {"$set": {"lastUpdated": ts}}
    chat = [{"sessionId": session, "projectHash": "constructed", "startTime": ts, "lastUpdated": ts,
             "kind": "main"},
            {"id": "u-prompt", "timestamp": ts, "type": "user", "content": [{"text": stdin}]}, setl]
    for n, tool in enumerate(sub(sc.get("tools", []), values)):
        events.append({"type": "tool_use", "timestamp": ts, "tool_name": tool["name"],
                       "tool_id": tool["id"], "parameters": tool["params"]})
        result = {"type": "tool_result", "timestamp": ts, "tool_id": tool["id"], "status": tool["status"]}
        if tool.get("stream_output") is not None:
            result["output"] = tool["stream_output"]
        events.append(result)
        if tool.get("chat", True):
            response = {"output": tool["chat_output"]} if tool["status"] == "success" else \
                {"error": tool.get("chat_output", "error")}
            cid = tool.get("chat_id", tool["id"])
            fr = {"functionResponse": {"id": cid, "name": tool.get("fr_name", tool.get("chat_name", tool["name"])),
                                       "response": response}}
            gem = {"id": f"g-{n}", "timestamp": ts, "type": "gemini", "content": "", "thoughts": [],
                   "tokens": {"input": 1, "output": 1, "cached": 0, "thoughts": 0, "tool": 0, "total": 2},
                   "model": chat_model}
            with_calls = dict(gem, toolCalls=[{"id": cid, "name": tool.get("chat_name", tool["name"]),
                                               "args": tool.get("chat_args", tool["params"]),
                                               "result": [fr], "status": tool["status"], "timestamp": ts}])
            chat += [gem, setl, with_calls, {"id": f"u-{n}", "timestamp": ts, "type": "user", "content": [fr]},
                     setl]
    text = sub(sc.get("delivery", ""), values)
    events += sc.get("extra_events", [])
    events.append({"type": "message", "timestamp": ts, "role": "assistant", "content": text, "delta": True})
    chat += [{"id": "g-final", "timestamp": ts, "type": "gemini", "content": text, "thoughts": [],
              "model": chat_model}, setl]
    chat += sc.get("chat_extra_lines", [])
    stats = {"total_tokens": 10, "input_tokens": 6, "output_tokens": 4, "cached": 0, "input": 6,
             "duration_ms": 1, "tool_calls": len(sc.get("tools", [])),
             "models": {host_model: {"total_tokens": 10, "input_tokens": 6,
                                     "output_tokens": 4, "cached": 0, "input": 6}}}
    for key in sc.get("drop_stats_fields", []):
        stats.pop(key, None)
    res = {"type": "result", "timestamp": ts, "status": sc.get("result_status", "success")}
    if not sc.get("no_stats"):
        res["stats"] = stats
    events.append(res)
    out = "".join(line + "\n" for line in sc.get("stdout_preamble", [])) + "".join(enc(e) + "\n" for e in events)
    sys.stdout.write(out)
    sys.stdout.flush()
    if sc.get("stderr"):
        sys.stderr.write(sc["stderr"])
        sys.stderr.flush()
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
