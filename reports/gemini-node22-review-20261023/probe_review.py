"""Independent small probes and raw-ledger extraction; no CLI/model/suite."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
V = ROOT.parents[1].parent / 'gemini-node22-probe-20261023'
E = Path(r'D:\JH-Agent OS\data\gemini-node22-probe-20261023')
sys.path.insert(0,str(V))
from src import agent_session_gemini as gem
from src.agent_session import SessionAddress,SubmitRequest


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def rows(p): return [json.loads(l) for l in p.read_bytes().splitlines() if l.strip()]


def main():
    expected='750b81673ee5b3d68bcab6c2ac85d87a32b94d72'
    git=lambda *args: subprocess.check_output(['git','--no-optional-locks',*args],cwd=V,text=True).strip()
    assert git('rev-parse','HEAD')==expected
    assert git('rev-parse','HEAD^')=='29e00201f85063f60cc23213262fc82cf430d7ee'
    assert not git('diff','--name-only',expected+'^',expected,'--','src','tests','docs','AGENTS.md')
    original=json.loads((V/'reports/gemini-node22-probe-20261023/evidence/audit-result.json').read_bytes())
    ledger=[]
    evidence_matches=0
    for name in sorted(original['runs']):
        case=E/'evidence'/name
        records=rows(case/'bridge/registry.jsonl')
        assert [r['event'] for r in records]==['reserved','dispatched','collected']
        c=records[-1]; rd=case/'bridge/runs'/c['run_id']
        native=[]; bad=[]
        for number,line in enumerate((rd/'stdout.jsonl').read_bytes().splitlines(),1):
            try: native.append(json.loads(line))
            except ValueError: bad.append({'line':number,'raw':line.decode('utf-8')})
        result=next(x for x in native if x['type']=='result')
        assert len([x for x in native if x['type']=='init'])==1
        assert native[0]['session_id']==c['native_session_id']
        for filename,meta in c['evidence'].items():
            assert (rd/filename).stat().st_size==meta['bytes'] and sha(rd/filename)==meta['sha256']
            evidence_matches+=1
        assert (rd/'workspace-before.json').read_bytes()==(rd/'workspace-after.json').read_bytes()
        ledger.append({'case':name,'run_id':c['run_id'],'state':c['state'],
                       'exit_code':c['artifacts'][0]['exit_code'],
                       'assertion':'UV_HANDLE_CLOSING' in (rd/'stderr.txt').read_text(encoding='utf-8'),
                       'synthetic_requests':len(rows(case/'synthetic-requests.jsonl')),
                       'synthetic_tokens':result['stats']['total_tokens'],'non_json':bad,
                       'registry_sha256':sha(case/'bridge/registry.jsonl')})
    assert sum(x['synthetic_requests'] for x in ledger)==52
    assert sum(x['synthetic_tokens'] for x in ledger)==936
    assert evidence_matches==30
    assert all(x['exit_code']==0 and not x['assertion'] for x in ledger if 'node22' in x['case'])
    assert all(x['exit_code']==3221226505 and x['assertion'] for x in ledger if 'node24' in x['case'])

    # Frozen public-entry rejection cases. All launcher calls would be errors.
    out=ROOT/'input-probe-01'; out.mkdir(exist_ok=False)
    inputs=out/'inputs'; (inputs/'target').mkdir(parents=True)
    (inputs/'中文工作区').mkdir()
    observed={'versions':0,'launches':0}
    def version(_): observed['versions']+=1; return '0.65.0'
    def launcher(*args,**kwargs): observed['launches']+=1; raise AssertionError('must not dispatch')
    cfg=gem.GeminiCliConfig(('NEVER_EXECUTED',),str(out/'home'),str(out/'evidence'))
    adapter=gem.GeminiCliAgentSessionAdapter(cfg,launcher=launcher,version_probe=version)
    addr=SessionAddress('gemini-cli','gemini-cli/local','new')
    old=os.getcwd(); input_results=[]
    try:
        os.chdir(inputs)
        drive=inputs.drive
        for category,path,target_exists in [
            ('relative-existing','target',True),('relative-missing','absent',False),
            ('root-relative-existing',str(inputs/'target')[2:],True),
            ('root-relative-missing',str(inputs/'absent')[2:],False),
            ('drive-relative-existing',drive+'target',True),
            ('drive-relative-missing',drive+'absent',False)]:
            before=dict(observed)
            assert Path(path).exists()==target_exists
            try:
                adapter.submit(SubmitRequest('probe',1,addr,'Read only.',path,idempotency_key=category))
                raise AssertionError('unexpected admission')
            except gem.HostRestrictionUnavailable as exc:
                input_results.append({'case':category,'exception':type(exc).__name__,
                                      'message':str(exc),'before':before,'after':dict(observed)})
            assert observed==before
        missing=str(inputs/'中文不存在')
        try:
            adapter.submit(SubmitRequest('probe',1,addr,'Read only.',missing,idempotency_key='missing-absolute'))
            raise AssertionError('unexpected admission')
        except gem.HostRestrictionUnavailable as exc:
            input_results.append({'case':'chinese-missing-public-submit','exception':type(exc).__name__,
                                  'message':str(exc),'launches':observed['launches']})
        good=str(inputs/'中文工作区')
        adapter._validate(SubmitRequest('probe',1,addr,'Read only.',good))
        assert gem.workspace_preflight(good,cfg.evidence_root,cfg.cli_home)==[]
        input_results.append({'case':'chinese-existing-preflight','accepted':True,'real_cli_execution':False})
    finally: os.chdir(old)
    assert observed['launches']==0 and not (out/'evidence').exists() and not (out/'home').exists()

    # Baseline parser classes for a future narrow compatibility change.
    clean=(E/'evidence/node22-run2/bridge/registry.jsonl')
    rid=rows(clean)[-1]['run_id']
    raw=(clean.parent/'runs'/rid/'stdout.jsonl').read_bytes()
    warning=b'Rename failed with EPERM, retrying in 50ms (attempt 1/5)...\n'
    split=raw.splitlines(keepends=True)
    variants={'clean':raw,'known-preamble':warning+raw,
              'unknown-preamble':b'Unknown host message\n'+raw,
              'same-text-midstream':split[0]+warning+b''.join(split[1:]),
              'same-text-after-result':raw+warning,
              'damaged-json':b'{broken\n'+raw,
              'bad-utf8':b'\xff\n'+raw,
              'json-string-not-event':json.dumps(warning.decode().strip()).encode()+b'\n'+raw}
    parser_results=[]
    for name,data in variants.items():
        events,problems=gem.parse_stream(data)
        assert bool(problems)==(name!='clean')
        parser_results.append({'case':name,'events':len(events),'problems':problems})
    report={'commit':expected,'base':git('rev-parse','HEAD^'),'source_and_tests_unchanged':True,
            'ledger':ledger,'synthetic_requests':52,'synthetic_tokens':936,
            'evidence_file_matches':evidence_matches,'public_input_probe':input_results,
            'parser_baseline':parser_results,
            'provided_audit_recorded_head':original['source']['head'],
            'provided_audit_sha256':sha(V/'reports/gemini-node22-probe-20261023/evidence/audit-result.json'),
            'review_real_cli_processes':0,'review_model_calls':0,'review_full_suite_runs':0}
    (ROOT/'review-summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'requests':52,'tokens':936,'raw_files_verified':evidence_matches,
                      'public_input_probe_cases':len(input_results),'parser_baseline_cases':len(parser_results),
                      'real_cli_processes':0,'model_calls':0,'full_suite_runs':0}))


if __name__=='__main__': main()
