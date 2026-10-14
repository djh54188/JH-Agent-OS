"""Independent JH-004 behavioural probes. Offline fixtures only; no product edits."""
import copy,hashlib,io,json,shutil,subprocess,sys,uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
WT=Path(r"D:\JH-Agent OS\data\worktrees\n1-artifact-entry")
sys.path.insert(0,str(WT))
sys.path.insert(0,str(WT/"tests"))
from src import cli
from src.mock_components import ScriptedWorkerAdapter, ScriptedReviewerAdapter
from src.storage import TaskStorage
from test_jh004_artifact_entry import _payload, CRITERIA, EXPECTED_SORTED
OUT=WT/"reports/jh004-independent-review-20261013"
CASE=WT/".test-tmp"/("jh004-independent-"+uuid.uuid4().hex)
assert CASE.resolve().is_relative_to(WT.resolve())
CASE.mkdir(parents=True)
def run_cli(args):
    buf=io.StringIO()
    with redirect_stdout(buf):
        code=cli.main(args)
    return code,json.loads(buf.getvalue())
def adapters():
    return ScriptedWorkerAdapter(tool_requests_by_turn=[[]],artifact_content_by_turn=[json.dumps(EXPECTED_SORTED,ensure_ascii=False,sort_keys=True)]),ScriptedReviewerAdapter(mode="pass")
def run_submission(name,criteria,fixture):
    d=CASE/name; ws=d/"ws";ws.mkdir(parents=True)
    db=d/"state.sqlite3";tid="task_"+uuid.uuid4().hex[:12]
    payload=_payload(tid,ws,acceptance_criteria=criteria)
    task=d/"task.json";task.write_text(json.dumps(payload),encoding="utf-8")
    worker,reviewer=adapters()
    response_flags={"worker":[],"reviewer":[]}
    def capture(original,role):
        def wrapped(*a,**kw):
            response=original(*a,**kw)
            response_flags[role].append(response.is_mock)
            return response
        return wrapped
    worker.invoke_structured=capture(worker.invoke_structured,"worker")
    reviewer.invoke_structured=capture(reviewer.invoke_structured,"reviewer")
    args=["--db",str(db),"--workspace",str(ws)]
    if fixture:args.append("--test-fixture-mode")
    args.extend(["submit",str(task),"--run"])
    if fixture:
        make=cli.create_orchestrator
        def assembly(*a,**kw):
            return make(*a,worker_adapter=worker,reviewer_adapter=reviewer,**kw)
        with patch.object(cli,"create_orchestrator",side_effect=assembly):
            code,body=run_cli(args)
    else:
        with patch.object(cli,"has_credential",return_value=True),patch.object(cli,"load_model_adapter",side_effect=lambda role:worker if role=="worker" else reviewer):
            code,body=run_cli(args)
    ro=TaskStorage(db,read_only=True)
    try:
        row={"case":name,"cli_exit_code":code,"state":ro.get_task(tid)["status"],"summary_source":body.get("task_result",{}).get("source_binding"),"acceptance":body.get("task_result",{}).get("acceptance_results"),"artifact":ro.get_latest_artifact(tid),"worker_calls":worker.call_count,"reviewer_calls":reviewer.call_count,"mock_adapter_classes":[type(worker).__name__,type(reviewer).__name__],"fixture_mode":fixture,"actual_response_is_mock_flags":response_flags}
    finally:ro.close()
    return row,db
rows=[]
try:
    control,db=run_submission("canonical_criteria_positive",list(CRITERIA),True)
    rows.append(control)
    impossible=["Input records contain exactly 999 rows","Output JSON must contain exactly 999 rows"]
    wrong,_=run_submission("non_template_criteria",impossible,True)
    wrong["input_row_count"]=2
    wrong["actual_output_row_count"]=len(json.loads(wrong["artifact"]["content"]))
    wrong["false_acceptance_reproduced"]=wrong["state"]=="succeeded" and all(a["passed"] for a in wrong["acceptance"]) and wrong["actual_output_row_count"]!=999
    rows.append(wrong)
    mocked,_=run_submission("mock_responses_in_production_assembly",list(CRITERIA),False)
    mocked["worker_is_mock"]=bool(mocked["actual_response_is_mock_flags"]["worker"]) and all(mocked["actual_response_is_mock_flags"]["worker"])
    mocked["mock_marked_as_production_success"]=mocked["state"]=="succeeded" and mocked["summary_source"]["source"]=="production" and mocked["worker_is_mock"]
    rows.append(mocked)
    before_hash=hashlib.sha256(db.read_bytes()).hexdigest()
    for command in ["status","summary","events","artifact"]:
        code,body=run_cli(["--db",str(db),command,"task_missing_probe"])
        rows.append({"case":"missing_task_"+command,"command":command,"cli_exit_code":code,"body":body})
    rows.append({"case":"read_only_db_hash","unchanged":hashlib.sha256(db.read_bytes()).hexdigest()==before_hash})
    # Unknown records metadata and non-finite values are validation observations, not findings by themselves.
    sample=_payload("task_shapeprobe1",CASE)
    for label,record in [("extra_record_field",{"region":"a","sales":1,"unrecognized":"kept"}),("nonfinite_sales",{"region":"a","sales":float("nan")})]:
        p=copy.deepcopy(sample);p["metadata"]["input_data"]["records"]=[record]
        try:
            cli.resolve_trusted_evaluator(p)
            rows.append({"case":label,"accepted":True})
        except Exception as exc:rows.append({"case":label,"accepted":False,"exception":type(exc).__name__})
    root=Path(r"D:\JH-Agent OS")
    origins=[]
    for directory in [root,WT]:
        c=subprocess.run([sys.executable,"-X","utf8","-B","-c","from src.cli import main,PROJECT_ROOT; import src.cli as c; print(c.__file__); print(hasattr(c, 'RECORDS_TRANSFORM_TEMPLATE_ID'))"],cwd=directory,capture_output=True,text=True,encoding="utf-8",timeout=20)
        origins.append({"cwd":str(directory),"exit_code":c.returncode,"stdout":c.stdout.strip().splitlines(),"stderr":c.stderr.strip()})
    rows.append({"case":"skill_hardcoded_workdir_module_origins","origins":origins,"skill_instructs_original_root":str(root).replace("\\","/") in (WT/".agents/skills/jh-task/SKILL.md").read_text(encoding="utf-8")})
    report={"source":"independent behavioural probes","api_calls":0,"rows":rows,"fixtures_cleaned":False,"limitations":["Model responses are scripted offline injections, not real model evidence","Production mock-response case tests whether the declared is_mock flag is gated; it does not demonstrate unprivileged code injection","This script does not modify shared product source"]}
finally:
    assert CASE.parent==WT/".test-tmp" and CASE.name.startswith("jh004-independent-")
    shutil.rmtree(CASE)
report["fixtures_cleaned"]=not CASE.exists()
(OUT/"behavioural-probes.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
brief=[{k:v for k,v in row.items() if k not in {"artifact","acceptance"}} for row in rows]
print(json.dumps({"rows":brief,"fixtures_cleaned":report["fixtures_cleaned"]},ensure_ascii=False))
