"""Read fixed source/evidence and summarize; does not run the full suite/CLI."""
import ast
import hashlib
import importlib.util
import io
import json
import pathlib
import platform
import re
import sys
import tempfile
import unittest
import uuid
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent
REMOTE = ROOT / "remote"
E = REMOTE / "reports/hark-gemini-bridge-20261021"
out = {"platform": platform.platform(), "python": sys.version}
manifest = [json.loads(l) for l in (ROOT / "download-manifest.jsonl").read_bytes().splitlines()]
out["downloaded_blobs"] = len(manifest)
out["all_blobs_match"] = all(
    hashlib.sha1(b"blob " + str(len(b := (REMOTE / r["path"]).read_bytes())).encode() + b"\0" + b).hexdigest() == r["blob"]
    and hashlib.sha256(b).hexdigest() == r["sha256"] for r in manifest)
logs = {}
for name in ("baseline-suite", "precommit-suite", "postcommit-suite"):
    text = (E / f"logs/{name}.txt").read_text(encoding="utf-8")
    logs[name] = {"summary_lines": re.findall(r"^(?:Ran \d+ tests?.*|FAILED.*|OK.*)$", text, re.M),
                  "failures": sorted(re.findall(r"^(?:ERROR|FAIL): .+$", text, re.M))}
out["suite_logs"] = {k: {"summary_lines": v["summary_lines"], "failure_lines": len(v["failures"]),
                        "unique_failure_lines": len(set(v["failures"]))} for k, v in logs.items()}
out["failure_sets_equal"] = len({tuple(v["failures"]) for v in logs.values()}) == 1
newtest = ast.parse((REMOTE / "tests/test_hark11_gemini_session.py").read_text(encoding="utf-8"))
out["new_test_methods_ast"] = sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_") for n in ast.walk(newtest))
out["new_test_log"] = re.findall(r"^(?:Ran \d+ tests?.*|FAILED.*|OK.*)$", (E / "logs/postcommit-hark11.txt").read_text(), re.M)
out["host_probe_checks"] = {d: json.loads((E / d / "summary.json").read_bytes())["checks"] for d in ("host-probe", "host-probe-postcommit")}
pre = (E / "host-probe/bridge/runs/gemini-run-e4d091ccdd29/stdout.jsonl").read_bytes()
events = [json.loads(l) for l in pre.splitlines()]
sid = events[0]["session_id"]
template = pre.decode().replace(sid, "{SESSION_ID}").replace("/workspace/work/h11-target", "{WORKSPACE}").encode()
out["native_template_exact_after_declared_replacements"] = template == (REMOTE / "tests/fixtures/gemini_cli/host_probe_stream.template.jsonl").read_bytes()
for label in ("D", "E"):
    requests = [json.loads(l) for l in (E / f"host/probe-{label}.synthetic-requests.jsonl").read_bytes().splitlines()]
    declared = set(); responses = []
    for request in requests:
        body = request.get("request", {})
        for tool in body.get("tools", []):
            declared.update(f.get("name") for f in tool.get("functionDeclarations", []))
        for content in body.get("contents", []):
            for part in content.get("parts", []):
                response = part.get("functionResponse")
                if response:
                    item = {"name": response.get("name"), "response": response.get("response")}
                    if item not in responses: responses.append(item)
    out["host_probe_" + label] = {"request_kinds": [r.get("kind") for r in requests],
                                  "declared_tools": sorted(declared), "function_responses": responses}
r4 = json.loads((ROOT / "round-04/results.json").read_bytes())
r5 = json.loads((ROOT / "round-05/results.json").read_bytes())
keys = ("case", "state", "declared_status", "workspace_unchanged", "denied", "exception", "launches", "new_launches", "blocked", "same_run", "all_lines_parse", "submitted")
out["probe_case_count_each_round"] = [len(r4["cases"]), len(r5["cases"])]
out["probe_outcomes_repeat"] = [{k: c.get(k) for k in keys} for c in r4["cases"]] == [{k: c.get(k) for k in keys} for c in r5["cases"]]

# Only two selected original tests. Their fake subprocess never starts Gemini.
temp = ROOT / "targeted-temp"
temp.mkdir(exist_ok=True)
tempfile.tempdir = str(temp)
spec = importlib.util.spec_from_file_location("hark11_original", ROOT / "candidate/tests/test_hark11_gemini_session.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
suite = unittest.TestSuite([
    module.TestValidDelivery("test_valid_delivery_is_declared_not_succeeded"),
    module.TestReadOnly("test_workspace_host_config_refused"),
])
contract_spec = importlib.util.spec_from_file_location("hark11_contract_original", ROOT / "candidate/tests/test_jh018_agent_session_contract.py")
contract = importlib.util.module_from_spec(contract_spec)
sys.modules[contract_spec.name] = contract
contract_spec.loader.exec_module(contract)
suite.addTest(contract.TestCoreHoldsNoConcreteAdapter("test_contract_consumers_are_real_adapters"))
suite.addTest(contract.TestCoreHoldsNoConcreteAdapter("test_the_real_adapter_is_flagged"))
stream = io.StringIO()
def ordinary_temp(suffix=None, prefix=None, dir=None):
    p = pathlib.Path(dir or temp) / ((prefix or "tmp") + uuid.uuid4().hex + (suffix or ""))
    p.mkdir()
    return str(p)
# Python 3.14 mkdtemp(mode=0700) creates inaccessible directories in this
# sandbox. Only substitute ordinary reviewer-owned directory creation.
with mock.patch.object(tempfile, "mkdtemp", ordinary_temp):
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
(ROOT / "targeted-original-tests.txt").write_bytes(stream.getvalue().encode())
out["selected_original_tests"] = {"run": result.testsRun, "failures": len(result.failures),
                                    "errors": len(result.errors), "skipped": len(result.skipped)}
out["selected_test_workaround"] = "ordinary mkdir in review-owned temp instead of inaccessible Python 3.14 mkdtemp(mode=0700); candidate bytes unchanged"
data = json.dumps(out, ensure_ascii=False, indent=2).encode()
(ROOT / "audit-summary.json").write_bytes(data)
print(data.decode())
