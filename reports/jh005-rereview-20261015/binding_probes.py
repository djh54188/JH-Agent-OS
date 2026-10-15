
from pathlib import Path
import json,subprocess,sys,hashlib,shutil,uuid
WT=Path(r"D:\JH-Agent OS\data\worktrees\n1-artifact-entry")
OUT=WT/"reports/jh005-rereview-20261015"
CASE=WT/"data/review-fixtures"/("jh005-rereview-"+uuid.uuid4().hex[:8]);CASE.mkdir(parents=True)
def prepare(name):
 root=CASE/name;(root/"scripts").mkdir(parents=True)
 shutil.copy2(WT/"scripts/verify_jh005_fixtures.py",root/"scripts/verify_jh005_fixtures.py")
 shutil.copytree(WT/"tests/fixtures/n2",root/"tests/fixtures/n2")
 shutil.copytree(WT/"src",root/"src",ignore=shutil.ignore_patterns("__pycache__"))
 shutil.copytree(WT/"docs/schemas",root/"docs/schemas")
 return root
def load(root,rel):return json.loads((root/rel).read_text(encoding="utf-8"))
def put(root,rel,value):(root/rel).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
def seal(root):
 for name in ("code_sortfix","data_rollup"):
  folder=root/"tests/fixtures/n2"/name;m=load(root,folder.relative_to(root)/"manifest.json");m["sealed"]=True
  for e in m["files"]:
   if e["path"]!="manifest.json":e["sha256"]=hashlib.sha256((folder/e["path"]).read_bytes()).hexdigest()
  put(root,folder.relative_to(root)/"manifest.json",m)
def run(root):
 p=subprocess.run([sys.executable,"-X","utf8","-B","scripts/verify_jh005_fixtures.py"],cwd=root,capture_output=True,text=True,encoding="utf-8",timeout=60)
 try:b=json.loads(p.stdout)
 except ValueError:b={"harness_error":p.stdout,"stderr":p.stderr}
 return {"exit_code":p.returncode,"body":b}
rows=[]
for case in ("sealed_control","post_apply_wrong_target","post_apply_wrong_parameter","post_apply_wrong_script","post_apply_bool_exit","code_wrong_authorization","data_wrong_authorization"):
 root=prepare(case)
 if case.startswith("post_apply_"):
  rel="tests/fixtures/n2/code_sortfix/checks.json";d=load(root,rel)
  c=next(c for c in d["checks"] if c["name"]=="post-apply-target-check")
  if case=="post_apply_wrong_target":c["command"][-1]="{HOST_WORKSPACE}/other/widget.py"
  elif case=="post_apply_wrong_parameter":
   c["command"][-1]="{OTHER_WORKSPACE}/target/widget.py"
   c["materialization"]={"OTHER_WORKSPACE":"Another workspace"}
  elif case=="post_apply_wrong_script":c["command"][1]="tests/fixtures/n2/code_sortfix/target/widget.py"
  elif case=="post_apply_bool_exit":c["expect_exit"]=False
  put(root,rel,d)
 elif case in ("code_wrong_authorization","data_wrong_authorization"):
  name="code_sortfix" if case=="code_wrong_authorization" else "data_rollup"
  rel=f"tests/fixtures/n2/{name}/task.json";d=load(root,rel);d["context"]["target_files"]=["other.py"];put(root,rel,d)
 seal(root);r=run(root);r["case"]=case;rows.append(r)
data=load(WT,"tests/fixtures/n2/data_rollup/expected.json")
assert data==[{"region":"east","sales":4},{"region":"east","sales":12},{"region":"north","sales":7},{"region":"north","sales":9},{"region":"south","sales":3},{"region":"west","sales":5}]
base=(WT/"tests/fixtures/n2/code_sortfix/target/widget.py").read_bytes()
ref=(WT/"tests/fixtures/n2/code_sortfix/expected/widget.py").read_bytes()
assert base.count(b", reverse=True")==1 and base.replace(b", reverse=True",b"")==ref
doc=(WT/"docs/host-delivery-contract.md").read_text(encoding="utf-8")
assert all(x in doc for x in ("prepare_code_delivery","complete_code_delivery","actual_after","expected_after","控制层必须读取","reviewing","准备就绪","不自动重放"))
(OUT/"binding-probes-before.json").write_text(json.dumps({"scope":"Offline checker probes only; no production/host application","scratch":str(CASE),"rows":rows,"literal_data_expected_verified":True,"reference_byte_contract_verified":True,"host_contract_design_text_reviewed":True},ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(rows,ensure_ascii=False,indent=2))

