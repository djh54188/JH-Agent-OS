"""Extract H13 delivery counts from raw evidence; does not execute any suite."""
import ast
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
E = ROOT / 'remote/reports/hark-gemini-registry-loss-20261023'
L = E / 'logs'


def read(name): return (L / name).read_text(encoding='utf-8')
def rows(name): return [json.loads(s) for s in read(name).splitlines() if s.strip()][:-1]


def main():
    manifests = [json.loads(l) for l in (ROOT / 'manifest.jsonl').read_text().splitlines()]
    for m in manifests:
        b = (ROOT / 'remote' / m['path']).read_bytes()
        assert len(b) == m['bytes']
        assert hashlib.sha1(b'blob ' + str(len(b)).encode() + b'\0' + b).hexdigest() == m['git_blob']
        assert hashlib.sha256(b).hexdigest() == m['sha256']
    suites = []
    failures = []
    for name in ('baseline-suite.txt', 'postcommit-suite.txt'):
        text = read(name)
        counts = re.findall(r'^Ran (\d+) tests? in .+$', text, re.M)
        result = re.findall(r'^(?:FAILED .*|OK.*)$', text, re.M)[-1]
        fs = re.findall(r'^(?:ERROR|FAIL): .+$', text, re.M)
        failures.append(set(fs))
        suites.append({'file': name, 'outer_run': int(counts[-1]), 'all_run_summaries': counts,
                       'result': result, 'failure_lines': len(fs), 'failure_unique': len(set(fs))})
    assert [s['outer_run'] for s in suites] == [768, 787]
    assert all(s['result'] == 'FAILED (failures=27, errors=78, skipped=2)' for s in suites)
    assert failures[0] == failures[1] and len(failures[0]) == 105
    previous = ROOT.parent / 'hark12-independent-review-20261022/remote/reports/hark-gemini-admission-20261022/logs/postcommit-suite.txt'
    assert failures[1] == set(re.findall(r'^(?:ERROR|FAIL): .+$', previous.read_text(encoding='utf-8'), re.M))
    targeted = []
    for name, count in [('hark13-run1.txt',18),('hark13-run2.txt',18),('hark13-run3.txt',18),('h12-h11-jh018.txt',107)]:
        t = read(name)
        assert int(re.findall(r'^Ran (\d+) tests?', t, re.M)[-1]) == count
        assert re.findall(r'^(?:OK.*|FAILED.*)$',t,re.M)[-1] == 'OK'
        targeted.append({'file':name,'run':count,'result':'OK'})
    tree = ast.parse((ROOT / 'candidate/tests/test_hark13_registry_loss.py').read_text(encoding='utf-8'))
    new_count = sum(isinstance(f,ast.FunctionDef) and f.name.startswith('test_')
                    for c in tree.body if isinstance(c,ast.ClassDef) for f in c.body)
    assert new_count == 18
    base, post = rows('repro-baseline.txt'), rows('repro-postcommit.txt')
    assert len(base) == len(post) == 11
    assert [r['case'] for r in base] == [r['case'] for r in post]
    for b,p in zip(base,post):
        if b['case'].startswith('loss:'):
            assert b['observed']['new_launches'] == 1
            assert p['observed']['new_launches'] == 0 and p['observed']['type'] == 'RegistryUnavailable'
        else:
            assert {k:v for k,v in b['observed'].items() if k != 'run_id'} == {k:v for k,v in p['observed'].items() if k != 'run_id'}
    old_repro = ROOT.parent / 'hark12-independent-review-20261022/remote/reports/hark-gemini-admission-20261022/logs/repro-postcommit.txt'
    old = [json.loads(l) for l in old_repro.read_text(encoding='utf-8').splitlines()][:-1]
    current = rows('repro12-candidate.txt')
    assert len(old) == len(current) == 32
    strip = lambda o: {k:v for k,v in o.items() if k not in ('results','run_id')}
    assert [r['case'] for r in old] == [r['case'] for r in current]
    assert all(strip(a['observed']) == strip(b['observed']) for a,b in zip(old,current))
    hp = E / 'host-probe-postcommit'
    summary = json.loads((hp / 'summary.json').read_text(encoding='utf-8'))
    assert len(summary['checks']) == 13 and all(summary['checks'].values())
    requests = [json.loads(l) for l in (hp / 'synthetic-requests.jsonl').read_text().splitlines()]
    assert len(requests) == 5
    recs = [json.loads(l) for l in (hp / 'bridge/registry.jsonl').read_text().splitlines()]
    collected = recs[-1]
    assert collected['event'] == 'collected' and collected['state'] == 'declared_complete'
    raw_verified = []
    for filename, meta in collected['evidence'].items():
        b = (hp / 'bridge/runs' / collected['run_id'] / filename).read_bytes()
        assert len(b) == meta['bytes'] and hashlib.sha256(b).hexdigest() == meta['sha256']
        raw_verified.append(filename)
    frozen_sha = hashlib.sha256((ROOT / 'frozen_probe_boundaries.py').read_bytes()).hexdigest()
    assert frozen_sha == 'a325cc541ea8ecc1dd0af128990e0ebb162abde96abf6dad6ad651f0f95e3e4f'
    report = {'verified_remote_files': len(manifests), 'suites': suites, 'failure_sets_identical': True,
              'same_failure_set_as_hark12': True, 'targeted': targeted, 'h13_test_methods':new_count,
              'h13_repro_cases':11,'h13_repro_loss_cases':7,'h12_repro_unchanged_cases':32,
              'offline_host_checks':13,'synthetic_requests':len(requests),'raw_evidence_verified':raw_verified,
              'frozen_probe_sha256':frozen_sha, 'review_model_calls':0,'review_full_suite_runs':0}
    (ROOT / 'review-summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
