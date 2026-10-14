"""Targeted in-memory mutations for R01-R04; excludes one known wording failure only."""
import inspect,io,json,socket,sys,textwrap,types,unittest
from pathlib import Path
from unittest.mock import patch
WT=Path(r"D:\JH-Agent OS\data\worktrees\n1-artifact-entry")
sys.path.insert(0,str(WT));sys.path.insert(0,str(WT/"tests"))
from src import cli
from src.orchestrator import Orchestrator
import test_jh004_artifact_entry as testmod
OUT=WT/"reports/jh004-rereview-20261014"
class OfflineFailureTransport:
    def __init__(self,role):
        self.model_id="offline-injected-"+role;self.config={}
    def invoke_structured(self,*a,**kw):
        return types.SimpleNamespace(parsed_json={},total_tokens=5,is_mock=True)
def flatten(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):yield from flatten(item)
        else:yield item
def run():
    cases=[t for t in flatten(unittest.defaultTestLoader.loadTestsFromModule(testmod)) if not t.id().endswith(".test_skill_binds_to_own_worktree")]
    stream=io.StringIO()
    with patch.object(cli,"has_credential",return_value=True),patch.object(cli,"load_model_adapter",side_effect=lambda role:OfflineFailureTransport(role)),patch.object(socket.socket,"connect",side_effect=AssertionError("mutation runner forbids network")):
        r=unittest.TextTestRunner(stream=stream,verbosity=0).run(unittest.TestSuite(cases))
    return {"ran":r.testsRun,"failures":len(r.failures),"errors":len(r.errors),"skipped":len(r.skipped),"output":stream.getvalue()}
def weaken(fn,old,new):
    source=textwrap.dedent(inspect.getsource(fn))
    assert source.count(old)==1,(fn.__name__,source.count(old))
    ns=dict(fn.__globals__)
    exec(compile(source.replace(old,new,1),"<independent-rereview-mutation>","exec"),ns)
    return ns[fn.__name__]
control=run()
assert control["failures"]==control["errors"]==0,control
rows=[{"case":"unmodified_control_without_known_wording_failure",**control}]
variants=[
("M01_only_criteria_count",patch.object(cli,"resolve_trusted_evaluator",weaken(cli.resolve_trusted_evaluator,"if list(criteria) != list(RECORDS_TRANSFORM_CRITERIA):","if len(criteria) != 2:"))),
("M02_skip_known_mock_assembly_rejection",patch.object(cli,"_is_known_mock_adapter",lambda _:False)),
("M03_skip_runtime_source_gate",patch.object(Orchestrator,"_invoke_model",weaken(Orchestrator._invoke_model,"if self._require_real_responses:","if False:"))),
("M04_allow_unknown_response_source",patch.object(Orchestrator,"_invoke_model",weaken(Orchestrator._invoke_model,"if source_flag is not False:","if source_flag is True:"))),
("M05_omit_source_audit_event",patch.object(Orchestrator,"_record_response_source",lambda *a,**kw:None)),
("M06_ignore_known_tainted_history",patch.object(cli,"_production_taint_blocker",lambda *a,**kw:None)),
("M07_events_accepts_missing_task",patch.object(cli,"main",weaken(cli.main,"ro_storage.get_task(args.task_id)  # fail closed on missing task, like other queries","pass  # weakened existence check for review mutation")))
]
for name,mutation in variants:
    with mutation:r=run()
    verdict="detected_by_assertion" if r["failures"]>0 and r["errors"]==0 else "error_not_counted" if r["errors"] else "survived"
    rows.append({"case":name,"verdict":verdict,**r})
report={"client_date":"2026-10-14","scope":"submitted JH-004 tests with only known wording failure excluded; full 180-test result remains failed","exclusions":[{"test":"test_skill_binds_to_own_worktree","reason":"unmodified full suite independently failed on containing/contains wording mismatch"}],"api_calls":0,"rows":rows,"limits":["Does not test rejection of missing historical source records; independent probe shows that gap","No product files patched; parent network forbidden and child CLI cases explicitly fixture mode"]}
(OUT/"mutation-results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps([{k:v for k,v in row.items() if k!="output"} for row in rows],ensure_ascii=False))
