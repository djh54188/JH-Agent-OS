"""Reproduce the Hark-派单-13 R1 tail: registry lost after a dispatch (offline).

A first adapter A submits key K (in-memory launcher: no process, no model; the
outcome stays undetermined), leaving `reserved`/`dispatched` records and
`runs/<run_id>/`. The registry is then deleted / emptied / blanked / cut back,
a NEW adapter B is created on the same evidence_root and submits K again.
Controls: a truly fresh store, a complete registry, legal reserved-only prefixes.

Uses only API present at baseline d9cbbf7, so the same script runs on the
baseline tree and on the candidate tree. Prints one JSON object per case.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import agent_session_gemini as gem  # noqa: E402
from src.agent_session import SessionAddress, SubmitRequest  # noqa: E402

ADDRESS = SessionAddress(app_kind="gemini-cli", instance_id="gemini-cli/local", session_id="new")


class PendingProcess:
    """Stands in for a launched CLI whose outcome is never determined."""
    returncode = None

    def poll(self):
        return None

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired("pending", timeout)


class Store:
    def __init__(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="hark13-repro-"))
        self.ws = os.path.join(self.tmp, "ws")
        os.makedirs(os.path.join(self.ws, "src"))
        with open(os.path.join(self.ws, "src", "a.py"), "w") as fh:
            fh.write("x = 1\n")
        self.ev = os.path.join(self.tmp, "evidence")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.launches = 0

    @property
    def registry(self):
        return os.path.join(self.ev, "registry.jsonl")

    def adapter(self):
        cfg = gem.GeminiCliConfig(argv_prefix=("gemini-not-started",), cli_home=self.home, evidence_root=self.ev)

        def launcher(argv, **kw):
            self.launches += 1
            return PendingProcess()
        return gem.GeminiCliAgentSessionAdapter(cfg, launcher=launcher, version_probe=lambda c: "0.65.0")

    def req(self, key="K", task="t"):
        return SubmitRequest(task_id=task, attempt=1, session=ADDRESS, instructions="Locate x.",
                             workspace=self.ws, idempotency_key=key)

    def runs(self):
        d = os.path.join(self.ev, "runs")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def close(self):
        d = os.path.join(self.ev, "runs")
        if os.path.isdir(d):
            os.chmod(d, 0o700)
        shutil.rmtree(self.tmp, ignore_errors=True)


def submit(store, adapter, **kw):
    before = store.launches
    try:
        status = adapter.submit(store.req(**kw))
        return {"outcome": "returned", "run_id": status.run_id, "state": status.state,
                "new_launches": store.launches - before}
    except Exception as exc:
        return {"outcome": "raised", "type": type(exc).__name__, "new_launches": store.launches - before}


def lose(store, how, keep_lines=None):
    if how == "delete":
        os.remove(store.registry)
    elif how == "zero":
        open(store.registry, "wb").close()
    elif how == "blank":
        with open(store.registry, "wb") as fh:
            fh.write(b" \n\t\n\n")
    elif how == "keep":
        with open(store.registry, "rb") as fh:
            lines = fh.read().splitlines(keepends=True)
        with open(store.registry, "wb") as fh:
            fh.write(b"".join(lines[:keep_lines]))


def main():
    out = []

    def case(name, observed, expected):
        out.append({"case": name, "observed": observed, "expected": expected})

    for how in ("delete", "zero", "blank"):
        for repeat in (1, 2):
            s = Store()
            a = s.adapter()
            first = submit(s, a)
            lose(s, how)
            b = s.adapter()
            obs = submit(s, b)
            obs["first_run_id_kept"] = first["run_id"] in s.runs()
            obs["same_run_as_first"] = obs.get("run_id") == first["run_id"]
            case(f"loss: registry {how} (fresh root #{repeat})", obs, "new_launches 0 (refused)")
            s.close()

    s = Store()
    a = s.adapter()
    k1 = submit(s, a, key="K1", task="t1")
    k2 = submit(s, a, key="K2", task="t2")
    lose(s, "keep", keep_lines=2)               # only K1's reserved+dispatched remain
    b = s.adapter()
    obs = submit(s, b, key="K2", task="t2")
    obs["run_dirs"] = len(s.runs())
    case("loss: non-empty tail lost (K1 kept, K2 cut)", obs, "new_launches 0 (refused)")
    s.close()

    s = Store()
    case("control: truly fresh store", submit(s, s.adapter()), "new_launches 1")
    s.close()

    s = Store()
    a = s.adapter()
    first = submit(s, a)
    obs = submit(s, s.adapter())
    obs["same_run_as_first"] = obs.get("run_id") == first["run_id"]
    case("control: complete registry reopened, same key", obs, "replay same run, unknown, new_launches 0")
    s.close()

    s = Store()
    a = s.adapter()
    first = submit(s, a)
    lose(s, "keep", keep_lines=1)               # legal reserved-only prefix
    obs = submit(s, s.adapter())
    obs["same_run_as_first"] = obs.get("run_id") == first["run_id"]
    case("control: reserved-only prefix, same key", obs, "replay same run, unknown, new_launches 0")
    s.close()

    s = Store()
    a = s.adapter()
    r1 = submit(s, a, key="K1", task="t1")
    r2 = submit(s, a, key="K2", task="t2")
    with open(s.registry, "rb") as fh:
        lines = [json.loads(l) for l in fh.read().splitlines()]
    with open(s.registry, "wb") as fh:
        for rec in lines:
            if rec["event"] == "reserved":
                fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
    b = s.adapter()
    o1 = submit(s, b, key="K1", task="t1")
    o2 = submit(s, b, key="K2", task="t2")
    case("control: two runs each with only reserved, both keys",
         {"K1_same": o1.get("run_id") == r1["run_id"], "K2_same": o2.get("run_id") == r2["run_id"],
          "states": [o1.get("state"), o2.get("state")], "new_launches": o1["new_launches"] + o2["new_launches"]},
         "both replay, unknown, new_launches 0")
    s.close()

    for item in out:
        print(json.dumps(item, ensure_ascii=False, sort_keys=True))
    print(json.dumps({"cases": len(out), "tree": ROOT}))


if __name__ == "__main__":
    main()
