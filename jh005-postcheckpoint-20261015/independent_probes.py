
from pathlib import Path
import json,subprocess,sys,hashlib,shutil,copy,uuid,difflib
WT=Path(r"D:\JH-Agent OS\data\worktrees\n1-artifact-entry")
OUT=Path(r"D:\JH-Agent OS\reports\jh005-postcheckpoint-20261015")
CASE=WT/"data/review-fixtures"/("jh005-"+uuid.uuid4().hex[:8]);CASE.mkdir(parents=True)
rows=[]
def capture(label,args,cwd):
 p=subprocess.run(args,cwd=cwd,text=True,capture_output=True,encoding="utf-8",timeout=30)
 return {"case":label,"exit_code":p.returncode,"stdout":p.stdout,"stderr":p.stderr}
for target in ("target/widget.py","expected/widget.py"):
 row=capture("behavior:"+target,[sys.executable,"-X","utf8","-B","tests/fixtures/n2/code_sortfix/check_widget.py","--target","tests/fixtures/n2/code_sortfix/"+target],WT);rows.append(row)
base=(WT/"tests/fixtures/n2/code_sortfix/target/widget.py").read_bytes()
ref=(WT/"tests/fixtures/n2/code_sortfix/expected/widget.py").read_bytes()
one_fix=base.replace(b", reverse=True",b"")
rows.append({"case":"reference_byte_contract","expected_single_edit_equals_reference":one_fix==ref,
 "baseline_sha256":hashlib.sha256(base).hexdigest(),"reference_sha256":hashlib.sha256(ref).hexdigest(),
 "diff":"".join(difflib.unified_diff(base.decode().splitlines(True),ref.decode().splitlines(True),fromfile="baseline",tofile="reference"))})
sys.path.insert(0,str(WT))
from src.cli import resolve_trusted_evaluator
from jsonschema import Draft7Validator,FormatChecker
schema=json.loads((WT/"docs/schemas/task.v1.json").read_text(encoding="utf-8"))
for fixture in ("code_sortfix","data_rollup"):
 task=json.loads((WT/f"tests/fixtures/n2/{fixture}/task.json").read_text(encoding="utf-8"))
 task["context"]["workspace_root"]=str(CASE/"schema-workspace")
 errors=[{"path":"/".join(map(str,e.absolute_path)),"message":e.message} for e in Draft7Validator(schema,format_checker=FormatChecker()).iter_errors(task)]
 row={"case":fixture+"_task_schema","errors":errors,"after_workspace_substitution":True}
 # Workspace placeholders may be filled by the explicit task materializer; schema errors below do not concern paths.
 rows.append(row)
 if fixture=="data_rollup":
  try:resolve_trusted_evaluator(task);result={"accepted":True}
  except Exception as exc:result={"accepted":False,"error":type(exc).__name__+": "+str(exc)}
  rows.append({"case":"data_template_resolver_without_materialization","result":result})

def make_copy(name):
 root=CASE/name
 (root/"scripts").mkdir(parents=True)
 shutil.copy2(WT/"scripts/verify_jh005_fixtures.py",root/"scripts/verify_jh005_fixtures.py")
 shutil.copytree(WT/"tests/fixtures/n2",root/"tests/fixtures/n2")
 return root
def put(root,rel,value):(root/rel).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
def load(root,rel):return json.loads((root/rel).read_text(encoding="utf-8"))
def seal(root):
 for fixture in ("code_sortfix","data_rollup"):
  folder=root/"tests/fixtures/n2"/fixture;mf=folder/"manifest.json";m=json.loads(mf.read_text())
  m["sealed"]=True
  for e in m["files"]:
   if e["path"]!="manifest.json":e["sha256"]=hashlib.sha256((folder/e["path"]).read_bytes()).hexdigest()
  mf.write_text(json.dumps(m,indent=2),encoding="utf-8")
def mutate(root,kind):
 taskpath="tests/fixtures/n2/data_rollup/task.json"
 if kind=="data_input_mismatch":
  t=load(root,taskpath);t["metadata"]["input_data"]["records"]=[{"region":"different","sales":999}]
  put(root,taskpath,t)
 elif kind=="manifest_empty":
  mpath="tests/fixtures/n2/data_rollup/manifest.json";m=load(root,mpath);m["files"]=[];m["sealed"]=True;put(root,mpath,m)
 elif kind=="manifest_omits_expected":
  mpath="tests/fixtures/n2/data_rollup/manifest.json";m=load(root,mpath);m["files"]=[e for e in m["files"] if e["path"]!="expected.json"];put(root,mpath,m)
 elif kind=="absolute_target":
  t=load(root,taskpath);t["context"]["target_files"]=[r"C:\fixture-outside\widget.py"];put(root,taskpath,t)
 elif kind=="scenarios_empty":
  spath="tests/fixtures/n2/data_rollup/scenarios.json";s=load(root,spath);s["scenarios"]=[];put(root,spath,s)
 elif kind=="checks_empty":
  p="tests/fixtures/n2/data_rollup/checks.json";v=load(root,p);v["checks"]=[];put(root,p,v)
 elif kind=="boolean_sales":
  for p in ("input.json","expected.json"):
   p="tests/fixtures/n2/data_rollup/"+p;v=load(root,p);v=[dict(r,sales=True) if r["region"]=="west" else r for r in v];put(root,p,v)
 elif kind=="manifest_string_seal":
  p="tests/fixtures/n2/data_rollup/manifest.json";v=load(root,p);v["sealed"]="false";put(root,p,v)
for kind in ("sealed_control","data_input_mismatch","manifest_empty","manifest_omits_expected","absolute_target","scenarios_empty","checks_empty","boolean_sales","manifest_string_seal"):
 root=make_copy(kind)
 seal(root)
 if kind!="sealed_control":mutate(root,kind)
 if kind not in ("manifest_empty","manifest_omits_expected","manifest_string_seal"):seal(root)
 row=capture(kind,[sys.executable,"-X","utf8","-B","scripts/verify_jh005_fixtures.py"],root)
 # Do not invoke any task or model. These results concern only the submitted checker.
 rows.append(row)
(OUT/"independent-probes-confirmation.json").write_text(json.dumps({"scope":"Offline fixture/spec verification only; not production authorization evidence","original_fixture_manifests_modified":False,"real_model_calls":0,"scratch_root":str(CASE),"results":rows},ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"repeat_results":len(rows),"checker_inconsistent_variants_accepted":sum(1 for r in rows if r.get("case") in {"data_input_mismatch","manifest_empty","manifest_omits_expected","absolute_target","scenarios_empty","checks_empty","boolean_sales","manifest_string_seal"} and r.get("exit_code")==0)},ensure_ascii=False))

# A deterministic host-delivery counterexample with synthetic data only.
folder=CASE/"host-byte-binding";(folder/"target").mkdir(parents=True)
baseline=WT/"tests/fixtures/n2/code_sortfix/target/widget.py"
original=baseline.read_bytes();approved=original.replace(b", reverse=True",b"")
applied=approved+b"\nUNREVIEWED_CHANGE = True\n"
target=folder/"target/widget.py";target.write_bytes(applied)
p=capture("extra_unreviewed_bytes_same_authorized_file",[sys.executable,"-X","utf8","-B",str(WT/"tests/fixtures/n2/code_sortfix/check_widget.py"),"--target",str(target)],WT)
p.update({"synthetic_only":True,"baseline_sha256":hashlib.sha256(original).hexdigest(),"approved_target_sha256":hashlib.sha256(approved).hexdigest(),"actual_target_sha256":hashlib.sha256(applied).hexdigest(),"same_authorized_path":True,"actual_bytes_equal_reviewed_patch_result":applied==approved,"checks_pass_despite_unreviewed_bytes":p["exit_code"]==0})
(OUT/"host-byte-binding-counterexample.json").write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(p,ensure_ascii=False,indent=2))

