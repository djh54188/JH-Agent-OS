"""Reproduce the Hark-派单-12 admission counterexamples (offline, SYNTHETIC).

Uses only the adapter API that already existed at baseline 7a41ac1, so the same
script runs against the baseline tree and against the candidate tree and shows
the observed behaviour side by side. The launched program is the tree's own
tests/fixtures/gemini_cli/fake_cli.py; no real CLI, no model.

Output: one JSON object per case (case, observed, expected) and a final
summary line. Exit 0 always: this script reports, the tests judge.
"""
from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
import os
import shutil
import sys
import tempfile
import threading
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "fixtures", "gemini_cli"))

from src import agent_session_gemini as gem  # noqa: E402
from src.agent_session import SessionAddress, SubmitRequest  # noqa: E402

FAKE = os.path.join(ROOT, "tests", "fixtures", "gemini_cli", "fake_cli.py")
ADDRESS = SessionAddress(app_kind="gemini-cli", instance_id="gemini-cli/local", session_id="new")
OUT = []


class Env:
    def __init__(self, ws_name="ws", scenario=None):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="hark12-repro-"))
        self.ws = os.path.join(self.tmp, ws_name)
        os.makedirs(os.path.join(self.ws, "src"))
        with open(os.path.join(self.ws, "src", "agent_session.py"), "w") as fh:
            fh.write("\n".join("class RunState:" if i == 94 else f"# {i}" for i in range(1, 140)) + "\n")
        self.ev = os.path.join(self.tmp, "evidence")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.scn = os.path.join(self.tmp, "scenario.json")
        self.scenario(scenario or {})
        self.launches = 0
        self.lock = threading.Lock()

    def scenario(self, value):
        with open(self.scn, "w") as fh:
            json.dump(value, fh)

    def adapter(self, fake=None, **kw):
        cfg = gem.GeminiCliConfig(argv_prefix=(sys.executable, fake or FAKE), cli_home=self.home, evidence_root=self.ev,
                                  extra_env=(("FAKE_GEMINI_SCENARIO", self.scn),))

        def launcher(argv, **k):
            with self.lock:
                self.launches += 1
            return gem.default_launcher(argv, **k)
        return gem.GeminiCliAgentSessionAdapter(cfg, launcher=launcher, **kw)

    def req(self, **o):
        v = dict(task_id="t", attempt=1, session=ADDRESS, instructions="Locate class RunState.",
                 workspace=self.ws, idempotency_key="k1")
        v.update(o)
        return SubmitRequest(**v)

    def records(self):
        with open(os.path.join(self.ev, "registry.jsonl"), "rb") as fh:
            return [json.loads(l) for l in fh.read().decode().splitlines() if l.strip()]

    def write(self, records):
        with open(os.path.join(self.ev, "registry.jsonl"), "wb") as fh:
            for r in records:
                fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")).encode() + b"\n")

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def report(case, observed, expected):
    OUT.append({"case": case, "observed": observed, "expected": expected})


def attempt(fn):
    try:
        return {"outcome": "returned", "value": fn()}
    except Exception as exc:
        return {"outcome": "raised", "type": type(exc).__name__}


def wait(adapter):
    for run in list(adapter._runs.values()):
        if getattr(run, "process", None) is not None:
            run.process.wait(timeout=60)


def r1():
    e = Env()
    a, b = e.adapter(), e.adapter()
    a.submit(e.req()); b.submit(e.req()); wait(a); wait(b)
    report("R1 two adapters, one evidence_root, same key", {"launches": e.launches}, "launches <= 1")
    e.close()

    e = Env()
    bar = threading.Barrier(2, timeout=10)
    ad = e.adapter(version_probe=lambda c: (bar.wait(), "0.65.0")[1])
    errs = []
    def go():
        try:
            ad.submit(e.req())
        except Exception as exc:
            errs.append(type(exc).__name__)
    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]; [t.join(60) for t in ts]; wait(ad)
    report("R1 one adapter, two threads met in version_probe", {"launches": e.launches, "errors": errs},
           "launches <= 1")
    e.close()

    e = Env()
    import mp_submit
    ctx = multiprocessing.get_context("spawn")
    paths = {"home": e.home, "evidence": e.ev, "scenario": e.scn, "workspace": e.ws,
             "launches": os.path.join(e.tmp, "launches.txt")}
    bar, q = ctx.Barrier(2), ctx.Queue()
    ps = [ctx.Process(target=mp_submit.worker, args=(paths, bar, q)) for _ in range(2)]
    [p.start() for p in ps]
    res = [q.get(timeout=120) for _ in ps]; [p.join(60) for p in ps]
    with open(paths["launches"]) as fh:
        n = len([l for l in fh.read().splitlines() if l])
    report("R1 two processes, same key", {"launches": n, "results": [r[0] + ":" + str(r[1]) for r in res]},
           "launches <= 1")
    e.close()

    e = Env()
    ad = e.adapter()
    os.makedirs(e.ev)
    with open(os.path.join(e.ev, "registry.jsonl"), "wb") as fh:
        fh.write(b"{not json\n")
    obs = attempt(lambda: ad.submit(e.req()).run_id); wait(ad)
    report("R1 registry corrupted after construction", {"launches": e.launches, "submit": obs["outcome"],
           "raised": obs.get("type")}, "refused, launches 0")
    e.close()

    e = Env()
    ad = e.adapter(); ad.submit(e.req()); wait(ad)
    legal = e.records()
    snap = legal[0]["request_snapshot"]
    cases = {
        "missing native_session_id": lambda rs: [r.pop("native_session_id") for r in rs],
        "missing instance_id": lambda rs: [r.pop("instance_id") for r in rs],
        "missing request_snapshot": lambda rs: [r.pop("request_snapshot") for r in rs],
        "attempt as string": lambda rs: [r.update(attempt="1") for r in rs],
        "empty run_id": lambda rs: [r.update(run_id="") for r in rs],
        "run_id int": lambda rs: [r.update(run_id=7) for r in rs],
        "unknown event": lambda rs: rs[1].update(event="launched"),
        "illegal state": lambda rs: rs[1].update(state="succeeded"),
        "fingerprint != snapshot": lambda rs: [r.update(request_fingerprint=hashlib.sha256(b"x").hexdigest()) for r in rs],
        "snapshot edited": lambda rs: [r.update(request_snapshot=snap.replace("Locate", "Find")) for r in rs],
        "native binding contradiction": lambda rs: rs[1].update(native_session_id="00000000-0000-4000-8000-000000000000"),
        "events reversed": lambda rs: rs.reverse(),
    }
    for name, mut in cases.items():
        rs = copy.deepcopy(legal); mut(rs); e.write(rs)
        before = e.launches
        ad2 = e.adapter()
        obs = attempt(lambda: ad2.submit(e.req(idempotency_key="k-" + name, task_id="t2")).run_id); wait(ad2)
        report(f"R1 derived history: {name}", {"new_launches": e.launches - before, "submit": obs["outcome"],
               "raised": obs.get("type")}, "refused, new_launches 0")
    e.write(legal)
    before = e.launches
    ad3 = e.adapter()
    same = attempt(lambda: ad3.submit(e.req()).run_id)
    report("R1 control: legal reserved/dispatched prefix, reopen, same key",
           {"new_launches": e.launches - before, "replayed_same_run": same.get("value") == legal[0]["run_id"]},
           "replay, new_launches 0")
    e.close()


def failing_scandir(target):
    real = os.scandir
    def f(path="."):
        if os.path.realpath(os.fspath(path)) == os.path.realpath(target):
            raise PermissionError(13, "injected", os.fspath(path))
        return real(path)
    return f


def r2():
    for label, sub in (("root", ""), ("subdir src", "src")):
        e = Env()
        ad = e.adapter()
        with mock.patch("os.scandir", failing_scandir(os.path.join(e.ws, sub) if sub else e.ws)):
            obs = attempt(lambda: ad.submit(e.req()).run_id)
            res = None
            if obs["outcome"] == "returned":
                res = ad.collect(ADDRESS, obs["value"], timeout=60)
        report(f"R2 scandir PermissionError on {label} before launch AND at collect",
               {"launches": e.launches, "submit": obs["outcome"], "raised": obs.get("type"),
                "state": ad.status(ADDRESS, obs["value"]).state if res else None,
                "workspace_unchanged": res.artifacts[0]["workspace_unchanged"] if res else None},
               "refused before launch (launches 0)")
        e.close()
    variants = {"missing": {"delete": ["status"]}, "null": {"set": {"status": None}},
                "unknown string": {"set": {"status": "maybe"}}, "array": {"set": {"status": ["error"]}}}
    for name, edit in variants.items():
        e = Env(scenario={"edit_events": [dict(type="tool_result", index=2, **edit)]})
        ad = e.adapter()
        obs = attempt(lambda: ad.collect(ADDRESS, ad.submit(e.req()).run_id, timeout=60))
        if obs["outcome"] == "returned":
            art = obs["value"].artifacts[0]
            observed = {"declared": obs["value"].declared_status, "denied": art["denied_tool_requests"]}
        else:
            observed = {"collect": "raised", "type": obs["type"]}
        report(f"R2 write_file tool_result.status {name}", observed,
               "not clean, write_file not listed as denied")
        e.close()
    for name, edit in (("tool_result.tool_id array", {"type": "tool_result", "index": 0, "set": {"tool_id": ["x"]}}),
                       ("event type array", {"type": "message", "index": 1, "set": {"type": ["message"]}})):
        e = Env(scenario={"edit_events": [edit]})
        ad = e.adapter()
        holder = {}
        def go():
            holder["run"] = ad.submit(e.req()).run_id
            return ad.collect(ADDRESS, holder["run"], timeout=60).declared_status
        obs = attempt(go)
        state = ad.status(ADDRESS, holder["run"]).state if obs["outcome"] == "returned" else None
        report(f"R2 {name}", {"collect": obs["outcome"], "raised": obs.get("type"), "declared": obs.get("value"),
                              "state": state},
               "returned, failed with diagnostic")
        e.close()
    for head in ([], None, "s", 3, True):
        e = Env(scenario={"chat_head": head})
        ad = e.adapter()
        obs = attempt(lambda: ad.collect(ADDRESS, ad.submit(e.req()).run_id, timeout=60).declared_status)
        report(f"R2 chat record first line {json.dumps(head)}", {"collect": obs["outcome"], "raised": obs.get("type")},
               "returned with diagnostic")
        e.close()


def r3():
    e = Env()
    cwd = os.getcwd(); os.chdir(e.tmp)
    try:
        ad = e.adapter()
        obs = attempt(lambda: ad.submit(e.req(workspace="ws")).run_id); wait(ad)
    finally:
        os.chdir(cwd)
    report("R3 existing relative directory 'ws'", {"launches": e.launches, "submit": obs["outcome"],
           "raised": obs.get("type")}, "refused, launches 0")
    e.close()
    # A real directory whose name contains a backslash: the replayed workspace
    # string then contains one, like a Windows path does.
    e = Env(ws_name="win\\like")
    # HARK12_REPRO_FAKE_R3 names another fake_cli (e.g. the baseline's) for this case only.
    ad = e.adapter(fake=os.environ.get("HARK12_REPRO_FAKE_R3"))
    obs = attempt(lambda: ad.collect(ADDRESS, ad.submit(e.req()).run_id, timeout=60))
    report("R3 fake_cli replay with a backslash in the workspace path",
           {"declared": obs["value"].declared_status if obs["outcome"] == "returned" else obs["type"],
            "json_problems": [u for u in obs["value"].unverified if "JSON" in u],
            "cli_stderr_invalid_escape": any("Invalid \\escape" in open(os.path.join(r.run_dir, "stderr.txt"),
                                                                       errors="replace").read()
                                             for r in ad._runs.values())
            if obs["outcome"] == "returned" else None},
           "success (valid JSON replay)")
    e.close()


if __name__ == "__main__":
    r1(); r2(); r3()
    for item in OUT:
        print(json.dumps(item, ensure_ascii=False, sort_keys=True))
    print(json.dumps({"cases": len(OUT), "tree": ROOT}))
