"""Adapted independent rereview probes, offline and isolated. No real model evidence."""
import copy,io,json,subprocess,sys,shutil,uuid,hashlib
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch
WT=Path(r"D:\JH-Agent OS\data\worktrees\n1-artifact-entry")
sys.path.insert(0,str(WT));sys.path.insert(0,str(WT/"tests"))
from src import cli
from src.adapters import AdapterError
from src.storage import TaskStorage
from src.mock_components import ScriptedWorkerAdapter,ScriptedReviewerAdapter
from test_jh004_artifact_entry import _payload,EXPECTED_SORTED
OUT=WT/"reports/jh004-closure-20261014"
CASE=WT/".test-tmp"/("jh004-adapted-"+uuid.uuid4().hex)
CASE.mkdir(parents=True)
class OfflineTransport:
    """Simulates source flags for gate testing; every response here is offline."""
    def __init__(self,role,flags=None):
        self.model_id="offline-source-gate-"+role
        self.config={}
        self.role=role
        self.flags=flags or {}
        self.used=[]
        self.inner=ScriptedWorkerAdapter(tool_requests_by_turn=[[]],artifact_content_by_turn=[json.dumps(EXPECTED_SORTED,sort_keys=True)]) if role=="worker" else ScriptedReviewerAdapter(mode="pass")
    def invoke_structured(self,messages,schema,**kwargs):
        response=self.inner.invoke_structured(messages,schema,**kwargs)
        properties=schema.get("properties",{})
        phase="review" if self.role=="reviewer" else "generation" if "artifact_id" in properties else "planning"
        flag=self.flags.get(phase,False)
        if flag=="missing":delattr(response,"is_mock")
        else:response.is_mock=flag
        self.used.append({"phase":phase,"declared_is_mock":getattr(response,"is_mock",None)})
        return response
def run(args):
    buf=io.StringIO()
    with redirect_stdout(buf):
        code=cli.main(args)
    return code,json.loads(buf.getvalue())
def make_case(name,criteria=None):
    folder=CASE/name;ws=folder/"ws";ws.mkdir(parents=True)
    tid="task_"+uuid.uuid4().hex[:12];db=folder/"state.sqlite3"
    p=_payload(tid,ws)
    if criteria is not None:p["acceptance_criteria"]=criteria
    taskfile=folder/"task.json";taskfile.write_text(json.dumps(p),encoding="utf-8")
    return tid,db,ws,taskfile
rows=[]
try:
    for name,criteria in [("wrong_999",["Input records contain exactly 999 rows","Output JSON must contain exactly 999 rows"]),("wrong_order",list(reversed(cli.RECORDS_TRANSFORM_CRITERIA))),("extra_condition",list(cli.RECORDS_TRANSFORM_CRITERIA)+["extra"])]:
        tid,db,ws,taskfile=make_case(name,criteria)
        with patch.object(cli,"create_orchestrator",side_effect=AssertionError("model assembly reached for invalid template")):
            code,body=run(["--db",str(db),"--workspace",str(ws),"--test-fixture-mode","submit",str(taskfile),"--run"])
        rows.append({"case":name,"exit_code":code,"body":body,"db_not_created":not db.exists(),"model_assembly_reached":False})
        assert code==1 and not db.exists()
    tid,db,ws,taskfile=make_case("known_mock_assembly")
    with patch.object(cli,"has_credential",return_value=True),patch.object(cli,"load_model_adapter",side_effect=lambda role:ScriptedWorkerAdapter() if role=="worker" else ScriptedReviewerAdapter(mode="pass")):
        code,body=run(["--db",str(db),"--workspace",str(ws),"submit",str(taskfile),"--run"])
    ro=TaskStorage(db,read_only=True)
    try:task_count=ro._conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0];attempt_count=ro._conn.execute("SELECT COUNT(*) FROM model_attempts").fetchone()[0]
    finally:ro.close()
    rows.append({"case":"known_mock_assembly","exit_code":code,"body":body,"tasks":task_count,"attempts":attempt_count})
    assert code==1 and task_count==attempt_count==0
    for name,flags in [("source_false_control",{}),("mock_planning",{"planning":True}),("mock_generation",{"generation":True}),("mock_review",{"review":True}),("missing_generation_source",{"generation":"missing"})]:
        tid,db,ws,taskfile=make_case(name)
        worker,reviewer=OfflineTransport("worker",flags),OfflineTransport("reviewer",flags)
        with patch.object(cli,"has_credential",return_value=True),patch.object(cli,"load_model_adapter",side_effect=lambda role:worker if role=="worker" else reviewer):
            code,body=run(["--db",str(db),"--workspace",str(ws),"submit",str(taskfile),"--run"])
        ro=TaskStorage(db,read_only=True)
        try:
            task=ro.get_task(tid);bindings=ro.list_review_bindings(tid)
            sources=[e["details"] for e in ro.list_events(tid) if e["event_type"]=="model_response_source"]
            row={"case":name,"exit_code":code,"status":task["status"],"review_bindings":len(bindings),"source_records":sources,"responses":worker.used+reviewer.used}
        finally:ro.close()
        rows.append(row)
        if name=="source_false_control":assert code==0 and row["status"]=="succeeded"
        else:assert code==1 and row["status"]!="succeeded" and row["review_bindings"]==0
    # Historical production migration fixture: a completed call exists with no source event.
    tid,db,ws,taskfile=make_case("historical_missing_source")
    worker,reviewer=OfflineTransport("worker"),OfflineTransport("reviewer")
    with patch.object(cli,"has_credential",return_value=True),patch.object(cli,"load_model_adapter",side_effect=lambda role:worker if role=="worker" else reviewer):
        code,_=run(["--db",str(db),"--workspace",str(ws),"submit",str(taskfile)])
    assert code==0
    storage=TaskStorage(db)
    try:
        aid=storage.begin_attempt(tid,"worker",{"fixture":"completed legacy call without provenance audit"})
        storage.complete_attempt(tid,aid,{"legacy":"response source not recorded"},10)
        blocker_before=cli._production_taint_blocker(storage,tid)
        legacy_before=storage.list_model_attempts(tid)
    finally:storage.close()
    with patch.object(cli,"has_credential",return_value=True),patch.object(cli,"load_model_adapter",side_effect=lambda role:worker if role=="worker" else reviewer):
        code,body=run(["--db",str(db),"--workspace",str(ws),"resume",tid])
    ro=TaskStorage(db,read_only=True)
    try:
        attempts=ro.list_model_attempts(tid)
        sources=[e["details"] for e in ro.list_events(tid) if e["event_type"]=="model_response_source"]
        covered={e.get("attempt_id") for e in sources}
        row={"case":"historical_missing_source","blocker_before":blocker_before,"exit_code":code,"status":ro.get_task(tid)["status"],"legacy_completed_attempt":aid,"attempts":len(attempts),"source_records":len(sources),"uncovered_completed_attempts":[a["attempt_id"] for a in attempts if a["status"]=="completed" and a["attempt_id"] not in covered],"summary_source":body.get("task_result",{}).get("source_binding"),"responses":worker.used+reviewer.used,"model_adapter_load_was_allowed":bool(worker.used or reviewer.used)}
    finally:ro.close()
    rows.append(row)
    # Query parity and actual module root checks under the skill-derived directory.
    ro=TaskStorage(db,read_only=True);ro.close()
    db_hash=hashlib.sha256(db.read_bytes()).hexdigest()
    query=[]
    for command in ("status","summary","events","artifact"):
        code,body=run(["--db",str(db),command,"task_missing_rereview"])
        query.append({"command":command,"exit_code":code,"error":body.get("error")})
    rows.append({"case":"missing_task_queries","results":query,"db_unchanged":hashlib.sha256(db.read_bytes()).hexdigest()==db_hash})
    assert all(q["exit_code"]==1 for q in query) and len({q["error"] for q in query})==1
    skill=WT/".agents/skills/jh-task/SKILL.md";resolved_root=skill.parent.parents[2]
    assert resolved_root==WT
    c=subprocess.run([sys.executable,"-X","utf8","-B","-c","import src.cli;print(src.cli.__file__);print(hasattr(src.cli,'RECORDS_TRANSFORM_TEMPLATE_ID'))"],cwd=resolved_root,text=True,capture_output=True,encoding="utf-8",timeout=15)
    g=subprocess.check_output(["git","rev-parse","--show-toplevel"],cwd=resolved_root,text=True).strip()
    rows.append({"case":"skill_worktree_module","skill_derived_root":str(resolved_root),"git_root":g,"module":c.stdout.strip().splitlines(),"exit_code":c.returncode,"hardcoded_original_root_absent":"D:/JH-Agent OS" not in skill.read_text(encoding="utf-8"),"host_skill_discovery_verified":False})
finally:
    assert CASE.parent==WT/".test-tmp" and CASE.name.startswith("jh004-adapted-")
    shutil.rmtree(CASE)
report={"client_date":"2026-10-14","api_calls":0,"scope":"offline independent fault injection; source_false_control simulates source flag false and is not real model evidence","rows":rows,"fixtures_cleaned":not CASE.exists(),"old_evidence_modified":False}
closed_history=next(row for row in rows if row["case"]=="historical_missing_source")
assert closed_history["blocker_before"] is not None
assert closed_history["exit_code"]==1 and closed_history["status"]=="created"
assert closed_history["attempts"]==1 and closed_history["source_records"]==0
assert not closed_history["model_adapter_load_was_allowed"]
report["historical_missing_source_closed"]=True
(OUT/"adapted-probes.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps([{k:v for k,v in r.items() if k not in {"source_records","responses"}} if r["case"]!="historical_missing_source" else r for r in rows],ensure_ascii=False))
