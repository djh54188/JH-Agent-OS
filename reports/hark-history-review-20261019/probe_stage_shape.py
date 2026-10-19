"""Hark-03 independent stage-shape counterexamples, entirely synthetic.
Use --repo CHECKOUT --out NEW_DIR --expect candidate|fixed.
candidate reproduces the 28 TypeErrors; fixed requires all invalid stages corrupt.
"""
import argparse,copy,json,sys
from pathlib import Path
from unittest.mock import patch

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--repo",required=True)
    ap.add_argument("--out",required=True)
    ap.add_argument("--expect",choices=["candidate","fixed"],required=True)
    args=ap.parse_args()
    repo=Path(args.repo).resolve()
    out=Path(args.out).resolve()
    if out.exists():
        raise SystemExit("Refusing existing --out; use a fresh evidence directory.")
    sys.path.insert(0,str(repo))
    import src.app_api as api_mod
    from src.agent_session_codex import parse_registry_text
    out.mkdir(parents=True,exist_ok=False)
    base={"event":"reserved","run_id":"synthetic-stage-run","app_kind":"codex",
          "instance_id":"codex-app-server/local","session_id":"new:synthetic",
          "task_id":"synthetic-stage-task","attempt":1,"request_snapshot":"{}",
          "request_fingerprint":"a"*64,"idempotency_key":"synthetic-stage-key",
          "native_session_id":None,"native_model":None,"native_turns":[],
          "state":"queued","native_state":"reserved","at":"2026-10-19T00:00:00Z"}
    branches=[
      ("failed_thread_refused","dispatch_failed","failed","thread/start-refused","thread/start",None),
      ("failed_thread_no_id","dispatch_failed","failed","thread/start-no-id","thread/start",None),
      ("failed_model","dispatch_failed","failed","model-not-honored","thread/start","synthetic-thread"),
      ("failed_turn_refused","dispatch_failed","failed","turn/start-refused","turn/start","synthetic-thread"),
      ("failed_turn_no_id","dispatch_failed","failed","turn/start-no-id","turn/start","synthetic-thread"),
      ("uncertain_thread","dispatch_uncertain","unknown","dispatch-uncertain","thread/start",None),
      ("uncertain_turn","dispatch_uncertain","unknown","dispatch-uncertain","turn/start","synthetic-thread")]
    values=[("correct",None),("null",None),("empty",""),("unknown","other"),
            ("bool",True),("int",7),("float",1.5),("empty_array",[]),
            ("array",["thread/start"]),("empty_object",{}),("object",{"value":"turn/start"})]
    rows=[]
    for label,event,state,native,normal,session in branches:
        for kind,value in values:
            last=dict(base,event=event,state=state,native_state=native,native_session_id=session,
                      stage=normal if kind=="correct" else value)
            items=[copy.deepcopy(base),last]
            payload=("\n".join(json.dumps(r) for r in items)+"\n").encode()
            path=out/(label+"-"+kind+".jsonl")
            path.write_bytes(payload)
            parse_registry_text(payload.decode())  # each input passes the existing field validator
            api=api_mod.AppInterface(db_path=out/"absent.sqlite3",runs_path=path)
            calls=[]
            with patch.object(api_mod,"raw_evidence_state",
                    side_effect=lambda *a,**k:(calls.append(1) or ("missing","synthetic probe"))):
                try:
                    result=api.list_agent_runs()
                    observed=result["state"]
                    empty=result["runs"]==[] and result["count"]==0
                except Exception as exc:
                    observed=type(exc).__name__
                    empty=False
                try:
                    detail=api.agent_run_detail(base["run_id"])
                    detail_state=detail["state"]
                    no_run=detail.get("run") is None
                except Exception as exc:
                    detail_state=type(exc).__name__
                    no_run=False
            container=kind in ("empty_array","array","empty_object","object")
            expected=("ok" if kind=="correct" else
                      "TypeError" if args.expect=="candidate" and container else "corrupt")
            passed=observed==expected and detail_state==expected and path.read_bytes()==payload
            if expected=="corrupt":
                passed=passed and empty and not calls and no_run
            rows.append({"branch":label,"stage_kind":kind,"observed":observed,
                         "detail_observed":detail_state,
                         "expected":expected,"source_bytes_unchanged":path.read_bytes()==payload,
                         "evidence_calls":len(calls),"passed":passed})
    report={"synthetic":True,"expect":args.expect,"cases":rows,"total":len(rows),
            "exceptions":sum(r["observed"]=="TypeError" for r in rows),
            "model_calls":0,"all_expected":all(r["passed"] for r in rows)}
    (out/"results.json").write_bytes(json.dumps(report,ensure_ascii=False,indent=2).encode())
    print(json.dumps({k:v for k,v in report.items() if k!="cases"},ensure_ascii=False,indent=2))
    return 0 if report["all_expected"] else 1

if __name__=="__main__":
    raise SystemExit(main())
