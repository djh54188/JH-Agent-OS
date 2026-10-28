"""Hark-派单-27 offline replay (run by hark27-zcode-prepare.ps1 AFTER its source checks).

Runs the real ZCodeAgentSessionAdapter end to end against the FAKE host
(tests/fixtures/hark27/fake_zcode.py) in a fresh work root. No model, no network, no ZCode
process, no user configuration, no key: the provider file is written with an empty key into
the isolated home under the work root. Optionally hashes a given zcode.cjs (bytes only; it is
never executed).

Exit codes: 0 OFFLINE_REPLAY_OK, 3 HOST_PIN_MISMATCH, 4 NEEDS_SETUP, 5 REPLAY_NOT_PASSING,
1 internal error. stdout: one JSON object with controlled fields only.
"""
import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FIX = os.path.join(ROOT, "tests", "fixtures", "hark27")
for _p in (ROOT, FIX):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src import agent_session_zcode as Z  # noqa: E402
from src.agent_session import SessionAddress, SubmitRequest  # noqa: E402
import scenarios as S  # noqa: E402

PINNED_ZCODE_CJS_SHA256 = "a055234a6ca3da0172616d3987cf3c617b6a26deb1332d69035300c2a0ae0de1"
PINNED_ZCODE_CJS_BYTES = 15243891


def emit(stage, code, **extra):
    sys.stdout.write(json.dumps({"schema": "hark27/zcode-offline-replay/v1", "stage": stage, **extra},
                                ensure_ascii=False, indent=1) + "\n")
    return code


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-root", required=True)
    ap.add_argument("--zcode-cjs")
    a = ap.parse_args(argv)
    host_pin = "not-checked"
    if a.zcode_cjs:
        try:
            size = os.path.getsize(a.zcode_cjs)
            digest = Z.sha256_file(a.zcode_cjs)
        except OSError:
            return emit("NEEDS_SETUP", 4, summary="zcode.cjs not readable", host_pin="unreadable")
        host_pin = "match" if (digest, size) == (PINNED_ZCODE_CJS_SHA256, PINNED_ZCODE_CJS_BYTES) else "mismatch"
        if host_pin == "mismatch":
            return emit("HOST_PIN_MISMATCH", 3, host_pin=host_pin, summary="zcode.cjs is not the pinned build")
    work = os.path.abspath(a.work_root)
    if os.path.exists(work):
        return emit("NEEDS_SETUP", 4, summary="work root already exists; use a new directory", host_pin=host_pin)
    if Z.ancestor_env_files(os.path.dirname(work)):
        return emit("NEEDS_SETUP", 4, summary="a .env file above the work root would be loaded by the host",
                    host_pin=host_pin)
    ws, ev, homes = (os.path.join(work, n) for n in ("workspace", "evidence", "homes"))
    shutil.copytree(os.path.join(FIX, "workspace"), ws)
    os.makedirs(homes)
    host = os.path.join(work, "fake_zcode.py")
    shutil.copyfile(os.path.join(FIX, "fake_zcode.py"), host)
    with open(os.path.join(homes, "scenario.json"), "w", encoding="utf-8") as fh:
        json.dump(S.normal(), fh)
    cfg = Z.ZCodeConfig(instance_id="zcode-offline-replay", argv_prefix=(sys.executable, host), host_file=host,
                        host_sha256=Z.sha256_file(host), evidence_root=ev, run_home_root=homes,
                        provider_id=S.PROVIDER, model_id=S.MODEL, base_url="http://127.0.0.1:9/offline-replay",
                        key_env=None)
    keep = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP") if k in os.environ}
    ad = Z.ZCodeAgentSessionAdapter(cfg, environ=keep)
    addr = SessionAddress("zcode", "zcode-offline-replay", "new", model_id=S.MODEL)
    req = SubmitRequest(task_id="hark27-offline-replay", attempt=1, session=addr,
                        instructions="Find where greet is defined and read it.", workspace=ws,
                        allowed_scope=("read", "search"), idempotency_key="replay-1")
    st = ad.submit(req)
    for live in ad._live.values():
        live.process.wait(timeout=120)
    st = ad.status(addr, st.run_id)
    col = ad.collect(addr, st.run_id)
    pub = Z.public_summary(col)
    ok = pub["state"] == "declared_complete" and pub["mechanical_problem_count"] == 0
    return emit("OFFLINE_REPLAY_OK" if ok else "REPLAY_NOT_PASSING", 0 if ok else 5, host_pin=host_pin,
                replay=pub, real_model_calls=0, note="fake host only; not evidence about the real ZCode build")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - stable refusal, no traceback text
        sys.exit(emit("INTERNAL_ERROR", 1, error_class=type(exc).__name__))
