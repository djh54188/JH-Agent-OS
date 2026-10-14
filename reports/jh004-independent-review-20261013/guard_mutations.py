"""Offline mutation checks of submitted JH-004 guard tests; no on-disk product edits."""
import contextlib,inspect,io,json,socket,sys,unittest,textwrap
from pathlib import Path
from unittest.mock import patch
WT=Path(r"D:\JH-Agent OS\data\worktrees\n1-artifact-entry")
sys.path.insert(0,str(WT));sys.path.insert(0,str(WT/"tests"))
from src import cli
from src.storage import TaskStorage
from src.production_executor import NoToolExecutor
from src.mock_components import ScriptedWorkerAdapter,ScriptedReviewerAdapter
import test_jh004_artifact_entry as tests
OUT=WT/"reports/jh004-independent-review-20261013"
def run():
    buf=io.StringIO()
    with patch.object(cli,"has_credential",return_value=True),patch.object(cli,"load_model_adapter",side_effect=lambda role:ScriptedWorkerAdapter(tool_requests_by_turn=[[]]) if role=="worker" else ScriptedReviewerAdapter(mode="pass")),patch.object(socket.socket,"connect",side_effect=AssertionError("review mutation runner forbids network")):
        result=unittest.TextTestRunner(stream=buf,verbosity=0).run(unittest.defaultTestLoader.loadTestsFromModule(tests))
    return {"ran":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),"skipped":len(result.skipped),"output":buf.getvalue()}
def weakened(fn,old,new):
    source=textwrap.dedent(inspect.getsource(fn))
    assert source.count(old)==1,(fn.__name__,source.count(old))
    namespace=dict(fn.__globals__)
    exec(compile(source.replace(old,new,1),"<independent-guard-mutation>","exec"),namespace)
    return namespace[fn.__name__]
rows=[]
control=run();assert control["failures"]==0 and control["errors"]==0,control
rows.append({"case":"unmodified_control",**control})
original_execute=NoToolExecutor.execute
def success_instead(self,req):
    r=original_execute(self,req);r["status"]="success";return r
variants=[
("M01_no_tool_result_success",patch.object(NoToolExecutor,"execute",success_instead)),
("M02_accept_untrusted_input_keys",patch.object(cli,"resolve_trusted_evaluator",weakened(cli.resolve_trusted_evaluator,'if set(input_data) != {"records"}:','if "records" not in input_data:'))),
("M03_skip_source_binding",patch.object(cli,"check_source_binding",lambda *a,**kw:None)),
("M04_wrong_source_stamp",patch.object(cli,"stamp_trusted_binding",weakened(cli.stamp_trusted_binding,'"source": "fixture" if test_fixture_mode else "production"','"source": "production" if test_fixture_mode else "fixture"'))),
# This decorator remains transactional: compile the wrapped source to preserve its atomic boundary.
("M05_recover_preempts_owner",patch.object(TaskStorage,"recover_interrupted_tasks",weakened(TaskStorage.recover_interrupted_tasks,'if held and held["owner_id"]:','if False:')))
]
for name,mutation in variants:
    if len(sys.argv)>1 and name!=sys.argv[1]:continue
    with mutation:
        result=run()
    verdict="detected_by_assertion" if result["failures"]>0 and result["errors"]==0 else "harness_error" if result["errors"] else "survived"
    rows.append({"case":name,"verdict":verdict,**result})
report={"scope":"submitted 20-test JH-004 suite, in-memory guard mutations; no files patched","api_calls":0,"rows":rows,"notes":["Network blocked in parent test process; subprocess CLI tests explicitly use --test-fixture-mode","Detection is counted only for test assertion failures with zero errors; this does not cover the newly discovered defects"]}
(OUT/("mutation-followup-results.json" if len(sys.argv)>1 else "mutation-results.json")).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps([{k:v for k,v in row.items() if k!="output"} for row in rows],ensure_ascii=False))
