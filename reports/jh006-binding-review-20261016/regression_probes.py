"""Independent synthetic probes; no real provider or production execution."""
import json
import sys
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from test_jh006_capture_binding import _BindCase, THREAD, ITEM, CALL
import src.capture_binding as cb
from test_jh006_code_delivery import _baseline_bytes

results = []
def probe(name, operation):
    case = _BindCase(); case.setUp()
    try:
        orch, tid, delivery = case._prepared_production()
        case._fixture_apply(); store = case._live_store()
        archive, _, _, _ = case._archive_for(tid, delivery, live_store=store)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            try:
                observed = operation(case, orch, tid, delivery, store, archive)
            except cb.BindingRefused as exc:
                observed = {"refused":str(exc)}
            results.append({"name":name,"synthetic":True,"observed":observed})
    finally:
        case.doCleanups()

def drift(c,o,t,d,s,a):
    c._register(o,t,d,a)
    (c.ws / "target/widget.py").write_bytes(_baseline_bytes())
    return cb.verify_binding_current(o.storage,t,d["delivery_id"])["current"]
def revoked(c,o,t,d,s,a):
    c._register(o,t,d,a)
    attempt=o.storage.list_model_attempts(t)[-1]; review=attempt["response"];review["verdict"]="revise"
    o.storage._conn.execute("UPDATE model_attempts SET response_json=? WHERE attempt_id=?",(json.dumps(review),attempt["attempt_id"]))
    return cb.verify_binding_current(o.storage,t,d["delivery_id"])["current"]
def echo(c,o,t,d,s,a):
    argv=["pwsh.exe","-Command","Write-Output PASS"]
    script="text(await tools.exec_command({cmd:'Write-Output PASS'}));"
    a,_,_,_=c._archive_for(t,d,live_store=s,argv=argv,call_script=script,call="test-bind-other-call",item="test-bind-other-exec")
    row=c._register(o,t,d,a,expected_argv=argv,expected_call_input=script,call_id="test-bind-other-call",item_id="test-bind-other-exec")
    return {"registered":bool(row),"current":cb.verify_binding_current(o.storage,t,d["delivery_id"])["current"]}
for name, f in [("target-changed-after-registration",drift),("review-revoked-after-registration",revoked),("caller-reasserts-echo-as-check",echo)]:
    probe(name,f)
print(json.dumps(results,indent=2))
assert results[0]["observed"] is False
assert results[1]["observed"] is False
assert "invocation-unverified" in results[2]["observed"]["refused"]
