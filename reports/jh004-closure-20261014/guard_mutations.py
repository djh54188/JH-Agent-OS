"""Focused source-history mutations; submitted 40 tests plus one independent wrong-attempt case."""
import inspect,io,json,socket,sys,textwrap,types,unittest
from pathlib import Path
from unittest.mock import patch
WT=Path(r"D:\JH-Agent OS\data\worktrees\n1-artifact-entry")
sys.path.insert(0,str(WT));sys.path.insert(0,str(WT/"tests"))
from src import cli
import test_jh004_artifact_entry as t
OUT=WT/"reports/jh004-closure-20261014"
class OfflineFailureTransport:
    def __init__(self,role):self.model_id="offline-failure-"+role;self.config={}
    def invoke_structured(self,*a,**kw):return types.SimpleNamespace(parsed_json={},total_tokens=5,is_mock=True)
class IndependentWrongAttempt(t.TestHistoricalSourceCoverage):
    def test_wrong_attempt_id_refused(self):
        tid="task_independent_wrong_attempt"
        self._submit_production(tid)
        aid=self._complete_worker_attempt(tid)
        before=self._snapshot(tid)
        self._record_source(tid,{"attempt_id":aid+"_other","role":"worker","is_mock":False})
        before=self._snapshot(tid)
        code,body=self._resume_production_strict(tid)
        self.assertEqual(code,1)
        self.assertIn("no trusted source record",body)
        self.assertEqual(self._snapshot(tid),before)
def run():
    suite=unittest.defaultTestLoader.loadTestsFromModule(t)
    suite.addTest(IndependentWrongAttempt("test_wrong_attempt_id_refused"))
    stream=io.StringIO()
    with patch.object(cli,"has_credential",return_value=True),patch.object(cli,"load_model_adapter",side_effect=lambda role:OfflineFailureTransport(role)),patch.object(socket.socket,"connect",side_effect=AssertionError("mutation runner forbids network")):
        result=unittest.TextTestRunner(stream=stream,verbosity=0).run(suite)
    return {"ran":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),"skipped":len(result.skipped),"output":stream.getvalue()}
def weakened(old,new):
    fn=cli._production_taint_blocker
    source=textwrap.dedent(inspect.getsource(fn));assert source.count(old)==1,(old,source.count(old))
    ns=dict(fn.__globals__);exec(compile(source.replace(old,new,1),"<source-history-mutation>","exec"),ns)
    return ns[fn.__name__]
control=run();assert control["failures"]==control["errors"]==0,control
rows=[{"case":"control_all_submitted_and_independent_wrong_id",**control}]
pair='d.get("attempt_id") == aid and d.get("role") == role'
variants=[
("H01_skip_history_validation",lambda *a,**kw:None),
("H02_reject_zero_call_task",weakened("if not completed:\n        return None","if not completed:\n        return 'mutation blocks zero-call task'")),
("H03_allow_missing_source",weakened("if not matching:","if False:")),
("H04_ignore_attempt_id",weakened(pair,'d.get("role") == role')),
("H05_ignore_actor_role",weakened(pair,'d.get("attempt_id") == aid')),
("H06_allow_contradiction",weakened('any(d.get("is_mock") is not False for d in matching)','all(d.get("is_mock") is not False for d in matching)')),
("H07_allow_missing_flag",weakened('d.get("is_mock") is not False','d.get("is_mock") is True')),
("H08_reject_fully_covered_history",weakened("\n    return None\n","\n    return 'mutation blocks complete coverage'\n"))
]
for name,fn in variants:
    with patch.object(cli,"_production_taint_blocker",fn):r=run()
    verdict="detected_by_assertion" if r["failures"]>0 and r["errors"]==0 else "errors_not_counted" if r["errors"] else "survived"
    rows.append({"case":name,"verdict":verdict,**r})
report={"client_date":"2026-10-14","rows":rows,"excluded_tests":[],"api_calls":0,"scope":"40 submitted tests plus one independent wrong-attempt-ID regression; no disk product edits","limits":["Mock/false-source transports are explicit offline test fixtures, not real model evidence"]}
(OUT/"mutation-results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps([{k:v for k,v in row.items() if k!="output"} for row in rows],ensure_ascii=False))
