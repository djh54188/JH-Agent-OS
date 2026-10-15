
from pathlib import Path
import subprocess,sys,json,shutil,uuid
WT=Path(r"D:\JH-Agent OS\data\worktrees\n1-artifact-entry");OUT=WT/"reports/jh005-rereview-20261015"
CASE=WT/"data/review-fixtures"/("jh005-guard-regression-"+uuid.uuid4().hex[:8]);CASE.mkdir(parents=True)
rows=[]
for name,anchor in [("disable_post_apply_binding",'if item.get("name") == "post-apply-target-check":'),("disable_fixture_authorization","if targets != REQUIRED_TARGETS[name]:")]:
 root=CASE/name;(root/"scripts").mkdir(parents=True)
 for f in ("verify_jh005_fixtures.py","verify_jh005_mutations.py"):shutil.copy2(WT/"scripts"/f,root/"scripts"/f)
 shutil.copytree(WT/"src",root/"src",ignore=shutil.ignore_patterns("__pycache__"))
 shutil.copytree(WT/"docs/schemas",root/"docs/schemas")
 shutil.copytree(WT/"tests/fixtures/n2",root/"tests/fixtures/n2")
 p=root/"scripts/verify_jh005_fixtures.py";s=p.read_text(encoding="utf-8");assert s.count(anchor)==1;s=s.replace(anchor,"if False:");p.write_text(s,encoding="utf-8")
 run=subprocess.run([sys.executable,"-X","utf8","-B","scripts/verify_jh005_mutations.py"],cwd=root,capture_output=True,text=True,encoding="utf-8",timeout=120)
 body=json.loads(run.stdout)
 assert run.returncode==1 and body["verdict"]=="fail" and body["rows"][0]["verdict"]=="pass"
 failed=[r["case"] for r in body["rows"][1:] if r["verdict"]!="detected_by_assertion"]
 expected={"post_apply_wrong_target","post_apply_wrong_parameter","post_apply_wrong_script","post_apply_bool_exit"} if name=="disable_post_apply_binding" else {"code_wrong_authorization","data_wrong_authorization"}
 assert set(failed)==expected,(name,failed)
 assert all("harness" not in r.get("reported_checks",[]) for r in body["rows"])
 rows.append({"source_mutant":name,"mutation_suite_exit":run.returncode,"control_still_passed":True,"failed_guard_cases":failed,"harness_errors":0})
(OUT/"guard-source-mutations.json").write_text(json.dumps({"scope":"Two source regressions confined to copied offline fixture checker","real_model_calls":0,"rows":rows},ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"guard_source_regressions_detected":len(rows),"harness_errors":0}))

