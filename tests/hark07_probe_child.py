"""Child process for tests/test_hark07_source_isolation.py (not a test module).

Each mode builds its own AppInterface over a SHARED probe directory, using the
offline fake transport only, and prints one JSON line with what it observed.

  race <tmp> <go_file>      wait for go_file, prepare + start (sync runner,
                            thread/start timeout so the run stays unresolved)
  accept_and_exit <tmp>     prepare + start with a deferred runner, then exit
                            without running the worker (crash after acceptance)
  hold_lock <tmp> <ready> <seconds>   hold the source lock, then exit
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_hark06_session_probe as fx  # noqa: E402
from src.app_api import ApiError  # noqa: E402
import src.session_probe as probe  # noqa: E402

if os.name != "nt":
    fx.codex_mod.is_special_path = fx._posix_evidence_seam


def attempt(rig):
    try:
        prepared = rig.api.prepare_session_probe({})
        started = rig.api.start_session_probe({"probe_id": prepared["probe_id"], "confirm": True})
        return {"accepted": started.get("accepted") is True}
    except ApiError as exc:
        return {"refused": exc.code}


def main():
    mode, tmp = sys.argv[1], sys.argv[2]
    if mode == "race":
        go = Path(sys.argv[3])
        rig = fx.Rig(tmp, transport_kw={"timeout_method": "thread/start"})
        deadline = time.time() + 30
        while not go.exists() and time.time() < deadline:
            time.sleep(0.005)
        result = attempt(rig)
        print(json.dumps(dict(result, pid=os.getpid(), thread_starts=rig.thread_starts())))
    elif mode == "accept_and_exit":
        rig = fx.Rig(tmp, runner="deferred")
        result = attempt(rig)
        print(json.dumps(dict(result, pid=os.getpid(), thread_starts=rig.thread_starts(),
                              deferred=len(rig.deferred))), flush=True)
        os._exit(0)                      # the worker never runs
    elif mode == "hold_lock":
        ready, seconds = Path(sys.argv[3]), float(sys.argv[4])
        probe_dir = Path(tmp) / "probe"
        probe_dir.mkdir(parents=True, exist_ok=True)
        with probe.acquire_source_lock(probe_dir):
            ready.write_text("held", encoding="utf-8")
            time.sleep(seconds)
        print(json.dumps({"held_seconds": seconds}))
    else:
        raise SystemExit(f"unknown mode {mode}")


if __name__ == "__main__":
    main()
