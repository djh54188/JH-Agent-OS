"""Independent, zero-process/zero-network probes of fixed Hark11 candidate.

Uses the candidate's captured native event template, decoded then substituted
as data (so the review input is valid JSON on Windows). Does not alter candidate.
Run: python -X utf8 -B probe_boundaries.py --round round-01
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import pathlib
import platform
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "candidate"))
from src import agent_session_gemini as gem
from src.agent_session import SessionAddress, SubmitRequest

ADDRESS = SessionAddress("gemini-cli", "gemini-cli/local", "new")
TEMPLATE = (HERE / "candidate/tests/fixtures/gemini_cli/host_probe_stream.template.jsonl").read_bytes()


class Done:
    returncode = 0

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0


class Harness:
    def __init__(self, root):
        self.root = root
        self.ws = root / "ws"
        (self.ws / "src").mkdir(parents=True)
        (self.ws / "src/agent_session.py").write_bytes(b"x\n" * 140)
        (self.ws / "hidden").mkdir()
        (self.ws / "hidden/real.txt").write_bytes(b"nonempty\n")
        self.home = root / "home"
        self.evidence = root / "evidence"
        self.home.mkdir()
        self.launches = 0
        self.edits = None
        self.chat_first = {"sessionId": "{SESSION_ID}"}

    def request(self, key="k1"):
        return SubmitRequest("t1", 1, ADDRESS, "Locate RunState, cite path:line.",
                             str(self.ws), idempotency_key=key)

    def launcher(self, argv, **kwargs):
        self.launches += 1
        sid = argv[argv.index("--session-id") + 1]
        # Substitute only after parsing; this is a review process substitute,
        # NOT a correction to the candidate fake_cli.py.
        native = json.loads("[" + ",".join(TEMPLATE.decode().splitlines()) + "]")
        native = json.loads(json.dumps(native).replace("{SESSION_ID}", sid)
                            .replace("{WORKSPACE}", json.dumps(str(self.ws))[1:-1]))
        if self.edits:
            self.edits(native)
        pathlib.Path(kwargs["stdout_path"]).write_bytes(
            b"".join(json.dumps(e, ensure_ascii=False).encode() + b"\n" for e in native))
        pathlib.Path(kwargs["stderr_path"]).write_bytes(b"")
        chat = self.home / ".gemini/tmp/probe/chats/session-probe.jsonl"
        chat.parent.mkdir(parents=True, exist_ok=True)
        chat.write_bytes(json.dumps(self.chat_first).replace("{SESSION_ID}", sid).encode() + b"\n")
        return Done()

    def adapter(self):
        config = gem.GeminiCliConfig(("NOT_EXECUTED",), str(self.home), str(self.evidence))
        return gem.GeminiCliAgentSessionAdapter(config, launcher=self.launcher,
                                              version_probe=lambda _: "0.65.0")

    def collect(self, a, s):
        result = a.collect(ADDRESS, s.run_id, timeout=0.1)
        return {"state": a.status(ADDRESS, s.run_id).state,
                "declared_status": result.declared_status,
                "unverified": result.unverified,
                "workspace_unchanged": result.artifacts[0].get("workspace_unchanged") if result.artifacts else None,
                "denied": result.artifacts[0].get("denied_tool_requests") if result.artifacts else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", required=True)
    args = ap.parse_args()
    out = HERE / args.round
    out.mkdir(exist_ok=False)
    rows = []

    def run(name, fn):
        h = Harness(out / name)
        try:
            result = fn(h)
        except Exception as exc:
            result = {"exception": type(exc).__name__, "message": str(exc)}
        result.update(case=name, launches=h.launches)
        rows.append(result)

    def once(h):
        a = h.adapter()
        return h.collect(a, a.submit(h.request()))

    run("positive", once)

    def duplicate(h):
        first, stale = h.adapter(), h.adapter()
        s1 = first.submit(h.request())
        s2 = stale.submit(h.request())
        return {"first": s1.run_id, "second": s2.run_id,
                "same_run": s1.run_id == s2.run_id}

    run("two_instances_same_source_same_key", duplicate)

    def reopen(h):
        a = h.adapter()
        s1 = a.submit(h.request())
        b = h.adapter()
        s2 = b.submit(h.request())
        return {"same_run": s1.run_id == s2.run_id, "state": s2.state}

    run("reopen_after_dispatch_control", reopen)

    def two_threads(h):
        a = h.adapter()
        barrier = threading.Barrier(2)
        def version(_):
            barrier.wait(timeout=5)
            return "0.65.0"
        a._version_probe = version
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(a.submit, h.request()) for _ in range(2)]
            results = [f.result(timeout=10) for f in futures]
        return {"same_run": results[0].run_id == results[1].run_id}
    run("same_instance_two_threads_same_key", two_threads)

    def bad_registry(h, late):
        if late:
            a = h.adapter()
        h.evidence.mkdir(exist_ok=True)
        (h.evidence / "registry.jsonl").write_bytes(b"{not json\n")
        if not late:
            a = h.adapter()
        try:
            a.submit(h.request())
            return {"blocked": None}
        except Exception as exc:
            return {"blocked": type(exc).__name__}
    run("registry_bad_json_control", lambda h: bad_registry(h, False))
    run("registry_bad_json_after_initialization", lambda h: bad_registry(h, True))

    def registry(h, edit):
        a = h.adapter()
        s = a.submit(h.request())
        registry = h.evidence / "registry.jsonl"
        records = [json.loads(l) for l in registry.read_bytes().splitlines()]
        records = edit(records)
        registry.write_bytes(b"".join(json.dumps(r).encode() + b"\n" for r in records))
        before = h.launches
        b = h.adapter()
        blocked = None
        try:
            b.submit(h.request("k2"))
        except Exception as exc:
            blocked = type(exc).__name__
        return {"new_launches": h.launches - before, "blocked": blocked,
                "registry_problem": b._registry_problem}

    def drop(field):
        def edit(rs):
            for r in rs:
                r.pop(field, None)
            return rs
        return edit

    def setfield(field, value):
        def edit(rs):
            for r in rs:
                r[field] = value
            return rs
        return edit

    for field in ("app_kind", "instance_id", "session_id", "task_id", "attempt", "request_snapshot",
                  "request_fingerprint", "native_session_id", "event", "idempotency_key"):
        run("registry_missing_" + field, lambda h, f=field: registry(h, drop(f)))
    for field, value in (("attempt", []), ("workspace", {}), ("run_id", ""),
                         ("event", "invented"), ("state", "succeeded"),
                         ("request_snapshot", "{}"), ("request_fingerprint", "0" * 64)):
        run("registry_wrong_" + field, lambda h, f=field, v=value: registry(h, setfield(f, v)))
    run("registry_nonobject_control", lambda h: registry(h, lambda _: [[]]))

    def contradicted(rs):
        x = copy.deepcopy(rs[-1]); x["native_session_id"] = "foreign-session"
        return rs + [x]

    run("registry_contradicted_binding", lambda h: registry(h, contradicted))
    run("registry_reversed_events", lambda h: registry(h, lambda rs: list(reversed(rs))))

    def enumerate_failure(h, target, phase):
        a = h.adapter()
        original = os.scandir
        denied = h.ws if target == "root" else h.ws / "hidden"
        def scandir(path):
            if os.path.normcase(os.path.abspath(path)) == os.path.normcase(str(denied)):
                raise PermissionError("independent injected enumeration failure")
            return original(path)
        if phase == "both":
            with mock.patch.object(gem.os, "scandir", scandir):
                s = a.submit(h.request())
                answer = h.collect(a, s)
        else:
            s = a.submit(h.request())
            with mock.patch.object(gem.os, "scandir", scandir):
                answer = h.collect(a, s)
        answer["before_manifest"] = json.loads((pathlib.Path(a.inspect(s.run_id).run_dir) / "workspace-before.json").read_bytes())
        return answer

    for target in ("root", "child"):
        for phase in ("both", "after_only"):
            run("enumerate_" + target + "_" + phase,
                lambda h, t=target, p=phase: enumerate_failure(h, t, p))

    def broken_stream(h, edit):
        h.edits = edit
        return once(h)

    def status_change(value, missing=False):
        def edit(events):
            for e in events:
                if e.get("type") == "tool_result":
                    if missing: e.pop("status", None)
                    else: e["status"] = value
        return edit

    for label, status, missing in (("missing", None, True), ("null", None, False),
                                    ("unknown", "in_progress", False), ("array", [], False)):
        run("tool_result_status_" + label,
            lambda h, s=status, m=missing: broken_stream(h, status_change(s, m)))

    def bad_tool_id(events):
        next(e for e in events if e["type"] == "tool_result")["tool_id"] = []
    run("stream_tool_id_array", lambda h: broken_stream(h, bad_tool_id))
    def bad_event_type(events):
        events.insert(1, {"type": []})
    run("stream_type_array", lambda h: broken_stream(h, bad_event_type))
    for label, value in (("array", []), ("null", None), ("string", "x"), ("number", 1), ("bool", True)):
        def chat(h, v=value):
            h.chat_first = v
            return once(h)
        run("chat_first_" + label, chat)

    # Do not start the candidate fake process: demonstrate its exact byte
    # substitution in isolation on the current Windows path.
    def windows_fixture(h):
        data = TEMPLATE.decode().replace("{SESSION_ID}", "review-session").replace("{WORKSPACE}", str(h.ws))
        try:
            for l in data.splitlines(): json.loads(l)
            return {"all_lines_parse": True}
        except ValueError as exc:
            return {"all_lines_parse": False, "exception": type(exc).__name__, "message": str(exc)}
    run("windows_fixture_substitution", windows_fixture)

    def existing_relative(h):
        original_cwd = os.getcwd()
        try:
            os.chdir(h.root)
            a = h.adapter()
            request = SubmitRequest("relative", 1, ADDRESS, "Locate RunState.", "ws", idempotency_key="relative")
            s = a.submit(request)
            return {"submitted": True, "registered_workspace": a.inspect(s.run_id).workspace}
        finally:
            os.chdir(original_cwd)
    run("existing_relative_workspace", existing_relative)

    report = {"candidate": "7a41ac1673adb5cf202b12a25c6c83ae8fdbb659",
              "platform": platform.platform(), "python": sys.version,
              "external_processes": 0, "network_calls": 0, "real_models": 0,
              "template_sha256": hashlib.sha256(TEMPLATE).hexdigest(), "cases": rows}
    data = json.dumps(report, ensure_ascii=False, indent=2).encode()
    (out / "results.json").write_bytes(data)
    print(json.dumps({"output": str(out / "results.json"), "cases": len(rows),
                      "rows": [{k: v for k, v in row.items() if k not in ("unverified", "before_manifest")}
                               for row in rows]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
