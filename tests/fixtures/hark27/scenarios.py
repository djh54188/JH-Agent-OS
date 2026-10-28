"""Scenario builders shared by the Hark-27 tests and the offline replay (fake host input).
Field shapes follow the official ZCode source (session-mapper / session.events), unverified
against the installed build."""
import copy

PROVIDER, MODEL = "jh-proxy", "gemini-3.8-flash-high"
SID, TID, UID = "sess_fake01", "trace_fake01", "turn_fake01"


def ev(typ, payload, n, turn=UID, sid=SID, tid=TID):
    return {"eventId": f"evt_{n:03d}", "payload": payload, "seq": n, "sessionId": sid,
            "timestamp": 1761000000000 + n, "traceId": tid, "turnId": turn, "type": typ}


def tool(n, cid, name, inp, content="ok", read_only=True, turn=UID):
    return [ev("tool.updated", {"toolCallId": cid, "kind": "scheduled", "toolName": name, "input": inp}, n, turn),
            ev("tool.updated", {"toolCallId": cid, "kind": "started", "toolName": name, "readOnly": read_only,
                                "startedAt": 1}, n + 1, turn),
            ev("tool.updated", {"toolCallId": cid, "kind": "result", "duration": 3,
                                "result": {"success": True, "content": content}}, n + 2, turn)]


def normal(provider=PROVIDER, model=MODEL, usage=True, response="greet is defined at src/greeter.py:4"):
    lines = [ev("session.created", {}, 1, turn=None), ev("turn.started", {}, 2),
             ev("session.updated", {"providerId": provider, "modelId": model, "messageCount": 1, "toolCount": 3}, 3)]
    lines += tool(4, "call_grep_1", "Grep", {"pattern": "def greet", "path": "{WS}"},
                  "{WS}/src/greeter.py:4:def greet(name: str) -> str:")
    lines += tool(7, "call_read_1", "Read", {"file_path": "{WS}/src/greeter.py"},
                  '1\t"""Tiny module the offline replay reads and searches."""')
    lines += [ev("session.updated", {"providerId": provider, "modelId": model, "messageCount": 5, "toolCount": 3}, 10),
              ev("turn.completed", {"response": response}, 11)]
    result = {"type": "result", "sessionId": SID, "traceId": TID, "turnId": UID, "response": response,
              "eventCount": 11, "projection": {"status": "idle", "turnCount": 1, "totalTokenCount": 1234,
                                               "contextUsed": None, "contextWindow": None}}
    if usage:
        result["usage"] = {"inputTokens": 1000, "outputTokens": 234}
    return {"exit": 0, "lines": lines + [result]}


def mutate(scn, fn):
    out = copy.deepcopy(scn)
    fn(out["lines"])
    return out
