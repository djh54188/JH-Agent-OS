"""JH-016 batch 2: isolated Windows publish and sharing controls; no API calls."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,sys,uuid
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from src import file_tools as ft
from src.acceptance import file_write_acceptance_evaluator
from src.mock_components import ScriptedWorkerAdapter,ScriptedReviewerAdapter
from src.orchestrator import Orchestrator
from src.storage import TaskStorage
from test_phase4_file_tools import make_file_task,stage_request
OLD=b'{"old":"ORIGINAL"}'
EXPECTED=json.dumps([{'region':'a','sales':1}],ensure_ascii=False)
CANDIDATE=EXPECTED.encode()

def sharing_control(base):
 base.mkdir(parents=True);target=base/'target.txt';temp=base/'candidate.txt'
 target.write_bytes(OLD);temp.write_bytes(CANDIDATE)
 record={'case':'guarded_replace_control'}
 with ft._TargetGuard(target,hashlib.sha256(OLD).hexdigest()):
  try:
   fd=os.open(target,os.O_RDWR|getattr(os,'O_BINARY',0));os.close(fd);record['new_write_open_allowed']=True
  except OSError as exc:record['new_write_open_allowed']=False;record['write_open_winerror']=getattr(exc,'winerror',None)
  try:
   os.replace(temp,target);record['replace_with_guard']='succeeded'
  except OSError as exc:
   record['replace_with_guard']=type(exc).__name__;record['replace_winerror']=getattr(exc,'winerror',None)
  record['target_during_guard']=target.read_bytes().decode()
 if temp.exists():
  os.replace(temp,target);record['same_replace_after_guard_closed']='succeeded'
 record['final_target']=target.read_text(encoding='utf-8')
 return record

def preopened_writer(base):
 base.mkdir(parents=True);target=base/'target.txt';target.write_bytes(OLD)
 fd=os.open(target,os.O_RDWR|getattr(os,'O_BINARY',0))
 record={'case':'preopened_ordinary_write_handle'}
 try:
  try:
   with ft._TargetGuard(target,hashlib.sha256(OLD).hexdigest()):record['guard_acquired']=True
  except ft.PathBoundaryError as exc:record['guard_acquired']=False;record['error']=str(exc).replace(str(base),'<fixture>')
  os.lseek(fd,0,os.SEEK_SET);record['existing_writer_still_usable']=os.write(fd,b'X')==1
 finally:os.close(fd)
 return record

def closed_loop(base,kind):
 ws=base/'ws';outside=base/'sibling-fixture'
 (ws/'out').mkdir(parents=True);outside.mkdir()
 target=ws/'out/report.json'
 if kind=='target_recreated':target.write_bytes(OLD)
 tid='task_b2_'+kind
 executor=ft.FileToolExecutor(ws);storage=TaskStorage(':memory:')
 orch=Orchestrator(storage=storage,workspace_root=ws,tool_executor=executor,
  worker_adapter=ScriptedWorkerAdapter(tool_requests_by_turn=[[stage_request(tid,'out/report.json',EXPECTED)]]),
  reviewer_adapter=ScriptedReviewerAdapter(mode='pass'),
  acceptance_evaluator=file_write_acceptance_evaluator(EXPECTED,'out/report.json'),enforce_target_files_scope=True)
 record={'case':kind,'interleaving_count':0}
 real_replace=os.replace;real_rename=os.rename
 def replace_boundary(src,dst):
  if Path(dst)==target and record['interleaving_count']==0:
   record['interleaving_count']+=1
   child_code="from pathlib import Path;import sys;p=Path(sys.argv[1]);p.rename(p.with_name('previous-object.json'));p.write_text('CONCURRENT_NEW',encoding='utf-8')"
   child=subprocess.run([sys.executable,'-X','utf8','-B','-c',child_code,str(target)],capture_output=True,text=True,encoding='utf-8',timeout=10)
   record['concurrent_process_returncode']=child.returncode
   record['concurrent_stderr']=child.stderr.strip().replace(str(base),'<fixture>')
   record['target_before_publish']=target.read_text(encoding='utf-8') if target.exists() else None
  return real_replace(src,dst)
 def rename_boundary(src,dst):
  if Path(dst)==target and record['interleaving_count']==0:
   record['interleaving_count']+=1
   real_rename(ws/'out',ws/'out-original')
   import _winapi
   _winapi.CreateJunction(str(outside),str(ws/'out'))
  return real_rename(src,dst)
 try:
  orch.submit_task(make_file_task(tid,ws,expected_content=EXPECTED))
  seam=patch.object(ft.os,'replace',replace_boundary) if kind=='target_recreated' else patch.object(ft.os,'rename',rename_boundary)
  with seam:final=orch.run_task(tid)
  record['status']=final['status'];record['error_info']=(final.get('error_info') or '').replace(str(base),'<fixture>')
  record['tool_statuses']=[{'tool':t['tool_name'],'status':t['status']} for t in storage.list_tool_executions(tid)]
  if kind=='target_recreated':
   record['target_after']=target.read_text(encoding='utf-8') if target.exists() else None
   previous=target.with_name('previous-object.json');record['previous_object_preserved']=previous.exists() and previous.read_bytes()==OLD
  else:
   sibling_target=outside/'report.json';record['sibling_file_created']=sibling_target.exists()
   record['sibling_content']=sibling_target.read_text(encoding='utf-8') if sibling_target.exists() else None
   record['original_workspace_target_exists']=(ws/'out-original/report.json').exists()
  return record
 finally:
  storage.close()
  link=ws/'out'
  if kind!='target_recreated' and os.path.lexists(link) and ft._is_reparse_point(link):os.rmdir(link)

def main():
 temp_base=(ROOT/'.test-tmp').resolve();temp_base.mkdir(exist_ok=True)
 temp=temp_base/('jh016-batch2-'+uuid.uuid4().hex);temp.mkdir()
 runs=[]
 try:
  for number in range(2):
   records=[]
   for name,fn in [('sharing',sharing_control),('preopened',preopened_writer),('target_recreated',lambda p:closed_loop(p,'target_recreated')),('parent_at_publish',lambda p:closed_loop(p,'parent_at_publish'))]:
    try:records.append(fn(temp/str(number)/name))
    except Exception as exc:records.append({'case':name,'harness_error':type(exc).__name__+': '+str(exc).replace(str(temp),'<fixture>')})
   runs.append(records)
 finally:
  resolved=temp.resolve()
  if resolved.parent!=temp_base or not resolved.name.startswith('jh016-batch2-'):raise RuntimeError('cleanup path outside expected fixture root')
  shutil.rmtree(resolved)
 report={'api_calls':0,'shared_source_modified':False,'fixture_cleaned':not temp.exists(),'runs':runs,'repeat_observations_equal':runs[0]==runs[1]}
 Path(__file__).with_name('publish-controls.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(report,ensure_ascii=False))
 return 1 if any('harness_error' in r for group in runs for r in group) else 0
if __name__=='__main__':raise SystemExit(main())
