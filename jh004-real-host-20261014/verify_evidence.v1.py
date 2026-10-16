"""Read-only independent evidence verification, not a production acceptance implementation."""
from pathlib import Path
import json,sqlite3,hashlib,sys,subprocess,shutil
OUT=Path(r"D:\JH-Agent OS\reports\jh004-real-host-20261014")
INFO=json.loads((OUT/"fixture-info.json").read_text(encoding="utf-8"))
ROOT=Path(INFO["worktree"]);CASE=Path(INFO["case"]);DB=Path(INFO["db"]);TID=INFO["task_id"]
checks=[]
def check(name,ok,evidence=None):
 checks.append({"check":name,"passed":bool(ok),"evidence":evidence})
def read(path):return json.loads(path.read_text(encoding="utf-8-sig"))
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def database(path):
 c=sqlite3.connect(path.resolve().as_uri()+"?mode=ro&immutable=1",uri=True);c.row_factory=sqlite3.Row
 try:
  return {r[0]:[dict(x) for x in c.execute('SELECT * FROM "'+r[0].replace('"','""')+'"')]
          for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")}
 finally:c.close()
try:
 host=OUT/"host-attempt-1"
 result=read(host/"host-process-result.json")
 events=[json.loads(x) for x in (host/"host-events.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
 commands=[e["item"] for e in events if e.get("type")=="item.completed" and e.get("item",{}).get("type")=="command_execution"]
 usage=[e.get("usage") for e in events if e.get("type")=="turn.completed"]
 check("native_host_finished",result["exit_code"]==0 and not result["timed_out"],result)
 skillread=[c for c in commands if "SKILL.md" in c.get("command","") and "# JH Task Entry" in c.get("aggregated_output","") and c.get("exit_code")==0]
 check("host_discovered_and_read_actual_skill",bool(skillread),[c.get("id") for c in skillread])
 check("host_preflight_correct_root_branch_commit_module",any("c7d7421" in c.get("aggregated_output","") and "n1-artifact-entry" in c.get("aggregated_output","") and "cli.py" in c.get("aggregated_output","") and "True" in c.get("aggregated_output","") for c in commands))
 cli_commands=[c for c in commands if "src.cli" in c.get("command","") and any(t in c.get("command","") for t in ("submit","resume","status","artifact","summary"))]
 (OUT/"host-command-index.json").write_text(json.dumps({"commands":commands,"host_usage":usage},ensure_ascii=False,indent=2),encoding="utf-8")
 check("host_cli_commands_completed",len(cli_commands)>=4 and all(c.get("exit_code")==0 for c in cli_commands),[{"id":c.get("id"),"exit":c.get("exit_code")} for c in cli_commands])
 check("fixture_database_exists",DB.is_file())
 if not DB.is_file():raise RuntimeError("No backend database: host integration stopped before submit")
 raw=database(DB)
 (OUT/"database-records.json").write_text(json.dumps(raw,ensure_ascii=False,indent=2),encoding="utf-8")
 tasks=[r for r in raw["tasks"] if r["task_id"]==TID]
 assert len(tasks)==1
 tr=tasks[0];task=json.loads(tr["task_json"]);control=next(r for r in raw["task_control"] if r["task_id"]==TID)
 artifact=max([r for r in raw["artifact_versions"] if r["task_id"]==TID],key=lambda r:r["version"])
 content=artifact["content"];sha=hashlib.sha256(content.encode("utf-8")).hexdigest()
 check("task_succeeded",tr["status"]=="succeeded",tr["status"])
 check("independently_sorted_literal_output",json.loads(content)==INFO["expected"],json.loads(content))
 check("independent_current_artifact_sha",sha==artifact["sha256"],sha)
 current=[r for r in raw["review_bindings"] if r["task_id"]==TID and r["attempt_id"]==control["active_attempt_id"]]
 check("current_review_attempt_binding",len(current)==1,control["active_attempt_id"])
 b=current[0];review=json.loads(next(r["review_json"] for r in raw["reviews"] if r["review_id"]==b["review_id"]))
 binding=json.loads(b["artifacts_json"]);acceptance=json.loads(b["acceptance_json"])
 reviewed_shas=[x.get("sha256") for x in binding.values() if isinstance(x,dict)]
 check("current_review_covers_current_sha",sha in reviewed_shas,binding)
 check("independent_review_pass_no_severe_issues",review["task_id"]==TID and review["verdict"]=="pass" and not any(x["severity"] in ("p0_blocker","p1_critical","p2_major") for x in review["issues"]),review)
 expected_criteria=read(OUT/"fixture-task.json")["acceptance_criteria"]
 check("all_required_criteria_evidenced",len(acceptance)==2 and all(a.get("criterion")==c and a.get("passed") is True and bool(a.get("evidence")) for a,c in zip(acceptance,expected_criteria)),acceptance)
 attempts=[r for r in raw["model_attempts"] if r["task_id"]==TID]
 ev=[r for r in raw["task_events"] if r["task_id"]==TID] if "task_events" in raw else [r for r in raw["events"] if r["task_id"]==TID]
 sources=[json.loads(e["details_json"]) for e in ev if e["event_type"]=="model_response_source"]
 check("all_completed_attempts_real_source_covered",bool(attempts) and all(a["status"]=="completed" and any(s.get("attempt_id")==a["attempt_id"] and s.get("role")==a["actor_role"] and s.get("is_mock") is False for s in sources) for a in attempts),sources)
 usage_events=[e for e in ev if e.get("tokens_used") is not None]
 token_sum=sum(e["tokens_used"] for e in usage_events)
 check("actual_usage_known_and_consistent",tr["tokens_unknown_count"]==0 and tr["tokens_used"]==token_sum,{"runtime":tr["tokens_used"],"event_sum":token_sum,"usage_events":[{"event":e["event_type"],"tokens":e["tokens_used"]} for e in usage_events]})
 original=read(OUT/"fixture-task.json")
 check("original_budget_and_source_preserved",task["budget"]==original["budget"] and task["metadata"]["_trusted"]=={"source":"production","template_id":"records_transform_v1","execution_mode":"generate"},task["metadata"]["_trusted"])
 check("within_original_budget",tr["tokens_used"]<=16000 and tr["turns_used"]<=6,{"tokens":tr["tokens_used"],"turns":tr["turns_used"]})
 check("no_unresolved_side_effect_usage_approval_or_owner",not tr["has_unconfirmed_side_effect"] and not tr["tokens_unknown_count"] and not control["pending_json"] and not control["owner_id"])
 tool_rows=[r for r in raw["tool_executions"] if r["task_id"]==TID]
 check("no_model_tools_or_target_application",not tool_rows and not any((CASE/"ws").iterdir()))
 before=database(CASE/"before-query.sqlite3");after=database(CASE/"after-query.sqlite3")
 before_c=next(r for r in before["task_control"] if r["task_id"]==TID)
 check("original_deadline_not_reset",control["deadline"]==before_c["deadline"],{"before":before_c["deadline"],"after":control["deadline"]})
 check("host_query_database_byte_identical",digest(CASE/"before-query.sqlite3")==digest(CASE/"after-query.sqlite3"))
 check("host_query_zero_attempts_and_created_state",not before["model_attempts"] and not after["model_attempts"] and before["tasks"][0]["status"]==after["tasks"][0]["status"]=="created")
 outputs={name:read(CASE/name) for name in ("a-submit.json","b-status.json","c-resume.json","d-artifact.json","d-summary.json")}
 summary=outputs["d-summary.json"]["task_result"]
 check("host_delivery_reports_generated_only",summary["status"]=="succeeded" and summary["delivery_scope"]["generated_artifact"] is True and summary["delivery_scope"]["target_applied"] is False and not summary["blockers"])
 for name,obj in outputs.items():(OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
 for name in ("before-query.sqlite3","after-query.sqlite3"):shutil.copy2(CASE/name,OUT/name)
 shutil.copy2(DB,OUT/"state.sqlite3")
 check("source_and_committed_worktree_unchanged",subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()==INFO["commit"] and not subprocess.check_output(["git","status","--short"],cwd=ROOT,text=True).strip())
except Exception as exc:
 check("verification_completed",False,f"{type(exc).__name__}: {exc}")
result={"task_id":TID,"commit":INFO["commit"],"passed":all(c["passed"] for c in checks),"checks":checks,
        "limits":["One synthetic fixed template through Codex CLI; current desktop C-drive chat was not rebound",
                  "Real local-proxy API calls; underlying vendor model identity/capacity follows approved proxy configuration",
                  "No target-file application, no full N2/P1 acceptance"],"host_usage_recorded_separately":True}
(OUT/"independent-verification.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(result,ensure_ascii=False,indent=2))
sys.exit(0 if result["passed"] else 1)
