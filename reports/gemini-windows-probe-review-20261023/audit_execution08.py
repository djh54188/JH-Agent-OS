"""Read-only audit of execution task 08. No CLI/model/full suite execution."""
import ast
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
WT = BASE.parent / 'gemini-windows-probe-20261023'
PIN = BASE / 'data/hark13-independent-review-20261023/candidate'
RT = Path(r'D:\JH-Agent OS\data\gemini-windows-probe-20261023')
COMMIT = 'fdec9c6876e5def7432fe5e8c1e9ec99f425431d'


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def lines(p): return [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines() if l.strip()]


def audit_case(rel):
    case = RT / rel
    rec = lines(case / 'bridge/registry.jsonl')[-1]
    run = case / 'bridge/runs' / rec['run_id']
    stream = lines(run / 'stdout.jsonl')
    uses = {e['tool_id']:e for e in stream if e['type'] == 'tool_use'}
    calls = []
    for e in stream:
        if e['type'] == 'tool_result':
            use = uses[e['tool_id']]
            calls.append({'name':use['tool_name'],'parameters':use['parameters'],
                          'status':e['status'],'error_type':(e.get('error') or {}).get('type')})
    matches = {}
    for name, m in rec['evidence'].items():
        f = run / name
        matches[name] = f.stat().st_size == m['bytes'] and sha(f) == m['sha256']
    assert all(matches.values())
    requests = case / ('synthetic-requests.jsonl' if rel.startswith('evidence/') else 'requests.jsonl')
    names = set()
    for r in lines(requests):
        for group in (r.get('request') or {}).get('tools',[]):
            for fn in group.get('functionDeclarations',[]): names.add(fn['name'])
    stderr = (run / 'stderr.txt').read_text(encoding='utf-8')
    assert 'Assertion failed: !(handle->flags & UV_HANDLE_CLOSING)' in stderr
    assert rec['state'] == 'failed' and rec['declared_status'] == 'success'
    exit_code = rec['artifacts'][0]['exit_code']
    assert exit_code == 3221226505
    before,after = run / 'workspace-before.json', run / 'workspace-after.json'
    assert before.read_bytes() == after.read_bytes()
    files = [run / n for n in rec['evidence']] + [before,after,case/'bridge/registry.jsonl',requests]
    return {'case':rel,'run_id':rec['run_id'],'workspace':rec['workspace'],
            'synthetic_requests':len(lines(requests)), 'events':len(stream),
            'state':rec['state'],'declared_status':rec['declared_status'],
            'exit_decimal':exit_code,'exit_hex':hex(exit_code),
            'tools_advertised':sorted(names),'tool_results':calls,
            'raw_evidence_matches':matches,'workspace_manifest_sha256':sha(before),
            'source_hashes':{str(p):sha(p) for p in files}}


def guard_target(source):
    tree = ast.parse(source)
    cls = next(c for c in tree.body if isinstance(c,ast.ClassDef) and c.name == 'TestCoreHoldsNoConcreteAdapter')
    fn = next(f for f in cls.body if isinstance(f,ast.FunctionDef) and f.name == 'test_contract_consumers_are_real_adapters')
    globals_ = {'AgentSessionAdapter':importlib.import_module('src.agent_session').AgentSessionAdapter}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'frozen-guard-method','exec'),globals_)
    return globals_[fn.name]


def main():
    changed = subprocess.check_output(['git','--no-optional-locks','diff-tree','--no-commit-id','--name-only','-r',COMMIT],cwd=WT,text=True).splitlines()
    comparisons = []
    for rel in changed:
        p,q = WT/rel,PIN/rel
        comparisons.append({'path':rel,'local_sha256':sha(p),'reference_available':q.exists(),
                            'reference_sha256':sha(q) if q.exists() else None,
                            'same_as_pinned':p.read_bytes()==q.read_bytes() if q.exists() else None})
    assert len([c for c in comparisons if c['same_as_pinned'] is True]) == 13
    assert (WT/'src/agent_session.py').read_bytes() == (PIN/'src/agent_session.py').read_bytes()
    sys.path.insert(0,str(PIN))
    gem = importlib.import_module('src.agent_session_gemini')
    guard = []
    for name,path in [('accepted',PIN/'tests/test_jh018_agent_session_contract.py'),('execution08',WT/'tests/test_jh018_agent_session_contract.py')]:
        fn = guard_target(path.read_text(encoding='utf-8'))
        for mode in ('control','gemini_is_plain_object'):
            def body(self): return fn(self)
            cls = type('FrozenGuard',(unittest.TestCase,),{'runTest':body})
            result = unittest.TestResult()
            if mode == 'control': cls().run(result)
            else:
                with mock.patch.object(gem,'GeminiCliAgentSessionAdapter',object): cls().run(result)
            guard.append({'source':name,'mode':mode,'run':result.testsRun,
                          'failures':len(result.failures),'errors':len(result.errors)})
    assert guard == [
        {'source':'accepted','mode':'control','run':1,'failures':0,'errors':0},
        {'source':'accepted','mode':'gemini_is_plain_object','run':1,'failures':1,'errors':0},
        {'source':'execution08','mode':'control','run':1,'failures':0,'errors':0},
        {'source':'execution08','mode':'gemini_is_plain_object','run':1,'failures':0,'errors':0}]
    cases = [audit_case(s) for s in ['evidence/first_host_probe','evidence/second_host_probe','boundary_tests/evidence_fd13f80c']]
    j = RT/'boundary_tests/ws_normal/link_to_outside'
    junction = {'path':str(j),'isjunction':os.path.isjunction(j),'resolved_target':os.path.realpath(j)}
    assert junction['isjunction'] and Path(junction['resolved_target']) == RT/'boundary_tests/outside'
    cli = RT/'cli/prefix/node_modules/@google/gemini-cli'
    package = json.loads((cli/'package.json').read_text())
    cli_hashes = {s:sha(cli/s) for s in ['package.json','bundle/gemini.js','bundle/chunk-DKZARV6U.js','bundle/gemini-GLQ4MVLB.js']}
    assert package['version'] == '0.65.0'
    report = {'execution_commit':COMMIT,
              'actual_parent':subprocess.check_output(['git','--no-optional-locks','rev-parse',COMMIT+'^'],cwd=WT,text=True).strip(),
              'requested_baseline':'29e00201f85063f60cc23213262fc82cf430d7ee',
              'file_comparisons':comparisons,'contract_source_identical':True,
              'inheritance_guard_probe':guard,'cases':cases,'junction_now':junction,
              'audited_complete_run_synthetic_requests':sum(c['synthetic_requests'] for c in cases),
              'all_attempts_count_complete':False,
              'cli_version':package['version'],'cli_node_engines':package.get('engines'),
              'cli_hashes':cli_hashes,'review_real_cli_processes':0,'review_model_calls':0,
              'review_full_suite_runs':0}
    (ROOT/'review-summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ['file_comparisons','cases']},ensure_ascii=False,indent=2))


if __name__ == '__main__': main()
