"""Windows cross-process admission probe without multiprocessing named pipes.
Two Python worker processes, memory launcher; no Gemini, model, or network.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "candidate"))
from src import agent_session_gemini as gem
from src.agent_session import SessionAddress, SubmitRequest


class Done:
    returncode = 0
    def wait(self, timeout=None): return 0
    def poll(self): return 0


def worker(case, index, key, timeout):
    def launch(argv, **kwargs):
        with open(case / "launches.jsonl", "ab") as f:
            f.write(json.dumps({"worker": index, "pid": os.getpid()}).encode() + b"\n")
        time.sleep(0.12)
        return Done()
    config = gem.GeminiCliConfig(("NOT_EXECUTED",), str(case / "home"), str(case / "evidence"),
                                registry_lock_timeout=timeout)
    a = gem.GeminiCliAgentSessionAdapter(config, launcher=launch, version_probe=lambda _: "0.65.0")
    (case / f"ready-{index}").write_bytes(b"ready")
    deadline = time.monotonic() + 10
    while not (case / "go").exists():
        if time.monotonic() >= deadline: raise RuntimeError("review barrier timed out")
        time.sleep(0.01)
    request = SubmitRequest("t", 1, SessionAddress("gemini-cli", "gemini-cli/local", "new"),
                            "Read source.", str(case / "ws"), idempotency_key=key)
    try:
        s = a.submit(request)
        result = {"worker": index, "run_id": s.run_id, "state": s.state}
    except Exception as e:
        result = {"worker": index, "exception": type(e).__name__, "message": str(e)}
    (case / f"result-{index}.json").write_bytes(json.dumps(result).encode())


def parent(round_name):
    root = HERE / round_name
    root.mkdir(exist_ok=False)
    rows = []
    for name, keys, timeout in [("same-key", ["same", "same"], 2.0),
                                ("different-keys", ["first", "second"], 2.0),
                                ("held-lock", ["busy"], 0.15)]:
        case = root / name
        (case / "ws").mkdir(parents=True)
        (case / "ws/source.txt").write_bytes(b"source\n")
        (case / "home").mkdir()
        processes = []
        handles = []
        for i, key in enumerate(keys):
            stdout = open(case / f"worker-{i}.stdout", "wb")
            stderr = open(case / f"worker-{i}.stderr", "wb")
            handles += [stdout, stderr]
            p = subprocess.Popen([sys.executable, "-X", "utf8", "-B", str(__file__), "--worker", str(case),
                                  str(i), key, str(timeout)], stdin=subprocess.DEVNULL,
                                 stdout=stdout, stderr=stderr,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            processes.append(p)
        deadline = time.monotonic() + 10
        while not all((case / f"ready-{i}").exists() for i in range(len(keys))):
            if time.monotonic() >= deadline: raise RuntimeError("review workers did not become ready")
            time.sleep(0.01)
        def finish():
            (case / "go").write_bytes(b"go")
            for p in processes: p.wait(timeout=12)
        if name == "held-lock":
            with gem.registry_lock(str(case / "evidence"), 1.0): finish()
        else: finish()
        for h in handles: h.close()
        results = [json.loads((case / f"result-{i}.json").read_bytes()) for i in range(len(keys))]
        launches = (case / "launches.jsonl").read_bytes().splitlines() if (case / "launches.jsonl").exists() else []
        if name == "same-key":
            ok = len(launches) == 1 and len({r.get("run_id") for r in results}) == 1 and all("exception" not in r for r in results)
        elif name == "different-keys":
            ok = len(launches) == 2 and len({r.get("run_id") for r in results}) == 2 and all("exception" not in r for r in results)
        else:
            ok = not launches and results[0].get("exception") == "RegistryBusy"
        rows.append({"case": name, "passes": ok, "launches": len(launches), "results": results,
                     "worker_exit_codes": [p.returncode for p in processes]})
    report = {"os_name": os.name, "lock_backend": "msvcrt.locking" if os.name == "nt" else "flock",
              "real_gemini_processes": 0, "model_calls": 0, "cases": rows}
    (root / "results.json").write_bytes(json.dumps(report, indent=2).encode())
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        worker(pathlib.Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4], float(sys.argv[5]))
    else:
        ap = argparse.ArgumentParser(); ap.add_argument("--round", required=True)
        parent(ap.parse_args().round)
