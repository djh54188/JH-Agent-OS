"""Independent extraction and explicit review predicates; no test/CLI execution."""
import ast
import hashlib
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent
E = ROOT / "remote/reports/hark-gemini-admission-20261022"
out = {}
manifest = [json.loads(l) for l in (ROOT / "download-manifest.jsonl").read_bytes().splitlines()]
checks = []
for row in manifest:
    b = (ROOT / row.get("storage", "remote") / row["path"]).read_bytes()
    checks.append(hashlib.sha1(b"blob " + str(len(b)).encode() + b"\0" + b).hexdigest() == row["blob"]
                  and hashlib.sha256(b).hexdigest() == row["sha256"])
out["downloaded_files"] = len(checks)
out["git_blobs_and_disk_digests_match"] = all(checks)
assert all(checks)

failure_sets = []
out["suite_logs"] = {}
for name in ("baseline-suite", "postcommit-suite"):
    text = (E / f"logs/{name}.txt").read_text(encoding="utf-8")
    fs = sorted(re.findall(r"^(?:FAIL|ERROR): .+$", text, re.M))
    failure_sets.append(fs)
    out["suite_logs"][name] = {
        "outer_run": re.findall(r"^Ran (\d+) tests?", text, re.M)[-1],
        "outer_result": re.findall(r"^(?:FAILED.*|OK.*)$", text, re.M)[-1],
        "failure_lines": len(fs), "unique_failure_lines": len(set(fs)),
    }
out["failure_lists_equal"] = failure_sets[0] == failure_sets[1]
out["new_tests_ast"] = sum(isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
                           for n in ast.walk(ast.parse((ROOT / "remote/tests/test_hark12_gemini_admission.py").read_text())))
out["targeted_log_summaries"] = {}
for name in ["postcommit-hark11-jh018"] + [f"postcommit-hark12-run{i}" for i in (1, 2, 3)]:
    text = (E / f"logs/{name}.txt").read_text()
    out["targeted_log_summaries"][name] = re.findall(r"^(?:Ran \d+ tests?.*|OK.*|FAILED.*)$", text, re.M)
weak = (E / "logs/postcommit-weakening-detail.txt").read_text().splitlines()
out["reported_weakenings"] = [{k: v for k, v in [entry.split("=", 1) for entry in l.split("\t")[1:]]}
                                | {"guard": l.split("\t")[0]} for l in weak]
out["host_checks"] = json.loads((E / "host-probe-postcommit/summary.json").read_bytes())["checks"]
requests = [json.loads(l) for l in (E / "host-probe-postcommit/synthetic-requests.jsonl").read_bytes().splitlines()]
out["synthetic_requests"] = len(requests)
out["host_declared_tools"] = sorted({fn["name"] for r in requests for t in r.get("request", {}).get("tools", [])
                                     for fn in t.get("functionDeclarations", [])})
out["reproduction_logs"] = {}
for name in ("repro-baseline", "repro-postcommit"):
    records = [json.loads(l) for l in (E / f"logs/{name}.txt").read_bytes().splitlines()]
    out["reproduction_logs"][name] = {"case_rows": len([r for r in records if "case" in r]), "summary": records[-1]}

def judge(c):
    name = c["case"]
    if name == "windows_fixture_substitution":
        return c["all_lines_parse"] is False  # frozen old literal algorithm, not candidate fake_cli
    if name == "positive": return c["state"] == "declared_complete" and c["workspace_unchanged"] is True
    if name in ("two_instances_same_source_same_key", "same_instance_two_threads_same_key"):
        return c["same_run"] is True and c["launches"] == 1
    if name == "reopen_after_dispatch_control": return c["same_run"] is True and c["launches"] == 1 and c["state"] == "unknown"
    if name in ("registry_bad_json_control", "registry_bad_json_after_initialization"):
        return c["blocked"] == "RegistryUnavailable" and c["launches"] == 0
    if name.startswith("registry_"): return c["blocked"] == "RegistryUnavailable" and c["new_launches"] == 0
    if name.startswith("enumerate_") and name.endswith("_both"):
        return c.get("exception") == "WorkspaceUnobservable" and c["launches"] == 0
    if name.startswith("enumerate_"):
        return c["state"] == "failed" and c["workspace_unchanged"] is None
    if name.startswith("tool_result_status_"): return c["state"] == "failed" and c["denied"] == []
    if name.startswith("stream_"): return "exception" not in c and c["state"] == "failed"
    if name.startswith("chat_first_"):
        return "exception" not in c and any("not a JSON object" in x for x in c["unverified"])
    if name == "existing_relative_workspace": return c["exception"] == "HostRestrictionUnavailable" and c["launches"] == 0
    raise AssertionError(name)

out["frozen_probes"] = {}
normalized = []
for name in ("frozen-01", "frozen-02"):
    rows = json.loads((ROOT / name / "results.json").read_bytes())["cases"]
    bad = [c["case"] for c in rows if not judge(c)]
    assert not bad, bad
    out["frozen_probes"][name] = {"cases": len(rows), "candidate_cases": len(rows) - 1,
                                  "old_literal_substitution_control": 1, "unexpected": bad}
    keys = ("case", "launches", "new_launches", "blocked", "same_run", "state", "declared_status", "workspace_unchanged", "exception", "denied", "all_lines_parse")
    normalized.append([{k: c.get(k) for k in keys} for c in rows])
out["frozen_probe_outcomes_repeat"] = normalized[0] == normalized[1]
out["windows_selected_original_tests"] = json.loads((ROOT / "selected-windows-original-tests.json").read_bytes())
out["windows_process_lock"] = json.loads((ROOT / "process-lock-01/results.json").read_bytes())
assert all(c["passes"] for c in out["windows_process_lock"]["cases"])
out["reopen_boundary_initial"] = json.loads((ROOT / "reopen-boundary-01/results.json").read_bytes())
out["reopen_boundary_variants"] = json.loads((ROOT / "reopen-boundary-02/results.json").read_bytes())
regressions = [c["case"] for c in out["reopen_boundary_variants"]["cases"]
               if c["case"] in ("truncate-empty", "delete", "whitespace", "lost-last-run") and c["new_launches"] != 0]
out["remaining_R1_source_loss_variants"] = regressions
assert len(regressions) == 4
data = json.dumps(out, ensure_ascii=False, indent=2).encode()
(ROOT / "review-summary.json").write_bytes(data)
print(json.dumps({k: v for k, v in out.items() if k not in ("reopen_boundary_initial", "reopen_boundary_variants", "windows_selected_original_tests")}, ensure_ascii=False, indent=2))
