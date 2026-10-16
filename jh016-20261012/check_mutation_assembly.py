"""Independent mutation-harness checks; in-memory modules only."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
spec=importlib.util.spec_from_file_location('jh016_guards',ROOT/'scripts/verify_phase4_guards.py')
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
from src import file_tools as live_ft, orchestrator as live_orch, acceptance as live_acc
records=[]
for name,rel,original,replacement,test_name in g.MUTATIONS:
 source=(ROOT/rel).read_text(encoding='utf-8')
 if source.count(original)!=1:raise RuntimeError('mutation target missing: '+name)
 mod_name='src.'+Path(rel).stem
 mutant=types.ModuleType(mod_name);mutant.__file__=str(ROOT/rel)
 exec(compile(source.replace(original,replacement),str(ROOT/rel),'exec'),mutant.__dict__)
 patches=[]
 if mod_name=='src.file_tools':
  for consumer in [live_ft,live_orch,g.phase4_tests]:
   for symbol in ['FileToolExecutor','PathBoundaryError']:
    patches.append(patch.object(consumer,symbol,getattr(mutant,symbol)))
 elif mod_name=='src.orchestrator':
  patches=[patch.object(live_orch,'Orchestrator',mutant.Orchestrator),patch.object(g.phase4_tests,'Orchestrator',mutant.Orchestrator)]
 elif mod_name=='src.acceptance':
  patches=[patch.object(live_acc,'file_write_acceptance_evaluator',mutant.file_write_acceptance_evaluator),patch.object(g.phase4_tests,'file_write_acceptance_evaluator',mutant.file_write_acceptance_evaluator)]
 case=next(cls(test_name) for cls in [g.phase4_tests.TestPhase4FileLoop,g.phase4_tests.TestFileToolExecutor] if hasattr(cls,test_name))
 with contextlib.ExitStack() as stack:
  for item in patches:stack.enter_context(item)
  identities={'executor_class_consistent':g.phase4_tests.FileToolExecutor is live_orch.FileToolExecutor,'exception_class_consistent':g.phase4_tests.PathBoundaryError is live_orch.PathBoundaryError}
  result=unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite([case]))
 records.append({'guard':name,**identities,'tests_run':result.testsRun,'failures':[{'test':str(t),'last_lines':tb.strip().splitlines()[-5:]} for t,tb in result.failures],'errors':[{'test':str(t),'last_lines':tb.strip().splitlines()[-5:]} for t,tb in result.errors],'survived':result.wasSuccessful()})
report={'shared_source_modified':False,'api_calls':0,'records':records,'survivors':sum(r['survived'] for r in records),'assertion_failures':sum(bool(r['failures']) for r in records),'harness_errors':sum(bool(r['errors']) for r in records),'note':'A failing wording assertion is not proof that the safety invariant failed; inspect each last_lines entry.'}
Path(__file__).with_name('corrected-mutation-assembly.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
