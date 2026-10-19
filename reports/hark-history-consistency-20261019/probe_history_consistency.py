"""Synthetic read-only history query counterexamples; no private logs or live calls.

python probe_history_consistency.py --repo REPO --out NEW_DIR --expect baseline
After a candidate fix, use a different NEW_DIR and --expect fixed.
"""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--repo",required=True)
    ap.add_argument("--out",required=True)
    ap.add_argument("--expect",choices=["baseline","fixed"],default=None)
    args=ap.parse_args()
    repo=Path(args.repo).resolve()
    out=Path(args.out).resolve()
    if out.exists():
        raise SystemExit("Refusing to overwrite an existing evidence directory; choose a new --out.")
    if not (repo/"src/app_api.py").is_file():
        raise SystemExit("--repo must point to the intended checkout.")
    sys.path.insert(0,str(repo))
    from src.app_api import AppInterface
    out.mkdir(parents=True,exist_ok=False)
    snapshot=json.dumps({
        "schema":"jh018/submit/v1","app_kind":"codex",
        "instance_id":"codex-app-server/local","session_id":"new:synthetic-probe",
        "model_id":None,"role":"worker","task_id":"synthetic-probe-task","attempt":1,
        "workspace":"SYNTHETIC-ONLY","allowed_scope":[],"budget_tokens":None,
        "instructions":"Synthetic fixture; never dispatched.","artifacts":[]
    },ensure_ascii=False,sort_keys=True)
    base={
        "run_id":"synthetic-history-run-1","app_kind":"codex",
        "instance_id":"codex-app-server/local","session_id":"new:synthetic-probe",
        "task_id":"synthetic-probe-task","attempt":1,
        "request_fingerprint":hashlib.sha256(snapshot.encode()).hexdigest(),
        "request_snapshot":snapshot,"idempotency_key":"synthetic-history-key",
        "native_session_id":None,"native_turns":[],"native_model":None,
        "state":"queued","native_state":"reserved","at":"2026-10-19T00:00:00Z"
    }
    records=[
        dict(base,event="reserved"),
        dict(base,event="dispatched",native_session_id="synthetic-thread-1",
             native_turns=["synthetic-turn-1"],state="running",native_state="turn-started"),
        dict(base,event="collected",native_session_id="synthetic-thread-1",
             native_turns=["synthetic-turn-1"],state="declared_complete",
             native_state="completed",declared_status="completed",
             artifacts=[],raw_refs=[],unverified=["synthetic only"],usage={})
    ]
    cases={
        "valid":copy.deepcopy(records),
        "valid_reserved_prefix":[copy.deepcopy(records[0])],
        "binding_changed":copy.deepcopy(records),
        "fingerprint_changed":copy.deepcopy(records),
        "event_reordered":[copy.deepcopy(records[i]) for i in [2,0,1]],
        "same_key_second_run":copy.deepcopy(records)+[
            dict(copy.deepcopy(r),run_id="synthetic-history-run-2") for r in records]
    }
    cases["binding_changed"][-1]["attempt"]=99
    cases["fingerprint_changed"][-1]["request_fingerprint"]="f"*64
    rows=[]
    for name,items in cases.items():
        target=out/(name+".jsonl")
        payload=("\n".join(json.dumps(r,ensure_ascii=False) for r in items)+"\n").encode()
        target.write_bytes(payload)
        api=AppInterface(db_path=out/"absent.sqlite3",runs_path=target)
        observed=api.list_agent_runs()
        unchanged=target.read_bytes()==payload
        good=name.startswith("valid")
        expected="ok" if args.expect=="baseline" or good else "corrupt"
        row={"case":name,"state":observed["state"],"count":observed["count"],
             "run_ids":[r["run_id"] for r in observed["runs"]],
             "source_bytes_unchanged":unchanged}
        if args.expect:
            row["expected_state"]=expected
            row["passed"]=unchanged and observed["state"]==expected and (
                good or args.expect=="baseline" or (observed["count"]==0 and observed["runs"]==[]))
        rows.append(row)
    report={"synthetic":True,"repo":str(repo),"expect":args.expect,
            "model_calls":0,"cases":rows}
    (out/"results.json").write_bytes(json.dumps(report,ensure_ascii=False,indent=2).encode())
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if not args.expect or all(r["passed"] for r in rows) else 1

if __name__=="__main__":
    raise SystemExit(main())
