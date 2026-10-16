"""Reproduce the original false bindings from the checkpoint, without checkout.

Every record is synthetic. Loads original source/test fixtures in memory and
repairs ONLY the known tuple fixture error to reach the original implementation.
"""
import json
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
REF = "afb3ac1"

def load(name, path):
    module = types.ModuleType(name); module.__file__ = str(ROOT / path)
    code = subprocess.check_output(["git", "show", REF + ":" + path], cwd=ROOT)
    exec(compile(code, module.__file__, "exec"), module.__dict__)
    return module

cb = load("submitted_capture_binding", "src/capture_binding.py")
tests = load("submitted_bind_tests", "tests/test_jh006_capture_binding.py")
tests.cb = cb
for key in ("register_capture_binding", "get_capture_binding", "get_capture_binding_by_call", "verify_binding_current"):
    setattr(tests, key, getattr(cb, key))
from test_jh006_code_delivery import _Case, _baseline_bytes
tests._BindCase._orch = lambda self, **kw: _Case._orch(self, **kw)[0]
results = []
for name in ("target-changed-after-registration", "review-revoked-after-registration", "caller-reasserts-echo-as-check"):
    case=tests._BindCase(); case.setUp()
    try:
        orch, tid, delivery=case._prepared_production(); case._fixture_apply(); store=case._live_store()
        archive,_,_,_=case._archive_for(tid,delivery,live_store=store)
        with patch.object(cb,"DESKTOP_SESSION_ROOT",store):
            if name == "caller-reasserts-echo-as-check":
                argv=["pwsh.exe","-Command","Write-Output PASS"]
                script="text(await tools.exec_command({cmd:'Write-Output PASS'}));"
                archive,_,_,_=case._archive_for(tid,delivery,live_store=store,argv=argv,
                    call_script=script,call="test-bind-other-call",item="test-bind-other-exec")
                row=case._register(orch,tid,delivery,archive,expected_argv=argv,expected_call_input=script,
                    call_id="test-bind-other-call",item_id="test-bind-other-exec")
                observed={"registered":bool(row),"current":cb.verify_binding_current(orch.storage,tid,delivery["delivery_id"])["current"]}
                assert observed == {"registered":True,"current":True}
            else:
                case._register(orch,tid,delivery,archive)
                if name == "target-changed-after-registration":
                    (case.ws/"target/widget.py").write_bytes(_baseline_bytes())
                else:
                    attempt=orch.storage.list_model_attempts(tid)[-1]
                    review=attempt["response"]; review["verdict"]="revise"
                    orch.storage._conn.execute("UPDATE model_attempts SET response_json=? WHERE attempt_id=?",
                                              (json.dumps(review),attempt["attempt_id"]))
                observed=cb.verify_binding_current(orch.storage,tid,delivery["delivery_id"])["current"]
                assert observed is True
            results.append({"name":name,"synthetic":True,"observed":observed})
    finally:
        case.doCleanups()
print(json.dumps(results,indent=2))
